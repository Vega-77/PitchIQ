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
// Only `nowIndex` is exercised here; the two renderers build DOM. It is in that
// module rather than in the callers because "which mark is the video inside"
// is arithmetic, and both the strip and the list have to answer it the same way.
import * as timeline from '../assets/timeline.js';
import * as heatmap from '../assets/heatmap.js';
import * as markMod from '../assets/shot-map.js';
import * as sample from '../assets/sample-report.js';
import * as passing from '../assets/passing.js';
// Only the sizing rules are exercised here — everything else in the module
// touches the DOM, and this suite deliberately has none.
import * as passMod from '../assets/pass-map.js';
import * as season from '../assets/season.js';
// Same split again: the scale and the radius are pure, the rest builds nodes.
import * as formMod from '../assets/form-chart.js';
// The sandbox's model half and its preset table. Neither touches the DOM or
// onnxruntime at import time — the session is only built on the first predict.
import * as xgModel from '../xg-sandbox/xg-model.js';
import * as presets from '../xg-sandbox/presets.js';
// The picker's geometry half. Zero imports for the same reason as the modules
// above: it has to be loadable without a browser.
import * as pitchModel from '../calibrate/pitch-model.js';

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

// `videoTime` used to live in video.js. It is now `matchClockMap(...).toVideo`,
// because one offset only holds until half-time — see the matchClockMap block
// at the foot of this file. These three cases moved with it.
describe('seeking to a match-clock reading', () => {
    const at = (offsetS) => report.matchClockMap({ videoOffsetS: offsetS });

    test('shifts a match-clock reading by the recording offset', () => {
        // Recording started two minutes before kick-off.
        assert.equal(at(120).toVideo(0), 120);
        assert.equal(at(120).toVideo(600), 720);
    });

    test('never goes negative', () => {
        // A mis-entered offset must not ask a player to seek to -30s.
        assert.equal(at(-600).toVideo(10), 0);
    });

    test('a missing offset is simply no shift', () => {
        assert.equal(at(undefined).toVideo(300), 300);
        assert.equal(at(null).toVideo(300), 300);
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

describe('windowClockRange', () => {
    const END = 5400;

    test('video seconds become match clock through the offset', () => {
        // The recording started two minutes before kick-off, so video 300s is
        // match clock 180s.
        const range = report.windowClockRange(
            { start_s: 300, end_s: 900 }, { videoOffsetS: 120, matchEndS: END },
        );
        assert.deepEqual(range, { startS: 180, endS: 780 });
    });

    test('it reads the camelCase spelling too', () => {
        // The window arrives from Python as snake_case and could reach a page
        // through a payload that renamed it. Both spellings, one meaning.
        assert.deepEqual(
            report.windowClockRange({ startS: 0, endS: 600 }, { matchEndS: END }),
            { startS: 0, endS: 600 },
        );
    });

    test('a missing end means the pipeline ran to the end of the file', () => {
        const range = report.windowClockRange({ start_s: 0 }, { matchEndS: END });
        assert.deepEqual(range, { startS: 0, endS: END });
    });

    test('footage that starts before kick-off is clamped to kick-off', () => {
        // The warm-up is not part of anybody's minutes, and a negative start
        // would silently widen every player's denominator.
        const range = report.windowClockRange(
            { start_s: 0, end_s: 600 }, { videoOffsetS: 120, matchEndS: END },
        );
        assert.equal(range.startS, 0);
    });

    test('with neither an end nor a match there is nothing to answer from', () => {
        assert.equal(report.windowClockRange({}, {}), null);
        assert.equal(report.windowClockRange(null, {}), null);
    });
});

describe('trackedCoverage', () => {
    const END = 5400;
    const WHOLE = { window: { start_s: 0, end_s: END }, matchEndS: END };

    test('a starter tracked for all of their half is fully covered', () => {
        // On 0-1800, and the tracker held them for all 30 minutes.
        const cover = report.trackedCoverage(30, STINTS.starter, WHOLE);
        assert.equal(cover.onPitchS, 1800);
        assert.equal(cover.watchedS, 1800);
        assert.equal(cover.share, 1);
    });

    test('fragmentation shows up as a share below one', () => {
        const cover = report.trackedCoverage(12, STINTS.starter, WHOLE);
        assert.equal(cover.share, 0.4);
    });

    test('a short clip is not held against the tracker', () => {
        // Five minutes of footage from a thirty-minute stint, and the tracker
        // had the player for all five. Measuring against their minutes played
        // would report 17% and read as a tracker that lost them.
        const cover = report.trackedCoverage(5, STINTS.starter, {
            window: { start_s: 0, end_s: 300 }, matchEndS: END,
        });
        assert.equal(cover.onPitchS, 1800);
        assert.equal(cover.watchedS, 300);
        assert.equal(cover.share, 1);
    });

    test('a substitute is scored against the part of the window they played', () => {
        // On at 30:00; the window is the first 40 minutes, so ten of their
        // minutes were filmed and the tracker had six of them.
        const cover = report.trackedCoverage(6, STINTS.sub, {
            window: { start_s: 0, end_s: 2400 }, matchEndS: END,
        });
        assert.equal(cover.watchedS, 600);
        assert.equal(cover.share, 0.6);
    });

    test('a player who was not on for any of the footage has no share', () => {
        // Not a share of zero. The window ended before they came on, so there
        // is nothing this run can say about them either way.
        const cover = report.trackedCoverage(0, STINTS.sub, {
            window: { start_s: 0, end_s: 600 }, matchEndS: END,
        });
        assert.equal(cover.watchedS, 0);
        assert.equal(cover.share, null);
    });

    test('no sub log means no answer, not a zero', () => {
        for (const stints of [null, [], undefined]) {
            const cover = report.trackedCoverage(30, stints, WHOLE);
            assert.equal(cover.onPitchS, null);
            assert.equal(cover.share, null);
            // The tracked minutes are still known — only the denominator is not.
            assert.equal(cover.trackedS, 1800);
        }
    });

    test('an unmatched player has no tracked time and no share', () => {
        const cover = report.trackedCoverage(null, STINTS.starter, WHOLE);
        assert.equal(cover.trackedS, null);
        assert.equal(cover.share, null);
        // The denominator survives: the sub log knows this without the video.
        assert.equal(cover.onPitchS, 1800);
    });

    test('two clusters on screen at once push the share above one', () => {
        // Which is the only symptom of a mapping that double-counts, so it has
        // to survive rather than be clamped to a tidy 100%.
        const cover = report.trackedCoverage(45, STINTS.starter, WHOLE);
        assert.ok(cover.share > 1.15);
    });
});

describe('coverageNote', () => {
    const END = 5400;
    const WHOLE = { window: { start_s: 0, end_s: END }, matchEndS: END };
    const noteFor = (mins, stints, ctx = WHOLE, opts) =>
        report.coverageNote(report.trackedCoverage(mins, stints, ctx), opts);

    test('good coverage states the minutes and stops', () => {
        const note = noteFor(27, STINTS.starter);
        assert.match(note, /27 of the 30 minutes/);
        assert.doesNotMatch(note, /part of the match/);
    });

    test('thin coverage says the totals are a sample', () => {
        const note = noteFor(12, STINTS.starter);
        assert.match(note, /part of the match rather than all of it/);
    });

    test('filmed and played are named separately when they differ', () => {
        // Otherwise the sentence blames the tracker for minutes nobody filmed.
        const note = noteFor(5, STINTS.starter, {
            window: { start_s: 0, end_s: 300 }, matchEndS: END,
        });
        assert.match(note, /covered 5 of the 30 minutes/);
        assert.match(note, /measured 5 of those/);
    });

    test('a double-counting mapping is called out, not smoothed over', () => {
        const note = noteFor(45, STINTS.starter);
        assert.match(note, /same time/);
        assert.match(note, /twice/);
    });

    test('it addresses a player directly on their own report', () => {
        assert.match(noteFor(27, STINTS.starter, WHOLE, { second: true }), /you played/);
        assert.match(noteFor(27, STINTS.starter), /they played/);
    });

    test('nothing to say is null, not an empty hedge', () => {
        assert.equal(noteFor(30, null), null);
        assert.equal(noteFor(null, STINTS.starter), null);
        assert.equal(report.coverageNote(null), null);
    });
});
describe('playerWobbleNote', () => {
    const CEILING = report.ACCEL_NOISE_CEILING_M;

    test('a wobble nobody measured is null, not a steady track', () => {
        assert.equal(report.playerWobbleNote(null), null);
        assert.equal(report.playerWobbleNote(undefined), null);
        // A fragment too short to measure one reports nothing, and the JSON
        // round trip can turn that into a zero. Zero metres of wobble is a
        // claim about precision, and this has none to make.
        assert.equal(report.playerWobbleNote(0), null);
    });

    test('a steady track explains the metres and stops there', () => {
        const note = report.playerWobbleNote(0.12);
        assert.match(note, /wobbled about 0.12m/);
        assert.match(note, /as well as your running/);
        assert.doesNotMatch(note, /burst/);
    });

    test('a noisy track says why the burst count is missing', () => {
        const note = report.playerWobbleNote(0.42);
        assert.match(note, /wobbled about 0.42m/);
        assert.match(note, /count of bursts is a count of that wobble/);
        assert.match(note, new RegExp(`Past ${CEILING}m`));
    });

    test('the ceiling matches the pipeline, boundary included', () => {
        // `cv/metrics.py` withholds bursts on `noise_m > MAX_ACCEL_NOISE_M`,
        // so a track sitting exactly on the ceiling still has a burst count.
        // Saying it does not would caption a card that is on the page.
        assert.doesNotMatch(report.playerWobbleNote(CEILING), /burst/);
        assert.match(report.playerWobbleNote(CEILING + 0.001), /burst/);
    });

    test('it quotes no phantom-metres rate', () => {
        // The smoothing window is fitted per track and published per run, so a
        // rate worked out on this side would be a rate for a window this file
        // cannot see. `cvQualityNotes` reads the published one; this says the
        // metres carry some wobble and leaves the arithmetic alone.
        for (const noise of [0.05, 0.12, CEILING, 0.42, 1.2]) {
            assert.doesNotMatch(report.playerWobbleNote(noise), /a minute|per minute/);
        }
    });

    test('it addresses the player, since the player page is its only caller', () => {
        assert.match(report.playerWobbleNote(0.12), /put you/);
    });
});

describe('metresPerMinute', () => {
    test('it is a rate over the time the video actually had them', () => {
        assert.equal(report.metresPerMinute(6000, 60), 100);
    });

    test('a substitute and a starter become comparable', () => {
        // 2km in 20 tracked minutes beats 6km in 80. The kilometre column says
        // the opposite, which is the whole reason this exists.
        assert.ok(
            report.metresPerMinute(2000, 20) > report.metresPerMinute(6000, 80),
        );
    });

    test('no distance or no tracked time is null, never zero', () => {
        assert.equal(report.metresPerMinute(null, 60), null);
        assert.equal(report.metresPerMinute(6000, 0), null);
        assert.equal(report.metresPerMinute(6000, null), null);
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

describe('the other figures that could be the same person', () => {
    // A blue shirt and a red one, in Lab. Far enough apart in chroma to be
    // different kits by identity.py's own threshold.
    const BLUE = [50, 20, -40];
    const BLUEISH = [62, 24, -36];      // same shirt, sunlit half of the pitch
    const RED = [50, 55, 35];

    const figure = (id, from, to, extra = {}) => ({
        cluster_id: id,
        first_seen_s: from,
        last_seen_s: to,
        team: 'team_a',
        colour: BLUE,
        sightings: 100,
        ...extra,
    });

    // Figure 0 was tracked for the first two minutes. Figure 1 picks up twenty
    // seconds later — the same player back from behind an opponent. Figure 2
    // was on screen the whole time, so it is somebody else.
    const clusters = () => [
        figure(0, 0, 120),
        figure(1, 140, 260),
        figure(2, 0, 300),
        figure(3, 400, 500, { colour: RED, team: 'team_b' }),
    ];

    const of = (id, options = {}) => report.sameFigureCandidates(
        clusters(), clusters().find((c) => c.cluster_id === id), options,
    );

    test('the nearest fragment in time leads', () => {
        assert.equal(of(0)[0].cluster.cluster_id, 1);
        assert.equal(of(0)[0].gapS, 20);
    });

    test('a figure on screen at the same time is ruled out', () => {
        const two = of(0).find((c) => c.cluster.cluster_id === 2);
        assert.equal(two.ruledOut, true);
        assert.equal(two.overlapS, 120);
    });

    test('an overlap inside one bridge is not an exclusion', () => {
        // Two seconds is what cv/identity.py will bridge, so an overlap that
        // small is the rounding at the seam of one player, not two people.
        // tests/test_identity.py asserts the invariant this rests on.
        const near = [figure(0, 0, 120), figure(1, 119, 240)];
        const [only] = report.sameFigureCandidates(near, near[0], {});
        assert.equal(only.overlapS, 1);
        assert.equal(only.ruledOut, false);
    });

    test('a figure in the other kit is ruled out', () => {
        const three = of(0).find((c) => c.cluster.cluster_id === 3);
        assert.equal(three.sameTeam, false);
        assert.equal(three.ruledOut, true);
    });

    test('nothing is dropped, however badly it fits', () => {
        // The same rule rankRosterForCluster follows. A ruled-out row is still
        // a row: the coach can see what was rejected and why.
        assert.equal(of(0).length, 3);
        assert.ok(of(0).some((c) => c.cluster.cluster_id === 3));
    });

    test('the ruled-out figures sort to the bottom', () => {
        const flags = of(0).map((c) => c.ruledOut);
        assert.deepEqual(flags, [false, true, true]);
    });

    test('a shirt that does not match sorts down without being excluded', () => {
        // Same team, no time clash, but a red shirt among blue ones. Evidence,
        // not proof: one bad colour sample in shade is a thing that happens.
        const odd = [
            figure(0, 0, 120),
            figure(1, 400, 500, { colour: RED }),
            figure(2, 140, 260, { colour: RED }),
        ];
        const ranked = report.sameFigureCandidates(odd, odd[0], {});
        assert.ok(ranked.every((c) => !c.ruledOut));
        assert.ok(ranked[0].kitS > report.SAME_KIT_CHROMA);
    });

    test('the same shirt in different light still reads as the same shirt', () => {
        const lit = [figure(0, 0, 120), figure(1, 140, 260, { colour: BLUEISH })];
        const [only] = report.sameFigureCandidates(lit, lit[0], {});
        assert.ok(only.kitS < report.SAME_KIT_CHROMA);
        assert.equal(only.ruledOut, false);
    });

    test('a missing colour is no evidence rather than a perfect match', () => {
        assert.equal(report.kitDistance(null, BLUE), null);
        assert.equal(report.kitDistance(BLUE, undefined), null);
        const blank = [figure(0, 0, 120), figure(1, 140, 260, { colour: null })];
        const [only] = report.sameFigureCandidates(blank, blank[0], {});
        assert.equal(only.kitS, null);
        assert.equal(only.ruledOut, false);
    });

    test('an unknown kit is not a different kit', () => {
        const unsure = [figure(0, 0, 120), figure(1, 140, 260, { team: 'unknown' })];
        const [only] = report.sameFigureCandidates(unsure, unsure[0], {});
        assert.equal(only.sameTeam, null);
        assert.equal(only.ruledOut, false);
    });

    test('a figure already named as someone else says who has it', () => {
        const ranked = of(0, { mapping: { 1: 'sub' }, player: 'starter' });
        assert.equal(ranked[0].takenBy, 'sub');
    });

    test('a figure someone else already has sorts below the free ones', () => {
        // Closest in time, so it would lead on the gap alone. It is still
        // offered — a coach who named it wrongly finds that out here — but it
        // is not the first thing suggested.
        const near = [figure(0, 0, 120), figure(1, 130, 200), figure(2, 300, 400)];
        const ranked = report.sameFigureCandidates(near, near[0], {
            mapping: { 1: 'sub' }, player: 'starter',
        });
        assert.deepEqual(ranked.map((c) => c.cluster.cluster_id), [2, 1]);
    });

    test('a figure already named as this same player is not "taken"', () => {
        const ranked = of(0, { mapping: { 1: 'starter' }, player: 'starter' });
        assert.equal(ranked[0].takenBy, null);
    });

    test('a candidate is scored against the minutes the player actually played', () => {
        // Figure 1 ran 140-260 in the video, which is 20-140 on a clock two
        // minutes behind it. The substitute did not come on until 30:00.
        const ranked = of(0, {
            player: 'sub', roster: squad(), videoOffsetS: 120, matchEndS: 5400,
        });
        assert.equal(ranked[0].playedShare, 0);

        const starter = of(0, {
            player: 'starter', roster: squad(), videoOffsetS: 120, matchEndS: 5400,
        });
        assert.equal(starter[0].playedShare, 1);
    });

    test('with nobody named there is no played share to report', () => {
        assert.equal(of(0)[0].playedShare, null);
    });

    test('a figure never compares against itself', () => {
        assert.ok(of(0).every((c) => c.cluster.cluster_id !== 0));
    });

    test('nothing to compare against is an empty list, not an error', () => {
        assert.deepEqual(report.sameFigureCandidates([], clusters()[0], {}), []);
        assert.deepEqual(report.sameFigureCandidates(null, clusters()[0], {}), []);
        assert.deepEqual(report.sameFigureCandidates(clusters(), null, {}), []);
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

    test('an unmapped player gets every cv field nulled', () => {
        // Nulls, not zeroes: a zero says the video measured them and found
        // nothing. And nulls rather than *nothing*, which is the change — the
        // publish is a merge now, so an omitted key keeps whatever was there,
        // and a player whose cluster a coach un-mapped would have gone on
        // carrying the numbers from before.
        const fields = report.cvReportFields(undefined);
        assert.ok(Object.keys(fields).length > 15);
        assert.ok(Object.values(fields).every((v) => v === null));
    });

    test('the nulls reach the fields only the pipeline writes', () => {
        // The heatmap, the attacking end and the calibration error are written
        // by cv/publish.py after this document exists. Under a merge, nothing
        // else would ever clear them.
        const fields = report.cvReportFields(undefined);
        for (const key of ['cvHeatmap', 'cvAttackingEnd', 'cvCalibrationErrorM',
            'cvShotMap']) {
            assert.equal(fields[key], null, key);
        }
    });

    test('a mapped player leaves the pipeline-only fields alone', () => {
        // The other half of the merge. This payload must not mention the
        // heatmap at all, or every re-publish would blank it — which is exactly
        // what the old overwrite was doing.
        const stats = report.cvStatsByPlayer([track(0)], { 0: ME })[ME];
        const fields = report.cvReportFields(stats);
        assert.equal('cvHeatmap' in fields, false);
        assert.equal('cvAttackingEnd' in fields, false);
        assert.equal('cvCalibrationErrorM' in fields, false);
    });

    test('a header the coach tagged reaches the player\'s own numbers', () => {
        // Otherwise the same match reads one way on the coach's page and
        // another on the player's, and neither side can see the other well
        // enough to notice.
        const shooter = {
            ...track(0),
            xg: 0.82,
            shot_map: [
                { event_id: 'a', video_s: 10, xg: 0.72, xg_header: 0.43 },
                { event_id: 'b', video_s: 20, xg: 0.10, xg_header: 0.08 },
            ],
        };
        const stats = report.cvStatsByPlayer([shooter], { 0: ME })[ME];
        const fields = report.cvReportFields(stats, null, [
            { id: 'a', header: true, xg: 0.43, counted: true },
        ]);

        assert.equal(fields.cvXg, 0.53);
        assert.equal(fields.cvShotMap[0].xg, 0.43);
        assert.equal(fields.cvShotMap[0].is_header, true);
        assert.equal(fields.cvShotMap[1].xg, 0.10);
    });

    test('with nothing tagged the total is the pipeline\'s own', () => {
        const shooter = {
            ...track(0),
            xg: 0.82,
            shot_map: [
                { event_id: 'a', video_s: 10, xg: 0.72 },
                { event_id: 'b', video_s: 20, xg: 0.10 },
            ],
        };
        const stats = report.cvStatsByPlayer([shooter], { 0: ME })[ME];
        assert.equal(report.cvReportFields(stats).cvXg, 0.82);
    });

    test('a player with no shot map keeps the xG the pipeline gave them', () => {
        // A report from before shot coordinates were published still has a
        // per-track total, and summing an empty list would zero it.
        const stats = report.cvStatsByPlayer(
            [{ ...track(0), xg: 0.4 }], { 0: ME },
        )[ME];
        assert.equal(report.cvReportFields(stats).cvXg, 0.4);
    });

    test('shots from two clusters land in one map, in order', () => {
        // A player split by the tracker is still one player, and their shots
        // arrive in whatever order the mapping happened to be written in.
        const stats = report.cvStatsByPlayer([
            { ...track(0), shot_map: [{ event_id: 'late', video_s: 90, xg: 0.2 }] },
            { ...track(1), shot_map: [{ event_id: 'early', video_s: 10, xg: 0.3 }] },
        ], { 0: ME, 1: ME })[ME];
        assert.deepEqual(
            report.cvReportFields(stats).cvShotMap.map((m) => m.event_id),
            ['early', 'late'],
        );
    });

    test('coverage travels onto the report when it is known', () => {
        const stats = report.cvStatsByPlayer([track(0)], { 0: ME })[ME];
        const fields = report.cvReportFields(
            stats,
            report.trackedCoverage(30, [{ inS: 0, outS: 2700 }], {
                window: { start_s: 0, end_s: 2700 }, matchEndS: 5400,
            }),
        );
        assert.equal(fields.cvMinutesOnPitch, 45);
        assert.equal(fields.cvMinutesFilmed, 45);
        assert.ok(Math.abs(fields.cvTrackedShare - 30 / 45) < 1e-9);
    });

    test('without coverage the report is what it always was', () => {
        // Python has no sub log, so a run published before this existed — or by
        // anything but the coach's page — simply carries no denominator.
        const stats = report.cvStatsByPlayer([track(0)], { 0: ME })[ME];
        const fields = report.cvReportFields(stats);
        assert.equal(fields.cvMinutesOnPitch, null);
        assert.equal(fields.cvTrackedShare, null);
        assert.ok(fields.cvTouches != null);
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

describe('seasonGroups', () => {
    const totals = (extra = {}) => ({
        matches: 8, minutes: 708, minutesUnknown: 0, goals: 4, assists: 3,
        yellowCards: 0, redCards: 0, fouls: 2,
        cvMatches: 0, cvTouches: 0, cvPassesAttempted: 0, cvPassesCompleted: 0,
        cvCarries: 0, cvTackles: 0, cvInterceptions: 0, cvRecoveries: 0,
        cvShots: 0, cvXg: 0, cvDistanceM: 0, cvTopSpeedKmh: 0, cvSprintCount: 0,
        cvAccelerations: 0, ...extra,
    });
    const ids = (groups) => groups.map((g) => g.id);
    const labels = (group) => group.rows.map((r) => r.label);

    test('a season nobody filmed is one group, not one and three empty headings', () => {
        assert.deepEqual(ids(report.seasonGroups([], totals())), ['tagged']);
    });

    test('footage adds three groups, each saying what it was measured over', () => {
        const groups = report.seasonGroups([], totals({
            cvMatches: 6, cvTouches: 367, cvPassesAttempted: 240,
            cvPassesCompleted: 187, cvDistanceM: 27400, cvTackles: 3,
        }));
        assert.deepEqual(ids(groups), ['tagged', 'ball', 'running', 'defending']);
        for (const group of groups.slice(1)) {
            assert.match(group.note, /6 filmed matches/);
        }
    });

    test('every video figure is marked as estimated', () => {
        const groups = report.seasonGroups([], totals({
            cvMatches: 6, cvTouches: 367, cvTackles: 3, cvDistanceM: 27400,
        }));
        for (const group of groups.filter((g) => g.id !== 'tagged')) {
            for (const row of group.rows) {
                assert.equal(row.confidence, 'medium', `${row.label} is marked`);
            }
        }
        // And nothing in the tagged group is: a goal somebody pressed a button
        // for must not carry the same mark as a distance a machine guessed.
        const tagged = groups.find((g) => g.id === 'tagged');
        assert.ok(tagged.rows.every((row) => !row.confidence));
    });

    test('every figure the tablet records survives the grouping', () => {
        // Fouls went missing when the coach's flat pile of boxes and the
        // player's were merged into one function — a figure that had been on
        // the coach's screen for months, gone in a refactor that nothing in the
        // suite would have noticed. This list is what the tagging tool writes,
        // so it is what the season has to show back.
        const labels = report.seasonGroups([], totals()).find((g) => g.id === 'tagged')
            .rows.map((r) => r.label);
        for (const wanted of ['Matches', 'Minutes played', 'Goals', 'Assists',
            'Fouls', 'Cards']) {
            assert.ok(labels.includes(wanted), `${wanted} is on the season`);
        }
    });

    test('a season with no clock kept shows a dash, never nought minutes', () => {
        const groups = report.seasonGroups([], totals({ minutes: 0, minutesUnknown: 8 }));
        const minutes = groups[0].rows.find((r) => r.label === 'Minutes played');
        assert.equal(minutes.value, '—');
    });

    test('per 90 needs a denominator worth dividing into', () => {
        assert.ok(!labels(report.seasonGroups([], totals({ minutes: 30 }))[0])
            .includes('G+A per 90'));
        assert.ok(labels(report.seasonGroups([], totals({ minutes: 90 }))[0])
            .includes('G+A per 90'));
    });

    test('their best afternoon is named, and only when there was one', () => {
        const season = [
            { opponentName: 'Linden', goals: 0, assists: 0, minutesPlayed: 90 },
            { opponentName: 'Summit', goals: 2, assists: 1, minutesPlayed: 90 },
        ];
        const rows = report.seasonGroups(season, totals()).find((g) => g.id === 'tagged').rows;
        const best = rows.find((r) => String(r.label).startsWith('Best'));
        assert.equal(best.label, 'Best · Summit');
        assert.equal(best.value, 3);

        const quiet = report.seasonGroups(
            [{ opponentName: 'Linden', goals: 0, assists: 0 }], totals(),
        );
        assert.ok(!labels(quiet[0]).some((l) => String(l).startsWith('Best')));
    });

    test('a match with no clock is not a short match', () => {
        // Unknown minutes must not count as a full match and must not count
        // against them either — it is simply not in the denominator.
        const season = [
            { minutesPlayed: 90, minutesKnown: true },
            { minutesPlayed: 0, minutesKnown: false },
        ];
        const full = report.seasonGroups(season, totals()).find((g) => g.id === 'tagged')
            .rows.find((r) => r.label === 'Full matches');
        assert.equal(full.value, 1);
    });

    test('the person changes without mangling the rest of the sentence', () => {
        // A `.replace(' them.', ' you.')` on the finished string turned "a pass
        // is two touches with a ball between them" into "…between you", which
        // is why the voice is an argument rather than a patch.
        const filmed = totals({
            cvMatches: 6, cvTouches: 367, cvPassesAttempted: 240,
            cvPassesCompleted: 187, cvDistanceM: 27400,
        });
        const third = report.seasonGroups([], filmed);
        const second = report.seasonGroups([], filmed, { second: true });

        const noteOf = (groups, id) => groups.find((g) => g.id === id).note;
        assert.match(noteOf(third, 'running'), /held on to them\./);
        assert.match(noteOf(second, 'running'), /held on to you\./);
        for (const groups of [third, second]) {
            assert.match(noteOf(groups, 'ball'), /a ball between them\./);
        }
    });

    test('called with nothing at all', () => {
        assert.deepEqual(ids(report.seasonGroups()), ['tagged']);
        assert.deepEqual(ids(report.seasonGroups(null, null)), ['tagged']);
    });
});

describe('the season at a glance', () => {
    const played = (id, us, them, extra = {}) => ({
        id, opponentName: id, finalized: true, status: 'full_time',
        scoreUs: us, scoreThem: them, ...extra,
    });

    describe('formGuide', () => {
        test('oldest first, however the list arrived', () => {
            // The matches tab holds them newest first, and a form guide is read
            // left to right in the order the season happened.
            const guide = report.formGuide([
                played('e', 3, 0), played('d', 1, 1), played('c', 0, 2),
                played('b', 2, 1), played('a', 1, 0),
            ]);
            assert.deepEqual(guide.map((g) => g.id), ['a', 'b', 'c', 'd', 'e']);
            assert.deepEqual(guide.map((g) => g.result), ['W', 'W', 'L', 'D', 'W']);
        });

        test('a fixture nobody played is not a gap in the run', () => {
            // Five results with a hole in the middle would say a match was
            // played and produced nothing.
            const guide = report.formGuide([
                played('c', 1, 0),
                { id: 'b', finalized: false, status: 'scheduled' },
                played('a', 0, 1),
            ]);
            assert.deepEqual(guide.map((g) => g.id), ['a', 'c']);
        });

        test('takes the most recent, not the first written', () => {
            const many = ['f', 'e', 'd', 'c', 'b', 'a'].map((id) => played(id, 1, 0));
            assert.deepEqual(
                report.formGuide(many, 3).map((g) => g.id), ['d', 'e', 'f'],
            );
        });

        test('nothing played is an empty run rather than a throw', () => {
            assert.deepEqual(report.formGuide([]), []);
            assert.deepEqual(report.formGuide(null), []);
        });
    });

    describe('nextFixture', () => {
        const fixture = (id, date, extra = {}) => ({
            id, date, opponentName: id, finalized: false, status: 'scheduled', ...extra,
        });

        test('the soonest one that has not been played', () => {
            const next = report.nextFixture([
                fixture('c', '2026-05-20'),
                fixture('a', '2026-05-16'),
                fixture('b', '2026-05-18'),
            ], '2026-05-15');
            assert.equal(next.match.id, 'a');
            assert.equal(next.daysAway, 1);
        });

        test('today counts as next, not as past', () => {
            const next = report.nextFixture([fixture('a', '2026-05-15')], '2026-05-15');
            assert.equal(next.daysAway, 0);
        });

        test('a fixture nobody tagged is a job, not the next match', () => {
            // Showing a game that has already been played under "next up" would
            // put it at the top of the page every week until somebody dealt
            // with it — which is what `seasonJobs` is for.
            assert.equal(report.nextFixture([fixture('gone', '2026-05-02')], '2026-05-15'), null);
        });

        test('a finished match is never next', () => {
            assert.equal(
                report.nextFixture(
                    [fixture('a', '2026-05-20', { finalized: true })], '2026-05-15',
                ),
                null,
            );
        });

        test('a fixture with no date is skipped rather than sorted to an end', () => {
            const next = report.nextFixture([
                fixture('undated', null), fixture('a', '2026-05-20'),
            ], '2026-05-15');
            assert.equal(next.match.id, 'a');
        });

        test('nothing to compare against means no answer', () => {
            assert.equal(report.nextFixture([fixture('a', '2026-05-20')], null), null);
            assert.equal(report.nextFixture([], '2026-05-15'), null);
        });
    });

    describe('daysBetween', () => {
        test('counts calendar days, not elapsed hours', () => {
            assert.equal(report.daysBetween('2026-05-15', '2026-05-16'), 1);
            assert.equal(report.daysBetween('2026-05-16', '2026-05-15'), -1);
            assert.equal(report.daysBetween('2026-05-15', '2026-05-15'), 0);
        });

        test('across a month and a year', () => {
            assert.equal(report.daysBetween('2026-02-28', '2026-03-01'), 1);
            assert.equal(report.daysBetween('2025-12-31', '2026-01-01'), 1);
        });

        test('anything that is not a date is no answer', () => {
            assert.equal(report.daysBetween('2026-05-15', null), null);
            assert.equal(report.daysBetween('', '2026-05-15'), null);
            assert.equal(report.daysBetween('15/05/2026', '2026-05-15'), null);
        });
    });

    describe('whenLabel', () => {
        test('the near days get names', () => {
            assert.equal(report.whenLabel(0), 'today');
            assert.equal(report.whenLabel(1), 'tomorrow');
            assert.equal(report.whenLabel(4), 'in 4 days');
        });

        test('further out loses the precision nobody needs', () => {
            assert.equal(report.whenLabel(9), 'next week');
            assert.equal(report.whenLabel(21), 'in 3 weeks');
        });

        test('nothing is not a day', () => {
            assert.equal(report.whenLabel(null), null);
        });
    });

    describe('seasonJobs', () => {
        const ids = (jobs) => jobs.map((j) => j.id);

        test('a squad with nothing outstanding has no jobs', () => {
            const jobs = report.seasonJobs({
                matches: [played('a', 1, 0)],
                players: [{ linkedUid: 'u1', position: 'mid' }],
                today: '2026-05-10',
            });
            assert.deepEqual(jobs, []);
        });

        test('played and not published comes first', () => {
            const jobs = report.seasonJobs({
                matches: [{ id: 'a', finalized: false, status: 'full_time' }],
                players: [{ linkedUid: null, position: null }],
                today: '2026-05-10',
            });
            assert.equal(ids(jobs)[0], 'publish');
            assert.equal(jobs[0].count, 1);
            assert.match(jobs[0].title, /1 match to publish/);
        });

        test('a fixture in the future is not outstanding work', () => {
            // A match created for Saturday is not a job on Thursday, and
            // listing it as one would make the panel noise every week.
            const matches = [
                { id: 'soon', finalized: false, status: 'scheduled', date: '2026-05-16' },
                { id: 'gone', finalized: false, status: 'scheduled', date: '2026-05-02' },
            ];
            const jobs = report.seasonJobs({ matches, players: [], today: '2026-05-10' });
            assert.deepEqual(ids(jobs), ['untagged']);
            assert.equal(jobs[0].count, 1);
        });

        test('with no date to compare against, nothing is called late', () => {
            const matches = [{ id: 'gone', finalized: false, status: 'scheduled', date: '2026-05-02' }];
            assert.deepEqual(ids(report.seasonJobs({ matches, players: [] })), []);
        });

        test('a player who left the squad is not outstanding work', () => {
            const players = [
                { linkedUid: null, position: null, active: false },
                { linkedUid: null, position: 'gk' },
            ];
            const jobs = report.seasonJobs({ players, today: '2026-05-10' });
            assert.deepEqual(ids(jobs), ['invite']);
            assert.equal(jobs[0].count, 1);
        });

        test('an unrecognised position still counts as unset', () => {
            // `positionOf` is the one authority on what a position is, and a
            // typo in a document must not read as an answered question.
            const jobs = report.seasonJobs({
                players: [{ linkedUid: 'u1', position: 'sweeper' }],
                today: '2026-05-10',
            });
            assert.deepEqual(ids(jobs), ['position']);
        });

        test('every job carries a count, a next step and where to do it', () => {
            const jobs = report.seasonJobs({
                matches: [{ id: 'a', finalized: false, status: 'full_time' }],
                players: [{ linkedUid: null, position: null }],
                today: '2026-05-10',
            });
            for (const job of jobs) {
                assert.ok(job.count > 0, `${job.id} has a count`);
                assert.ok(job.note, `${job.id} says what to do`);
                assert.ok(['matches', 'roster'].includes(job.tab), `${job.id} has a tab`);
            }
        });

        test('called with nothing at all', () => {
            assert.deepEqual(report.seasonJobs(), []);
            assert.deepEqual(report.seasonJobs({}), []);
        });
    });
});

describe('railTarget', () => {
    test('the section asked for, when it is there', () => {
        assert.equal(report.railTarget(['a', 'b', 'c'], 'b'), 'b');
    });

    test('falls back to the first when the chosen one has gone', () => {
        // The set of sections changes under a reader: turning the sample
        // preview off takes four blocks away and one of them may be the one
        // being read. Landing on the first is where every report opens anyway.
        assert.equal(report.railTarget(['a', 'b'], 'gone'), 'a');
        assert.equal(report.railTarget(['a', 'b'], null), 'a');
        assert.equal(report.railTarget(['a', 'b'], undefined), 'a');
    });

    test('null only when there is genuinely nothing to show', () => {
        assert.equal(report.railTarget([], 'a'), null);
        assert.equal(report.railTarget(null, 'a'), null);
    });

    test('an id that is empty is not a section', () => {
        // `block.id` is '' for a section nobody gave one, and falling back to
        // it would toggle every block at once — `groupOf` returns '' for all
        // of them.
        assert.equal(report.railTarget(['', 'b'], ''), 'b');
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

    // ---- how much of the pitch was in frame ----
    //
    // The reason this is said at all: an unseen third does not read as unseen.
    // Territory divides possession across the thirds, so a band the camera
    // never held comes out as a side that never went there.

    const coverage = (over) => ({
        pitch_coverage: {
            visible_share: 1.0, complete: true,
            thirds: { left: 1, middle: 1, right: 1 },
            goalmouths: { left: 1, right: 1 },
            ...over,
        },
    });

    test('a camera that framed the pitch says nothing about it', () => {
        // The expected case. A line confirming it would be noise in a list
        // whose entire job is caveats.
        const notes = report.cvQualityNotes(coverage({}), { calibrated: true });
        assert.ok(!notes.some((n) => n.includes('pitch')), notes);
    });

    test('a partial frame says what share of the pitch it was', () => {
        const text = joined(
            coverage({ visible_share: 0.82, complete: false }),
            { calibrated: true },
        );
        assert.match(text, /framed 82% of the pitch/);
        // And says why that matters, rather than leaving a bare percentage.
        assert.match(text, /shares of that part of it/);
    });

    test('a goalmouth out of shot outranks the percentage', () => {
        // Not a matter of degree: shots at that end were not undercounted,
        // they were never seen, and the shot map is missing them entirely.
        const text = joined(
            coverage({
                visible_share: 0.78, complete: false,
                goalmouths: { left: 1, right: 0.1 },
            }),
            { calibrated: true },
        );
        assert.match(text, /1 goalmouth never in shot/);
        assert.match(text, /no shot at that end was seen/);
        assert.ok(!text.includes('framed 78%'), text);
    });

    test('both goalmouths missing reads as both', () => {
        const text = joined(
            coverage({
                visible_share: 0.4, complete: false,
                goalmouths: { left: 0.0, right: 0.2 },
            }),
            { calibrated: true },
        );
        assert.match(text, /2 goalmouths never in shot/);
        assert.match(text, /either end/);
    });

    test('an uncalibrated run says nothing — there is nothing to measure it in', () => {
        // It still says "no pitch calibration", which is the note above this
        // one. What must not appear is a share of a pitch nothing was measured
        // against.
        const text = joined(
            coverage({ visible_share: 0.5, complete: false }),
            { calibrated: false },
        );
        assert.ok(!text.includes('framed'), text);
        assert.ok(!text.includes('goalmouth'), text);
    });

    test('a report from before this was measured says nothing', () => {
        // Absent is not "the camera saw everything" and it is not "the camera
        // saw nothing" — it is nobody looked, and silence is the honest form.
        const text = joined({ pitch_coverage: null }, { calibrated: true });
        assert.ok(!text.includes('framed'), text);
        assert.ok(!text.includes('goalmouth'), text);
    });

    test('a camera that moved says when, because that is the actionable half', () => {
        // "The camera moved" is a fact; "from 2m 34s onwards" tells a coach
        // which half of the match to disbelieve and which tripod to check.
        const notes = report.cvQualityNotes(
            { camera: { moved: true, first_s: 154, checked: true } },
            { calibrated: true },
        );
        const moved = notes.filter((n) => n.includes('camera moved'));
        assert.equal(moved.length, 1);
        assert.match(moved[0], /2m 34s/);
        assert.match(moved[0], /wrong pitch/);
    });

    test('a still camera, or one nobody could check, says nothing', () => {
        const still = { camera: { moved: false, first_s: null, checked: true } };
        const unchecked = { camera: { moved: false, first_s: null, checked: false } };
        for (const q of [still, unchecked, {}]) {
            assert.ok(!report.cvQualityNotes(q, { calibrated: true })
                .some((n) => n.includes('camera moved')));
        }
    });

    test('without a calibration there are no metres to invalidate', () => {
        // Warning about a moved camera on a run that never claimed a distance
        // would be noise on the majority of clips.
        const notes = report.cvQualityNotes(
            { camera: { moved: true, first_s: 154, checked: true } },
            { calibrated: false },
        );
        assert.ok(!notes.some((n) => n.includes('camera moved')));
    });

    test('a run slower than the football says how late it was, not its ratio', () => {
        // The half-time whistle is the only deadline in this project. "1.4x
        // real time" does not tell a coach they waited eighteen minutes.
        const notes = report.cvQualityNotes({ realtime_factor: 1.4 });
        const late = notes.filter((n) => n.includes('longer to work out'));
        assert.equal(late.length, 1);
        assert.match(late[0], /18m/);
    });

    test('a run that kept up says nothing about it', () => {
        // A batch report produced the next morning is not improved by being
        // told it could have been live.
        const notes = report.cvQualityNotes({ realtime_factor: 0.4 });
        assert.ok(!notes.some((n) => n.includes('longer to work out')));
    });

    test('an unmeasured run is not a slow one', () => {
        assert.ok(!report.cvQualityNotes({}).some((n) => n.includes('longer to work out')));
        assert.ok(!report.cvQualityNotes({ realtime_factor: null })
            .some((n) => n.includes('longer to work out')));
    });

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

    test('recording what a shot did is not a verdict on whether it was one', () => {
        // The same map holds both answers. Reading a bare `result` as a
        // confirmation would let the xG log inflate this scorecard from the
        // other end of the page, without anyone having checked anything.
        const s = score(events('shot'), { e0: { result: 'saved' } });
        assert.equal(s.byType.shot.precision, null);
        assert.equal(s.byType.shot.unreviewed, 1);
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

    test('only events a human touched are labelled', () => {
        // Treating the rest as negatives would train a detector on the
        // pipeline's own unchecked guesses.
        const out = labels({ a: { status: 'confirmed' } });
        assert.deepEqual(out.labelled.map((l) => l.id), ['a']);
    });

    test('a header tag is a label of its own', () => {
        // The only field in the file the pipeline cannot produce at all — one
        // fixed camera never sees the ball's height — and the one a pose model
        // would have to be trained on.
        const out = labels({ c: { header: true } });
        assert.deepEqual(out.labelled.map((l) => l.id), ['c']);
        assert.equal(out.labelled[0].header, true);
    });

    test('a shot marked with no verdict is still a label', () => {
        // "That one was saved" is a human statement about that moment, and the
        // only ground truth in the file a finishing model could learn from.
        const out = labels({ c: { result: 'saved' } });
        assert.deepEqual(out.labelled.map((l) => l.id), ['c']);
        assert.equal(out.labelled[0].result, 'saved');
        assert.equal(out.labelled[0].verdict, null);
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

// -------------------------------------------------------- the passing network
//
// The first thing in the app that draws a shape rather than a total, so the
// rules about what it refuses to draw carry more weight than the arithmetic. A
// network built from two thirds of the passes looks exactly like one built from
// all of them.

describe('playersByTrack', () => {
    const clusters = [
        { cluster_id: 0, track_ids: [1, 2] },
        { cluster_id: 1, track_ids: [3] },
        { cluster_id: 2, track_ids: [4] },
    ];

    test('every track of a named cluster resolves to that player', () => {
        const byTrack = passing.playersByTrack(clusters, { 0: 'ada', 1: 'dee' });
        assert.equal(byTrack.get(1), 'ada');
        assert.equal(byTrack.get(2), 'ada');
        assert.equal(byTrack.get(3), 'dee');
    });

    test('an unnamed cluster resolves to nobody', () => {
        const byTrack = passing.playersByTrack(clusters, { 0: 'ada' });
        assert.equal(byTrack.has(4), false);
    });

    test('a cluster ruled out as not a player is not a player', () => {
        const byTrack = passing.playersByTrack(
            clusters, { 0: 'ada', 2: '__not_a_player' }, '__not_a_player',
        );
        assert.equal(byTrack.has(4), false);
    });
});

describe('passingNetwork', () => {
    const byTrack = new Map([[1, 'ada'], [2, 'dee'], [3, 'noor']]);
    const pass = (trackId, receiverTrackId, startM, outcome = 'completed') => ({
        type: 'pass', team: 'team_a', trackId, receiverTrackId, startM, outcome,
    });
    const build = (events, options) =>
        passing.passingNetwork(events, { byTrack, team: 'team_a', ...options });

    test('a node sits at the mean of where that player passed from', () => {
        // Not their heatmap centroid. The edges are passes, so the positions
        // have to answer the same question the edges ask — a diagram that mixes
        // "where they stood" with "who they passed to" gives a reader no way to
        // tell the two apart.
        const net = build([
            pass(1, 2, [20, 30]), pass(1, 2, [40, 50]),
        ]);
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.x, 30);
        assert.equal(ada.y, 40);
    });

    test('an incomplete pass counts for the passer and joins no line', () => {
        const net = build([pass(1, 2, [20, 30], 'incomplete')]);
        assert.equal(net.nodes.find((n) => n.playerId === 'ada').passes, 1);
        assert.equal(net.nodes.find((n) => n.playerId === 'ada').completed, 0);
        assert.equal(net.edges.length, 0);
        assert.equal(net.incomplete, 1);
    });

    test('a pass from a figure nobody has named is counted, never guessed', () => {
        // A line drawn to an unnamed figure looks exactly like a fact.
        const net = build([pass(99, 2, [20, 30])]);
        assert.equal(net.nodes.length, 0);
        assert.equal(net.unmapped, 1);
        assert.equal(net.edges.length, 0);
    });

    test('a completed pass to an unnamed figure still counts as completed', () => {
        // It did find a team-mate. It just cannot be drawn to one, which is a
        // different fact from the pass having failed.
        const net = build([pass(1, 99, [20, 30])]);
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.completed, 1);
        assert.equal(net.incomplete, 0);
        assert.equal(net.edges.length, 0);
    });

    test('an uncalibrated run gives a player counts but nowhere to stand', () => {
        const net = build([pass(1, 2, null), pass(1, 2, undefined)]);
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.passes, 2);
        assert.equal(ada.x, null);
        assert.equal(net.unplaced, 2);
    });

    test('a pass to yourself is not a connection', () => {
        // Two fragments of one player, both mapped to the same name. The
        // tracker lost them mid-carry; the ball never changed hands.
        const two = new Map([[1, 'ada'], [5, 'ada']]);
        const net = build([pass(1, 5, [20, 30])], { byTrack: two });
        assert.equal(net.edges.length, 0);
    });

    test('the other team is left out when a team is named', () => {
        const theirs = { ...pass(1, 2, [20, 30]), team: 'team_b' };
        assert.equal(build([theirs]).nodes.length, 0);
    });

    test('only passes count — a shot is not a link', () => {
        const shot = { ...pass(1, 2, [20, 30]), type: 'shot' };
        assert.equal(build([shot]).nodes.length, 0);
    });

    test('attacking left mirrors the whole picture', () => {
        // The same flip the shot maps do. A second-half network drawn at the
        // wrong end is a diagram nobody can compare to the first half.
        const net = build([pass(1, 2, [20, 30])], { attackingEnd: 'left' });
        const ada = net.nodes.find((n) => n.playerId === 'ada');
        assert.equal(ada.x, 105 - 20);
        assert.equal(ada.y, 68 - 30);
    });

    test('edges are directed and counted', () => {
        const net = build([
            pass(1, 2, [20, 30]), pass(1, 2, [20, 30]), pass(2, 1, [40, 30]),
        ]);
        const there = net.edges.find((e) => e.from === 'ada' && e.to === 'dee');
        const back = net.edges.find((e) => e.from === 'dee' && e.to === 'ada');
        assert.equal(there.count, 2);
        assert.equal(back.count, 1);
    });

    test('busiest player first, so a renderer drops the quietest', () => {
        const net = build([
            pass(1, 2, [20, 30]), pass(1, 2, [20, 30]), pass(3, 1, [50, 30]),
        ]);
        assert.equal(net.nodes[0].playerId, 'ada');
    });
});

describe('foldEdges', () => {
    test('two directions become one line that remembers the split', () => {
        // "14 passes" between two players says nothing about whether one was
        // feeding the other or they were sharing it, and that difference is
        // most of what a coach reads this for.
        const folded = passing.foldEdges([
            { from: 'ada', to: 'dee', count: 9 },
            { from: 'dee', to: 'ada', count: 5 },
        ]);
        assert.equal(folded.length, 1);
        assert.equal(folded[0].count, 14);
        assert.equal(folded[0].aToB, 9);
        assert.equal(folded[0].bToA, 5);
    });

    test('a one-way link keeps its direction', () => {
        const folded = passing.foldEdges([{ from: 'dee', to: 'ada', count: 6 }]);
        assert.equal(folded[0].a, 'ada');
        assert.equal(folded[0].bToA, 6);
        assert.equal(folded[0].aToB, 0);
    });

    test('busiest pair first', () => {
        const folded = passing.foldEdges([
            { from: 'a', to: 'b', count: 2 },
            { from: 'c', to: 'd', count: 9 },
        ]);
        assert.equal(folded[0].count, 9);
    });
});

describe('strongestLink', () => {
    const name = (id) => ({ ada: 'Ada', dee: 'Dee' }[id] || id);

    test('a real link is named, both ways', () => {
        const text = passing.strongestLink(
            [{ a: 'ada', b: 'dee', count: 14, aToB: 9, bToA: 5 }], name,
        );
        assert.match(text, /Ada and Dee/);
        assert.match(text, /14 passes, 9 one way and 5 back/);
    });

    test('a thin link is not a finding', () => {
        // "Your main link was Ada and Dee, with two passes" is a sentence about
        // nothing that reads exactly like a sentence about something.
        assert.equal(passing.strongestLink(
            [{ a: 'ada', b: 'dee', count: 2, aToB: 1, bToA: 1 }], name,
        ), null);
        assert.equal(passing.strongestLink([], name), null);
    });
});

describe('networkNote', () => {
    const note = (network) => passing.networkNote(network);

    test('it says how many players are actually drawn', () => {
        const text = note({ nodes: [{ x: 1, y: 1 }, { x: 2, y: 2 }, { x: null }] });
        assert.match(text, /2 players placed/);
    });

    test('unnamed passers are reported, with what to do about it', () => {
        const text = note({ nodes: [], unmapped: 40 });
        assert.match(text, /40 passes came from a tracked figure nobody has named/);
        assert.match(text, /Who the video tracked/);
    });

    test('an uncalibrated run says so rather than blaming the mapping', () => {
        // Two different absences with two different fixes: one wants a coach at
        // the picker, the other wants a calibration.
        const text = note({ nodes: [], unplaced: 12 });
        assert.match(text, /no pitch calibration/);
        assert.doesNotMatch(text, /nobody has named/);
    });

    test('a trimmed event list is disclosed', () => {
        const text = passing.networkNote({ nodes: [] }, { truncated: true });
        assert.match(text, /trimmed to the most confident/);
    });
});

describe('the passing network draws what it claims to draw', () => {
    test('four times the passes is four times the circle', () => {
        // The obvious `MIN + (MAX - MIN) * sqrt(share)` looks identical and is
        // proportional to nothing: on the sample squad a player with three
        // times another's passes came out 1.85 times the area, so the diagram
        // quietly flattened the difference it exists to show.
        const big = passMod.nodeRadius(80, 80);
        const quarter = passMod.nodeRadius(20, 80);
        assert.ok(Math.abs((big * big) / (quarter * quarter) - 4) < 1e-9);
    });

    test('a barely-involved player is clamped, not vanished', () => {
        // Below the floor every dot draws the same size. A dot too small to
        // see is a player the diagram has deleted, and "barely involved" is
        // said in the note underneath instead.
        assert.equal(passMod.nodeRadius(1, 200), passMod.nodeRadius(2, 200));
        assert.ok(passMod.nodeRadius(1, 200) > 1);
    });

    test('twice as thick is twice as many passes', () => {
        assert.ok(Math.abs(
            passMod.edgeWidth(20, 20) / passMod.edgeWidth(10, 20) - 2,
        ) < 1e-9);
    });

    test('a pair that exchanged one pass is not a connection', () => {
        // A hairline between every pair of names turns the diagram into a mesh
        // where nothing stands out.
        assert.ok(passMod.MIN_EDGE >= 2);
    });
});

describe('the sample passing network', () => {
    const built = () => {
        const { byTrack } = sample.samplePassMapping();
        return passing.passingNetwork(sample.samplePassEvents(), {
            byTrack, team: 'team_a',
        });
    };

    test('every player in the shape is placed', () => {
        const net = built();
        assert.equal(net.nodes.length, 11);
        assert.ok(net.nodes.every((n) => n.x != null && n.y != null));
    });

    test('the invented counts match the invented passes', () => {
        // A preview whose figures contradict each other teaches whoever reads
        // it to stop checking whether figures agree.
        const net = built();
        for (const node of net.nodes) {
            assert.ok(node.completed <= node.passes, node.playerId);
        }
    });

    test('it previews the caveats, not just the picture', () => {
        // Two unnamed passers and some passes that went nowhere, on purpose:
        // the note under the diagram is most of what it is for.
        const net = built();
        assert.ok(net.unmapped > 0, 'unnamed passers');
        assert.ok(net.incomplete > 0, 'passes that found nobody');
        assert.equal(net.unplaced, 0, 'the sample run is calibrated');
    });

    test('the left side is the busiest pair, which is the point of it', () => {
        const net = built();
        const top = passing.foldEdges(net.edges)[0];
        assert.deepEqual([top.a, top.b].sort(), ['lb', 'lcm']);
    });

    test('every node sits inside the pitch', () => {
        for (const node of built().nodes) {
            assert.ok(node.x >= 0 && node.x <= 105, `${node.playerId} x`);
            assert.ok(node.y >= 0 && node.y <= 68, `${node.playerId} y`);
        }
    });
});

// ------------------------------------------ marking the model's predictions
//
// The arithmetic that decides whether an xG number survives contact with a real
// pitch. Two things are worth guarding here and they pull in opposite
// directions: the tally must count the right shots, and the verdict must refuse
// to say anything the sample cannot support. The second is the one a future
// change will quietly break, because "12 shots, predicted 1.2, scored 3" looks
// like a finding right up until you work out the band.

describe('shotLedger', () => {
    const events = [
        { id: 's2', type: 'shot', timestampS: 200, xg: 0.4, team: 'team_a', outcome: 'goal' },
        { id: 'p1', type: 'pass', timestampS: 150 },
        { id: 's1', type: 'shot', timestampS: 100, xg: 0.1, team: 'team_b' },
    ];
    const ledger = (byEvent = {}) => report.shotLedger(events, { byEvent });

    test('only shots, in the order they were taken', () => {
        assert.deepEqual(ledger().map((r) => r.id), ['s1', 's2']);
    });

    test('an unmarked shot still counts — it just has no result yet', () => {
        const [first] = ledger();
        assert.equal(first.result, null);
        assert.equal(first.counted, true);
    });

    test('the pipeline\'s own reading travels, but never as an answer', () => {
        // Shown beside the buttons so a coach can sanity-check it. If this ever
        // became the default `result`, the check would be grading the xG model
        // against a ball detector, which is two guesses agreeing.
        const row = ledger().find((r) => r.id === 's2');
        assert.equal(row.guessed, 'goal');
        assert.equal(row.result, null);
    });

    test('a rejected shot stays in the list and stops counting', () => {
        const [, second] = ledger({ s2: { status: 'rejected', result: 'goal' } });
        assert.equal(second.counted, false);
        // Kept, so undoing a mis-tap does not mean marking it again.
        assert.equal(second.result, 'goal');
    });

    test('a shot edited into something else stops being a shot', () => {
        const [, second] = ledger({ s2: { status: 'edited', type: 'pass' } });
        assert.equal(second.counted, false);
    });

    test('an edit that only reassigns the player leaves it a shot', () => {
        const [, second] = ledger({ s2: { status: 'edited', type: 'shot', playerId: 'p' } });
        assert.equal(second.counted, true);
    });

    test('a result nobody offered is not a result', () => {
        // The buttons are the vocabulary. Anything else reached the document
        // some other way and must not reach the tally.
        const [, second] = ledger({ s2: { result: 'nutmeg' } });
        assert.equal(second.result, null);
    });
});

describe('tagging a shot as a header', () => {
    // 0.72 off the foot, 0.43 off the head — the real model's own numbers for a
    // six-yard chance. The gap is why this is a control and not a footnote.
    const events = [
        { id: 's1', type: 'shot', timestampS: 100, xg: 0.72, xgHeader: 0.43 },
        { id: 's2', type: 'shot', timestampS: 200, xg: 0.10, xgHeader: 0.08 },
    ];
    const ledger = (byEvent = {}) => report.shotLedger(events, { byEvent });

    test('an untagged shot is the foot reading', () => {
        const [first] = ledger();
        assert.equal(first.xg, 0.72);
        assert.equal(first.header, false);
    });

    test('a tagged one is the header reading, and both stay visible', () => {
        const [first] = ledger({ s1: { header: true } });
        assert.equal(first.xg, 0.43);
        assert.equal(first.xgFoot, 0.72);
        assert.equal(first.xgHeader, 0.43);
    });

    test('a header on a run with no header reading has no xG at all', () => {
        // Falling back to the foot figure would score it as exactly the thing
        // the coach just said it was not, which is the error being corrected.
        // Null takes it out of the check instead.
        const rows = report.shotLedger(
            [{ id: 'old', type: 'shot', timestampS: 1, xg: 0.3 }],
            { byEvent: { old: { header: true, result: 'goal' } } },
        );
        assert.equal(rows[0].xg, null);
        assert.equal(report.xgTally(rows), null);
    });

    test('the tally uses the corrected number', () => {
        const rows = ledger({
            s1: { header: true, result: 'goal' }, s2: { result: 'saved' },
        });
        const tally = report.xgTally(rows);
        assert.equal(tally.predicted, 0.53);
        assert.equal(tally.variance, report.xgTally([
            { xg: 0.43, result: 'goal', counted: true },
            { xg: 0.10, result: 'saved', counted: true },
        ]).variance);
    });
});

describe('headerCorrection', () => {
    const rows = (...specs) => specs.map(([xgFoot, xgHeader, header], i) => ({
        id: `s${i}`, xgFoot, xgHeader, header, counted: true,
        xg: header ? xgHeader : xgFoot,
    }));

    test('no tags means nothing to say, not a correction of zero', () => {
        assert.equal(report.headerCorrection(rows([0.72, 0.43, false])), null);
    });

    test('it reports the movement, not just the new total', () => {
        // A total that silently drops between two visits looks like a bug. The
        // same drop with "two headers" beside it is the tool working.
        const out = report.headerCorrection(
            rows([0.72, 0.43, true], [0.10, 0.08, true], [0.30, 0.20, false]),
        );
        assert.equal(out.headers, 2);
        assert.equal(out.from, 0.82);
        assert.equal(out.to, 0.51);
    });

    test('a header with no header reading is counted and named', () => {
        const out = report.headerCorrection(rows([0.30, null, true]));
        assert.equal(out.headers, 1);
        assert.equal(out.unscorable, 1);
        // It left the total rather than staying in it wrong, so neither side of
        // the movement includes it.
        assert.equal(out.from, 0);
        assert.equal(out.to, 0);
    });

    test('a rejected shot is not corrected', () => {
        const out = report.headerCorrection([{
            id: 'x', xgFoot: 0.72, xgHeader: 0.43, header: true,
            xg: 0.43, counted: false,
        }]);
        assert.equal(out, null);
    });
});

describe('the defensive line says what it averaged over', () => {
    const notes = (options) => report.cvQualityNotes({}, options);
    const found = (options) => notes(options)
        .find((n) => n.includes('out of possession'));

    test('it names the thing the figure is not', () => {
        // "Defensive line height" conventionally means where the back line sat
        // while defending. This is every instant of the run, including the
        // spells this side spent camped in the other half with the ball, which
        // pull it up.
        assert.match(found({ lineHeight: 28.4 }), /whole run/);
    });

    test('and stays quiet on the runs that have no line height', () => {
        // Which is most of them: the figure needs a calibration and somebody
        // to have said which goal each side defends. A caveat about a row that
        // is not on screen gets attached to whatever is.
        assert.equal(found({}), undefined);
        assert.equal(found({ lineHeight: null }), undefined);
        assert.equal(found({ shots: 4 }), undefined);
    });

    test('a line on the goal line is still a line', () => {
        // Absent is not zero, and 0.0m is a real reading — a side defending on
        // its own goal line for the whole run. Gating on truthiness rather
        // than on null would drop the caveat exactly where it matters most.
        assert.ok(found({ lineHeight: 0 }));
    });
});

describe('the foot-shot caveat knows when it has been answered', () => {
    const notes = (options) => report.cvQualityNotes({}, { shots: 4, ...options });

    test('untagged, it names the assumption and that it can be settled', () => {
        const line = notes({}).find((n) => n.includes('struck with the foot'));
        assert.match(line, /until somebody says otherwise/);
    });

    test('tagged, it says how many were corrected', () => {
        // Otherwise a coach who has just tagged three headers is told, on the
        // same screen, that nothing was tagged.
        const line = notes({ headersTagged: 3 })
            .find((n) => n.includes('struck with the foot'));
        assert.match(line, /except the 3 you tagged as headers/);
        assert.doesNotMatch(line, /until somebody/);
    });

    test('one is a header, not headers', () => {
        const line = notes({ headersTagged: 1 })
            .find((n) => n.includes('struck with the foot'));
        assert.match(line, /except the 1 you tagged as a header/);
    });
});

describe('headerNote', () => {
    const note = (headers, unscorable, from, to) =>
        report.headerNote({ headers, unscorable, from, to });

    test('no tags, nothing to say', () => {
        assert.equal(report.headerNote(null), null);
        assert.equal(note(0, 0, 0, 0), null);
    });

    test('a scorable tag reports the movement', () => {
        assert.match(note(2, 0, 0.82, 0.51), /0\.82 xG down to 0\.51/);
    });

    test('an all-unscorable match does not claim a 0.00 to 0.00 correction', () => {
        // What this exists to stop. Running the two facts into one sentence
        // produced "which took 0.00 xG down to 0.00" — arithmetically true and
        // the opposite of what happened.
        const text = note(1, 1, 0, 0);
        assert.doesNotMatch(text, /0\.00/);
        assert.match(text, /left out of these totals/);
        assert.match(text, /rather than counted as foot shots/);
    });

    test('a mixed match says both things, and which is which', () => {
        const text = note(3, 1, 0.82, 0.51);
        assert.match(text, /2 shots you tagged as a header are scored as one/);
        assert.match(text, /1 more came from a run/);
    });
});

describe('correctedShotMarks', () => {
    const marks = [
        { event_id: 's1', x_m: 99, y_m: 34, xg: 0.72 },
        { event_id: 's2', x_m: 80, y_m: 40, xg: 0.10 },
    ];

    test('a report nobody has tagged renders exactly as it did', () => {
        assert.equal(report.correctedShotMarks(marks, []), marks);
    });

    test('a tagged shot is redrawn at its header value', () => {
        const out = report.correctedShotMarks(marks, [
            { id: 's1', header: true, xg: 0.43, counted: true },
        ]);
        assert.equal(out[0].xg, 0.43);
        assert.equal(out[0].is_header, true);
        // Untouched, and still the same object — nothing else on the map moves.
        assert.equal(out[1], marks[1]);
    });

    test('marks from before event ids existed are left alone', () => {
        // The join is on the id, never the timestamp: two shots inside one
        // second would swap their corrections and nothing would look wrong.
        const old = [{ video_s: 100, xg: 0.72 }];
        const out = report.correctedShotMarks(old, [
            { id: 's1', header: true, xg: 0.43, counted: true },
        ]);
        assert.equal(out[0].xg, 0.72);
    });

    test('a rejected shot leaves the map rather than being corrected on it', () => {
        // It used to keep its mark and its original number, which was right
        // about the number and wrong about the mark: a dot on a shot map is a
        // claim that a shot happened there, and the coach has just said it did
        // not. Tagging it a header changes nothing either — a rejected shot is
        // not a header, it is not a shot.
        const rows = [
            { id: 's2', header: true, xg: 0.08, xgFoot: 0.10, xgHeader: 0.08, counted: false },
        ];
        const out = report.correctedShotMarks(marks, rows);
        assert.ok(!out.some((m) => m.event_id === 's2'));
        assert.ok(out.length < marks.length);
        assert.equal(report.headerCorrection(rows), null);
    });

    test('a header the run cannot score leaves the total instead of sitting in it', () => {
        // The bug this replaced: the mark kept its foot xG, so the caption
        // counted a shot the sentence under it said had been dropped and the
        // check below it agreed had been dropped. Null is what makes all three
        // agree — shotSummary skips it and markRadius draws it at the floor, so
        // the dot stays on the pitch without contributing a number nobody
        // believes.
        const rows = [
            { id: 's1', header: true, xg: null, xgFoot: 0.72, xgHeader: null, counted: true },
        ];
        const out = report.correctedShotMarks(marks, rows);
        assert.equal(out[0].xg, null);
        assert.equal(markMod.shotSummary(out).xg, 0.10);
        assert.equal(report.headerCorrection(rows).from, 0);
    });
});

describe('xgTally', () => {
    const row = (xg, result, counted = true) => ({ xg, result, counted });

    test('nothing marked is null, not a row of zeroes', () => {
        assert.equal(report.xgTally([row(0.3, null)]), null);
        assert.equal(report.xgTally([]), null);
    });

    test('predicted, scored and variance over the marked shots', () => {
        const tally = report.xgTally([row(0.5, 'goal'), row(0.2, 'saved')]);
        assert.deepEqual(tally, {
            shots: 2, predicted: 0.7, scored: 1, variance: 0.41,
        });
    });

    test('only a goal is a goal', () => {
        const tally = report.xgTally(
            ['saved', 'blocked', 'off_target', 'woodwork'].map((r) => row(0.2, r)),
        );
        assert.equal(tally.scored, 0);
        assert.equal(tally.shots, 4);
    });

    test('a shot with no xG is skipped on both sides at once', () => {
        // Counting its goal without its prediction would credit the team with a
        // goal the model was never asked about — the one mistake here that
        // makes the answer wrong in a direction nobody would question.
        const tally = report.xgTally([row(null, 'goal'), row(0.5, 'goal')]);
        assert.equal(tally.shots, 1);
        assert.equal(tally.scored, 1);
        assert.equal(tally.predicted, 0.5);
    });

    test('a shot that is not counted contributes nothing', () => {
        assert.equal(report.xgTally([row(0.5, 'goal', false)]), null);
    });
});

describe('sumXgTallies', () => {
    test('variances add, which is the whole reason they are stored', () => {
        // Storing a standard deviation instead would make the season figure
        // wrong in the reassuring direction: sqrt(a)+sqrt(b) > sqrt(a+b), so
        // the band would come out too wide and every real miscalibration would
        // read as "cannot tell".
        const out = report.sumXgTallies([
            { shots: 2, predicted: 0.7, scored: 1, variance: 0.41 },
            { shots: 3, predicted: 0.6, scored: 0, variance: 0.48 },
        ]);
        assert.deepEqual(out, { shots: 5, predicted: 1.3, scored: 1, variance: 0.89 });
    });

    test('a season nobody has marked is null', () => {
        assert.equal(report.sumXgTallies([null, undefined, { shots: 0 }]), null);
    });
});

describe('xgCalibration', () => {
    // Twelve identical half-chances: predicted 6, sd 1.73, band 3.46.
    const evens = (shots, scored) => ({
        shots, scored, predicted: shots * 0.5, variance: shots * 0.25,
    });

    test('nothing marked says nothing, in nulls rather than zeroes', () => {
        const cal = report.xgCalibration(null);
        assert.equal(cal.shots, 0);
        assert.equal(cal.predicted, null);
        assert.equal(cal.verdict, null);
    });

    test('the band is two standard deviations of the goals themselves', () => {
        const cal = report.xgCalibration(evens(12, 6));
        assert.equal(Math.round(cal.sd * 1000), 1732);
        assert.equal(Math.round(cal.band * 100), 346);
    });

    test('a small sample that found no gap is inconclusive, not agreement', () => {
        // Six shots cannot tell a good model from one half out, so "the model
        // agrees" would be the most flattering possible reading of no data.
        const cal = report.xgCalibration(evens(6, 3));
        assert.equal(cal.verdict, 'inconclusive');
    });

    test('a big enough sample that found no gap does mean agreement', () => {
        // 150 shots at a typical 0.1 each: predicted 15, band 7.3 against a 7.5
        // threshold. That is roughly a season of both teams' shots, and it is
        // the honest answer to "how long before this says anything" — the
        // resolving power grows with the square root, so there is no shortcut.
        const cal = report.xgCalibration({
            shots: 150, scored: 15, predicted: 15, variance: 13.5,
        });
        assert.equal(cal.verdict, 'consistent');
    });

    test('the threshold is a share of the prediction, not a number of goals', () => {
        // The easy inversion. A goal-denominated threshold would call these six
        // shots conclusive — their band is a narrow 1.2 goals — and a whole
        // season inconclusive, because the band grows with √n while the
        // prediction grows with n.
        const tiny = report.xgCalibration({
            shots: 6, scored: 1, predicted: 0.6, variance: 0.54,
        });
        assert.ok(tiny.band < 1.5, 'a small sample has a narrow absolute band');
        assert.equal(tiny.verdict, 'inconclusive');
    });

    test('more goals than predicted, past the band, means the model is low', () => {
        const cal = report.xgCalibration(evens(12, 11));
        assert.equal(cal.verdict, 'model_low');
        assert.equal(cal.gap, 5);
    });

    test('two long shots and one lucky finish accuses nobody', () => {
        // The band is a normal approximation to a sum of coin flips, and on a
        // tenth of an expected goal it is meaningless — the real distribution is
        // almost all mass at zero. Left ungated this reads "the model is rating
        // these too low", off one shot going in, which is the exact over-reading
        // every other line here exists to prevent.
        const cal = report.xgCalibration({
            shots: 2, scored: 1, predicted: 0.111, variance: 0.101,
        });
        assert.ok(cal.gap > cal.band, 'the raw band would have called this');
        assert.equal(cal.verdict, 'inconclusive');
    });

    test('fewer goals than predicted, past the band, means the model is high', () => {
        assert.equal(report.xgCalibration(evens(12, 1)).verdict, 'model_high');
    });

    test('a gap wider than a wide band is still a finding', () => {
        // The order of the checks matters. A sample too small to resolve a 50%
        // error can still resolve a 100% one, and calling that "inconclusive"
        // would throw away the only unambiguous evidence this tool produces.
        const cal = report.xgCalibration(evens(8, 8));
        assert.ok(cal.band >= cal.predicted * 0.5, 'too small to resolve a half');
        assert.equal(cal.verdict, 'model_low');
    });
});

describe('calibrationNote', () => {
    const note = (tally, options) =>
        report.calibrationNote(report.xgCalibration(tally), options);

    test('nothing marked has nothing to say', () => {
        assert.equal(note(null), null);
    });

    test('the band is in the same breath as the difference', () => {
        // Not a footnote. "Predicted 6, scored 3" is the reading a coach takes
        // from two numbers side by side, and on twelve shots it means nothing.
        const text = note({ shots: 12, scored: 3, predicted: 6, variance: 3 });
        assert.match(text, /12 shots/);
        assert.match(text, /6\.00 xG/);
        assert.match(text, /±3\.5 goals/);
    });

    test('an inconclusive sample is told to keep going, not told it agrees', () => {
        const text = note({ shots: 6, scored: 3, predicted: 3, variance: 1.5 });
        assert.match(text, /Keep marking/);
        assert.doesNotMatch(text, /agree/);
    });

    test('a real gap says which way the model is wrong', () => {
        const text = note({ shots: 12, scored: 11, predicted: 6, variance: 3 });
        assert.match(text, /rating these chances too low/);
    });

    test('the season line says it is a season', () => {
        const text = note(
            { shots: 150, scored: 15, predicted: 15, variance: 13.5 },
            { over: 'marked across the season' },
        );
        assert.match(text, /marked across the season/);
        assert.match(text, /the model and this pitch agree/);
    });
});

// ------------------------------------- what has actually reached the server
//
// The tablet tags at a field. `navigator.onLine` reports a link rather than a
// reachable server, so a school Wi-Fi with a captive portal reads as connected
// — and the old indicator said "Saved" on the strength of it.

describe('syncState', () => {
    test('connected with nothing outstanding is the quiet case', () => {
        const s = report.syncState({ pending: 0, fromCache: false, online: true });
        assert.equal(s.tone, 'ok');
        assert.equal(s.label, 'Saved');
    });

    test('outstanding taps are counted on the chip, not hidden in a tooltip', () => {
        // A tooltip on a tablet is a tooltip nobody can open.
        const s = report.syncState({ pending: 3, fromCache: true, online: false });
        assert.equal(s.tone, 'waiting');
        assert.equal(s.label, '3 waiting');
        assert.match(s.detail, /saved on this tablet/);
    });

    test('one waiting tap is written as one thing, not "1 taps are"', () => {
        const s = report.syncState({ pending: 1, fromCache: true });
        assert.match(s.detail, /1 tap is saved/);
        assert.match(s.detail, /has not reached/);
        // The sentence keeps agreeing with itself all the way to the end; the
        // first version switched to "they" halfway through.
        assert.match(s.detail, /until it does/);
        assert.match(s.detail, /it uploads on its own/);
    });

    test('counting beats connectedness — waiting wins even when online', () => {
        // A write can be outstanding on a perfectly good connection, and that
        // is the moment somebody must not close the tab.
        const s = report.syncState({ pending: 2, fromCache: false, online: true });
        assert.equal(s.tone, 'waiting');
    });

    test('disconnected with nothing outstanding is reassuring, and true', () => {
        // An acknowledged write is on the server by definition, so this is not
        // a hedge — it is the strongest thing that can honestly be said.
        const s = report.syncState({ pending: 0, fromCache: true, online: true });
        assert.equal(s.tone, 'stale');
        assert.match(s.detail, /Everything tapped so far is on the server/);
    });

    test('either signal saying disconnected is enough', () => {
        // Claiming a connection that is not there is the error that loses a
        // match; claiming none while one exists costs twenty seconds.
        assert.equal(report.syncState({ fromCache: true, online: true }).tone, 'stale');
        assert.equal(report.syncState({ fromCache: false, online: false }).tone, 'stale');
    });

    test('before the listener reports, it says so rather than reassuring', () => {
        // "Everything tapped so far is on the server" is the one sentence here
        // that must never be said on no evidence.
        const s = report.syncState({ pending: null, fromCache: true });
        assert.equal(s.label, 'Checking');
        assert.doesNotMatch(s.detail, /is on the server/);
    });

    test('an empty call assumes a healthy connection and nothing queued', () => {
        assert.equal(report.syncState().tone, 'ok');
        assert.equal(report.syncState({}).tone, 'ok');
    });
});

describe('safeToClose', () => {
    test('only an empty queue is safe', () => {
        assert.equal(report.safeToClose({ pending: 0 }), true);
        assert.equal(report.safeToClose({ pending: 1 }), false);
    });

    test('a connection nobody has is not what makes it unsafe', () => {
        // Offline with everything acknowledged: walking away loses nothing.
        assert.equal(report.safeToClose({ pending: 0, fromCache: true }), true);
    });

    test('an unknown state is not safe', () => {
        // Saying "yes, go ahead" on no information is the same failure the old
        // navigator.onLine indicator made, in the place it costs a match.
        assert.equal(report.safeToClose(null), false);
        assert.equal(report.safeToClose({}), false);
        assert.equal(report.safeToClose({ pending: null }), false);
    });
});

// ------------------------------------------------------------ which half
//
// The period decides which goal each side attacked, and every pitch drawing on
// these pages is built from that. A wrong answer mirrors all of them and
// changes how none of them look, so the only defence is saying when nobody
// actually knows.

describe('periodNote', () => {
    test('a log-settled period says nothing', () => {
        // A correct answer arrived at correctly is not a caveat, and a note
        // that fires on every run takes the ones that matter down with it.
        assert.deepEqual(report.periodNote('second_half', 'log'), []);
    });

    test('a period somebody typed says nothing either', () => {
        assert.deepEqual(report.periodNote('second_half', 'flag'), []);
    });

    test('a defaulted period names the assumption and its consequence', () => {
        const [line] = report.periodNote('first_half', 'default');
        assert.match(line, /nothing said which half/);
        assert.match(line, /the first half/);
        assert.match(line, /mirrored/);
    });

    test('an old report with no period field is left alone', () => {
        // Nothing published before this existed recorded the decision, and
        // caveating every one of them retroactively is noise, not honesty.
        assert.deepEqual(report.periodNote(undefined, undefined), []);
    });

    test('it reaches the quality note the coach actually reads', () => {
        const notes = report.cvQualityNotes({}, {
            calibrated: true, period: 'first_half', periodSource: 'default',
        });
        assert.ok(notes.some((n) => n.includes('nothing said which half')));

        const settled = report.cvQualityNotes({}, {
            calibrated: true, period: 'first_half', periodSource: 'log',
        });
        assert.ok(!settled.some((n) => n.includes('which half')));
    });
});

// ------------------------------------------------------- a season, as a line
//
// The measures a season chart can carry, and the three ways a season chart
// lies: by averaging rates instead of pooling them, by closing the gaps where
// nothing was measured, and by drawing a number that partial coverage bends in
// a direction beside three it only makes noisier.

describe('seasonForm', () => {
    // newest first, the order playerSeason returns
    const match = (opponent, extra = {}) => ({ opponentName: opponent, ...extra });
    const filmed = (opponent, minutes, distance) =>
        match(opponent, { cvMinutesTracked: minutes, cvDistanceM: distance });

    test('an unknown measure is null, not an empty chart', () => {
        assert.equal(season.seasonForm([], 'nonsense'), null);
    });

    test('the season figure is pooled, not the mean of the match rates', () => {
        // A full match at 100 and a ten-minute fragment at 40. The mean of the
        // two rates is 70; the player's actual season is 5400m over 60 minutes,
        // which is 90. The fragment gets a sixth of the say, not half.
        const form = season.seasonForm([
            filmed('recent', 10, 400), filmed('older', 50, 5000),
        ], 'distancePerMin');
        assert.equal(form.pooled, 90);
        assert.notEqual(form.pooled, 70);
    });

    test('oldest match is first, whatever order the database used', () => {
        const form = season.seasonForm([
            filmed('last', 50, 5000), filmed('first', 50, 4000),
        ], 'distancePerMin');
        assert.deepEqual(form.points.map((p) => p.opponent), ['first', 'last']);
    });

    test('a match nobody filmed keeps its slot on the axis', () => {
        // Closing the gap would space three filmed matches evenly and imply
        // they happened evenly across the season, which nobody measured.
        const form = season.seasonForm([
            filmed('c', 50, 5000), match('b'), filmed('a', 50, 4500),
        ], 'distancePerMin');
        assert.equal(form.points.length, 3);
        assert.equal(form.points[1].value, null);
        assert.equal(form.points[1].filmed, false);
        assert.equal(form.unfilmed, 1);
    });

    test('a match tracked for too few minutes is counted, not plotted', () => {
        const form = season.seasonForm([
            filmed('b', 4, 380), filmed('a', 50, 4500),
        ], 'distancePerMin');
        assert.equal(form.thin, 1);
        assert.equal(form.measured, 1);
        assert.equal(form.points[1].value, null);
        assert.equal(form.points[1].thin, true);
    });

    test('the thin match is out of the season figure as well as off the chart', () => {
        // Otherwise the line sits somewhere the dots around it cannot explain.
        const withThin = season.seasonForm([
            filmed('b', 4, 40), filmed('a', 50, 5000),
        ], 'distancePerMin');
        const without = season.seasonForm([filmed('a', 50, 5000)], 'distancePerMin');
        assert.equal(withThin.pooled, without.pooled);
    });

    test('pass accuracy is floored on attempts, not on minutes', () => {
        // Both halves of a ratio come from the same span, so the minutes are
        // not what makes it thin — the number of passes is.
        const form = season.seasonForm([{
            opponentName: 'a', cvMinutesTracked: 80,
            cvPassesAttempted: 4, cvPassesCompleted: 3,
        }], 'passAccuracy');
        assert.equal(form.thin, 1);
        assert.equal(form.measured, 0);
    });

    test('top speed pools as a maximum and is marked as bent downward', () => {
        const form = season.seasonForm([
            { opponentName: 'b', cvMinutesTracked: 60, cvTopSpeedKmh: 26.0 },
            { opponentName: 'a', cvMinutesTracked: 60, cvTopSpeedKmh: 28.5 },
        ], 'topSpeed');
        assert.equal(form.pooled, 28.5);
        assert.equal(form.biased, 'low');
    });

    test('a season with nothing filmed produces no charts at all', () => {
        assert.deepEqual(season.seasonForms([match('a'), match('b')]), []);
    });
});

describe('formNote', () => {
    test('the counts are per match, not read off one measure', () => {
        // Pass accuracy is floored on attempts and the rest on minutes, so a
        // match can be thin in one and fine in another. A caption that assumed
        // they agreed would be right almost always, which is the worst
        // frequency for a wrong number to be wrong at.
        const reports = [{
            opponentName: 'a', cvMinutesTracked: 70, cvDistanceM: 6800,
            cvPassesAttempted: 3, cvPassesCompleted: 2,
        }];
        const note = season.formNote(season.seasonForms(reports));
        assert.match(note, /1 of 1 matches/);
        assert.doesNotMatch(note, /more (was|were) filmed/);
    });

    test('the downward bias on top speed is named once', () => {
        const reports = [
            { opponentName: 'b', cvMinutesTracked: 60, cvTopSpeedKmh: 26, cvDistanceM: 5800 },
            { opponentName: 'a', cvMinutesTracked: 60, cvTopSpeedKmh: 27, cvDistanceM: 5900 },
        ];
        const note = season.formNote(season.seasonForms(reports));
        assert.equal(note.match(/Top speed/g).length, 1);
        assert.match(note, /floor rather than a figure/);
    });
});

describe('the season chart geometry', () => {
    const form = (low, high) => ({ low, high });

    test('a flat season sits on the middle line rather than dividing by zero', () => {
        const scale = formMod.formScale(form(90, 90));
        assert.ok(isFinite(scale.y(90)));
        assert.ok(Math.abs(scale.y(90) - scale.y(90)) < 1e-9);
        assert.ok(scale.min < 90 && scale.max > 90);
    });

    test('the axis fits the data rather than starting at zero', () => {
        // A dot chart may do this and a bar chart may not: a dot's height is a
        // position, a bar's length is a quantity. The two ends are printed on
        // the card because of it.
        const scale = formMod.formScale(form(88, 100));
        assert.ok(scale.min > 0);
        assert.ok(scale.min < 88 && scale.max > 100);
    });

    test('dot area is proportional to the minutes behind the point', () => {
        const big = formMod.pointRadius(80, 80);
        const small = formMod.pointRadius(20, 80);
        // Four times the minutes, four times the area — not four times across.
        assert.ok(Math.abs((big / small) ** 2 - 4) < 0.01);
    });

    test('a point with no minutes still draws, at the floor', () => {
        assert.ok(formMod.pointRadius(0, 80) > 0);
    });

    test('one match sits in the middle instead of hard against the edge', () => {
        assert.equal(formMod.slotX(0, 1), 50);
    });
});

describe('the sample season', () => {
    test('it arrives newest first, the way the database returns it', () => {
        // A fixture in any other order would preview a season running backwards
        // and every assertion about it would still pass.
        const dates = sample.sampleSeason().map((r) => r.matchDate);
        assert.deepEqual(dates, [...dates].sort().reverse());
    });

    test('it carries the two gaps a real season is full of', () => {
        const form = season.seasonForm(sample.sampleSeason(), 'distancePerMin');
        assert.equal(form.unfilmed, 2);
        assert.equal(form.thin, 1);
        assert.equal(form.measured, 5);
    });

    test('the season figure is the pooled one, to the metre', () => {
        const form = season.seasonForm(sample.sampleSeason(), 'distancePerMin');
        const drawn = form.points.filter((p) => p.value != null);
        const metres = drawn.reduce((s, p) => s + p.of, 0);
        const minutes = drawn.reduce((s, p) => s + p.per, 0);
        assert.ok(Math.abs(form.pooled - metres / minutes) < 1e-9);
    });

    test('every measure has enough points to be worth a line', () => {
        for (const form of season.seasonForms(sample.sampleSeason())) {
            assert.ok(form.measured >= season.MIN_FORM_POINTS,
                `${form.key} has ${form.measured}`);
        }
    });
});

// ----------------------------------------------------- the press, over time

describe('pressingTrend', () => {
    const seg = (start, allowed, actions, ppda) => ({
        start_s: start, end_s: start + 900, allowed, actions, ppda,
    });

    test('no segments means no chart, not an empty one', () => {
        assert.equal(report.pressingTrend(null), null);
        assert.equal(report.pressingTrend([]), null);
    });

    test('bar length is measured from zero, not from the shortest block', () => {
        // A chart that starts at the minimum turns an ordinary quarter-hour
        // into a collapse, and the differences here are inside the noise more
        // often than not.
        const trend = report.pressingTrend([
            seg(0, 40, 10, 4), seg(900, 80, 10, 8),
        ]);
        assert.deepEqual(trend.blocks.map((b) => b.share), [0.5, 1]);
    });

    test('the two kinds of silence are told apart', () => {
        const trend = report.pressingTrend([
            seg(0, 40, 10, 4),
            seg(900, 30, 0, null),      // nobody challenged at all
            seg(1800, 25, 2, null),     // too few to divide by
        ]);
        assert.deepEqual(trend.blocks.map((b) => b.unchallenged),
            [false, true, false]);
        assert.deepEqual(trend.blocks.map((b) => b.thin), [false, false, true]);
        assert.deepEqual(trend.blocks.map((b) => b.share), [1, null, null]);
        assert.equal(trend.scored, 1);
    });

    test('the video offset moves the minutes onto the match clock', () => {
        const trend = report.pressingTrend([seg(900, 40, 10, 4)],
            { videoOffsetS: 300 });
        assert.equal(trend.blocks[0].startMin, 10);
        assert.equal(trend.blocks[0].endMin, 25);
    });

    test('a clock that would run negative is pinned at kickoff', () => {
        // An offset larger than the timestamp means the offset is wrong, and
        // "minute -4" is a worse way to say so than "minute 0".
        const trend = report.pressingTrend([seg(0, 40, 10, 4)],
            { videoOffsetS: 600 });
        assert.equal(trend.blocks[0].startMin, 0);
    });

    test('the hardest and softest spells are the ones with ratios', () => {
        const trend = report.pressingTrend([
            seg(0, 40, 10, 4), seg(900, 30, 0, null), seg(1800, 90, 10, 9),
        ]);
        assert.equal(trend.hardest.ppda, 4);
        assert.equal(trend.softest.ppda, 9);
    });
});

describe('pressingRead', () => {
    const trend = (...ppdas) => report.pressingTrend(ppdas.map((p, i) => ({
        start_s: i * 900, end_s: (i + 1) * 900,
        allowed: 40, actions: 10, ppda: p,
    })));

    test('a press that more than doubled its cost is called out', () => {
        const read = report.pressingRead(trend(4, 6, 9));
        assert.match(read.detail, /every 4\.0 passes/);
        assert.match(read.detail, /took 9\.0/);
    });

    test('a quarter-hour of ordinary variation says nothing', () => {
        // ~10 actions a block carries about ±32%, so the ratio of two blocks
        // moves ~±45% on chance alone. Anything under that is not a finding.
        assert.equal(report.pressingRead(trend(6, 7, 9)), null);
    });

    test('a press that held says nothing', () => {
        assert.equal(report.pressingRead(trend(8, 7.5, 8.2)), null);
    });

    test('blocks without a ratio take no part in the comparison', () => {
        // The last block is the loudest one on the chart and the least
        // measurable. Reading a slope into it would invent the finding.
        assert.equal(report.pressingRead(trend(8, 8.2, null)), null);
    });

    test('one scored block is not a trend', () => {
        assert.equal(report.pressingRead(trend(4, null)), null);
    });
});

describe('pressingNote', () => {
    const trend = (...blocks) => report.pressingTrend(blocks);
    const block = (allowed, actions, ppda) => ({
        start_s: 0, end_s: 900, allowed, actions, ppda,
    });

    test('the foul caveat is always there', () => {
        // Fouls count as a challenge in the standard definition and a camera
        // cannot see one, so every bar runs long — a known direction and an
        // unknown size, which has to sit beside the number.
        assert.match(report.pressingNote(trend(block(40, 10, 4))), /Fouls/);
    });

    test('a spell with no challenge at all is named as the reading', () => {
        const text = report.pressingNote(trend(block(30, 0, null)));
        assert.match(text, /nobody made a challenge/);
        assert.match(text, /not a gap/);
    });

    test('a thin block is described as thin, not as empty', () => {
        const text = report.pressingNote(trend(block(25, 2, null)));
        assert.match(text, /too few challenges to divide by/);
        assert.doesNotMatch(text, /nobody made a challenge/);
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

    test('a back line that dropped is described as deeper, not as smaller', () => {
        // The wording is the point. "Line height down 6m" reads as a decline;
        // the figure has no good direction, so the sentence says where on the
        // pitch the line went and leaves the verdict to the coach.
        const reads = report.cvReads(cv({
            shape_drift: { change: { line_m: -6.2 } },
        }));
        assert.equal(reads.length, 1);
        assert.match(reads[0].detail, /6m deeper/);
        assert.doesNotMatch(reads[0].detail, /lower|down|worse/);
    });

    test('and as higher up the pitch when it pushed on', () => {
        const reads = report.cvReads(cv({
            shape_drift: { change: { line_m: 7.4 } },
        }));
        assert.match(reads[0].detail, /7m higher up the pitch/);
    });

    test('the line is left out of the sentence when it was never measured', () => {
        // The common case: no `side_of_team`, so the other three drifted and
        // this one has nothing to say. A missing key must not become "0m".
        const reads = report.cvReads(cv({
            shape_drift: { change: { width_m: 6.2, depth_m: 0.4, compactness_m: -4.1 } },
        }));
        assert.doesNotMatch(reads[0].detail, /pitch|deeper|0m/);
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

describe('matchLine', () => {
    /** A published report of a win, ninety minutes, nothing else. */
    const played = (fields) => ({
        minutesPlayed: 90, minutesKnown: true, goals: 0, assists: 0,
        stints: [{ inS: 0, outS: 5400 }], scoreUs: 1, scoreThem: 0, ...fields,
    });

    test('reads as a sentence in every branch', () => {
        for (const fields of [
            {}, { goals: 2 }, { assists: 1 }, { goals: 1, assists: 1 },
            { minutesPlayed: 0, stints: [] },
            { minutesPlayed: 0, stints: [{ inS: 5300, outS: 5400 }] },
            { minutesKnown: false, minutesPlayed: 0 },
            { scoreUs: null, scoreThem: null },
            { minutesPlayed: 1, goals: 1, assists: 1 },
        ]) {
            const said = report.matchLine(played(fields));
            assert.match(said, /^[A-Z]/, `does not open a sentence: ${said}`);
            assert.match(said, /\.$/, `does not close one: ${said}`);
            // The phrases used to be spliced in after a fixed "You played".
            assert.doesNotMatch(said, /You played an unused/, said);
            assert.doesNotMatch(said, /\b1 (minutes|goals|assists)\b/, said);
        }
    });

    test('a player who never came on is told exactly that', () => {
        assert.equal(
            report.matchLine(played({ minutesPlayed: 0, stints: [] })),
            'Won. You did not get on.',
        );
    });

    test('a substitute the clock rounded to nothing still played', () => {
        // `minutesFrom` rounds, so somebody who came on with twenty-five
        // seconds left comes back as 0 — with a stint against their name. Zero
        // alone cannot tell the two apart, and telling somebody who came on
        // that they did not is the failure this sentence was rewritten for
        // once already.
        assert.equal(
            report.matchLine(played({
                minutesPlayed: 0, stints: [{ inS: 5375, outS: 5400 }],
            })),
            'Won. You were on for under a minute.',
        );
    });

    test('anything they did is evidence they were on', () => {
        // Even with no stints on the document, a goal means they played.
        assert.equal(
            report.matchLine(played({ minutesPlayed: 0, stints: [], goals: 1 })),
            'Won. You were on for under a minute and got 1 goal.',
        );
    });

    test('counts one of anything as one', () => {
        assert.equal(
            report.matchLine(played({ minutesPlayed: 1, goals: 1, assists: 1 })),
            'Won. You played 1 minute and got 1 goal and 1 assist.',
        );
    });

    test('a match nobody kept the clock for says so', () => {
        assert.equal(
            report.matchLine(played({ minutesKnown: false, minutesPlayed: 0 })),
            'Won. You played, but nobody kept the clock.',
        );
    });

    test('a report with no score is still a sentence', () => {
        assert.equal(
            report.matchLine(played({ scoreUs: null, goals: 2 })),
            'You played 90 minutes and got 2 goals.',
        );
    });
});

describe('taggedTeamRows', () => {
    const counts = (us = {}, them = {}) => ({
        us: { goal: 0, corner: 0, free_kick: 0, foul: 0, offside: 0, card: 0, ...us },
        them: { goal: 0, corner: 0, free_kick: 0, foul: 0, offside: 0, card: 0, ...them },
    });
    const by = (rows) => Object.fromEntries(rows.map((r) => [r.label, r]));

    test('carries both sides of every figure', () => {
        // The bug this function was written for: the coach's match report
        // showed "Our cards" and "Offsides against us" and nothing about the
        // opposition, while the half-time page showed both — so the fuller
        // document said less than the three-minute one.
        const rows = by(report.taggedTeamRows(
            counts({ card: 1, offside: 2, foul: 3 }, { card: 4, offside: 5, foul: 6 }),
        ));
        assert.equal(rows.Cards.usN, 1);
        assert.equal(rows.Cards.themN, 4);
        assert.equal(rows.Offside.usN, 2);
        assert.equal(rows.Offside.themN, 5);
        assert.equal(rows['Fouls committed'].usN, 3);
        assert.equal(rows['Fouls committed'].themN, 6);
    });

    test('every figure the tablet records survives the grouping', () => {
        // A regression guard with a name. Merging two hand-written lists is
        // exactly how a figure goes missing, and one already had.
        const rows = by(report.taggedTeamRows(counts(
            { goal: 1, corner: 1, free_kick: 1, foul: 1, offside: 1, card: 1 },
            { goal: 1, corner: 1, free_kick: 1, foul: 1, offside: 1, card: 1 },
        ), { subs: 2 }));
        for (const label of ['Goals', 'Corners won', 'Free kicks won',
            'Fouls committed', 'Offside', 'Cards', 'Substitutions']) {
            assert.ok(rows[label], `${label} is missing`);
        }
    });

    test('drops nothing on a report and drops the empty rows at half-time', () => {
        const only = counts({ corner: 3 });
        assert.equal(report.taggedTeamRows(only).length, 6);
        assert.deepEqual(
            report.taggedTeamRows(only, { goals: false, dropEmpty: true })
                .map((r) => r.label),
            ['Corners won'],
        );
    });

    test('leaves the opposition substitutions null, never zero', () => {
        // Nobody taps the other team's changes, so this is the one row where
        // the tablet was never asked. Zero would claim they made none.
        const subs = report.taggedTeamRows(counts(), { subs: 3 }).at(-1);
        assert.equal(subs.label, 'Substitutions');
        assert.equal(subs.usN, 3);
        assert.equal(subs.themN, null);
    });

    test('leaves the substitutions row out when nobody asked for it', () => {
        const labels = report.taggedTeamRows(counts()).map((r) => r.label);
        assert.ok(!labels.includes('Substitutions'));
    });

    test('reads the restart tags as the noise they are', () => {
        // Throw-ins, goal kicks and out-of-bounds are tagged so the pipeline
        // knows when the ball was dead. They are not a read on the match and
        // neither page shows them.
        const rows = report.taggedTeamRows(
            counts({ throw_in: 40, goal_kick: 9, out_of_bounds: 60 }),
        );
        assert.deepEqual(rows.filter((r) => r.usN), []);
    });
});

describe('taggedCount', () => {
    test('is the count when something was tagged', () => {
        assert.equal(report.taggedCount(2, [{ id: 'a' }]), 2);
        assert.equal(report.taggedCount(0, [{ id: 'a' }]), 0);
    });

    test('is an em dash when nothing was', () => {
        // Nil-all is a result. A match nobody ran the tablet for does not have
        // one, and it used to lead both pages in the largest type on screen.
        assert.equal(report.taggedCount(0, []), '—');
        assert.equal(report.taggedCount(undefined, null), '—');
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
        assert.equal(report.shapeStatRows({ width_m: 41.2 }, 0.4).length, 4);
    });

    // The three shape figures every calibrated run has, without the fourth.
    const OURS = { width_m: 41.2, depth_m: 33.8, compactness_m: 14.6 };
    const THEIRS = { width_m: 37.9, depth_m: 30.2, compactness_m: 13.1 };
    const shapeLine = (rows) => rows.find((r) => r.label === 'Defensive line');

    test('the defensive line is the one shape row that can go missing', () => {
        // It asks for more than the other three: somebody has to have said
        // which goal each side defends, and enough of the side has to be
        // tracked at once for the deepest few to be a back line. Absent on
        // both sides, `groupStats` drops the row rather than drawing a dash
        // beside three real figures.
        const rows = report.shapeStatRows(OURS, THEIRS, 0.4);
        assert.equal(shapeLine(rows).value, null);
        assert.equal(report.groupStats(rows)[0].rows.length, 3);
    });

    test('a measured line height draws in metres, against theirs', () => {
        const rows = report.shapeStatRows(
            { ...OURS, line_m: 28.4 },
            { ...THEIRS, line_m: 35.6 },
            0.4,
        );
        assert.equal(shapeLine(rows).value, '28m');
        assert.equal(shapeLine(rows).themValue, '36m');
        assert.equal(report.groupStats(rows)[0].rows.length, 4);
    });

    test('one side having a line height is enough to keep the row', () => {
        // The asymmetric case is real: the two sides are measured from
        // opposite goals off the same `side_of_team`, but a side the tracker
        // saw less of loses the figure on its own, and dropping the row then
        // would take the opposition's real figure down with it.
        const rows = report.shapeStatRows(OURS, { ...THEIRS, line_m: 35.6 },
            0.4);
        assert.equal(shapeLine(rows).value, null);
        assert.equal(shapeLine(rows).themValue, '36m');
        assert.equal(report.groupStats(rows)[0].rows.length, 4);
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

// ---------------------------------------------------------- the goalkeepers
//
// `cv/keeper.py` computed this block for months and no page drew a figure of
// it. What these cover is the two ways it could arrive wrong: a figure that is
// missing being drawn as a nought, and one keeper's absence taking the other
// keeper's numbers down with it.

describe('keeperStatRows', () => {
    const OURS = {
        team: 'team_a', saves: 3, goals_conceded: 1, save_pct: 0.75,
        claims: 4, sweeper_actions: 2, sweeper_max_distance_m: 21.4,
        distributions: 26, kick_accuracy: 0.5, punt_accuracy: null,
        throw_accuracy: 0.889, mean_kick_distance_m: 39.2,
        mean_punt_distance_m: null,
    };
    const THEIRS = {
        team: 'team_b', saves: 2, goals_conceded: 2, save_pct: 0.5,
        claims: 1, sweeper_actions: 5, sweeper_max_distance_m: 28.6,
        distributions: 31, kick_accuracy: 0.35, punt_accuracy: 0.4,
        throw_accuracy: 0.75, mean_kick_distance_m: 44.8,
        mean_punt_distance_m: 52.1,
    };
    const labelled = (rows) => Object.fromEntries(rows.map((r) => [r.label, r]));

    test('says nothing when neither side had a keeper identified', () => {
        assert.deepEqual(report.keeperStatRows(null), []);
        assert.deepEqual(report.keeperStatRows([]), []);
    });

    test('both keepers draw side by side, in the unit the row prints', () => {
        const rows = labelled(report.keeperStatRows([OURS, THEIRS]));
        assert.equal(rows.Saves.value, 3);
        assert.equal(rows['Save percentage'].value, '75%');
        assert.equal(rows['Save percentage'].themValue, '50%');
        // The bar's number and the printed number come from the same figure,
        // so a percentage row carries 75, not 0.75.
        assert.equal(rows['Save percentage'].usN, 75);
        assert.equal(rows['Furthest from goal'].value, '21m');
        assert.equal(rows['Average kick'].themValue, '45m');
    });

    test('one keeper alone keeps his own column and empties the other', () => {
        // `identify_keepers` works per team, so a side whose keeper wore a
        // colour close to his outfielders is the one that goes missing, and it
        // can be either side. Dropping the block would take the keeper who was
        // found down with the one who was not.
        const rows = labelled(report.keeperStatRows([THEIRS]));
        assert.equal(rows.Saves.value, null);
        assert.equal(rows.Saves.themValue, 2);
        assert.equal(report.groupStats(report.keeperStatRows([THEIRS]))[0].rows.length, 11);
    });

    test('a kind he never attempted is a dash, not nought per cent', () => {
        // Our keeper never punted. A 0% would say every punt he took was
        // wasted, which is a claim about punts that were never taken.
        const rows = labelled(report.keeperStatRows([OURS, THEIRS]));
        assert.equal(rows['Punt accuracy'].value, null);
        assert.equal(rows['Punt accuracy'].themValue, '40%');
        assert.equal(rows['Average punt'].value, null);
    });

    test('a keeper who never left his line has no furthest sweep', () => {
        // Reports published under schema 13 sent 0.0 for the maximum over an
        // empty set. Reading the count rather than the distance keeps one of
        // those from drawing a keeper who held his line as one who came out
        // and got exactly nowhere.
        const held = { ...OURS, sweeper_actions: 0, sweeper_max_distance_m: 0.0 };
        const rows = labelled(report.keeperStatRows([held, THEIRS]));
        assert.equal(rows['Furthest from goal'].value, null);
        assert.equal(rows['Sweeper actions'].value, 0);
    });

    test('the positional figures a run could not measure are dropped', () => {
        // Schema 14 sends null for all four when nothing said which goal the
        // keeper defends. Neither side has them here, so `groupStats` takes the
        // rows out rather than drawing four dashes beside real save figures.
        const blind = (k) => ({
            ...k, claims: null, sweeper_actions: null,
            sweeper_max_distance_m: null, distributions: null,
        });
        const groups = report.groupStats(
            report.keeperStatRows([blind(OURS), blind(THEIRS)]),
        );
        assert.deepEqual(
            groups[0].rows.map((r) => r.label),
            ['Saves', 'Save percentage', 'Kick accuracy', 'Punt accuracy',
             'Throw accuracy', 'Average kick', 'Average punt'],
        );
    });

    test('the metre rows carry the calibration band, not the event one', () => {
        // They rest on the homography. An event detector that did well says
        // nothing about whether 21 metres was 21 metres.
        const rows = labelled(report.keeperStatRows(
            [OURS, THEIRS], { events: 'high', shape: 'low' },
        ));
        assert.equal(rows.Saves.confidence, 'high');
        assert.equal(rows['Furthest from goal'].confidence, 'low');
        assert.equal(rows['Average kick'].confidence, 'low');
    });

    test('only the rate with the workload divided out is marked good or bad', () => {
        // Eight saves is either an excellent keeper or ten bad outfielders,
        // and a count cannot tell those apart.
        const rows = report.keeperStatRows([OURS, THEIRS]);
        const better = rows.filter((r) => r.better).map((r) => r.label);
        assert.deepEqual(better, ['Save percentage', 'Kick accuracy',
                                  'Punt accuracy', 'Throw accuracy']);
    });

    test('the keeping group sits between defending and shape', () => {
        // `groupStats` orders by `STAT_TYPES`, and a coach reading down the
        // page meets what the back line did before what the man behind it did.
        const ids = report.STAT_TYPES.map((t) => t.id);
        assert.deepEqual(ids.slice(ids.indexOf('defending'), ids.indexOf('shape') + 1),
                         ['defending', 'keeping', 'shape']);
    });

    test('teamStatRows carries the block through to the page', () => {
        // The whole point of the task: nothing in coach.js changed, so the
        // block has to arrive through the one function that page calls.
        const rows = report.teamStatRows({
            quality: {}, teams: { team_a: { possession_pct: 0.5 } },
            keepers: [OURS, THEIRS],
        });
        assert.ok(rows.some((r) => r.type === 'keeping' && r.label === 'Saves'));
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

describe('markLabel', () => {
    test('a headed chance says so, beside its number', () => {
        // Otherwise the dot is simply smaller than a foot shot from the same
        // spot for a reason the reader cannot see — and on the player's own
        // page there is nothing else on screen that would explain it.
        assert.equal(
            markMod.markLabel({ xg: 0.43, is_header: true, on_target: true }),
            'On target · header · 0.43 xG',
        );
    });

    test('a foot shot says nothing extra', () => {
        assert.equal(
            markMod.markLabel({ xg: 0.72, on_target: true }), 'On target · 0.72 xG',
        );
    });

    test('a header the run could not score still says it was one', () => {
        // The number is gone on purpose; the reason it is gone is the only
        // thing left worth showing.
        assert.equal(
            markMod.markLabel({ xg: null, is_header: true, outcome: 'goal' }),
            'Goal · header',
        );
    });

    test('the label drops xG on the same bands the radius does', () => {
        // A number the map has stopped drawing but a tooltip still reports is
        // the same claim made quietly, and the quiet one gets written down.
        assert.equal(
            markMod.markLabel({ xg: 0.43, on_target: true }, 'total'), 'On target',
        );
        assert.equal(markMod.markRadius(0.43, 'total'), markMod.markRadius(0.9, 'total'));
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
            { shots: 0, unplaced: 0, onTarget: 0, goals: 0, xg: null });
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

    test('on a good calibration, a single shot is worth showing', () => {
        // 0.5m is where calibrate/ calls a fit good, and it is now also where
        // per-shot xG stops. The band was 1.0m against the 12-feature model;
        // dropping shot_height made the model lean harder on position, so the
        // p95 shift at 1m went from 40% of the number to 89% of it.
        assert.equal(report.xgTrust(0), 'shot');
        assert.equal(report.xgTrust(0.5), 'shot');
    });

    test('past half a metre only the total is', () => {
        // At 1m a single shot's p95 shift is 0.168 on a 0.188 baseline — nearly
        // the whole quantity. The total survives because per-shot errors are
        // independent: over a half's six shots it lands within 12% at 1m.
        assert.equal(report.xgTrust(0.51), 'total');
        assert.equal(report.xgTrust(1.0), 'total');
        assert.equal(report.xgTrust(2), 'total');
        assert.equal(report.xgTrust(4.0), 'total');
    });

    test('past four metres even the total is not worth printing', () => {
        // Measured: a six-shot total moves 26% at 4m, and a single shot's p95
        // shift is twice the number.
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

    test("the sample player's coverage is its own arithmetic", () => {
        // Three literals that could drift apart into a preview showing a share
        // its own two minute figures contradict.
        const me = sample.samplePlayerReport();
        assert.ok(Math.abs(
            me.cvTrackedShare - me.cvMinutesTracked / me.cvMinutesFilmed,
        ) < 1e-9);
        assert.ok(me.cvMinutesFilmed <= me.cvMinutesOnPitch);
    });

    test('the sample previews the clean coverage sentence', () => {
        // Deliberate: the caveated wording is already exercised by the team
        // preview's 3.4 tracks per player, and a fixture where every caveat
        // fires at once teaches nobody which caveat means what.
        const me = sample.samplePlayerReport();
        const note = report.coverageNote({
            trackedS: me.cvMinutesTracked * 60,
            onPitchS: me.cvMinutesOnPitch * 60,
            watchedS: me.cvMinutesFilmed * 60,
            share: me.cvTrackedShare,
        }, { second: true });
        assert.match(note, /72 of the 90 minutes you played/);
        assert.doesNotMatch(note, /part of the match/);
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

    for (const key of ['team_a', 'team_b']) {
        test(`${key} pressing blocks add back up to its PPDA`, () => {
            // The chart sits directly under the row that prints this number.
            // Hand-written fixture segments that did not divide back to it
            // would model exactly the failure the real pipeline avoids by
            // counting both figures through one function.
            const team = sample.sampleCvSummary().teams[key];
            const sum = (field) => team.pressing_segments
                .reduce((total, s) => total + s[field], 0);
            assert.ok(Math.abs(sum('allowed') / sum('actions') - team.ppda) < 0.01);
        });

        test(`${key} pressing blocks tile the processed window`, () => {
            const cv = sample.sampleCvSummary();
            const blocks = cv.teams[key].pressing_segments;
            assert.equal(blocks[0].start_s, cv.window.start_s);
            assert.equal(blocks[blocks.length - 1].end_s, cv.window.end_s);
            for (let i = 1; i < blocks.length; i += 1) {
                assert.equal(blocks[i].start_s, blocks[i - 1].end_s);
            }
        });

        test(`${key} withholds a ratio exactly where the pipeline would`, () => {
            // MIN_PRESSING_ACTIONS in cv/events.py. A fixture that scored a
            // three-challenge block would preview a page the pipeline cannot
            // produce.
            for (const s of sample.sampleCvSummary().teams[key].pressing_segments) {
                assert.equal(s.ppda == null, s.actions < 5,
                    `${s.actions} challenges scored ${s.ppda}`);
                if (s.ppda != null) {
                    assert.ok(Math.abs(s.allowed / s.actions - s.ppda) < 0.01);
                }
            }
        });
    }

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

/**
 * The clock map. The half of every match the app was quietly getting wrong.
 *
 * The tablet's clock stops at half-time and the footage does not, so a single
 * offset is exact until the break and then wrong by the whole of it. These
 * cases are mostly about the second anchor: what it fixes, what it costs when
 * it is missing, and what happens when the two numbers cannot both be true.
 */
describe('matchClockMap', () => {
    // A recording started two minutes before kick-off; a first half that ran
    // 46:00 on the tablet; a thirteen-minute interval on the video.
    const REAL = {
        videoOffsetS: 120,
        secondHalfVideoS: 120 + 46 * 60 + 13 * 60,
        secondHalfClockS: 46 * 60,
    };

    test('the first half is the plain offset, exactly as before', () => {
        const clock = report.matchClockMap(REAL);
        assert.equal(clock.toClock(120).clockS, 0);
        assert.equal(clock.toClock(120 + 600).clockS, 600);
        assert.equal(clock.toClock(120 + 600).period, report.FIRST_HALF);
    });

    test('the second half is shifted back by the break, not carried through it', () => {
        const clock = report.matchClockMap(REAL);
        const videoS = REAL.secondHalfVideoS + 600;   // ten minutes into it
        assert.equal(clock.toClock(videoS).clockS, 46 * 60 + 600);
        assert.equal(clock.toClock(videoS).period, report.SECOND_HALF);

        // What one offset would have said, and the size of the mistake.
        const naive = videoS - REAL.videoOffsetS;
        assert.equal(naive - clock.toClock(videoS).clockS, 13 * 60);
    });

    test('the break reports the frozen reading, and says it is frozen', () => {
        const clock = report.matchClockMap(REAL);
        const midBreak = REAL.videoOffsetS + 46 * 60 + 300;
        assert.equal(clock.toClock(midBreak).period, report.HALF_TIME);
        // The clock really did read this for the whole interval.
        assert.equal(clock.toClock(midBreak).clockS, 46 * 60);
        assert.equal(clock.breakS, 13 * 60);
    });

    test('seeking round-trips on both sides of the break', () => {
        const clock = report.matchClockMap(REAL);
        for (const clockS of [0, 600, 46 * 60 - 1, 46 * 60, 46 * 60 + 900]) {
            assert.equal(clock.toClock(clock.toVideo(clockS)).clockS, clockS);
        }
    });

    test('the restart second belongs to the second half, not the whistle', () => {
        // `halftime` and `kickoff_2nd` carry the same clock reading, and of the
        // two video positions that could mean, the restart is the useful one.
        const clock = report.matchClockMap(REAL);
        assert.equal(clock.toVideo(46 * 60), REAL.secondHalfVideoS);
    });

    test('with no second anchor it is the old behaviour, and admits it', () => {
        const clock = report.matchClockMap({ videoOffsetS: 120 });
        assert.equal(clock.knowsSecondHalf, false);
        assert.equal(clock.breakS, null);
        assert.equal(clock.toClock(1000).clockS, 880);
        // The important half of this: no period is claimed, so nothing
        // downstream can print a half it was never told.
        assert.equal(clock.toClock(1000).period, null);
    });

    test('a pair that would run the clock backwards is refused, not honoured', () => {
        // The second half cannot kick off before the first one ended. Honouring
        // this would map two positions in the footage to one reading, and the
        // marks on the strip would cross over each other.
        const clock = report.matchClockMap({
            videoOffsetS: 120,
            secondHalfVideoS: 120 + 30 * 60,
            secondHalfClockS: 46 * 60,
        });
        assert.equal(clock.inconsistent, true);
        assert.equal(clock.knowsSecondHalf, false);
        assert.equal(clock.toClock(120 + 40 * 60).clockS, 40 * 60);
    });

    test('a zero break is fine — the two anchors simply agree', () => {
        const clock = report.matchClockMap({
            videoOffsetS: 0, secondHalfVideoS: 2760, secondHalfClockS: 2760,
        });
        assert.equal(clock.knowsSecondHalf, true);
        assert.equal(clock.breakS, 0);
    });

    test('a half-time reading of zero is not a half-time reading', () => {
        const clock = report.matchClockMap({
            videoOffsetS: 0, secondHalfVideoS: 900, secondHalfClockS: 0,
        });
        assert.equal(clock.knowsSecondHalf, false);
    });

    test('the map never runs backwards across the whole footage', () => {
        // The property that makes every timeline drawable: later in the file is
        // never earlier on the clock.
        const clock = report.matchClockMap(REAL);
        let previous = -1;
        for (let videoS = 0; videoS < 7000; videoS += 17) {
            const { clockS } = clock.toClock(videoS);
            assert.ok(clockS >= previous, `went backwards at ${videoS}s`);
            previous = clockS;
        }
    });

    test('clockFromMatch reads the fields a match document actually carries', () => {
        const clock = report.clockFromMatch({
            videoOffsetS: 120, secondHalfVideoS: 3660, halfTimeClockS: 2760,
        });
        assert.equal(clock.knowsSecondHalf, true);
        assert.equal(clock.breakS, 3660 - 120 - 2760);
    });

    test('a match with nothing set still produces a usable map', () => {
        const clock = report.clockFromMatch(undefined);
        assert.equal(clock.toClock(500).clockS, 500);
        assert.equal(clock.toVideo(500), 500);
    });
});

describe('clockMapNote', () => {
    const mmss = (s) => `${Math.floor(s / 60)}m`;

    test('an unset second anchor is stated as a consequence, not a blank', () => {
        const note = report.clockMapNote({ videoOffsetS: 120 }, mmss);
        assert.equal(note.tone, 'muted');
        // A coach has no other way to suspect it: the timestamps they get look
        // like ordinary minutes.
        assert.match(note.text, /never stopped/);
    });

    test('a video position with no half-time tap behind it says which is missing', () => {
        const note = report.clockMapNote(
            { videoOffsetS: 120, secondHalfVideoS: 3600 }, mmss,
        );
        assert.equal(note.tone, 'warn');
        assert.match(note.text, /tablet/);
    });

    test('an impossible pair names the reading it contradicts', () => {
        const note = report.clockMapNote(
            { videoOffsetS: 120, secondHalfVideoS: 1200, halfTimeClockS: 2760 }, mmss,
        );
        assert.equal(note.tone, 'warn');
        assert.match(note.text, /46m/);
    });

    test('a good pair reports the break it implies', () => {
        const note = report.clockMapNote(
            { videoOffsetS: 120, secondHalfVideoS: 3660, halfTimeClockS: 2760 }, mmss,
        );
        assert.equal(note.tone, 'ok');
        assert.match(note.text, /13m/);
    });
});

describe('everything that converts a timestamp uses the same map', () => {
    // The failure this guards against is not a wrong number, it is two right
    // numbers that disagree — the state before this existed, where four call
    // sites each did the subtraction themselves.
    const MATCH = { videoOffsetS: 120, secondHalfVideoS: 3660, halfTimeClockS: 2760 };
    const clock = report.clockFromMatch(MATCH);

    test('the processed window lands on the same clock as a seek', () => {
        const range = report.windowClockRange({ start_s: 4000, end_s: 5000 }, { clock });
        assert.equal(range.startS, clock.toClock(4000).clockS);
        assert.equal(range.endS, clock.toClock(5000).clockS);
        // And the second-half shift is really applied here, not merely available.
        assert.equal(range.startS, 2760 + (4000 - 3660));
    });

    test('a second-half cluster is ranked against the minutes it really overlaps', () => {
        const roster = [
            { id: 'starter', stints: [{ inS: 0, outS: 2760 }] },
            { id: 'sub', stints: [{ inS: 2760, outS: null }] },
        ];
        // Sighted through the second half. With one offset this stretch reads
        // as 3880-4780 on the clock, past the end of the match, overlapping
        // nobody — and the picker would have offered the whole roster equally.
        const cluster = { first_seen_s: 4000, last_seen_s: 4900 };
        const ranked = report.rankRosterForCluster(roster, cluster, {
            clock, matchEndS: 5400,
        });
        assert.equal(ranked[0].entry.id, 'sub');
        assert.ok(ranked[0].overlapShare > 0.99);
    });

    test('pressing blocks are quoted in match minutes, not video minutes', () => {
        const trend = report.pressingTrend([
            { start_s: 3660, end_s: 4560, allowed: 30, actions: 10, ppda: 3 },
            { start_s: 4560, end_s: 5460, allowed: 40, actions: 10, ppda: 4 },
        ], { clock });
        assert.equal(trend.blocks[0].startMin, 46);
        assert.equal(trend.blocks[1].startMin, 61);
    });
});

/**
 * Bursts and the wobble, on the browser side of the seam.
 *
 * The arithmetic lives in cv/metrics.py and is tested there. What can go wrong
 * here is the joining: a null that becomes a zero on the way through, or a
 * figure summed that should have been taken at its worst.
 */
describe('bursts across a player\'s fragments', () => {
    const cluster = (id, fields) => ({ cluster_id: id, team: 'team_a', ...fields });

    test('bursts add across the fragments a player was assembled from', () => {
        const stats = report.cvStatsByPlayer(
            [cluster(0, { accelerations: 12 }), cluster(1, { accelerations: 7 })],
            { 0: 'p1', 1: 'p1' },
        );
        assert.equal(stats.p1.accelerations, 19);
    });

    test('a fragment that could not answer is skipped, not counted as none', () => {
        // One fragment too short to hold a burst window, one that measured 12.
        // Nineteen would be wrong and so would twelve-out-of-two-fragments; the
        // right answer is twelve, from the one fragment that could say.
        const stats = report.cvStatsByPlayer(
            [cluster(0, { accelerations: 12 }), cluster(1, { accelerations: null })],
            { 0: 'p1', 1: 'p1' },
        );
        assert.equal(stats.p1.accelerations, 12);
    });

    test('a player whose every fragment was unreadable has no figure at all', () => {
        // Not zero. Zero says they never accelerated, which nothing measured.
        const stats = report.cvStatsByPlayer(
            [cluster(0, { accelerations: null }), cluster(1, { accelerations: null })],
            { 0: 'p1', 1: 'p1' },
        );
        assert.equal(stats.p1.accelerations, undefined);
        assert.equal(report.cvReportFields(stats.p1).cvAccelerations, null);
    });

    test('the wobble is taken at the worst fragment, not averaged', () => {
        // A player stitched from a clean track and a jittery one is only as
        // trustworthy as the jittery one, and an average would hide it behind
        // the clean one.
        const stats = report.cvStatsByPlayer(
            [cluster(0, { position_noise_m: 0.04 }), cluster(1, { position_noise_m: 0.22 })],
            { 0: 'p1', 1: 'p1' },
        );
        assert.equal(stats.p1.position_noise_m, 0.22);
    });

    test('both figures reach the published report', () => {
        const fields = report.cvReportFields({ accelerations: 31, position_noise_m: 0.12 });
        assert.equal(fields.cvAccelerations, 31);
        assert.equal(fields.cvPositionNoiseM, 0.12);
    });
});

describe('what the quality note says about the wobble', () => {
    const notes = (quality) => report.cvQualityNotes(quality, { calibrated: true });

    test('it is stated as a consequence, not as a number on its own', () => {
        // 0.14m means nothing to a coach. "A player standing still is credited
        // with twenty metres a minute" means a great deal.
        const said = notes({
            position_noise_m: 0.14, smoothing_s: 0.7, phantom_m_per_minute: 21.3,
        }).join(' ');
        assert.match(said, /standing still/);
        assert.match(said, /21m a minute/);
    });

    test('it quotes the rate the pipeline published, not one of its own', () => {
        // The window is fitted per track to the measured wobble, so the phantom
        // rate is no longer proportional to the wobble and this file cannot
        // work it out. Same noise, two different windows, two different rates —
        // and a browser deriving it would print the same number for both.
        const at = (phantom) => notes({
            position_noise_m: 0.20, smoothing_s: 1.0, phantom_m_per_minute: phantom,
        }).join(' ');
        assert.match(at(21.3), /21m a minute/);
        assert.match(at(49.6), /50m a minute/);
    });

    test('it says how long the figures behind it were smoothed for', () => {
        // A coach comparing two matches is entitled to know the second was
        // smoothed harder than the first, because that is a decision and not a
        // measurement.
        const said = notes({
            position_noise_m: 0.20, smoothing_s: 1.0, phantom_m_per_minute: 21.3,
        }).join(' ');
        assert.match(said, /smoothed over 1\.0s/);
    });

    test('a report from before the window was fitted keeps its own caveat', () => {
        // Everything already in Firestore. 353σ was measured against the fixed
        // nine-frame smoothing that produced those numbers, so it is still the
        // true statement about them — and it is the wrong one about a report
        // written since. Falling back is not a guess, it is the older answer.
        const said = notes({ position_noise_m: 0.10 }).join(' ');
        assert.match(said, /35m a minute/);
        assert.doesNotMatch(said, /smoothed over/);
    });

    test('past the ceiling it says the bursts were withheld, and why', () => {
        const said = notes({ position_noise_m: 0.45 }).join(' ');
        assert.match(said, /count of the jitter/);
    });

    test('under the ceiling it does not mention bursts at all', () => {
        const said = notes({ position_noise_m: 0.14 }).join(' ');
        assert.doesNotMatch(said, /jitter/);
    });

    test('a run that never measured it says nothing rather than zero', () => {
        // Every report from before this existed. Claiming a perfectly steady
        // camera would be the worst of the three possible answers.
        const said = notes({}).join(' ');
        assert.doesNotMatch(said, /wobble/);
    });
});

/**
 * Comparing the two sides, and the three different claims a bar can make.
 *
 * The pipeline has always measured both teams and the coach's report read one.
 * These cover the join, and — more importantly — the arithmetic that decides
 * how long each bar is, because a bar is read before either number beside it
 * and a wrong one is believed at a glance.
 */
describe('comparePair', () => {
    test('a share puts the boundary where the share is', () => {
        // Possession of the ball: their figure IS the rest of ours, and there
        // is no count behind it that could be too small.
        const bars = report.comparePair(58, 42, report.SHARE);
        assert.equal(bars.mode, 'split');
        assert.equal(Math.round(bars.us), 58);
        assert.equal(Math.round(bars.them), 42);
        assert.equal(bars.tentative, false);
    });

    test('a share of two figures that sum to nothing is not a share', () => {
        // Half and half would draw a dead heat nobody measured.
        assert.equal(report.comparePair(0, 0, report.SHARE), null);
    });

    test('a count is a split too — twelve shots to four is three quarters', () => {
        const bars = report.comparePair(12, 4, report.COUNT);
        assert.equal(bars.mode, 'split');
        assert.equal(bars.us, 75);
    });

    test('but a count says when its lead is smaller than chance', () => {
        // Three shots to one is also three quarters, and it is four shots in a
        // whole match. Same bar, and only one of them means anything.
        assert.equal(report.comparePair(30, 10, report.COUNT).tentative, false);
        assert.equal(report.comparePair(3, 1, report.COUNT).tentative, true);
        // The shares really are identical, which is the point.
        assert.equal(report.comparePair(3, 1, report.COUNT).us,
                     report.comparePair(30, 10, report.COUNT).us);
    });

    test('a share is never called tentative, because it has no count', () => {
        // 58% of the ball is not 58 of anything. Applying an event-count band
        // to a share of time would invent a sample size.
        assert.equal(report.comparePair(3, 1, report.SHARE).tentative, false);
    });

    test('rates run on their own scale, because they are not a whole', () => {
        // 84% and 71% pass accuracy are shares of two different denominators.
        // Drawn as a split they come out 54/46, which is a number about nothing.
        const bars = report.comparePair(84, 71, report.RATE);
        assert.equal(bars.mode, 'opposed');
        assert.equal(bars.us, 84);
        assert.equal(bars.them, 71);
    });

    test('a rate is clamped rather than overflowing its track', () => {
        assert.equal(report.comparePair(140, -5, report.RATE).us, 100);
        assert.equal(report.comparePair(140, -5, report.RATE).them, 0);
    });

    test('a pair with a side missing is not a pair', () => {
        // Filling the other half with zero would report a nil return the
        // pipeline never saw. Every kind refuses it.
        for (const kind of [report.SHARE, report.COUNT, report.RATE]) {
            assert.equal(report.comparePair(12, null, kind), null, kind);
            assert.equal(report.comparePair(null, 12, kind), null, kind);
        }
    });

    test('counts of nothing on both sides draw nothing', () => {
        assert.equal(report.comparePair(0, 0, report.COUNT), null);
    });
});

describe('insideNoise', () => {
    test('the band is two standard deviations of an even split', () => {
        // Under a null of no difference each event is a coin toss, so the gap
        // between the two counts has standard deviation sqrt(n). These are the
        // three worked examples in the docstring.
        assert.equal(report.insideNoise(3, 1), true);      // gap 2 vs 4.0
        assert.equal(report.insideNoise(12, 4), false);    // gap 8 vs 8.0
        assert.equal(report.insideNoise(30, 10), false);   // gap 20 vs 12.6
    });

    test('a dead heat is always inside it', () => {
        assert.equal(report.insideNoise(50, 50), true);
    });

    test('it takes more events to call the same ratio', () => {
        // 60/40 is the same ratio at every scale, and only becomes a finding
        // once there are enough of them.
        assert.equal(report.insideNoise(6, 4), true);
        assert.equal(report.insideNoise(60, 40), false);
    });

    test('nothing at all is not a lead', () => {
        assert.equal(report.insideNoise(0, 0), true);
    });
});

describe('when a difference has earned being called one', () => {
    const call = (o) => report.verdict(o);

    test('the side that is ahead is named, in the direction that is good', () => {
        assert.equal(call({ ours: 84, theirs: 71, better: 'high' }), 'ours-good');
        assert.equal(call({ ours: 71, theirs: 84, better: 'high' }), 'ours-bad');
    });

    test('and where low is better, the low side is the good one', () => {
        // PPDA, and everything else where a smaller number is the achievement.
        assert.equal(call({ ours: 6.9, theirs: 14.8, better: 'low' }), 'ours-good');
    });

    test('two figures that print the same are not a difference', () => {
        // The one browser verification caught. 10 possessions in 88 and 8 in 71
        // are 11.36% and 11.27%; both print "11%", and colouring one put a
        // verdict beside its own disproof — the reader is looking at the
        // evidence for it and it says the same thing twice.
        assert.equal(call({
            ours: 11.36, theirs: 11.27, usText: '11%', themText: '11%',
            better: 'high',
        }), null);
    });

    test('but the same numbers printed differently still are', () => {
        // The guard must not swallow a real lead just because it is small.
        assert.equal(call({
            ours: 11.36, theirs: 11.27, usText: '11.4%', themText: '11.3%',
            better: 'high',
        }), 'ours-good');
    });

    test('a lead inside the noise is not called either', () => {
        assert.equal(call({ ours: 3, theirs: 1, better: 'high', tentative: true }), null);
    });

    test('a row with no better direction is never coloured', () => {
        // Average width. A wider side is not a better one.
        assert.equal(call({ ours: 41, theirs: 32 }), null);
    });

    test('a side with no figure is not beaten by one that has', () => {
        assert.equal(call({ ours: 84, theirs: null, better: 'high' }), null);
        assert.equal(call({ ours: null, theirs: 71, better: 'high' }), null);
    });

    test('a dead heat is a dead heat', () => {
        assert.equal(call({ ours: 50, theirs: 50, better: 'high' }), null);
    });
});

describe('the phase-of-play funnel', () => {
    // 40 possessions: 28 reach midfield, 12 reach the final third, 5 end in a
    // shot. 18 start at the back and 9 of those get out of it.
    const PHASE = {
        total: 40,
        started: { defensive: 18, middle: 15, attacking: 7 },
        reached: { defensive: 40, middle: 28, attacking: 12 },
        ended: { shot: 5, lost: 20, stopped: 15 },
        passes: { defensive: 50, middle: 80, attacking: 40 },
        passes_completed: { defensive: 46, middle: 56, attacking: 22 },
        escaped_defence: 9,
    };
    const cvWith = (phase, theirs = undefined) => ({
        quality: {},
        teams: {
            team_a: { possession_pct: 0.5, phase_of_play: phase },
            team_b: { possession_pct: 0.5, phase_of_play: theirs },
        },
    });
    const rowsFor = (phase, theirs) => report.teamStatRows(cvWith(phase, theirs));
    const find = (rows, label) => rows.find((r) => r.label === label);

    test('the funnel is a share of that side’s own possessions', () => {
        // Never of the match. A team with the ball twice as often would
        // otherwise look twice as good at moving it.
        const rows = rowsFor(PHASE);
        assert.equal(find(rows, 'Reached midfield').value, '70%');
        assert.equal(find(rows, 'Reached the final third').value, '30%');
        assert.equal(find(rows, 'Ended in a shot').value, '13%');
    });

    test('it never widens as it goes forward', () => {
        // The property that makes a stack of bars readable as a funnel at all.
        // `reached` is cumulative in the pipeline, and if that ever stopped
        // being true the bars would grow downward and mean nothing.
        const rows = rowsFor(PHASE);
        const stages = ['Reached midfield', 'Reached the final third', 'Ended in a shot']
            .map((label) => find(rows, label).usN);
        for (let i = 1; i < stages.length; i += 1) {
            assert.ok(stages[i] <= stages[i - 1], `${stages[i]} > ${stages[i - 1]}`);
        }
    });

    test('the note says a possession counts by how far it got', () => {
        // The definitional choice a reader would otherwise have to guess at.
        // "Reached midfield" counts a possession that *began* in the final
        // third, because the funnel scores by the furthest point reached and
        // has to stay monotonic to be drawn as one. A side that wins the ball
        // high a lot is flattered by that, and the caption is where it says so.
        const groups = report.groupStats(report.teamStatRows(cvWith(PHASE)));
        const note = groups.find((g) => g.id === 'phases').note;
        assert.match(note, /furthest point/);
        assert.match(note, /began in the final third/);
    });

    test('playing out from the back uses the possessions that started there', () => {
        // 9 of 18, not 9 of 40. The whole-funnel share is flattered by every
        // possession that began in midfield already.
        assert.equal(find(rowsFor(PHASE), 'Out from the back').value, '50%');
    });

    test('a side that never had it at the back is not scored on getting out', () => {
        // Absent, not zero: they did not fail to play out from the back.
        const never = { ...PHASE, started: { defensive: 0, middle: 40 }, escaped_defence: 0 };
        assert.equal(find(rowsFor(never), 'Out from the back').value, null);
    });

    test('passing is split by the third it was played from', () => {
        // The point of the whole feature. 92 / 70 / 55 is a normal side; one
        // overall figure of 74% hides all three.
        const rows = rowsFor(PHASE);
        assert.equal(find(rows, 'Passing at the back').value, '92%');
        assert.equal(find(rows, 'Passing in midfield').value, '70%');
        assert.equal(find(rows, 'Passing up front').value, '55%');
    });

    test('a third nobody passed in has no accuracy rather than none percent', () => {
        const quiet = {
            ...PHASE,
            passes: { defensive: 50, middle: 80, attacking: 0 },
            passes_completed: { defensive: 46, middle: 56, attacking: 0 },
        };
        assert.equal(find(rowsFor(quiet), 'Passing up front').value, null);
    });

    test('every funnel row is drawn against a fixed hundred, not as a split', () => {
        // Two sides can both take 30% of their possessions into the final
        // third. Drawn as a split that reads as a dead heat between figures
        // that are not shares of one thing at all.
        const rows = rowsFor(PHASE, PHASE);
        for (const row of rows.filter((r) => r.type === 'phases' && r.kind)) {
            if (row.label === 'Possessions') continue;
            assert.equal(row.kind, report.RATE, row.label);
        }
    });

    test('possessions themselves are a count, with the band that goes with it', () => {
        // 40 against 38 is not a lead in how often you had the ball.
        const rows = rowsFor(PHASE, { ...PHASE, total: 38 });
        const row = find(rows, 'Possessions');
        assert.equal(row.kind, report.COUNT);
        assert.ok(report.insideNoise(row.usN, row.themN));
    });

    test('a run that measured no phases shows no phase rows', () => {
        // Not a column of zeroes. Without a calibration there is no third to
        // name, so there is nothing to say rather than nothing that happened.
        const groups = report.groupStats(report.teamStatRows(cvWith(undefined)));
        assert.equal(groups.find((g) => g.id === 'phases'), undefined);
    });

    test('the opponent gets the same funnel from the same fields', () => {
        const rows = rowsFor(PHASE, { ...PHASE, reached: { ...PHASE.reached, attacking: 20 } });
        const row = find(rows, 'Reached the final third');
        assert.equal(row.value, '30%');
        assert.equal(row.themValue, '50%');
    });
});

describe('the opponent reaches the report', () => {
    const CV = {
        calibrationErrorM: 0.4,
        quality: { live_share: 0.7 },
        teams: {
            team_a: {
                possession_pct: 0.58, touches: 400, shots: 12, pass_accuracy: 0.84,
                passes_attempted: 300, passes_by_direction: { forward: 90 },
                tackles: 14, xg: 1.4,
                territory: { defensive: 0.2, middle: 0.5, attacking: 0.3 },
                shape: { width_m: 38, depth_m: 30, compactness_m: 12 },
            },
            team_b: {
                possession_pct: 0.42, touches: 300, shots: 4, pass_accuracy: 0.71,
                passes_attempted: 200, passes_by_direction: { forward: 80 },
                tackles: 9, xg: 0.5,
                territory: { defensive: 0.35, middle: 0.45, attacking: 0.2 },
                shape: { width_m: 30, depth_m: 26, compactness_m: 10 },
            },
        },
    };

    const find = (rows, label) => rows.find((r) => r.label === label);

    test('every comparable row carries both sides', () => {
        const rows = report.teamStatRows(CV);
        const compared = rows.filter((r) => r.kind);
        assert.ok(compared.length >= 15);
        for (const row of compared) {
            assert.ok('themN' in row, `${row.label} has no opposition figure`);
        }
    });

    test('the two sides are read off the same field, never two', () => {
        const shots = find(report.teamStatRows(CV), 'Shots');
        assert.equal(shots.usN, 12);
        assert.equal(shots.themN, 4);
        assert.equal(shots.kind, report.COUNT);
    });

    test('possession is the only share of a continuous whole', () => {
        // Everything else is either a count of events or a rate on its own
        // denominator. Getting this wrong would put an event-count noise band
        // on a share of time.
        const rows = report.teamStatRows(CV);
        const shares = rows.filter((r) => r.kind === report.SHARE);
        assert.equal(shares.length, 1);
        assert.match(shares[0].label, /Possession/);
    });

    test('a share of each side\'s own attempts uses that side\'s own total', () => {
        // 90 of our 300 is 30%; 80 of their 200 is 40%. Dividing their forward
        // passes by our attempts would give 27% — a number about nobody.
        const forward = find(report.teamStatRows(CV), 'Played forward');
        assert.equal(forward.value, '30%');
        assert.equal(forward.themValue, '40%');
        assert.equal(forward.kind, report.RATE);
    });

    test('shape is compared too, since a width means little on its own', () => {
        const width = find(report.teamStatRows(CV), 'Average width');
        assert.equal(width.value, '38m');
        assert.equal(width.themValue, '30m');
    });

    test('a report with no opposition block degrades to one side', () => {
        // Every report published before both sides were carried. The rows stay
        // and the bars drop out, rather than the opposition reading as zero.
        const rows = report.teamStatRows({ ...CV, teams: { team_a: CV.teams.team_a } });
        const shots = find(rows, 'Shots');
        assert.equal(shots.usN, 12);
        assert.equal(shots.themN, null);
        assert.equal(report.comparePair(shots.usN, shots.themN, shots.kind), null);
    });

    test('a row measurable only for them survives to be shown', () => {
        // The asymmetric case is real: PPDA is null for a side that made no
        // defensive actions at all, and dropping the row would take the
        // opposition's figure with it.
        const cv = {
            ...CV,
            teams: {
                team_a: { ...CV.teams.team_a, ppda: null },
                team_b: { ...CV.teams.team_b, ppda: 8.2 },
            },
        };
        const rows = report.groupStats(report.teamStatRows(cv))
            .flatMap((g) => g.rows);
        const ppda = find(rows, 'PPDA');
        assert.ok(ppda, 'the row was dropped along with their figure');
        assert.equal(ppda.value, null);
        assert.equal(ppda.themValue, '8.2');
    });

    test('fewer passes does not read as less direct', () => {
        // They attempted two thirds of what we did. If both breakdowns were
        // shares of our total, every one of theirs would be understated by a
        // third and they would look like a side that never went forward.
        const forward = find(report.teamStatRows(CV), 'Played forward');
        assert.ok(forward.themN > forward.usN);
    });
});

describe('what the run could not see', () => {
    const split = (blind) => report.blindSplit({ blind });
    const checked = {
        total_s: 400, checked: true,
        dead_s: 250, accounted_s: 100, unexplained_s: 50, worst: [],
    };

    test('nothing unseen is nothing to draw', () => {
        assert.equal(report.blindSplit({}), null);
        assert.equal(report.blindSplit({ blind: null }), null);
        assert.equal(split({ total_s: 0, checked: true }), null);
    });

    test('the three parts come back in the order they happened to a coach', () => {
        // Stoppages first because that is most of it and none of it is a
        // problem; the hole is last, where the eye finishes.
        assert.deepEqual(
            split(checked).segments.map((s) => s.key),
            ['dead', 'accounted', 'unexplained'],
        );
    });

    test('the shares are of the blind time, not of the match', () => {
        const parts = split(checked).segments;
        assert.equal(parts[0].share, 0.625);
        assert.equal(parts[2].share, 0.125);
    });

    test('the segments fill the track exactly', () => {
        const total = split(checked).segments
            .reduce((acc, s) => acc + s.share, 0);
        assert.ok(Math.abs(total - 1) < 1e-9);
    });

    test('a part measured at zero gets no segment rather than an empty one', () => {
        const parts = split({
            total_s: 100, checked: true,
            dead_s: 0, accounted_s: 40, unexplained_s: 60,
        }).segments;
        assert.deepEqual(parts.map((s) => s.key), ['accounted', 'unexplained']);
    });

    test('an unchecked run draws no bar at all', () => {
        // A bar whose whole length is one unnamed block claims a split was
        // measured and came out that way. Nothing measured it.
        const unchecked = split({
            total_s: 400, checked: false,
            dead_s: null, accounted_s: null, unexplained_s: null,
        });
        assert.deepEqual(unchecked.segments, []);
        assert.equal(unchecked.unexplainedS, null);
        assert.equal(unchecked.totalS, 400);
    });

    test('camelCase reaches it too, the way every other quality field does', () => {
        const parts = report.blindSplit({
            blind: {
                totalS: 100, checked: true,
                deadS: 50, accountedS: 30, unexplainedS: 20,
            },
        }).segments;
        assert.deepEqual(parts.map((s) => s.seconds), [50, 30, 20]);
    });
});

describe('the quality note about what was missed', () => {
    const joined = (q) => report.cvQualityNotes(q).join(' | ');
    const blind = {
        total_s: 459, checked: true,
        dead_s: 268, accounted_s: 96, unexplained_s: 95, worst: [],
    };

    test('a checked run ends the sentence on the part that is a hole', () => {
        // 459s of blindness and 95s of missing football are very different
        // reports, and the second is the one a coach can do anything with.
        const note = joined({ ball_seen_share: 0.83, no_ball_s: 459, blind });
        assert.match(note, /83% of frames/);
        assert.match(note, /of the 7m 39s with no ball in sight/);
        assert.match(note, /1m 35s was live play nothing accounts for/);
    });

    test('a well-tagged half no longer looks worse than an untagged one', () => {
        // The bug this fixes: the same footage, tagged and untagged, and the
        // tagged run reported a bigger number for the same tracking.
        const tagged = joined({ ball_seen_share: 0.83, no_ball_s: 459, blind });
        const untagged = joined({ ball_seen_share: 0.83, no_ball_s: 459 });
        assert.match(untagged, /7m 39s of the clip with no ball in sight/);
        assert.doesNotMatch(untagged, /nothing accounts for/);
        assert.ok(tagged.includes('1m 35s'));
    });

    test('an unchecked run falls back to the total it can stand behind', () => {
        const note = joined({
            ball_seen_share: 0.83, no_ball_s: 459,
            blind: { total_s: 459, checked: false, dead_s: null,
                     accounted_s: null, unexplained_s: null },
        });
        assert.match(note, /7m 39s of the clip with no ball in sight/);
        assert.doesNotMatch(note, /accounts for/);
    });

    test('the split still gets said when ball coverage itself is missing', () => {
        const note = joined({ no_ball_s: 459, blind });
        assert.match(note, /^of the 7m 39s with no ball in sight/);
    });

    test('a run that never lost the ball says nothing about it', () => {
        assert.doesNotMatch(joined({ ball_seen_share: 1 }), /no ball in sight/);
    });
});

describe('the worst stretch the run lost', () => {
    const withWorst = (worst) => report.blindSplit({
        blind: {
            total_s: 400, checked: true,
            dead_s: 250, accounted_s: 100, unexplained_s: 50, worst,
        },
    });

    test('the longest one comes back, since that is the order it arrives in', () => {
        // A hundred flickers and one blackout lose the same minutes and are
        // not the same failure. cv/blind.py sorts longest-first.
        const split = withWorst([
            { start_s: 1284, duration_s: 28, kind: 'unexplained' },
            { start_s: 402, duration_s: 19, kind: 'unexplained' },
        ]);
        assert.deepEqual(split.worst, { startS: 1284, durationS: 28 });
    });

    test('a run with nothing worth naming has no worst stretch', () => {
        assert.equal(withWorst([]).worst, null);
        assert.equal(withWorst(undefined).worst, null);
    });

    test('an unchecked run still says where its longest gap was', () => {
        // The split is withheld because nothing sorted it; where the footage
        // went dark was measured either way.
        const split = report.blindSplit({
            blind: {
                total_s: 400, checked: false,
                dead_s: null, accounted_s: null, unexplained_s: null,
                worst: [{ start_s: 90, duration_s: 45, kind: 'unchecked' }],
            },
        });
        assert.deepEqual(split.worst, { startS: 90, durationS: 45 });
        assert.deepEqual(split.segments, []);
    });
});

describe('the two records in one column', () => {
    const clock = report.matchClockMap({ videoOffsetS: 0 });
    const cv = (id, type, timestampS, extra = {}) => ({ id, type, timestampS, ...extra });
    const tag = (type, matchClockS) => ({ type, matchClockS });
    const feed = (events, log, options) => report.reviewFeed(events, log, { clock, ...options });

    test('both records land in one list, in time order', () => {
        const items = feed(
            [cv('a', 'pass', 30), cv('b', 'tackle', 90)],
            [tag('corner', 60), tag('foul', 10)],
        );
        assert.deepEqual(items.map((i) => i.clockS), [10, 30, 60, 90]);
        assert.deepEqual(items.map((i) => i.source), [
            report.FROM_TAGGED, report.FROM_VIDEO,
            report.FROM_TAGGED, report.FROM_VIDEO,
        ]);
    });

    test('a shot and the goal it became read in the order they happened', () => {
        // The tagger taps after the ball crosses the line. On a tie the video
        // goes first, because that is when the thing occurred.
        const items = feed([cv('a', 'shot', 600)], [tag('goal', 600)]);
        assert.deepEqual(items.map((i) => i.source),
            [report.FROM_VIDEO, report.FROM_TAGGED]);
    });

    test('period markers stay out of the order they cannot claim', () => {
        // Everything in the interval shares one clock reading, so a half-time
        // row would sit somewhere it did not happen.
        const items = feed([], [
            { kind: 'period', type: 'halftime', matchClockS: 2700 },
            tag('corner', 2700),
        ]);
        assert.deepEqual(items.map((i) => i.type), ['corner']);
    });

    test('an entry with no clock is dropped rather than placed at zero', () => {
        const items = feed([], [tag('corner', 60), { type: 'foul' }]);
        assert.equal(items.length, 1);
    });

    test('subs come through, keyed so two of them cannot collide', () => {
        const items = feed([], [
            { kind: 'sub', matchClockS: 1200, label: 'A off, B on' },
            { kind: 'sub', matchClockS: 1800, label: 'C off, D on' },
        ]);
        assert.deepEqual(items.map((i) => i.type), ['sub', 'sub']);
        assert.notEqual(items[0].id, items[1].id);
    });

    test('the two clocks are reconciled once, not per comparison', () => {
        // A candidate 40s into footage that started 30s before kick-off is at
        // 10s on the clock, which is the unit the tagged entry beside it uses.
        const shifted = report.matchClockMap({ videoOffsetS: 30 });
        const items = report.reviewFeed([cv('a', 'pass', 40)], [tag('foul', 10)],
            { clock: shifted });
        assert.equal(items[0].clockS, 10);
        assert.equal(items[1].clockS, 10);
    });
});

describe('what the log says was happening', () => {
    const clock = report.matchClockMap({ videoOffsetS: 0 });
    const cv = (id, type, timestampS, extra = {}) => ({ id, type, timestampS, ...extra });
    const tag = (type, matchClockS) => ({ type, matchClockS });
    const feed = (events, log, options) => report.reviewFeed(events, log, { clock, ...options });
    // The list is in time order, and a restart is tagged before the touch it
    // produced, so the candidate is not reliably first.
    const candidate = (items) => items.find((i) => i.source === report.FROM_VIDEO);

    test('a candidate carries the tagged entry nearest to it', () => {
        // The case this exists for: a "pass" two seconds after a throw-in is
        // almost certainly the throw, which is the touch the detector gets
        // wrong most and the hardest one to judge without scrubbing.
        const items = feed([cv('a', 'pass', 62)], [tag('throw_in', 60)]);
        assert.equal(candidate(items).nearbyTag.type, 'throw_in');
        assert.equal(candidate(items).nearbyTag.gapS, -2);
    });

    test('a tag further away than a restart is not context', () => {
        const items = feed([cv('a', 'pass', 100)], [tag('throw_in', 60)]);
        assert.equal(candidate(items).nearbyTag, null);
    });

    test('the nearest one wins when several are in range', () => {
        const items = feed([cv('a', 'pass', 60)], [tag('foul', 57), tag('corner', 63)]);
        assert.equal(candidate(items).nearbyTag.type, 'foul');
    });

    test('a substitution is not context for a pass', () => {
        const items = feed([cv('a', 'pass', 60)],
            [{ kind: 'sub', matchClockS: 61, label: 'A off' }]);
        assert.equal(candidate(items).nearbyTag, null);
    });
});

describe('a goal the video missed and a human already proved', () => {
    const clock = report.matchClockMap({ videoOffsetS: 0 });
    const cv = (id, type, timestampS, extra = {}) => ({ id, type, timestampS, ...extra });
    const tag = (type, matchClockS) => ({ type, matchClockS });
    const feed = (events, log, options) => report.reviewFeed(events, log, { clock, ...options });
    const suggestion = (items) => items.find((i) => i.suggestion)?.suggestion ?? null;

    test('a tagged goal with no candidate near it offers to be recorded', () => {
        const items = feed([cv('a', 'pass', 100)], [tag('goal', 600)]);
        assert.deepEqual(suggestion(items), { type: 'shot', clockS: 600, recorded: false });
    });

    test('a shot the video did find is not a miss, whatever it called it', () => {
        // Recall asks whether the moment was found. A shot scored as saved
        // found the moment; getting the outcome wrong is a different failure
        // and the shot ledger's business.
        const items = feed([cv('a', 'shot', 596, { outcome: 'saved' })], [tag('goal', 600)]);
        assert.equal(suggestion(items), null);
    });

    test('a shot outside the pairing window does not cover the goal', () => {
        const far = 600 + report.GOAL_PAIR_S + 5;
        const items = feed([cv('a', 'shot', far)], [tag('goal', 600)]);
        assert.ok(suggestion(items));
    });

    test('a miss already recorded says so instead of offering again', () => {
        const items = feed([], [tag('goal', 600)], { missed: [{ clockS: 602, type: 'shot' }] });
        assert.equal(suggestion(items).recorded, true);
    });

    test('a recorded miss of another type is not this one', () => {
        const items = feed([], [tag('goal', 600)], { missed: [{ clockS: 600, type: 'pass' }] });
        assert.equal(suggestion(items).recorded, false);
    });

    test('nothing but a goal is ever offered', () => {
        // The two vocabularies intersect on one word. A corner is not something
        // the pipeline ever claimed to find, so its absence is not a miss.
        const items = feed([], [tag('corner', 60), tag('foul', 90), tag('card', 120)]);
        assert.equal(suggestion(items), null);
    });
});

describe('the tagged log never scores the pipeline', () => {
    const clock = report.matchClockMap({ videoOffsetS: 0 });

    test('merging the log leaves precision and recall untouched', () => {
        // A tagged corner is not something the pipeline claimed, and counting
        // it as agreement would credit the detector with work nobody did.
        const events = [
            { id: 'a', type: 'pass', timestampS: 10 },
            { id: 'b', type: 'pass', timestampS: 20 },
        ];
        const review = { byEvent: { a: { status: 'confirmed' } }, missed: [] };
        const before = report.reviewScore(events, review);

        const log = [
            { type: 'corner', matchClockS: 11 },
            { type: 'goal', matchClockS: 15 },
            { type: 'throw_in', matchClockS: 21 },
        ];
        report.reviewFeed(events, log, { clock, missed: review.missed });
        assert.deepEqual(report.reviewScore(events, review), before);
    });

    test('the feed does not mutate the events it was given', () => {
        const events = [{ id: 'a', type: 'pass', timestampS: 10 }];
        report.reviewFeed(events, [{ type: 'corner', matchClockS: 11 }], { clock });
        assert.deepEqual(Object.keys(events[0]), ['id', 'type', 'timestampS']);
    });

    test('an empty log is a list of candidates, not an error', () => {
        const items = report.reviewFeed(
            [{ id: 'a', type: 'pass', timestampS: 10 }], null, { clock },
        );
        assert.equal(items.length, 1);
        assert.equal(items[0].nearbyTag, null);
    });

    test('a log with no video run is still a list', () => {
        const items = report.reviewFeed(null, [{ type: 'goal', matchClockS: 60 }], { clock });
        assert.equal(items.length, 1);
        assert.ok(items[0].suggestion);
    });
});

describe('what rate the run was read at', () => {
    const joined = (q) => report.cvQualityNotes(q).join(' | ');

    test('a run that skipped frames says so, in both rates', () => {
        // One number would not do it: 15 a second is half a camcorder and a
        // quarter of a phone, and only the pair says which happened.
        assert.match(joined({ source_fps: 60, sample_fps: 15 }),
            /read at 15 of the footage's 60 frames a second/);
    });

    test('a run that read every frame says nothing', () => {
        // "30 of 30" is noise in a list of caveats.
        assert.doesNotMatch(joined({ source_fps: 30, sample_fps: 30 }),
            /frames a second/);
    });

    test('a hair of floating-point drift is not a skipped frame', () => {
        assert.doesNotMatch(joined({ source_fps: 29.97, sample_fps: 29.97 }),
            /frames a second/);
    });

    test('broadcast rates read as sentences rather than as long decimals', () => {
        assert.match(joined({ source_fps: 59.94, sample_fps: 29.97 }),
            /read at 30 of the footage's 59.9 frames a second/);
    });

    test('a report written before the rate was recorded claims nothing', () => {
        // Every one of them ran at full rate and none of them can prove it,
        // so the honest thing is silence rather than an assumption.
        assert.doesNotMatch(joined({ ball_seen_share: 0.8 }), /frames a second/);
        assert.doesNotMatch(joined({ source_fps: 30 }), /frames a second/);
        assert.doesNotMatch(joined({ sample_fps: 15 }), /frames a second/);
    });

    test('camelCase reaches it too', () => {
        assert.match(joined({ sourceFps: 60, sampleFps: 30 }),
            /read at 30 of the footage's 60 frames a second/);
    });
});

describe('what a printed sheet says about itself', () => {
    const day = new Date(2026, 4, 2);

    test('whose it is, which match, and when it was printed', () => {
        // The three things a page loses the moment it leaves the browser, and
        // the three somebody will ask of it in three months.
        const stamp = report.printStamp({
            subject: 'Alex Vega',
            matchLine: 'vs Linden · 2 May 2026',
            printedAt: day,
        });
        assert.match(stamp, /Alex Vega/);
        assert.match(stamp, /vs Linden/);
        assert.match(stamp, /printed/);
    });

    test('the printing date is not the match date', () => {
        // A sheet re-printed after a coach corrected the review says something
        // different from one printed on the night, and only this tells them
        // apart. Both dates are on the line and only one is labelled.
        const stamp = report.printStamp({
            matchLine: 'vs Linden · 2 May 2026',
            printedAt: new Date(2026, 7, 9),
        });
        assert.match(stamp, /vs Linden · 2 May 2026/);
        assert.match(stamp, /printed .*2026/);
        assert.doesNotMatch(stamp, /printed 2 May/);
    });

    test('a page with only half the answer still carries that half', () => {
        assert.match(report.printStamp({ subject: 'Dev High', printedAt: day }),
            /^Dev High · printed/);
        assert.match(report.printStamp({ matchLine: 'vs Linden', printedAt: day }),
            /^vs Linden · printed/);
    });

    test('a page about nothing in particular is still dated', () => {
        assert.match(report.printStamp({ printedAt: day }), /^printed /);
    });

    test('an estimated report says so on the same line', () => {
        // The quality banner travels onto the page, but a page can be read out
        // loud from across a room without it. This line is on every sheet.
        const stamp = report.printStamp({ subject: 'Alex Vega', estimated: true });
        assert.match(stamp, /estimated from video/);
        // And what the marks beside those figures are. On screen they carry a
        // title attribute; on paper three bare dots read as a reference to a
        // footnote that is not there.
        assert.match(stamp, /marked ···/);
        assert.doesNotMatch(report.printStamp({ subject: 'Alex Vega' }),
            /estimated from video/);
    });

    test('an unusable date is dropped rather than printed as nonsense', () => {
        const stamp = report.printStamp({ subject: 'Alex Vega', printedAt: 'not a date' });
        assert.equal(stamp, 'Alex Vega');
        assert.doesNotMatch(stamp, /Invalid/);
    });

    test('a date given as a string is still a date', () => {
        assert.match(report.printStamp({ printedAt: '2026-05-02T10:00:00Z' }),
            /^printed .*2026/);
    });
});

describe('the review reaches the numbers a player sees', () => {
    const clusters = [
        { cluster_id: 0, track_ids: [3, 4] },
        { cluster_id: 1, track_ids: [9] },
    ];
    const mapping = { 0: 'alex', 1: 'sam' };
    const tracks = [
        { cluster_id: 0, passes_attempted: 10, passes_completed: 8, carries: 4,
          tackles: 2, interceptions: 1, recoveries: 1, touches: 40,
          distance_m: 8000, top_speed_kmh: 27, sprint_count: 12 },
        { cluster_id: 1, passes_attempted: 5, passes_completed: 3, carries: 1,
          tackles: 0, interceptions: 0, recoveries: 0, touches: 20,
          distance_m: 6000, top_speed_kmh: 25, sprint_count: 6 },
    ];
    const ev = (id, type, trackId, extra = {}) =>
        ({ id, type, trackId, ...extra });

    const stats = (events, byEvent) => report.cvStatsByPlayer(tracks, mapping, {
        events, review: { byEvent }, clusters,
    });

    test('with no review the figures are the pipeline\'s own', () => {
        const plain = report.cvStatsByPlayer(tracks, mapping);
        assert.equal(plain.alex.passes_attempted, 10);
        assert.equal(plain.alex.reviewed, undefined);
    });

    test('a rejected pass leaves both halves of the accuracy', () => {
        const out = stats(
            [ev('e1', 'pass', 3, { outcome: 'completed' })],
            { e1: { status: 'rejected' } },
        );
        assert.equal(out.alex.passes_attempted, 9);
        assert.equal(out.alex.passes_completed, 7);
        assert.equal(out.alex.passAccuracy, 7 / 9);
    });

    test('an incomplete pass rejected takes only the attempt', () => {
        const out = stats(
            [ev('e1', 'pass', 3, { outcome: 'incomplete' })],
            { e1: { status: 'rejected' } },
        );
        assert.equal(out.alex.passes_attempted, 9);
        assert.equal(out.alex.passes_completed, 8);
    });

    test('a confirmed event changes nothing', () => {
        const out = stats([ev('e1', 'pass', 3)], { e1: { status: 'confirmed' } });
        assert.equal(out.alex.passes_attempted, 10);
    });

    test('an unreviewed event stays counted', () => {
        // The review is twelve events out of five hundred. Counting only what a
        // human confirmed would wipe out the match.
        const out = stats([ev('e1', 'pass', 3), ev('e2', 'pass', 3)],
            { e1: { status: 'rejected' } });
        assert.equal(out.alex.passes_attempted, 9);
    });

    test('a retyped event moves from one counter to the other', () => {
        const out = stats([ev('e1', 'pass', 3, { outcome: 'completed' })],
            { e1: { status: 'edited', type: 'tackle' } });
        assert.equal(out.alex.passes_attempted, 9);
        assert.equal(out.alex.passes_completed, 7);
        assert.equal(out.alex.tackles, 3);
    });

    test('an event reassigned to another player moves whole', () => {
        // The correction that matters most: it is how a coach fixes an identity
        // the cluster mapping got wrong, without redoing the mapping.
        const out = stats([ev('e1', 'tackle', 3)],
            { e1: { status: 'edited', playerId: 'sam' } });
        assert.equal(out.alex.tackles, 1);
        assert.equal(out.sam.tackles, 1);
    });

    test('retyped and reassigned at once lands under both corrections', () => {
        const out = stats([ev('e1', 'pass', 3)],
            { e1: { status: 'edited', type: 'carry', playerId: 'sam' } });
        assert.equal(out.alex.passes_attempted, 9);
        assert.equal(out.sam.carries, 2);
    });

    test('an edit that only renames the player it already was does nothing', () => {
        const out = stats([ev('e1', 'tackle', 3)],
            { e1: { status: 'edited', playerId: 'alex' } });
        assert.equal(out.alex.tackles, 2);
    });

    test('a retype into something nothing publishes just removes the original', () => {
        const out = stats([ev('e1', 'pass', 3)],
            { e1: { status: 'edited', type: 'duel' } });
        assert.equal(out.alex.passes_attempted, 9);
        assert.equal(out.alex.duels, undefined);
    });

    test('distance, speed and touches are untouched by any verdict', () => {
        // They come from the track, not from the event list. No verdict about
        // an event is a verdict about where a player ran.
        const out = stats(
            [ev('e1', 'pass', 3), ev('e2', 'carry', 3), ev('e3', 'tackle', 3)],
            { e1: { status: 'rejected' }, e2: { status: 'rejected' },
              e3: { status: 'rejected' } },
        );
        assert.equal(out.alex.distance_m, 8000);
        assert.equal(out.alex.top_speed_kmh, 27);
        assert.equal(out.alex.sprint_count, 12);
        assert.equal(out.alex.touches, 40);
    });

    test('a counter cannot be driven below zero', () => {
        const rejected = {};
        const events = [];
        for (let i = 0; i < 12; i += 1) {
            events.push(ev(`e${i}`, 'tackle', 3));
            rejected[`e${i}`] = { status: 'rejected' };
        }
        assert.equal(stats(events, rejected).alex.tackles, 0);
    });

    test('a shot is left to the ledger rather than counted twice', () => {
        // `correctedShotMarks` already decides a shot's fate. A second
        // subtraction here would take it off the total twice.
        const out = stats([ev('e1', 'shot', 3)], { e1: { status: 'rejected' } });
        assert.equal(out.alex.shots, undefined);
    });

    test('a correction for an unmapped figure is dropped, not invented', () => {
        const out = stats([ev('e1', 'pass', 77)], { e1: { status: 'rejected' } });
        assert.equal(out.alex.passes_attempted, 10);
    });

    test('a corrected player is marked as corrected', () => {
        // A number that silently moved between two visits looks like a bug.
        const out = stats([ev('e1', 'pass', 3)], { e1: { status: 'rejected' } });
        assert.equal(out.alex.reviewed, true);
        assert.equal(out.sam.reviewed, undefined);
    });
});

describe('a rejected shot leaves the map it was drawn on', () => {
    const mark = (id, xg) => ({ event_id: id, xg, video_s: 10 });

    test('a rejected shot is dropped rather than kept at zero', () => {
        // Unlike an unscorable header — which happened and cannot be scored —
        // a rejected shot did not happen, and a dot on a map claims it did.
        const rows = [{ id: 'a', counted: false }, { id: 'b', counted: true }];
        const out = report.correctedShotMarks([mark('a', 0.4), mark('b', 0.1)], rows);
        assert.deepEqual(out.map((m) => m.event_id), ['b']);
    });

    test('the count and the xG fall together, off the same list', () => {
        const stats = { shotMap: [mark('a', 0.4), mark('b', 0.1)], shots: 2, xg: 0.5 };
        const rows = [{ id: 'a', counted: false }, { id: 'b', counted: true }];
        const fields = report.cvReportFields(stats, null, rows);
        assert.equal(fields.cvShots, 1);
        assert.ok(Math.abs(fields.cvXg - 0.1) < 1e-9);
    });

    test('a player whose only shot was rejected is not handed it back', () => {
        // The fallback exists for reports written before shot maps did. An
        // emptied map is not an absent one.
        const stats = { shotMap: [mark('a', 0.4)], shots: 1, xg: 0.4 };
        const fields = report.cvReportFields(stats, null, [{ id: 'a', counted: false }]);
        assert.equal(fields.cvShots, 0);
        assert.equal(fields.cvXg, 0);
    });

    test('a report from before shot maps still reports its own total', () => {
        const fields = report.cvReportFields({ shots: 3, xg: 0.9 }, null, null);
        assert.equal(fields.cvShots, 3);
        assert.equal(fields.cvXg, 0.9);
    });

    test('headers are still corrected, not dropped', () => {
        const rows = [{ id: 'a', counted: true, header: true, xg: 0.12 }];
        const out = report.correctedShotMarks([mark('a', 0.4)], rows);
        assert.equal(out.length, 1);
        assert.equal(out[0].xg, 0.12);
        assert.equal(out[0].is_header, true);
    });

    test('a shot the ledger says nothing about is left alone', () => {
        const out = report.correctedShotMarks([mark('a', 0.4)], []);
        assert.deepEqual(out.map((m) => m.xg), [0.4]);
    });
});

// ------------------------------------------------- which moment is playing

describe('the mark the video is inside', () => {
    // Clock readings, in the order a strip would have been given them.
    const at = (...seconds) => seconds.map((clockS) => ({ clockS }));

    test('nothing is current before the first mark', () => {
        // A highlight on a moment that has not happened gives it away, which
        // is the opposite of what a strip is for.
        assert.equal(timeline.nowIndex(at(600, 1200), 300), -1);
    });

    test('a mark just passed is the current one', () => {
        assert.equal(timeline.nowIndex(at(600, 1200), 605), 0);
    });

    test('the mark is dropped once the video has moved on', () => {
        // 25 seconds is the window; 40 is past it.
        assert.equal(timeline.nowIndex(at(600), 640), -1);
    });

    test('a mark is still current well into the celebration', () => {
        assert.equal(timeline.nowIndex(at(600), 620), 0);
    });

    test('two marks a second apart resolve to the later one', () => {
        // Both qualify. The one the video has most recently passed is the one
        // a viewer is looking at.
        assert.equal(timeline.nowIndex(at(600, 601), 602), 1);
    });

    test('the answer indexes the array it was given, not a sorted copy', () => {
        // The strip draws marks in whatever order the page built them and the
        // list sorts its own; an index into somebody else's ordering would
        // light up the wrong row.
        assert.equal(timeline.nowIndex(at(1200, 600), 605), 1);
    });

    test('a mark exactly on the playhead counts', () => {
        assert.equal(timeline.nowIndex(at(600), 600), 0);
    });

    test('a position nobody has reported is not a position at zero', () => {
        // `currentTime` starts null, and null must not light up the kick-off.
        assert.equal(timeline.nowIndex(at(0, 600), null), -1);
        assert.equal(timeline.nowIndex(at(0, 600), undefined), -1);
    });

    test('marks with no clock reading are skipped rather than counted as zero', () => {
        const marks = [{ clockS: null }, { clockS: 600 }];
        assert.equal(timeline.nowIndex(marks, 605), 1);
    });

    test('an empty strip has nothing current', () => {
        assert.equal(timeline.nowIndex([], 605), -1);
        assert.equal(timeline.nowIndex(null, 605), -1);
    });

    test('the window is adjustable, and a zero window means exactly on it', () => {
        assert.equal(timeline.nowIndex(at(600), 601, 0), -1);
        assert.equal(timeline.nowIndex(at(600), 600, 0), 0);
    });
});

describe('taking a player off, and what that costs', () => {
    const footprint = (over = {}) => ({
        matches: [
            { id: 'm1', label: 'Linden', hasRoster: true, hasReport: true, clusters: 2 },
            { id: 'm2', label: 'Oak', hasRoster: true, hasReport: false, clusters: 0 },
            { id: 'm3', label: 'Pine', hasRoster: false, hasReport: false, clusters: 0 },
        ],
        hasInvite: true,
        ...over,
    });
    const said = (f) => report.erasureNote(f).lines.join(' | ');

    test('a match they were only named in still counts', () => {
        // m2 has a roster entry and no report. Their name is in it, so it is a
        // match the erase reaches, and a coach told "1 match" when the answer
        // is 2 has been given a number they cannot act on.
        assert.equal(report.erasureNote(footprint()).matchCount, 2);
        assert.equal(report.erasureNote(footprint()).reportCount, 1);
    });

    test('a match they never appeared in is not counted', () => {
        assert.ok(!said(footprint()).includes('3 matches'));
    });

    test('having played nothing yet is an ordinary answer', () => {
        const fresh = report.erasureNote({ matches: [], hasInvite: false });
        assert.equal(fresh.matchCount, 0);
        assert.match(fresh.lines[0], /nothing but their squad entry/);
    });

    test('the tracked figures are named, because they are photographs', () => {
        // The mapping is what ties a crop cut out of the footage to a person.
        assert.match(said(footprint()), /2 tracked figures/);
        assert.match(said(footprint()), /no longer tied to a name/);
    });

    test('no video mapping means nothing is said about figures', () => {
        const noCv = footprint({
            matches: [{ id: 'm1', hasRoster: true, hasReport: true, clusters: 0 }],
        });
        assert.ok(!said(noCv).includes('tracked figure'));
    });

    test('the email is named as the last place it is stored', () => {
        assert.match(said(footprint()), /invitation goes/);
        assert.ok(!said(footprint({ hasInvite: false })).includes('invitation goes'));
    });

    test('what stays is said whenever they were in a match', () => {
        // A coach never told the log survives has been told the erase was
        // total, and it is not.
        assert.match(said(footprint()), /substitutions stay in the match log/);
    });

    test('and is not said when nothing of theirs stays', () => {
        // For a player added last week and removed today the erase really does
        // take everything, and a caveat about what is kept would be a caveat
        // about nothing.
        assert.ok(!said({ matches: [], hasInvite: false }).includes('substitutions stay'));
    });

    test('one match reads as one match', () => {
        const one = footprint({
            matches: [{ id: 'm1', hasRoster: true, hasReport: true, clusters: 1 }],
        });
        assert.match(said(one), /1 match\b/);
        assert.match(said(one), /1 published report is deleted/);
        assert.match(said(one), /1 tracked figure /);
    });

    test('a missing footprint is not a crash', () => {
        assert.ok(report.erasureNote().lines.length);
        assert.ok(report.erasureNote({}).lines.length);
    });
});

describe('mappingWithout', () => {
    const mapping = () => ({ 0: 'p1', 1: 'p2', 2: 'p1', 3: '__not_a_player' });

    test('every cluster pointing at them is dropped', () => {
        const out = report.mappingWithout(mapping(), 'p1');
        assert.deepEqual(Object.keys(out).sort(), ['1', '3']);
    });

    test('nobody else is re-attributed', () => {
        // The failure that would matter: erasing one student and silently
        // moving their figures onto another.
        assert.equal(report.mappingWithout(mapping(), 'p1')['1'], 'p2');
    });

    test('it does not mutate what it was given', () => {
        // The caller holds this in `state`. Mutating it would leave the screen
        // agreeing with a write that has not happened yet.
        const before = mapping();
        report.mappingWithout(before, 'p1');
        assert.equal(Object.keys(before).length, 4);
    });

    test('a player with no figures leaves the mapping alone', () => {
        assert.deepEqual(report.mappingWithout(mapping(), 'p9'), mapping());
    });

    test('no mapping at all is an empty one', () => {
        assert.deepEqual(report.mappingWithout(null, 'p1'), {});
        assert.deepEqual(report.mappingWithout(undefined, 'p1'), {});
    });
});

describe('where a player plays', () => {
  /**
   * The field exists for one reason: a goalkeeper covers a fraction of the
   * ground an outfielder does, and the player table used to rank them against
   * each other down one column with nothing saying so.
   *
   * The behaviour worth pinning is the *absence* case. Most squads will never
   * fill this in, and a coach who has not must get back exactly the table they
   * had — same players, same order, no headings for a field they have never
   * seen.
   */

  const squad = (...positions) => positions.map((position, i) => ({
    id: `p${i}`, playerName: `Player ${i}`, position, goals: 0, minutesPlayed: 90,
  }));

  const titles = (groups) => groups.map((g) => g.title);
  const names = (groups) => groups.flatMap((g) => g.players.map((p) => p.id));

  describe('reading a position', () => {
    test('the four it knows', () => {
      for (const id of ['gk', 'def', 'mid', 'fwd']) {
        assert.equal(report.positionOf(id), id);
      }
    });

    test('anything else is unset, not an error and not a guess', () => {
      // A document written before this field existed, a typo, or a value from
      // some future vocabulary. All of them mean "nobody has said".
      for (const bad of [null, undefined, '', 'goalkeeper', 'GK', 'striker', 7, {}]) {
        assert.equal(report.positionOf(bad), null);
      }
    });

    test('a label only for the ones it knows', () => {
      assert.equal(report.positionLabel('mid'), 'Midfielder');
      assert.equal(report.positionLabel('winger'), null);
      assert.equal(report.positionLabel(null), null);
    });

    test('the keeper test does not fire on an unset player', () => {
      assert.equal(report.isKeeper({ position: 'gk' }), true);
      assert.equal(report.isKeeper({ position: 'def' }), false);
      assert.equal(report.isKeeper({ position: null }), false);
      assert.equal(report.isKeeper({}), false);
      assert.equal(report.isKeeper(null), false);
    });
  });

  describe('grouping a squad', () => {
    test('nobody has a position, so there are no headings at all', () => {
      const groups = report.groupByPosition(squad(null, null, null));
      assert.equal(groups.length, 1);
      assert.equal(groups[0].title, null);
      assert.deepEqual(names(groups), ['p0', 'p1', 'p2']);
    });

    test('an unrecognised value counts as nobody having said', () => {
      // Guards the case a stray string in one document silently switches the
      // whole table into a grouped layout with one bogus heading.
      const groups = report.groupByPosition(squad('striker', 'GK'));
      assert.equal(groups.length, 1);
      assert.equal(groups[0].title, null);
    });

    test('one position set is enough to group the whole table', () => {
      const groups = report.groupByPosition(squad('gk', null, null));
      assert.deepEqual(titles(groups), ['Goalkeepers', 'No position set']);
    });

    test('team-sheet order, keepers first, and empty lines dropped', () => {
      const groups = report.groupByPosition(squad('fwd', 'gk', 'mid'));
      assert.deepEqual(titles(groups), ['Goalkeepers', 'Midfielders', 'Forwards']);
    });

    test('the unset go last, under a heading that is not a line of the team', () => {
      const groups = report.groupByPosition(squad('mid', null));
      assert.equal(groups.at(-1).title, 'No position set');
      assert.deepEqual(groups.at(-1).players.map((p) => p.id), ['p1']);
    });

    test('the involvement order survives inside a line', () => {
      // The point of grouping is to add a heading, not to take the ranking
      // away — a coach still wants the standouts at the top of each line.
      const players = [
        { id: 'quiet', position: 'mid', goals: 0 },
        { id: 'loud', position: 'mid', goals: 3 },
        { id: 'keeper', position: 'gk', goals: 0 },
      ];
      const groups = report.groupByPosition(players, (a, b) => b.goals - a.goals);
      assert.deepEqual(names(groups), ['keeper', 'loud', 'quiet']);
    });

    test('everyone survives the grouping', () => {
      // A dropped player is the failure that would go unnoticed longest: the
      // table still looks like a table.
      const players = squad('gk', 'def', 'def', null, 'fwd', 'mid');
      const groups = report.groupByPosition(players);
      assert.equal(names(groups).length, players.length);
      assert.deepEqual(new Set(names(groups)).size, players.length);
    });

    test('an empty squad, and no squad at all', () => {
      assert.deepEqual(report.groupByPosition([]), [{ id: null, title: null, players: [] }]);
      assert.deepEqual(report.groupByPosition(null), [{ id: null, title: null, players: [] }]);
    });

    test('it does not reorder what it was given', () => {
      // `state.match.stats.players` is held across re-renders and read by the
      // coverage note; sorting it in place would reorder somebody else's list.
      const players = squad('fwd', 'gk');
      const before = players.map((p) => p.id);
      report.groupByPosition(players, (a, b) => a.id.localeCompare(b.id));
      assert.deepEqual(players.map((p) => p.id), before);
    });
  });
});

describe('the order a reviewer works in', () => {
  /**
   * The Testing Strategy has said since it was written that this tool "sorts by
   * lowest confidence first, so a human's limited review time goes to what's
   * likely wrong instead of skimming everything uniformly". It never did. The
   * feed is chronological, and chronological is not a bug — every row seeks the
   * video, so match order is one forward scrub and doubt order is a jump across
   * the half per verdict.
   *
   * So it is a choice, and what these pin is that choosing it changes the order
   * and nothing else: same rows, same count, and the tagged record not shuffled
   * into a ranking it has no place in.
   */

  const vid = (id, confidence, clockS) => ({
    source: report.FROM_VIDEO, clockS, event: { id, confidence, type: 'pass' },
  });
  const tag = (id, clockS) => ({ source: report.FROM_TAGGED, clockS, id, type: 'goal' });

  const feed = () => [
    vid('a', 0.9, 10),
    tag('t1', 20),
    vid('b', 0.2, 30),
    vid('c', 0.55, 40),
    tag('t2', 50),
  ];
  const ids = (list) => list.map((i) => i.event?.id ?? i.id);

  test('match order is the default and leaves the feed alone', () => {
    assert.deepEqual(ids(report.orderFeed(feed())), ['a', 't1', 'b', 'c', 't2']);
    assert.deepEqual(
      ids(report.orderFeed(feed(), report.BY_CLOCK)), ['a', 't1', 'b', 'c', 't2'],
    );
  });

  test('doubt order puts the least sure candidate first', () => {
    assert.deepEqual(
      ids(report.orderFeed(feed(), report.BY_DOUBT)).slice(0, 3), ['b', 'c', 'a'],
    );
  });

  test('the tagged record is not ranked, it is appended in its own order', () => {
    // A tap carries no confidence: the log is a person, not a detector. Sorting
    // it into a doubt ranking would invent a certainty nobody recorded.
    const out = ids(report.orderFeed(feed(), report.BY_DOUBT));
    assert.deepEqual(out.slice(-2), ['t1', 't2']);
  });

  test('a candidate with no confidence sinks rather than leading', () => {
    // Missing is not zero. An event the pipeline attached no confidence to is
    // not the one it was least sure about.
    const list = [vid('scored', 0.4, 10), vid('blank', null, 20)];
    assert.deepEqual(ids(report.orderFeed(list, report.BY_DOUBT)), ['scored', 'blank']);
  });

  test('reordering never adds or drops a row', () => {
    const before = feed();
    const after = report.orderFeed(before, report.BY_DOUBT);
    assert.equal(after.length, before.length);
    assert.deepEqual(new Set(ids(after)), new Set(ids(before)));
  });

  test('it does not reorder what it was given', () => {
    // `visibleItems` builds this fresh each time, but the day it does not, an
    // in-place sort would reorder the caller's list behind its back.
    const before = feed();
    const order = ids(before);
    report.orderFeed(before, report.BY_DOUBT);
    assert.deepEqual(ids(before), order);
  });

  test('an empty feed, and no feed at all', () => {
    assert.deepEqual(report.orderFeed([], report.BY_DOUBT), []);
    assert.deepEqual(report.orderFeed(null, report.BY_DOUBT), []);
  });

  describe('what it costs the numbers', () => {
    test('doubt order says the sample is deliberately the hard cases', () => {
      const text = report.orderCaveat(report.BY_DOUBT, 12);
      assert.match(text, /least sure first/);
      // The claim that matters: not an average.
      assert.match(text, /floor, not an average/);
      assert.match(text, /hardest 12/);
    });

    test('match order says nothing — an even sample needs no caveat', () => {
      assert.equal(report.orderCaveat(report.BY_CLOCK, 12), '');
    });

    test('nothing checked yet, nothing to qualify', () => {
      // The caveat is about a sample. Before there is one it would be a warning
      // about numbers that do not exist.
      assert.equal(report.orderCaveat(report.BY_DOUBT, 0), '');
    });
  });
});

describe('the football either side of a substitution', () => {
  // A clock with a real interval in it, because half of what this feature has
  // to get right is that the interval exists.
  const OFFSET_S = 40;
  const BREAK_S = 13 * 60;
  const clock = () => report.matchClockMap({
    videoOffsetS: OFFSET_S,
    secondHalfVideoS: OFFSET_S + 45 * 60 + BREAK_S,
    secondHalfClockS: 45 * 60,
  });
  const MATCH_END_S = 92 * 60;

  const swap = (name, offS) => ({ playerName: name, stints: [{ inS: 0, outS: offS }] });
  const on = (name, inS) => ({ playerName: name, stints: [{ inS, outS: null }] });

  /** An even stream of on-ball events, with our share settable per stretch. */
  const stream = (shares, { stepS = 4, endS = MATCH_END_S } = {}) => {
    const map = clock();
    const events = [];
    let n = 0;
    for (let clockS = 10; clockS < endS; clockS += stepS) {
      n += 1;
      const share = shares(clockS);
      events.push({
        id: `e${n}`,
        type: 'pass',
        timestampS: map.toVideo(clockS),
        team: ((n * share) % 1) < share ? 'team_a' : 'team_b',
        inPlay: true,
      });
    }
    return events;
  };

  describe('finding the changes', () => {
    test('a kick-off and a final whistle are not changes', () => {
      // Everyone has an inS; almost everyone has an outS. Neither is a
      // substitution, and a block that listed them would open with eleven rows
      // saying nothing happened.
      const roster = [
        { playerName: 'Ana', stints: [{ inS: 0, outS: MATCH_END_S }] },
        { playerName: 'Ben', stints: [{ inS: 0, outS: null }] },
      ];
      assert.deepEqual(
        report.substitutionChanges(roster, { matchEndS: MATCH_END_S }), [],
      );
    });

    test('a double change at one stoppage is one change', () => {
      const roster = [
        swap('Ana', 60 * 60), on('Ben', 60 * 60),
        swap('Cy', 60 * 60 + 25), on('Di', 60 * 60 + 25),
      ];
      const changes = report.substitutionChanges(roster, { matchEndS: MATCH_END_S });
      assert.equal(changes.length, 1);
      assert.deepEqual(changes[0].on.sort(), ['Ben', 'Di']);
      assert.deepEqual(changes[0].off.sort(), ['Ana', 'Cy']);
    });

    test('a match nobody tagged still has substitutions in it', () => {
      // matchEndS comes off the tag log, so it is 0 on a match with no log —
      // and 0 taken as a final whistle is a whistle before kick-off. Every
      // player who came off left "after" it, and the block rendered four
      // changes where somebody came on and nobody went off.
      const roster = [swap('Ana', 60 * 60), on('Ben', 60 * 60)];
      const changes = report.substitutionChanges(roster, { matchEndS: 0 });
      assert.equal(changes.length, 1);
      assert.deepEqual(changes[0].off, ['Ana']);
      assert.deepEqual(changes[0].on, ['Ben']);

      // And the windows that hang off it are not clipped to nothing either.
      const out = report.substitutionWindows(roster, stream(() => 0.5), {
        clock: clock(), matchEndS: 0,
      });
      assert.equal(out.rows[0].scored, true);
      assert.equal(out.rows[0].spanS, 600);
    });

    test('a trickle of changes cannot chain into one group', () => {
      // Each move is inside CHANGE_GROUP_S of the one before it. Measured from
      // the group's first move rather than its latest, five of them are three
      // groups and not one that swallows six minutes of football.
      const roster = [0, 80, 160, 240, 320].map(
        (d, i) => on(`P${i}`, 30 * 60 + d),
      );
      const changes = report.substitutionChanges(roster, { matchEndS: MATCH_END_S });
      assert.equal(changes.length, 3);
    });
  });

  describe('what the windows are cut against', () => {
    const windowsFor = (roster, events, extra = {}) => report.substitutionWindows(
      roster, events,
      { clock: clock(), matchEndS: MATCH_END_S, ...extra },
    );

    test('nothing at all when nobody was substituted', () => {
      // Null, not an empty list: the caller hides the block rather than putting
      // a heading over a blank.
      assert.equal(
        windowsFor([{ playerName: 'Ana', stints: [{ inS: 0, outS: null }] }], []),
        null,
      );
    });

    test('both sides are cut to the shorter of the two', () => {
      // 62:00 has ten clear minutes before it and only six after, because of
      // the change at 68:00. Nine minutes against six would have more of
      // everything in it for a reason that is not football.
      const roster = [
        swap('Ana', 62 * 60), on('Ben', 62 * 60),
        swap('Cy', 68 * 60), on('Di', 68 * 60),
      ];
      const out = windowsFor(roster, stream(() => 0.5));
      assert.equal(out.rows[0].spanS, 6 * 60);
      assert.equal(
        out.rows[0].before.us + out.rows[0].before.them,
        out.rows[0].after.us + out.rows[0].after.them,
      );
    });

    test('a window never crosses half-time', () => {
      // Ten minutes either side of 41:00 would compare the end of one half
      // against the start of the next, with the oranges in between.
      const roster = [swap('Ana', 41 * 60), on('Ben', 41 * 60)];
      const out = windowsFor(roster, stream(() => 0.5));
      assert.equal(out.rows[0].spanS, 4 * 60);
      assert.equal(out.rows[0].scored, true);
    });

    test('a change at the break is listed and never scored', () => {
      const roster = [swap('Ana', 45 * 60), on('Ben', 45 * 60)];
      const out = windowsFor(roster, stream(() => 0.5));
      assert.equal(out.rows[0].atBreak, true);
      assert.equal(out.rows[0].scored, false);
      assert.equal(out.rows[0].reason, 'break');
      assert.equal(out.atBreak, 1);
      assert.match(report.substitutionNote(out), /team talk/);
    });

    test('the processed footage is the outer limit', () => {
      // The clip stops at 70 minutes. Ten minutes after a change on 68:00 is
      // two minutes of football and eight of nothing having been looked at,
      // which on screen reads as a team that stopped playing.
      const roster = [swap('Ana', 68 * 60), on('Ben', 68 * 60)];
      const map = clock();
      const out = windowsFor(roster, stream(() => 0.5), {
        window: { start_s: 0, end_s: map.toVideo(70 * 60) },
      });
      assert.equal(out.rows[0].scored, false);
      assert.equal(out.rows[0].reason, 'footage');
    });

    test('a clip that runs to the whistle blames the whistle, not the clip', () => {
      // Both limits land on the same second. "The processed footage runs out"
      // would send a coach looking for a problem with their video.
      const roster = [swap('Ana', 90 * 60), on('Ben', 90 * 60)];
      const map = clock();
      const out = windowsFor(roster, stream(() => 0.5), {
        window: { start_s: 0, end_s: map.toVideo(MATCH_END_S) },
      });
      assert.equal(out.rows[0].reason, 'edge');
    });

    test('two changes close together measure neither of them', () => {
      const roster = [
        swap('Ana', 60 * 60), on('Ben', 60 * 60),
        swap('Cy', 62 * 60), on('Di', 62 * 60),
      ];
      const out = windowsFor(roster, stream(() => 0.5));
      assert.deepEqual(out.rows.map((r) => r.reason), ['crowded', 'crowded']);
      assert.equal(out.scored, 0);
    });

    test('dead-ball events are left out of the share', () => {
      const roster = [swap('Ana', 60 * 60), on('Ben', 60 * 60)];
      const live = stream(() => 0.5);
      const dead = live.map(
        (e) => ({ ...e, id: `${e.id}-dead`, team: 'team_b', inPlay: false }),
      );
      const out = windowsFor(roster, [...live, ...dead]);
      const plain = windowsFor(roster, live);
      assert.equal(out.rows[0].shareBefore, plain.rows[0].shareBefore);
    });
  });

  describe('nothing is scored on a clock that cannot place it', () => {
    test('no second-half anchor, no figures', () => {
      // Second-half stints are match minutes and second-half events are video
      // minutes. Without the anchor the offset relates them wrongly by the
      // whole interval, in the direction that slides a window quietly off the
      // football it claims to describe.
      const roster = [swap('Ana', 60 * 60), on('Ben', 60 * 60)];
      const out = report.substitutionWindows(roster, stream(() => 0.5), {
        clock: report.matchClockMap({ videoOffsetS: OFFSET_S }),
        matchEndS: MATCH_END_S,
      });
      assert.equal(out.placeable, false);
      assert.equal(out.rows[0].reason, 'clock');
      assert.match(report.substitutionNote(out), /second-half kick-off/);
    });

    test('a first-half clip needs no anchor', () => {
      // There is no interval inside it to be wrong about.
      const roster = [swap('Ana', 25 * 60), on('Ben', 25 * 60)];
      const out = report.substitutionWindows(
        roster, stream(() => 0.5, { endS: 45 * 60 }),
        {
          clock: report.matchClockMap({ videoOffsetS: OFFSET_S }),
          matchEndS: 45 * 60,
          period: report.FIRST_HALF,
        },
      );
      assert.equal(out.placeable, true);
      assert.equal(out.rows[0].scored, true);
    });
  });

  describe('whether a swing is a swing', () => {
    test('a ten-point move off a hundred events is a draw', () => {
      // Measured, not asserted: at an even split it takes about fourteen
      // points before a hundred events a window can tell the difference.
      assert.equal(
        report.shareShifted({ us: 45, them: 55 }, { us: 55, them: 45 }), false,
      );
    });

    test('twenty-two points off a hundred and fifty is not', () => {
      assert.equal(
        report.shareShifted({ us: 69, them: 81 }, { us: 102, them: 48 }), true,
      );
    });

    test('an empty window decides nothing', () => {
      assert.equal(
        report.shareShifted({ us: 0, them: 0 }, { us: 40, them: 40 }), false,
      );
    });

    test('the read only fires on a swing that survived', () => {
      const roster = [swap('Ana', 60 * 60), on('Ben', 60 * 60)];
      const flat = report.substitutionWindows(roster, stream(() => 0.5), {
        clock: clock(), matchEndS: MATCH_END_S,
      });
      assert.equal(flat.rows[0].tentative, true);
      assert.equal(report.substitutionRead(flat), null);

      const swung = report.substitutionWindows(
        roster, stream((c) => (c < 60 * 60 ? 0.4 : 0.68)),
        { clock: clock(), matchEndS: MATCH_END_S },
      );
      assert.equal(swung.rows[0].tentative, false);
      const read = report.substitutionRead(swung);
      assert.match(read.title, /Ben/);
      // Never as a consequence. The change and the reason for it arrive
      // together and nothing here can pull them apart.
      assert.match(read.detail, /not because of it/);
    });
  });

  describe('what the note is obliged to say', () => {
    test('the causal caveat is not conditional', () => {
      const roster = [swap('Ana', 60 * 60), on('Ben', 60 * 60)];
      for (const events of [[], stream(() => 0.5)]) {
        const out = report.substitutionWindows(roster, events, {
          clock: clock(), matchEndS: MATCH_END_S,
        });
        assert.match(
          report.substitutionNote(out), /does not say the substitution did it/,
        );
      }
    });

    test('a truncated event list says the sample is uneven', () => {
      const roster = [swap('Ana', 60 * 60), on('Ben', 60 * 60)];
      const out = report.substitutionWindows(roster, stream(() => 0.5), {
        clock: clock(), matchEndS: MATCH_END_S, truncated: true,
      });
      assert.match(report.substitutionNote(out), /truncated/);
    });
  });

  describe('the fixture the preview is drawn from', () => {
    test('one of each thing the block can say', () => {
      const fixture = sample.sampleSubClock();
      const out = report.substitutionWindows(
        sample.sampleSubRoster(), sample.sampleSubEvents(),
        {
          clock: report.matchClockMap(fixture),
          matchEndS: fixture.matchEndS,
          window: fixture.window,
        },
      );
      assert.deepEqual(
        out.rows.map((r) => (r.scored ? (r.tentative ? 'draw' : 'read') : r.reason)),
        ['break', 'read', 'draw', 'edge'],
      );
      // The last row is the double change, grouped.
      assert.equal(out.rows[3].on.length, 2);
    });
  });
});

describe('a shot with nowhere to go', () => {
  // `Number(null) || 0` put a positionless shot at (0, 0) — the corner flag —
  // and the dot that came out was indistinguishable from a real one. Nothing
  // produces this today, because shot_marks in cv/report_json.py drops
  // positionless shots, but a picture a coach reads as measurement should not
  // be able to invent a point at all.
  test('a mark with no position is not placeable', () => {
    assert.equal(markMod.placeable({ x_m: 30, y_m: 12 }), true);
    assert.equal(markMod.placeable({ x_m: 0, y_m: 0 }), true);
    assert.equal(markMod.placeable({ x_m: null, y_m: 12 }), false);
    assert.equal(markMod.placeable({ x_m: 30 }), false);
    assert.equal(markMod.placeable({ x_m: 'wide', y_m: 12 }), false);
    assert.equal(markMod.placeable(null), false);
    // The trap this whole guard is about, and the one the first version of it
    // fell into: Number(null) is 0, and 0 is a finite number on a real pitch.
    assert.equal(Number.isFinite(Number(null)), true);
    assert.equal(markMod.placeable({ x_m: undefined, y_m: 12 }), false);
    assert.equal(markMod.placeable({ x_m: NaN, y_m: 12 }), false);
  });

  test('the summary counts what it could not place', () => {
    // The count above the map still includes it — a shot that happened
    // happened — so the number of dots being short is the only symptom, and
    // this is what a caller would have to read to say so.
    const summary = markMod.shotSummary([
      { x_m: 30, y_m: 12, xg: 0.1, on_target: true },
      { x_m: null, y_m: null, xg: 0.2 },
    ]);
    assert.equal(summary.shots, 2);
    assert.equal(summary.unplaced, 1);
  });
});

describe('a whistle nobody tagged', () => {
  // The bug this whole section exists for: `setLineup` writes a starter's
  // {inS: 0, outS: null} when the lineup is set, hours before kick-off. So a
  // match that was filmed and processed but never tagged has a full roster of
  // open stints and an empty log — and every one of those players used to be
  // recorded as having played nought minutes.
  const starter = [{ inS: 0, outS: null }];
  const substituted = [{ inS: 0, outS: 60 * 60 }];

  describe('finding the end of the match', () => {
    test('a tagged full time is the end of the match', () => {
      assert.deepEqual(
        report.whistleFrom([
          { matchClockS: 2700, type: 'halftime' },
          { matchClockS: 5460, type: 'full_time' },
        ]),
        { matchEndS: 5460, source: report.FROM_WHISTLE },
      );
    });

    test('with no whistle, the last thing anybody tapped — and it says so', () => {
      // A real underestimate, in one direction only, which is what makes it
      // usable and makes carrying the provenance compulsory.
      assert.deepEqual(
        report.whistleFrom([
          { matchClockS: 600, type: 'goal' },
          { matchClockS: 4080, type: 'corner' },
        ]),
        { matchEndS: 4080, source: report.FROM_LAST_TAG },
      );
    });

    test('an empty log answers null, not zero', () => {
      assert.deepEqual(
        report.whistleFrom([]), { matchEndS: null, source: null },
      );
    });

    test('entries with no clock reading cannot date a whistle', () => {
      // A queued entry that never got a clock is not evidence the match ended
      // at second zero.
      assert.deepEqual(
        report.whistleFrom([{ type: 'goal' }, { type: 'corner' }]),
        { matchEndS: null, source: null },
      );
    });
  });

  describe('minutes, when the end is unknown', () => {
    test('a starter still on the pitch has no minutes', () => {
      assert.equal(report.minutesFrom(starter, null), null);
    });

    test('a player who came off has all of theirs', () => {
      // Partial knowledge is knowledge. Sixty minutes were played whether or
      // not anybody wrote down when the match finished, and blanking this row
      // would throw away most of a substituted squad to be safe.
      assert.equal(report.minutesFrom(substituted, null), 60);
    });

    test('an unused substitute is a real zero', () => {
      // No stints at all is not the same absence: they were never on, and that
      // is a fact the empty list states rather than withholds.
      assert.equal(report.minutesFrom([], null), 0);
    });

    test('one open stint among several is enough to make the total unknown', () => {
      assert.equal(
        report.minutesFrom([{ inS: 0, outS: 1200 }, { inS: 3600, outS: null }], null),
        null,
      );
    });

    test('with a whistle, everything is countable again', () => {
      assert.equal(report.minutesFrom(starter, 5400), 90);
      assert.equal(report.minutesFrom(substituted, 5400), 60);
    });
  });

  describe('what a published report says about its own minutes', () => {
    test('absent counts as known', () => {
      // Every report published before `minutesKnown` existed carries real
      // minutes and no flag. Reading that absence as "unknown" would blank
      // whole seasons to fix a handful of matches.
      assert.equal(report.knownMinutes({ minutesPlayed: 90 }), true);
      assert.equal(report.knownMinutes({ minutesPlayed: 90, minutesKnown: true }), true);
      assert.equal(report.knownMinutes({ minutesPlayed: 0, minutesKnown: false }), false);
    });
  });

  describe('the sentence under the column', () => {
    test('a tagged whistle needs no sentence', () => {
      assert.equal(report.minutesNote(report.FROM_WHISTLE), null);
    });

    test('no log at all says the number is missing, not nought', () => {
      const note = report.minutesNote(null);
      assert.match(note, /Nobody tagged this match/);
      assert.match(note, /not nought minutes, no answer/);
    });

    test('no whistle says which direction the error runs', () => {
      // An underestimate presented without its direction is just a wrong
      // number, and this one is wrong the same way every time.
      assert.match(report.minutesNote(report.FROM_LAST_TAG), /short by however long/);
    });

    test('a match still being played has no whistle to have missed', () => {
      // Found in the browser at 45:30 of a tagged first half. The status was
      // "halftime" and the note read "nobody tagged the final whistle", which
      // is an accusation about something the coach has not had the chance to do
      // yet — and it would have fired at every half-time of every match.
      assert.equal(report.minutesNote(report.FROM_LAST_TAG, { over: false }), null);
      assert.notEqual(report.minutesNote(report.FROM_LAST_TAG, { over: true }), null);
    });

    test('a tablet that has recorded nothing is worth saying mid-match', () => {
      // Not gated the same way. By half-time an empty log is a problem the
      // coach can still do something about.
      assert.match(report.minutesNote(null, { over: false }), /Nobody tagged this match/);
    });

    test('the player reads it in the second person', () => {
      assert.match(
        report.minutesNote(report.FROM_LAST_TAG, { second: true }), /you were still on/,
      );
      // And grammatically. The first attempt at this branch substituted a
      // pronoun into a sentence built for a noun and produced "no minutes for
      // you still on at the end".
      assert.match(
        report.minutesNote(null, { second: true }),
        /no minutes for you if you were still on at the end/,
      );
      assert.match(report.minutesNote(null), /no minutes for anyone still on/);
    });
  });
});

describe('what binning the pitch into cells costs', () => {
  // The grid arrives 12 x 8 over a 105 x 68 m pitch, so a cell is 8.75 m along
  // the pitch — twice the gap between a defender and a midfielder standing
  // apart. Every figure in the shape block is read off that grid, so the size
  // of the error it introduces is not a detail; it decides whether any of it
  // can be reported in metres at all.
  //
  // These numbers are the ones written into `assets/heatmap.js`. They are here
  // so that a change to the binning, the correction, or the grid's default
  // shape fails a test rather than quietly widening the error under a docstring
  // that still claims the old one.

  const L = 105;

  /**
   * Occupancy laid down as a Gaussian along the pitch and then binned, next to
   * the same distribution measured unbinned.
   *
   * Quadrature rather than sampled points, so the comparison isolates the one
   * thing being measured — the binning — instead of mixing in the noise of a
   * random draw. Truncated at the goal lines, because a real player's occupancy
   * is, and the truncation is half of what the tails of this table are about.
   */
  const laid = (meanM, spreadM, cols = 12, rows = 8) => {
    const values = new Array(cols * rows).fill(0);
    const cellW = L / cols;
    const steps = cols * 800;
    let total = 0;
    let sumX = 0;
    let sumXX = 0;

    for (let i = 0; i < steps; i += 1) {
      const xM = ((i + 0.5) / steps) * L;
      const share = Math.exp(-0.5 * ((xM - meanM) / spreadM) ** 2);
      // One lane across the pitch: the y axis plays no part in either figure.
      values[Math.min(cols - 1, Math.floor(xM / cellW)) * rows + 3] += share;
      total += share;
      sumX += share * xM;
      sumXX += share * xM * xM;
    }

    const trueMeanM = sumX / total;
    return {
      grid: { cols, rows, values: values.map((v) => v / total) },
      trueMeanM,
      trueSpreadM: Math.sqrt(sumXX / total - trueMeanM ** 2),
    };
  };

  const sweep = (spreads, from, to) => {
    let worst = 0;
    const all = [];
    for (const spreadM of spreads) {
      for (let meanM = from; meanM <= to; meanM += 0.25) {
        const { grid, trueMeanM } = laid(meanM, spreadM);
        const off = Math.abs(heatmap.gridCentroid(grid).xM - trueMeanM);
        all.push(off);
        worst = Math.max(worst, off);
      }
    }
    all.sort((a, b) => a - b);
    return { worst, median: all[all.length >> 1] };
  };

  test('out on the pitch the binned middle is the middle', () => {
    // Spreads from a full-back who holds a line to a midfielder who covers
    // half the pitch, everywhere between the two penalty areas.
    const { worst, median } = sweep([4, 5, 6, 8, 10, 12, 16, 20], 8, 97);
    assert.ok(worst < 0.25, `worst ${worst}`);
    assert.ok(median < 0.02, `median ${median}`);
  });

  test('the error is worst on the one row no verdict is drawn from', () => {
    // Against a goal line the pitch truncates the distribution and the cell it
    // is truncated in stops being symmetric, which is where the whole error
    // lives. Twice the outfield figure — and the only player who averages four
    // metres off their own goal line is the keeper, who appears in the rows and
    // never in a remark.
    const { worst } = sweep([4, 5, 6, 8], 4, 7);
    assert.ok(worst > 0.3 && worst < 0.5, `worst ${worst}`);
  });

  test('somebody who never moved is the case it cannot do', () => {
    // 2.3 m out at a 1 m spread. A footballer does not have a 1 m spread; a
    // corner flag does. Recorded because the failure is real and because it
    // shrinks fast — by 3 m of spread it is back under a third of a metre.
    const tightest = (spreadM) => {
      let worst = 0;
      for (let meanM = 20; meanM <= 85; meanM += 0.25) {
        const { grid, trueMeanM } = laid(meanM, spreadM);
        worst = Math.max(worst, Math.abs(heatmap.gridCentroid(grid).xM - trueMeanM));
      }
      return worst;
    };
    assert.ok(tightest(1) > 2.2 && tightest(1) < 2.4);
    assert.ok(tightest(2) < 1.0);
    assert.ok(tightest(3) < 0.3);
  });

  test('Sheppard\'s correction gives the band back', () => {
    // Averaged over where the cell edges fall, which is what the correction is
    // derived for. Without it every one of these reads high, and a tight player
    // would be handed a wider uncertainty than they earned.
    const across = (spreadM) => {
      let sum = 0;
      let n = 0;
      for (let meanM = 48.125; meanM < 48.125 + 8.75; meanM += 0.125) {
        sum += heatmap.gridSpread(laid(meanM, spreadM).grid);
        n += 1;
      }
      return sum / n;
    };
    for (const spreadM of [4, 5, 6, 8, 12]) {
      assert.ok(Math.abs(across(spreadM) - spreadM) < 0.01, `${spreadM} -> ${across(spreadM)}`);
    }
  });

  test('one player at one phase does worse than the average of all of them', () => {
    // The figure the docstring used to give was the phase-averaged one, and a
    // single player is not an average over phases. A true 4 m reads anywhere in
    // 3.85-4.14 depending on where the grid's lines happen to fall, and a true
    // 3 m - a third of a cell - breaks down completely.
    const at = (meanM, spreadM) => heatmap.gridSpread(laid(meanM, spreadM).grid);
    assert.ok(Math.abs(at(48.125, 4) - 3.85) < 0.02);
    assert.ok(Math.abs(at(52.5, 4) - 4.14) < 0.02);
    assert.ok(at(48.125, 3) < 2.3);
    assert.ok(at(52.5, 3) > 3.6);
  });
});

describe('turning a grid into a place on the pitch', () => {
  const grid = (cols, rows, at) => {
    const values = new Array(cols * rows).fill(0);
    values[at[0] * rows + at[1]] = 1;
    return { cols, rows, values };
  };

  test('a player attacking right is measured from the left goal', () => {
    // Column 1 of 12 is 8.75-17.5 m along, centred at 13.125.
    const out = heatmap.orientedCentroid(grid(12, 8, [1, 1]), 'right');
    assert.ok(Math.abs(out.forwardM - 13.125) < 0.001);
    assert.ok(Math.abs(out.lateralM - 12.75) < 0.001);
  });

  test('attacking left mirrors both axes, not just the one', () => {
    // Flip only x and a player who lived on the left wing in the second half
    // comes out on the right — the same reason cv/report_json.py::shot_marks
    // mirrors both.
    const out = heatmap.orientedCentroid(grid(12, 8, [1, 1]), 'left');
    assert.ok(Math.abs(out.forwardM - (105 - 13.125)) < 0.001);
    assert.ok(Math.abs(out.lateralM - (68 - 12.75)) < 0.001);
  });

  test('without an attacking end there is no forward', () => {
    // "They stayed high" and "they sat deep" are the same picture flipped, so
    // there is no half-answer to give here.
    for (const end of [null, undefined, '', 'up', 0]) {
      assert.equal(heatmap.orientedCentroid(grid(12, 8, [1, 1]), end), null);
    }
    assert.equal(heatmap.orientedCentroid(null, 'right'), null);
    assert.equal(heatmap.orientedCentroid({ cols: 2, rows: 2, values: [0, 0, 0, 0] }, 'right'), null);
  });

  test('the spread is the same figure whichever way they were kicking', () => {
    const wide = { cols: 12, rows: 8, values: new Array(96).fill(1 / 96) };
    const right = heatmap.orientedCentroid(wide, 'right');
    const left = heatmap.orientedCentroid(wide, 'left');
    assert.equal(right.spreadM, left.spreadM);
    assert.ok(right.spreadM > 0);
  });

  test('an empty grid is nowhere, not the centre spot', () => {
    // The centre spot is a real place a defensive midfielder averages, so it
    // could never have been the sentinel even if this project allowed one.
    assert.equal(heatmap.gridCentroid({ cols: 2, rows: 2, values: [0, 0, 0, 0] }), null);
    assert.equal(heatmap.gridCentroid(null), null);
    assert.equal(heatmap.gridSpread(null), null);
  });
});

describe('how firmly an average position is pinned down', () => {
  test('the band is the formula, in metres either way', () => {
    // 2 sigma of the standard error of a mean over correlated samples, with a
    // 60 s wander time: 2 * spread * sqrt(tau / (30 * minutes)).
    const band = report.positionBand(6, 60);
    assert.ok(Math.abs(band - 2 * 6 * Math.sqrt(60 / 1800)) < 1e-9);
    // Halving the time tracked widens it by root two, not by two.
    assert.ok(Math.abs(report.positionBand(6, 30) / band - Math.SQRT2) < 1e-9);
  });

  test('no band without both halves of it', () => {
    // A position offered without a band reads as exact, which is the one thing
    // it is not.
    for (const [spread, minutes] of [[0, 60], [6, 0], [null, 60], [6, null], [-1, 60]]) {
      assert.equal(report.positionBand(spread, minutes), null);
    }
  });

  test('a roamer and a barely-tracked player fail the same gate', () => {
    // Both land past MAX_BAND_M, from opposite directions, and both mean the
    // same thing: this average is not worth comparing to anyone else's.
    assert.ok(report.positionBand(25, 60) > report.MAX_BAND_M);
    assert.ok(report.positionBand(6, 2) > report.MAX_BAND_M);
    assert.ok(report.positionBand(6, 60) < report.MAX_BAND_M);
    // And the gate is not far away from an ordinary player: a 20 m spread over
    // a full hour lands at 7.3 m, inside it. This is a wide net by design —
    // it removes the players nobody could describe, not the players who moved.
    assert.ok(Math.abs(report.positionBand(20, 60) - 7.303) < 0.001);
  });
});

describe('whether the camera saw enough to support a verdict', () => {
  const thirds = (left, middle, right) => ({ thirds: { left, middle, right } });

  test('an even camera is fine', () => {
    const out = report.coverageVerdict(thirds(0.9, 0.95, 0.85));
    assert.equal(out.ok, true);
    assert.ok(Math.abs(out.spread - 0.1) < 1e-9);
  });

  test('a camera that favoured one end withholds the verdict', () => {
    const out = report.coverageVerdict(thirds(0.95, 0.8, 0.5));
    assert.equal(out.ok, false);
    assert.equal(out.reason, 'uneven');
  });

  test('not knowing is a third answer, not the good one', () => {
    // Every report published before the coverage figure existed looks like
    // this, and treating it as an even camera would apply the verdict exactly
    // where nobody can check it.
    for (const bad of [null, undefined, {}, { thirds: null }, thirds(0.9, null, 0.9), thirds('a', 1, 1)]) {
      assert.deepEqual(report.coverageVerdict(bad), { ok: false, reason: 'unknown' });
    }
  });
});

describe('where they played against the line they were picked in', () => {
  /**
   * The shape of this feature is that the rows are a measurement and the
   * remarks are a verdict, and the two have to be able to come apart. Almost
   * every test here is about a case where the measurement stands and the
   * verdict is withheld, because that is the common case on real footage and
   * the one a careless version gets wrong by reporting a finding anyway.
   */

  const EVEN = { thirds: { left: 0.92, middle: 0.95, right: 0.9 } };

  // 5 m of spread over a full match: a band of 1.83 m, well inside MAX_BAND_M.
  const at = (playerId, position, forwardM, extra = {}) => ({
    playerId,
    name: playerId,
    position,
    forwardM,
    lateralM: 34,
    spreadM: 5,
    minutesTracked: 60,
    ...extra,
  });

  const LINES = [
    at('d1', 'def', 25), at('d2', 'def', 27),
    at('m1', 'mid', 45), at('m2', 'mid', 48),
    at('f1', 'fwd', 75),
  ];

  const play = (rows, coverage = EVEN) => report.positionalPlay(rows, { coverage });

  test('a side that lined up where it was picked gets no remarks and no complaint', () => {
    const out = play(LINES);
    assert.equal(out.withheld, null);
    assert.deepEqual(out.remarks, []);
    assert.equal(out.note, null);
    assert.equal(out.rows.length, 5);
  });

  test('the rows come back up the pitch, whatever order they went in', () => {
    const out = play([...LINES].reverse());
    assert.deepEqual(out.rows.map((r) => r.playerId), ['d1', 'd2', 'm1', 'm2', 'f1']);
  });

  test('a defender ahead of every midfielder is the remark', () => {
    const out = play([at('d1', 'def', 25), at('d2', 'def', 60), ...LINES.slice(2)]);
    assert.equal(out.remarks.length, 1);
    const [only] = out.remarks;
    assert.equal(only.playerId, 'd2');
    assert.equal(only.direction, 'ahead');
    assert.equal(only.line, 'mid');
    assert.match(only.text, /ahead of every one of the midfielders/);
    // The margin over the nearest of them, which is what "by how much" means.
    assert.equal(only.gapM, 12);
  });

  test('ahead of most of a line is not ahead of the line', () => {
    // 60 clears the midfielder at 45 by 15 m and the one at 58 by 2. A rule
    // that took the line's average would fire here, and a coach told twice
    // about nothing stops reading the third time.
    const out = play([
      at('d1', 'def', 25), at('d2', 'def', 60),
      at('m1', 'mid', 45), at('m2', 'mid', 58), at('f1', 'fwd', 75),
    ]);
    assert.deepEqual(out.remarks, []);
    assert.equal(out.withheld, null);
  });

  test('one player, one remark: the furthest line they cleared', () => {
    const out = play([at('d1', 'def', 25), at('d2', 'def', 90), ...LINES.slice(2)]);
    assert.equal(out.remarks.length, 1);
    assert.equal(out.remarks[0].line, 'fwd');
    assert.match(out.remarks[0].text, /ahead of every one of the forwards/);
    assert.equal(out.remarks[0].gapM, 15);
  });

  test('a forward behind the whole defence reads the other way round', () => {
    const out = play([...LINES.slice(0, 4), at('f1', 'fwd', 15)]);
    assert.equal(out.remarks.length, 1);
    assert.equal(out.remarks[0].playerId, 'f1');
    assert.equal(out.remarks[0].direction, 'behind');
    assert.match(out.remarks[0].text, /behind every one of the defenders/);
  });

  test('two lines that swapped over are one finding, not five', () => {
    // The arithmetic is symmetric and would otherwise flag both sides: the lone
    // striker who dropped in, and every defender he dropped behind. Said from
    // the smaller side only, because it is the same sentence either way.
    const lone = play([...LINES.slice(0, 4), at('f1', 'fwd', 15)]);
    assert.deepEqual(lone.remarks.map((r) => r.playerId), ['f1']);

    // The other way round, with one defender behind two forwards, the defender
    // is the smaller side and the one spoken about.
    const solo = play([at('d1', 'def', 25), at('f1', 'fwd', 15), at('f2', 'fwd', 17)]);
    assert.deepEqual(solo.remarks.map((r) => r.playerId), ['d1']);
    assert.equal(solo.remarks[0].direction, 'ahead');

    // Two against two leaves nothing to choose between them, and the honest
    // answer is to say it from both ends rather than pick one.
    const even = play([
      at('d1', 'def', 25), at('d2', 'def', 27),
      at('f1', 'fwd', 15), at('f2', 'fwd', 17),
    ]);
    assert.deepEqual(
      even.remarks.map((r) => r.playerId).sort(), ['d1', 'd2', 'f1', 'f2'],
    );
  });

  test('one unjudgeable player takes their whole line out of the comparison', () => {
    // "Ahead of the whole midfield" is a claim about the whole midfield and
    // cannot be made from two of the three.
    const roamer = at('m3', 'mid', 50, { spreadM: 20, minutesTracked: 30 });
    const line = [at('d1', 'def', 25), at('d2', 'def', 60), ...LINES.slice(2)];

    // Without the roamer, d2 at 60 is ahead of a midfield sitting at 45 and 48.
    assert.deepEqual(play(line).remarks.map((r) => r.playerId), ['d2']);

    // With them, there is no longer a whole midfield to be ahead of.
    const out = play([...line, roamer]);
    assert.deepEqual(out.remarks, []);
    // And they are still on the screen with everyone else.
    assert.equal(out.rows.length, 6);
    assert.equal(out.rows.find((r) => r.playerId === 'm3').judgeable, false);
  });

  test('a keeper is measured and never judged', () => {
    // A keeper covers a third the ground of a midfielder. Ranking them on this
    // axis would put every keeper last and call it a finding.
    const out = play([at('g1', 'gk', 8), ...LINES]);
    assert.equal(out.rows[0].playerId, 'g1');
    assert.deepEqual(out.remarks, []);
    // Even one playing absurdly high, which is a real thing a keeper does.
    const sweeper = play([at('g1', 'gk', 62), ...LINES]);
    assert.deepEqual(sweeper.remarks.map((r) => r.playerId), []);
  });

  test('an uneven camera withholds the verdict and keeps the measurement', () => {
    const out = play(LINES, { thirds: { left: 0.95, middle: 0.8, right: 0.5 } });
    assert.equal(out.withheld, 'uneven');
    assert.equal(out.rows.length, 5);
    assert.deepEqual(out.remarks, []);
    assert.match(out.note, /would mostly compare the camera/);
  });

  test('a run that never measured its framing is withheld too', () => {
    const out = play(LINES, null);
    assert.equal(out.withheld, 'unknown');
    assert.match(out.note, /did not record how much of the pitch/);
  });

  test('a squad with no positions set is asked for them, not judged', () => {
    const out = play(LINES.map((r) => ({ ...r, position: null })));
    assert.equal(out.withheld, 'no-lines');
    assert.equal(out.rows.length, 5);
    assert.match(out.note, /Give these players a position/);
  });

  test('one comparable player is nobody to compare with', () => {
    const out = play([
      at('d1', 'def', 25),
      at('m1', 'mid', 45, { spreadM: 20, minutesTracked: 30 }),
    ]);
    assert.equal(out.withheld, 'too-thin');
    assert.equal(out.rows.length, 2);
  });

  test('a row with no position at all is not a row', () => {
    const out = play([...LINES, at('x', 'mid', null), at('y', 'mid', NaN), null]);
    assert.equal(out.rows.length, 5);
  });

  test('nothing to draw is not an error', () => {
    for (const empty of [[], null, undefined]) {
      const out = report.positionalPlay(empty, { coverage: EVEN });
      assert.deepEqual(out.rows, []);
      assert.deepEqual(out.remarks, []);
    }
  });
});

// ------------------------------------------------- measuring the pitch size

/**
 * What the picker can and cannot work out about a pitch it was never told the
 * size of.
 *
 * The mechanism under test is one sentence: a corner is wherever you say the
 * corner is, so a set of corners fits every size equally well, while the
 * penalty box, the goal and the penalty spot are fixed distances in the Laws
 * and one of those pins the scale of everything else. Both halves matter. The
 * recoveries below prove the measurement works; the refusals prove it knows
 * when it does not, which is the half that keeps it from inventing a pitch
 * and scaling every distance the software ever reports by the invention.
 *
 * Shot through a fixed synthetic camera — a real perspective matrix, not an
 * affine one, so the landmarks foreshorten the way they do in a photograph.
 */
describe('measuring the pitch from the clicks', () => {
  // Tilted and off-centre on purpose: a camera square to the pitch is the one
  // case where several wrong sizes are hard to tell apart.
  const CAM = [[11.5, 2.1, 240.0], [-1.4, -9.8, 700.0], [0.0009, -0.0035, 1.0]];
  const proj = (x, y) => {
    const w = CAM[2][0] * x + CAM[2][1] * y + CAM[2][2];
    return [
      (CAM[0][0] * x + CAM[0][1] * y + CAM[0][2]) / w,
      (CAM[1][0] * x + CAM[1][1] * y + CAM[1][2]) / w,
    ];
  };

  // Seeded rather than Math.random: a test that measures a tolerance has to
  // fail for the same reason twice or it is not evidence of anything.
  const jitter = (seed) => {
    let s = seed;
    return () => {
      s = (s * 1103515245 + 12345) & 0x7fffffff;
      return s / 0x7fffffff - 0.5;
    };
  };

  const shoot = (names, lengthM, widthM, px = 0, seed = 12345) => {
    const marks = pitchModel.landmarks(lengthM, widthM);
    const rnd = jitter(seed);
    return new Map(names.map((n) => {
      const [x, y] = proj(...marks[n]);
      return [n, px ? [x + rnd() * px * 2, y + rnd() * px * 2] : [x, y]];
    }));
  };

  const CORNERS = [
    'corner_bottom_left', 'corner_top_left',
    'corner_bottom_right', 'corner_top_right',
  ];
  const MIDLINE = ['halfway_top', 'halfway_bottom', 'centre_spot'];
  const ALL = [...CORNERS, ...MIDLINE, 'pen_spot_left',
    'pen_left_top_corner', 'pen_left_bottom_corner',
    'pen_right_bottom_corner', 'goalpost_left_bottom'];

  test('recovers a size it was never told', () => {
    for (const [L, W] of [[105, 68], [100, 50], [110, 60]]) {
      const got = pitchModel.measureField(shoot(ALL, L, W));
      assert.ok(got.lengthConfident && got.widthConfident, `${L}x${W} refused`);
      assert.ok(Math.abs(got.lengthM - L) < 0.3, `length ${got.lengthM} != ${L}`);
      assert.ok(Math.abs(got.widthM - W) < 0.3, `width ${got.widthM} != ${W}`);
    }
  });

  test('click jitter costs accuracy in proportion, not in kind', () => {
    // A coach clicking a landmark on a phone is a couple of pixels out. The
    // answer degrades smoothly across that range rather than falling apart,
    // and the interval widens to say so.
    for (const [px, tol] of [[0.5, 0.5], [1, 0.5], [2, 1.0], [4, 1.5]]) {
      const got = pitchModel.measureField(shoot(ALL, 100, 50, px));
      assert.ok(got.lengthConfident && got.widthConfident, `${px}px refused`);
      assert.ok(Math.abs(got.lengthM - 100) < tol + 1.0, `${px}px length`);
      assert.ok(Math.abs(got.widthM - 50) < tol, `${px}px width`);
    }
  });

  test('four points measure nothing, and are not asked to', () => {
    // A homography maps four points to four points exactly whatever size you
    // assume, so every candidate scores a perfect zero. Refusing on the count
    // alone is cheaper than discovering that from a flat error surface.
    assert.equal(pitchModel.measureField(shoot(CORNERS, 100, 50)), null);
  });

  test('corners and the halfway line still measure nothing', () => {
    // The interesting refusal: seven points, all placed perfectly, and the
    // page still cannot say. Every landmark here is defined as a fraction of
    // the pitch, so rescaling the model rescales all of them together and the
    // fit is exactly as good. Nothing is broken; there is genuinely no answer.
    const got = pitchModel.measureField(shoot([...CORNERS, ...MIDLINE], 100, 50));
    assert.equal(got.lengthConfident, false);
    assert.equal(got.widthConfident, false);
    assert.ok(got.meanM < 0.01, 'the fit is perfect and still says nothing');
  });

  test('one penalty box corner is enough to pin the whole pitch', () => {
    // Same seven points as above plus one marking with a fixed size in the
    // Laws, and the size falls out exactly. This is the mechanism, isolated.
    const got = pitchModel.measureField(
      shoot([...CORNERS, ...MIDLINE, 'pen_left_top_corner'], 100, 50));
    assert.ok(got.lengthConfident && got.widthConfident);
    assert.ok(Math.abs(got.lengthM - 100) < 0.3);
    assert.ok(Math.abs(got.widthM - 50) < 0.3);
  });

  test('the two dimensions are refused independently', () => {
    // From a real failed calibration: eight clicks on a school pitch, average
    // error 1.77m against the 105x68 default. The width is the thing actually
    // wrong and comes back confidently at roughly 52m; the length is refused,
    // because one of the three fixed-size landmarks present was misplaced.
    // Reporting the width while declining the length is the honest answer,
    // and a version that averaged them into one verdict would lose both.
    const clicked = new Map([
      ['centre_spot', [638, 343]], ['corner_bottom_left', [0, 400]],
      ['corner_top_right', [894, 319]], ['pen_right_bottom_corner', [1009, 382]],
      ['corner_bottom_right', [1276, 402]], ['corner_top_left', [376, 321]],
      ['goalpost_left_bottom', [251, 346]], ['pen_spot_left', [337, 345]],
    ]);
    const got = pitchModel.measureField(clicked);
    assert.equal(got.lengthConfident, false);
    assert.equal(got.widthConfident, true);
    assert.ok(got.widthM > 45 && got.widthM < 58, `width ${got.widthM}`);
    // Whatever it says, it must not be the default nobody measured.
    assert.ok(Math.abs(got.widthM - 68) > 10);
  });

  test('unknown landmark names are ignored, not fatal', () => {
    const pts = shoot(ALL, 100, 50);
    pts.set('not_a_landmark', [10, 10]);
    const got = pitchModel.measureField(pts);
    assert.equal(got.points, ALL.length);
    assert.ok(Math.abs(got.widthM - 50) < 0.5);
  });

  test('nothing to measure is null, not a guess', () => {
    assert.equal(pitchModel.measureField(new Map()), null);
  });
});
