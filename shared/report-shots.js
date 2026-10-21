// The shot ledger, header corrections, and the xG tally and its
// calibration.
//
// Part of `report.js`, which re-exports it.

import { REJECTED_STATUS, EDITED_STATUS } from './report-review.js?v=145';

// -------------------------------------------- marking the model's predictions
//
// The xG model was fitted on a hundred thousand-odd StatsBomb shots taken by
// professionals. Nothing in it has ever seen a high school pitch, and until
// somebody records what a shot actually did there is no evidence either way:
// every number on the shot map is a prediction nobody has ever marked.
//
// Two rules make the marking worth doing.
//
// **The verdict has to come from a person.** `Shot.outcome` is already in the
// report, inferred from a ball the pipeline sees in roughly 60% of frames, and
// grading one model against another model's guess measures the agreement of two
// guesses. So the pipeline's own reading is shown beside the buttons and never
// preselects one — a prefilled answer clicked past is an unmarked shot with a
// signature on it.
//
// **Goals are rare and a match is small.** Ten shots worth 1.2 xG between them
// will produce anywhere from none to four goals with nothing wrong anywhere, so
// a bare "predicted 1.2, scored 3" reads as a broken model and is not evidence
// of one. Everything below therefore carries the size of the gap this many
// shots could actually have detected, and refuses to call anything smaller.
//
// The tally is deliberately four numbers rather than a verdict. A verdict is
// only worth reading over a season, and a season is these four summed.

export const SHOT_RESULTS = [
    { value: 'goal', label: 'Goal' },
    { value: 'saved', label: 'Saved' },
    { value: 'blocked', label: 'Blocked' },
    { value: 'off_target', label: 'Off target' },
    { value: 'woodwork', label: 'Woodwork' },
];

const SHOT_RESULT_VALUES = new Set(SHOT_RESULTS.map((r) => r.value));

// The smallest miscalibration worth being able to see, as a share of the goals
// predicted.
//
// A share, not a number of goals, and this is the easy thing to get backwards.
// The band grows with the square root of the shot count and the prediction grows
// with the count itself, so a threshold measured in goals would call three shots
// conclusive and a season inconclusive — precisely inverted. At half, the
// question is "could this sample tell a good model from one that is 50% out",
// which on typical chances takes about 150 shots: a season of both teams' shots,
// and honest about being one.
const RESOLVABLE_SHARE = 0.5;

// How many goals a sample has to expect before the band is allowed to accuse
// the model of anything.
//
// The band is a normal approximation to a sum of coin flips, and that
// approximation is poor when the expected count is tiny — the real distribution
// is sharply skewed, so two standard deviations is nowhere near the 95% it
// implies. Without this gate, two half-chances and one lucky finish come out as
// "the model is rating these too low", which is precisely the over-reading every
// other line here exists to prevent. The usual np ≥ 5 rule of thumb, relaxed a
// little and applied from both ends.
const MIN_EXPECTED = 4;

const SHOT_TYPE = 'shot';

const round4 = (value) => Math.round(value * 1e4) / 1e4;

/**
 * Every detected shot, with whatever a coach has said happened to it.
 *
 * Built from `cvStats/events` rather than the shot map, because the map carries
 * positions and no ids — and an id is what lets a verdict outlive the page.
 *
 * Rejected and retyped shots stay in the list, marked rather than removed. A row
 * that disappears the moment you reject it looks like a bug, and the struck-out
 * row is the only thing that explains why the tally below just moved.
 */
export function shotLedger(events, review) {
    const byEvent = review?.byEvent || {};
    const rows = [];

    for (const event of events || []) {
        if (event.type !== SHOT_TYPE) continue;
        const decision = byEvent[event.id] || {};

        // Two ways a shot stops being one, and they have to be handled
        // together: rejected outright, or edited into some other kind of event.
        // Either way the moment is no longer a shot, and a chance the model was
        // never asked about cannot be evidence about the model.
        const retyped = decision.status === EDITED_STATUS
            && decision.type && decision.type !== SHOT_TYPE;

        // Which xG counts depends on a thing the camera cannot see, so the
        // pipeline sent both and the coach picks. No fallback when a header was
        // tagged on a run that predates the second reading: scoring it as a foot
        // shot anyway is exactly the error being corrected, so it drops out of
        // the check with a null instead.
        const header = decision.header === true;
        const xgFoot = event.xg ?? null;
        const xgHeader = event.xgHeader ?? null;

        rows.push({
            id: event.id,
            timestampS: event.timestampS ?? null,
            team: event.team ?? null,
            trackId: event.trackId ?? null,
            xgFoot,
            xgHeader,
            header,
            xg: header ? xgHeader : xgFoot,
            // What the pipeline made of it. Shown, never used as an answer.
            guessed: event.outcome ?? null,
            result: SHOT_RESULT_VALUES.has(decision.result) ? decision.result : null,
            counted: decision.status !== REJECTED_STATUS && !retyped,
        });
    }

    rows.sort((a, b) => (a.timestampS ?? 0) - (b.timestampS ?? 0));
    return rows;
}

