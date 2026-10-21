// Positions, position bands and positional play.
//
// Part of `report.js`, which re-exports it.

// ------------------------------------------------------- where they play
//
// Four positions, and no more. Football names positions as finely as you like —
// left-back, holding midfielder, inside forward — and every extra name is
// another judgement a coach has to make about a sixteen-year-old who probably
// played three of them this season. Four is what the figures on these pages can
// actually be read against: a line of the team.
//
// The distinction that does real work is the first one. A goalkeeper covers
// about a third the ground of a midfielder, so their metres a minute belongs on
// a different scale, and the player table ranked them against each other with
// nothing saying so.
//
// `role` was taken. Everywhere else in this repo it means *access* — coach,
// tagger, player — and the two must not be confused: one decides what a person
// may read and the other where they stand on a pitch.
export const POSITIONS = [
    { id: 'gk', short: 'GK', label: 'Goalkeeper', plural: 'Goalkeepers' },
    { id: 'def', short: 'DEF', label: 'Defender', plural: 'Defenders' },
    { id: 'mid', short: 'MID', label: 'Midfielder', plural: 'Midfielders' },
    { id: 'fwd', short: 'FWD', label: 'Forward', plural: 'Forwards' },
];

const POSITION_BY_ID = new Map(POSITIONS.map((p) => [p.id, p]));

/** A position id from anywhere, or null. Anything unrecognised is unset. */
export function positionOf(value) {
    return POSITION_BY_ID.has(value) ? value : null;
}

/** "Midfielder", or null when nobody has said. Never a guess. */
export function positionLabel(value) {
    return POSITION_BY_ID.get(value)?.label ?? null;
}

export const isKeeper = (player) => positionOf(player?.position) === 'gk';

/**
 * A squad split into lines, in team-sheet order, keepers first.
 *
 * **Returns one unnamed group when nobody has a position**, which is the
 * important case rather than a fallback: a coach who has not filled this in
 * gets exactly the table they had before, in exactly the order they had it,
 * instead of a page that has quietly reorganised itself around a field they
 * have never seen. Headings appear the moment the first player is given a line
 * and not before.
 *
 * Players with no position go to a group at the end rather than into a line
 * somebody might read as a claim. `compare` orders within a group, so the
 * existing "most involved first" survives — a line is where you look, and the
 * standouts still rise inside it.
 */
export function groupByPosition(players, compare = null) {
    const list = [...(players || [])];
    const sorted = compare ? list.sort(compare) : list;

    if (!sorted.some((p) => positionOf(p.position))) {
        return [{ id: null, title: null, players: sorted }];
    }

    const groups = POSITIONS.map((pos) => ({
        id: pos.id,
        title: pos.plural,
        players: sorted.filter((p) => positionOf(p.position) === pos.id),
    }));
    groups.push({
        id: null,
        // Named as a question rather than as a category. "Other" would read as
        // a line of the team, and these are players nobody has got to yet.
        title: 'No position set',
        players: sorted.filter((p) => !positionOf(p.position)),
    });

    return groups.filter((g) => g.players.length);
}

// Where each player actually played, against the line they were put in.
//
// The pipeline records where somebody spent the match; the coach records which
// line they were picked in. Those are two independent records of the same
// thing, and this compares them — the way `pitchiq/reconcile.py` compares the tagged
// log against the detected events, and for the same reason. It does **not**
// compare anybody against a template of where a defender ought to stand. There
// is no such template that survives contact with a modern full-back or a false
// nine, and inventing one would manufacture findings out of tactics.
//
// So a remark here never says "wrong". It says two records disagree, which is
// an invitation to look at the footage.
//
//     The measurement that set every threshold below.
//
// A mean position taken over a finite window is a sample, and a footballer is
// not a fixed point with noise on top — they drift, and they drift slowly, so
// consecutive positions are nothing like independent. Modelled as an
// Ornstein-Uhlenbeck walk (a player pulled toward a station, wandering around
// it with correlation time tau), the standard deviation of the window mean is
// `spread * sqrt(2 * tau / tracked)`, which a 6000-trial simulation confirms to
// within 5% from ten minutes upward and which over-states the error below that
// — the safe direction for something a threshold is built on.
//
// The size of it is the finding. For a player of ordinary 12 m spread:
//
// | tracked | their own position | gap between two of them, 2 sigma |
// |---|---|---|
// | 10 min | +/- 5.4 m | **14.4 m** |
// | 20 min | +/- 3.8 m | **10.7 m** |
// | 45 min | +/- 2.5 m | **7.2 m** |
//
// A 5 m threshold — which is what this was going to use, and which sounds
// conservative for a 105 m pitch — would have reported pure sampling noise as a
// defender playing ahead of the midfield, on every thinly tracked match. That
// error dwarfs the two smaller ones already measured: the 8.75 m bins cost
// 0.36 m at worst (`gridSpread` in `shared/heatmap.js`), and an uneven camera
// biases a deep player up by 1.2 m at the coverage gate below.

