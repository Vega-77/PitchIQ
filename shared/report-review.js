// The review feed, which merges the two records of a match, and the
// reviewer's scorecard.
//
// Part of `report.js`, which re-exports it.

import { matchClockMap } from './report-clock.js?v=144';

// ------------------------------------------------------ scoring the reviewer
//
// The review tool has been collecting verdicts since it shipped and computing
// nothing from them beyond "84 of 512 checked". The two numbers it exists to
// produce are precision and recall, and they need different halves of the data:
// precision comes from judging what the pipeline claimed, recall only from
// recording what it never claimed at all. A detector that finds six passes a
// half and gets all six right has perfect precision and is useless.
//
// The awkward case is an edit. A reviewer who changes a "tackle" to an
// "interception" has said two things at once: the pipeline was wrong to call it
// a tackle, and it was right that *something* happened there. Both matter, and
// collapsing them either way produces a flattering number. So an edit that
// changes the type counts against the type it claimed and in favour of the type
// it should have been.

// -------------------------------------------------- two records, one column
//
// Phase 11 opens by saying the review tool is "pre-populated with Phase 3's
// live-tagged events and subs, plus Phase 10's CV candidates — the reviewer's
// job is verifying/correcting/filling gaps, not labeling from scratch". Half of
// that was true. The list held the pipeline's candidates and nothing else; the
// tagged log sat in a different strip on a different part of the page, and a
// reviewer wanting to know what a human had said about 34:11 had to scroll away
// from the row they were judging and find it by eye.
//
// That is the wrong shape for the actual work. Judging a candidate is almost
// always a question about context — *was the ball even in play, and what had
// just happened?* — and the log is the only record that can answer it. So the
// two records are merged into one column, in time order.
//
//     What a tagged row is, and what it is not.
//
// It is a human's own record of the match, made at the time. There is nothing
// for a reviewer to confirm about it and it carries no verdict buttons. It is
// **never** a candidate: it does not enter precision, it does not enter recall,
// and it does not move the "checked so far" count. A tagged corner is not
// something the pipeline claimed, so counting it as agreement would inflate
// every figure on the scorecard with work nobody did.
//
// The one place the log does more than sit there is a **goal it tapped that no
// candidate stands near**. That is a miss the pipeline made and a human already
// proved, and until now recording it meant typing a clock into a text box from
// memory. One tap is the head start the roadmap is asking for.

export const FROM_VIDEO = 'video';
export const FROM_TAGGED = 'tagged';

// The two ways to work through the feed.
export const BY_CLOCK = 'clock';
export const BY_DOUBT = 'doubt';

/**
 * Reorder the merged feed, without changing what is in it.
 *
 * The Testing Strategy has claimed since it was written that this tool "sorts by
 * lowest confidence first, so a human's limited review time goes to what's
 * likely wrong instead of skimming everything uniformly". It never did — the
 * feed is chronological, and has been since it existed.
 *
 * Chronological is not a bug, though, which is why this is a choice rather than
 * a correction. Every row seeks the video, and going in match order means
 * scrubbing forward through a half once; going by doubt means jumping back and
 * forth across ninety minutes for every verdict. That is the cost the original
 * claim never accounted for.
 *
 * **`BY_DOUBT` buys a better use of an hour and pays for it in what the numbers
 * then mean.** Checking the least sure events first is the fastest way to find
 * what the detector gets wrong, and it makes the reviewed set a deliberately
 * pessimistic sample — precision measured over it is a floor, not an estimate.
 * `reviewScore` already reports "out of the N you have checked"; `orderCaveat`
 * below is what says the N was not drawn evenly.
 *
 * Tagged rows have no confidence — the log is a person, not a detector, and
 * there is no doubt attached to a tap. They keep their place on the clock
 * relative to each other and sort after the candidates, so the other record
 * stays readable rather than being shuffled into a ranking it is not part of.
 */
export function orderFeed(feed, order = BY_CLOCK) {
    const items = [...(feed || [])];
    if (order !== BY_DOUBT) return items;

    const doubt = (item) => (
        item.source === FROM_VIDEO && item.event?.confidence != null
            ? item.event.confidence
            : Infinity
    );
    return items.sort(
        (a, b) => doubt(a) - doubt(b) || a.clockS - b.clockS,
    );
}

/**
 * What reading in doubt order does to the numbers underneath.
 *
 * Only said while it is true, and it is about the sample rather than the tool:
 * the scorecard is unchanged, what changed is how the events in it were chosen.
 */
export function orderCaveat(order, checked = 0) {
    if (order !== BY_DOUBT || !checked) return '';
    return 'You are checking the least sure first, so these are the hardest '
        + `${checked} the video found — precision here is a floor, not an average.`;
}

