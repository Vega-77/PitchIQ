/**
 * End-to-end data flow: the same operations the browser surfaces perform,
 * run as a real authenticated coach so the rules and the data shape are
 * exercised together.
 *
 * rules.test.js proves what must be DENIED. This file proves the legitimate
 * path actually works.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { before, after, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
    initializeTestEnvironment, assertSucceeds, assertFails,
} from '@firebase/rules-unit-testing';
import {
    doc, setDoc, getDoc, updateDoc, deleteDoc, collection, getDocs, onSnapshot,
    query, where, collectionGroup, writeBatch, serverTimestamp,
    disableNetwork, enableNetwork,
} from 'firebase/firestore';

const HERE = dirname(fileURLToPath(import.meta.url));

const COACH = { uid: 'coach1', email: 'coach@school.org' };
const ASSISTANT = { uid: 'assist1', email: 'assistant@school.org' };
const PLAYER = { uid: 'alex1', email: 'alex@school.org' };

const TEAM = 'team1';
const MATCH = 'match1';

let testEnv;

const google = (user) => ({
    sub: user.uid,
    email: user.email,
    email_verified: true,
    firebase: { sign_in_provider: 'google.com' },
});

const as = (user) =>
    testEnv.authenticatedContext(user.uid, google(user)).firestore();

before(async () => {
    testEnv = await initializeTestEnvironment({
        projectId: 'pitchiq-flow-test',
        firestore: {
            rules: readFileSync(join(HERE, '..', 'firestore.rules'), 'utf8'),
            host: '127.0.0.1',
            port: 8085,
        },
    });
});

after(async () => { await testEnv?.cleanup(); });

beforeEach(async () => {
    await testEnv.clearFirestore();
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
        const db = ctx.firestore();
        await setDoc(doc(db, 'coachAllowlist', COACH.email), { note: 'head coach' });
        await setDoc(doc(db, 'teams', TEAM), {
            name: 'South Brunswick', coachUids: [COACH.uid], taggerUids: [],
            archived: false, createdBy: COACH.uid,
        });
        await setDoc(doc(db, 'teams', TEAM, 'players', 'p1'), {
            name: 'Alex Vega', jerseyNumber: 9,
            emailLower: PLAYER.email, linkedUid: PLAYER.uid, active: true,
        });
        await setDoc(doc(db, 'teams', TEAM, 'players', 'p2'), {
            name: 'Bench Guy', jerseyNumber: 15,
            emailLower: 'bench@school.org', linkedUid: null, active: true,
        });
        await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH), {
            opponentName: 'Linden', date: '2026-07-27',
            status: 'first_half', finalized: false, createdBy: COACH.uid,
        });
    });
});

// ---------------------------------------------------------------- pure logic

/** Mirrors minutesFrom() in assets/db.js. */
function minutesFrom(stints, matchEndS) {
    if (!stints?.length) return 0;
    const seconds = stints.reduce(
        (total, s) => total + Math.max(0, (s.outS ?? matchEndS) - s.inS), 0
    );
    return Math.round(seconds / 60);
}

describe('minutes played', () => {
    it('counts a full match for a starter who never comes off', () => {
        assert.equal(minutesFrom([{ inS: 0, outS: null }], 5400), 90);
    });

    it('stops counting at the substitution', () => {
        assert.equal(minutesFrom([{ inS: 0, outS: 2700 }], 5400), 45);
    });

    it('sums multiple stints — high school rules allow re-entry', () => {
        const stints = [{ inS: 0, outS: 1200 }, { inS: 3600, outS: null }];
        assert.equal(minutesFrom(stints, 5400), 50);
    });

    it('is zero for an unused substitute', () => {
        assert.equal(minutesFrom([], 5400), 0);
    });
});

// ---------------------------------------------------------------- round trip

