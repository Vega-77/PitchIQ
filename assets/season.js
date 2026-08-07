// A player's season, as a run of matches rather than one pile of totals.
//
// `seasonTotals` in db.js adds the video-derived fields up across every filmed
// match, and for a headline count that is right. For anything you would compare
// — is she covering more ground than she was, is his passing holding up — a sum
// is the wrong shape twice over. It hides which matches it came from, and it
// assumes the matches were measured alike when they were not: each one was
// tracked for however long the tracker held on, and that share has ranged from
// most of a match to a few minutes of one.
//
//     Everything here is a rate, and the rates are pooled, not averaged.
//
// The season figure is the sum of the numerators over the sum of the
// denominators, which weights each match by how much of it was actually
// measured. Averaging the per-match rates instead gives a twelve-minute
// fragment the same say as a full match, and on this pipeline the fragments are
// the common case. The two answers differ by a lot and only one of them is the
// player's season.
//
//     Which measures survive being watched for only part of a match.
//
// Not all of them, and the difference is not a detail:
//
//   * A **rate** — metres per minute, touches per minute — is measured over
//     exactly the span it is divided by. Less coverage makes it noisier and
//     does not push it either way.
//   * A **ratio of two counts from the same span** — pass accuracy — is the
//     safest of all. Both halves are missing the same minutes.
//   * A **maximum** — top speed — is biased *down*, and unlike the others it
//     does not average out. The fastest thing a player did is likelier to have
//     happened in the minutes nobody was watching the more minutes nobody was
//     watching. A season best from thin coverage is a floor, not a figure.
//
// That last one is the reason `biased` exists on a measure at all, and why the
// note under the chart says it in words. A downward bias plotted beside three
// unbiased traces reads as a player who is slowing down.

// Minutes of tracking below which a single match is not a point on a chart.
//
// Ten minutes is a sixth of a half. A rate from less than that is a statement
// about one spell of one match, and the eye reads every dot on a line as the
// same kind of thing — so the thin ones are counted, pooled into nothing, and
// named underneath instead of drawn.
export const MIN_POINT_MINUTES = 10;

// The same idea for a ratio whose denominator is a count rather than a clock.
export const MIN_POINT_ATTEMPTS = 10;

const num = (value) => (typeof value === 'number' && isFinite(value) ? value : null);

/**
 * The measures worth tracing across a season.
 *
 * `of` over `per` is the rate; `weight` is what the point rests on, which is
 * the minutes in every case including the ones divided by something else.
 * `floor` is in the units of `per`.
 */
export const FORM_MEASURES = [
    {
        key: 'distancePerMin',
        label: 'Metres per minute',
        kind: 'rate',
        of: (r) => num(r?.cvDistanceM),
        per: (r) => num(r?.cvMinutesTracked),
        weight: (r) => num(r?.cvMinutesTracked),
        floor: MIN_POINT_MINUTES,
        format: (v) => Math.round(v),
        biased: null,
    },
    {
        key: 'touchesPerMin',
        label: 'Touches per minute',
        kind: 'rate',
        of: (r) => num(r?.cvTouches),
        per: (r) => num(r?.cvMinutesTracked),
        weight: (r) => num(r?.cvMinutesTracked),
        floor: MIN_POINT_MINUTES,
        format: (v) => v.toFixed(1),
        biased: null,
    },
    {
        key: 'passAccuracy',
        label: 'Pass accuracy',
        kind: 'rate',
        of: (r) => num(r?.cvPassesCompleted),
        per: (r) => num(r?.cvPassesAttempted),
        weight: (r) => num(r?.cvMinutesTracked),
        floor: MIN_POINT_ATTEMPTS,
        format: (v) => `${Math.round(v * 100)}%`,
        biased: null,
    },
    {
        key: 'topSpeed',
        label: 'Top speed',
        kind: 'max',
        of: (r) => num(r?.cvTopSpeedKmh),
        per: (r) => num(r?.cvMinutesTracked),
        weight: (r) => num(r?.cvMinutesTracked),
        floor: MIN_POINT_MINUTES,
        format: (v) => `${v.toFixed(1)} km/h`,
        biased: 'low',
    },
];

export const MEASURE_BY_KEY = new Map(FORM_MEASURES.map((m) => [m.key, m]));

