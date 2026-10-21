// Who was on and when, video time against the match clock, and how much
// of a match was watched.
//
// Part of `report.js`, which re-exports it.

import { num } from './report-format.js?v=147';

// ------------------------------------------------- who was on, and when
//
// A tracked figure appears between two moments in the video, and the roster
// knows who was on the pitch between two moments of the match. Those are the
// same question asked on two different clocks, and lining them up turns a
// picker offering all twenty players into one offering the four it could be.
//
// Nothing here filters. It ranks, and the picker groups by the ranking — see
// `clusterRow` in coach/mapping.js for why that distinction is load-bearing.

/** Everyone on the pitch at a given match-clock moment. */
// --------------------------------------------------- when the match ended,
//                                                       and how anyone knows
//
// `matchEndS` closes every open stint, so it decides how many minutes each
// player is recorded as having played. It came out of the tag log as
// `max(matchClockS)`, defaulting to **zero** — and zero is not a final whistle,
// it is the absence of one.
//
// That default is reachable in the most ordinary way there is. `setLineup`
// writes a starter's `{inS: 0, outS: null}` the moment a lineup is set, hours
// before kick-off. So a match that was filmed and processed but that nobody ran
// the tablet for has a full roster of open stints and an empty log — and every
// one of those players was recorded as having played **nought minutes**.
//
// It did not stop there. `trackedCoverage` guards on `matchEndS != null`, which
// zero passes, so a starter came out with `onPitchS` of 0 and `watchedS` of 0;
// `share` then fell to null and `coverageNote` — the sentence that exists to
// explain a shortfall — said nothing at all. `cvReportFields` published
// `cvMinutesOnPitch: 0` beside a real `cvDistanceM` of four kilometres. And the
// player portal, reading `minutesPlayed` of 0, described a starter to their own
// face as **"an unused substitute"**.
//
// The fix is provenance rather than a better default. A whistle that was tagged
// and a whistle that was inferred from the last thing anybody tapped are two
// different facts, and no whistle at all is a third.

/** The match was tagged as finished, so this is when it finished. */
export const FROM_WHISTLE = 'whistle';

/**
 * Nobody tagged full time, so this is the last thing anybody tapped.
 *
 * An underestimate by however long the match ran on afterwards, and only in one
 * direction — which makes it usable and makes saying so compulsory.
 */
export const FROM_LAST_TAG = 'last_tag';

/**
 * When the match ended, and how that was arrived at.
 *
 * Returns `{ matchEndS, source }`, with **both** null when the log holds
 * nothing that can date a whistle. Null rather than zero: the callers all treat
 * an absent end as unanswerable and a zero as a real instant, and it is the
 * second reading that turns a full ninety into nothing.
 */
export function whistleFrom(log) {
    const entries = (log || []).filter((e) => num(e?.matchClockS) != null);
    if (!entries.length) return { matchEndS: null, source: null };

    const whistle = entries.find((e) => e.type === 'full_time');
    if (whistle) {
        return { matchEndS: whistle.matchClockS, source: FROM_WHISTLE };
    }
    return {
        matchEndS: entries.reduce((max, e) => Math.max(max, e.matchClockS), 0),
        source: FROM_LAST_TAG,
    };
}

/**
 * How many minutes a player was on the pitch, or null when nobody can say.
 *
 * Lived in `db.js` until 2026-08-15, where the pure test suites could not
 * reach it — so `tests/flow.test.js` carried a hand-copied second version under
 * a comment reading "mirrors minutesFrom() in shared/db.js", and four tests
 * pinned the copy. Moved here for the same reason `playerTimeline` is here.
 *
 * **A closed stint is still knowable without a whistle.** A player who came off
 * on 60 minutes played sixty of them whether or not anybody tagged full time,
 * so only an *open* stint needs the end of the match. That is most of a
 * substituted squad kept rather than blanked.
 */
