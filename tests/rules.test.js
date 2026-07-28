/**
 * Firestore security rules tests.
 *
 * These are the spec. The database holds names and email addresses of high
 * school students, so the cases that matter most are the ones that MUST be
 * denied — particularly privilege escalation by someone already on the roster,
 * which is the most likely real attacker.
 *
 * Run:  npm test        (starts the emulator via firebase emulators:exec)
 */

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { before, after, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds,
} from '@firebase/rules-unit-testing';

import {
  doc, getDoc, setDoc, updateDoc, deleteDoc,
  collection, getDocs, query, where, collectionGroup, serverTimestamp,
} from 'firebase/firestore';

const HERE = dirname(fileURLToPath(import.meta.url));

const COACH = { uid: 'coach1', email: 'coach@school.org' };
const PLAYER = { uid: 'player1', email: 'alex@school.org' };
const OTHER = { uid: 'player2', email: 'jordan@school.org' };
const STRANGER = { uid: 'rando', email: 'rando@gmail.com' };

const TEAM = 'team1';
const VICTIM_TEAM = 'team2';
const MATCH = 'match1';

/** A verified Google identity — what every legitimate user looks like. */
function google(user) {
  return {
    sub: user.uid,
    email: user.email,
    email_verified: true,
    firebase: { sign_in_provider: 'google.com' },
  };
}

let testEnv;

function as(user, tokenOverrides = {}) {
  return testEnv
    .authenticatedContext(user.uid, { ...google(user), ...tokenOverrides })
    .firestore();
}

before(async () => {
  testEnv = await initializeTestEnvironment({
    projectId: 'pitchiq-rules-test',
    firestore: {
      rules: readFileSync(join(HERE, '..', 'firestore.rules'), 'utf8'),
      host: '127.0.0.1',
      port: 8085,
    },
  });
});

after(async () => {
  await testEnv?.cleanup();
});

/** Seed via withSecurityRulesDisabled so fixtures don't depend on the rules. */
beforeEach(async () => {
  await testEnv.clearFirestore();
  await testEnv.withSecurityRulesDisabled(async (ctx) => {
    const db = ctx.firestore();

    await setDoc(doc(db, 'coachAllowlist', COACH.email), { note: 'head coach' });

    await setDoc(doc(db, 'teams', TEAM), {
      name: 'South Brunswick',
      coachUids: [COACH.uid],
      taggerUids: [],
      archived: false,
      createdBy: COACH.uid,
    });

    await setDoc(doc(db, 'teams', VICTIM_TEAM), {
      name: 'Some Other School',
      coachUids: ['someoneelse'],
      taggerUids: [],
      archived: false,
      createdBy: 'someoneelse',
    });

    // Unclaimed roster slot for PLAYER, plus a claimed one for OTHER.
    await setDoc(doc(db, 'teams', TEAM, 'players', 'p1'), {
      name: 'Alex Vega', jerseyNumber: 9,
      emailLower: PLAYER.email, linkedUid: null, active: true,
    });
    await setDoc(doc(db, 'teams', TEAM, 'players', 'p2'), {
      name: 'Jordan Cho', jerseyNumber: 4,
      emailLower: OTHER.email, linkedUid: OTHER.uid, active: true,
    });

    await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH), {
      opponentName: 'Linden', date: '2026-07-27',
      status: 'first_half', finalized: false, createdBy: COACH.uid,
    });

    await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'roster', 'p1'), {
      playerName: 'Alex Vega', jerseyNumber: 9,
      isStarter: true, isActive: true, stints: [{ inS: 0, outS: null }], version: 0,
    });

    await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'log', 'dev_000001'), {
      kind: 'event', type: 'corner', matchClockS: 120, side: 'us',
      playerId: null, detail: null, source: 'live_tag',
      seq: 1, deviceId: 'dev', createdBy: COACH.uid,
    });

    // One published report per player, so cross-player reads can be tested.
    await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1'), {
      linkedUid: PLAYER.uid, published: true, minutesPlayed: 80,
      goals: 1, cards: 0, matchDate: '2026-07-27', opponentName: 'Linden',
    });
    await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p2'), {
      linkedUid: OTHER.uid, published: true, minutesPlayed: 90,
      goals: 0, cards: 1, matchDate: '2026-07-27', opponentName: 'Linden',
    });
    await setDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p3'), {
      linkedUid: PLAYER.uid, published: false, minutesPlayed: 12,
      goals: 0, cards: 0, matchDate: '2026-07-20', opponentName: 'Edison',
    });
  });
});

// =====================================================================
// P0 — privilege escalation
// =====================================================================

