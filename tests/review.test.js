/**
 * The review tool: what its verdicts score, the order a reviewer works in, and
 * how a verdict reaches the numbers a player sees.
 *
 * Run:  node --test tests/review.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../assets/report.js';
import { ev } from './report-fixtures.js';

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
