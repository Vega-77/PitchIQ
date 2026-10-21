// The press over time, and the half read out loud.
//
// Part of `report.js`, which re-exports it.

import { clockOf } from './report-clock.js?v=148';

// ------------------------------------------------------- the press, over time
//
// One PPDA for a whole match answers "did they press" and hides the question a
// coach actually asks, which is "for how long". A side that squeezed for twenty
// minutes and then sat off has the same match figure as one that pressed evenly
// throughout, and those are different teams playing differently.
//
// `pitchiq/events.py::pressing_segments` does the counting and decides which blocks
// have a denominator worth a ratio; nothing here second-guesses it. A block
// arrives with `ppda: null` for one of two reasons and the difference is the
// whole reading — `actions: 0` is a spell in which nobody was challenged, which
// is the strongest possible finding, and a small non-zero count is a spell too
// quiet to score. Both draw no bar. Only one of them is news.
//
//     Longer bar, worse press.
//
// PPDA is passes *allowed*, so it runs backwards from every other bar on this
// site. The alternative — plotting its reciprocal, so bigger meant harder —
// reads more naturally and would put a number on screen that disagrees with the
// PPDA row printed directly above it. Two numbers side by side describing one
// thing with different denominators is the failure this whole app keeps
// tripping over, so the direction is stated in words instead.

/**
 * The press block by block, with the bar lengths worked out.
 *
 * `share` is measured against the longest bar and **from zero**, not from the
 * shortest. A bar chart that starts at the minimum makes a 10% difference look
 * total, and the differences here are already inside the noise more often than
 * not.
 *
 * Returns null rather than an empty trend when there is nothing to draw, so a
 * caller hides the block instead of captioning a blank.
 */
export function pressingTrend(segments, options = {}) {
    if (!segments?.length) return null;

    const clock = clockOf(options);
    const minute = (s) => Math.max(0, Math.round(clock.toClock(s).clockS / 60));
    const blocks = segments.map((seg) => ({
        startS: seg.start_s ?? null,
        endS: seg.end_s ?? null,
        allowed: seg.allowed ?? 0,
        actions: seg.actions ?? 0,
        ppda: seg.ppda ?? null,
        startMin: seg.start_s == null ? null : minute(seg.start_s),
        endMin: seg.end_s == null ? null : minute(seg.end_s),
        // Two different silences, and the note tells them apart.
        unchallenged: (seg.ppda ?? null) == null && !(seg.actions ?? 0),
        thin: (seg.ppda ?? null) == null && (seg.actions ?? 0) > 0,
    }));

    const scored = blocks.filter((b) => b.ppda != null);
    const longest = Math.max(0, ...scored.map((b) => b.ppda));
    for (const block of blocks) {
        block.share = block.ppda == null || !longest ? null : block.ppda / longest;
    }

    return {
        blocks,
        scored: scored.length,
        thin: blocks.filter((b) => b.thin).length,
        unchallenged: blocks.filter((b) => b.unchallenged).length,
        hardest: scored.length
            ? scored.reduce((a, b) => (b.ppda < a.ppda ? b : a)) : null,
        softest: scored.length
            ? scored.reduce((a, b) => (b.ppda > a.ppda ? b : a)) : null,
    };
}

// How much worse the last scored block has to be than the first before it is
// worth saying the press dropped off.
//
// Set from the denominator, not from taste. A block holds roughly ten defensive
// actions, which as a count carries about ±32%; the ratio of two such blocks
// therefore moves about ±45% on chance alone, so a factor under about 1.8 is
// indistinguishable from an ordinary quarter-hour. Blocks without a ratio take
// no part in this comparison at all.
const PRESS_DROP_FACTOR = 1.8;

/**
 * What the trend says out loud. Null when it says nothing.
 *
 * Deliberately compares first scored block against last rather than fitting a
 * slope. "You allowed twice as many passes per challenge by the end" is a thing
 * a coach can be told; a gradient in PPDA per minute is not, and on four points
 * a fitted slope is mostly a picture of the noise anyway.
 */
export function pressingRead(trend) {
    const scored = (trend?.blocks || []).filter((b) => b.ppda != null);
    if (scored.length < 2) return null;

    const first = scored[0];
    const last = scored[scored.length - 1];
    if (last.ppda < first.ppda * PRESS_DROP_FACTOR) return null;

    return {
        title: 'Your press faded',
        detail: `Early on you challenged every ${first.ppda.toFixed(1)} passes`
            + ` they made; by minute ${last.startMin}–${last.endMin} it took`
            + ` ${last.ppda.toFixed(1)}.`,
    };
}

/**
 * The caveats under the chart. Always returns at least the foul one.
 *
 * The foul caveat is not decoration. Fouls are a defensive action in the
 * standard definition and are invisible to a bounding box, so every figure here
 * is short a denominator and reads high — a known direction and an unknown size,
 * which is exactly the kind of bias that has to be printed beside the number
 * rather than buried in a Python docstring.
 */
export function pressingNote(trend) {
    if (!trend) return null;
    const parts = [];

    // One counter, so the two sentences cannot end up as "One block" beside
    // "1 block" — which is what the first version did.
    const blocks = (n) => (n === 1 ? 'One block' : `${n} blocks`);

    if (trend.unchallenged) {
        parts.push(`${blocks(trend.unchallenged)} `
            + `${trend.unchallenged === 1 ? 'has' : 'have'} no bar because nobody `
            + 'made a challenge in the pressing zone at all — that is the '
            + 'reading, not a gap.');
    }
    if (trend.thin) {
        parts.push(`${blocks(trend.thin)} had too few challenges to divide by, `
            + 'so only the counts are shown.');
    }

    parts.push('Fouls count as a challenge in the usual definition of this figure '
        + 'and a camera cannot see them, so every bar here runs a little long.');
    return parts.join(' ');
}

