// The passing network, on a pitch.
//
// The arithmetic is in `passing.js`, which imports nothing and is fully tested;
// this is only the drawing. Everything here is a size, a position or a label,
// and every one of them is a claim about the match — so each is derived from a
// count rather than chosen to look right.
//
//     Why the whole pitch, not the attacking half.
//
// A shot map is about chances, which happen in one third of the field, so it
// crops. A passing network is about *shape*: where the back line sat, whether
// the midfield split, whether one full-back was doing all the work. Cropping
// would delete the half the question lives in.
//
//     Line width, and the claim it makes.
//
// Width scales linearly with the number of passes between a pair, so a line
// twice as thick is twice as many passes and nothing else. Squaring it, or
// scaling by area the way a shot dot does, would exaggerate the busiest pair in
// a picture whose entire point is comparing pairs. Below `MIN_EDGE` passes a
// pair is not drawn at all — two players who exchanged one pass are not a
// connection, and a hairline between every pair of names turns the diagram into
// a mesh where nothing stands out.
//
//     Node size.
//
// Radius by **area**, on total passes played, so a player who passed four times
// as often is four times the circle rather than four times the width. That is
// the same rule the shot map uses for xG, for the same reason: area is what
// the eye reads as quantity.

import {
    PITCH_LENGTH_M, PITCH_WIDTH_M, PITCH_VIEWBOX, pitchMarkings,
} from './pitch-backdrop.js?v=38';
import { foldEdges } from './passing.js?v=38';

const NS = 'http://www.w3.org/2000/svg';

// A pair has to have exchanged this many passes before it is a line. See above.
export const MIN_EDGE = 2;

const MIN_R = 1.6;
const MAX_R = 4.2;
const MIN_W = 0.35;
const MAX_W = 2.2;

function el(name, attrs) {
    const node = document.createElementNS(NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

/**
 * Node radius from passes played, scaled so **area** carries the count.
 *
 * Clamped at the bottom rather than offset from it, which is not a detail. The
 * obvious `MIN_R + (MAX_R - MIN_R) * √share` looks like the same thing and is
 * not proportional to anything: with a floor added to the *radius*, a player
 * with three times another's passes came out 1.85 times the area on the sample
 * squad, so the picture quietly flattened the difference it exists to show.
 *
 * Above the floor this is exact — four times the passes is four times the
 * circle. Below it every dot draws the same size, because a dot too small to
 * see is a player the diagram has deleted, and the note underneath is where
 * "barely involved" gets said in words rather than in a size nobody can read.
 */
export function nodeRadius(passes, busiest) {
    if (!busiest || passes <= 0) return MIN_R;
    const share = Math.min(1, passes / busiest);
    return Math.max(MIN_R, MAX_R * Math.sqrt(share));
}

/**
 * Line width from pass count — twice as thick is twice as many passes.
 *
 * Same clamp, same reason. Linear rather than square-rooted because a line's
 * apparent weight is its width; a stroke is not a disc and the eye does not
 * read its area.
 */
export function edgeWidth(count, busiest) {
    if (!busiest) return MIN_W;
    return Math.max(MIN_W, MAX_W * Math.min(1, count / busiest));
}

/**
 * The network as an `<svg>`.
 *
 * `nameOf` turns a player id into something to print. Nodes with no position
 * are skipped here rather than filtered by the caller — they are real players
 * with real counts and they belong in the note underneath, which is where
 * `networkNote` puts them.
 */
export function passMapSvg(network, { nameOf = (id) => id, onPick = null } = {}) {
    const svg = el('svg', {
        viewBox: PITCH_VIEWBOX,
        class: 'pass-map-svg',
        preserveAspectRatio: 'xMidYMid meet',
    });
    svg.appendChild(pitchMarkings());

    const placed = (network?.nodes || []).filter((n) => n.x != null && n.y != null);
    const at = new Map(placed.map((n) => [n.playerId, n]));

    const folded = foldEdges(network?.edges || [])
        .filter((e) => e.count >= MIN_EDGE && at.has(e.a) && at.has(e.b));
    const busiestEdge = folded[0]?.count || 0;
    const busiestNode = Math.max(0, ...placed.map((n) => n.passes));

    // Lines first, so a node always sits on top of the lines that reach it.
    for (const edge of folded) {
        const a = at.get(edge.a);
        const b = at.get(edge.b);
        const line = el('line', {
            x1: a.x, y1: a.y, x2: b.x, y2: b.y,
            class: 'pass-edge',
            'stroke-width': edgeWidth(edge.count, busiestEdge).toFixed(2),
        });
        // Both directions, because "14 passes" between two players says nothing
        // about whether one of them was feeding the other or they were sharing
        // it, and that difference is most of what a coach reads this for.
        line.appendChild(el('title', {})).textContent =
            `${nameOf(edge.a)} and ${nameOf(edge.b)} — ${edge.count} passes, `
            + `${edge.aToB} one way and ${edge.bToA} back`;
        svg.appendChild(line);
    }

    for (const node of placed) {
        const group = el('g', { class: 'pass-node' });
        const dot = el('circle', {
            cx: node.x, cy: node.y,
            r: nodeRadius(node.passes, busiestNode).toFixed(2),
            class: 'pass-dot',
        });
        group.appendChild(dot);

        const name = nameOf(node.playerId);
        group.appendChild(el('title', {})).textContent =
            `${name} — ${node.passes} passes, ${node.completed} completed, `
            + `${node.received} received`;

        if (onPick) {
            group.classList.add('is-pickable');
            group.addEventListener('click', () => onPick(node));
        }
        svg.appendChild(group);
    }

    return svg;
}

/**
 * Put a network into `host`. False when there is nothing worth drawing.
 *
 * "Nothing worth drawing" is one placed player or none. A pitch with a single
 * dot on it is not a network, and an empty pitch reads as a team that never
 * passed — which is the same failure the heatmap and shot map both hide from by
 * refusing to render.
 */
export function renderPassMap(host, network, options = {}) {
    if (!host) return false;
    host.innerHTML = '';

    const placed = (network?.nodes || []).filter((n) => n.x != null && n.y != null);
    if (placed.length < 2) return false;

    const svg = passMapSvg(network, options);
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', options.label
        || `${placed.length} players and the passes between them`);
    host.appendChild(svg);
    return true;
}

export { PITCH_LENGTH_M, PITCH_WIDTH_M };