describe('match tagging', () => {
    const logPath = (db, id) =>
        doc(db, 'teams', TEAM, 'matches', MATCH, 'log', id);

    function entry(overrides) {
        return {
            kind: 'event', type: 'corner', matchClockS: 0, side: 'us',
            playerId: null, subOutId: null, subInId: null, detail: null,
            source: 'live_tag', seq: 1, deviceId: 'devA', revert: null,
            tappedAt: Date.now(), createdAt: serverTimestamp(),
            createdBy: COACH.uid,
            ...overrides,
        };
    }

    it('orders the log by match clock, not write order', async () => {
        const db = as(COACH);

        // Written deliberately out of order.
        await assertSucceeds(setDoc(logPath(db, 'devA_000002'),
            entry({ seq: 2, matchClockS: 900, type: 'corner' })));
        await assertSucceeds(setDoc(logPath(db, 'devA_000001'),
            entry({ seq: 1, matchClockS: 0, type: 'kickoff_1st', kind: 'period' })));
        await assertSucceeds(setDoc(logPath(db, 'devB_000001'),
            entry({ seq: 1, deviceId: 'devB', matchClockS: 450, type: 'foul' })));

        const snap = await getDocs(
            collection(db, 'teams', TEAM, 'matches', MATCH, 'log')
        );
        const log = snap.docs
            .map((d) => ({ id: d.id, ...d.data() }))
            .sort((a, b) => a.matchClockS - b.matchClockS);

        assert.deepEqual(log.map((e) => e.type), ['kickoff_1st', 'foul', 'corner']);
    });

    it('two devices can tag concurrently without colliding', async () => {
        const db = as(COACH);
        await assertSucceeds(setDoc(logPath(db, 'devA_000001'), entry({ seq: 1 })));
        await assertSucceeds(setDoc(logPath(db, 'devB_000001'),
            entry({ seq: 1, deviceId: 'devB', type: 'foul' })));

        const snap = await getDocs(
            collection(db, 'teams', TEAM, 'matches', MATCH, 'log')
        );
        assert.equal(snap.size, 2, 'same seq from different devices must coexist');
    });

    it('attributes a goal to a player', async () => {
        const db = as(COACH);
        await assertSucceeds(setDoc(logPath(db, 'devA_000005'),
            entry({ seq: 5, type: 'goal', matchClockS: 1800, playerId: 'p1' })));

        const snap = await getDoc(logPath(db, 'devA_000005'));
        assert.equal(snap.data().playerId, 'p1');
    });

    it('the half-time tap writes the clock the halves split on, and undo takes it back', async () => {
        // The batch `writePeriod` sends: the log entry and the match status,
        // plus — only at half-time — the clock reading. That reading is the
        // second anchor of the clock map, and this is the one place in the app
        // that knows it, because the tablet's clock freezes here and nothing
        // downstream can recover the length of the break afterwards.
        const db = as(COACH);
        const matchRef = doc(db, 'teams', TEAM, 'matches', MATCH);

        const batch = writeBatch(db);
        batch.set(logPath(db, 'devA_000009'), entry({
            seq: 9, kind: 'period', type: 'halftime', matchClockS: 2760,
            revert: { prevStatus: 'first_half' },
        }));
        batch.update(matchRef, { status: 'halftime', halfTimeClockS: 2760 });
        await assertSucceeds(batch.commit());

        assert.equal((await getDoc(matchRef)).data().halfTimeClockS, 2760);

        // Undo. Left behind, the reading would anchor the second half to a
        // break the log no longer says happened.
        const undo = writeBatch(db);
        undo.update(matchRef, { status: 'first_half', halfTimeClockS: null });
        undo.delete(logPath(db, 'devA_000009'));
        await assertSucceeds(undo.commit());

        assert.equal((await getDoc(matchRef)).data().halfTimeClockS, null);
    });
});

