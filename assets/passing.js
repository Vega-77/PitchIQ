// Who passed to whom, and from where.
//
// The first piece of the post-game tactical catalog. A passing network is the
// one diagram that shows a *shape* rather than a total: which players the ball
// actually travelled between, and which pairs never connected at all. A table
// of pass counts cannot say that, however many columns it has.
//
// Zero imports, like report.js and for the same reason — `tests/passing.test.js`
// loads it straight off disk and can cover every rule below without a DOM, a
// Firestore connection or a fixture file.
//
// Three decisions carry most of the honesty here.
//
// **Nodes sit where each player actually passed from**, not where they spent
// their time. A heatmap centroid is available and would have been easier, but
// it averages defensive shape and off-ball running into a diagram whose edges
// are passes — two different questions, one picture, and no way for a reader to
// tell they had been mixed. Mean pass origin answers the question the edges ask.
//
// **Only players a coach has named appear.** A tracked figure nobody has
// matched to a roster slot is a guess about identity, and a guess with a line
// drawn to it looks exactly like a fact. They are counted and reported as a
// number instead, so the picture never silently omits half a team.
//
// **An incomplete pass is not an edge.** It happened, it belongs in the
// player's own attempt count, and it connects nobody — drawing it to whoever
// happened to be nearest would invent the one thing the diagram exists to show.

const PASS = 'pass';
const COMPLETED = 'completed';

/**
 * Track id to player id, through the clusters a coach has named.
 *
 * The pipeline knows tracks; the coach names clusters; a cluster is several
 * tracks. Nothing else in the app needs this particular hop because everything
 * else works in cluster space — the event list is the exception, since an event
 * is stamped with the track that produced it.
 */
export function playersByTrack(clusters, byCluster, notAPlayer = null) {
    const out = new Map();
    for (const cluster of clusters || []) {
        const playerId = (byCluster || {})[String(cluster.cluster_id)];
        if (!playerId || playerId === notAPlayer) continue;
        for (const trackId of cluster.track_ids || []) out.set(trackId, playerId);
    }
    return out;
}

const mean = (values) => values.reduce((a, b) => a + b, 0) / values.length;

/**
 * The network, from the published event list and a coach's mapping.
 *
 * `events` is `cvStats/events`'s list. `byTrack` is `playersByTrack`'s Map.
 * `attackingEnd` mirrors the whole picture so the named team plays left to
 * right, the same flip `pitchiq/report_json.py::shot_marks` does for shot maps and
 * for the same reason: a diagram is only readable if everyone attacks one way.
 *
 * Returns nodes with a position and counts, edges with a count per direction,
 * and — just as importantly — what it had to leave out.
 */
export function passingNetwork(events, options = {}) {
    const {
        byTrack = new Map(),
        team = null,
        attackingEnd = 'right',
        pitchLengthM = 105,
        pitchWidthM = 68,
    } = options;

    const nodes = new Map();
    const edges = new Map();
    let unmapped = 0;
    let unplaced = 0;
    let incomplete = 0;

    const nodeFor = (playerId) => {
        if (!nodes.has(playerId)) {
            nodes.set(playerId, {
                playerId, xs: [], ys: [], passes: 0, completed: 0, received: 0,
            });
        }
        return nodes.get(playerId);
    };

    for (const event of events || []) {
        if (event.type !== PASS) continue;
        if (team && event.team !== team) continue;

        const from = byTrack.get(event.trackId);
        if (!from) {
            // A real pass by somebody nobody has named. Counted, never guessed.
            unmapped += 1;
            continue;
        }

        const node = nodeFor(from);
        node.passes += 1;

        // Position is optional even for a mapped player: an uncalibrated run
        // has no metres at all, and the node then has counts but nowhere to
        // stand. Kept apart from `unmapped` because the fixes differ — one
        // wants a coach at the picker, the other wants a calibration.
        const at = event.startM;
        if (Array.isArray(at) && at.length === 2) {
            node.xs.push(at[0]);
            node.ys.push(at[1]);
        } else {
            unplaced += 1;
        }

        if (event.outcome !== COMPLETED) {
            incomplete += 1;
            continue;
        }
        node.completed += 1;

        const to = byTrack.get(event.receiverTrackId);
        // A completed pass to a figure nobody has named connects nothing. It
        // still counts as this player's completed pass — it did find a
        // team-mate — so it is not `unmapped`, which counts passes with no
        // *passer*. It simply draws no line.
        if (!to || to === from) continue;

        nodeFor(to).received += 1;
        // A player id cannot contain a NUL, so this pair key is
        // unambiguous however the ids are spelled. Written as an escape
        // rather than the byte itself: a literal NUL makes the whole file
        // binary to grep and to every other text tool, which is how this
        // module got skipped by a sweep that read every other one.
        const key = `${from}\u0000${to}`;
        edges.set(key, { from, to, count: (edges.get(key)?.count || 0) + 1 });
    }

    const placed = [];
    for (const node of nodes.values()) {
        const { xs, ys, ...rest } = node;
        // Mirrored here rather than by the renderer, so no drawing code can
        // forget and put a second-half network at the wrong end.
        const x = xs.length ? mean(xs) : null;
        const y = ys.length ? mean(ys) : null;
        placed.push({
            ...rest,
            x: x == null ? null : (attackingEnd === 'left' ? pitchLengthM - x : x),
            y: y == null ? null : (attackingEnd === 'left' ? pitchWidthM - y : y),
        });
    }

    // Busiest first, so a renderer that has to drop some drops the quietest.
    placed.sort((a, b) => b.passes - a.passes || String(a.playerId).localeCompare(b.playerId));
    const list = [...edges.values()].sort((a, b) => b.count - a.count);

    return { nodes: placed, edges: list, unmapped, unplaced, incomplete };
}

