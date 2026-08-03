// A faint pitch diagram behind page headers.
//
// Chosen over a gradient or a noise texture because it is the one ornament that
// is actually about the subject: real markings at real proportions, drawn from
// the same dimensions cv/pitch.py uses. It sits at low opacity and is
// aria-hidden — decoration, never information.

const NS = 'http://www.w3.org/2000/svg';

// Metres, matching the defaults in cv/pitch.py.
const L = 105;
const W = 68;
const PEN_LEN = 16.5;
const PEN_W = 40.32;
const SIX_LEN = 5.5;
const SIX_W = 18.32;
const CIRCLE_R = 9.15;

function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

// The pitch, in metres, so anything drawn on top can be positioned in the same
// units cv/ measures in and nothing has to convert.
export const PITCH_LENGTH_M = L;
export const PITCH_WIDTH_M = W;
export const PITCH_VIEWBOX = `-2 -2 ${L + 4} ${W + 4}`;

/**
 * The markings as a `<g>`, in metres.
 *
 * Separate from `pitchSvg` because the same lines are wanted twice over: once
 * faintly behind a page header, and once at full strength under a heatmap or a
 * shot map, where they are the reference that makes the plot readable rather
 * than ornament. Two copies of this geometry would be two chances to draw a
 * penalty box in the wrong place, and only one of them would be noticed.
 */
export function pitchMarkings({ width = 0.35 } = {}) {
    const group = el('g', {});
    const stroke = { stroke: 'currentColor', 'stroke-width': width, fill: 'none' };
    const cy = W / 2;

    group.appendChild(el('rect', { x: 0, y: 0, width: L, height: W, ...stroke }));
    group.appendChild(el('line', { x1: L / 2, y1: 0, x2: L / 2, y2: W, ...stroke }));
    group.appendChild(el('circle', { cx: L / 2, cy, r: CIRCLE_R, ...stroke }));
    group.appendChild(el('circle', {
        cx: L / 2, cy, r: 0.6, fill: 'currentColor', stroke: 'none',
    }));

    for (const side of [0, 1]) {
        const x = side ? L - PEN_LEN : 0;
        const sx = side ? L - SIX_LEN : 0;
        group.appendChild(el('rect', {
            x, y: cy - PEN_W / 2, width: PEN_LEN, height: PEN_W, ...stroke,
        }));
        group.appendChild(el('rect', {
            x: sx, y: cy - SIX_W / 2, width: SIX_LEN, height: SIX_W, ...stroke,
        }));
        group.appendChild(el('circle', {
            cx: side ? L - 11 : 11, cy, r: 0.6, fill: 'currentColor', stroke: 'none',
        }));
    }

    return group;
}

/** Build the markings as an <svg>, viewBox in metres so nothing needs scaling. */
export function pitchSvg({ opacity = 0.5 } = {}) {
    const svg = el('svg', {
        viewBox: PITCH_VIEWBOX,
        preserveAspectRatio: 'xMidYMid slice',
        fill: 'none',
        'aria-hidden': 'true',
        focusable: 'false',
    });
    svg.style.opacity = opacity;
    svg.appendChild(pitchMarkings());
    return svg;
}

/**
 * Drop the backdrop into a container. The container needs position:relative;
 * the wrapper handles clipping and the fade-out.
 */
export function mountPitchBackdrop(container, options = {}) {
    if (!container || container.querySelector('.pitch-backdrop')) return;

    const wrap = document.createElement('div');
    wrap.className = 'pitch-backdrop';
    wrap.setAttribute('aria-hidden', 'true');
    wrap.appendChild(pitchSvg(options));

    container.prepend(wrap);
    return wrap;
}
