// All Firestore reads and writes. Keeping them here means the surfaces stay
// declarative and there's one place to audit data access.

import {
    doc, collection, collectionGroup,
    getDoc, getDocs, setDoc, updateDoc, deleteDoc, onSnapshot,
    query, where, orderBy, writeBatch, serverTimestamp,
} from 'https://www.gstatic.com/firebasejs/12.16.0/firebase-firestore.js';

import { db } from './firebase-init.js?v=50';
import { EVENT_TYPES } from './events.js?v=50';
// Kept in its own dependency-free module so the rules about what a player may
// see can be tested without opening a Firestore connection. See report.js.
import {
    playerTimeline, cvStatsByPlayer, cvReportFields, trackedCoverage, clockFromMatch,
} from './report.js?v=50';

export {
    playerTimeline, cvStatsByPlayer, cvReportFields, trackedCoverage, clockFromMatch,
};

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

    // The clock stops at the break and restarts from the same second, so this
    // one reading is both the end of the first half and the start of the
    // second. It is written onto the match because that is where every reader
    // of the footage looks: without it nothing outside the tag log can tell a
    // second-half video position from a first-half one, and the offset alone
    // puts every second-half moment out by the length of the interval. See
    // `matchClockMap` in report.js.
    const patch = { status: PERIOD_STATUS[period] };
    if (period === 'halftime') patch.halfTimeClockS = matchClockS;

    batch.update(doc(db, 'teams', teamId, 'matches', matchId), patch);

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
        const patch = { status: entry.revert.prevStatus };
        // Undoing the half-time tap has to take the clock reading with it. Left
        // behind, it would anchor the second half to a break that the log no
        // longer says happened, and every second-half timestamp would be
        // converted against it.
        if (entry.type === 'halftime') patch.halfTimeClockS = null;
        batch.update(doc(db, 'teams', teamId, 'matches', matchId), patch);
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

/**
 * Watch how much of the log is still only on this tablet.
 *
 * `includeMetadataChanges` is the whole point of this listener. Without it a
 * snapshot only fires when the *data* changes, so the moment that matters most
 * — a queued write finally being acknowledged, which changes no field on any
 * document — never arrives, and the indicator would sit on "waiting" until
 * somebody tapped something else.
 *
 * `hasPendingWrites` is per document and exact: it is false once the server has
 * acknowledged that write. `fromCache` is per snapshot and says the read never
 * reached the server at all, which is the honest replacement for
 * `navigator.onLine` — a link is not a reachable server, and a school field
 * with a captive portal has the first without the second.
 *
 * Returns the unsubscribe function. One listener over a collection that holds
 * around two hundred entries by full time, and the page already read the whole
 * thing twice.
 */