/**
 * What the coach's header tags did to the total, or null if they tagged none.
 *
 * Worth stating as a movement rather than just showing a corrected figure. A
 * total that quietly drops from 1.42 to 1.05 between two visits looks like a
 * bug; the same drop with "three headers" beside it is the tool working.
 *
 * `unscorable` counts headers on runs old enough to carry only the foot
 * reading. Those leave the total rather than staying in it wrong, which is the
 * right answer and a surprising one, so it is said out loud.
 */
export function headerCorrection(rows) {
    let headers = 0;
    let unscorable = 0;
    let from = 0;
    let to = 0;

    for (const row of rows || []) {
        if (!row?.counted || !row.header) continue;
        headers += 1;
        if (row.xgHeader == null) {
            unscorable += 1;
            continue;
        }
        from += row.xgFoot ?? 0;
        to += row.xgHeader;
    }

    if (!headers) return null;
    return { headers, unscorable, from: round4(from), to: round4(to) };
}

/**
 * The header tags as a sentence, or null when there are none.
 *
 * The two halves are separate sentences because they are separate facts, and
 * running them together produced the reading this exists to stop: a match where
 * every tagged header was unscorable said "which took 0.00 xG down to 0.00",
 * which is arithmetically true and says the opposite of what happened.
 */
export function headerNote(correction) {
    if (!correction?.headers) return null;
    const { headers, unscorable, from, to } = correction;
    const scored = headers - unscorable;
    const parts = [];

    if (scored) {
        parts.push(`${scored} shot${scored === 1 ? '' : 's'} you tagged as a`
            + ` header ${scored === 1 ? 'is' : 'are'} scored as one, which took`
            + ` ${from.toFixed(2)} xG down to ${to.toFixed(2)}.`);
    }
    if (unscorable) {
        const it = unscorable === 1 ? 'it is' : 'they are';
        parts.push(`${unscorable}${scored ? ' more' : ''} came from a run made`
            + ' before the header figure existed, so'
            + ` ${it} left out of these totals rather than counted as foot`
            + ' shots. Re-running the pipeline brings'
            + ` ${unscorable === 1 ? 'it' : 'them'} back.`);
    }
    return parts.join(' ');
}

/**
 * Shot-map marks with the coach's body-part tags applied.
 *
 * Joined on `event_id`, which is why the pipeline started emitting one onto each
 * mark: a rounded timestamp is not an identity, and two shots inside the same
 * second would swap their corrections without anything looking wrong.
 *
 * Marks the ledger says nothing about pass through untouched, so a report from
 * before any of this existed renders exactly as it did.
 *
 * A header the run cannot score comes out with a **null** xG rather than its
 * foot figure. That is what makes the map agree with everything around it:
 * `shotSummary` skips a null and `markRadius` draws it at the floor, so the dot
 * stays on the pitch — the shot happened — while the caption's total, the
 * sentence under it and the check below all leave it out together. Keeping the
 * foot figure here would have left the map counting a shot the note beside it
 * said had been dropped.
 */
export function correctedShotMarks(marks, rows) {
    const byId = new Map();
    // Shots the coach said were not shots. Dropped rather than kept with a null
    // xG, because unlike an unscorable header — which happened and cannot be
    // scored — a rejected shot did not happen, and a dot on a shot map is a
    // claim that it did.
    const gone = new Set();
    for (const row of rows || []) {
        if (row?.id == null) continue;
        if (!row.counted) { gone.add(row.id); continue; }
        // `counted` as well as `header`, so this and `headerCorrection` are
        // always describing the same set of shots. A map that has quietly
        // corrected one more shot than the sentence under it claims is the kind
        // of half-a-goal discrepancy nobody ever tracks down.
        if (row.header) byId.set(row.id, row.xg);
    }
    if (!byId.size && !gone.size) return marks || [];

    return (marks || [])
        .filter((mark) => !gone.has(mark.event_id))
        .map((mark) => (byId.has(mark.event_id)
            ? { ...mark, xg: byId.get(mark.event_id), is_header: true }
            : mark));
}

/**
 * The four numbers a match contributes to the check, or null if it contributes
 * none.
 *
 * `variance` travels rather than a standard deviation because variances add and
 * standard deviations do not — a season is the sum of these, and storing the
 * root would make the season figure quietly wrong in the safe-looking direction.
 *
 * A shot with no xG is skipped on both sides at once. Counting its goal without
 * its prediction would credit the team with a goal the model was never asked to
 * predict, which is the one arithmetic mistake here that flatters nobody and
 * still ruins the answer.
 */