export function minutesFrom(stints, matchEndS) {
    if (!stints?.length) return 0;

    let seconds = 0;
    for (const stint of stints) {
        const end = stint.outS ?? matchEndS;
        // Still on the pitch at a whistle nobody recorded. There is no number
        // here, and zero is the one answer that is certainly wrong.
        if (num(end) == null) return null;
        seconds += Math.max(0, end - stint.inS);
    }
    return Math.round(seconds / 60);
}

/**
 * Whether a published report's `minutesPlayed` is a measurement or a stand-in.
 *
 * The document always carries a number — `firestore.rules` requires one, and so
 * does every report written before this question was asked — so the boolean
 * beside it is the only thing that can tell the two apart.
 *
 * **Absent counts as known.** Reports published before `minutesKnown` existed
 * carry real minutes and no flag, and reading that absence as "unknown" would
 * blank whole seasons to fix a handful of matches.
 */
export const knownMinutes = (report) => report?.minutesKnown !== false;

/**
 * What the minutes column is worth, in one sentence, or null when it is fine.
 *
 * Said once under a table rather than per row: on an untagged match every row
 * has the same thing wrong with it, and eleven identical footnotes is a wall a
 * reader learns to skip.
 */
export function minutesNote(source, options = {}) {
    const { second = false, over = true } = options;
    const who = second ? 'you were' : 'anybody was';

    if (source == null) {
        const nobody = second
            ? 'no minutes for you if you were still on at the end'
            : 'no minutes for anyone still on at the end';
        return 'Nobody tagged this match, so there is no clock to say when it '
            + `ended and ${nobody} — not nought minutes, no answer. Anything `
            + 'the video measured was measured all the same.';
    }
    // A match still being played has no final whistle to have tagged, and
    // saying so at every half-time is how a warning becomes wallpaper. The
    // no-log case above is *not* gated on this: a tablet that has recorded
    // nothing by half-time is worth knowing about immediately.
    if (source === FROM_LAST_TAG && !over) return null;

    if (source === FROM_LAST_TAG) {
        return 'Nobody tagged the final whistle, so the match is counted as '
            + `ending at the last thing anybody tapped. If ${who} still on the `
            + 'pitch then, the minutes here are short by however long it ran on '
            + 'afterwards.';
    }
    return null;
}

export function onPitchAt(roster, clockS) {
    const out = new Set();
    for (const entry of roster || []) {
        for (const stint of entry.stints || []) {
            const end = stint.outS ?? Infinity;
            if (stint.inS <= clockS && clockS < end) {
                out.add(entry.id);
                break;
            }
        }
    }
    return out;
}

/**
 * How many seconds of a player's stints overlap a window.
 *
 * `matchEndS` closes an open stint. A stint with `outS === null` means the
 * player was still on when the log ended, not that they played forever.
 *
 * **An unknown match end closes an open stint at the end of the window asked
 * about, not at second zero.** Passing zero used to do the latter, and every
 * caller reads the result as time on the pitch: the cluster picker ranked a
 * whole untagged squad at nought seconds of overlap, so the figure a coach was
 * looking at could not be offered its own player. Whoever genuinely cannot
 * answer — `trackedCoverage` — checks for the null itself before asking.
 */
export function stintOverlapS(stints, startS, endS, matchEndS) {
    if (endS <= startS) return 0;
    let total = 0;
    for (const stint of stints || []) {
        const end = stint.outS ?? (num(matchEndS) ?? endS);
        total += Math.max(0, Math.min(end, endS) - Math.max(stint.inS, startS));
    }
    return total;
}