/**
 * One measure across a season, oldest match first.
 *
 * Every match the player has a published report for gets a slot, filmed or not.
 * Dropping the unfilmed ones and closing the gap would space four filmed
 * matches evenly across a chart and imply they happened evenly across the
 * season, which is a claim about time that nobody measured. A gap is drawn as a
 * gap.
 *
 * `pooled` is computed over exactly the points that are drawn — not over every
 * filmed match — so the reference line is the weighted average of what the
 * reader can see. Pooling over a wider set would be defensible arithmetic and
 * would put a line on the chart that the dots around it do not explain.
 */
export function seasonForm(reports, measure) {
    const spec = typeof measure === 'string' ? MEASURE_BY_KEY.get(measure) : measure;
    if (!spec) return null;

    // `playerSeason` hands these back newest first, and a season reads left to
    // right. Reversing here rather than at each call site is deliberate: it is
    // the kind of thing that gets fixed in one chart and not the next.
    const season = [...(reports || [])].reverse();

    const points = season.map((report, index) => {
        const of = spec.of(report);
        const per = spec.per(report);
        const weight = spec.weight(report);
        const filmed = of != null && per != null;
        const thin = filmed && per < spec.floor;
        return {
            index,
            matchId: report?.matchId ?? null,
            opponent: report?.opponentName || 'opponent',
            date: report?.matchDate || null,
            of, per,
            weight: weight ?? 0,
            filmed,
            thin,
            value: filmed && !thin && per > 0
                ? (spec.kind === 'max' ? of : of / per) : null,
        };
    });

    const drawn = points.filter((p) => p.value != null);
    const values = drawn.map((p) => p.value);

    let pooled = null;
    if (drawn.length && spec.kind === 'max') {
        pooled = Math.max(...values);
    } else if (drawn.length) {
        const per = drawn.reduce((sum, p) => sum + p.per, 0);
        pooled = per > 0 ? drawn.reduce((sum, p) => sum + p.of, 0) / per : null;
    }

    return {
        key: spec.key,
        label: spec.label,
        kind: spec.kind,
        biased: spec.biased,
        format: spec.format,
        points,
        pooled,
        measured: drawn.length,
        thin: points.filter((p) => p.thin).length,
        unfilmed: points.filter((p) => !p.filmed).length,
        heaviest: Math.max(0, ...points.map((p) => p.weight)),
        low: values.length ? Math.min(...values) : null,
        high: values.length ? Math.max(...values) : null,
    };
}

/** Every measure that has at least one point worth drawing. */
export function seasonForms(reports) {
    return FORM_MEASURES
        .map((measure) => seasonForm(reports, measure))
        .filter((form) => form && form.measured);
}

/**
 * Whether a run of matches is long enough for a chart to be a chart.
 *
 * Two dots joined by a line is not a trend; it is two numbers with a line
 * drawn through them, and a line is a much stronger claim than two numbers.
 */
export const MIN_FORM_POINTS = 3;

/**
 * The caveats under the whole row, or null when there is nothing to say.
 *
 * One note rather than one per chart, because it is the same match that is thin
 * or unfilmed in all of them and saying so four times reads as four separate
 * problems.
 *
 * The counts are worked out per *match* across every measure rather than read
 * off one of them. Nothing guarantees the measures agree — pass accuracy is
 * floored on attempts and the rest on minutes, so a match can be thin in one
 * and fine in another — and a caption that quietly assumed they agreed would be
 * right almost always, which is the worst frequency for a wrong number.
 */
export function formNote(forms, options = {}) {
    const { measured = 'filmed' } = options;
    const first = forms?.[0];
    if (!first) return null;

    const slots = first.points.length;
    let placed = 0;
    let thin = 0;
    for (let i = 0; i < slots; i += 1) {
        if (forms.some((f) => f.points[i]?.value != null)) placed += 1;
        else if (forms.some((f) => f.points[i]?.thin)) thin += 1;
    }

    const parts = [];
    parts.push(`${placed} of ${slots} matches ${measured} and tracked long enough `
        + 'to place on a line.');

    if (thin) {
        parts.push(`${thin} more ${thin === 1 ? 'was' : 'were'} filmed but `
            + 'followed for too few minutes to be a point — those minutes are '
            + 'left out of the season figure too, rather than quietly averaged in.');
    }

    // Named once, from the measures that actually carry it.
    if ((forms || []).some((f) => f.biased === 'low')) {
        parts.push('Top speed is the one figure here that partial coverage bends '
            + 'in a direction: the fastest thing a player did is likelier to have '
            + 'happened in minutes nobody was watching, so a season best from thin '
            + 'coverage is a floor rather than a figure.');
    }

    return parts.join(' ');
}
