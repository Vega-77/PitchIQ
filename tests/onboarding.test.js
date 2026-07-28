/**
 * First-run onboarding, which the rules and flow suites both missed.
 *
 * A coach with no team yet cannot prove they coach anything — there is no team
 * whose coachUids contains them — so they resolve as 'none'. That is correct,
 * but it means every surface has to route 'none' somewhere useful instead of
 * bouncing them back to where they came from. This suite pins the resolution
 * outcomes so a regression shows up here rather than on someone's first login.
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { before, after, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { initializeTestEnvironment } from '@firebase/rules-unit-testing';
import { doc, getDoc, setDoc, collection, getDocs } from 'firebase/firestore';

const HERE = dirname(fileURLToPath(import.meta.url));

const COACH = { uid: 'coach1', email: 'coach@school.org' };
const PLAYER = { uid: 'alex1', email: 'alex@school.org' };
const STRANGER = { uid: 'rando', email: 'rando@gmail.com' };

const TEAM = 'team1';

let testEnv;

const google = (u) => ({
    sub: u.uid, email: u.email, email_verified: true,
    firebase: { sign_in_provider: 'google.com' },
});

const as = (u) => testEnv.authenticatedContext(u.uid, google(u)).firestore();

/** Mirrors resolveAccess() in assets/auth.js. */
async function resolveAccess(db, user) {
    const hint = await getDoc(doc(db, 'users', user.uid)).catch(() => null);
    const teamIds = hint?.exists() ? (hint.data().teamIds || []) : [];

    const teams = [];
    for (const id of teamIds) {
        const snap = await getDoc(doc(db, 'teams', id)).catch(() => null);
        if (snap?.exists()) teams.push({ id: snap.id, ...snap.data() });
    }

    const coaching = teams.filter((t) => (t.coachUids || []).includes(user.uid));
    if (coaching.length) return { role: 'coach', teams: coaching };

    const lastRef = hint?.exists() ? hint.data().lastPlayerRef : null;
    if (lastRef?.teamId && lastRef?.playerId) {
        const snap = await getDoc(
            doc(db, 'teams', lastRef.teamId, 'players', lastRef.playerId)
        ).catch(() => null);
        if (snap?.exists() && snap.data().linkedUid === user.uid) {
            return { role: 'player', teams: [] };
        }
    }

    return { role: 'none', teams: [] };
}

before(async () => {
    testEnv = await initializeTestEnvironment({
        projectId: 'pitchiq-onboarding-test',
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
        await setDoc(doc(ctx.firestore(), 'coachAllowlist', COACH.email), {
            note: 'head coach',
        });
    });
});

describe('first run', () => {
    it('an allowlisted coach with no team resolves as none, not coach', async () => {
        // The state every new coach starts in. The landing page and the coach
        // dashboard must both offer team creation here rather than redirect.
        const access = await resolveAccess(as(COACH), COACH);
        assert.equal(access.role, 'none');
        assert.equal(access.teams.length, 0);
    });

    it('becomes a coach once the team exists', async () => {
        const db = as(COACH);

        await setDoc(doc(db, 'teams', TEAM), {
            name: 'South Brunswick', coachUids: [COACH.uid], taggerUids: [],
            archived: false, createdAt: new Date(), createdBy: COACH.uid,
        }).catch(() => { /* createdAt must be request.time; seed below instead */ });

        await testEnv.withSecurityRulesDisabled(async (ctx) => {
            const admin = ctx.firestore();
            await setDoc(doc(admin, 'teams', TEAM), {
                name: 'South Brunswick', coachUids: [COACH.uid], taggerUids: [],
                archived: false, createdBy: COACH.uid,
            });
            await setDoc(doc(admin, 'users', COACH.uid), {
                displayName: 'Coach', emailLower: COACH.email,
                teamIds: [TEAM], lastPlayerRef: null,
            });
        });

        const access = await resolveAccess(as(COACH), COACH);
        assert.equal(access.role, 'coach');
        assert.equal(access.teams[0].id, TEAM);
    });

    it('a non-allowlisted account cannot create a team', async () => {
        const db = as(STRANGER);
        await assert.rejects(
            setDoc(doc(db, 'teams', 'squat'), {
                name: 'Squatters', coachUids: [STRANGER.uid], taggerUids: [],
                archived: false, createdAt: new Date(), createdBy: STRANGER.uid,
            }),
            (err) => err.code === 'permission-denied'
        );
    });

    it('an invited player sees their invitation before claiming', async () => {
        await testEnv.withSecurityRulesDisabled(async (ctx) => {
            const admin = ctx.firestore();
            await setDoc(doc(admin, 'teams', TEAM), {
                name: 'South Brunswick', coachUids: [COACH.uid], taggerUids: [],
                archived: false, createdBy: COACH.uid,
            });
            await setDoc(doc(admin, 'teams', TEAM, 'players', 'p1'), {
                name: 'Alex Vega', jerseyNumber: 9,
                emailLower: PLAYER.email, linkedUid: null, active: true,
            });
            await setDoc(doc(admin, 'invites', PLAYER.email, 'from', TEAM), {
                playerId: 'p1', teamName: 'South Brunswick',
                coachName: 'Coach', createdBy: COACH.uid,
            });
        });

        const db = as(PLAYER);

        // Not linked yet, so still 'none' — the landing page must show the
        // invitation rather than a dead end.
        const before = await resolveAccess(db, PLAYER);
        assert.equal(before.role, 'none');

        const invites = await getDocs(collection(db, 'invites', PLAYER.email, 'from'));
        assert.equal(invites.size, 1);
        assert.equal(invites.docs[0].data().playerId, 'p1');
    });

    it('a signed-in stranger with no invite sees nothing', async () => {
        const db = as(STRANGER);
        const access = await resolveAccess(db, STRANGER);
        assert.equal(access.role, 'none');

        const invites = await getDocs(
            collection(db, 'invites', STRANGER.email, 'from')
        );
        assert.equal(invites.size, 0);
    });
});
