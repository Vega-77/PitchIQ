// Every shot, as a point on the attacking half, sized by how good a chance it
// was.
//
// The xG model has existed since before the CV work started and nothing has
// ever drawn a shot on a pitch, because no shot coordinate reached the browser:
// `events_payload` drops `start_m` from every event, for reasons that are still
// right for passes and carries. Shots are different — there are a dozen a half
// and they are the ones anybody wants to see placed.
//
//     Everything attacks right.
//
// The mirroring happens in `cv/report_json.py::shot_marks`, not here, so a
// second-half shot cannot end up plotted at the wrong end by a renderer that
// forgot. By the time a mark reaches this module, `x_m`/`y_m` already mean
// "position as if attacking the right-hand goal".
//
//     Area, not radius.
//
// A dot's size encodes xG through its area, because that is what the eye
// actually compares. Scaling the radius by xG makes a 0.4 chance look four
// times the chance of a 0.1 one instead of four times the area, and every shot
// map that does it overstates the good chances.
//
//     Unless the number cannot carry it.
//
// Sizing is a claim that two dots differ by the amount they look like they
// differ. `xgTrust` (assets/report.js) decides whether the calibration supports
// that claim, and hands down `'shot'`, `'total'` or `'none'`; anything but
// `'shot'` flattens every radius here. A map drawn to a precision the positions
// do not have is worse than a map with no sizes, because it is legible.

import {
    PITCH_LENGTH_M, PITCH_WIDTH_M, pitchMarkings,
} from './pitch-backdrop.js?v=91';

const NS = 'http://www.w3.org/2000/svg';

// Only the attacking half is drawn. A shot from inside your own half is worth
// about 0.005 xG and happens twice a season; giving it half the picture costs
// every other shot half its resolution.
const FROM_X = PITCH_LENGTH_M / 2;

// Metres of radius. The floor exists so a 0.01 chance is still visible as a
// thing that happened — a shot map that hides the bad ones flatters the team.
const MIN_R = 0.9;
const MAX_R = 3.4;

// What a dot draws at when the xG behind it is too loose to size by. Deliberately
// mid-range and identical for every shot: a map of same-sized dots says "these
// happened, here" and nothing more, which is exactly what a loose calibration
// supports. Sizing them anyway would draw differences finer than the error bar.
const FLAT_R = 1.8;

function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

/**
 * Radius in metres for a chance worth `xg`.
 *
 * Square-rooted so **area** scales with xG. A null xG — every shot before the
 * bridge was wired up, and any shot the model could not be run for — draws at
 * the floor rather than not at all: the shot happened whether or not it could
 * be scored.
 */
export function markRadius(xg, trust = 'shot') {
    if (trust !== 'shot') return FLAT_R;
    if (xg == null) return MIN_R;
    const clamped = Math.max(0, Math.min(1, xg));
    return MIN_R + (MAX_R - MIN_R) * Math.sqrt(clamped);
}

/** Which visual class a shot gets, from its outcome. */
export function markClass(mark) {
    if (mark?.outcome === 'goal') return 'is-goal';
    if (mark?.on_target) return 'is-on-target';
    return 'is-off';
}

/**
 * The hover label for one dot.
 *
 * Split out of the renderer so it can be tested without a DOM — it carries two
 * rules that are easy to break silently and impossible to see in a screenshot.
 *
 * The first: xG is dropped on exactly the bands the radius is. A number the map
 * has stopped drawing but a tooltip still reports is the same claim made
 * quietly, and the quiet one is what people write down.
 *
 * The second: a header says so. Its dot is smaller than a foot shot from the
 * same spot, and on a player's own page nothing else on screen would explain
 * why. A circle that shrank for an unstated reason is a claim the reader cannot
 * check.
 */
export function markLabel(mark, xgTrust = 'shot') {
    return [
        mark?.outcome === 'goal' ? 'Goal' : (mark?.on_target ? 'On target' : 'Off target'),
        mark?.is_header ? 'header' : null,
        (xgTrust === 'shot' && mark?.xg != null) ? `${mark.xg.toFixed(2)} xG` : null,
    ].filter(Boolean).join(' · ');
}

/**
 * Totals worth printing under the map: shots, on target, goals, and xG.
 *
 * xG is null rather than 0 when no shot carried one — a run before the model
 * was wired in has no expected goals, which is not the same as no chances.
 */
/**
 * Has this mark a position the pitch can actually take?
 *
 * `typeof === 'number'`, not `Number.isFinite(Number(v))` — the first attempt at
 * this guard used the latter and reproduced the exact bug it was written to
 * stop, because **`Number(null)` is `0`** and zero is finite. `Number('')` and
 * `Number([])` are zero too. The published marks are JSON numbers, so demanding
 * an actual number is both the correct test and the only one that fails closed.
 */
export const placeable = (mark) => (
    typeof mark?.x_m === 'number' && Number.isFinite(mark.x_m)
    && typeof mark?.y_m === 'number' && Number.isFinite(mark.y_m)
);