// ------------------------------------------------- reading the half out loud
//
// The stats catalog asks for "plain-language flags over raw tables" at
// half-time, and gives the shape of them: *"RB has covered 20% less ground than
// LB"*, *"team compactness dropped in the last 15 minutes"*. A coach has three
// minutes and is standing up. A table of eighteen numbers is not a read on the
// half; three sentences are.
//
// Every threshold below is a guess, none has been checked against a real match,
// and the consequence of setting one too low is worse than it looks: a flag
// that fires every game stops being read, and takes the ones that matter with
// it. So they are set where a difference is large enough that a coach would
// have noticed it themselves — the value here is confirmation and a number to
// say out loud, not detection.

// Share of your own possession spent in your own third before it is worth
// saying. An even spread across three thirds is 33%, so this is already well
// clear of ordinary.
const PINNED_BACK_SHARE = 0.45;

// Metres a shape figure has to move. Matches MIN_DRIFT_M in pitchiq/metrics.py, and
// is about a player's worth of spacing.
const SHAPE_MOVE_M = 3.0;

// Giveaways in your own defensive third before it is a pattern rather than a
// bad minute.
const DANGEROUS_GIVEAWAYS = 5;

const SHAPE_WORDS = {
    width_m: ['wider', 'narrower'],
    depth_m: ['longer front to back', 'shorter front to back'],
    compactness_m: ['more spread out', 'more compact'],
    // Higher means further from the goal this side was defending. Said as a
    // direction on the pitch rather than as a figure rising, because "line
    // height up 6m" reads as an improvement and this has no good direction: a
    // side that pushed up won the ball higher and left more grass behind it.
    line_m: ['higher up the pitch', 'deeper'],
};

/**
 * What the video says about the half, as sentences rather than rows.
 *
 * `cv` is the published `cvStats/summary` document. Returns
 * `[{ title, detail }]`, worst-first-ish and often empty — empty is the correct
 * and common answer, and a caller should render nothing rather than a heading
 * over a blank space.
 *
 * Reads only `teams.team_a`, which is always the coach's own side.
 *
 * `options.videoOffsetS` only affects the minutes quoted in the pressing read.
 * Left out it quotes video minutes, which are the right minutes when nobody has
 * synced the clock — a wrong match minute reads as fact, a video minute reads
 * as a position in the footage.
 *
 * `options.goals` is the tagged score for our side, when there is one. It
 * replaces the video's goal count in the chances read: the scoreline at the
 * top of the same page is the tagged one, and a read saying "2 scored" under a
 * 1–0 is the page arguing with itself. The video still supplies the shots and
 * the expected goals, which nobody tags.
 */
export function cvReads(cv, options = {}) {
    const ours = cv?.teams?.team_a;
    if (!ours) return [];

    const reads = [];
    const pct = (v) => Math.round(v * 100);

    // Where the ball was, not just how much of it you had. The two come apart
    // exactly where it matters: a side pinned in its own half can hold 60% of
    // the ball and be losing.
    const territory = ours.territory;
    if (territory && territory.defensive >= PINNED_BACK_SHARE) {
        reads.push({
            title: 'You had the ball, but not where it counts',
            detail: `${pct(territory.defensive)}% of your possession was in your `
                + `own third, and ${pct(territory.attacking)}% in theirs.`,
        });
    }

    // Whether the shape held. Deliberately not toned good or bad — a side that
    // tightened up was well-drilled or was pinned back, and this cannot tell
    // the difference.
    const change = ours.shape_drift?.change || {};
    const moved = Object.entries(SHAPE_WORDS)
        .map(([key, [grew, shrank]]) => [key, change[key], grew, shrank])
        .filter(([, value]) => value != null && Math.abs(value) >= SHAPE_MOVE_M);

    if (moved.length) {
        reads.push({
            title: 'Your shape changed during the half',
            detail: moved
                .map(([, value, grew, shrank]) =>
                    `${Math.round(Math.abs(value))}m ${value > 0 ? grew : shrank}`)
                .join(', ') + ' by the end of it.',
        });
    }

    // Giveaways in front of your own goal. A single turnover count cannot say
    // this, which is why it is counted by third.
    const lost = ours.turnovers_by_third;
    if (lost && lost.defensive >= DANGEROUS_GIVEAWAYS) {
        reads.push({
            title: `${lost.defensive} giveaways in your own third`,
            detail: 'Passes lost in front of your own goal — the ones that turn '
                + 'straight into a chance against you.',
        });
    }

    // Whether the press lasted. The half-time page is the one place this read
    // can still be acted on, which is most of why it exists.
    const faded = pressingRead(pressingTrend(ours.pressing_segments, options));
    if (faded) reads.push(faded);

    // Chances created against chances taken. Both directions are worth saying:
    // one is bad luck or bad finishing, the other is a lead that flatters.
    const goals = options.goals ?? ours.goals;
    if (ours.xg != null && goals != null && ours.shots) {
        const gap = goals - ours.xg;
        if (gap <= -1.0) {
            reads.push({
                title: 'You have made more than you have taken',
                detail: `${ours.shots} shots worth about ${ours.xg.toFixed(1)} `
                    + `expected goals, and ${goals} scored.`,
            });
        } else if (gap >= 1.0) {
            reads.push({
                title: 'The scoreline is ahead of the chances',
                detail: `${goals} from about ${ours.xg.toFixed(1)} expected `
                    + `goals across ${ours.shots} shots.`,
            });
        }
    }

    return reads;
}
