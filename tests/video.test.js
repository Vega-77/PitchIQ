/**
 * Match video: which links become a player, where the video sits on a page,
 * and the clock map that turns a match-clock reading into a place in the
 * footage.
 *
 * `videoKind` decides which element a coach-supplied string becomes, and that
 * string ends up in a `src`, so anything it accepts, it had better mean to
 * accept. The clock map is the half of every match the app was quietly getting
 * wrong; its block explains why.
 *
 * Run:  node --test tests/video.test.js
 */

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import * as video from '../shared/video.js';
import * as report from '../shared/report.js';
import * as matchVideo from '../shared/match-video.js';
// Only `nowIndex` is exercised here; the two renderers build DOM. It is in that
// module rather than in the callers because "which mark is the video inside"
// is arithmetic, and both the strip and the list have to answer it the same way.
import * as timeline from '../shared/timeline.js';
import { roster } from './report-fixtures.js';

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
// below. These three cases moved with it.
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