// ------------------------------------------------- video time vs the match clock
//
// Two clocks, and until now one subtraction related them.
//
// The tablet keeps a match clock: zero at the first kick-off, and **frozen for
// the whole of half-time** — `advancePeriod` in tagging.js stops it at the
// break and restarts it from the same second. The video's clock is a position
// in a file and freezes for nothing; it runs straight through the oranges.
//
// So `clockS = videoS - offsetS` is exact for the first half and wrong for the
// entire second half by however long that break lasted, in both directions at
// once. A goal tagged at 52:30 seeks to a video position somewhere in the
// middle of the interval, and a shot the pipeline found at video 68:00 is
// reported as the 68th minute of a match that was 53 minutes old. Ten to
// fifteen minutes of error on every second-half timestamp in the app, and
// invisible, because both numbers come out looking like plausible minutes.
//
// Fixing it needs exactly one fact the offset does not carry: where in the
// video the second half kicks off. Nothing can derive it. The break's length is
// not in the tag log — the tag log is the thing that froze — so the coach
// supplies it the way they already supply the first offset, and the clock the
// second half restarts on comes from the tablet, which knew it at the time and
// now writes it down.
//
// With both anchors the map is piecewise and exact at each of them. With only
// the offset it degrades to precisely today's behaviour and says so, so a
// caller can decline to quote a match minute it cannot stand behind.

export const FIRST_HALF = 'first_half';
export const HALF_TIME = 'half_time';
export const SECOND_HALF = 'second_half';

/**
 * The map between a position in the footage and a reading on the match clock.
 *
 * `secondHalfClockS` is what the tablet's clock read when the second half
 * kicked off — which, because it froze at the break, is also what it read when
 * the first half ended. `secondHalfVideoS` is where that same moment sits in
 * the video.
 *
 * A pair that implies a **negative** break is refused outright rather than
 * used. It means one of the two numbers is mistyped, and honouring it would
 * make the map run backwards: two different moments in the footage would map to
 * one reading on the clock, and the marks on the timeline would cross over each
 * other. Today's one-anchor behaviour is wrong by a known amount in a known
 * direction; a non-monotonic clock is wrong in a way nothing downstream could
 * even describe. `inconsistent` is set so the coach can be told which it is,
 * because a silently ignored number reads as a field that does not work.
 *
 * `period` is `null`, never a guess, whenever only the offset is known. Half of
 * the point here is being able to say "this is a position in the footage, not a
 * match minute".
 */
export function matchClockMap(options = {}) {
    const { videoOffsetS = 0, secondHalfVideoS = null, secondHalfClockS = null } = options;

    const offsetS = Number(videoOffsetS) || 0;
    const kickoffS = num(secondHalfVideoS);
    const restartClockS = num(secondHalfClockS);

    // Both halves of the anchor, and a clock that actually ran before it. A
    // second half restarting at 00:00 is not a second half.
    const paired = kickoffS != null && restartClockS != null && restartClockS > 0;
    const breakS = paired ? (kickoffS - offsetS) - restartClockS : null;
    const inconsistent = paired && breakS < 0;
    const knowsSecondHalf = paired && !inconsistent;

    return {
        videoOffsetS: offsetS,
        knowsSecondHalf,
        inconsistent,
        // How long the video spent on the interval. Null when unknown — which
        // is a different thing from a match played without a break.
        breakS: knowsSecondHalf ? breakS : null,
        secondHalfVideoS: knowsSecondHalf ? kickoffS : null,
        secondHalfClockS: knowsSecondHalf ? restartClockS : null,

        /** What the clock read at a position in the footage. */
        toClock(videoS) {
            const v = Number(videoS) || 0;
            if (!knowsSecondHalf) {
                return { clockS: Math.max(0, v - offsetS), period: null };
            }
            if (v >= kickoffS) {
                return { clockS: restartClockS + (v - kickoffS), period: SECOND_HALF };
            }
            const first = v - offsetS;
            // Inside the break the clock really did read the same second the
            // whole time, so that is what is returned — with the period saying
            // it is a frozen reading rather than a moment in a half.
            if (first >= restartClockS) {
                return { clockS: restartClockS, period: HALF_TIME };
            }
            return { clockS: Math.max(0, first), period: FIRST_HALF };
        },

        /** Where in the footage the clock read this.
         *
         * The restart's own second belongs to the second half: a `kickoff_2nd`
         * tag and the `halftime` tag before it share a clock reading, and of the
         * two positions that could mean, the restart is the one worth seeking
         * to.
         */
        toVideo(clockS) {
            const c = Math.max(0, Number(clockS) || 0);
            if (knowsSecondHalf && c >= restartClockS) {
                return kickoffS + (c - restartClockS);
            }
            return Math.max(0, c + offsetS);
        },

        /** Which half a position in the footage falls in, or null. */
        periodAt(videoS) {
            return this.toClock(videoS).period;
        },
    };
}

