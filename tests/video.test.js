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
import * as heatmap from '../assets/heatmap.js';
import * as markMod from '../assets/shot-map.js';
import * as sample from '../assets/sample-report.js';
// The sandbox's model half and its preset table. Neither touches the DOM or
// onnxruntime at import time — the session is only built on the first predict.
import * as xgModel from '../xg-sandbox/xg-model.js';
import * as presets from '../xg-sandbox/presets.js';

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

    // The xG caveat only exists because xG is now a real number rather than a
    // permanent null. It says the one thing about it that is a bias rather than
    // noise: a header is scored as a foot shot, every time, upward.
    test('the header bias is named whenever there are shots to caveat', () => {
        assert.match(joined({}, { shots: 4 }), /struck with the foot/);
    });

    test('no shots, no xG caveat', () => {
        // Nothing on screen to qualify. A caveat about a figure the coach
        // cannot see is noise in the line that carries the real warnings.
        assert.doesNotMatch(joined({}, { shots: 0 }), /foot/);
        assert.doesNotMatch(joined({}, {}), /foot/);
    });

    test('a loose calibration adds the per-shot warning, a tight one does not', () => {
        // Measured, not guessed: at 0.5m the mean xG shift is 0.035 on a 0.254
        // baseline, and it keeps widening. See tests/test_xg_noise.py.
        const loose = joined({}, { shots: 3, calibrationErrorM: 2.4 });
        assert.match(loose, /2\.4m of calibration error/);
        assert.match(loose, /only the total is shown/);

        assert.doesNotMatch(
            joined({}, { shots: 3, calibrationErrorM: 0.4 }),
            /calibration error/,
        );
    });

    test('past the point of usefulness the note says xG is gone, not caveated', () => {
        // The header bias is a caveat on a number being shown. Once there is no
        // number, repeating it would imply one is up there somewhere.
        const gone = joined({}, { shots: 3, calibrationErrorM: 6 });
        assert.match(gone, /xG is not shown/);
        assert.doesNotMatch(gone, /struck with the foot/);
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

// ------------------------------------------------- scoring the review tool
//
// The review tool has been collecting verdicts since it shipped and computing
// nothing but "84 of 512 checked" from them. These are the two numbers it
// exists for, and the reason they need their own tests is that the obvious
// implementation of either one flatters the detector.

describe('reviewScore', () => {
    const events = (...types) =>
        types.map((type, i) => ({ id: `e${i}`, type }));

    const score = (evts, byEvent = {}, missed = []) =>
        report.reviewScore(evts, { byEvent, missed });

    test('a confirmed event is a success for its type', () => {
        const s = score(events('pass'), { e0: { status: 'confirmed' } });
        assert.equal(s.byType.pass.precision, 1);
    });

    test('a rejected event counts against the type it claimed', () => {
        const s = score(events('pass', 'pass'), {
            e0: { status: 'confirmed' }, e1: { status: 'rejected' },
        });
        assert.equal(s.byType.pass.precision, 0.5);
    });

    test('unreviewed events are excluded from both numbers, not counted wrong', () => {
        // The alternative is a scorecard that starts at 0% and climbs as
        // somebody works through it, which reads as a broken detector.
        const s = score(events('pass', 'pass'), { e0: { status: 'confirmed' } });
        assert.equal(s.byType.pass.precision, 1);
        assert.equal(s.byType.pass.unreviewed, 1);
    });

    test('an edit that only fixes the player leaves the type standing', () => {
        // The pipeline found the right kind of thing and pinned it on the wrong
        // person. Identity is a separate problem with a separate fix, and
        // charging it to the detector hides both.
        const s = score(events('pass'), {
            e0: { status: 'edited', playerId: 'p7' },
        });
        assert.equal(s.byType.pass.precision, 1);
    });

    test('an edit that changes the type charges one and credits the other', () => {
        // Two statements at once: wrong to call it a tackle, right that
        // something happened. Collapsing either way produces a kind number.
        const s = score(events('tackle'), {
            e0: { status: 'edited', type: 'interception' },
        });
        assert.equal(s.byType.tackle.precision, 0);
        assert.equal(s.byType.interception.detected, 1);
        assert.equal(s.byType.interception.recall, 1);
    });

    test('recall comes from the misses and nothing else can supply it', () => {
        // A thing the pipeline never saw leaves no record to disagree with, so
        // no amount of judging what it did find can produce this number.
        const found = score(events('pass'), { e0: { status: 'confirmed' } });
        assert.equal(found.byType.pass.recall, 1);

        const withMisses = score(
            events('pass'), { e0: { status: 'confirmed' } },
            [{ clockS: 10, type: 'pass' }, { clockS: 20, type: 'pass' }],
        );
        assert.equal(withMisses.byType.pass.recall, 1 / 3);
    });

    test('a miss of a type the pipeline never claimed still counts', () => {
        const s = score(events('pass'), {}, [{ clockS: 5, type: 'shot' }]);
        assert.equal(s.byType.shot.recall, 0);
        assert.equal(s.byType.shot.missed, 1);
    });

    test('nothing reviewed gives null, not zero', () => {
        // Zero is a measurement. This is the absence of one, and a scorecard
        // reading 0% for a type nobody looked at is read as a broken detector.
        const s = score(events('pass'));
        assert.equal(s.byType.pass.precision, null);
        assert.equal(s.byType.pass.recall, null);
        assert.equal(s.overall.precision, null);
    });

    test('the overall figures are the sum of the parts, not an average of rates', () => {
        // Averaging per-type rates would let one confirmed clearance weigh as
        // much as four hundred passes.
        const s = score(events('pass', 'pass', 'pass', 'shot'), {
            e0: { status: 'confirmed' },
            e1: { status: 'confirmed' },
            e2: { status: 'confirmed' },
            e3: { status: 'rejected' },
        });
        assert.equal(s.overall.precision, 0.75);
    });

    test('an empty review and an empty event list do not throw', () => {
        assert.equal(report.reviewScore([], {}).overall.precision, null);
        assert.equal(report.reviewScore(null, null).overall.recall, null);
    });
});

describe('reviewLabels', () => {
    const events = [
        { id: 'a', type: 'pass', timestampS: 60, trackId: 4, confidence: 0.8 },
        { id: 'b', type: 'tackle', timestampS: 90, trackId: 9, confidence: 0.4 },
        { id: 'c', type: 'shot', timestampS: 120 },
    ];

    const labels = (byEvent, missed = [], meta = {}) =>
        report.reviewLabels(events, { byEvent, missed }, meta);

    test('only events with a verdict are labelled', () => {
        // Treating the rest as negatives would train a detector on the
        // pipeline's own unchecked guesses.
        const out = labels({ a: { status: 'confirmed' } });
        assert.deepEqual(out.labelled.map((l) => l.id), ['a']);
    });

    test('an edited event records what it should have been', () => {
        const out = labels({ b: { status: 'edited', type: 'interception' } });
        assert.equal(out.labelled[0].claimedType, 'tackle');
        assert.equal(out.labelled[0].actualType, 'interception');
    });

    test('a rejected event has no actual type — nothing happened there', () => {
        const out = labels({ a: { status: 'rejected' } });
        assert.equal(out.labelled[0].actualType, null);
    });

    test('the misses travel too, since they are the half nothing else has', () => {
        const out = labels({}, [{ clockS: 300, type: 'shot' }]);
        assert.deepEqual(out.missed, [{ clockS: 300, type: 'shot', playerId: null }]);
    });

    test('the two halves are named for the clocks they are actually on', () => {
        // An event is stamped in video seconds by the pipeline; a miss is typed
        // by a human off the match clock. Calling both `clockS` would put two
        // different clocks under one name in one file, which is the exact
        // confusion `videoOffsetS` exists to keep visible.
        const out = labels({ a: { status: 'confirmed' } }, [{ clockS: 300, type: 'shot' }]);
        assert.equal(out.labelled[0].videoS, 60);
        assert.equal(out.labelled[0].clockS, undefined);
        assert.equal(out.missed[0].clockS, 300);
        assert.match(out.note, /videoS = clockS \+ videoOffsetS/);
    });

    test('the file says what match it belongs to', () => {
        // Worthless in a month otherwise, which is exactly when it gets opened.
        const out = labels({}, [], { matchId: 'm1', teamId: 't1' });
        assert.equal(out.matchId, 'm1');
        assert.equal(out.format, 'pitchiq-review-labels');
    });

    test('it survives a JSON round trip, which is the only thing it is for', () => {
        const out = labels({ a: { status: 'confirmed' } }, [], { matchId: 'm1' });
        assert.deepEqual(JSON.parse(JSON.stringify(out)), out);
    });
});

// --------------------------------------------- reading the half out loud
//
// The catalog asks for plain-language flags rather than tables at half-time.
// The risk with a flag is not that it is wrong, it is that it fires every
// match — a flag that always appears stops being read and takes the ones that
// matter with it. So most of these test silence.

describe('cvReads', () => {
    const cv = (team) => ({ teams: { team_a: team } });

    test('a side pinned in its own third is told so', () => {
        const reads = report.cvReads(cv({
            territory: { defensive: 0.52, middle: 0.31, attacking: 0.17 },
        }));
        assert.equal(reads.length, 1);
        assert.match(reads[0].detail, /52% of your possession was in your own third/);
        assert.match(reads[0].detail, /17% in theirs/);
    });

    test('an ordinary spread across the thirds says nothing', () => {
        // An even split is 33% each, so this must stay quiet well past that.
        assert.deepEqual(report.cvReads(cv({
            territory: { defensive: 0.36, middle: 0.34, attacking: 0.30 },
        })), []);
    });

    test('a shape that moved is described in both directions at once', () => {
        const reads = report.cvReads(cv({
            shape_drift: { change: { width_m: 6.2, depth_m: 0.4, compactness_m: -4.1 } },
        }));
        assert.equal(reads.length, 1);
        assert.match(reads[0].detail, /6m wider/);
        assert.match(reads[0].detail, /4m more compact/);
        // depth barely moved, so it is not mentioned at all
        assert.doesNotMatch(reads[0].detail, /front to back/);
    });

    test('a shape that held is not remarked on', () => {
        assert.deepEqual(report.cvReads(cv({
            shape_drift: { change: { width_m: 1.1, depth_m: -0.8, compactness_m: 0.2 } },
        })), []);
    });

    test('giveaways in your own third are counted, not just totalled', () => {
        const reads = report.cvReads(cv({
            turnovers_by_third: { defensive: 9, middle: 3, attacking: 1 },
        }));
        assert.match(reads[0].title, /9 giveaways in your own third/);
    });

    test('a couple of giveaways is a bad minute, not a pattern', () => {
        assert.deepEqual(report.cvReads(cv({
            turnovers_by_third: { defensive: 2, middle: 8, attacking: 4 },
        })), []);
    });

    test('chances made against chances taken, in both directions', () => {
        const wasteful = report.cvReads(cv({ xg: 2.4, goals: 1, shots: 11 }));
        assert.match(wasteful[0].title, /made more than you have taken/);

        const flattered = report.cvReads(cv({ xg: 0.6, goals: 2, shots: 4 }));
        assert.match(flattered[0].title, /scoreline is ahead of the chances/);
    });

    test('a normal conversion rate is not a story', () => {
        assert.deepEqual(report.cvReads(cv({ xg: 1.4, goals: 1, shots: 8 })), []);
    });

    test('nothing measured means nothing said', () => {
        // Every field here is null on an uncalibrated run, which is every run
        // so far. The page must render no heading rather than an empty one.
        assert.deepEqual(report.cvReads(null), []);
        assert.deepEqual(report.cvReads({}), []);
        assert.deepEqual(report.cvReads(cv({})), []);
        assert.deepEqual(report.cvReads(cv({
            territory: null, shape_drift: null, turnovers_by_third: null,
            xg: null, goals: null, shots: null,
        })), []);
    });

    test('a half with several problems reports all of them', () => {
        const reads = report.cvReads(cv({
            territory: { defensive: 0.52, middle: 0.31, attacking: 0.17 },
            shape_drift: { change: { width_m: 6.2, depth_m: 0, compactness_m: 0 } },
            turnovers_by_third: { defensive: 9, middle: 3, attacking: 1 },
            xg: 2.4, goals: 1, shots: 11,
        }));
        assert.equal(reads.length, 4);
        assert.ok(reads.every((r) => r.title && r.detail));
    });
});

// ----------------------------------------------------------- the sandbox
//
// The gap these close is a specific one. tests/test_xg_parity.py builds a
// scenario in StatsBomb space and converts it into each side's convention, so
// it proves Python and JavaScript agree about a point — and cannot notice that
// the point is not where the sandbox says it is. It did not notice for months:
// `toStatsBomb` mapped the sandbox's half pitch onto a full StatsBomb one, so
// every shot reached the model at twice its distance, and the parity test's own
// inverse carried the same mistake and agreed with it perfectly.
//
// So these start from metres — the units on the sliders, which is the only
// place a person can tell whether the answer is right.

describe('the sandbox speaks the same units as the pipeline', () => {
    // The sandbox's 0-1 space: x across 68m, y out from the goal line over the
    // 52.5m half it draws.
    const at = (acrossM, fromGoalM) => ({ x: acrossM / 68, y: fromGoalM / 52.5 });

    // StatsBomb is 120 long and 80 wide over a 105x68 pitch, so a unit is 0.875m
    // along it and 0.85m across it. **The space is not isotropic in metres**,
    // and neither is cv/pitch.py's `to_statsbomb`, which does the same thing:
    // an angle in model space is about 3% wider than the one a protractor would
    // measure on the grass. That is inherited from how the model was trained
    // and is not something to correct here — but it does mean a diagonal cannot
    // be converted with one factor, which is what the first draft of these
    // tests tried and why two of them failed against correct code.
    const UNITS_PER_M = 120 / 105;
    const ACROSS_PER_M = 80 / 68;

    const featuresFor = (shooter, keeper = at(34, 2), defenders = []) =>
        xgModel.buildFeatures({
            shooter,
            keeper,
            defenders,
            shot: {
                isFoot: true, isHeader: false, underPressure: false,
                isOpenPlay: true, height: 0.6,
            },
        });

    test('a shot the sliders call 20m is 20m to the model', () => {
        // The regression itself. This read 45.71 units — 40m — while the
        // Distance slider above it said 20.
        const { distance_to_goal: distance } = featuresFor(at(34, 20));
        assert.ok(
            Math.abs(distance / UNITS_PER_M - 20) < 0.05,
            `${distance} units is ${(distance / UNITS_PER_M).toFixed(1)}m`,
        );
    });

    test('distance is measured to the goal from anywhere on the half', () => {
        for (const [across, out] of [[34, 5], [34, 30], [34, 52.5], [45, 11], [20, 25]]) {
            // Each axis scaled by its own factor, because they differ.
            const expected = Math.hypot(
                out * UNITS_PER_M, (across - 34) * ACROSS_PER_M,
            );
            const units = featuresFor(at(across, out)).distance_to_goal;
            assert.ok(Math.abs(units - expected) < 0.05,
                `(${across}, ${out}) -> ${units.toFixed(2)}, wanted ${expected.toFixed(2)}`);
        }
    });

    test('the goalmouth subtends the angle trigonometry says it does', () => {
        // The goal is 8 units of an 80-unit width, which on a 68m pitch is
        // 6.8m and not the regulation 7.32 — another thing baked into the
        // training data. So the check is done in units, where it is exact.
        //
        // The old mapping halved this to 10 degrees, and how much of the goal a
        // shooter can see is most of why a shot scores what it does.
        const degrees = featuresFor(at(34, 20)).angle_to_goal * (180 / Math.PI);
        const expected =
            2 * Math.atan(4 / (20 * UNITS_PER_M)) * (180 / Math.PI);
        assert.ok(Math.abs(degrees - expected) < 0.05,
            `${degrees.toFixed(2)} deg, trigonometry says ${expected.toFixed(2)}`);
    });

    test('the halfway line is halfway, not the far goal', () => {
        // y = 1 is the top of what the sandbox draws. Under the old mapping it
        // came out at the opposite goal line, 0 units from the wrong goal.
        const { distance_to_goal: far } = featuresFor(at(34, 52.5));
        assert.ok(Math.abs(far - 60) < 0.5, `${far} units`);
    });

    test('a keeper on his line is not counted as having come out', () => {
        // keeper_off_line is a hard threshold at 3 units, so doubling the scale
        // flipped it for any keeper more than 1.3m off his line.
        assert.equal(featuresFor(at(34, 12), at(34, 1)).keeper_off_line, 0);
        assert.equal(featuresFor(at(34, 12), at(34, 6)).keeper_off_line, 1);
    });
});

describe('the sandbox presets', () => {
    test('every preset places all ten players inside the half', () => {
        for (const preset of presets.PRESETS) {
            const spots = [
                preset.shooter, preset.keeper,
                ...preset.defenders, ...preset.attackers,
            ];
            assert.equal(spots.length, 10, preset.id);
            for (const spot of spots) {
                const position = presets.fromMetres(spot);
                assert.ok(position.x >= 0 && position.x <= 1, `${preset.id} ${spot}`);
                assert.ok(position.y >= 0 && position.y <= 1, `${preset.id} ${spot}`);
            }
        }
    });

    test('the arrays are the length the sandbox has slots for', () => {
        // applyPreset writes into players.defence[1..4] and players.attack[1..4].
        // A fifth entry would be dropped in silence and the scenario would be
        // subtly not the one described.
        for (const preset of presets.PRESETS) {
            assert.equal(preset.defenders.length, 4, preset.id);
            assert.equal(preset.attackers.length, 4, preset.id);
        }
    });

    test('every preset says what it is, under a name of its own', () => {
        const ids = presets.PRESETS.map((p) => p.id);
        assert.equal(new Set(ids).size, ids.length);
        for (const preset of presets.PRESETS) {
            assert.ok(preset.name && preset.detail, preset.id);
            assert.ok(preset.shot.isOpenPlay !== undefined, preset.id);
        }
    });

    test('the penalty is the one thing the model can be told about a penalty', () => {
        // There is no penalty feature. Twelve yards and is_open_play = 0 is the
        // whole of it, so a preset that left open play on would be a preset of
        // an ordinary shot from the spot.
        const penalty = presets.presetById('penalty');
        assert.equal(penalty.shot.isOpenPlay, false);
        assert.ok(Math.abs(penalty.shooter[1] - 11) < 0.5);
    });

    test('the sample-match presets sit where the fixture publishes the shots', () => {
        // What makes the number on the coach's preview reproducible here. The
        // fixture is metres on a 105x68 pitch attacking right, so a shot at x_m
        // is 105 - x_m out from the goal line.
        const shots = sample.sampleCvSummary().teams.team_a.shot_map;
        for (const [id, videoS] of [
            ['sample-opener', 412.4], ['sample-miss', 908.7],
        ]) {
            const shot = shots.find((s) => s.video_s === videoS);
            const [across, out] = presets.presetById(id).shooter;
            assert.ok(Math.abs(across - shot.y_m) < 0.05, id);
            assert.ok(Math.abs(out - (105 - shot.x_m)) < 0.05, id);
        }
    });
});

// ------------------------------------------------------- stats, by kind
//
// The grouping is presentation, so most of what could be tested here would just
// restate the table. What is worth pinning is the handful of decisions that
// would be silent failures on a coach's screen: a dropped row, a caption
// attached to figures that are not there, and the two pages disagreeing about
// which group a statistic belongs in.

describe('groupStats', () => {
    const row = (type, label, value = 1) => ({ type, label, value });

    test('keeps the order of the type list, not the order rows arrive in', () => {
        const groups = report.groupStats([
            row('defending', 'Tackles'),
            row('match', 'Goals for'),
            row('passing', 'Passes attempted'),
        ]);
        assert.deepEqual(groups.map((g) => g.id), ['match', 'passing', 'defending']);
    });

    test('drops a group with nothing in it rather than heading a blank space', () => {
        const groups = report.groupStats([row('match', 'Goals for')]);
        assert.equal(groups.length, 1);
    });

    test('drops a row the pipeline could not measure, and keeps a measured zero', () => {
        // The distinction the whole project turns on. A null is "not measured";
        // a zero is a measurement, and dropping it would delete a real answer.
        const groups = report.groupStats([
            { type: 'attacking', label: 'Shots', value: 0 },
            { type: 'attacking', label: 'Expected goals', value: null },
            { type: 'attacking', label: 'Crosses', value: undefined },
        ]);
        assert.deepEqual(groups[0].rows.map((r) => r.label), ['Shots']);
    });

    test('keeps a row whose type it does not recognise', () => {
        // Visibly wrong beats silently gone: a typo in a type name must not
        // delete a measured figure from a coach's screen.
        const groups = report.groupStats([row('possesion', 'Possession')]);
        assert.equal(groups.length, 1);
        assert.equal(groups[0].rows.length, 1);
    });

    test('drops a caption whose figures are not on screen', () => {
        // Both notes explain a denominator that only some rows have. A caption
        // about thirds, over a group with no thirds in it, would be read as
        // applying to whatever is there.
        const withThirds = report.groupStats([
            row('possession', 'Possession'),
            { type: 'possession', label: 'In their third', value: '17%', explained: true },
        ]);
        const without = report.groupStats([row('possession', 'Possession')]);

        assert.match(withThirds[0].note, /thirds/i);
        assert.equal(without[0].note, '');
    });
});

describe('teamStatRows', () => {
    const cv = (team = {}, extra = {}) => ({
        quality: {}, teams: { team_a: { team: 'team_a', ...team } }, ...extra,
    });
    const labelled = (rows) => Object.fromEntries(rows.map((r) => [r.label, r]));

    test('says nothing at all without a video run', () => {
        assert.deepEqual(report.teamStatRows(null), []);
        assert.deepEqual(report.teamStatRows({ teams: {} }), []);
    });

    test('every row carries a type this module knows', () => {
        // The check that makes the unknown-type fallback above a safety net
        // rather than something anyone relies on.
        const known = new Set(report.STAT_TYPES.map((t) => t.id));
        const rows = report.teamStatRows(cv({
            possession_pct: 0.5, passes_attempted: 100, shots: 3, tackles: 4,
            territory: { defensive: 0.4, middle: 0.4, attacking: 0.2 },
            shape: { width_m: 40, depth_m: 30, compactness_m: 14 },
        }));
        for (const r of rows) assert.ok(known.has(r.type), `${r.label}: ${r.type}`);
    });

    test('the possession label says what it was divided by', () => {
        // Two different claims wearing the same percentage. With a tagged log
        // the dead time is out of the denominator; without one, a player
        // standing over the ball waiting to take a throw counts as possession.
        const withLog = report.teamStatRows({
            quality: { live_share: 0.7 }, teams: { team_a: { possession_pct: 0.58 } },
        });
        const without = report.teamStatRows(cv({ possession_pct: 0.58 }));

        assert.ok(labelled(withLog)['Possession, ball in play']);
        assert.ok(labelled(without).Possession);
    });

    test('the passing breakdowns are shares of what was attempted', () => {
        // Counts alone cannot say how direct a side was: 142 forward passes is
        // a different story out of 341 than out of 160.
        const rows = labelled(report.teamStatRows(cv({
            passes_attempted: 341,
            passes_by_direction: { forward: 142, sideways: 131, backward: 68 },
            passes_by_length: { short: 198, medium: 109, long: 34 },
        })));
        assert.equal(rows['Played forward'].value, '42%');
        assert.equal(rows['Played long'].value, '10%');
    });

    test('a breakdown with no total to divide by is not shown', () => {
        const rows = labelled(report.teamStatRows(cv({
            passes_by_direction: { forward: 142 },
        })));
        assert.equal(rows['Played forward'].value, null);
    });

    test('the shape rows wait for a calibration', () => {
        // Width in metres is not something a pixel can answer, and three
        // zeroes would say the team stood on top of each other.
        assert.deepEqual(report.shapeStatRows(null, 0.4), []);
        assert.deepEqual(report.shapeStatRows({ width_m: null }, 0.4), []);
        assert.equal(report.shapeStatRows({ width_m: 41.2 }, 0.4).length, 3);
    });

    test('expected goals is withheld, not zeroed, past the trust band', () => {
        const shown = labelled(report.teamStatRows(
            cv({ xg: 1.44 }, { calibrationErrorM: 0.4 }),
        ));
        const withheld = labelled(report.teamStatRows(
            cv({ xg: 1.44 }, { calibrationErrorM: 9.0 }),
        ));
        assert.equal(shown['Expected goals'].value, '1.44');
        assert.equal(withheld['Expected goals'].value, null);
    });
});

// ------------------------------------------------------------- the heatmap
//
// The grid was computed per track, never carried across to the cluster, and
// never drawn — declared, published and null on every run. These cover the two
// things that can silently produce a plausible wrong picture: the weighting,
// and which axis is which.

describe('mergeHeatmaps', () => {
    // 2 wide (along the pitch) by 2 deep (across it), column-major.
    const grid = (values) => ({ cols: 2, rows: 2, values });

    const corner = grid([1, 0, 0, 0]);   // one end
    const far = grid([0, 0, 0, 1]);      // the other

    test('one grid comes back normalised', () => {
        const out = heatmap.mergeHeatmaps([{ grid: grid([2, 0, 0, 2]), minutes: 10 }]);
        assert.deepEqual(out.values, [0.5, 0, 0, 0.5]);
    });

    test('minutes decide the weight, not the number of fragments', () => {
        // The failure this prevents: a player tracked cleanly for 45 minutes
        // plus once more for eight seconds at the edge of frame, shown with a
        // hotspot at the edge of frame.
        const out = heatmap.mergeHeatmaps([
            { grid: corner, minutes: 45 },
            { grid: far, minutes: 5 },
        ]);
        assert.equal(out.values[0], 0.9);
        assert.equal(out.values[3], 0.1);
    });

    test('a fragment with no minutes still counts, but barely', () => {
        const out = heatmap.mergeHeatmaps([
            { grid: corner, minutes: 30 },
            { grid: far, minutes: 0 },
        ]);
        assert.ok(out.values[0] > 0.99);
        assert.ok(out.values[3] > 0);
    });

    test('grids of different shapes are skipped, not stretched', () => {
        // Two bin counts mean two different runs, and resampling one onto the
        // other would invent positions.
        const out = heatmap.mergeHeatmaps([
            { grid: corner, minutes: 10 },
            { grid: { cols: 3, rows: 3, values: new Array(9).fill(1) }, minutes: 10 },
        ]);
        assert.equal(out.cols, 2);
        assert.deepEqual(out.values, [1, 0, 0, 0]);
    });

    test('nothing to draw gives null, not an empty pitch', () => {
        // An empty pitch reads as a player who never moved.
        assert.equal(heatmap.mergeHeatmaps([]), null);
        assert.equal(heatmap.mergeHeatmaps(null), null);
        assert.equal(heatmap.mergeHeatmaps([{ grid: null, minutes: 90 }]), null);
        assert.equal(heatmap.mergeHeatmaps([{ grid: grid([0, 0, 0, 0]), minutes: 9 }]), null);
    });

    test('a malformed grid is refused rather than half-read', () => {
        assert.equal(heatmap.isGrid({ cols: 2, rows: 2, values: [1, 2] }), false);
        assert.equal(heatmap.isGrid({ cols: 0, rows: 0, values: [] }), false);
        assert.equal(heatmap.isGrid(undefined), false);
    });
});

describe('cellAt and busiestCell', () => {
    // 3 along the pitch by 2 across it. Column-major, so index = x * rows + y.
    const grid = { cols: 3, rows: 2, values: [0, 0, 0, 0.7, 0.3, 0] };

    test('x runs along the pitch and y across it', () => {
        // Getting this backwards draws a plausible picture of somebody who
        // played sideways, which is the kind of wrong that survives review.
        assert.equal(heatmap.cellAt(grid, 1, 1), 0.7);
        assert.equal(heatmap.cellAt(grid, 2, 0), 0.3);
        assert.equal(heatmap.cellAt(grid, 0, 0), 0);
    });

    test('the busiest cell is found in the same coordinates', () => {
        assert.deepEqual(heatmap.busiestCell(grid), { x: 1, y: 1, share: 0.7 });
    });

    test('an empty grid has no busiest cell', () => {
        assert.equal(heatmap.busiestCell({ cols: 2, rows: 2, values: [0, 0, 0, 0] }), null);
        assert.equal(heatmap.busiestCell(null), null);
    });
});

// ------------------------------------------------------------- the shot map
//
// The mirroring happens in Python (cv/report_json.py::shot_marks) so no
// renderer can forget it, which leaves two things worth pinning here: the size
// scale, and the totals printed under the map.

describe('markRadius', () => {
    test('area scales with xG, not radius', () => {
        // Scaling the radius directly makes a 0.4 chance look four times a 0.1
        // one instead of twice, and every shot map that does it overstates the
        // good chances. Area ∝ r², so r ∝ √xg.
        const area = (xg) => Math.PI * markMod.markRadius(xg) ** 2;
        const base = markMod.markRadius(0);

        // With the floor subtracted, the growth is √-shaped.
        const grow = (xg) => markMod.markRadius(xg) - base;
        assert.ok(Math.abs(grow(0.4) / grow(0.1) - 2) < 0.001);
        assert.ok(area(0.4) > area(0.1));
    });

    test('a shot with no xG still gets a dot', () => {
        // Every shot before the model was wired in has a null xG. It happened
        // whether or not it could be scored, and hiding it would undercount.
        assert.equal(markMod.markRadius(null), markMod.markRadius(0));
        assert.ok(markMod.markRadius(undefined) > 0);
    });

    test('an impossible xG is clamped rather than trusted', () => {
        assert.equal(markMod.markRadius(5), markMod.markRadius(1));
        assert.equal(markMod.markRadius(-1), markMod.markRadius(0));
    });

    test('a trust band below per-shot flattens every radius', () => {
        // The whole point: a size difference is a claim that two chances differ
        // by the amount they look like they differ, and a loose calibration
        // cannot support it. Measured, at 4m of error the p95 shift in one
        // shot's xG is larger than a typical xG.
        for (const band of ['total', 'none']) {
            const flat = markMod.markRadius(0.7, band);
            assert.equal(markMod.markRadius(0.02, band), flat);
            assert.equal(markMod.markRadius(null, band), flat);
        }
    });

    test('the flat radius sits between the extremes it replaces', () => {
        // Not the floor and not the ceiling — either would read as every shot
        // being uniformly poor or uniformly excellent.
        const flat = markMod.markRadius(0.4, 'total');
        assert.ok(flat > markMod.markRadius(0));
        assert.ok(flat < markMod.markRadius(1));
    });

    test('sizing is on by default, so an unaware caller gets the old map', () => {
        assert.equal(markMod.markRadius(0.4), markMod.markRadius(0.4, 'shot'));
    });
});

describe('markClass', () => {
    test('three outcomes and no more', () => {
        assert.equal(markMod.markClass({ outcome: 'goal', on_target: true }), 'is-goal');
        assert.equal(markMod.markClass({ outcome: 'saved', on_target: true }), 'is-on-target');
        assert.equal(markMod.markClass({ outcome: 'off_target' }), 'is-off');
        assert.equal(markMod.markClass(null), 'is-off');
    });
});

describe('shotSummary', () => {
    const marks = [
        { xg: 0.4, outcome: 'goal', on_target: true },
        { xg: 0.05, outcome: 'off_target', on_target: false },
        { xg: 0.2, outcome: 'saved', on_target: true },
    ];

    test('counts the three things worth printing', () => {
        const out = markMod.shotSummary(marks);
        assert.equal(out.shots, 3);
        assert.equal(out.onTarget, 2);
        assert.equal(out.goals, 1);
        assert.ok(Math.abs(out.xg - 0.65) < 1e-9);
    });

    test('no xG anywhere gives null, not zero', () => {
        // A run before the model was wired in has no expected goals, which is
        // not the same as a half with no chances in it.
        const out = markMod.shotSummary([{ outcome: 'off_target', on_target: false }]);
        assert.equal(out.xg, null);
        assert.equal(out.shots, 1);
    });

    test('xG sums only over the shots that carry one', () => {
        const out = markMod.shotSummary([{ xg: 0.3, on_target: true }, { on_target: false }]);
        assert.ok(Math.abs(out.xg - 0.3) < 1e-9);
        assert.equal(out.shots, 2);
    });

    test('nothing at all does not throw', () => {
        assert.deepEqual(markMod.shotSummary(null),
            { shots: 0, onTarget: 0, goals: 0, xg: null });
    });

    test('the total survives the band that per-shot xG does not', () => {
        // Per-shot errors are independent, so a half of them largely cancel.
        // That is the entire reason 'total' exists as a band of its own.
        const out = markMod.shotSummary(marks, 'total');
        assert.ok(Math.abs(out.xg - 0.65) < 1e-9);
        assert.equal(out.shots, 3);
    });

    test("at 'none' the counts stay and the xG goes", () => {
        const out = markMod.shotSummary(marks, 'none');
        assert.equal(out.xg, null);
        assert.equal(out.shots, 3);
        assert.equal(out.goals, 1);
    });
});

describe('xgTrust', () => {
    // The bands are read off tests/test_xg_noise.py, measured against the real
    // model. They are not taste, and changing one means re-running that.

    test('under a metre, a single shot is worth showing', () => {
        assert.equal(report.xgTrust(0), 'shot');
        assert.equal(report.xgTrust(0.5), 'shot');
        assert.equal(report.xgTrust(1.0), 'shot');
    });

    test('past a metre only the total is', () => {
        // At 2m the p95 shift is 0.201 on a 0.254 baseline — most of the quantity.
        assert.equal(report.xgTrust(1.01), 'total');
        assert.equal(report.xgTrust(2), 'total');
        assert.equal(report.xgTrust(4.0), 'total');
    });

    test('past four metres the error bar is wider than the number', () => {
        // Measured: p95 shift 0.344 against a mean clean xG of 0.254.
        assert.equal(report.xgTrust(4.01), 'none');
        assert.equal(report.xgTrust(50), 'none');
    });

    test('an unknown error is not treated as a good one', () => {
        // But it is not evidence of a bad fit either, so it lands on the
        // reading that stays true across the band it might be in.
        assert.equal(report.xgTrust(null), 'total');
        assert.equal(report.xgTrust(undefined), 'total');
        assert.notEqual(report.xgTrust(null), 'shot');
    });
});

// ------------------------------------------------------------ sample data
//
// The sample exists so the CV blocks can be seen before there is footage. That
// only works if it stays the same shape as a real published run, and if it goes
// through the same renderers rather than a preview path of its own. These tests
// hold both, plus the rule that nothing sampled is ever mistaken for real.

describe('sampleHeatmap', () => {
    test('is a grid the real renderer accepts', () => {
        // Not a bespoke shape for the preview. If this ever fails, the preview
        // has started proving something other than what it claims to.
        assert.ok(heatmap.isGrid(sample.sampleHeatmap()));
    });

    test('sums to 1, which every consumer of mergeHeatmaps assumes', () => {
        const grid = sample.sampleHeatmap();
        const total = grid.values.reduce((a, b) => a + b, 0);
        assert.ok(Math.abs(total - 1) < 1e-4, `summed to ${total}`);
    });

    test('is deterministic, so two loads draw the same pitch', () => {
        assert.deepEqual(sample.sampleHeatmap(), sample.sampleHeatmap());
    });

    test('has a hotspot rather than an even wash', () => {
        // A flat grid is drawn as nothing by the FLOOR in heatmap.js, so a
        // sample without a peak would preview an empty pitch.
        const grid = sample.sampleHeatmap();
        const busiest = heatmap.busiestCell(grid);
        assert.ok(busiest);
        assert.ok(heatmap.cellAt(grid, busiest.x, busiest.y)
            > 4 / (grid.cols * grid.rows));
    });
});

describe('the sample match', () => {
    test('every object says it is a sample', () => {
        assert.ok(sample.isSample(sample.sampleCvSummary()));
        assert.ok(sample.isSample(sample.samplePlayerReport()));
        assert.ok(!sample.isSample({}));
        assert.ok(!sample.isSample(null));
    });

    test('the calibration is good enough to size a shot map by', () => {
        // Stated in the module docstring as a deliberate choice. If it drifts
        // past a metre the preview silently stops showing the sized map, which
        // is most of what there is to look at.
        const cv = sample.sampleCvSummary();
        assert.equal(report.xgTrust(cv.calibrationErrorM), 'shot');
    });

    test('the shot map agrees with the shot count beside it', () => {
        // A preview whose own figures contradict each other teaches whoever
        // reads it to stop checking whether figures agree.
        for (const key of ['team_a', 'team_b']) {
            const team = sample.sampleCvSummary().teams[key];
            assert.equal(team.shot_map.length, team.shots);
            assert.equal(
                team.shot_map.filter((s) => s.on_target).length,
                team.shots_on_target,
            );
            assert.equal(
                team.shot_map.filter((s) => s.outcome === 'goal').length,
                team.goals,
            );
        }
    });

    test('the team xG is the sum of the shots on the map', () => {
        const team = sample.sampleCvSummary().teams.team_a;
        const summed = markMod.shotSummary(team.shot_map).xg;
        assert.ok(Math.abs(summed - team.xg) < 0.005, `${summed} vs ${team.xg}`);
    });

    test("the player's shots are on their own team's map", () => {
        // Track 7 is one of team_a's shooters. If the two drifted apart the
        // preview would be a preview of a bug.
        const mine = sample.samplePlayerReport().cvShotMap;
        const ours = sample.sampleCvSummary().teams.team_a.shot_map;
        assert.ok(mine.length);
        for (const shot of mine) {
            assert.ok(ours.some((s) => s.video_s === shot.video_s));
        }
    });

    test('the quality block trips the warnings it is meant to', () => {
        // The point of a realistic sample: a preview of a flawless run would
        // hide every caveat these pages exist to show.
        const cv = sample.sampleCvSummary();
        const notes = report.cvQualityNotes(cv.quality, {
            calibrated: cv.calibrated,
            shots: cv.teams.team_a.shots,
            calibrationErrorM: cv.calibrationErrorM,
            reconciliation: cv.reconciliation,
        }).join(' | ');

        assert.match(notes, /83% of frames/);
        assert.match(notes, /into about 3 pieces/);
        assert.match(notes, /matching neither kit/);
        assert.match(notes, /agree on 3 of 4 goals/);
    });

    test('it produces plain-language reads for the half-time page', () => {
        // cvReads only fires above deliberately high thresholds. A sample that
        // never trips one would leave the whole decisions block unpreviewable.
        const reads = report.cvReads(sample.sampleCvSummary());
        assert.ok(reads.length >= 2, `only ${reads.length} reads`);
        assert.match(reads.map((r) => r.title).join(' | '), /not where it counts/);
    });

    test('it carries a goal disagreement to look at', () => {
        // The highest-value row in the review block, and the one most likely to
        // be wired up wrong precisely because it is rare on real data.
        const { disagreements } = sample.sampleCvSummary().reconciliation;
        assert.equal(disagreements.length, 1);
        assert.equal(disagreements[0].status, 'tag_only');
        assert.ok(disagreements[0].tag_s > 0);
    });
});

describe('the sample breakdowns add up', () => {
    // A breakdown that does not sum to its own total is the kind of thing a
    // fixture should never model as acceptable — whoever reads the preview to
    // learn the shape of the data would learn the wrong shape.
    for (const key of ['team_a', 'team_b']) {
        for (const field of ['passes_by_length', 'passes_by_direction']) {
            test(`${key}.${field} sums to passes attempted`, () => {
                const team = sample.sampleCvSummary().teams[key];
                const total = Object.values(team[field]).reduce((a, b) => a + b, 0);
                assert.equal(total, team.passes_attempted);
            });
        }
    }

    test('possession shares across the two sides make a whole match', () => {
        const { team_a: a, team_b: b } = sample.sampleCvSummary().teams;
        assert.ok(Math.abs(a.possession_pct + b.possession_pct - 1) < 1e-9);
    });

    test("each territory split is a whole of that side's own possession", () => {
        for (const key of ['team_a', 'team_b']) {
            const t = sample.sampleCvSummary().teams[key].territory;
            const total = t.defensive + t.middle + t.attacking;
            assert.ok(Math.abs(total - 1) < 1e-9, `${key} summed to ${total}`);
        }
    });

    test('completed passes never exceed attempted', () => {
        for (const key of ['team_a', 'team_b']) {
            const t = sample.sampleCvSummary().teams[key];
            assert.ok(t.passes_completed <= t.passes_attempted);
            assert.ok(Math.abs(
                t.passes_completed / t.passes_attempted - t.pass_accuracy,
            ) < 0.001);
        }
    });
});

describe('the sample heatmap agrees with the sample shots', () => {
    test('the busiest cell is in the half this player attacks', () => {
        // Caught by looking at it, not by a test: the first version put a
        // striker's heatmap on the halfway line, right beside their own shot
        // map showing two shots from inside the box. Both plots passed every
        // assertion and described different players.
        const report = sample.samplePlayerReport();
        const grid = report.cvHeatmap;
        const busiest = heatmap.busiestCell(grid);

        assert.equal(report.cvAttackingEnd, 'right');
        assert.ok(busiest.x >= grid.cols / 2,
            `busiest column ${busiest.x} is in the defending half`);
    });

    test('their shots are in the same half the heatmap is', () => {
        // 105m pitch, already mirrored to attack right by report_json.
        for (const shot of sample.samplePlayerReport().cvShotMap) {
            assert.ok(shot.x_m > 52.5, `shot at ${shot.x_m}m`);
        }
    });
});
