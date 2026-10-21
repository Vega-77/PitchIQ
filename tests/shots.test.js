/**
 * Shots: the ledger and the xG calibration behind it, headers, and the shot
 * map's sizes and totals.
 *
 * Run:  node --test tests/shots.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../assets/report.js';
import * as markMod from '../assets/shot-map.js';

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
        // 0.5m is where shapeConfidence calls a fit good, and it is now also where
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
