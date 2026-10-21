/**
 * The season pages: the groups a season is read in, the form line, and the
 * press over time.
 *
 * Run:  node --test tests/season.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../shared/report.js';
import * as sample from '../shared/sample-report.js';
import * as season from '../shared/season.js';
// Same split again: the scale and the radius are pure, the rest builds nodes.
import * as formMod from '../shared/form-chart.js';

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

    test('per 80 needs a denominator worth dividing into', () => {
        assert.ok(!labels(report.seasonGroups([], totals({ minutes: 30 }))[0])
            .includes('G+A per 80 min'));
        assert.ok(labels(report.seasonGroups([], totals({ minutes: 90 }))[0])
            .includes('G+A per 80 min'));
    });

    test('per 80 is per full match: one involvement in 80 minutes reads 1.00', () => {
        const row = report.seasonGroups([], totals({ minutes: 80, goals: 1, assists: 0 }))[0]
            .rows.find((r) => r.label === 'G+A per 80 min');
        assert.equal(row.value, '1.00');
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
