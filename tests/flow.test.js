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
    doc, setDoc, getDoc, updateDoc, collection, getDocs,
    query, where, collectionGroup, writeBatch, serverTimestamp,
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