export function watchSync(teamId, matchId, onChange) {
    return onSnapshot(
        collection(db, 'teams', teamId, 'matches', matchId, 'log'),
        { includeMetadataChanges: true },
        (snap) => onChange({
            pending: snap.docs.filter((d) => d.metadata.hasPendingWrites).length,
            fromCache: snap.metadata.fromCache,
            total: snap.size,
        }),
        // A listener that dies takes the indicator's meaning with it, and a
        // stuck "Saved" is worse than no indicator. Report it as disconnected,
        // which is the pessimistic answer and the safe one.
        () => onChange({ pending: 0, fromCache: true, total: 0 }),
    );
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
export async function publishReports(teamId, matchId, match, team, players, score, extra = {}) {
    const batch = writeBatch(db);
    const log = extra.log || [];
    const roster = extra.roster || [];
    const counts = extra.counts || null;

    // Video stats reach a player only through a mapping a coach confirmed.
    // Without one this is empty and no cv* field is written at all — an
    // unconfirmed cluster is a guess about identity, and a guess with a name
    // attached is the one thing that would make these numbers untrustworthy.
    const cvByPlayer = cvStatsByPlayer(extra.cvTracks, extra.cvMapping, {
        events: extra.cvEvents,
        review: extra.cvReview,
        clusters: extra.cvClusters,
    });

    // What each player's video figures were measured over. The roster is the
    // only thing that knows this — Python never sees the sub log — so the join
    // happens here, at the one point where the stints, the processed window and
    // the tracked minutes are all in the same scope.
    const coverageContext = {
        window: extra.cvWindow,
        clock: clockFromMatch(match),
        matchEndS: extra.matchEndS ?? null,
    };

    for (const player of players) {
        const rosterDoc = await getDoc(
            doc(db, 'teams', teamId, 'players', player.id)
        ).catch(() => null);
        const linkedUid = rosterDoc?.exists() ? rosterDoc.data().linkedUid : null;

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

                // Denormalized so a player can see the match they played in.
                // Their entire read surface is this one document — there is no
                // rules-only way to give them part of a collection — so
                // anything they should see has to be copied here at publish
                // time. See the collection-group note in firestore.rules.
                scoreUs: score?.us ?? 0,
                scoreThem: score?.them ?? 0,
                teamCounts: counts || null,
                timeline: playerTimeline(log, roster, player.id),
                matchId,
                videoUrl: match.videoUrl || null,
                // All three, because the player portal seeks this footage too
                // and one offset only reaches half-time. See `clockFromMatch`.
                videoOffsetS: match.videoOffsetS ?? 0,
                secondHalfVideoS: match.secondHalfVideoS ?? null,
                halfTimeClockS: match.halfTimeClockS ?? null,

                ...cvReportFields(
                    cvByPlayer[player.id],
                    trackedCoverage(
                        cvByPlayer[player.id]?.minutes_tracked,
                        player.stints,
                        coverageContext,
                    ),
                    // The coach's body-part tags, so a player's own shot map
                    // and xG say what the coach's screen says.
                    extra.cvShotRows || null,
                ),
            },
            // Merged, not overwritten. `cv/publish.py` fills in the heatmap,
            // the shot map's attacking end and the calibration error *after*
            // this document is created — a plain set() deleted all of them
            // every time a coach re-published, and nothing noticed because
            // nothing here reads them back. cvReportFields nulls every video
            // field explicitly when a player has no mapping, which is what the
            // overwrite used to do for free.
            { merge: true },
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
/**
 * Push a changed video link onto reports that were already published.
 *
 * `publishReports` copies `videoUrl` onto every player's document, because a
 * player's entire read surface is that one document — there is no rules-only
 * way to let them see a field of the match. That copy is taken at publish time,
 * which means the very common order of events breaks it: publish the reports
 * after the match, upload the footage that evening, paste the link. Every
 * player's report keeps saying "no video for this match yet" until somebody
 * thinks to publish a second time, and nothing on the page suggests that.
 *
 * So saving a link fixes the copies too. Update, never create: a report exists
 * because a coach published the match, and inventing one here would put a
 * document in front of a player for a match whose reports were never finished.
 *
 * Returns how many were updated, so the coach is told a number rather than
 * being left to wonder whether it reached anyone.
 */
export async function pushVideoToReports(teamId, matchId, videoUrl, timing = {}) {
    const snap = await getDocs(
        collection(db, 'teams', teamId, 'matches', matchId, 'playerReports')
    );
    if (snap.empty) return 0;

    const batch = writeBatch(db);
    for (const report of snap.docs) {
        batch.update(report.ref, {
            videoUrl: videoUrl || null,
            // The whole clock, not just the kick-off. A player whose copy had
            // the offset but not the second anchor would seek every one of
            // their second-half touches into the interval — which looks like
            // the video being broken rather than like a number being unset.
            videoOffsetS: timing.videoOffsetS ?? 0,
            secondHalfVideoS: timing.secondHalfVideoS ?? null,
            halfTimeClockS: timing.halfTimeClockS ?? null,
        });
    }
    await batch.commit();
    return snap.size;
}

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

        // Video-derived, and only present on matches that were filmed and
        // published. `cvMatches` counts those separately, because averaging
        // touches over a whole season would divide by matches nobody filmed.
        cvMatches: acc.cvMatches + (r.cvTouches == null ? 0 : 1),
        cvTouches: acc.cvTouches + (r.cvTouches || 0),
        cvPassesAttempted: acc.cvPassesAttempted + (r.cvPassesAttempted || 0),
        cvPassesCompleted: acc.cvPassesCompleted + (r.cvPassesCompleted || 0),
        cvCarries: acc.cvCarries + (r.cvCarries || 0),
        cvTackles: acc.cvTackles + (r.cvTackles || 0),
        cvInterceptions: acc.cvInterceptions + (r.cvInterceptions || 0),
        cvRecoveries: acc.cvRecoveries + (r.cvRecoveries || 0),
        cvShots: acc.cvShots + (r.cvShots || 0),
        cvXg: acc.cvXg + (r.cvXg || 0),
        cvDistanceM: acc.cvDistanceM + (r.cvDistanceM || 0),
        cvTopSpeedKmh: Math.max(acc.cvTopSpeedKmh, r.cvTopSpeedKmh || 0),
        cvSprintCount: acc.cvSprintCount + (r.cvSprintCount || 0),
        // Matches where it could not be measured contribute nothing rather
        // than a zero, which is the same thing the per-match card does.
        cvAccelerations: acc.cvAccelerations + (r.cvAccelerations || 0),
    }), {
        matches: 0, minutes: 0, goals: 0, assists: 0,
        yellowCards: 0, redCards: 0, fouls: 0,
        cvMatches: 0, cvTouches: 0, cvPassesAttempted: 0, cvPassesCompleted: 0,
        cvCarries: 0, cvTackles: 0, cvInterceptions: 0, cvRecoveries: 0,
        cvShots: 0, cvXg: 0, cvDistanceM: 0, cvTopSpeedKmh: 0, cvSprintCount: 0,
        cvAccelerations: 0,
    });
}