/**
 * How close a tagged entry has to sit before it is worth showing on a
 * candidate's own row.
 *
 * Six seconds is a restart: the whistle, the walk, the throw. Wider and every
 * pass in a busy passage picks up a foul from further away than it was caused
 * by; narrower and the throw-in itself — the case this exists for, because it
 * is the touch the detector gets wrong most — stops being labelled.
 */
export const NEARBY_TAG_S = 6.0;

/**
 * How far from a tagged goal a candidate shot may sit and still be that goal.
 *
 * Mirrors `GOAL_WINDOW_S` in `pitchiq/reconcile.py`, for the same reason: a tagger
 * taps a goal after the ball crosses the line and usually after the celebration
 * has started. The two numbers have to agree, or the browser would offer to
 * record a miss for a goal the pipeline's own reconciliation counted as found.
 */
export const GOAL_PAIR_S = 15.0;

/** How close a recorded miss has to be to count as the same miss. */
const SAME_MISS_S = 5.0;

const PERIOD_KIND = 'period';

/**
 * The pipeline's candidates and the tagged log, merged in time order.
 *
 * `events` are in **video** seconds and the log in **match clock** seconds, so
 * `clock` does the conversion once here rather than at every comparison — the
 * same rule `pitchiq/phases.py` keeps, and it matters more than usual because the
 * windows above are seconds wide and a half-time interval is fifty times that.
 *
 * Period markers are left out. `halftime` and `kickoff_2nd` share a clock
 * reading with everything else in the interval, so a row for one would claim a
 * position in the order that it does not have.
 */
export function reviewFeed(events, log, options = {}) {
    const {
        clock = matchClockMap({}),
        missed = [],
        nearbyS = NEARBY_TAG_S,
        goalPairS = GOAL_PAIR_S,
    } = options;

    const candidates = (events || []).map((event) => {
        const { clockS, period } = clock.toClock(event.timestampS || 0);
        return { source: FROM_VIDEO, event, id: event.id, type: event.type, clockS, period };
    });

    const tagged = (log || [])
        .filter((e) => e && e.kind !== PERIOD_KIND && typeof e.matchClockS === 'number')
        .map((entry, index) => ({
            source: FROM_TAGGED,
            entry,
            // Not the log document's own id: an entry has one and a sub does
            // not, and a row keyed on undefined would collide with every other
            // sub in the half.
            id: `tag:${index}`,
            type: entry.kind === 'sub' ? 'sub' : entry.type,
            clockS: entry.matchClockS,
            period: null,
        }));

    // Only real ball events give a candidate its context. A substitution says
    // the tagger was busy, not that the ball was anywhere in particular.
    const context = tagged.filter((item) => item.type !== 'sub');
    for (const item of candidates) {
        const near = nearest(context, item.clockS, nearbyS);
        item.nearbyTag = near
            ? { type: near.type, clockS: near.clockS, gapS: near.clockS - item.clockS }
            : null;
    }

    // A goal the log recorded and no candidate stands near. `shot` rather than
    // `goal` because the pipeline's vocabulary has no goal in it — a goal is a
    // shot whose outcome was one — and **any** shot nearby counts as found,
    // even one scored as saved. Recall asks whether the moment was found, which
    // is the same rule `reviewScore` applies to a retyped event.
    const shots = candidates.filter((item) => item.event.type === 'shot');
    for (const item of tagged) {
        item.suggestion = null;
        if (item.type !== 'goal') continue;
        if (nearest(shots, item.clockS, goalPairS)) continue;
        item.suggestion = {
            type: 'shot',
            clockS: item.clockS,
            recorded: (missed || []).some(
                (m) => m && m.type === 'shot'
                    && Math.abs((m.clockS ?? 0) - item.clockS) <= SAME_MISS_S,
            ),
        };
    }

    // Ties go to the video. The tagger taps after the ball crosses the line, so
    // a shot and the goal it became read in the order they happened.
    const rank = (item) => (item.source === FROM_VIDEO ? 0 : 1);
    return [...candidates, ...tagged]
        .sort((a, b) => a.clockS - b.clockS || rank(a) - rank(b));
}

function nearest(items, clockS, windowS) {
    let best = null;
    for (const item of items) {
        const gap = Math.abs(item.clockS - clockS);
        if (gap > windowS) continue;
        if (!best || gap < Math.abs(best.clockS - clockS)) best = item;
    }
    return best;
}