describe('substitutions', () => {
    const rosterPath = (db, id) =>
        doc(db, 'teams', TEAM, 'matches', MATCH, 'roster', id);

    beforeEach(async () => {
        const db = as(COACH);
        const batch = writeBatch(db);
        batch.set(rosterPath(db, 'p1'), {
            playerName: 'Alex Vega', jerseyNumber: 9, isStarter: true,
            isActive: true, stints: [{ inS: 0, outS: null }], version: 0,
        });
        batch.set(rosterPath(db, 'p2'), {
            playerName: 'Bench Guy', jerseyNumber: 15, isStarter: false,
            isActive: false, stints: [], version: 0,
        });
        await batch.commit();
    });

    it('flips both roster entries and bumps versions in one batch', async () => {
        const db = as(COACH);
        const batch = writeBatch(db);
        batch.update(rosterPath(db, 'p1'), {
            isActive: false, stints: [{ inS: 0, outS: 2400 }], version: 1,
        });
        batch.update(rosterPath(db, 'p2'), {
            isActive: true, stints: [{ inS: 2400, outS: null }], version: 1,
        });
        await assertSucceeds(batch.commit());

        const [out, incoming] = await Promise.all([
            getDoc(rosterPath(db, 'p1')), getDoc(rosterPath(db, 'p2')),
        ]);

        assert.equal(out.data().isActive, false);
        assert.equal(out.data().stints[0].outS, 2400);
        assert.equal(incoming.data().isActive, true);
        assert.equal(incoming.data().stints.length, 1);
    });

    it('undo restores an earlier stint that re-derivation would have lost', async () => {
        const db = as(COACH);

        // Already on, off, and back on again.
        const priorStints = [{ inS: 0, outS: 600 }, { inS: 1800, outS: null }];
        await assertSucceeds(updateDoc(rosterPath(db, 'p1'), {
            stints: priorStints, version: 1,
        }));

        const revert = { id: 'p1', isActive: true, stints: priorStints, version: 1 };

        // Sub off...
        await assertSucceeds(updateDoc(rosterPath(db, 'p1'), {
            isActive: false,
            stints: [priorStints[0], { inS: 1800, outS: 3000 }],
            version: 2,
        }));

        // ...then undo, replaying the captured inverse.
        await assertSucceeds(updateDoc(rosterPath(db, 'p1'), {
            isActive: revert.isActive,
            stints: revert.stints,
            version: revert.version + 2,
        }));

        const restored = await getDoc(rosterPath(db, 'p1'));
        assert.equal(restored.data().isActive, true);
        assert.equal(restored.data().stints.length, 2, 'earlier stint must survive');
        assert.equal(restored.data().stints[1].outS, null);
    });
});

describe('publishing and the player portal', () => {
    it('a player reads their own reports across matches, and only those', async () => {
        const coachDb = as(COACH);

        // Coach publishes reports for two matches.
        await testEnv.withSecurityRulesDisabled(async (ctx) => {
            const db = ctx.firestore();
            for (const m of ['match1', 'match2']) {
                await setDoc(doc(db, 'teams', TEAM, 'matches', m), {
                    opponentName: 'Linden', date: `2026-07-${m === 'match1' ? '01' : '08'}`,
                    status: 'full_time', finalized: true, createdBy: COACH.uid,
                });
            }
        });

        const batch = writeBatch(coachDb);
        batch.set(doc(coachDb, 'teams', TEAM, 'matches', 'match1', 'playerReports', 'p1'), {
            linkedUid: PLAYER.uid, published: true, playerName: 'Alex Vega',
            jerseyNumber: 9, minutesPlayed: 90, goals: 1, cards: 0, stints: [],
            matchDate: '2026-07-01', opponentName: 'Linden', teamName: 'South Brunswick',
        });
        batch.set(doc(coachDb, 'teams', TEAM, 'matches', 'match2', 'playerReports', 'p1'), {
            linkedUid: PLAYER.uid, published: true, playerName: 'Alex Vega',
            jerseyNumber: 9, minutesPlayed: 45, goals: 0, cards: 1, stints: [],
            matchDate: '2026-07-08', opponentName: 'Edison', teamName: 'South Brunswick',
        });
        batch.set(doc(coachDb, 'teams', TEAM, 'matches', 'match1', 'playerReports', 'p2'), {
            linkedUid: 'someoneelse', published: true, playerName: 'Bench Guy',
            jerseyNumber: 15, minutesPlayed: 20, goals: 0, cards: 0, stints: [],
            matchDate: '2026-07-01', opponentName: 'Linden', teamName: 'South Brunswick',
        });
        await assertSucceeds(batch.commit());

        // The portal's actual query.
        const playerDb = as(PLAYER);
        const snap = await assertSucceeds(getDocs(query(
            collectionGroup(playerDb, 'playerReports'),
            where('linkedUid', '==', PLAYER.uid),
            where('published', '==', true),
        )));

        const mine = snap.docs.map((d) => d.data());
        assert.equal(mine.length, 2, 'only this player\'s reports');
        assert.ok(mine.every((r) => r.linkedUid === PLAYER.uid));
        assert.equal(mine.reduce((t, r) => t + r.minutesPlayed, 0), 135);
        assert.equal(mine.reduce((t, r) => t + r.goals, 0), 1);
    });

    /**
     * The write order the app actually uses, and the bug it used to hide.
     *
     * A coach publishes first, which creates the document. `cv/publish.py` then
     * fills in the heatmap, the shot map's attacking end and the calibration
     * error — it updates, never creates, precisely because a report exists
     * because a coach made one. Then the coach goes back, corrects something,
     * and publishes again.
     *
     * That third step used to be a `set()` with no merge, so it silently
     * deleted everything the pipeline had added. Nothing caught it because
     * nothing in the app reads those fields back before the player's own page
     * does, by which time the coach is long gone. Tagging headers gave coaches
     * a strong new reason to re-publish, which is what turned a latent bug into
     * a likely one.
     */
    it('re-publishing keeps what the pipeline added afterwards', async () => {
        const coachDb = as(COACH);
        const ref = doc(coachDb, 'teams', TEAM, 'matches', 'match1', 'playerReports', 'p1');
        const base = {
            linkedUid: PLAYER.uid, published: true, playerName: 'Alex Vega',
            jerseyNumber: 9, minutesPlayed: 90, goals: 1, cards: 0, stints: [],
            matchDate: '2026-07-01', opponentName: 'Linden', teamName: 'South Brunswick',
        };

        await assertSucceeds(setDoc(ref, base));

        // The pipeline's pass, exactly as cv/publish.py does it.
        await assertSucceeds(updateDoc(ref, {
            cvHeatmap: { cols: 2, rows: 2, values: [0.1, 0.2, 0.3, 0.4] },
            cvAttackingEnd: 'right',
            cvCalibrationErrorM: 0.42,
        }));

        // The coach publishes again, the way assets/db.js now writes it.
        await assertSucceeds(setDoc(
            ref, { ...base, goals: 2 }, { merge: true },
        ));

        const after = (await getDoc(ref)).data();
        assert.equal(after.goals, 2, 'the correction landed');
        assert.equal(after.cvAttackingEnd, 'right', 'the pipeline\'s work survived');
        assert.equal(after.cvCalibrationErrorM, 0.42);
        assert.deepEqual(after.cvHeatmap.values, [0.1, 0.2, 0.3, 0.4]);
    });
});