describe('privilege escalation', () => {
  it('a self-written user doc grants no access to a team', async () => {
    const db = as(STRANGER);

    // Writing the hint doc is allowed — it just has to be worthless.
    await assertSucceeds(setDoc(doc(db, 'users', STRANGER.uid), {
      displayName: 'Rando', emailLower: STRANGER.email,
      teamIds: [VICTIM_TEAM], lastPlayerRef: null, updatedAt: serverTimestamp(),
    }));

    await assertFails(getDoc(doc(db, 'teams', VICTIM_TEAM)));
    await assertFails(getDocs(collection(db, 'teams', VICTIM_TEAM, 'players')));
  });

  it('a rostered player cannot add themselves to coachUids', async () => {
    const db = as(PLAYER);
    await assertFails(updateDoc(doc(db, 'teams', TEAM), {
      coachUids: [COACH.uid, PLAYER.uid],
    }));
  });

  it('a coach cannot add themselves to another team', async () => {
    const db = as(COACH);
    await assertFails(updateDoc(doc(db, 'teams', VICTIM_TEAM), {
      coachUids: ['someoneelse', COACH.uid],
    }));
  });

  it('the last coach cannot remove themselves and orphan the team', async () => {
    const db = as(COACH);
    await assertFails(updateDoc(doc(db, 'teams', TEAM), { coachUids: [] }));
  });

  it('teams cannot be deleted (no cascade in Firestore)', async () => {
    await assertFails(deleteDoc(doc(as(COACH), 'teams', TEAM)));
  });

  it('team creation requires a coachAllowlist entry', async () => {
    await assertFails(setDoc(doc(as(STRANGER), 'teams', 'newteam'), {
      name: 'Squatters', coachUids: [STRANGER.uid], taggerUids: [],
      archived: false, createdAt: serverTimestamp(), createdBy: STRANGER.uid,
    }));

    await assertSucceeds(setDoc(doc(as(COACH), 'teams', 'newteam'), {
      name: 'JV', coachUids: [COACH.uid], taggerUids: [],
      archived: false, createdAt: serverTimestamp(), createdBy: COACH.uid,
    }));
  });
});

// =====================================================================
// P0 — player data isolation
// =====================================================================

describe('player data isolation', () => {
  it('a player cannot list the roster (email harvest)', async () => {
    await assertFails(getDocs(collection(as(PLAYER), 'teams', TEAM, 'players')));
  });

  it('a player cannot read the match log or roster', async () => {
    const db = as(PLAYER);
    await assertFails(getDocs(collection(db, 'teams', TEAM, 'matches', MATCH, 'log')));
    await assertFails(getDocs(collection(db, 'teams', TEAM, 'matches', MATCH, 'roster')));
    await assertFails(getDoc(doc(db, 'teams', TEAM, 'matches', MATCH)));
  });

  it('a player reads their own report but not a teammate\'s', async () => {
    const db = as(PLAYER);
    const base = ['teams', TEAM, 'matches', MATCH, 'playerReports'];

    await assertSucceeds(getDoc(doc(db, ...base, 'p1')));
    await assertFails(getDoc(doc(db, ...base, 'p2')));
  });

  it('an unpublished report is hidden even from its owner', async () => {
    await assertFails(
      getDoc(doc(as(PLAYER), 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p3'))
    );
  });

  it('the collection-group query must be filtered to the caller', async () => {
    const db = as(PLAYER);

    await assertSucceeds(getDocs(query(
      collectionGroup(db, 'playerReports'),
      where('linkedUid', '==', PLAYER.uid),
      where('published', '==', true),
    )));

    // Unfiltered, and filtered to someone else — both must fail.
    await assertFails(getDocs(collectionGroup(db, 'playerReports')));
    await assertFails(getDocs(query(
      collectionGroup(db, 'playerReports'),
      where('linkedUid', '==', OTHER.uid),
      where('published', '==', true),
    )));
  });
});

// =====================================================================
// Identity guards
// =====================================================================

describe('identity', () => {
  it('rejects unverified email', async () => {
    const db = as(COACH, { email_verified: false });
    await assertFails(getDoc(doc(db, 'teams', TEAM)));
  });

  it('rejects non-Google providers', async () => {
    const db = as(COACH, { firebase: { sign_in_provider: 'password' } });
    await assertFails(getDoc(doc(db, 'teams', TEAM)));
  });

  it('rejects anonymous sessions', async () => {
    const db = testEnv.authenticatedContext('anon', {
      sub: 'anon', firebase: { sign_in_provider: 'anonymous' },
    }).firestore();
    await assertFails(getDoc(doc(db, 'teams', TEAM)));
  });

  it('rejects unauthenticated access', async () => {
    const db = testEnv.unauthenticatedContext().firestore();
    await assertFails(getDoc(doc(db, 'teams', TEAM)));
  });
});

// =====================================================================
// The claim flow
// =====================================================================

describe('claiming a roster slot', () => {
  const slot = (db) => doc(db, 'teams', TEAM, 'players', 'p1');

  it('succeeds when the verified email matches the roster record', async () => {
    await assertSucceeds(updateDoc(slot(as(PLAYER)), { linkedUid: PLAYER.uid }));
  });

  it('works for a mixed-case Google Workspace address', async () => {
    // Workspace returns John.Smith@school.org where Gmail returns lowercase.
    // This is the case that passes local testing and fails in production.
    const db = as({ uid: PLAYER.uid, email: 'Alex@School.org' });
    await assertSucceeds(updateDoc(slot(db), { linkedUid: PLAYER.uid }));
  });

  it('fails when the email does not match', async () => {
    await assertFails(updateDoc(slot(as(STRANGER)), { linkedUid: STRANGER.uid }));
  });

  it('fails when the slot is already claimed', async () => {
    await assertFails(updateDoc(
      doc(as(PLAYER), 'teams', TEAM, 'players', 'p2'), { linkedUid: PLAYER.uid }
    ));
  });

  it('fails when claiming for a different uid', async () => {
    await assertFails(updateDoc(slot(as(PLAYER)), { linkedUid: STRANGER.uid }));
  });

  it('cannot smuggle other field changes alongside the claim', async () => {
    await assertFails(updateDoc(slot(as(PLAYER)), {
      linkedUid: PLAYER.uid, jerseyNumber: 1,
    }));
  });

  it('a coach cannot link a slot to an arbitrary account', async () => {
    await assertFails(updateDoc(slot(as(COACH)), { linkedUid: STRANGER.uid }));
  });
});

// =====================================================================
// Invites
// =====================================================================

describe('invites', () => {
  beforeEach(async () => {
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), 'invites', PLAYER.email, 'from', TEAM), {
        playerId: 'p1', teamName: 'South Brunswick', coachName: 'Coach',
        createdBy: COACH.uid,
      });
    });
  });

  it('a player reads only invites addressed to them', async () => {
    await assertSucceeds(
      getDocs(collection(as(PLAYER), 'invites', PLAYER.email, 'from'))
    );
    await assertFails(
      getDocs(collection(as(STRANGER), 'invites', PLAYER.email, 'from'))
    );
  });

  it('a coach cannot write an invite on behalf of another team', async () => {
    await assertFails(setDoc(
      doc(as(COACH), 'invites', PLAYER.email, 'from', VICTIM_TEAM),
      {
        playerId: 'p1', teamName: 'Some Other School', coachName: 'Coach',
        createdAt: serverTimestamp(), createdBy: COACH.uid,
      }
    ));
  });

  it('an invite must point at a real roster player', async () => {
    await assertFails(setDoc(
      doc(as(COACH), 'invites', 'ghost@school.org', 'from', TEAM),
      {
        playerId: 'does-not-exist', teamName: 'South Brunswick',
        coachName: 'Coach', createdAt: serverTimestamp(), createdBy: COACH.uid,
      }
    ));
  });
});