/**
 * Directed edges folded into one line per pair.
 *
 * A diagram with two lines between every pair of players is unreadable, and
 * drawing only the busier direction throws away half the passes. So the line
 * carries the total and remembers the split, which is what lets the label say
 * "14 passes — 9 one way, 5 back" instead of a number with no shape to it.
 */
export function foldEdges(edges) {
    const pairs = new Map();
    for (const edge of edges || []) {
        const [a, b] = [edge.from, edge.to].sort();
        const key = `${a}\u0000${b}`;
        const entry = pairs.get(key) || { a, b, count: 0, aToB: 0, bToA: 0 };
        entry.count += edge.count;
        if (edge.from === a) entry.aToB += edge.count;
        else entry.bToA += edge.count;
        pairs.set(key, entry);
    }
    return [...pairs.values()].sort((x, y) => y.count - x.count
        || x.a.localeCompare(y.a) || x.b.localeCompare(y.b));
}

/**
 * The pair the ball travelled between most, as a sentence — or null.
 *
 * Null below `floor` passes rather than naming whichever pair happened to top a
 * thin list. "Your main link was Ada and Dee, with two passes" is a sentence
 * about nothing, and it reads as a finding.
 */
export function strongestLink(folded, nameOf, floor = 5) {
    const top = (folded || [])[0];
    if (!top || top.count < floor) return null;
    return `The ball went between ${nameOf(top.a)} and ${nameOf(top.b)} more than`
        + ` any other pair — ${top.count} passes,`
        + ` ${top.aToB} one way and ${top.bToA} back.`;
}

/**
 * What the picture had to leave out, said plainly.
 *
 * Not a footnote. A network drawn from a third of the passes looks exactly like
 * a network drawn from all of them, and the missing two thirds are precisely
 * the players a coach has not got round to naming yet.
 */
export function networkNote(network, options = {}) {
    const { truncated = false } = options;
    const { nodes = [], unmapped = 0, unplaced = 0, incomplete = 0 } = network || {};
    const parts = [];

    const drawn = nodes.filter((n) => n.x != null).length;
    parts.push(`${drawn} player${drawn === 1 ? '' : 's'} placed where they`
        + ' passed from on average, joined by the passes that found a team-mate.');

    if (unmapped) {
        parts.push(`${unmapped} pass${unmapped === 1 ? '' : 'es'} came from a`
            + ' tracked figure nobody has named yet, so nothing here shows them.'
            + ' Name them under "Who the video tracked" and they join the picture.');
    }
    if (unplaced) {
        parts.push(`${unplaced} had no position — this run has no pitch`
            + ' calibration, so they count but cannot be drawn.');
    }
    if (incomplete) {
        parts.push(`${incomplete} did not find anyone. They are in each player's`
            + ' own count and join no line, because a pass that went nowhere'
            + ' connects nobody.');
    }
    if (truncated) {
        parts.push('The event list was trimmed to the most confident candidates,'
            + ' so some passes are missing from this entirely.');
    }
    return parts.join(' ');
}