// ---------------------------------------------------------------- staff

/**
 * A head coach adding an assistant, exactly as the browser does it.
 *
 * The interesting part is the ordering: the claim has to write the full
 * coachUids array, which means reading the team first — and until the moment
 * the claim lands, the invitee cannot read anything else about the team. The
 * `get` grant for pending invitees is what makes the sequence possible at all.
 */
describe('adding a second coach', () => {
    it('runs invite -> read -> claim -> full access, in that order', async () => {
        const coachDb = as(COACH);
        const assistantDb = as(ASSISTANT);

        // Before any invite, the assistant is a stranger to this team.
        await assertFails(getDoc(doc(assistantDb, 'teams', TEAM)));
        await assertFails(getDocs(collection(assistantDb, 'teams', TEAM, 'players')));

        await assertSucceeds(setDoc(
            doc(coachDb, 'invites', ASSISTANT.email, 'from', TEAM),
            {
                playerId: null, role: 'coach', teamName: 'South Brunswick',
                coachName: 'Head Coach', createdAt: serverTimestamp(),
                createdBy: COACH.uid,
            },
        ));

        // The invite alone grants only the team document — not the roster,
        // which is where students' email addresses live.
        const teamSnap = await assertSucceeds(getDoc(doc(assistantDb, 'teams', TEAM)));
        await assertFails(getDocs(collection(assistantDb, 'teams', TEAM, 'players')));

        const coachUids = teamSnap.data().coachUids;
        await assertSucceeds(updateDoc(doc(assistantDb, 'teams', TEAM), {
            coachUids: [...coachUids, ASSISTANT.uid],
        }));

        // Now a full coach: roster, matches, and the staff directory.
        const roster = await assertSucceeds(
            getDocs(collection(assistantDb, 'teams', TEAM, 'players')));
        assert.equal(roster.docs.length, 2);

        await assertSucceeds(getDocs(collection(assistantDb, 'teams', TEAM, 'matches')));

        await assertSucceeds(setDoc(
            doc(assistantDb, 'teams', TEAM, 'staff', ASSISTANT.uid),
            {
                displayName: 'Assistant Coach', emailLower: ASSISTANT.email,
                role: 'coach', joinedAt: serverTimestamp(),
            },
        ));

        const staff = await assertSucceeds(
            getDocs(collection(coachDb, 'teams', TEAM, 'staff')));
        assert.equal(staff.docs.length, 1);
        assert.equal(staff.docs[0].data().emailLower, ASSISTANT.email);
    });

    it('lets one coach run two squads independently', async () => {
        const coachDb = as(COACH);

        await assertSucceeds(setDoc(doc(coachDb, 'teams', 'jv'), {
            name: 'JV', coachUids: [COACH.uid], taggerUids: [],
            archived: false, createdAt: serverTimestamp(), createdBy: COACH.uid,
        }));

        // A player added to JV must not appear on the varsity roster.
        await assertSucceeds(setDoc(doc(coachDb, 'teams', 'jv', 'players', 'j1'), {
            name: 'Freshman Kid', jerseyNumber: 22,
            emailLower: 'freshman@school.org', linkedUid: null, active: true,
            createdAt: serverTimestamp(),
        }));

        const varsity = await assertSucceeds(
            getDocs(collection(coachDb, 'teams', TEAM, 'players')));
        const jv = await assertSucceeds(
            getDocs(collection(coachDb, 'teams', 'jv', 'players')));

        assert.equal(varsity.docs.length, 2);
        assert.equal(jv.docs.length, 1);
        assert.equal(jv.docs[0].data().name, 'Freshman Kid');
    });
});

