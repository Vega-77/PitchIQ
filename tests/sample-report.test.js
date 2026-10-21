/**
 * The sample report, which shows the video-derived blocks before there is any
 * footage.
 *
 * Run:  node --test tests/sample-report.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../assets/report.js';
import * as heatmap from '../assets/heatmap.js';
import * as markMod from '../assets/shot-map.js';
import * as sample from '../assets/sample-report.js';

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
        // Both halves of the comparison, so a preview of the note shows the
        // ball-detection caveat that travels with the exit rate.
        assert.match(notes, /21 of 34 balls going out of play/);
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
            // MIN_PRESSING_ACTIONS in pitchiq/events.py. A fixture that scored a
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