// =====================================================================
// Write shape validation
// =====================================================================

describe('write validation', () => {
  const logRef = (db, id) => doc(db, 'teams', TEAM, 'matches', MATCH, 'log', id);

  function entry(overrides = {}) {
    return {
      kind: 'event', type: 'corner', matchClockS: 300, side: 'us',
      playerId: null, subOutId: null, subInId: null, detail: null,
      source: 'live_tag', seq: 2, deviceId: 'dev', revert: null,
      tappedAt: 1234567890, createdAt: serverTimestamp(), createdBy: COACH.uid,
      ...overrides,
    };
  }

  it('accepts a well-formed entry', async () => {
    await assertSucceeds(setDoc(logRef(as(COACH), 'dev_000002'), entry()));
  });

  it('rejects unknown event types', async () => {
    await assertFails(setDoc(logRef(as(COACH), 'dev_000003'), entry({ type: 'nonsense' })));
  });

  it('rejects out-of-range clock values', async () => {
    const db = as(COACH);
    await assertFails(setDoc(logRef(db, 'dev_000004'), entry({ matchClockS: -5 })));
    await assertFails(setDoc(logRef(db, 'dev_000005'), entry({ matchClockS: 999999 })));
  });

  it('rejects an oversized detail string', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000006'), entry({ detail: 'x'.repeat(500) })
    ));
  });

  it('rejects extra fields', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000007'), entry({ smuggled: 'value' })
    ));
  });

  it('rejects attributing our player to an opponent event', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000008'), entry({ side: 'them', playerId: 'p1' })
    ));
  });

  it('rejects rewriting history', async () => {
    await assertFails(updateDoc(logRef(as(COACH), 'dev_000001'), { type: 'goal' }));
  });

  it('requires a version bump on roster updates', async () => {
    const ref = doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'roster', 'p1');

    await assertFails(updateDoc(ref, { isActive: false, version: 0 }));
    await assertSucceeds(updateDoc(ref, {
      isActive: false,
      stints: [{ inS: 0, outS: 400 }],
      version: 1,
    }));
  });
});