// ---------------------------------------------------------------- CV stats

/**
 * What the video pipeline derived for a match, or null if it was never run.
 *
 * These documents are written by cv/publish.py through the Admin SDK; the
 * security rules make them read-only to every client, so what comes back here
 * genuinely came from the pipeline rather than from somebody's browser.
 */
export async function readCvStats(teamId, matchId) {
    const base = ['teams', teamId, 'matches', matchId, 'cvStats'];
    const [summary, identity] = await Promise.all([
        getDoc(doc(db, ...base, 'summary')),
        getDoc(doc(db, ...base, 'identity')),
    ]);
    if (!summary.exists()) return null;
    return {
        ...summary.data(),
        identity: identity.exists() ? identity.data() : null,
    };
}

/**
 * How much to trust a video-derived figure: 'high', 'medium' or 'low'.
 *
 * Three bands rather than a number, because the uncertainty here is not
 * calibrated well enough to justify one. What each band means is "how much of
 * the thing this stat is built from did we actually see":
 *
 *   - possession and touch counts rest on finding the ball, so they are graded
 *     on ball coverage and on how often a clear holder was identifiable;
 *   - passes, tackles and the rest rest on the touch detector, so they are
 *     graded on its confidence spread;
 *   - anything per-player additionally rests on fragmented tracks having been
 *     merged and confirmed, which caps it at 'low' until a human has done that.
 *
 * The floors match the pipeline's own: 25% clear-holder is where cv/pipeline.py
 * stops believing possession at all.
 */
