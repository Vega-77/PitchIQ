/**
 * Match video URL handling, and the player timeline's privacy boundary.
 *
 * Two things worth testing here and nothing else. First, `videoKind` decides
 * which element a coach-supplied string becomes, and that string ends up in a
 * `src` — so anything it accepts, it had better mean to accept. Second,
 * `playerTimeline` is the only place a teammate's name can reach a player's
 * report, because the portal never receives the roster; if it leaks something
 * it should not, no downstream check exists to catch it.
 *
 * Run:  node --test tests/video.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

// Both modules import nothing, so Node can load them straight from disk. That
// is the reason playerTimeline lives in report.js rather than in db.js, which
// opens a Firestore connection the moment it is imported.
import * as video from '../assets/video.js';
import * as report from '../assets/report.js';
import * as matchVideo from '../assets/match-video.js';

// ---------------------------------------------------------------- video URLs

describe('videoKind', () => {
    test('accepts the YouTube shapes people actually paste', () => {
        for (const url of [
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
            'https://youtu.be/dQw4w9WgXcQ',
            'https://youtube.com/embed/dQw4w9WgXcQ',
            'https://www.youtube.com/live/dQw4w9WgXcQ',
            'https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=90s',
        ]) {
            assert.equal(video.videoKind(url), 'youtube', url);
        }
    });

    test('accepts a direct video file', () => {
        assert.equal(video.videoKind('https://cdn.example.com/match.mp4'), 'file');
        assert.equal(video.videoKind('https://cdn.example.com/a/b.webm?x=1'), 'file');
    });

    test('refuses anything it cannot embed rather than guessing', () => {
        // Returning null sends the caller down the "show it as a link" path.
        // Guessing here means putting an arbitrary page in an iframe.
        for (const url of [
            'https://drive.google.com/file/d/abc/view',
            'https://example.com/some/page',
            'https://www.youtube.com/watch?v=short',
            'not a url at all',
            '',
        ]) {
            assert.equal(video.videoKind(url), null, url);
        }
    });

    test('refuses non-https schemes', () => {
        // http is blocked as mixed content on a page served over https, and
        // javascript:/data: are the reason this check is not merely cosmetic.
        assert.equal(video.videoKind('http://www.youtube.com/watch?v=dQw4w9WgXcQ'), null);
        assert.equal(video.videoKind('javascript:alert(1)//youtube.com'), null);
        assert.equal(video.videoKind('data:text/html,<script>x</script>'), null);
    });

    test('a lookalike hostname is not YouTube', () => {
        assert.equal(video.videoKind('https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ'), null);
        assert.equal(video.videoKind('https://notyoutube.com/watch?v=dQw4w9WgXcQ'), null);
    });
});

describe('youtubeId', () => {
    test('pulls the id out of every accepted shape', () => {
        assert.equal(video.youtubeId('https://youtu.be/dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
        assert.equal(
            video.youtubeId('https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=x'),
            'dQw4w9WgXcQ',
        );
        assert.equal(video.youtubeId('https://youtube.com/shorts/dQw4w9WgXcQ'), 'dQw4w9WgXcQ');
    });

    test('rejects an id of the wrong length', () => {
        assert.equal(video.youtubeId('https://youtu.be/tooshort'), null);
    });
});

describe('videoTime', () => {
    test('shifts a match-clock reading by the recording offset', () => {
        // Recording started two minutes before kick-off.
        assert.equal(video.videoTime(0, 120), 120);
        assert.equal(video.videoTime(600, 120), 720);
    });

    test('never goes negative', () => {
        // A mis-entered offset must not ask a player to seek to -30s.
        assert.equal(video.videoTime(10, -600), 0);
    });

    test('a missing offset is simply no shift', () => {
        assert.equal(video.videoTime(300), 300);
        assert.equal(video.videoTime(300, null), 300);
    });
});

// ---------------------------------------------------------------- timeline

const db = report;

const ME = 'p1';
const MATE = 'p2';

const roster = [
    { id: ME, playerName: 'Alex Vega' },
    { id: MATE, playerName: 'Jordan Cho' },
];

const ev = (over) => ({ kind: 'event', side: 'us', matchClockS: 0, ...over });

describe('playerTimeline', () => {
    test('includes what the player did', () => {
        const out = db.playerTimeline([
            ev({ type: 'goal', playerId: ME, matchClockS: 600 }),
            ev({ type: 'foul', playerId: ME, matchClockS: 900 }),
        ], roster, ME);

        assert.deepEqual(out.map((e) => e.label), ['You scored', 'You conceded a foul']);
        assert.ok(out.every((e) => e.mine));
    });

    test('names a teammate only when they scored', () => {
        // Goals are shouted across a pitch. That is the whole justification,
        // and it does not extend any further.
        const out = db.playerTimeline([
            ev({ type: 'goal', playerId: MATE, matchClockS: 300 }),
        ], roster, ME);

        assert.equal(out[0].label, 'Jordan Cho scored');
        assert.equal(out[0].mine, false);
    });

    test("never leaks a teammate's card", () => {
        // One minor's disciplinary record is not something to publish to
        // another minor. This is the single most important assertion here.
        const out = db.playerTimeline([
            ev({ type: 'card', playerId: MATE, cardColor: 'red', matchClockS: 500 }),
        ], roster, ME);

        assert.equal(out.length, 0);
        assert.ok(!JSON.stringify(out).includes('Jordan'));
    });

    test("never leaks a teammate's foul, offside or substitution", () => {
        const out = db.playerTimeline([
            ev({ type: 'foul', playerId: MATE }),
            ev({ type: 'offside', playerId: MATE }),
            { kind: 'sub', matchClockS: 700, subInId: MATE, subOutId: 'p3' },
        ], roster, ME);

        assert.equal(out.length, 0);
    });

    test('credits an assist and names who finished it', () => {
        const out = db.playerTimeline([
            ev({ type: 'goal', playerId: MATE, assistPlayerId: ME, matchClockS: 400 }),
        ], roster, ME);

        assert.equal(out[0].label, 'You assisted Jordan Cho');
        assert.ok(out[0].mine);
    });

    test('marks the player coming on and off', () => {
        const out = db.playerTimeline([
            { kind: 'sub', matchClockS: 1800, subInId: ME, subOutId: MATE },
            { kind: 'sub', matchClockS: 3600, subInId: MATE, subOutId: ME },
        ], roster, ME);

        assert.deepEqual(out.map((e) => e.label), ['Came on', 'Came off']);
    });

    test('keeps period markers so the strip has landmarks', () => {
        const out = db.playerTimeline([
            { kind: 'period', type: 'kickoff_1st', matchClockS: 0 },
            { kind: 'period', type: 'full_time', matchClockS: 5400 },
        ], roster, ME);

        assert.deepEqual(out.map((e) => e.label), ['Kick-off', 'Full time']);
        assert.ok(out.every((e) => !e.mine));
    });

    test('a conceded goal is not attributed to anyone', () => {
        // There is no opposition roster, so naming is impossible anyway — but
        // the label has to read sensibly rather than say "undefined scored".
        const out = db.playerTimeline([
            ev({ type: 'goal', side: 'them', matchClockS: 200 }),
        ], roster, ME);

        assert.equal(out[0].label, 'Goal conceded');
    });

    test('comes back in clock order', () => {
        const out = db.playerTimeline([
            ev({ type: 'foul', playerId: ME, matchClockS: 900 }),
            ev({ type: 'goal', playerId: ME, matchClockS: 100 }),
        ], roster, ME);

        assert.deepEqual(out.map((e) => e.clockS), [100, 900]);
    });

    test('is capped so a runaway log cannot fail the publish', () => {
        // Firestore documents stop at a megabyte, and the whole batch fails
        // together — one bad match should not block every player's report.
        const log = Array.from({ length: 500 }, (_, i) =>
            ev({ type: 'foul', playerId: ME, matchClockS: i }));

        assert.equal(db.playerTimeline(log, roster, ME).length, 120);
    });

    test('an empty log is not an error', () => {
        assert.deepEqual(db.playerTimeline([], roster, ME), []);
    });
});

// ------------------------------------------------------- cluster → player

const track = (id, over = {}) => ({
    cluster_id: id,
    touches: 10,
    passes_attempted: 8,
    passes_completed: 6,
    tackles: 1,
    distance_m: 1000,
    top_speed_kmh: 20,
    sprint_count: 2,
    minutes_tracked: 10,
    touch_times_s: [100, 200],
    ...over,
});

describe('cvStatsByPlayer', () => {
    test('a mapped figure becomes that player', () => {
        const out = report.cvStatsByPlayer([track(0)], { 0: ME });
        assert.equal(out[ME].touches, 10);
        assert.deepEqual(out[ME].clusters, [0]);
    });

    test('several figures for one player add up', () => {
        // The tracker loses people who leave frame, and identity.py only
        // rejoins fragments seconds apart — so one player really is several
        // figures, and mapping them all to one name has to sum.
        const out = report.cvStatsByPlayer(
            [track(0), track(1), track(2)], { 0: ME, 1: ME, 2: ME },
        );
        assert.equal(out[ME].touches, 30);
        assert.equal(out[ME].distance_m, 3000);
        assert.deepEqual(out[ME].clusters, [0, 1, 2]);
    });

    test('top speed takes the maximum, not the sum', () => {
        // A player who hit 31 km/h in one fragment did not hit 62 across two.
        const out = report.cvStatsByPlayer(
            [track(0, { top_speed_kmh: 24 }), track(1, { top_speed_kmh: 31 })],
            { 0: ME, 1: ME },
        );
        assert.equal(out[ME].top_speed_kmh, 31);
    });

    test('touch times merge in clock order across figures', () => {
        // Clusters arrive in mapping order, so the combined list is unsorted
        // until this fixes it — and it feeds a timeline strip.
        const out = report.cvStatsByPlayer(
            [track(0, { touch_times_s: [500, 900] }),
             track(1, { touch_times_s: [100, 300] })],
            { 0: ME, 1: ME },
        );
        assert.deepEqual(out[ME].touchTimes, [100, 300, 500, 900]);
    });

    test('an unmatched figure counts for nobody', () => {
        // The safe default. A figure nobody claimed is simply not counted,
        // rather than being attributed to a guess.
        const out = report.cvStatsByPlayer([track(0), track(1)], { 0: ME });
        assert.deepEqual(Object.keys(out), [ME]);
        assert.equal(out[ME].touches, 10);
    });

    test('a blank selection is not a mapping', () => {
        assert.deepEqual(report.cvStatsByPlayer([track(0)], { 0: '' }), {});
        assert.deepEqual(report.cvStatsByPlayer([track(0)], { 0: null }), {});
    });

    test('a mapping pointing at a figure that does not exist is ignored', () => {
        assert.deepEqual(report.cvStatsByPlayer([track(0)], { 9: ME }), {});
    });

    test('no mapping at all yields nothing', () => {
        assert.deepEqual(report.cvStatsByPlayer([track(0)], {}), {});
        assert.deepEqual(report.cvStatsByPlayer([track(0)], null), {});
        assert.deepEqual(report.cvStatsByPlayer(null, { 0: ME }), {});
    });

    test('pass accuracy is computed, and is null when nothing was attempted', () => {
        const out = report.cvStatsByPlayer(
            [track(0, { passes_attempted: 10, passes_completed: 7 })], { 0: ME },
        );
        assert.equal(out[ME].passAccuracy, 0.7);

        const none = report.cvStatsByPlayer(
            [track(0, { passes_attempted: 0, passes_completed: 0 })], { 0: ME },
        );
        assert.equal(none[ME].passAccuracy, null);
    });

    test('a null stat on one figure does not poison the sum', () => {
        // Uncalibrated runs emit null for anything in metres.
        const out = report.cvStatsByPlayer(
            [track(0, { distance_m: null }), track(1, { distance_m: 500 })],
            { 0: ME, 1: ME },
        );
        assert.equal(out[ME].distance_m, 500);
    });

    test('touch times are capped', () => {
        const many = Array.from({ length: 900 }, (_, i) => i);
        const out = report.cvStatsByPlayer(
            [track(0, { touch_times_s: many })], { 0: ME },
        );
        assert.equal(out[ME].touchTimes.length, report.MAX_TOUCH_TIMES);
    });

    test('a figure ruled out as not a player counts for nobody', () => {
        // The sentinel shares the map with real player ids, so it would
        // otherwise become a player whose id is a magic string.
        const out = report.cvStatsByPlayer(
            [track(0), track(1)], { 0: ME, 1: report.NOT_A_PLAYER },
        );
        assert.deepEqual(Object.keys(out), [ME]);
        assert.equal(out[ME].touches, 10);
    });
});

// ------------------------------------------------ who was on the pitch, when

const STINTS = {
    // On from kick-off, off at 30:00.
    starter: [{ inS: 0, outS: 1800 }],
    // On at 30:00 and never came off.
    sub: [{ inS: 1800, outS: null }],
    // On, off, and back on — the case a single in/out pair gets wrong.
    reentry: [{ inS: 0, outS: 600 }, { inS: 1200, outS: null }],
    never: [],
};

const squad = () => [
    { id: 'starter', playerName: 'Ada', jerseyNumber: 4, stints: STINTS.starter },
    { id: 'sub', playerName: 'Bo', jerseyNumber: 9, stints: STINTS.sub },
    { id: 'reentry', playerName: 'Cy', jerseyNumber: 2, stints: STINTS.reentry },
    { id: 'never', playerName: 'Dee', jerseyNumber: 17, stints: STINTS.never },
];

describe('onPitchAt', () => {
    test('finds who was on at a given moment', () => {
        assert.deepEqual([...report.onPitchAt(squad(), 300)].sort(),
                         ['reentry', 'starter']);
        assert.deepEqual([...report.onPitchAt(squad(), 1900)].sort(),
                         ['reentry', 'sub']);
    });

    test('a player between two stints is off', () => {
        assert.ok(!report.onPitchAt(squad(), 900).has('reentry'));
        assert.ok(report.onPitchAt(squad(), 1300).has('reentry'));
    });

    test('an open stint runs to the end', () => {
        assert.ok(report.onPitchAt(squad(), 99999).has('sub'));
    });

    test('the moment of coming off is already off', () => {
        // Half-open: [inS, outS). Otherwise both players in a substitution are
        // on the pitch on the same second.
        assert.ok(!report.onPitchAt(squad(), 1800).has('starter'));
        assert.ok(report.onPitchAt(squad(), 1800).has('sub'));
    });

    test('somebody who never played is never on', () => {
        for (const t of [0, 600, 1800, 5400]) {
            assert.ok(!report.onPitchAt(squad(), t).has('never'));
        }
    });

    test('an empty roster is not an error', () => {
        assert.equal(report.onPitchAt([], 100).size, 0);
        assert.equal(report.onPitchAt(null, 100).size, 0);
    });
});

describe('stintOverlapS', () => {
    const END = 5400;

    test('a window fully inside a stint is the whole window', () => {
        assert.equal(report.stintOverlapS(STINTS.starter, 100, 200, END), 100);
    });

    test('a window straddling the substitution counts only the played part', () => {
        assert.equal(report.stintOverlapS(STINTS.starter, 1700, 1900, END), 100);
    });

    test('both halves of a re-entry are counted', () => {
        assert.equal(report.stintOverlapS(STINTS.reentry, 0, 1800, END), 1200);
    });

    test('an open stint is closed by the end of the match, not by infinity', () => {
        assert.equal(report.stintOverlapS(STINTS.sub, 0, 99999, END), END - 1800);
    });

    test('no overlap is zero, never negative', () => {
        assert.equal(report.stintOverlapS(STINTS.sub, 0, 100, END), 0);
        assert.equal(report.stintOverlapS(STINTS.never, 0, END, END), 0);
        assert.equal(report.stintOverlapS(null, 0, END, END), 0);
    });

    test('an empty window is zero', () => {
        assert.equal(report.stintOverlapS(STINTS.starter, 300, 300, END), 0);
    });
});

describe('rankRosterForCluster', () => {
    // The cluster was on screen from 5:00 to 7:00 of a video that started two
    // minutes before kick-off — so 3:00 to 5:00 on the match clock.
    const cluster = { cluster_id: 0, first_seen_s: 420, last_seen_s: 540 };
    const options = { videoOffsetS: 120, matchEndS: 5400 };

    test('whoever was on the pitch then comes first', () => {
        const ranked = report.rankRosterForCluster(squad(), cluster, options);
        assert.equal(ranked[0].overlapS, 120);
        assert.ok(['starter', 'reentry'].includes(ranked[0].entry.id));
    });

    test('nobody is filtered out, however badly they fit', () => {
        // The offset is the fiddliest number in the app. If it is wrong, every
        // overlap here is wrong, and a picker that had hidden the players who
        // do not fit would have hidden the right answer.
        const ranked = report.rankRosterForCluster(squad(), cluster, options);
        assert.equal(ranked.length, 4);
        assert.ok(ranked.some((r) => r.entry.id === 'never'));
    });

    test('players who were off have zero overlap and sort last', () => {
        const ranked = report.rankRosterForCluster(squad(), cluster, options);
        assert.equal(ranked.at(-1).overlapS, 0);
        assert.equal(ranked.at(-2).overlapS, 0);
    });

    test('the share is of the time the figure was on screen', () => {
        const ranked = report.rankRosterForCluster(squad(), cluster, options);
        assert.equal(ranked[0].overlapShare, 1);
    });

    test('the video offset is applied', () => {
        // With no offset the same figure lands at 7:00-9:00 of the match, which
        // the substitute at 30:00 still misses — but the numbers must move.
        const shifted = report.rankRosterForCluster(
            squad(), cluster, { videoOffsetS: 0, matchEndS: 5400 },
        );
        const byId = Object.fromEntries(
            shifted.map((r) => [r.entry.id, r.overlapS]),
        );
        assert.equal(byId.starter, 120);
        assert.equal(byId.sub, 0);
    });

    test('a wrong offset that pushes a figure past everyone is still ranked', () => {
        const ranked = report.rankRosterForCluster(
            squad(), cluster, { videoOffsetS: -99999, matchEndS: 5400 },
        );
        assert.equal(ranked.length, 4);
        assert.ok(ranked.every((r) => r.overlapS === 0));
    });

    test('ties break on jersey number so the order is stable', () => {
        const ranked = report.rankRosterForCluster(squad(), cluster, options);
        const zeroes = ranked.filter((r) => r.overlapS === 0);
        assert.deepEqual(zeroes.map((r) => r.entry.jerseyNumber), [9, 17]);
    });

    test('an empty roster is not an error', () => {
        assert.deepEqual(report.rankRosterForCluster([], cluster, options), []);
        assert.deepEqual(report.rankRosterForCluster(null, cluster, options), []);
    });
});

describe('cvReportFields', () => {
    test('every field is cv-prefixed', () => {
        const stats = report.cvStatsByPlayer([track(0)], { 0: ME })[ME];
        const fields = report.cvReportFields(stats);
        assert.ok(Object.keys(fields).length > 5);
        assert.ok(Object.keys(fields).every((k) => k.startsWith('cv')));
    });

    test('reports how many figures a player was assembled from', () => {
        // A player stitched from nine fragments is a weaker claim than one
        // tracked cleanly, and the coach should be able to see that.
        const stats = report.cvStatsByPlayer(
            [track(0), track(1)], { 0: ME, 1: ME },
        )[ME];
        assert.equal(report.cvReportFields(stats).cvClusterCount, 2);
    });

    test('an unmapped player gets no cv fields at all', () => {
        // Not zeroes. A zero says the video measured them and found nothing.
        assert.deepEqual(report.cvReportFields(undefined), {});
    });
});

// ------------------------------------------------- saying what a run rests on
//
// These four are the wording under every video-derived number in the app, and
// the thing they mostly have to get right is the difference between a figure
// that is zero and a figure that was never measured. `live_share` absent means
// nobody told the pipeline when the ball was out of play; `live_share: 0` would
// mean it was told, and the answer was none. The sentences differ, and so does
// the possession figure printed beside them.

describe('possessionIsInPlay', () => {
    test('a live share means dead time was taken out of the denominator', () => {
        assert.equal(report.possessionIsInPlay({ live_share: 0.68 }), true);
    });

    test('no live share means stoppages are still being counted as play', () => {
        assert.equal(report.possessionIsInPlay({}), false);
        assert.equal(report.possessionIsInPlay({ live_share: null }), false);
        assert.equal(report.possessionIsInPlay(null), false);
        assert.equal(report.possessionIsInPlay(undefined), false);
    });

    test('a live share of zero is an answer, not a missing one', () => {
        // A tagged log saying the ball was never in play is a claim about a
        // strange match. It is not the same as no log, and treating the two
        // alike would relabel a possession figure on the strength of nothing.
        assert.equal(report.possessionIsInPlay({ live_share: 0 }), true);
    });

    test('reads the camelCase Firestore spelling too', () => {
        assert.equal(report.possessionIsInPlay({ liveShare: 0.7 }), true);
    });
});

describe('roughDuration', () => {
    test('under a minute stays in seconds', () => {
        assert.equal(report.roughDuration(41), '41s');
        assert.equal(report.roughDuration(0), '0s');
    });

    test('a round minute drops the seconds', () => {
        assert.equal(report.roughDuration(120), '2m');
    });

    test('anything else reads as minutes and seconds', () => {
        assert.equal(report.roughDuration(260), '4m 20s');
    });

    test('nothing is zero seconds rather than NaN', () => {
        assert.equal(report.roughDuration(null), '0s');
        assert.equal(report.roughDuration(undefined), '0s');
    });
});

describe('shapeConfidence', () => {
    test('the bands are the calibrate page own', () => {
        // renderQuality in calibrate.js calls a fit good at 0.5m mean error.
        // Two standards for one number would let a coach be told the fit is
        // good on one page and be quietly doubted on another.
        assert.equal(report.shapeConfidence(0.4), 'high');
        assert.equal(report.shapeConfidence(0.5), 'high');
        assert.equal(report.shapeConfidence(1.2), 'medium');
        assert.equal(report.shapeConfidence(4), 'low');
    });

    test('no calibration error is the lowest band, not the highest', () => {
        // The failure mode worth guarding: absent reading as perfect.
        assert.equal(report.shapeConfidence(null), 'low');
        assert.equal(report.shapeConfidence(undefined), 'low');
    });
});

describe('cvQualityNotes', () => {
    const full = {
        ball_seen_share: 0.83,
        no_ball_s: 260,
        live_share: 0.68,
        stoppages: 27,
        flagged_officials: 2,
        tracks_per_cluster: 4.2,
    };
    const joined = (q, o) => report.cvQualityNotes(q, o).join(' | ');

    test('coverage carries the seconds with it, not as a second complaint', () => {
        // The percentage and the seconds are the same fact in two units, and
        // splitting them would read as two separate problems with the run.
        const notes = report.cvQualityNotes({ ball_seen_share: 0.83, no_ball_s: 260 });
        const coverage = notes.filter((n) => n.includes('visible'));
        assert.equal(coverage.length, 1);
        assert.match(coverage[0], /83% of frames/);
        assert.match(coverage[0], /4m 20s/);
    });

    test('seconds with no ball still get said when coverage is missing', () => {
        assert.match(joined({ no_ball_s: 90 }), /1m 30s of the clip with no ball/);
    });

    test('no tagged log is stated, not left as an absence', () => {
        // The whole point: a coach reading a possession split needs to know it
        // includes every second spent waiting for a throw-in.
        assert.match(joined({}), /no tagged log/);
        assert.doesNotMatch(joined({}), /in play/);
    });

    test('a tagged log reports the live share and the stoppage count', () => {
        assert.match(joined(full), /68% of it in play across 27 stoppages/);
        assert.doesNotMatch(joined(full), /no tagged log/);
    });

    test('one stoppage is not one stoppages', () => {
        assert.match(joined({ live_share: 0.9, stoppages: 1 }), /1 stoppage\b/);
    });

    test('a live share with no stoppage count still reads as a sentence', () => {
        assert.match(joined({ live_share: 0.9 }), /90% of it in play$/);
    });

    test('carried officials are named as a caveat', () => {
        // They are inside the counts above, not removed from them, and a
        // referee in a possession figure is exactly the sort of thing that
        // reads as a real number unless somebody says otherwise.
        assert.match(joined(full), /2 figures matching neither kit still counted/);
        assert.match(joined({ flagged_officials: 1 }), /1 figure matching/);
    });

    test('a clean run says nothing about officials', () => {
        assert.doesNotMatch(joined({ flagged_officials: 0, live_share: 1 }), /neither kit/);
    });

    test('calibration is only mentioned when there is none', () => {
        assert.match(joined({}, { calibrated: false }), /no pitch calibration/);
        assert.doesNotMatch(joined({}, { calibrated: true }), /calibration/);
    });

    test('fragmentation is only mentioned when it is bad', () => {
        assert.match(joined({ tracks_per_cluster: 4.2 }), /about 4 pieces/);
        assert.doesNotMatch(joined({ tracks_per_cluster: 1.4 }), /pieces/);
    });

    test('every note is a caveat — nothing here reports good news', () => {
        // Padding a warning line with things that went well is how the warnings
        // stop being read.
        const clean = report.cvQualityNotes(
            { ball_seen_share: 0.99, live_share: 0.7, tracks_per_cluster: 1.1 },
            { calibrated: true },
        );
        assert.equal(clean.length, 2, clean.join(' | '));
    });

    test('an empty quality block does not throw', () => {
        assert.ok(Array.isArray(report.cvQualityNotes(null)));
        assert.ok(Array.isArray(report.cvQualityNotes(undefined, {})));
    });
});

// ------------------------------------------------------- the video on a page
//
// `videoKind` answers "which element do I build", and returns null both for
// "there is no link" and for "there is a link and we refuse to embed it".
// Those are the same answer to the renderer and two very different things to
// say to a person, which is the whole reason `videoPlacement` exists.

describe('videoPlacement', () => {
    test('a YouTube link is something we embed', () => {
        assert.equal(matchVideo.videoPlacement('https://youtu.be/dQw4w9WgXcQ'), 'embed');
    });

    test('a direct file is too', () => {
        assert.equal(matchVideo.videoPlacement('https://s.example.com/m.mp4'), 'embed');
    });

    test('a link we will not embed is still a link, not nothing', () => {
        // A Drive share is real footage. Losing it because we cannot frame it
        // would be worse than declining to frame it.
        assert.equal(
            matchVideo.videoPlacement('https://drive.google.com/file/d/abc/view'),
            'link',
        );
    });

    test('http is refused rather than embedded', () => {
        // Mixed content would break it anyway, but this is the string that ends
        // up in a src attribute.
        assert.equal(matchVideo.videoPlacement('http://youtube.com/watch?v=abc'), 'link');
    });

    test('no link is none', () => {
        assert.equal(matchVideo.videoPlacement(''), 'none');
        assert.equal(matchVideo.videoPlacement(null), 'none');
        assert.equal(matchVideo.videoPlacement(undefined), 'none');
    });
});

describe('teamMarks', () => {
    const describe_ = (e) => `${e.type || e.kind} at ${e.matchClockS}`;
    const log = [
        { kind: 'period', type: 'kickoff_1st', matchClockS: 0 },
        { kind: 'event', type: 'goal', matchClockS: 610, side: 'us' },
        { kind: 'event', type: 'corner', matchClockS: 700, side: 'us' },
        { kind: 'event', type: 'foul', matchClockS: 800, side: 'them' },
        { kind: 'event', type: 'card', matchClockS: 812, side: 'them' },
        { kind: 'sub', matchClockS: 1500, label: 'Ortiz on for Marchetti' },
        { kind: 'period', type: 'halftime', matchClockS: 2700 },
    ];

    test('goals, cards and subs are marked', () => {
        assert.deepEqual(
            matchVideo.teamMarks(log, describe_).map((m) => m.clockS),
            [610, 812, 1500],
        );
    });

    test('restarts and fouls are not', () => {
        // A tagged half carries thirty restarts and twenty fouls. Marking them
        // turns the strip into a texture instead of a set of things to jump to.
        const types = matchVideo.teamMarks(log, describe_).map((m) => m.type);
        assert.ok(!types.includes('corner'));
        assert.ok(!types.includes('foul'));
    });

    test('period boundaries are landmarks, not moments', () => {
        // The strip already draws half-time, and nobody seeks to kick-off.
        const clocks = matchVideo.teamMarks(log, describe_).map((m) => m.clockS);
        assert.ok(!clocks.includes(0));
        assert.ok(!clocks.includes(2700));
    });

    test('a substitution is typed sub, whatever its event type says', () => {
        const sub = matchVideo.teamMarks(log, describe_).find((m) => m.clockS === 1500);
        assert.equal(sub.type, 'sub');
        assert.equal(sub.label, 'sub at 1500');
    });

    test('the label comes from the caller, not from here', () => {
        // The coach page and the half-time page name the two teams differently,
        // and that naming is theirs.
        const marks = matchVideo.teamMarks(log, () => 'whatever they said');
        assert.ok(marks.every((m) => m.label === 'whatever they said'));
    });

    test('an entry with no clock is skipped, not placed at zero', () => {
        // A mark at 0:00 on a strip is a claim that something happened at
        // kick-off, which is worse than the mark being absent.
        const marks = matchVideo.teamMarks(
            [{ kind: 'event', type: 'goal' },
             { kind: 'event', type: 'goal', matchClockS: null },
             { kind: 'event', type: 'goal', matchClockS: 60 }],
            describe_,
        );
        assert.deepEqual(marks.map((m) => m.clockS), [60]);
    });

    test('an empty log is an empty list', () => {
        assert.deepEqual(matchVideo.teamMarks([], describe_), []);
        assert.deepEqual(matchVideo.teamMarks(null, describe_), []);
    });
});
