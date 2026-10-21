/**
 * The half-time brief and the plain-language reads behind it.
 *
 * Run:  node --test tests/match-brief.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../shared/report.js';
import { roster } from './report-fixtures.js';

// --------------------------------------------- reading the half out loud
//
// The catalog asks for plain-language flags rather than tables at half-time.
// The risk with a flag is not that it is wrong, it is that it fires every
// match — a flag that always appears stops being read and takes the ones that
// matter with it. So most of these test silence.

describe('seasonMoments', () => {
    const t = (clockS, type, label, mine = true) => ({ clockS, type, label, mine });

    test('their goals, assists and cards, newest match first, in match order', () => {
        const newer = { opponentName: 'B', timeline: [
            t(3000, 'goal', 'You assisted Sam'), t(600, 'goal', 'You scored'),
        ] };
        const older = { opponentName: 'A', timeline: [t(2440, 'card', 'You were booked')] };
        const moments = report.seasonMoments([newer, older]);
        assert.deepEqual(moments.map((m) => [m.kind, m.minute, m.report.opponentName]), [
            ['goal', '11′', 'B'], ['assist', '51′', 'B'], ['card', '41′', 'A'],
        ]);
    });

    test('nobody else’s goals, and none of the rest of their timeline', () => {
        const r = { timeline: [
            t(0, 'kickoff_1st', 'Kick-off', false), t(900, 'goal', 'Sam scored', false),
            t(1200, 'goal', 'Goal conceded', false), t(1800, 'sub', 'Came off'),
            t(2000, 'foul', 'You conceded a foul'),
        ] };
        assert.deepEqual(report.seasonMoments([r]), []);
    });

    test('a report from before timelines adds nothing rather than failing', () => {
        assert.deepEqual(report.seasonMoments([{ opponentName: 'Old' }]), []);
        assert.deepEqual(report.seasonMoments(null), []);
    });
});

describe('matchBrief', () => {
    const roster = [{ id: 'p1', playerName: 'Alex Vega' }, { id: 'p2', playerName: 'Rae Nkemelu' }];
    const counts = (us, them) => ({ us: { goal: us }, them: { goal: them } });
    const goal = (side, s, playerId) => ({ kind: 'event', type: 'goal', side, matchClockS: s, playerId });
    const row = (label, usN, themN, better = 'high', extra = {}) => ({
        label, usN, themN, better, kind: 'count', value: usN, themValue: themN, ...extra,
    });

    test('the result, with who scored and when, comes first', () => {
        const [first] = report.matchBrief({
            log: [goal('us', 940, 'p1'), goal('them', 2000), goal('us', 3000, 'p1')],
            roster, counts: counts(2, 1), status: 'full_time',
        });
        assert.equal(first.title, 'Won 2–1');
        assert.equal(first.detail, 'Scored by Alex Vega 16′, Alex Vega 51′.');
        assert.equal(first.tone, 'good');
    });

    test('a match still going says so instead of calling a result', () => {
        const lines = report.matchBrief({
            log: [goal('us', 940, 'p1')], roster, counts: counts(1, 0), status: 'halftime',
        });
        assert.equal(lines[0].title, '1–0 up at half-time');
        assert.equal(report.matchBrief({
            log: [{ kind: 'event', type: 'corner', side: 'us', matchClockS: 5 }],
            counts: counts(0, 0), status: 'second_half',
        })[0].title, 'Level at 0–0 so far');
    });

    test('nothing tagged means no result line, not a 0–0', () => {
        assert.deepEqual(report.matchBrief({ log: [], counts: counts(0, 0), status: 'full_time' }), []);
    });

    test('our cards are named with the minute', () => {
        const lines = report.matchBrief({
            log: [{ kind: 'event', type: 'card', side: 'us', matchClockS: 1500,
                    playerId: 'p2', cardColor: 'yellow' }],
            roster, counts: counts(0, 0), status: 'full_time',
        });
        assert.equal(lines[1].title, 'One card');
        assert.equal(lines[1].detail, 'Rae Nkemelu 26′.');
    });

    test('edges are the big gaps only, in the direction that is better', () => {
        const lines = report.matchBrief({
            rows: [
                row('Shots', 6, 4),                      // a third apart: an edge
                row('Ground duels', 44, 44),             // level: nothing
                row('Fouls committed', 2, 9, 'low'),     // fewer is better: ahead
                row('Tackles', 12, 21),                  // behind
                row('Corners won', 1, 0),                // too small to mean anything
                row('Goals', 3, 0),                      // has a line of its own
            ],
        });
        const ahead = lines.find((l) => l.title === 'Ahead on');
        const behind = lines.find((l) => l.title === 'Behind on');
        assert.equal(ahead.detail, 'Fouls committed 2 to 9 · Shots 6 to 4');
        assert.equal(behind.detail, 'Tackles 12 to 21');
        assert.equal(lines.length, 2);
    });

    test('an edge resting on the video says it is estimated', () => {
        const [line] = report.matchBrief({
            rows: [row('Shots', 6, 2, 'high', { confidence: 'medium' })],
        });
        assert.equal(line.estimated, true);
    });

    test('the video reads are carried, two at most', () => {
        const reads = [1, 2, 3].map((n) => ({ title: `Read ${n}`, detail: '' }));
        const lines = report.matchBrief({ reads });
        assert.deepEqual(lines.map((l) => l.title), ['Read 1', 'Read 2']);
    });
});

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

    test('the tagged score, when there is one, is the goals it reads', () => {
        // The video counted 2 where the scoreline above says 1. The read must
        // quote the scoreline, and here that turns "flattered" into nothing.
        const team = { xg: 0.8, goals: 2, shots: 4 };
        assert.match(report.cvReads(cv(team))[0].detail, /^2 from about 0\.8/);
        assert.deepEqual(report.cvReads(cv(team), { goals: 1 }), []);
        // And a tagged 0 is a 0, not a gap to fall back through.
        const wasteful = report.cvReads(cv({ xg: 1.6, goals: 1, shots: 9 }), { goals: 0 });
        assert.match(wasteful[0].detail, /and 0 scored/);
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