/**
 * The three verdicts, exported because the only thing that writes one lives in
 * another file.
 *
 * `coach/review.js` renders the three buttons and `decide` sends whatever the
 * pressed one carries straight to Firestore, so that button's own attribute is
 * the real definition of a verdict. It used to be three string literals in an
 * HTML template, five hundred lines below three constants of the same meaning
 * in the same file, and a third copy of all three down here. None of the three
 * could see the others, and `firestore.rules` says in as many words that it
 * does not validate the shapes inside `byEvent`.
 *
 * Worth more than a tidiness argument, because `hasVerdict` below asks only
 * whether `status` is a non-empty string. An unrecognised verdict is therefore
 * not ignored, it is counted: `reviewScore` finds it is neither `rejected` nor
 * an `edited` retype and scores it a true positive. Rename the reject button's
 * attribute and a clip whose candidates a coach threw half of away reports
 * near-perfect precision, with nothing raised and nothing logged.
 */
export const CONFIRMED_STATUS = 'confirmed';
export const REJECTED_STATUS = 'rejected';
export const EDITED_STATUS = 'edited';

/**
 * Has anybody actually said whether this event happened?
 *
 * `byEvent` holds three separate answers under one key: whether the pipeline
 * was right to call this a shot (`status`), what the shot did (`result`), and
 * whether it was headed (`header`). Only the first is a verdict. An entry
 * carrying just a result is a coach saying "that one was saved" — a statement
 * about a shot they have not yet agreed was a shot.
 *
 * Every count of how much has been reviewed goes through here, because the
 * alternative is what it replaced: four places each deciding for themselves
 * what an entry means, and a scorecard disagreeing with the line above it.
 */
export const hasVerdict = (decision) => Boolean(decision?.status);

/**
 * The answers that still have a question attached.
 *
 * A CV event id is `frame:track:kind` (`pitchiq/events.py`) and a cluster id is the
 * clusterer's own integer label. Both are handed out fresh on every pipeline
 * run, and nothing clears `cvReview` or `cvMapping` when a new run lands — so
 * after a re-publish an answer map holds keys naming events and figures that no
 * longer exist, next to the ones that do.
 *
 * Counting the map is therefore not the same as counting the work done, and it
 * is wrong in the direction that hurts: it reports more finished than there is,
 * and it overstates hardest exactly when a re-run has created the most left to
 * do. Two of the counters that did it that way subtracted from the current
 * total and hid their rail badge behind a `> 0` guard, so a stale map did not
 * just inflate the figure — it silenced the prompt to go and look.
 *
 * `reviewScore` has always been immune to this, because it walks the events and
 * looks each one up rather than the other way round. This is that same move,
 * pulled out to where the counters can share it.
 *
 * Keys compare as strings: `cvMapping` is keyed by a cluster id that became one
 * on the way into Firestore.
 */
export function currentAnswers(ids, answers) {
    const live = new Set((ids || []).map((id) => String(id)));
    return Object.entries(answers || {})
        .filter(([key]) => live.has(key))
        .map(([, answer]) => answer);
}

function emptyScore() {
    return {
        truePositives: 0,   // claimed this type, and it was
        falsePositives: 0,  // claimed this type, and it was not
        detected: 0,        // really this type, and the moment was found
        missed: 0,          // really this type, and nothing was found
        unreviewed: 0,
        precision: null,
        recall: null,
    };
}

function ratio(numerator, denominator) {
    // Null, not zero. Zero is a measurement; this is the absence of one, and a
    // scorecard reading 0% for a type nobody has looked at would be read as a
    // detector that gets everything wrong.
    return denominator ? numerator / denominator : null;
}

/**
 * Precision and recall per event type, from a coach's review decisions.
 *
 * `events` is `cvStats/events`'s list, `review` is the `cvReview/decisions`
 * document: `{ byEvent: {id: {status, type?, playerId?}}, missed: [{clockS,
 * type}] }`.
 *
 * Everything here describes **the events actually reviewed**, and the caller
 * must say so on screen. Precision over twelve of five hundred events is a fact
 * about those twelve, and a reviewer who checked the twelve most obvious ones
 * has not measured the detector.
 */
