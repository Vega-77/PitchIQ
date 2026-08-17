// One squad, one filmed match, one played match, shared by every page.
//
// Deliberately small and deliberately awkward: a substitute who came on, a
// player on a yellow, a match with a log and a match without one. A fixture
// where everybody played ninety minutes and nothing happened would load every
// page and prove almost nothing.

export const COACH = {
    uid: 'uid-coach',
    email: 'coach@example.com',
    displayName: 'A Coach',
    emailVerified: true,
};

export const STUDENT = {
    uid: 'uid-rae',
    email: 'rae@example.com',
    displayName: 'Rae Nkemelu',
    emailVerified: true,
};

export const TEAM_ID = 'team-1';
export const MATCH_ID = 'match-1';

const log = (id, entry) => [
    `teams/${TEAM_ID}/matches/${MATCH_ID}/log/${id}`,
    {
        kind: 'event', type: null, matchClockS: 0, side: 'us', playerId: null,
        assistPlayerId: null, cardColor: null, subOutId: null, subInId: null,
        detail: null, source: 'live_tag', seq: 1, deviceId: 'dev-a',
        revert: null, tappedAt: 0, createdBy: COACH.uid, ...entry,
    },
];

/**
 * A published per-player report, the way `publishReports` writes one.
 *
 * The two students get deliberately different seasons. A fixture where every
 * player's totals matched would load the coach's player view twice and never
 * notice a rail still showing the first one — which is the bug this fixture was
 * built for.
 */
const report = (matchId, playerId, fields) => [
    `teams/${TEAM_ID}/matches/${matchId}/playerReports/${playerId}`,
    {
        published: true, linkedUid: null, jerseyNumber: null,
        minutesPlayed: 90, minutesKnown: true, goals: 0, assists: 0,
        cards: 0, yellowCards: 0, redCards: 0, fouls: 0, stints: [{ inS: 0, outS: 5400 }],
        matchDate: '', opponentName: '', teamName: 'Riverside High',
        scoreUs: 0, scoreThem: 0, teamCounts: null, timeline: [],
        matchId, videoUrl: null, videoOffsetS: 0, secondHalfVideoS: null,
        halfTimeClockS: null, cvTouches: null,
        ...fields,
    },
];

/**
 * A filmed match: the summary the pipeline publishes, plus the candidate events
 * the review tool works through.
 *
 * Built from `assets/sample-report.js` rather than invented again here, so the
 * one place that describes what a run looks like stays the one place. The
 * caller passes it in — most pages are opened against a match nobody filmed,
 * because most matches are.
 */
export async function filmed() {
    // No `?v=` on this one specifier, deliberately. Every import inside the app
    // carries the cache-busting stamp and `python stamp_version.py` rewrites
    // them all; a stamp in here would be a copy of that number nothing updates,
    // and it would go stale silently rather than fail. sample-report.js imports
    // nothing, so a second instance of it costs nothing either.
    const { sampleCvSummary, samplePassEvents } =
        await import('../assets/sample-report.js');
    const cv = sampleCvSummary();
    const base = `teams/${TEAM_ID}/matches/${MATCH_ID}`;

    const events = samplePassEvents();
    const counts = {};
    for (const event of events) counts[event.type] = (counts[event.type] || 0) + 1;

    return {
        ...fixture(),
        [`${base}/cvStats/summary`]: cv,
        ...(cv.identity ? { [`${base}/cvStats/identity`]: cv.identity } : {}),
        [`${base}/cvStats/events`]: {
            schemaVersion: 3, events, counts, truncated: false,
            droppedBelowConfidence: null,
        },
    };
}