/**
 * What the tablet can honestly claim about a tap at a field with no signal.
 *
 * `persistentLocalCache` makes the write survive; this is about whether the
 * person holding the tablet is told the truth about it. The old indicator read
 * `navigator.onLine`, which reports a link rather than a reachable server — so
 * on a school Wi-Fi with a captive portal it said "Saved" while nothing had
 * been saved.
 *
 * These drive a real client through a real disconnection, because the property
 * being tested is Firestore's snapshot metadata and nothing else can produce
 * it. The arithmetic over that metadata is pure and lives in
 * assets/report.js::syncState, covered without an emulator in video.test.js.
 */
describe('knowing what has reached the server', () => {
    const logPath = (db, id) =>
        doc(db, 'teams', TEAM, 'matches', MATCH, 'log', id);

    const tap = (seq, clock) => ({
        kind: 'event', type: 'corner', matchClockS: clock, side: 'us',
        playerId: null, subOutId: null, subInId: null, detail: null,
        source: 'live_tag', seq, deviceId: 'devA', revert: null,
        tappedAt: Date.now(), createdAt: serverTimestamp(),
        createdBy: COACH.uid,
    });

    /** The same count `watchSync` reports, off one snapshot. */
    const pendingIn = (snap) =>
        snap.docs.filter((d) => d.metadata.hasPendingWrites).length;

    /** Wait for a metadata-bearing snapshot that satisfies `ready`. */
    function until(db, ready) {
        return new Promise((resolve, reject) => {
            const stop = onSnapshot(
                collection(db, 'teams', TEAM, 'matches', MATCH, 'log'),
                { includeMetadataChanges: true },
                (snap) => {
                    if (!ready(snap)) return;
                    stop();
                    resolve(snap);
                },
                (err) => { stop(); reject(err); },
            );
            setTimeout(() => { stop(); reject(new Error('no matching snapshot')); }, 5000);
        });
    }

    it('counts a tap made with no connection, and clears it on reconnect', async () => {
        const db = as(COACH);

        // Warm the listener up while connected, so the first offline snapshot
        // is a change rather than an initial load.
        await until(db, (s) => !s.metadata.fromCache);

        await disableNetwork(db);

        // Not awaited: offline, setDoc's promise does not settle until the
        // server acknowledges, which is the whole reason the tagging UI must
        // never block on it. The local write lands immediately regardless.
        setDoc(logPath(db, 'devA_000101'), tap(101, 60));
        setDoc(logPath(db, 'devA_000102'), tap(102, 120));

        const offline = await until(db, (s) => pendingIn(s) === 2);
        assert.equal(offline.metadata.fromCache, true,
            'a disconnected read never reached the server');

        await enableNetwork(db);

        const back = await until(db, (s) => pendingIn(s) === 0);
        assert.equal(back.metadata.fromCache, false);
        // And the taps are really there rather than merely no longer pending.
        const ids = back.docs.map((d) => d.id);
        assert.ok(ids.includes('devA_000101'));
        assert.ok(ids.includes('devA_000102'));
    });

    it('a write made while connected is not pending for long', async () => {
        const db = as(COACH);
        await setDoc(logPath(db, 'devA_000201'), tap(201, 200));
        const snap = await until(db, (s) =>
            s.docs.some((d) => d.id === 'devA_000201') && pendingIn(s) === 0);
        assert.equal(pendingIn(snap), 0);
    });
});


