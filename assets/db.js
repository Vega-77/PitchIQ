// All Firestore reads and writes. Keeping them here means the surfaces stay
// declarative and there's one place to audit data access.

import {
    doc, collection, collectionGroup,
    getDoc, getDocs, setDoc, updateDoc, deleteDoc,
    query, where, orderBy, writeBatch, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js';

import { db } from './firebase-init.js?v=10';
import { EVENT_TYPES } from './events.js?v=10';

export const PERIOD_STATUS = {
    kickoff_1st: 'first_half',
    halftime: 'halftime',
    kickoff_2nd: 'second_half',
    full_time: 'full_time',
};

// ---------------------------------------------------------------- teams

export async function createTeam(user, name) {
    const ref = doc(collection(db, 'teams'));
    await setDoc(ref, {
        name,
        coachUids: [user.uid],
        taggerUids: [],
        archived: false,
        createdAt: serverTimestamp(),
        createdBy: user.uid,
    });
    return ref.id;
}

export async function getTeam(teamId) {
    const snap = await getDoc(doc(db, 'teams', teamId));
    return snap.exists() ? { id: snap.id, ...snap.data() } : null;
}

// ---------------------------------------------------------------- staff

/**
 * The coaching staff, as names rather than uids.
 *
 * coachUids on the team document stays the authority; this only supplies the
 * labels. A uid with no directory entry still appears, because dropping it
 * would hide a real coach from the list — the more misleading of the two
 * failures.
 */
export async function listStaff(team) {
    const snap = await getDocs(collection(db, 'teams', team.id, 'staff'))
        .catch(() => null);

    const byUid = new Map(
        (snap?.docs ?? []).map((d) => [d.id, { uid: d.id, ...d.data() }])
    );

    return (team.coachUids || []).map((uid) => byUid.get(uid) ?? {
        uid,
        displayName: 'Coach',
        emailLower: '',
        role: 'coach',
        unknown: true,
    });
}

/** Invite someone to help run a team. The invite is the grant — coaches only. */
export function inviteCoach(user, team, email) {
    const emailLower = (email || '').trim().toLowerCase();
    if (!emailLower) throw new Error('Enter an email address.');

    return setDoc(doc(db, 'invites', emailLower, 'from', team.id), {
        // A staff invite carries no roster slot; the rules reject one that does.
        playerId: null,
        role: 'coach',
        teamName: team.name,
        coachName: user.displayName || 'A coach',
        createdAt: serverTimestamp(),
        createdBy: user.uid,
    });
}

export function cancelInvite(emailLower, teamId) {
    return deleteDoc(doc(db, 'invites', emailLower, 'from', teamId));
}

/**
 * Take someone off a team's staff.
 *
 * Only the coach who created the team may do this — the rules enforce it, so an
 * assistant cannot lock the head coach out of their own squad.
 */
export function removeCoach(team, uid) {
    const remaining = (team.coachUids || []).filter((id) => id !== uid);
    if (!remaining.length) {
        throw new Error('A team has to keep at least one coach.');
    }
    return updateDoc(doc(db, 'teams', team.id), { coachUids: remaining });
}

// ---------------------------------------------------------------- roster

export async function listPlayers(teamId) {
    const snap = await getDocs(collection(db, 'teams', teamId, 'players'));
    return snap.docs
        .map((d) => ({ id: d.id, ...d.data() }))
        .sort((a, b) => (a.jerseyNumber ?? 999) - (b.jerseyNumber ?? 999));
}

export async function addPlayer(teamId, { name, jerseyNumber, email }) {
    const ref = doc(collection(db, 'teams', teamId, 'players'));
    await setDoc(ref, {
        name,
        jerseyNumber: jerseyNumber ?? null,
        emailLower: (email || '').toLowerCase(),
        linkedUid: null,
        active: true,
        createdAt: serverTimestamp(),
    });
    return ref.id;
}

export function removePlayer(teamId, playerId) {
    return deleteDoc(doc(db, 'teams', teamId, 'players', playerId));
}

/** An invite is a pointer; the claim itself re-verifies against the roster. */
export async function invitePlayer(user, team, player) {
    const email = (player.emailLower || '').toLowerCase();
    if (!email) throw new Error('That player has no email address on file.');

    await setDoc(doc(db, 'invites', email, 'from', team.id), {
        playerId: player.id,
        teamName: team.name,
        coachName: user.displayName || 'Your coach',
        createdAt: serverTimestamp(),
        createdBy: user.uid,
    });
}

// ---------------------------------------------------------------- matches

export async function listMatches(teamId) {
    const snap = await getDocs(collection(db, 'teams', teamId, 'matches'));
    return snap.docs
        .map((d) => ({ id: d.id, ...d.data() }))
        .sort((a, b) => (b.date || '').localeCompare(a.date || ''));
}

export async function getMatch(teamId, matchId) {
    const snap = await getDoc(doc(db, 'teams', teamId, 'matches', matchId));
    return snap.exists() ? { id: snap.id, ...snap.data() } : null;
}

export async function createMatch(user, teamId, { opponentName, date }) {
    const ref = doc(collection(db, 'teams', teamId, 'matches'));
    await setDoc(ref, {
        opponentName,
        date,
        status: 'scheduled',
        finalized: false,
        scoreUs: 0,
        scoreThem: 0,
        createdAt: serverTimestamp(),
        createdBy: user.uid,
    });
    return ref.id;
}

export function updateMatch(teamId, matchId, patch) {
    return updateDoc(doc(db, 'teams', teamId, 'matches', matchId), patch);
}

// ---------------------------------------------------------------- match roster

export async function setLineup(teamId, matchId, players, starterIds) {
    const batch = writeBatch(db);
    for (const player of players) {
        const isStarter = starterIds.includes(player.id);
        batch.set(
            doc(db, 'teams', teamId, 'matches', matchId, 'roster', player.id),
            {
                playerName: player.name,
                jerseyNumber: player.jerseyNumber ?? null,
                isStarter,
                isActive: isStarter,
                stints: isStarter ? [{ inS: 0, outS: null }] : [],
                version: 0,
            }
        );
    }
    await batch.commit();
}

export async function listMatchRoster(teamId, matchId) {
    const snap = await getDocs(
        collection(db, 'teams', teamId, 'matches', matchId, 'roster')
    );
    return snap.docs
        .map((d) => ({ id: d.id, ...d.data() }))
        .sort((a, b) => (a.jerseyNumber ?? 999) - (b.jerseyNumber ?? 999));
}

// ---------------------------------------------------------------- match log

/**
 * Log entry IDs are {deviceId}_{seq}, which makes undo a direct reference
 * rather than a query — so it works offline and can't race another tagger.
 */
export function logId(deviceId, seq) {
    return `${deviceId}_${String(seq).padStart(6, '0')}`;
}

function baseEntry(user, deviceId, seq) {
    return {
        playerId: null,
        assistPlayerId: null,
        cardColor: null,
        subOutId: null,
        subInId: null,
        detail: null,
        source: 'live_tag',
        seq,
        deviceId,
        revert: null,
        tappedAt: Date.now(),
        createdAt: serverTimestamp(),
        createdBy: user.uid,
    };
}

export function writeEvent(user, teamId, matchId, {
    deviceId, seq, type, matchClockS, side,
    playerId, assistPlayerId, cardColor, detail,
}) {
    return setDoc(
        doc(db, 'teams', teamId, 'matches', matchId, 'log', logId(deviceId, seq)),
        {
            ...baseEntry(user, deviceId, seq),
            kind: 'event',
            type,
            matchClockS,
            side: side ?? null,
            playerId: playerId ?? null,
            // Only ever set on the events that can carry them; the rules
            // enforce the same constraint server-side.
            assistPlayerId: type === 'goal' ? (assistPlayerId ?? null) : null,
            cardColor: type === 'card' ? (cardColor ?? null) : null,
            detail: detail ?? null,
        }
    );
}

export async function writePeriod(user, teamId, matchId, { deviceId, seq, period, matchClockS, prevStatus }) {
    const batch = writeBatch(db);

    batch.set(
        doc(db, 'teams', teamId, 'matches', matchId, 'log', logId(deviceId, seq)),
        {
            ...baseEntry(user, deviceId, seq),
            kind: 'period',
            type: period,
            matchClockS,
            side: null,
            // Storing the inverse means undo is a field read rather than a
            // second query to re-derive what the previous state was.
            revert: { prevStatus },
        }
    );

    batch.update(doc(db, 'teams', teamId, 'matches', matchId), {
        status: PERIOD_STATUS[period],
    });

    await batch.commit();
}

/**
 * A substitution touches three documents. runTransaction would be the obvious
 * tool but it fails outright when offline, which is exactly match-day
 * conditions. A writeBatch queues and replays offline; the `version` field,
 * checked in the rules as version + 1, supplies the compare-and-swap that the
 * batch alone doesn't — a conflicting concurrent write is rejected at sync time
 * instead of silently clobbering.
 */
export async function writeSubstitution(user, teamId, matchId, { deviceId, seq, outEntry, inEntry, matchClockS }) {
    if (!outEntry.isActive) throw new Error('That player is not on the field.');
    if (inEntry.isActive) throw new Error('That player is already on the field.');

    const outStints = (outEntry.stints || []).map((s) => ({ ...s }));
    if (outStints.length && outStints[outStints.length - 1].outS === null) {
        outStints[outStints.length - 1].outS = matchClockS;
    }

    const inStints = [...(inEntry.stints || []), { inS: matchClockS, outS: null }];

    const batch = writeBatch(db);
    const rosterPath = (id) =>
        doc(db, 'teams', teamId, 'matches', matchId, 'roster', id);

    batch.update(rosterPath(outEntry.id), {
        isActive: false,
        stints: outStints,
        version: (outEntry.version ?? 0) + 1,
    });

    batch.update(rosterPath(inEntry.id), {
        isActive: true,
        stints: inStints,
        version: (inEntry.version ?? 0) + 1,
    });

    batch.set(
        doc(db, 'teams', teamId, 'matches', matchId, 'log', logId(deviceId, seq)),
        {
            ...baseEntry(user, deviceId, seq),
            kind: 'sub',
            type: 'sub',
            matchClockS,
            side: 'us',
            subOutId: outEntry.id,
            subInId: inEntry.id,
            // Capture the exact prior state. Re-deriving it later is how the old
            // SQL implementation lost a player's earlier stint on re-entry.
            revert: {
                out: {
                    id: outEntry.id,
                    isActive: outEntry.isActive,
                    stints: outEntry.stints || [],
                    version: outEntry.version ?? 0,
                },
                in: {
                    id: inEntry.id,
                    isActive: inEntry.isActive,
                    stints: inEntry.stints || [],
                    version: inEntry.version ?? 0,
                },
            },
        }
    );

    await batch.commit();
}

/** Undo by direct reference — no query, so it works offline. */
export async function undoEntry(teamId, matchId, entry) {
    const batch = writeBatch(db);
    const entryRef = doc(db, 'teams', teamId, 'matches', matchId, 'log', entry.id);

    if (entry.kind === 'sub' && entry.revert) {
        for (const side of ['out', 'in']) {
            const prior = entry.revert[side];
            if (!prior) continue;
            batch.update(
                doc(db, 'teams', teamId, 'matches', matchId, 'roster', prior.id),
                {
                    isActive: prior.isActive,
                    stints: prior.stints,
                    version: prior.version + 2, // +1 for the sub, +1 for this undo
                }
            );
        }
    }

    if (entry.kind === 'period' && entry.revert?.prevStatus) {
        batch.update(doc(db, 'teams', teamId, 'matches', matchId), {
            status: entry.revert.prevStatus,
        });
    }

    batch.delete(entryRef);
    await batch.commit();
}

export async function listLog(teamId, matchId) {
    const snap = await getDocs(
        collection(db, 'teams', teamId, 'matches', matchId, 'log')
    );
    // Ordered by match clock, never createdAt: serverTimestamp() reads as null
    // locally until the write is acknowledged and then resolves to sync time,
    // not tap time — so it misorders exactly when offline.
    return snap.docs
        .map((d) => ({ id: d.id, ...d.data() }))
        .sort((a, b) => a.matchClockS - b.matchClockS);
}

// ---------------------------------------------------------------- stats

/** Aggregate a match log client-side. ~200 entries, so this is cheap. */
export function aggregateMatch(log, roster) {
    const counts = { us: {}, them: {} };
    for (const type of EVENT_TYPES) {
        counts.us[type] = 0;
        counts.them[type] = 0;
    }

    let subs = 0;
    for (const entry of log) {
        if (entry.kind === 'sub') { subs += 1; continue; }
        if (entry.kind !== 'event') continue;
        const side = entry.side === 'them' ? 'them' : 'us';
        if (entry.type in counts[side]) counts[side][entry.type] += 1;
    }

    const fullTime = log.find((e) => e.type === 'full_time');
    const matchEndS = fullTime
        ? fullTime.matchClockS
        : log.reduce((max, e) => Math.max(max, e.matchClockS), 0);

    const isOurs = (e) => e.side !== 'them';

    const players = roster.map((entry) => ({
        ...entry,
        minutesPlayed: minutesFrom(entry.stints, matchEndS),
        goals: log.filter(
            (e) => e.kind === 'event' && e.type === 'goal'
                && isOurs(e) && e.playerId === entry.id
        ).length,
        assists: log.filter(
            (e) => e.kind === 'event' && e.type === 'goal'
                && isOurs(e) && e.assistPlayerId === entry.id
        ).length,
        yellowCards: log.filter(
            (e) => e.kind === 'event' && e.type === 'card' && e.playerId === entry.id
                && (e.cardColor === 'yellow' || e.cardColor === 'second_yellow')
        ).length,
        redCards: log.filter(
            (e) => e.kind === 'event' && e.type === 'card' && e.playerId === entry.id
                && e.cardColor === 'red'
        ).length,
        fouls: log.filter(
            (e) => e.kind === 'event' && e.type === 'foul' && e.playerId === entry.id
        ).length,
    }));

    return { counts, subs, matchEndS, players };
}

/** Sums every stint, so re-entry is counted correctly. */
export function minutesFrom(stints, matchEndS) {
    if (!stints?.length) return 0;
    const seconds = stints.reduce((total, stint) => {
        const end = stint.outS ?? matchEndS;
        return total + Math.max(0, end - stint.inS);
    }, 0);
    return Math.round(seconds / 60);
}

// ---------------------------------------------------------------- reports

/**
 * Written at finalize. These documents exist because Firestore evaluates a list
 * rule against the query rather than per document — there is no way to let a
 * player read only their own row out of the shared match data, so the coach
 * publishes a per-player document instead.
 */
export async function publishReports(teamId, matchId, match, team, players, score) {
    const batch = writeBatch(db);

    for (const player of players) {
        const roster = await getDoc(
            doc(db, 'teams', teamId, 'players', player.id)
        ).catch(() => null);
        const linkedUid = roster?.exists() ? roster.data().linkedUid : null;

        batch.set(
            doc(db, 'teams', teamId, 'matches', matchId, 'playerReports', player.id),
            {
                linkedUid: linkedUid ?? null,
                published: true,
                playerName: player.playerName,
                jerseyNumber: player.jerseyNumber ?? null,
                minutesPlayed: player.minutesPlayed,
                goals: player.goals,
                assists: player.assists ?? 0,
                cards: (player.yellowCards ?? 0) + (player.redCards ?? 0),
                yellowCards: player.yellowCards ?? 0,
                redCards: player.redCards ?? 0,
                fouls: player.fouls ?? 0,
                stints: player.stints || [],
                matchDate: match.date || '',
                opponentName: match.opponentName || '',
                teamName: team.name || '',
            }
        );
    }

    // Store the score on the match itself so the dashboard can show a season
    // record without re-reading every match's full event log.
    batch.update(doc(db, 'teams', teamId, 'matches', matchId), {
        finalized: true,
        scoreUs: score?.us ?? 0,
        scoreThem: score?.them ?? 0,
    });
    await batch.commit();
}

/** Season totals derived from finalized matches. */
export function seasonSummary(matches) {
    const played = matches.filter((m) => m.finalized);

    let won = 0, drawn = 0, lost = 0, scored = 0, conceded = 0;
    for (const m of played) {
        const us = m.scoreUs ?? 0;
        const them = m.scoreThem ?? 0;
        scored += us;
        conceded += them;
        if (us > them) won += 1;
        else if (us < them) lost += 1;
        else drawn += 1;
    }

    return {
        played: played.length,
        upcoming: matches.length - played.length,
        won, drawn, lost, scored, conceded,
        goalDifference: scored - conceded,
        record: `${won}-${drawn}-${lost}`,
    };
}

/**
 * Every published report for one player, for the coach's own view.
 *
 * Deliberately not a collection-group query: the collection-group rule is
 * scoped to `linkedUid == request.auth.uid`, which is what keeps a player from
 * reading a teammate's row, and a coach is not the linkedUid on anyone's
 * report. Coaches read each report by path instead, which the per-match rule
 * does allow. That is one read per finalized match — trivial for a season.
 */
export async function playerSeason(teamId, matches, playerId) {
    const finalized = matches.filter((m) => m.finalized);

    const reports = await Promise.all(finalized.map(async (match) => {
        const snap = await getDoc(
            doc(db, 'teams', teamId, 'matches', match.id, 'playerReports', playerId)
        ).catch(() => null);
        return snap?.exists() ? { matchId: match.id, ...snap.data() } : null;
    }));

    return reports
        .filter(Boolean)
        .sort((a, b) => (b.matchDate || '').localeCompare(a.matchDate || ''));
}

/** Totals across a set of per-match reports. */
export function seasonTotals(reports) {
    return reports.reduce((acc, r) => ({
        matches: acc.matches + 1,
        minutes: acc.minutes + (r.minutesPlayed || 0),
        goals: acc.goals + (r.goals || 0),
        assists: acc.assists + (r.assists || 0),
        yellowCards: acc.yellowCards + (r.yellowCards || 0),
        redCards: acc.redCards + (r.redCards || 0),
        fouls: acc.fouls + (r.fouls || 0),
    }), {
        matches: 0, minutes: 0, goals: 0, assists: 0,
        yellowCards: 0, redCards: 0, fouls: 0,
    });
}

/** The player portal's single query. Must filter on linkedUid to be permitted. */
export async function myReports(uid) {
    const snap = await getDocs(query(
        collectionGroup(db, 'playerReports'),
        where('linkedUid', '==', uid),
        where('published', '==', true),
        orderBy('matchDate', 'desc'),
    ));
    return snap.docs.map((d) => ({ id: d.id, ...d.data() }));
}
