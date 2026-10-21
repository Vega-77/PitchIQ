/**
 * The match statistics: rows by kind, the goalkeepers, the two sides compared,
 * the phase-of-play funnel, and the opponent's figures.
 *
 * Run:  node --test tests/match-stats.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../assets/report.js';

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

describe('keeperOfTrack', () => {
    const KEEPERS = [
        { team: 'team_a', track_ids: [2, 7] },
        { team: 'team_b', track_ids: [18] },
    ];

    test('a track the pipeline called a keeper names its side', () => {
        assert.equal(report.keeperOfTrack(KEEPERS, 2), 'team_a');
        assert.equal(report.keeperOfTrack(KEEPERS, 7), 'team_a');
        assert.equal(report.keeperOfTrack(KEEPERS, 18), 'team_b');
    });

    test('every other figure on the pitch is nobody', () => {
        // The answer has to be null rather than a side, because the caller
        // prints it: a stray truthy value would put `goalkeeper` against an
        // outfielder for the whole review.
        assert.equal(report.keeperOfTrack(KEEPERS, 3), null);
        assert.equal(report.keeperOfTrack(KEEPERS, 0), null);
    });

    test('a run with no keepers found answers, rather than throwing', () => {
        // Both sides can be missing — `identify_keepers` finds each team’s
        // independently — and this is called on every review row.
        assert.equal(report.keeperOfTrack(null, 2), null);
        assert.equal(report.keeperOfTrack([], 2), null);
        assert.equal(report.keeperOfTrack([{ team: 'team_a' }], 2), null);
    });

    test('the two spellings of a track id are the same track', () => {
        // Python writes numbers; anything that has been a document key comes
        // back a string. Comparing those with === would silently find nothing.
        assert.equal(report.keeperOfTrack(KEEPERS, '2'), 'team_a');
        assert.equal(report.keeperOfTrack([{ team: 'team_b', track_ids: ['18'] }], 18),
                     'team_b');
    });

    test('a missing track id is not a match for anything', () => {
        // Every event carries a track id, but an event may carry none, and
        // Number(null) is 0 — which would make track 0 the keeper.
        assert.equal(report.keeperOfTrack([{ team: 'team_a', track_ids: [0] }], null),
                     null);
        assert.equal(report.keeperOfTrack([{ team: 'team_a', track_ids: [0] }], undefined),
                     null);
        assert.equal(report.keeperOfTrack([{ team: 'team_a', track_ids: [0] }], 0),
                     'team_a');
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
        const ppda = find(rows, 'Passes allowed per tackle (PPDA)');
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