/**
 * The clock map for a match document, or for a player's copy of one.
 *
 * Both carry the same three fields under the same names — `pushVideoToReports`
 * makes sure of it — so the coach's screen, the half-time view and the player
 * portal cannot end up converting the same moment differently. Which was the
 * previous state of affairs by accident rather than by design: they each did
 * the same subtraction separately, and they were each wrong by the same
 * fifteen minutes, so nothing ever disagreed loudly enough to notice.
 */
/**
 * What the two numbers beside the video link add up to, in a sentence.
 *
 * Four states, and the one worth the most is the first: a coach who has never
 * filled the second field in has no reason to suspect anything is wrong, since
 * the timestamps they get look like ordinary minutes. So the absence is stated
 * as a consequence — moments placed as if the match never stopped — rather than
 * as a blank field.
 *
 * `clockText` is passed in rather than imported: the report-*.js files import
 * only each other, by design, so the whole of it can be tested without a DOM,
 * and `renderMatchVideo` already takes its formatter the same way.
 */
export function clockMapNote(inputs = {}, clockText = (s) => `${Math.round(s)}s`) {
    const { videoOffsetS = 0, secondHalfVideoS = null, halfTimeClockS = null } = inputs;

    if (num(secondHalfVideoS) == null) {
        return {
            tone: 'muted',
            text: 'Without this, second-half moments are placed as if the match '
                + 'never stopped — every one of them lands late by however long '
                + 'the break ran.',
        };
    }
    if (num(halfTimeClockS) == null || halfTimeClockS <= 0) {
        return {
            tone: 'warn',
            text: 'Nobody tapped half-time on the tablet for this match, so there '
                + 'is no clock reading to line this up against. Saved, but not '
                + 'used until there is one.',
        };
    }

    const breakS = (secondHalfVideoS - (Number(videoOffsetS) || 0)) - halfTimeClockS;
    if (breakS < 0) {
        return {
            tone: 'warn',
            text: `That puts the second-half kick-off before the first half ended `
                + `— the tablet had the clock at ${clockText(halfTimeClockS)} when `
                + `half-time was tapped. One of these two numbers is wrong, so the `
                + `second half is still being placed without them.`,
        };
    }
    return {
        tone: 'ok',
        text: `Half-time ran ${clockText(breakS)} in the footage. Second-half `
            + 'moments are placed across it.',
    };
}

export function clockFromMatch(match) {
    return matchClockMap({
        videoOffsetS: match?.videoOffsetS ?? 0,
        secondHalfVideoS: match?.secondHalfVideoS ?? null,
        secondHalfClockS: match?.halfTimeClockS ?? null,
    });
}

/**
 * Rank a roster by how well each player's time on the pitch fits a cluster's.
 *
 * The cluster's first and last sightings are in video time and the stints are
 * in match clock, related by `matchClockMap` above — which is piecewise across
 * the break, so a substitute who came on in the second half is scored against
 * the right stretch of footage rather than one shifted by the interval.
 *
 * Returned in full and sorted, never filtered. If that offset is wrong — and it
 * is the single most fiddly number in the app — then every overlap here is
 * wrong too, and a picker that had hidden the non-overlapping players would
 * have hidden the right answer.
 */