/**
 * How long a player drifts in one direction before being pulled back, seconds.
 *
 * Assumed, not measured, and the one input here that footage will replace: a
 * minute is where positional play looks like positional play, and both
 * neighbouring values were simulated so the cost of being wrong is known. At
 * tau = 30 s the bands narrow by about a quarter, at 120 s they widen by about
 * a half. Wrong in the widening direction costs findings that were real; wrong
 * the other way invents findings. That is why this sits at the pessimistic end
 * of plausible rather than the middle.
 */
const WANDER_TAU_S = 60;

/** Two standard deviations, so a remark survives one match's worth of luck. */
const POSITION_SIGMAS = 2;

/**
 * The widest a player's own uncertainty can get before they are unjudgeable.
 *
 * Both failure modes land here: tracked too briefly, or genuinely everywhere. A
 * roamer whose band is 20 m wide has no average position worth comparing, and
 * that is a fact about how they played rather than a fault in the video. Their
 * row still shows what was measured — it is the verdict that is withheld, not
 * the figure.
 */
export const MAX_BAND_M = 8;

/**
 * The smallest gap this will ever call a gap, whatever the arithmetic says.
 *
 * A pair of tightly held, fully tracked players can drive the computed
 * threshold down toward 3 m, and at that point the model's own assumptions —
 * tau above, one camera, a pitch measured with a tape — are doing more work
 * than the data. Five metres is a stride and a half of a real difference.
 */
const POSITION_FLOOR_M = 5;

/**
 * Added to every threshold to cover the camera's own pull on a deep player.
 *
 * Not in quadrature: an uneven camera is a *bias*, not noise, and biases add.
 * It is one-directional in the worst possible direction too — it drags deep
 * players up and leaves forwards alone, which is precisely the pattern that
 * would fake a "your defender played ahead of your midfield" finding. Measured
 * at the gate below.
 */
const COVERAGE_MARGIN_M = 1.25;

/**
 * How uneven the camera may be before no verdict is offered at all.
 *
 * Third-to-third spread in visible share. Measured, on Gaussian occupancy
 * weighted by per-third visibility, as the shift it puts on the deepest line:
 *
 * | spread | 0.05 | 0.10 | 0.15 | 0.20 | 0.30 | 0.40 | 0.50 |
 * |---|---|---|---|---|---|---|---|
 * | deep player pulled up | 0.4 m | 0.7 | 0.9 | **1.2** | 1.8 | 2.6 | 3.5 |
 *
 * At 0.2 the bias is 1.2 m and `COVERAGE_MARGIN_M` covers it. Past that it
 * grows faster than any constant can absorb, and a camera that lost a whole end
 * puts a defender 9.5 m up the pitch on its own — a finding made of nothing.
 */
const COVERAGE_SPREAD_MAX = 0.2;

/**
 * How firmly one player's average position is pinned down, in metres either way.
 *
 * `POSITION_SIGMAS * spread * sqrt(2 * tau / tracked)`, per the table above.
 * Null when either input is missing, because a band is the only thing that
 * makes the position underneath it mean anything, and a position offered
 * without one would be read as exact.
 */
export function positionBand(spreadM, minutesTracked) {
    if (!(spreadM > 0) || !(minutesTracked > 0)) return null;
    return POSITION_SIGMAS * spreadM * Math.sqrt(WANDER_TAU_S / (30 * minutesTracked));
}