export function fixture() {
    return Object.fromEntries([
        [`users/${COACH.uid}`, { teamIds: [TEAM_ID] }],
        [`users/${STUDENT.uid}`, {
            teamIds: [],
            lastPlayerRef: { teamId: TEAM_ID, playerId: 'p-rae' },
        }],

        [`teams/${TEAM_ID}`, {
            name: 'Riverside High',
            coachUids: [COACH.uid],
            taggerUids: [],
            archived: false,
            createdBy: COACH.uid,
        }],
        [`teams/${TEAM_ID}/staff/${COACH.uid}`, { name: 'A Coach', role: 'coach' }],

        [`teams/${TEAM_ID}/players/p-rae`, {
            name: 'Rae Nkemelu', jerseyNumber: 7, emailLower: STUDENT.email,
            position: 'mid', linkedUid: STUDENT.uid, active: true,
        }],
        [`teams/${TEAM_ID}/players/p-alex`, {
            name: 'Alex Vega', jerseyNumber: 9, emailLower: '',
            position: 'fwd', linkedUid: null, active: true,
        }],
        [`teams/${TEAM_ID}/players/p-sam`, {
            name: 'Sam Okonjo', jerseyNumber: 14, emailLower: '',
            position: null, linkedUid: null, active: true,
        }],

        // Played, tagged, and mid-match: status 'halftime' is the state the
        // half-time page exists for, and the one no other fixture covers.
        [`teams/${TEAM_ID}/matches/${MATCH_ID}`, {
            opponentName: 'Northgate', date: '2026-08-14', status: 'halftime',
            finalized: false, scoreUs: 1, scoreThem: 0, videoOffsetS: 0,
            halfTimeClockS: 2700, videoUrl: null, createdBy: COACH.uid,
        }],
        // Scheduled, nothing tagged: the empty path every page also has to draw.
        [`teams/${TEAM_ID}/matches/match-2`, {
            opponentName: 'Eastvale', date: '2026-08-21', status: 'scheduled',
            finalized: false, scoreUs: 0, scoreThem: 0, createdBy: COACH.uid,
        }],

        [`teams/${TEAM_ID}/matches/${MATCH_ID}/roster/p-rae`, {
            playerName: 'Rae Nkemelu', jerseyNumber: 7, isStarter: true,
            isActive: true, stints: [{ inS: 0, outS: null }], version: 0,
        }],
        [`teams/${TEAM_ID}/matches/${MATCH_ID}/roster/p-alex`, {
            playerName: 'Alex Vega', jerseyNumber: 9, isStarter: true,
            isActive: false, stints: [{ inS: 0, outS: 1800 }], version: 1,
        }],
        [`teams/${TEAM_ID}/matches/${MATCH_ID}/roster/p-sam`, {
            playerName: 'Sam Okonjo', jerseyNumber: 14, isStarter: false,
            isActive: true, stints: [{ inS: 1800, outS: null }], version: 1,
        }],

        // Two matches already played and published, so a season exists to open.
        [`teams/${TEAM_ID}/matches/match-0`, {
            opponentName: 'Westbrook', date: '2026-08-07', status: 'full_time',
            finalized: true, scoreUs: 2, scoreThem: 2, createdBy: COACH.uid,
        }],
        [`teams/${TEAM_ID}/matches/match-00`, {
            opponentName: 'Southbank', date: '2026-07-31', status: 'full_time',
            finalized: true, scoreUs: 0, scoreThem: 3, createdBy: COACH.uid,
        }],

        report('match-0', 'p-alex', {
            playerName: 'Alex Vega', jerseyNumber: 9, minutesPlayed: 90,
            goals: 2, assists: 1, fouls: 3, matchDate: '2026-08-07',
            opponentName: 'Westbrook', scoreUs: 2, scoreThem: 2,
            cvTouches: 54, cvPasses: 31, cvDistanceM: 9200,
        }),
        report('match-00', 'p-alex', {
            playerName: 'Alex Vega', jerseyNumber: 9, minutesPlayed: 62,
            goals: 0, assists: 0, fouls: 1, matchDate: '2026-07-31',
            opponentName: 'Southbank', scoreUs: 0, scoreThem: 3,
        }),
        // One match, no goals, and minutes nobody could measure — the season
        // that must not come back reading like somebody else's.
        report('match-0', 'p-rae', {
            playerName: 'Rae Nkemelu', jerseyNumber: 7, linkedUid: STUDENT.uid,
            minutesPlayed: 0, minutesKnown: false, goals: 0, assists: 0,
            fouls: 0, matchDate: '2026-08-07', opponentName: 'Westbrook',
            scoreUs: 2, scoreThem: 2,
        }),

        log('dev-a_000001', { kind: 'period', type: 'kickoff_1st', side: null, seq: 1 }),
        log('dev-a_000002', { type: 'corner', matchClockS: 320, seq: 2 }),
        log('dev-a_000003', {
            type: 'goal', matchClockS: 940, playerId: 'p-alex',
            assistPlayerId: 'p-rae', seq: 3,
        }),
        log('dev-a_000004', {
            type: 'card', matchClockS: 1500, side: 'us', playerId: 'p-rae',
            cardColor: 'yellow', seq: 4,
        }),
        log('dev-a_000005', { type: 'foul', matchClockS: 1500, side: 'us', seq: 5 }),
        log('dev-a_000006', {
            kind: 'sub', type: 'sub', matchClockS: 1800, side: 'us',
            subOutId: 'p-alex', subInId: 'p-sam', seq: 6,
        }),
        log('dev-a_000007', { type: 'corner', matchClockS: 2100, side: 'them', seq: 7 }),
        log('dev-a_000008', {
            kind: 'period', type: 'halftime', side: null, matchClockS: 2700, seq: 8,
        }),
    ]);
}
