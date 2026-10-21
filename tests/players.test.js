/**
 * One player's report: the timeline, the join from tracked figures to the
 * roster, who was on the pitch when, and what a substitution does to all of it.
 *
 * `playerTimeline` is the only place a teammate's name can reach a player's
 * report, because the portal never receives the roster; if it leaks something
 * it should not, no downstream check exists to catch it.
 *
 * Run:  node --test tests/players.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

// report.js imports nothing, so Node can load it straight from disk. That is
// the reason playerTimeline lives there rather than in db.js, which opens a
// Firestore connection the moment it is imported.
import * as report from '../assets/report.js';
import * as sample from '../assets/sample-report.js';
import { ME, MATE, roster, ev, track, STINTS, squad } from './report-fixtures.js';

// The timeline used to live in db.js, and its cases still read it that way.
const db = report;

// ---------------------------------------------------------------- timeline

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

describe('an answer map after the pipeline has been re-run', () => {
    // A CV event id is `frame:track:kind` and a cluster id is the clusterer's
    // own integer label. Both are handed out fresh on every run, and nothing
    // clears the answers when a new run lands — so this is the ordinary state
    // of a match somebody re-processed, not a corrupted document.
    const events = [
        { id: '10:1:shot', type: 'shot' },
        { id: '20:2:pass', type: 'pass' },
    ];
    const byEvent = {
        '10:1:shot': { status: 'confirmed' },
        '99:7:pass': { status: 'confirmed' },  // answered against the last run
        '98:7:duel': { status: 'rejected' },   // answered against the last run
    };

    test('only the answers with a question still attached count', () => {
        const live = report.currentAnswers(events.map((e) => e.id), byEvent);
        assert.equal(live.length, 1);
        assert.equal(live[0].status, 'confirmed');
    });

    test('counting the map instead claims more done than there is', () => {
        // The bug this was written for, as arithmetic: three answers, two
        // events, and a progress line that reads "3 of 2 checked".
        assert.ok(Object.keys(byEvent).length > events.length);
    });

    test('cluster ids compare as strings, because cvMapping keys are', () => {
        const clusters = [{ cluster_id: 3 }, { cluster_id: 4 }];
        const mapping = { 3: 'p1', 11: 'p9' };
        assert.deepEqual(
            report.currentAnswers(clusters.map((c) => c.cluster_id), mapping),
            ['p1'],
        );
    });

    test('nothing asked and nothing answered are both empty, not a throw', () => {
        assert.deepEqual(report.currentAnswers([], byEvent), []);
        assert.deepEqual(report.currentAnswers(['10:1:shot'], null), []);
        assert.deepEqual(report.currentAnswers(null, null), []);
    });

    test('the labels file counts the rows it actually contains', () => {
        // A summary that disagrees with the records under it is worse than no
        // summary at all, in a file whose whole purpose is to still be
        // trustworthy a month after anyone remembers exporting it.
        const out = report.reviewLabels(events, { byEvent, missed: [] });
        assert.equal(out.labelled.length, 1);
        assert.equal(out.counts.labelled, out.labelled.length);
    });

    test('the scorecard was never fooled by this, and still is not', () => {
        // `reviewScore` walks the events and looks each one up, which is both
        // why it was right all along and where the fix above came from.
        const s = report.reviewScore(events, { byEvent, missed: [] });
        assert.equal(s.byType.shot.truePositives, 1);
        assert.equal(s.byType.pass.unreviewed, 1);
        assert.equal(s.byType.duel, undefined);
    });
});