/** The gap two players must show before it is worth remarking on. */
function gapThreshold(a, b) {
    return Math.max(POSITION_FLOOR_M, Math.hypot(a.bandM, b.bandM) + COVERAGE_MARGIN_M);
}

/**
 * Whether the camera saw enough of the pitch evenly enough to support a verdict.
 *
 * Three answers, not two. An absent `pitch_coverage` is not an even camera —
 * it is a run that could not measure its own framing, which every report
 * published before task #97 also looks like. Treating that as fine would apply
 * the verdict exactly where it cannot be checked.
 */
export function coverageVerdict(coverage) {
    const thirds = coverage?.thirds;
    if (!thirds) return { ok: false, reason: 'unknown' };
    // Read before converting. `Number(null)` is 0 and 0 is a perfectly finite
    // share, so a third the run never measured would arrive here as a camera
    // that saw nothing of that end — 'uneven' rather than 'unknown', which is a
    // different claim and the wrong note under the bars. A missing key already
    // came back NaN; an explicitly null one has to land in the same place.
    const raw = ['left', 'middle', 'right'].map((k) => thirds[k]);
    if (raw.some((s) => s == null || !Number.isFinite(Number(s)))) {
        return { ok: false, reason: 'unknown' };
    }
    const shares = raw.map(Number);
    const spread = Math.max(...shares) - Math.min(...shares);
    return spread > COVERAGE_SPREAD_MAX
        ? { ok: false, reason: 'uneven', spread }
        : { ok: true, reason: null, spread };
}

const WITHHELD_NOTES = {
    unknown: 'This run did not record how much of the pitch the camera saw, so '
        + 'the positions below are shown without a verdict — a camera that '
        + 'favoured one end drags deep players up the pitch by several metres, '
        + 'and there is no way to tell from here whether this one did.',
    uneven: 'The camera saw one part of the pitch much better than another, '
        + 'which pulls players toward the part it saw. The positions below are '
        + 'measured; comparing them between lines would mostly compare the '
        + 'camera.',
    'no-lines': 'Give these players a position and this will say whether any of '
        + 'them played somewhere other than where they were picked.',
    'too-thin': 'Nobody was tracked long enough, or held a tight enough band, '
        + 'for their average position to be worth comparing with anyone '
        + 'else\u2019s — so the positions below are shown on their own.',
};

/**
 * Where each player averaged, and where that disagrees with their assigned line.
 *
 * `rows` are already oriented — `{ playerId, name, position, forwardM,
 * lateralM, spreadM, minutesTracked }`, forwardM measured up the pitch from the
 * goal they were defending. Orientation happens in `shared/heatmap.js`, which
 * owns the pitch dimensions; this module has none and takes metres as given.
 *
 * Returns `{ rows, remarks, withheld, note }`. **The rows are always there and
 * the remarks may not be**, which is the whole shape of this feature: where
 * somebody played is a measurement and stands on its own, while "they played
 * out of position" is a verdict that needs the camera and the tracking to have
 * been good enough to support it. A described figure and a judgement are
 * different claims and this refuses to let the second ride in on the first.
 *
 * An empty `remarks` with `withheld` null is a real answer, and the useful one:
 * every line sat where it was picked to sit.
 *
 * Keepers appear in the rows and never in the remarks. How high a keeper played
 * is a genuine question — it is most of what a sweeper-keeper is — but they
 * cover a third the ground of a midfielder, and ranking them on the same axis
 * would put every keeper at the bottom of the table and call it a finding.
 *
 * `lateralM` is carried and never judged. `POSITIONS` has four lines and no
 * sides, so there is nothing assigned for a left-right measurement to disagree
 * with, and a right-back who spent the match on the left would have to be seen
 * rather than flagged.
 */
export function positionalPlay(rows, options = {}) {
    const usable = (rows || []).filter((r) => r && Number.isFinite(r.forwardM));
    const out = usable.map((r) => {
        const bandM = positionBand(r.spreadM, r.minutesTracked);
        return { ...r, bandM, judgeable: bandM != null && bandM <= MAX_BAND_M };
    }).sort((a, b) => a.forwardM - b.forwardM);

    const withheld = (() => {
        if (!out.some((r) => positionOf(r.position))) return 'no-lines';
        const camera = coverageVerdict(options.coverage);
        if (!camera.ok) return camera.reason;
        if (out.filter((r) => r.judgeable && positionOf(r.position)).length < 2) {
            return 'too-thin';
        }
        return null;
    })();

    return {
        rows: out,
        remarks: withheld ? [] : lineRemarks(out),
        withheld,
        note: withheld ? WITHHELD_NOTES[withheld] : null,
    };
}

