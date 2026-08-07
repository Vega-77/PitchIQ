// One measure across a season, as a small chart meant to be read in a row of
// them.
//
// The arithmetic and every refusal are in `season.js`, which imports nothing
// and is fully tested. This is only the drawing, and the drawing makes three
// claims that have to be defended.
//
//     Dots, not bars — and therefore not a zero baseline.
//
// Everywhere else on this site a bar starts at zero, because a bar's *length*
// is the quantity and a truncated axis exaggerates it. A dot is different: its
// height is a position, not a length, and forcing a hundred metres-per-minute
// onto a zero baseline squashes a season's whole range into two pixels of a
// tall empty chart. So the axis fits the data and the two ends of it are
// printed on the chart, which is the price of the change.
//
//     A line only between matches that were both measured.
//
// Consecutive points are joined; a point either side of an unmeasured match is
// not. Drawing through the gap would say the figure passed smoothly through
// values nobody recorded, and the gaps are exactly where this pipeline is
// weakest.
//
//     Dot area carries the minutes behind the point.
//
// The same rule as the passing network and the shot map, for the same reason: a
// figure from twelve tracked minutes and one from seventy are not the same kind
// of evidence, and nothing else on the chart says which is which.

const NS = 'http://www.w3.org/2000/svg';

const W = 100;
const H = 32;
const PAD_Y = 4;

// Metres of nothing, in viewBox units, kept above and below the data so the
// outermost dots are not clipped by the frame.
const MIN_R = 1.1;
const MAX_R = 2.6;

// A flat season would divide by zero working out where to put things. Given one
// value, or several identical ones, everything sits on the middle line — which
// is the honest picture of a figure that did not move.
const FLAT = 1e-9;

function el(name, attrs = {}) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

/**
 * Hang a hover label on a mark.
 *
 * A named helper rather than `node.append(el('title')).textContent = …`, which
 * is what this was and which silently sets a property on `undefined` —
 * `append` returns nothing where `appendChild` returns the node. Every chart in
 * this repo has one of these chains in it and only this one got it wrong.
 */
function titled(node, text) {
    const label = el('title');
    label.textContent = text;
    node.appendChild(label);
    return node;
}

/**
 * Dot radius from the minutes behind the point, scaled so **area** carries them.
 *
 * Clamped at the bottom rather than offset from it. Adding a floor to the
 * radius looks like the same thing and is proportional to nothing — the mistake
 * that made the passing network flatten a three-to-one difference into
 * 1.85-to-one, caught only by measuring the rendered circles.
 */
export function pointRadius(weight, heaviest) {
    if (!heaviest || !(weight > 0)) return MIN_R;
    return Math.max(MIN_R, MAX_R * Math.sqrt(Math.min(1, weight / heaviest)));
}

/**
 * Where a value sits vertically, and the padded range it was placed in.
 *
 * The range is padded outward from the observed extremes rather than from zero;
 * see the note at the top for why this chart is allowed to do that and a bar
 * chart is not.
 */
export function formScale(form) {
    const { low, high } = form || {};
    if (low == null || high == null) return null;

    const span = high - low;
    // A tenth of the range each way, or a tenth of the value itself when every
    // match landed on the same number and there is no range to take a share of.
    const pad = span > FLAT ? span * 0.1 : Math.max(Math.abs(high) * 0.1, 1);
    const min = low - pad;
    const max = high + pad;

    return {
        min, max,
        y: (value) => H - PAD_Y - ((value - min) / (max - min)) * (H - 2 * PAD_Y),
    };
}

/** Horizontal centre of the slot for match `index` of `count`. */
export function slotX(index, count) {
    if (count <= 1) return W / 2;
    return MAX_R + (index / (count - 1)) * (W - 2 * MAX_R);
}

/**
 * One season trace as an `<svg>`.
 *
 * Unmeasured matches are drawn as a tick on the floor rather than skipped, so
 * the horizontal axis stays a run of matches instead of a run of the matches
 * that happened to work.
 */
