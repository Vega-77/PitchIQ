// Where a player actually spent the match, drawn on a pitch.
//
// `cv/metrics.py::heatmap` has produced an occupancy grid per tracked figure
// since the day it was written. Nothing ever drew one, and it turned out
// nothing ever finished computing one either: the grid was built per *track*
// and `track_stats` never carried it across to the cluster, so `TrackStats.
// heatmap` serialised as null on every run — declared, published, and never
// once filled in.
//
//     One shape, everywhere.
//
// A grid arrives as `{ cols, rows, values }` with `values` in column-major
// order — `values[x * rows + y]`, x along the pitch and y across it, matching
// the `np.histogram2d(length, width)` call that produced it. It is flat because
// Firestore refuses nested arrays outright, and it stays flat here rather than
// being unpacked on arrival so there is only ever one layout to get wrong.
//
//     Why the merge is not a sum.
//
// A player is often several clusters: the tracker loses people when they leave
// frame, and `cv/identity.py` only rejoins fragments seconds apart. Each grid
// is normalised to sum to 1, so adding them raw gives an eight-second fragment
// the same weight as a half — and a player tracked cleanly throughout plus once
// more at the edge of frame would show a hotspot at the edge of frame.
//
//     What it cannot say.
//
// The grid is in absolute pitch coordinates and does not know which way the
// player was attacking. One run covers one period, so the direction is at least
// consistent within a report — but "they stayed high" and "they sat deep" are
// the same picture flipped, so `attackingEnd` is what makes it readable.

import {
    PITCH_LENGTH_M, PITCH_WIDTH_M, PITCH_VIEWBOX, pitchMarkings,
} from './pitch-backdrop.js?v=56';

const NS = 'http://www.w3.org/2000/svg';

// Anything below this share of the busiest cell is left blank rather than drawn
// at 2% opacity. A wash of near-invisible colour over the whole pitch reads as
// "they were everywhere", which is the opposite of what a heatmap is for.
const FLOOR = 0.06;

function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

/** Whether a value is a grid this module can do anything with. */
export function isGrid(grid) {
    return Boolean(
        grid && grid.cols > 0 && grid.rows > 0
        && Array.isArray(grid.values)
        && grid.values.length === grid.cols * grid.rows,
    );
}

/** The occupancy of one cell. `x` runs along the pitch, `y` across it. */
export function cellAt(grid, x, y) {
    return Number(grid.values[x * grid.rows + y]) || 0;
}

/**
 * Combine several clusters' grids into one, weighted by time tracked.
 *
 * `entries` is `[{ grid, minutes }]`. Returns a grid normalised to sum to 1, or
 * null when there is nothing to draw — null rather than a grid of zeroes, so a
 * caller renders no plot instead of an empty pitch that reads as a player who
 * never moved.
 *
 * Grids of differing shapes are skipped rather than reconciled: two different
 * bin counts mean two different runs, and stretching one onto the other would
 * invent positions.
 */
export function mergeHeatmaps(entries) {
    const usable = (entries || []).filter((e) => isGrid(e?.grid));
    if (!usable.length) return null;

    const { cols, rows } = usable[0].grid;
    const values = new Array(cols * rows).fill(0);
    let total = 0;

    for (const { grid, minutes } of usable) {
        if (grid.cols !== cols || grid.rows !== rows) continue;
        // A fragment with no minutes recorded still happened, so it counts for
        // something rather than nothing — but only just.
        const weight = minutes > 0 ? minutes : 0.001;

        for (let i = 0; i < values.length; i += 1) {
            const share = (Number(grid.values[i]) || 0) * weight;
            values[i] += share;
            total += share;
        }
    }

    if (total <= 0) return null;
    return { cols, rows, values: values.map((v) => v / total) };
}

/**
 * The busiest cell, as `{ x, y, share }` — or null for an empty grid.
 *
 * Exposed because "a seventh of your match was spent in one square" is a
 * sentence, and a picture is not.
 */
export function busiestCell(grid) {
    if (!isGrid(grid)) return null;
    let best = null;
    for (let x = 0; x < grid.cols; x += 1) {
        for (let y = 0; y < grid.rows; y += 1) {
            const share = cellAt(grid, x, y);
            if (!best || share > best.share) best = { x, y, share };
        }
    }
    return best && best.share > 0 ? best : null;
}

/** The grid as an `<svg>` over pitch markings. */
export function heatmapSvg(grid, { attackingEnd = null } = {}) {
    const svg = el('svg', {
        viewBox: PITCH_VIEWBOX,
        preserveAspectRatio: 'xMidYMid meet',
        class: 'heatmap-svg',
        role: 'img',
    });

    const cellW = PITCH_LENGTH_M / grid.cols;
    const cellH = PITCH_WIDTH_M / grid.rows;
    const peak = busiestCell(grid)?.share || 1;

    const cells = el('g', { class: 'heatmap-cells' });
    for (let x = 0; x < grid.cols; x += 1) {
        for (let y = 0; y < grid.rows; y += 1) {
            const share = cellAt(grid, x, y) / peak;
            if (share < FLOOR) continue;
            cells.appendChild(el('rect', {
                x: x * cellW, y: y * cellH, width: cellW, height: cellH,
                // Square-rooted so the mid-range is visible. Occupancy is very
                // long-tailed — one or two cells hold most of the time — and a
                // linear ramp renders everywhere else as effectively blank.
                opacity: (Math.sqrt(share) * 0.85).toFixed(3),
            }));
        }
    }

    svg.appendChild(cells);
    svg.appendChild(pitchMarkings({ width: 0.3 }));

    if (attackingEnd === 'left' || attackingEnd === 'right') {
        svg.appendChild(directionArrow(attackingEnd));
    }
    return svg;
}

/** A small arrow above the pitch showing which way they were playing. */
function directionArrow(attackingEnd) {
    const group = el('g', { class: 'heatmap-arrow' });
    const y = -0.9;
    const right = attackingEnd === 'right';
    const [from, to] = right ? [38, 67] : [67, 38];

    group.appendChild(el('line', {
        x1: from, y1: y, x2: to, y2: y,
        stroke: 'currentColor', 'stroke-width': 0.4,
    }));
    const back = right ? -1.8 : 1.8;
    group.appendChild(el('polygon', {
        points: `${to},${y} ${to + back},${y - 0.9} ${to + back},${y + 0.9}`,
        fill: 'currentColor',
    }));
    return group;
}

/**
 * Put a merged heatmap into `host`, or leave it empty and say so by returning
 * false — so a caller hides the section rather than showing a heading over
 * nothing.
 */
export function renderHeatmap(host, entries, options = {}) {
    if (!host) return false;
    host.innerHTML = '';

    const grid = mergeHeatmaps(entries);
    if (!grid) return false;

    const svg = heatmapSvg(grid, options);
    svg.setAttribute('aria-label', options.label
        || 'Where this player spent the match, drawn on the pitch');
    host.appendChild(svg);
    return true;
}
