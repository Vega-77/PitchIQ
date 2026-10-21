/**
 * The wording under every video-derived number: what a run rests on, what it
 * could not see, and when the camera saw too little to support a verdict.
 *
 * Run:  node --test tests/cv-notes.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../assets/report.js';

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
    test('the bands are the old calibrate page own', () => {
        // renderQuality on the calibrate page (gone since 2026-10-07) called a
        // fit good at 0.5m mean error, and the report kept that standard.
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

    // ---- who the keeping figures belong to ----
    //
    // `cv/keeper.py` names the stake itself: a wrong keeper poisons save
    // percentage, distribution and every keeper feature in the xG model at
    // once. The rows on screen look the same either way, so the line that
    // separates a confirmed keeper from a worked-out one has to be written.

    test('an inferred keeper is named as inferred', () => {
        const text = joined({ keeper_method: 'colour+position' }, { keepers: 2 });
        assert.match(text, /nobody named the goalkeepers/);
        assert.match(text, /kit colour and where they stood/);
    });

    test('a keeper somebody named needs no caveat', () => {
        assert.doesNotMatch(
            joined({ keeper_method: 'manual' }, { keepers: 2 }), /goalkeepers/);
    });

    test('no keeping rows, no keeper caveat', () => {
        // 'unavailable' is the deliberate refusal to guess, and it puts no
        // keeping rows on screen. A caveat about figures the coach cannot see
        // is noise in the one line that carries the real warnings.
        assert.doesNotMatch(
            joined({ keeper_method: 'unavailable' }, { keepers: 0 }), /goalkeepers/);
        assert.doesNotMatch(
            joined({ keeper_method: 'colour+position' }, { keepers: 0 }),
            /nobody named/);
    });

    test('the camelCased spelling reads the same', () => {
        // The emulator fixtures write the quality block camelCased; Python
        // writes it snake_cased. Both reach this function.
        assert.match(
            joined({ keeperMethod: 'colour+position' }, { keepers: 1 }),
            /nobody named the goalkeepers/);
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

    // ---- the tagged log against the video ----
    //
    // Two fallible records of the same match. The only honest thing to say
    // about them is how often they agreed, and both halves of that get said:
    // goals, where the two vocabularies genuinely overlap, and balls going out
    // of play, where the video's half of the answer rests entirely on the ball
    // detection.

    const both = (over) => ({
        goal_agreement: 0.75,
        exit_agreement: 0.62,
        goals: { agreed: 3, cv_only: 0, tag_only: 1 },
        exits: { agreed: 21, cv_only: 7, tag_only: 6 },
        exits_checked: true,
        ...over,
    });
    const agreed = (over) => joined({}, { reconciliation: both(over) });

    test('both halves of the comparison are counted, not just rated', () => {
        // A rate alone is unactionable: 62% of eight stoppages and 62% of two
        // hundred are the same number and completely different situations.
        const text = agreed({});
        assert.match(text, /agree on 3 of 4 goals/);
        assert.match(text, /21 of 34 balls going out of play/);
    });

    test('the exit rate says which half of it is the weak half', () => {
        // A missed exit usually means the ball detection lost the ball, not
        // that the tagger was wrong — and without that sentence the number
        // reads as a mark out of ten for the person with the tablet.
        assert.match(agreed({}), /rests on the ball detection rather than on the tagging/);
    });

    test('one ball out of play is a ball', () => {
        const text = agreed({ exits: { agreed: 1, cv_only: 0, tag_only: 0 } });
        assert.match(text, /1 of 1 ball going out of play/);
        assert.doesNotMatch(text, /1 balls/);
    });

    test('unchecked is not zero, and says why it was never checked', () => {
        // With no calibration there is no touchline in the frame for a ball to
        // cross, so the tagged stoppages went unchecked rather than unmatched.
        // A missing rate on its own would read as a check that found nothing.
        const text = agreed({
            exit_agreement: null, exits: null, exits_checked: false,
        });
        assert.match(text, /nothing checked the tagged stoppages/);
        assert.match(text, /needs a calibration first/);
        // The goal half does not need a calibration and still gets said.
        assert.match(text, /agree on 3 of 4 goals/);
    });

    test('a check that ran and found nothing to compare stays quiet', () => {
        // Neither line is true here: nothing was left unchecked, and there is
        // no agreement to report on a match where no ball ever went out.
        const text = agreed({
            exit_agreement: null,
            exits: { agreed: 0, cv_only: 0, tag_only: 0 },
        });
        assert.doesNotMatch(text, /out of play/);
        assert.doesNotMatch(text, /nothing checked/);
    });

    test('no comparison at all, no lines about one', () => {
        // Every run without a tag log, which is most of them.
        const text = joined({}, { shots: 3 });
        assert.doesNotMatch(text, /out of play/);
        assert.doesNotMatch(text, /nothing checked/);
        assert.doesNotMatch(text, /agree on/);
    });

    test('the camelCased reconciliation reads the same', () => {
        // Python writes the top-level keys snake_cased and the emulator
        // fixtures write them camelCased. The counts inside stay snake_cased
        // in both, which is why only the outer names are aliased.
        const text = joined({}, { reconciliation: {
            goalAgreement: 0.75,
            exitAgreement: 0.62,
            goals: { agreed: 3, cv_only: 0, tag_only: 1 },
            exits: { agreed: 21, cv_only: 7, tag_only: 6 },
            exitsChecked: true,
        } });
        assert.match(text, /agree on 3 of 4 goals/);
        assert.match(text, /21 of 34 balls going out of play/);
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