describe('erasing a player', () => {
    /**
     * The claim being tested is not "the coach is allowed to delete this" — the
     * rules always allowed it. It is that after the app's erase, nothing
     * carrying the student's name is left anywhere a reader can reach.
     *
     * So every assertion here reads the documents back rather than trusting the
     * writes, and the last one reads them back as the *player*, through the
     * collection-group grant their own portal uses. A report that survives an
     * erase and is still visible to the person it is about is a different
     * failure from one that merely survives.
     */

    /** What the browser does, mirrored: see erasePlayer() in assets/db.js. */
    async function erase(db, teamId, playerId, uid) {
        const matches = await getDocs(collection(db, 'teams', teamId, 'matches'));

        for (const match of matches.docs) {
            const base = ['teams', teamId, 'matches', match.id];
            const batch = writeBatch(db);
            batch.delete(doc(db, ...base, 'roster', playerId));
            batch.delete(doc(db, ...base, 'playerReports', playerId));

            const mapRef = doc(db, ...base, 'cvMapping', 'players');
            const snap = await getDoc(mapRef);
            if (snap.exists()) {
                const kept = {};
                for (const [cluster, id] of Object.entries(snap.data().byCluster || {})) {
                    if (id !== playerId) kept[cluster] = id;
                }
                // Whole document, never `{ merge: true }`: Firestore merges a
                // map key by key, so a smaller byCluster leaves the removed
                // keys in place. This test failed exactly that way first.
                batch.set(mapRef, {
                    byCluster: kept, updatedAt: serverTimestamp(), updatedBy: uid,
                });
            }
            await batch.commit();
        }

        // Read it back before claiming it worked, exactly as erasePlayer()
        // does. Every write above can half-happen — a batch per match, a match
        // list that came back short — and the failure mode is telling a coach a
        // student's data is gone when it is not. It also stops this test from
        // passing on a run where the match list arrived empty and the loop had
        // nothing to do, which is how it flaked before the check existed.
        for (const match of (await getDocs(collection(db, 'teams', teamId, 'matches'))).docs) {
            const base = ['teams', teamId, 'matches', match.id];
            const [roster, report] = await Promise.all([
                getDoc(doc(db, ...base, 'roster', playerId)),
                getDoc(doc(db, ...base, 'playerReports', playerId)),
            ]);
            assert.equal(roster.exists(), false, `roster left in ${match.id}`);
            assert.equal(report.exists(), false, `report left in ${match.id}`);
        }

        await deleteDoc(doc(db, 'invites', PLAYER.email, 'from', teamId));
        await deleteDoc(doc(db, 'teams', teamId, 'players', playerId));
    }

    beforeEach(async () => {
        await testEnv.withSecurityRulesDisabled(async (ctx) => {
            const db = ctx.firestore();
            for (const [id, name, number] of [['p1', 'Alex Vega', 9], ['p2', 'Bench Guy', 15]]) {
                await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'roster', id), {
                    playerName: name, jerseyNumber: number,
                    isStarter: true, isActive: true,
                    stints: [{ inS: 0, outS: null }], version: 0,
                });
                await setDoc(
                    doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', id), {
                        linkedUid: id === 'p1' ? PLAYER.uid : null,
                        published: true, playerName: name, jerseyNumber: number,
                        minutesPlayed: 90, goals: 1, assists: 0, cards: 0,
                        yellowCards: 0, redCards: 0, fouls: 0,
                        stints: [{ inS: 0, outS: null }], matchDate: '2026-07-27',
                    },
                );
            }
            await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'), {
                byCluster: { 0: 'p1', 1: 'p2', 2: 'p1' },
                updatedAt: serverTimestamp(), updatedBy: COACH.uid,
            });
            await setDoc(doc(db, 'invites', PLAYER.email, 'from', TEAM), {
                playerId: 'p1', role: 'player', teamName: 'South Brunswick',
                coachName: 'Coach', createdAt: serverTimestamp(), createdBy: COACH.uid,
            });
        });
    });

    it('leaves nothing with their name in it', async () => {
        const db = as(COACH);
        await erase(db, TEAM, 'p1', COACH.uid);

        const left = await Promise.all([
            getDoc(doc(db, 'teams', TEAM, 'players', 'p1')),
            getDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'roster', 'p1')),
            getDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1')),
        ]);
        assert.deepEqual(left.map((d) => d.exists()), [false, false, false]);
    });

    it('takes the report out of the player’s own portal', async () => {
        // Read the way the portal reads it: a collection-group query scoped to
        // their uid. A report that outlives an erase and is still visible to
        // the person it describes is the failure that matters most.
        const asPlayer = as(PLAYER);
        const mine = () => getDocs(query(
            collectionGroup(asPlayer, 'playerReports'),
            where('linkedUid', '==', PLAYER.uid),
            where('published', '==', true),
        ));

        assert.equal((await mine()).size, 1);
        await erase(as(COACH), TEAM, 'p1', COACH.uid);
        assert.equal((await mine()).size, 0);
    });

    it('does not touch anybody else', async () => {
        // The failure that would be worst: a season of somebody else's work
        // disappearing because a name next to theirs was erased.
        const db = as(COACH);
        await erase(db, TEAM, 'p1', COACH.uid);

        const other = await getDoc(
            doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p2'),
        );
        assert.equal(other.data().playerName, 'Bench Guy');
        assert.equal(other.data().minutesPlayed, 90);
        assert.ok((await getDoc(doc(db, 'teams', TEAM, 'players', 'p2'))).exists());
    });

    it('unpicks the tracked figures without re-attributing them', async () => {
        // The mapping is what ties a crop cut out of the footage to a person.
        // Clusters 0 and 2 must stop pointing at anyone; cluster 1 must still
        // be the other player, not shifted along.
        const db = as(COACH);
        await erase(db, TEAM, 'p1', COACH.uid);

        const mapping = await getDoc(
            doc(db, 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'),
        );
        assert.deepEqual(mapping.data().byCluster, { 1: 'p2' });
    });

    it('keeps the match log, which names nobody', async () => {
        // A substitution records ids, never names, and it is the arithmetic
        // behind every other player's minutes. Deleting it to erase one student
        // would take time off whoever came on for them.
        await testEnv.withSecurityRulesDisabled(async (ctx) => {
            await setDoc(doc(ctx.firestore(), 'teams', TEAM, 'matches', MATCH, 'log', 'e1'), {
                kind: 'sub', type: 'sub', matchClockS: 1800, side: 'us',
                subOutId: 'p1', subInId: 'p2', seq: 1, deviceId: 'd1',
                source: 'live_tag', createdAt: serverTimestamp(), createdBy: COACH.uid,
            });
        });

        const db = as(COACH);
        await erase(db, TEAM, 'p1', COACH.uid);

        const entry = await getDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'log', 'e1'));
        assert.ok(entry.exists());
        // Pseudonymous, and that is the point: what is left is an id that now
        // resolves to nobody.
        assert.equal(entry.data().subOutId, 'p1');
        assert.ok(!JSON.stringify(entry.data()).includes('Alex'));
    });

    it('removes the invitation, which is where the email address lived', async () => {
        await erase(as(COACH), TEAM, 'p1', COACH.uid);
        await testEnv.withSecurityRulesDisabled(async (ctx) => {
            const gone = await getDoc(
                doc(ctx.firestore(), 'invites', PLAYER.email, 'from', TEAM),
            );
            assert.equal(gone.exists(), false);
        });
    });

    it('leaving the team is not erasing, and keeps the reports', async () => {
        // The other half of the choice. A coach who means "they moved away"
        // must not lose the team's own record of matches that happened.
        const db = as(COACH);
        await assertSucceeds(
            updateDoc(doc(db, 'teams', TEAM, 'players', 'p1'), { active: false }),
        );

        const report = await getDoc(
            doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1'),
        );
        assert.equal(report.data().minutesPlayed, 90);
        assert.equal(
            (await getDoc(doc(db, 'teams', TEAM, 'players', 'p1'))).data().active,
            false,
        );
    });
});
