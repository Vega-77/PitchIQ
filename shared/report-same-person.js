// The same person, tracked twice over.
//
// Part of `report.js`, which re-exports it.

import { stintOverlapS, clockOf } from './report-clock.js?v=146';
import { NOT_A_PLAYER } from './report-player.js?v=146';

// ----------------------------------------- the same person, tracked twice over
//
// `pitchiq/identity.py` rejoins fragments that are seconds apart and deliberately
// stops there: a player who goes off, or who leaves frame while the camera
// pans and comes back a minute later, stays two figures. That is the right
// failure — two figures named as one player still sum correctly, whereas a
// wrong merge attributes one teenager's match to another and cannot be undone
// downstream — and naming both is already the whole fix, because the picker is
// many-to-one and `cvStatsByPlayer` sums across every cluster mapped to a name.
//
// What is missing is only the saying-so. The list is ordered by how long each
// figure was tracked, so the two halves of one player's match are nowhere near
// each other in it, and finding the second one means recognising a face in a
// forty-row list you have already scrolled past.
//
//     The one thing that can be said with certainty.
//
// Two figures on screen in the same frame are two people, whatever they look
// like. `identity.py` calls that its only certainty and it does most of the
// work there.
//
// The browser has intervals rather than frames — `first_seen_s` and
// `last_seen_s`, not the frame sets — and an interval is normally a much weaker
// thing to reason from. Here it is not, because of an invariant the merging
// gives for free: every merge in `identity.py` joins a pair with a gap between
// 0 and `MAX_BRIDGE_S`, and a cluster is connected through such pairs, so **no
// cluster has an internal hole longer than two seconds**. A cluster's interval
// is therefore very nearly solid, and two intervals that overlap by more than
// that really were on screen together. `tests/test_identity.py` asserts the
// invariant against the merger itself, because this is the load-bearing half of
// the argument and it lives in the other language.
//
// So overlap rules a figure out and nothing else does. Kit colour and the size
// of the gap are evidence, and evidence orders the list rather than shortening
// it, for the same reason `rankRosterForCluster` refuses to filter: the numbers
// underneath are only as good as the video offset, and hiding the poor fits
// would hide the right answer on the day that number is wrong.

// The longest gap `pitchiq/identity.py` will bridge, and so the largest overlap two
// intervals can show while still being one person seen either side of it.
const BRIDGE_S = 2.0;

// Lab chroma distance beyond which two figures are wearing different shirts.
// Mirrors MAX_CHROMA_DISTANCE in pitchiq/identity.py, which is the source of truth;
// it is looser than team assignment uses because this asks whether two
// sightings are one player, and light changes across a pitch more than kits do.
export const SAME_KIT_CHROMA = 30.0;

/**
 * Lab distance ignoring lightness, matching `_chroma_distance` in identity.py.
 *
 * `null` rather than a number when either colour is missing: no evidence is not
 * the same as agreement, and returning 0 would promote an unknown shirt to a
 * perfect match.
 */
export function kitDistance(a, b) {
    if (!Array.isArray(a) || !Array.isArray(b)) return null;
    if (a.length < 3 || b.length < 3) return null;
    return Math.hypot(a[1] - b[1], a[2] - b[2]);
}

/**
 * The other tracked figures that could be the same person as this one.
 *
 * Returns every other cluster, ranked, each carrying why it is where it is:
 *
 *   - `overlapS` — seconds the two were on screen together. Above `BRIDGE_S`
 *     this is `ruledOut`, the one hard answer available.
 *   - `gapS` — the silence between them, `0` when they touch or overlap. The
 *     strongest ordering signal: a figure that starts eight seconds after this
 *     one ends is a likelier continuation than one an hour later.
 *   - `kitS` — chroma distance, `null` when either has no colour.
 *   - `sameTeam` — `true`, `false`, or `null` when either side is unclear.
 *   - `takenBy` — the player already mapped to it, if that is somebody else.
 *     Not a bar: a coach who mis-named a fragment should be able to see it here
 *     and say so, and picking it just moves the mapping.
 *
 * `player` in the options is who this figure has been called. When given, each
 * candidate also carries `playedShare` — how much of the candidate's own span
 * that player was actually on the pitch for. A figure tracked entirely while
 * the player it would join was on the bench is a poor suggestion, and this is
 * the number that says so.
 */
export function sameFigureCandidates(clusters, cluster, options = {}) {
    const id = cluster?.cluster_id;
    if (id == null) return [];

    const { mapping = {}, player = null, matchEndS = null } = options;
    const clock = clockOf(options);
    const mine = seenSpan(cluster);

    return (clusters || [])
        .filter((other) => other && other.cluster_id !== id)
        .map((other) => {
            const theirs = seenSpan(other);
            const overlapS = Math.max(
                0,
                Math.min(mine.endS, theirs.endS) - Math.max(mine.startS, theirs.startS),
            );
            const gapS = overlapS > 0
                ? 0
                : Math.max(theirs.startS - mine.endS, mine.startS - theirs.endS);
            const kitS = kitDistance(cluster.colour, other.colour);
            const known = cluster.team && cluster.team !== 'unknown'
                && other.team && other.team !== 'unknown';

            const named = mapping[String(other.cluster_id)];
            const takenBy = named && named !== player ? named : null;

            let playedShare = null;
            if (player && player !== NOT_A_PLAYER) {
                const entry = (options.roster || []).find((r) => r.id === player);
                const startS = clock.toClock(theirs.startS).clockS;
                const endS = clock.toClock(theirs.endS).clockS;
                const width = Math.max(0, endS - startS);
                playedShare = width > 0
                    ? stintOverlapS(entry?.stints, startS, endS, matchEndS) / width
                    : null;
            }

            return {
                cluster: other,
                overlapS,
                gapS,
                kitS,
                sameTeam: known ? cluster.team === other.team : null,
                takenBy,
                playedShare,
                ruledOut: overlapS > BRIDGE_S || (known && cluster.team !== other.team),
            };
        })
        .sort((a, b) => (
            (a.ruledOut ? 1 : 0) - (b.ruledOut ? 1 : 0)
            // A figure the coach has already given to somebody else is a poor
            // suggestion but a legitimate one — they may have named it wrongly,
            // and this is where they would find that out.
            || (a.takenBy ? 1 : 0) - (b.takenBy ? 1 : 0)
            // A shirt that does not match is evidence, so it sorts down; it is
            // never a reason to drop a row, because one bad colour sample on a
            // shaded touchline is a thing that happens.
            || farKit(a) - farKit(b)
            || a.gapS - b.gapS
            || (b.cluster.sightings || 0) - (a.cluster.sightings || 0)
        ));
}

function seenSpan(cluster) {
    return {
        startS: cluster?.first_seen_s ?? 0,
        endS: Math.max(cluster?.first_seen_s ?? 0, cluster?.last_seen_s ?? 0),
    };
}

const farKit = (row) => (row.kitS != null && row.kitS > SAME_KIT_CHROMA ? 1 : 0);