export function shotSummary(marks, trust = 'shot') {
    const list = marks || [];
    const scored = list.filter((m) => m.xg != null);
    return {
        shots: list.length,
        // Counted rather than hidden. Zero on every run the pipeline produces;
        // anything else means a mark reached a map with nowhere to go, and the
        // number of dots will be short by exactly this much.
        unplaced: list.filter((m) => !placeable(m)).length,
        onTarget: list.filter((m) => m.on_target).length,
        goals: list.filter((m) => m.outcome === 'goal').length,
        // Withheld entirely at `'none'`. Summing shots whose individual error
        // bars are wider than themselves does average the noise down, but the
        // total is still anchored to positions nobody should trust that far.
        xg: (trust !== 'none' && scored.length)
            ? scored.reduce((sum, m) => sum + m.xg, 0)
            : null,
    };
}

/**
 * The attacking half with every shot on it.
 *
 * `onPick` makes each shot something you can activate to seek the video to it,
 * which is the thing that turns a picture into something a coach uses. Without
 * one the marks are inert circles and no cursor changes.
 *
 * Not `<button>` elements — a button cannot live inside an `<svg>`. Each mark is
 * the circle itself, carrying `role="button"`, `tabindex="0"`, a name of its
 * own, and a keydown handler for Enter and Space: the four things a real button
 * would have given for free, and the four things a click listener on a shape
 * gives none of. This paragraph read *"makes each shot a button"* until
 * 2026-08-18 while the code built a bare `<circle>` with a click listener on it
 * — `cursor: pointer` and a hover state, nothing focusable, nothing announced.
 * `assets/timeline.js` sets the standard for its own marks, *"real buttons
 * rather than styled spans, so the whole thing is reachable from a keyboard"*,
 * and these are the same moments plotted on a different axis.
 *
 * The role on the `<svg>` has to move with them. `role="img"` collapses the
 * whole subtree into one picture, so focusable children under it are reachable
 * by tab and announced as nothing — which is worse than not being reachable,
 * because focus lands somewhere silent. `role="group"` keeps the `aria-label`
 * `renderShotMap` puts on the map and lets the marks inside speak.
 */
export function shotMapSvg(marks, { onPick = null, xgTrust = 'shot' } = {}) {
    const svg = el('svg', {
        viewBox: `${FROM_X - 2} -2 ${PITCH_LENGTH_M - FROM_X + 4} ${PITCH_WIDTH_M + 4}`,
        preserveAspectRatio: 'xMidYMid meet',
        class: 'shot-map-svg',
        // See the note above: a picture when the marks are inert, a container
        // of controls when they are not.
        role: onPick ? 'group' : 'img',
    });

    svg.appendChild(pitchMarkings({ width: 0.3 }));

    // Biggest first, so a tap-in never buries the half-chance beside it. At a
    // trust band that flattens every radius this sorts a constant and the
    // original order stands, which is correct — there is no size to bury.
    const ordered = [...(marks || [])].sort(
        (a, b) => markRadius(b.xg, xgTrust) - markRadius(a.xg, xgTrust),
    );

    for (const mark of ordered) {
        // A shot with no position at all is not a shot at the corner flag.
        // `Number(null) || 0` gave it one, and the dot that came out was
        // indistinguishable from a real shot from the goal line. `shot_marks`
        // in cv/report_json.py drops positionless shots before they get here,
        // so nothing produces this today — but the failure mode is an invented
        // data point on a picture a coach reads as measurement, which is the
        // kind of thing that should be impossible rather than merely unused.
        if (!placeable(mark)) continue;

        // A shot from the defending half is real but off this picture. Pinned
        // to the halfway line rather than dropped: the count under the map
        // includes it, so removing the dot entirely would not add up.
        const x = Math.max(FROM_X, mark.x_m);
        const y = mark.y_m;

        const dot = el('circle', {
            cx: x, cy: y, r: markRadius(mark.xg, xgTrust),
            class: `shot-mark ${markClass(mark)}`,
        });

        const label = markLabel(mark, xgTrust);
        dot.appendChild(el('title', {})).textContent = label;

        if (onPick) {
            dot.classList.add('is-pickable');
            dot.setAttribute('role', 'button');
            dot.setAttribute('tabindex', '0');
            // `<title>` is a hover tooltip. A control needs a name whether or
            // not there is a pointer on the screen.
            dot.setAttribute('aria-label', label);
            dot.addEventListener('click', () => onPick(mark));
            // Space scrolls the page when nothing swallows it, and on both
            // pages that draw this map the video being seeked to sits above
            // it — so the one keystroke would jump the video and scroll it out
            // of view at the same time.
            dot.addEventListener('keydown', (event) => {
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                onPick(mark);
            });
        }
        svg.appendChild(dot);
    }

    return svg;
}

/**
 * Put a shot map into `host`. Returns false when there is nothing to draw, so a
 * caller hides the section rather than showing an empty half-pitch.
 *
 * An empty array and a null are different and both draw nothing: null means no
 * calibration, an empty array means a calibrated run in which nobody shot. The
 * caller has the wording for that distinction; this only reports that it drew
 * nothing.
 */
export function renderShotMap(host, marks, options = {}) {
    if (!host) return false;
    host.innerHTML = '';
    if (!marks?.length) return false;

    const svg = shotMapSvg(marks, options);
    svg.setAttribute('aria-label', options.label
        || `${marks.length} shots, placed on the pitch and sized by chance quality`);
    host.appendChild(svg);
    return true;
}
