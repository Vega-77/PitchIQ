/**
 * The tagged log beside the video: what has reached the server, the two
 * records side by side, and the rule that the log never scores the pipeline.
 *
 * Run:  node --test tests/tagged-log.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as report from '../shared/report.js';

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
