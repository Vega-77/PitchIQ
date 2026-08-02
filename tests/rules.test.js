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
const ASSISTANT = { uid: 'assist1', email: 'assistant@school.org' };
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

/** Seed an invite directly, so claim tests don't depend on the invite rule. */
function seedStaffInvite(emailLower, teamId, role) {
  return testEnv.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'invites', emailLower, 'from', teamId), {
      playerId: null,
      role,
      teamName: 'South Brunswick',
      coachName: 'Head Coach',
      createdBy: COACH.uid,
    });
  });
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

    // An ordinary player invite, so staff-claim tests can prove it is not
    // interchangeable with a coaching one.
    await setDoc(doc(db, 'invites', PLAYER.email, 'from', TEAM), {
      playerId: 'p1', role: 'player', teamName: 'South Brunswick',
      coachName: 'Head Coach', createdBy: COACH.uid,
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

  // ---- multiple coaches per team ----
  //
  // The staff claim is the one place a non-coach may write to a team document,
  // so every way of abusing it is worth pinning down.

  it('an assistant cannot join a team without an invite', async () => {
    await assertFails(updateDoc(doc(as(ASSISTANT), 'teams', TEAM), {
      coachUids: [COACH.uid, ASSISTANT.uid],
    }));
  });

  it('an invited assistant can add themselves, and only themselves', async () => {
    await seedStaffInvite(ASSISTANT.email, TEAM, 'coach');
    const db = as(ASSISTANT);

    // The invite names ASSISTANT, so smuggling a third party in alongside them
    // must still fail.
    await assertFails(updateDoc(doc(db, 'teams', TEAM), {
      coachUids: [COACH.uid, ASSISTANT.uid, STRANGER.uid],
    }));

    await assertSucceeds(updateDoc(doc(db, 'teams', TEAM), {
      coachUids: [COACH.uid, ASSISTANT.uid],
    }));
  });

  it('a player invite cannot be used to claim a coaching seat', async () => {
    // PLAYER already has a player invite seeded for TEAM.
    await assertFails(updateDoc(doc(as(PLAYER), 'teams', TEAM), {
      coachUids: [COACH.uid, PLAYER.uid],
    }));
  });

  it("a staff invite for one team cannot claim a seat on another", async () => {
    await seedStaffInvite(ASSISTANT.email, TEAM, 'coach');
    await assertFails(updateDoc(doc(as(ASSISTANT), 'teams', VICTIM_TEAM), {
      coachUids: ['someoneelse', ASSISTANT.uid],
    }));
  });

  it('the staff claim cannot smuggle in other field changes', async () => {
    await seedStaffInvite(ASSISTANT.email, TEAM, 'coach');
    await assertFails(updateDoc(doc(as(ASSISTANT), 'teams', TEAM), {
      coachUids: [COACH.uid, ASSISTANT.uid],
      name: 'Renamed By An Invitee',
    }));
  });

  it('claiming cannot drop an existing coach', async () => {
    await seedStaffInvite(ASSISTANT.email, TEAM, 'coach');
    await assertFails(updateDoc(doc(as(ASSISTANT), 'teams', TEAM), {
      coachUids: [ASSISTANT.uid],
    }));
  });

  it('only a coach can issue a staff invite', async () => {
    await assertFails(setDoc(
      doc(as(PLAYER), 'invites', ASSISTANT.email, 'from', TEAM),
      {
        playerId: null, role: 'coach', teamName: 'South Brunswick',
        coachName: 'Not A Coach', createdAt: serverTimestamp(),
        createdBy: PLAYER.uid,
      },
    ));
  });

  it('a staff invite must not carry a roster slot', async () => {
    await assertFails(setDoc(
      doc(as(COACH), 'invites', ASSISTANT.email, 'from', TEAM),
      {
        playerId: 'p1', role: 'coach', teamName: 'South Brunswick',
        coachName: 'Coach', createdAt: serverTimestamp(), createdBy: COACH.uid,
      },
    ));
  });

  it('an assistant cannot remove the head coach who created the team', async () => {
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await updateDoc(doc(ctx.firestore(), 'teams', TEAM), {
        coachUids: [COACH.uid, ASSISTANT.uid],
      });
    });

    // The assistant may still edit the team in every other way.
    await assertSucceeds(updateDoc(doc(as(ASSISTANT), 'teams', TEAM), {
      name: 'South Brunswick Varsity',
    }));

    await assertFails(updateDoc(doc(as(ASSISTANT), 'teams', TEAM), {
      coachUids: [ASSISTANT.uid],
    }));

    // The creator can.
    await assertSucceeds(updateDoc(doc(as(COACH), 'teams', TEAM), {
      coachUids: [COACH.uid],
    }));
  });

  it('the staff directory carries no authority and cannot be spoofed', async () => {
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await updateDoc(doc(ctx.firestore(), 'teams', TEAM), {
        coachUids: [COACH.uid, ASSISTANT.uid],
      });
    });

    // Writing a directory entry for someone else, or under another address,
    // must fail — it is what the UI shows next to each uid.
    await assertFails(setDoc(doc(as(ASSISTANT), 'teams', TEAM, 'staff', COACH.uid), {
      displayName: 'Impostor', emailLower: COACH.email, role: 'coach',
      joinedAt: serverTimestamp(),
    }));

    await assertFails(setDoc(doc(as(ASSISTANT), 'teams', TEAM, 'staff', ASSISTANT.uid), {
      displayName: 'Assistant', emailLower: COACH.email, role: 'coach',
      joinedAt: serverTimestamp(),
    }));

    await assertSucceeds(setDoc(doc(as(ASSISTANT), 'teams', TEAM, 'staff', ASSISTANT.uid), {
      displayName: 'Assistant', emailLower: ASSISTANT.email, role: 'coach',
      joinedAt: serverTimestamp(),
    }));

    // A directory entry alone grants nothing on a team you do not coach.
    await assertFails(setDoc(doc(as(STRANGER), 'teams', TEAM, 'staff', STRANGER.uid), {
      displayName: 'Rando', emailLower: STRANGER.email, role: 'coach',
      joinedAt: serverTimestamp(),
    }));
    await assertFails(getDocs(collection(as(STRANGER), 'teams', TEAM, 'staff')));
  });

  it('a player cannot read the staff directory', async () => {
    await assertFails(getDocs(collection(as(PLAYER), 'teams', TEAM, 'staff')));
  });

  // ---- multiple teams per coach ----

  it('one coach can hold several teams at once', async () => {
    const db = as(COACH);
    await assertSucceeds(setDoc(doc(db, 'teams', 'varsity'), {
      name: 'Varsity', coachUids: [COACH.uid], taggerUids: [],
      archived: false, createdAt: serverTimestamp(), createdBy: COACH.uid,
    }));
    await assertSucceeds(setDoc(doc(db, 'teams', 'jv'), {
      name: 'JV', coachUids: [COACH.uid], taggerUids: [],
      archived: false, createdAt: serverTimestamp(), createdBy: COACH.uid,
    }));

    await assertSucceeds(getDoc(doc(db, 'teams', 'varsity')));
    await assertSucceeds(getDoc(doc(db, 'teams', 'jv')));
  });

  it('an assistant on one team gains nothing on the head coach\'s other team', async () => {
    await seedStaffInvite(ASSISTANT.email, TEAM, 'coach');
    await assertSucceeds(updateDoc(doc(as(ASSISTANT), 'teams', TEAM), {
      coachUids: [COACH.uid, ASSISTANT.uid],
    }));

    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), 'teams', 'jv'), {
        name: 'JV', coachUids: [COACH.uid], taggerUids: [],
        archived: false, createdBy: COACH.uid,
      });
    });

    await assertFails(getDoc(doc(as(ASSISTANT), 'teams', 'jv')));
    await assertFails(getDocs(collection(as(ASSISTANT), 'teams', 'jv', 'players')));
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

  it('a coach can read any of their own team\'s player reports', async () => {
    // The coach dashboard's player view depends on this. Coaches are not the
    // linkedUid on anyone's report, so they cannot use the collection-group
    // path and must read each report by its own path instead.
    const db = as(COACH);
    const base = ['teams', TEAM, 'matches', MATCH, 'playerReports'];

    await assertSucceeds(getDoc(doc(db, ...base, 'p1')));
    await assertSucceeds(getDoc(doc(db, ...base, 'p2')));
    // Including ones not yet published — the coach is the one who publishes.
    await assertSucceeds(getDoc(doc(db, ...base, 'p3')));
    await assertSucceeds(getDocs(collection(db, ...base)));
  });

  it('a coach of another team cannot', async () => {
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), 'users', 'othercoach'), {
        displayName: 'Other', emailLower: 'other@x.org', teamIds: [VICTIM_TEAM],
      });
    });
    const db = testEnv
      .authenticatedContext('othercoach', google({ uid: 'othercoach', email: 'other@x.org' }))
      .firestore();

    await assertFails(
      getDoc(doc(db, 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1'))
    );
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

// ---------------------------------------------------------------------------
// match video
// ---------------------------------------------------------------------------

describe('match video link', () => {
  const match = () => doc(as(COACH), 'teams', TEAM, 'matches', MATCH);

  it('a coach can attach a video and an offset', async () => {
    await assertSucceeds(updateDoc(match(), {
      videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
      videoOffsetS: 120,
    }));
  });

  it('clearing the link is allowed', async () => {
    await assertSucceeds(updateDoc(match(), { videoUrl: null, videoOffsetS: 0 }));
  });

  it('rejects a non-https link', async () => {
    // The site is served over HTTPS, so an http:// video is blocked as mixed
    // content — it would look like a broken page rather than a bad setting.
    await assertFails(updateDoc(match(), {
      videoUrl: 'http://www.youtube.com/watch?v=dQw4w9WgXcQ',
    }));
  });

  it('rejects a javascript: link', async () => {
    // This string ends up in an element's src attribute.
    await assertFails(updateDoc(match(), { videoUrl: 'javascript:alert(1)' }));
  });

  it('rejects an absurd offset', async () => {
    await assertFails(updateDoc(match(), { videoOffsetS: 999999 }));
    await assertFails(updateDoc(match(), { videoOffsetS: 'soon' }));
  });

  it('rejects an over-long link', async () => {
    await assertFails(updateDoc(match(), {
      videoUrl: `https://example.com/${'x'.repeat(600)}.mp4`,
    }));
  });

  it('a player cannot attach a video', async () => {
    await assertFails(updateDoc(
      doc(as(PLAYER), 'teams', TEAM, 'matches', MATCH),
      { videoUrl: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ' },
    ));
  });
});

// ---------------------------------------------------------------------------
// cvStats — what the video pipeline derived
// ---------------------------------------------------------------------------

describe('cvStats', () => {
  const base = ['teams', TEAM, 'matches', MATCH, 'cvStats'];

  beforeEach(async () => {
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      const db = ctx.firestore();
      // A student manager who runs the tablet: writes the log, never reads
      // minors' emails. They should see the CV stats too.
      await setDoc(doc(db, 'teams', TEAM), {
        name: 'South Brunswick',
        coachUids: [COACH.uid],
        taggerUids: [STRANGER.uid],
        archived: false,
        createdBy: COACH.uid,
      });
      await setDoc(doc(db, ...base, 'summary'), {
        schemaVersion: 1,
        trustworthy: false,
        quality: { ballSeenShare: 0.65 },
        teams: {},
      });
    });
  });

  it('nobody can write it, not even a coach', async () => {
    // These documents come from cv/publish.py through the Admin SDK, which
    // bypasses these rules entirely. So this rule is not what protects the
    // data — it is what keeps "the pipeline produced this" a true statement.
    // A coach reading a stat line has no way to tell a derived figure from one
    // a browser posted.
    await assertFails(setDoc(doc(as(COACH), ...base, 'summary'), { teams: {} }));
    await assertFails(setDoc(doc(as(COACH), ...base, 'identity'), { clusters: [] }));
    await assertFails(setDoc(doc(as(STRANGER), ...base, 'summary'), { teams: {} }));
    await assertFails(setDoc(doc(as(PLAYER), ...base, 'summary'), { teams: {} }));
  });

  it('a coach and a tagger can read it', async () => {
    await assertSucceeds(getDoc(doc(as(COACH), ...base, 'summary')));
    await assertSucceeds(getDoc(doc(as(STRANGER), ...base, 'summary')));
    await assertSucceeds(getDocs(collection(as(COACH), ...base)));
  });

  it('a player cannot read the team-wide CV stats', async () => {
    // Same boundary as the rest of the match: a player's entire read surface
    // is their own report. Team stats would expose the whole squad's work.
    await assertFails(getDoc(doc(as(PLAYER), ...base, 'summary')));
  });

  it('a coach of another team cannot read it', async () => {
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(doc(ctx.firestore(), 'users', 'othercoach2'), {
        displayName: 'Other', emailLower: 'other2@x.org', teamIds: [VICTIM_TEAM],
      });
    });
    const db = testEnv
      .authenticatedContext('othercoach2', google({ uid: 'othercoach2', email: 'other2@x.org' }))
      .firestore();

    await assertFails(getDoc(doc(db, ...base, 'summary')));
  });

  it('a coach can record which figure is which player', async () => {
    await assertSucceeds(setDoc(
      doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'),
      { byCluster: { 0: 'p1', 3: 'p2' }, updatedAt: serverTimestamp(), updatedBy: COACH.uid },
    ));
  });

  it('a tagger can read the mapping but not write it', async () => {
    // A student manager runs the tablet; saying who is who is a coach's call.
    await testEnv.withSecurityRulesDisabled(async (ctx) => {
      await setDoc(
        doc(ctx.firestore(), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'),
        { byCluster: { 0: 'p1' }, updatedBy: COACH.uid },
      );
    });

    await assertSucceeds(getDoc(
      doc(as(STRANGER), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players')));
    await assertFails(setDoc(
      doc(as(STRANGER), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'),
      { byCluster: { 0: 'p1' }, updatedAt: serverTimestamp(), updatedBy: STRANGER.uid },
    ));
  });

  it('a player cannot decide who the video was tracking', async () => {
    await assertFails(setDoc(
      doc(as(PLAYER), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'),
      { byCluster: { 0: 'p1' }, updatedAt: serverTimestamp(), updatedBy: PLAYER.uid },
    ));
  });

  it('the mapping cannot be attributed to someone else', async () => {
    await assertFails(setDoc(
      doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players'),
      { byCluster: { 0: 'p1' }, updatedAt: serverTimestamp(), updatedBy: PLAYER.uid },
    ));
  });

  it('the mapping rejects stray fields and bad shapes', async () => {
    const ref = doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'cvMapping', 'players');

    await assertFails(setDoc(ref, {
      byCluster: { 0: 'p1' }, updatedAt: serverTimestamp(), updatedBy: COACH.uid,
      // cvStats is pipeline-authored; smuggling stats in through the mapping
      // document would blur exactly the line this collection exists to keep.
      touches: 900,
    }));
    await assertFails(setDoc(ref, {
      byCluster: 'p1', updatedAt: serverTimestamp(), updatedBy: COACH.uid,
    }));
  });

  // ------------------------------------------------ the coach's verdict on it

  const review = (db) =>
    doc(db, 'teams', TEAM, 'matches', MATCH, 'cvReview', 'decisions');

  const decisions = (uid, over = {}) => ({
    byEvent: { 'e1': { status: 'confirmed' } },
    missed: [{ clockS: 750, type: 'shot', playerId: null }],
    updatedAt: serverTimestamp(),
    updatedBy: uid,
    ...over,
  });

  it('a coach can record what they made of each candidate', async () => {
    await assertSucceeds(setDoc(review(as(COACH)), decisions(COACH.uid)));
  });

  it('a tagger can read the review but not write it', async () => {
    // STRANGER is this block's student manager — a tagger, per the fixture
    // above. They should see what the coach made of the video without being
    // able to overrule it.
    await assertSucceeds(setDoc(review(as(COACH)), decisions(COACH.uid)));
    await assertSucceeds(getDoc(review(as(STRANGER))));
    await assertFails(setDoc(review(as(STRANGER)), decisions(STRANGER.uid)));
  });

  it('somebody outside the team cannot read the review at all', async () => {
    // OTHER plays for this team but has no staff role of any kind.
    await assertFails(getDoc(review(as(OTHER))));
  });

  it('a player cannot read or write the review', async () => {
    await assertFails(getDoc(review(as(PLAYER))));
    await assertFails(setDoc(review(as(PLAYER)), decisions(PLAYER.uid)));
  });

  it('the review cannot be attributed to someone else', async () => {
    await assertFails(setDoc(review(as(COACH)), decisions(PLAYER.uid)));
  });

  it('the review rejects stray fields and bad shapes', async () => {
    await assertFails(setDoc(review(as(COACH)),
      decisions(COACH.uid, { touches: 900 })));
    await assertFails(setDoc(review(as(COACH)),
      decisions(COACH.uid, { byEvent: 'confirmed' })));
    await assertFails(setDoc(review(as(COACH)),
      decisions(COACH.uid, { missed: { clockS: 1 } })));
  });

  it('a runaway review is capped rather than becoming unreadable', async () => {
    const byEvent = {};
    for (let i = 0; i < 1501; i += 1) byEvent[`e${i}`] = { status: 'confirmed' };
    await assertFails(setDoc(review(as(COACH)), decisions(COACH.uid, { byEvent })));

    const missed = Array.from({ length: 301 }, (_, i) => ({ clockS: i, type: 'pass' }));
    await assertFails(setDoc(review(as(COACH)), decisions(COACH.uid, { missed })));
  });

  it('the review cannot forge a CV stat', async () => {
    // The whole point of the split: cvStats is what the pipeline measured and
    // stays unwritable, however much a coach disagrees with it.
    await assertFails(setDoc(
      doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'cvStats', 'events'),
      { events: [{ id: 'e1', type: 'goal' }] },
    ));
  });

  it('a coach can push a video link onto an already-published report', async () => {
    // publishReports copies videoUrl at publish time, and the ordinary order of
    // events is publish, then upload the footage, then paste the link. Without
    // this partial update every player's report says "no video for this match
    // yet" until somebody re-publishes. The update carries only the two video
    // fields, so it leans on request.resource.data being the merged document
    // rather than the patch — published/minutesPlayed/goals come from the
    // stored report.
    await assertSucceeds(updateDoc(
      doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1'),
      { videoUrl: 'https://youtu.be/dQw4w9WgXcQ', videoOffsetS: 120 },
    ));
  });

  it('a player report refuses a video link that is not https', async () => {
    // This is the copy the player portal actually reads, and the string in it
    // becomes an iframe src on a page a minor opens. The match document has
    // been guarded since the field existed; this one had the same coach write
    // path and no guard at all.
    const base = doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1');

    await assertFails(updateDoc(base, { videoUrl: 'javascript:alert(1)' }));
    await assertFails(updateDoc(base, { videoUrl: 'http://example.com/m.mp4' }));
    await assertFails(updateDoc(base, { videoUrl: 'data:text/html,<script>' }));
    await assertFails(updateDoc(base, { videoUrl: 42 }));
  });

  it('a player report refuses an absurd video offset', async () => {
    const base = doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1');
    await assertFails(updateDoc(base, { videoOffsetS: 999999 }));
    await assertFails(updateDoc(base, { videoOffsetS: 'soon' }));
  });

  it('clearing the video link is allowed', async () => {
    // A coach who pasted the wrong link has to be able to take it back.
    await assertSucceeds(updateDoc(
      doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1'),
      { videoUrl: null, videoOffsetS: 0 },
    ));
  });

  it('a player report still accepts the prefixed CV fields', async () => {
    // playerReports has no keys().hasOnly(), so cv/publish.py can add its
    // fields without a rules change. Pinned because that is load-bearing and
    // invisible — a later tightening of this rule would break publishing with
    // no other warning.
    await assertSucceeds(updateDoc(
      doc(as(COACH), 'teams', TEAM, 'matches', MATCH, 'playerReports', 'p1'),
      { cvTouches: 41, cvPassesCompleted: 22, cvDistanceM: 3100.5 },
    ));
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
      playerId: null, assistPlayerId: null, cardColor: null,
      subOutId: null, subInId: null, detail: null,
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

  it('accepts a card that states its colour', async () => {
    await assertSucceeds(setDoc(
      logRef(as(COACH), 'dev_000020'),
      entry({ type: 'card', cardColor: 'yellow', playerId: 'p1', seq: 20 })
    ));
  });

  it('rejects a card with no colour', async () => {
    // "Card" alone is useless after the match — yellow and red mean
    // completely different things.
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000021'),
      entry({ type: 'card', playerId: 'p1', seq: 21 })
    ));
  });

  it('rejects an invented card colour', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000022'),
      entry({ type: 'card', cardColor: 'orange', playerId: 'p1', seq: 22 })
    ));
  });

  it('rejects a colour on something that is not a card', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000023'),
      entry({ type: 'corner', cardColor: 'yellow', seq: 23 })
    ));
  });

  it('accepts a goal with an assist', async () => {
    await assertSucceeds(setDoc(
      logRef(as(COACH), 'dev_000024'),
      entry({ type: 'goal', playerId: 'p1', assistPlayerId: 'p2', seq: 24 })
    ));
  });

  it('rejects an assist on a non-goal', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000025'),
      entry({ type: 'corner', assistPlayerId: 'p2', seq: 25 })
    ));
  });

  it('rejects a player assisting their own goal', async () => {
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000026'),
      entry({ type: 'goal', playerId: 'p1', assistPlayerId: 'p1', seq: 26 })
    ));
  });

  it('rejects an assist credited on an opponent goal', async () => {
    // We do not have the opposition roster, so an assist there is meaningless.
    await assertFails(setDoc(
      logRef(as(COACH), 'dev_000027'),
      entry({ type: 'goal', side: 'them', assistPlayerId: 'p2', seq: 27 })
    ));
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