export function cvConfidence(quality = {}, kind = 'team', mappingConfirmed = false) {
    if (!quality) return 'low';

    // The share of frames the ball was actually *seen* in, never the share it
    // has a position for. Positions between sightings are a straight line
    // drawn through a gap, and grading on those would let a run that barely
    // saw the ball score as well as one that watched it throughout.
    const coverage = quality.ball_seen_share ?? quality.ballSeenShare ?? 0;
    const holder = quality.clear_holder_share ?? quality.clearHolderShare ?? 0;
    const touchP50 = quality.touch_confidence_p50 ?? quality.touchConfidenceP50 ?? 0;

    if (kind === 'player' && !mappingConfirmed) return 'low';

    if (kind === 'possession') {
        if (coverage >= 0.75 && holder >= 0.5) return 'high';
        if (coverage >= 0.5 && holder >= 0.25) return 'medium';
        return 'low';
    }

    // Event-derived. The touch detector is the floor under all of it.
    if (coverage >= 0.75 && touchP50 >= 0.6) return 'high';
    if (coverage >= 0.5 && touchP50 >= 0.4) return 'medium';
    return 'low';
}

/**
 * Which tracked figure a coach has said is which player.
 *
 * Its own document, separate from cvStats, because that collection is
 * pipeline-authored and read-only to clients. This one is a human's judgement,
 * and keeping the two apart means a coach correcting an identity can never be
 * mistaken for the pipeline having measured something.
 */
export async function readCvMapping(teamId, matchId) {
    const snap = await getDoc(
        doc(db, 'teams', teamId, 'matches', matchId, 'cvMapping', 'players')
    ).catch(() => null);
    return snap?.exists() ? (snap.data().byCluster || {}) : {};
}

export function saveCvMapping(user, teamId, matchId, byCluster) {
    // Unassigned clusters are dropped rather than stored as null, so the
    // document stays a plain "these are the ones we know" rather than a list
    // of every figure the tracker ever produced.
    const cleaned = Object.fromEntries(
        Object.entries(byCluster || {}).filter(([, playerId]) => Boolean(playerId))
    );
    return setDoc(
        doc(db, 'teams', teamId, 'matches', matchId, 'cvMapping', 'players'),
        { byCluster: cleaned, updatedAt: serverTimestamp(), updatedBy: user.uid },
    );
}

/**
 * The individual events the pipeline found, for checking one at a time.
 *
 * Absent for any match published before the review tool existed, and for any
 * run that predates schema 2 — so the caller gets null and hides the tool
 * rather than showing an empty list that looks like "it found nothing".
 */
export async function readCvEvents(teamId, matchId) {
    const snap = await getDoc(
        doc(db, 'teams', teamId, 'matches', matchId, 'cvStats', 'events')
    ).catch(() => null);
    return snap?.exists() ? snap.data() : null;
}

/**
 * What a coach decided about those events.
 *
 * `byEvent` is keyed by event id: confirmed, rejected, or edited with what it
 * should have been. `missed` is the other half of the same job — the moments
 * the pipeline found nothing and should have. Precision comes from the first
 * and recall only from the second, and recall is the number that says whether
 * the detector is good enough.
 */
export async function readCvReview(teamId, matchId) {
    const snap = await getDoc(
        doc(db, 'teams', teamId, 'matches', matchId, 'cvReview', 'decisions')
    ).catch(() => null);
    const data = snap?.exists() ? snap.data() : {};
    return { byEvent: data.byEvent || {}, missed: data.missed || [] };
}

export function saveCvReview(user, teamId, matchId, review) {
    return setDoc(
        doc(db, 'teams', teamId, 'matches', matchId, 'cvReview', 'decisions'),
        {
            byEvent: review.byEvent || {},
            missed: review.missed || [],
            updatedAt: serverTimestamp(),
            updatedBy: user.uid,
        },
    );
}

/**
 * How much to trust one player's video stats, from how cleanly they were
 * tracked.
 *
 * A player's numbers only exist at all once a coach has matched a tracked
 * figure to them, so the question is no longer whether they were identified —
 * it is how many pieces they had to be assembled from. Someone followed
 * through the whole match is a much stronger claim than someone stitched out
 * of nine fragments, where every join is a place the tracker could have picked
 * up the wrong person.
 */
export function cvPlayerConfidence(clusterCount = 0) {
    if (!clusterCount) return 'low';
    if (clusterCount <= 2) return 'high';
    return clusterCount <= 5 ? 'medium' : 'low';
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