/**
 * One remark per player who cleared a whole line they were not picked in.
 *
 * The rule is deliberately hard to satisfy: further forward than **every**
 * player in a line ahead of them, each pair by its own threshold. Beating the
 * average of a line is a much weaker claim that a single deep-lying midfielder
 * would trigger constantly, and a coach who is told twice about something that
 * turns out to be nothing stops reading the third time.
 *
 * A line with anyone unjudgeable in it is skipped entirely rather than compared
 * on whoever is left. "Ahead of the whole midfield" is a statement about the
 * whole midfield, and it cannot be made from three of the four.
 */
function lineRemarks(rows) {
    const order = POSITIONS.map((p) => p.id).filter((id) => id !== 'gk');
    const lines = new Map(order.map((id) => [id, rows.filter(
        (r) => positionOf(r.position) === id,
    )]));
    const solid = (id) => {
        const line = lines.get(id) || [];
        return line.length > 0 && line.every((r) => r.judgeable) ? line : null;
    };

    const best = new Map();
    const keep = (row, remark, rank) => {
        const held = best.get(row.playerId);
        if (!held || rank > held.rank) best.set(row.playerId, { ...remark, rank });
    };

    for (let i = 0; i < order.length; i += 1) {
        for (let j = i + 1; j < order.length; j += 1) {
            const behind = solid(order[i]);
            const ahead = solid(order[j]);
            if (!behind || !ahead) continue;
            const aheadLabel = POSITION_BY_ID.get(order[j]).plural.toLowerCase();
            const behindLabel = POSITION_BY_ID.get(order[i]).plural.toLowerCase();

            // Each side of the comparison, worked out before either is
            // recorded, because whether one of them is worth saying depends on
            // the other.
            const risen = behind.filter(
                (p) => ahead.every((q) => p.forwardM - q.forwardM > gapThreshold(p, q)),
            );
            const dropped = ahead.filter(
                (q) => behind.every((p) => p.forwardM - q.forwardM > gapThreshold(p, q)),
            );

            // Every player in one line clearing every player in the other is
            // the same statement read from both ends, and it arrives as one
            // remark per player on both sides: a lone striker dropping in — a
            // 4-2-3-1, the most ordinary shape there is — would flag himself
            // and all four defenders for the one thing that happened. So when
            // the two lines have swapped over entirely, only the smaller of
            // them is remarked on. Not because the smaller line is the one at
            // fault, which nothing here can know, but because "your striker
            // played behind your defence" and "your four defenders played ahead
            // of your striker" are the same sentence and the coach only needs
            // it once. Two lines of equal size leave nothing to choose between,
            // and both are kept.
            const inverted = behind.length > 0 && risen.length === behind.length;
            const sayRisen = inverted && behind.length > ahead.length ? [] : risen;
            const sayDropped = inverted && ahead.length > behind.length ? [] : dropped;

            for (const p of sayRisen) {
                {
                    keep(p, {
                        playerId: p.playerId,
                        name: p.name,
                        position: p.position,
                        direction: 'ahead',
                        line: order[j],
                        // The gap actually cleared, not the one that mattered
                        // least — a coach asked "by how much" means the margin
                        // over the nearest of them.
                        gapM: Math.min(...ahead.map((q) => p.forwardM - q.forwardM)),
                        text: `played ahead of every one of the ${aheadLabel}`,
                    }, j);
                }
            }
            for (const q of sayDropped) {
                {
                    keep(q, {
                        playerId: q.playerId,
                        name: q.name,
                        position: q.position,
                        direction: 'behind',
                        line: order[i],
                        gapM: Math.min(...behind.map((p) => p.forwardM - q.forwardM)),
                        text: `played behind every one of the ${behindLabel}`,
                    }, order.length - i);
                }
            }
        }
    }

    return [...best.values()]
        .map(({ rank, ...remark }) => remark)
        .sort((a, b) => b.gapM - a.gapM);
}