export function formSparkline(form) {
    const scale = formScale(form);
    // Uniform scaling, unlike the minutes chart next door — that one draws
    // rectangles, which stretch without saying anything untrue, and this one
    // draws circles, which become ovals. `.form-plot` carries the matching
    // `aspect-ratio` so `meet` fills the box instead of letterboxing inside it.
    const svg = el('svg', {
        viewBox: `0 0 ${W} ${H}`,
        preserveAspectRatio: 'xMidYMid meet',
        class: 'form-svg',
        role: 'img',
        'aria-label': `${form.label} across ${form.points.length} matches`,
    });
    if (!scale) return svg;

    const count = form.points.length;
    const at = (point) => ({ x: slotX(point.index, count), y: scale.y(point.value) });

    // The season figure, as a line to read the dots against. Drawn first so a
    // dot sitting on it stays legible.
    if (form.pooled != null) {
        const y = scale.y(form.pooled);
        svg.append(el('line', {
            x1: 0, y1: y, x2: W, y2: y,
            class: 'form-mean',
            'vector-effect': 'non-scaling-stroke',
        }));
    }

    // Segments, never a single path: a path through every measured point would
    // bridge the gaps, which is the one thing this chart must not do.
    for (let i = 1; i < count; i += 1) {
        const previous = form.points[i - 1];
        const current = form.points[i];
        if (previous.value == null || current.value == null) continue;
        const a = at(previous);
        const b = at(current);
        svg.append(el('line', {
            x1: a.x, y1: a.y, x2: b.x, y2: b.y,
            class: 'form-link',
            'vector-effect': 'non-scaling-stroke',
        }));
    }

    for (const point of form.points) {
        if (point.value == null) {
            // A match that happened and could not be measured. On the floor and
            // faint — present, and clearly not a value.
            const tick = el('rect', {
                x: slotX(point.index, count) - 0.4, y: H - 2.2,
                width: 0.8, height: 2.2, rx: 0.4,
                class: `form-gap${point.thin ? ' is-thin' : ''}`,
            });
            svg.append(titled(tick, point.thin
                ? `${point.opponent} — filmed, tracked for too few minutes to place`
                : `${point.opponent} — not filmed`));
            continue;
        }

        const { x, y } = at(point);
        const dot = el('circle', {
            cx: x, cy: y,
            r: pointRadius(point.weight, form.heaviest).toFixed(2),
            class: 'form-dot',
        });
        svg.append(titled(dot, `${point.opponent} — ${form.format(point.value)}`
            + `, over ${Math.round(point.weight)} tracked minutes`));
    }

    return svg;
}

/**
 * A titled card holding one trace, its season figure and the range it spans.
 *
 * The two ends of the axis are printed because the axis does not start at zero.
 * A chart that rescales silently makes every season look equally dramatic.
 */
export function formCard(form) {
    const card = document.createElement('div');
    card.className = 'form-card';

    const scale = formScale(form);
    const head = document.createElement('div');
    head.className = 'form-head';
    head.innerHTML = `<span class="form-label"></span><span class="form-mean-v"></span>`;
    head.querySelector('.form-label').textContent = form.label;
    head.querySelector('.form-mean-v').textContent =
        form.pooled == null ? '—' : form.format(form.pooled);
    card.append(head);

    const plot = document.createElement('div');
    plot.className = 'form-plot';
    plot.append(formSparkline(form));
    card.append(plot);

    const foot = document.createElement('div');
    foot.className = 'form-foot';
    foot.textContent = scale
        ? `${form.format(form.low)} to ${form.format(form.high)}`
        : '';
    card.append(foot);

    return card;
}

/**
 * Put a row of traces into `host`. False when there is nothing worth drawing.
 *
 * `minPoints` is the guard against the two-dots-and-a-line chart, which is a
 * much stronger claim than the two numbers it is made of.
 */
export function renderForms(host, forms, { minPoints = 3 } = {}) {
    if (!host) return false;
    host.innerHTML = '';

    const worth = (forms || []).filter((f) => f.measured >= minPoints);
    if (!worth.length) return false;

    for (const form of worth) host.append(formCard(form));
    return true;
}