export function rankRosterForCluster(roster, cluster, options = {}) {
    const { matchEndS = null } = options;
    const clock = clockOf(options);
    const startS = clock.toClock(cluster?.first_seen_s ?? 0).clockS;
    const endS = clock.toClock(cluster?.last_seen_s ?? 0).clockS;
    const span = Math.max(0, endS - startS);

    return (roster || [])
        .map((entry) => {
            const overlapS = stintOverlapS(entry.stints, startS, endS, matchEndS);
            return {
                entry,
                overlapS,
                // What fraction of the time this figure was on screen the
                // player was actually playing. The useful number for a human:
                // "on for all of it" beats "on for 40 seconds" when the figure
                // was only tracked for 40 seconds.
                overlapShare: span > 0 ? overlapS / span : 0,
            };
        })
        .sort((a, b) => (
            b.overlapS - a.overlapS
            || (a.entry.jerseyNumber ?? 999) - (b.entry.jerseyNumber ?? 999)
        ));
}

// --------------------------------------------- how much of a match was watched
//
// A player's card puts "Minutes 71" next to "km covered 1.9". Those are two
// different denominators printed as one row. The first is the sub log — the
// whole time they were on the pitch. The second is the video: however much of
// that the tracker kept hold of, inside whatever window of footage was actually
// processed. At 3.4 tracks per player the gap between them is not small, and a
// coach ranking a roster by the second column is mostly ranking who the tracker
// happened to follow.
//
// So every per-player figure from the video carries the share of that player's
// own minutes it was measured over. Nothing here scales a figure up to what it
// would have been: a total measured over a third of someone's match is a third
// of a total, and tripling it would be inventing the other two thirds. The
// comparable figure is a rate, and it is offered as its own number.

/**
 * Below this share of a player's minutes, a video total is a sample, not a total.
 *
 * Set where the arithmetic stops being a rounding difference and starts being a
 * different claim: at 0.8 a distance reads a fifth low, which is wrong but still
 * recognisably the player's match. At 0.5 it is half their match with the label
 * of a whole one.
 */
export const TRACKED_SHARE_FLOOR = 0.7;

/**
 * The stretch of footage that was processed, in match-clock seconds.
 *
 * `window` is in video seconds, because it is a pair of seek positions in a
 * file. Stints are in match clock, because that is what a tablet on the
 * touchline records. `matchClockMap` above is the only thing relating them, and
 * getting this backwards would score a substitute against the warm-up.
 *
 * An absent bound means the pipeline ran to that edge of the file, so it is
 * answered from the match rather than guessed at: kick-off for a missing start,
 * and the last thing the log recorded for a missing end. With neither the window
 * nor a match end there is nothing to answer from, and this returns null —
 * which is a different thing from a window of zero length.
 */
export function windowClockRange(window, options = {}) {
    const { matchEndS = null } = options;
    const clock = clockOf(options);
    const startVideoS = window?.start_s ?? window?.startS ?? null;
    const endVideoS = window?.end_s ?? window?.endS ?? null;

    const endS = endVideoS == null ? matchEndS : clock.toClock(endVideoS).clockS;
    if (endS == null) return null;

    const startS = startVideoS == null ? 0 : clock.toClock(startVideoS).clockS;
    return { startS: Math.max(0, startS), endS };
}

/**
 * The clock map a caller supplied, or the one its offset alone describes.
 *
 * Every one of these options bags used to carry a bare `videoOffsetS`, and a
 * caller that still does gets exactly the behaviour it had. There is only one
 * implementation of the conversion; this is only two ways of naming what is
 * known about it.
 */
export function clockOf(options = {}) {
    return options.clock || matchClockMap({ videoOffsetS: options.videoOffsetS ?? 0 });
}

/**
 * How one player's video figures relate to the match they actually played.
 *
 * Three different spans, and the difference between them is the whole point:
 *
 *   - `onPitchS` — what the sub log says they played.
 *   - `watchedS` — how much of that fell inside the processed footage. On a
 *     three-minute clip this is three minutes for everyone, which is why the
 *     window has to be intersected rather than assumed away: without it every
 *     player on a short clip would show a coverage of about 3%, and the figure
 *     would be a statement about the clip rather than about them.
 *   - `trackedS` — how much of *that* the tracker held on to. The shortfall is
 *     fragmentation, and it is the number nobody has been able to see per
 *     player until now.
 *
 * Every field is null rather than zero when it cannot be answered. A player
 * whose stints were never recorded did not play no minutes.
 */