export function reviewScore(events, review) {
    const byEvent = review?.byEvent || {};
    const byType = {};
    const at = (type) => (byType[type] = byType[type] || emptyScore());

    for (const event of events || []) {
        const claimed = event.type;
        const decision = byEvent[event.id];

        // A verdict, specifically. The same map also holds what a coach said a
        // shot *did*, and an entry carrying only that has said nothing about
        // whether the pipeline was right to call it a shot — counting it as a
        // confirmation would let the xG log quietly inflate this scorecard.
        if (!hasVerdict(decision)) {
            at(claimed).unreviewed += 1;
            continue;
        }

        // An edit that only reassigns the player leaves the type standing, and
        // is a success for the type — the pipeline found the right kind of
        // thing and pinned it on the wrong person. Identity is a separate
        // problem with its own separate fix.
        const truth = decision.status === EDITED_STATUS && decision.type
            ? decision.type
            : claimed;

        if (decision.status === REJECTED_STATUS) {
            at(claimed).falsePositives += 1;
            continue;
        }

        if (truth === claimed) {
            at(claimed).truePositives += 1;
            at(claimed).detected += 1;
        } else {
            at(claimed).falsePositives += 1;
            // Found, but called something else. Still found, which is the only
            // question recall asks.
            at(truth).detected += 1;
        }
    }

    for (const miss of review?.missed || []) {
        if (miss?.type) at(miss.type).missed += 1;
    }

    for (const score of Object.values(byType)) {
        score.precision = ratio(
            score.truePositives, score.truePositives + score.falsePositives,
        );
        score.recall = ratio(score.detected, score.detected + score.missed);
    }

    const overall = emptyScore();
    for (const score of Object.values(byType)) {
        for (const key of [
            'truePositives', 'falsePositives', 'detected', 'missed', 'unreviewed',
        ]) {
            overall[key] += score[key];
        }
    }
    overall.precision = ratio(
        overall.truePositives, overall.truePositives + overall.falsePositives,
    );
    overall.recall = ratio(overall.detected, overall.detected + overall.missed);

    return { byType, overall };
}

/**
 * The reviewed set as a file, for tuning a detector on later.
 *
 * This is the reason the review tool is worth using on footage whose numbers
 * mean nothing yet: it produces labelled data as a side effect, and labelled
 * data is what a fine-tune needs. Until now those labels could only be read
 * back out of the Firestore console one document at a time.
 *
 * `meta` carries whatever says which match this is. A labels file that cannot
 * say what it belongs to is worthless in a month, which is exactly when someone
 * will open it.
 *
 * The two halves are on **different clocks**, and the field names say so rather
 * than leaving it to be discovered. A pipeline event is stamped in video
 * seconds; a recorded miss is typed by a human off the match clock. They are
 * related by `videoOffsetS`, which is why it travels in the file — converting
 * here would bake in whatever the offset happened to be at export time, and the
 * offset is the number in this app most likely to be corrected later.
 */
export function reviewLabels(events, review, meta = {}) {
    const byEvent = review?.byEvent || {};

    const labels = {
        format: 'pitchiq-review-labels',
        version: 1,
        exportedAt: new Date().toISOString(),
        ...meta,
        // Stated rather than implied. A consumer that treats the unreviewed
        // events as negatives would be training on the pipeline's own guesses.
        note: 'Only events a human touched are labelled. Anything absent from '
            + '`labelled` was never looked at, and is not a negative example. '
            + 'labelled[].videoS is a position in the video; missed[].clockS is '
            + 'a match-clock reading a person typed. videoS = clockS + '
            + 'videoOffsetS. labelled[].result is what a coach said a shot did, '
            + 'and labelled[].xg is what the model predicted before anyone '
            + 'looked — the pair is the only ground truth this system produces.',
        labelled: (events || [])
            // Either half counts as a label. A shot marked "saved" with no
            // verdict beside it is still a human statement about that moment,
            // and it is the only statement in here a finishing model could
            // ever be trained on.
            .filter((event) => byEvent[event.id]?.status
                || byEvent[event.id]?.result || byEvent[event.id]?.header)
            .map((event) => {
                const decision = byEvent[event.id];
                return {
                    id: event.id,
                    videoS: event.timestampS,
                    claimedType: event.type,
                    verdict: decision.status ?? null,
                    actualType: decision.status === REJECTED_STATUS
                        ? null
                        : (decision.type || event.type),
                    // What the shot did, when somebody said. Null on everything
                    // that is not a shot, and on shots nobody has marked.
                    result: decision.result ?? null,
                    // Whether it was headed. The pipeline cannot see this at
                    // all, so every true here is a label that exists nowhere
                    // else — and body part is exactly what a pose model would
                    // need to be trained on.
                    header: decision.header === true,
                    xg: event.xg ?? null,
                    xgHeader: event.xgHeader ?? null,
                    playerId: decision.playerId ?? null,
                    trackId: event.trackId ?? null,
                    confidence: event.confidence ?? null,
                    inPlay: event.inPlay ?? null,
                };
            }),
        // The other half, and the one nothing else in the system can supply: a
        // thing the pipeline never saw leaves no record to disagree with.
        missed: (review?.missed || []).map((miss) => ({
            clockS: miss.clockS,
            type: miss.type,
            playerId: miss.playerId ?? null,
        })),
    };

    // Counted off the two arrays above rather than off `byEvent`. A re-run
    // renumbers every event id and nothing clears the old decisions, so the map
    // can hold keys this file has no row for — and a summary that disagrees
    // with the records under it is worse than no summary at all, in a file
    // whose whole purpose is to still be trustworthy in a month.
    labels.counts = {
        events: (events || []).length,
        labelled: labels.labelled.length,
        missed: labels.missed.length,
    };

    return labels;
}