export function xgTally(rows) {
    let shots = 0;
    let predicted = 0;
    let scored = 0;
    let variance = 0;

    for (const row of rows || []) {
        if (!row?.counted || row.result == null || row.xg == null) continue;
        shots += 1;
        predicted += row.xg;
        // Bernoulli: a shot worth 0.5 is the most uncertain one there is, and a
        // tap-in worth 0.95 barely widens the band at all.
        variance += row.xg * (1 - row.xg);
        if (row.result === 'goal') scored += 1;
    }

    if (!shots) return null;
    return {
        shots,
        predicted: round4(predicted),
        scored,
        variance: round4(variance),
    };
}

/** Several matches' tallies as one. Null when none of them had anything. */
export function sumXgTallies(tallies) {
    const total = { shots: 0, predicted: 0, scored: 0, variance: 0 };
    let any = false;

    for (const tally of tallies || []) {
        if (!tally?.shots) continue;
        any = true;
        total.shots += tally.shots;
        total.predicted += tally.predicted || 0;
        total.scored += tally.scored || 0;
        total.variance += tally.variance || 0;
    }

    if (!any) return null;
    total.predicted = round4(total.predicted);
    total.variance = round4(total.variance);
    return total;
}

/**
 * What a tally says about the model, and how much it is entitled to say.
 *
 * Under the model each shot is its own coin weighted by its xG, so the goals a
 * set of shots should produce has mean `Σ xg` and variance `Σ xg(1−xg)`. Two
 * standard deviations is the gap this sample could have detected; anything
 * inside it is what chance does to a small number of shots, not a finding.
 *
 * `verdict`:
 *  - `model_low`  — more went in than predicted, past what chance covers
 *  - `model_high` — fewer did
 *  - `consistent` — no gap, on a sample that could have found a sizeable one
 *  - `inconclusive` — no gap, on a sample that could not have found one anyway
 *
 * The last two are the pair that matters. Collapsing them into "agrees" is how a
 * model gets declared fit for a season on the strength of nine shots.
 */
export function xgCalibration(tally) {
    if (!tally?.shots) {
        return {
            shots: 0, predicted: null, scored: null,
            sd: null, band: null, gap: null, verdict: null,
        };
    }

    const { shots, predicted, scored, variance } = tally;
    const sd = Math.sqrt(variance);
    const band = 2 * sd;
    const gap = scored - predicted;

    // A gap wider than the band is a real difference however wide the band is —
    // a sample too small to find a 50% error can still find a 300% one, and
    // that is the only unambiguous evidence this tool ever produces. But only
    // once the approximation behind the band holds at all: below `MIN_EXPECTED`
    // the accusation is an artefact of the maths, not a finding about football.
    const trustworthyBand = predicted >= MIN_EXPECTED
        && shots - predicted >= MIN_EXPECTED;

    let verdict;
    if (trustworthyBand && gap > band) verdict = 'model_low';
    else if (trustworthyBand && -gap > band) verdict = 'model_high';
    else if (!trustworthyBand || band >= predicted * RESOLVABLE_SHARE) {
        verdict = 'inconclusive';
    } else verdict = 'consistent';

    return { shots, predicted, scored, sd, band, gap, verdict };
}

/**
 * The calibration as a sentence, because the numbers alone mislead.
 *
 * "Predicted 1.2, scored 3" is the reading a coach will take from a table, and
 * on twelve shots it means nothing whatsoever. The band has to be in the same
 * breath as the difference or it will not be read at all.
 */
export function calibrationNote(cal, options = {}) {
    if (!cal?.shots) return null;
    const { over = 'you have marked' } = options;

    const shots = `${cal.shots} shot${cal.shots === 1 ? '' : 's'}`;
    const head = `The ${shots} ${over} were worth ${cal.predicted.toFixed(2)} xG`
        + ` between them, and ${cal.scored} went in.`;
    const band = cal.band.toFixed(1);
    const gap = Math.abs(cal.gap).toFixed(1);

    if (cal.verdict === 'inconclusive') {
        return `${head} That is inside the ±${band} goals chance alone moves a`
            + ` sample this size — ${shots} could not tell a good model from one`
            + ' that is half out either way. Keep marking.';
    }
    if (cal.verdict === 'consistent') {
        return `${head} That is inside the ±${band} goals chance alone moves a`
            + ' sample this size, and there are now enough shots that a model half'
            + ' out would have shown. So far the model and this pitch agree.';
    }
    const direction = cal.verdict === 'model_low'
        ? 'more than it expected, so it is rating these chances too low'
        : 'fewer than it expected, so it is rating these chances too high';
    return `${head} That is ${gap} goals ${direction} — further than the ±${band}`
        + ' goals chance accounts for on this many shots.';
}