export function trackedCoverage(minutesTracked, stints, context = {}) {
    const { window, matchEndS = null } = context;

    const trackedS = minutesTracked == null ? null : minutesTracked * 60;
    const known = Array.isArray(stints) && stints.length > 0 && matchEndS != null;
    const range = windowClockRange(window, { clock: clockOf(context), matchEndS });

    const onPitchS = known ? stintOverlapS(stints, 0, matchEndS, matchEndS) : null;
    const watchedS = known && range
        ? stintOverlapS(stints, range.startS, range.endS, matchEndS)
        : null;

    // `watchedS` of zero is falsy on purpose. A player who was not on the pitch
    // for any of the processed footage has no share — not a share of nothing.
    const share = trackedS != null && watchedS ? trackedS / watchedS : null;
    return { trackedS, onPitchS, watchedS, share };
}

/**
 * Metres per minute of the time the video actually held a player.
 *
 * The one movement figure that survives fragmentation. A total is a fraction of
 * the truth when the tracker only had someone for half of their match; a rate
 * over the half it did have is still a rate. It is also what makes a substitute
 * who played twenty minutes comparable to a starter who played eighty, which a
 * column of kilometres never was.
 *
 * Not unbiased, and worth saying so: the tracker loses people in the congested,
 * fast passages where they are working hardest, so this reads low rather than
 * randomly. Low and comparable beats a total that is neither.
 */
export function metresPerMinute(distanceM, minutesTracked) {
    if (distanceM == null || !minutesTracked) return null;
    return distanceM / minutesTracked;
}

/**
 * One sentence on how much of a player's match the video was able to measure.
 *
 * The fraction is against `watchedS`, not against the minutes they played, and
 * the difference matters on any run short of a full match: a three-minute clip
 * of a player who was on for seventy would otherwise report 4% and read as a
 * tracker that lost them, when in fact nobody filmed the other sixty-seven
 * minutes. Filmed and played are named separately whenever they differ, because
 * they are two different shortfalls and only one of them is the software's.
 *
 * Null when there is nothing to say — no sub log, no window, no confirmed
 * cluster — since a sentence hedging about a number that is not on screen is
 * noise, and this sits under figures that are often all a player has.
 */
export function coverageNote(coverage, options = {}) {
    const { second = false } = options;
    const { trackedS, onPitchS, watchedS, share } = coverage || {};
    if (share == null || trackedS == null) return null;

    const they = second ? 'you' : 'they';
    const mins = (seconds) => Math.round(seconds / 60);

    // Above one means two figures mapped to this player were on screen at once,
    // which one player cannot be — so it is evidence the mapping double-counts,
    // and every total resting on it is inflated. Said outright rather than
    // clamped to a tidy 100%, which would hide the only symptom.
    if (share > 1.15) {
        return `Two of the tracked figures matched to ${second ? 'you' : 'this player'}`
            + ' were on screen at the same time, so these totals count part of'
            + ' the match twice.';
    }

    const tracked = mins(trackedS);
    const played = onPitchS == null ? null : mins(onPitchS);
    const filmed = mins(watchedS);

    // Within a minute they are the same span, and saying it twice would invite
    // the reader to look for a distinction that is not there.
    const partial = played != null && Math.abs(played - filmed) >= 1;
    const head = partial
        ? `The video covered ${filmed} of the ${played} minutes ${they} played,`
            + ` and measured ${tracked} of those`
        : `The video measured ${tracked}`
            + (played == null ? ' minutes' : ` of the ${played} minutes ${they} played`);

    if (share >= TRACKED_SHARE_FLOOR) return `${head}.`;
    return `${head} — so the totals here are part of the match rather than all`
        + ' of it, and the rate is the fairer comparison.';
}
