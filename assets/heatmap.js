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
} from './pitch-backdrop.js?v=96';

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

/**
 * Where the middle of this grid sits, as `{ xM, yM }` in pitch metres.
 *
 * Cell centres weighted by occupancy. `x` runs along the pitch from the left
 * goal line, `y` across it, in the same absolute frame the grid arrived in —
 * so this still knows nothing about which way anybody was attacking, and a
 * caller who has not applied `attackingEnd` has half a figure.
 *
 * Null for a grid this module cannot use, never `{ xM: 52.5, yM: 34 }`. The
 * centre spot is a real place a defensive midfielder averages, so it could not
 * have been a sentinel even if the project allowed one.
 *
 *     What the binning costs, measured.
 *
 * Twelve columns is 8.75 m a cell, which sounds fatal for a figure read in
 * metres and is not. Swept in 0.25 m steps across every mean position on the
 * pitch, for a player whose occupancy spreads 4 m to 20 m:
 *
 *     mean position          worst error   median error
 *     8–97 m (outfield)          0.229 m        0.012 m
 *     4–101 m (all of it)        0.475 m        0.013 m
 *
 * The whole difference between those two rows is the last four metres at each
 * end, where the pitch cuts the distribution off and the binned mean is pulled
 * back towards the middle by the half of it that fell off the end. A 0.48 m
 * error belongs to somebody averaging four metres from their own goal line.
 *
 * The other place it bites is a player who barely moved, where the mean sits
 * inside a single cell and the grid has nothing to interpolate between:
 *
 *     spread   worst error
 *      1 m         2.29 m
 *      2 m         0.97 m
 *      3 m         0.29 m
 *
 * Both of those describe the same person — a keeper, standing near a goal line
 * and moving less than anyone on the pitch — and `positionalPlay` measures
 * keepers and never judges them. For everybody it does judge, the figure is
 * good to about a fifth of a metre, which is far inside the band the comparison
 * already carries.
 *
 * The error that does bite is not in here at all. See `positionalPlay`.
 */
export function gridCentroid(grid) {
    if (!isGrid(grid)) return null;
    const cellW = PITCH_LENGTH_M / grid.cols;
    const cellH = PITCH_WIDTH_M / grid.rows;
    let total = 0;
    let sumX = 0;
    let sumY = 0;

    for (let x = 0; x < grid.cols; x += 1) {
        for (let y = 0; y < grid.rows; y += 1) {
            const share = cellAt(grid, x, y);
            if (share <= 0) continue;
            total += share;
            sumX += share * (x + 0.5) * cellW;
            sumY += share * (y + 0.5) * cellH;
        }
    }

    if (total <= 0) return null;
    return { xM: sumX / total, yM: sumY / total };
}

/**
 * How far up and down the pitch this player's occupancy spreads, in metres.
 *
 * The standard deviation along the pitch, on the same axis `forwardM` measures
 * — so it is the width of the band they lived in, and it is two things at once:
 * a description a coach can read ("held a 9 m band", "ranged over 22 m"), and
 * the input `positionalPlay` needs to know how firmly their average position is
 * pinned down. A player who never left one zone has a mean position worth
 * comparing. A player who covered half the pitch does not, however many minutes
 * they were tracked for.
 *
 * Mirroring does not touch it, so unlike the centroid this needs no attacking
 * end and is the same figure in either frame.
 *
 *     Sheppard's correction, and why it is here.
 *
 * Binning inflates a spread: every point in an 8.75 m cell is counted as sitting
 * at its centre, which adds the variance of the cell itself. Subtracting
 * `cellW² / 12` recovers the true figure to within 0.01 m at every spread
 * measured — a true 4.0 m reads 4.73 m binned and 3.999 m corrected. Left in, a
 * tight player would be reported 18% wider than they played, and would then be
 * handed a wider uncertainty than they had earned.
 *
 * That 0.01 m is an average over where the player's mean falls inside its cell,
 * and one player has one phase, not an average of them. Taken a phase at a time
 * the correction is coarser, and it is worth being exact about how much:
 *
 *     true spread   mean at a cell centre   mean at a cell edge
 *        4 m               3.85 m                 4.14 m
 *        3 m               2.17 m                 3.65 m
 *
 * A third of a cell is about as far as this can be pushed before the grid
 * stops being able to describe the shape at all. What keeps it harmless is
 * where the figure is used: `positionalPlay` turns a spread into a band and
 * then floors the comparison at `POSITION_FLOOR_M`, and the floor is what binds
 * for a 3 m player past about 10 minutes tracked and for a 4 m player past
 * about 18. Below those, the spread is not what makes the band wide — the
 * minutes are.
 *
 * It reads *low* for a player near a touchline or a goal line, because the pitch
 * truncates the distribution and no correction puts back what the grid never
 * had. That direction is the safe one: it never inflates somebody's uncertainty
 * into a finding.
 */
export function gridSpread(grid) {
    const middle = gridCentroid(grid);
    if (!middle) return null;
    const cellW = PITCH_LENGTH_M / grid.cols;
    let total = 0;
    let sumSq = 0;

    for (let x = 0; x < grid.cols; x += 1) {
        const centre = (x + 0.5) * cellW;
        for (let y = 0; y < grid.rows; y += 1) {
            const share = cellAt(grid, x, y);
            if (share <= 0) continue;
            total += share;
            sumSq += share * (centre - middle.xM) ** 2;
        }
    }

    if (total <= 0) return null;
    return Math.sqrt(Math.max(sumSq / total - (cellW * cellW) / 12, 0));
}

/**
 * The same middle, turned so the player is attacking right.
 *
 * `{ forwardM, lateralM }` — metres up the pitch from the goal this player was
 * defending, and metres across it from the right touchline as they faced it.
 * Null without an attacking end, matching `cv/report_json.py::shot_marks`,
 * which returns None for the same reason and mirrors through the centre on
 * both axes for the same one: flip only `x` and a player who lived on the left
 * wing in the second half appears on the right.
 *
 * Two figures of very different standing. `forwardM` is comparable between
 * players and is what the whole comparison rests on. `lateralM` is reported
 * and never judged: nothing in this system knows a left-back from a right-back
 * — `POSITIONS` has four lines and no sides — so there is no assignment for it
 * to disagree with.
 *
 * `spreadM` rides along because every caller that wants one wants the other:
 * a mean position is not reportable without some idea of how wide the thing it
 * averages was.
 */
export function orientedCentroid(grid, attackingEnd) {
    if (attackingEnd !== 'left' && attackingEnd !== 'right') return null;
    const middle = gridCentroid(grid);
    if (!middle) return null;
    const spreadM = gridSpread(grid);
    return attackingEnd === 'left'
        ? { forwardM: PITCH_LENGTH_M - middle.xM, lateralM: PITCH_WIDTH_M - middle.yM, spreadM }
        : { forwardM: middle.xM, lateralM: middle.yM, spreadM };
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
