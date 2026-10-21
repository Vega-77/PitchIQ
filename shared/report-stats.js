// The match figures, grouped by kind.
//
// Part of `report.js`, which re-exports it.

// --------------------------------------------------------- stats, by kind
//
// The coach's match view was one grid of twenty-five boxes in the order they
// happened to be written, tagged counts and video-derived figures interleaved,
// and the half-time page was a flat list built separately from the same
// document. Both grew a row at a time and neither was ever laid out.
//
// A coach does not read twenty-five numbers. They ask a question — did we keep
// the ball, did we pass it forward, did we make chances, did we defend — and
// look for the two or three numbers that answer it. Grouping is what makes that
// possible without reading every box, and it is the only reason several
// published figures below could be added at all: territory, giveaways by third
// and the passing breakdowns have been in the document since they were
// computed, and there was nowhere to put them that would not have made the pile
// worse.
//
// Both pages group by this list rather than each keeping their own, because the
// interesting failure is not a missing heading — it is the two pages quietly
// disagreeing about whether a switch of play is passing or attacking, in a
// project whose whole premise is that the half-time page and the full report
// describe the same match.

export const STAT_TYPES = [
    { id: 'match', title: 'The match' },
    {
        id: 'possession',
        title: 'Possession',
        note: 'Thirds are each side’s share of its own time on the ball, '
            + 'not of the match — so the two rows do not add up to 100%.',
    },
    {
        id: 'phases',
        title: 'Phase of play',
        note: 'Each row is a share of that side’s own possessions, so the two '
            + 'columns do not add up. A possession counts by the furthest point '
            + 'it reached, so one that began in the final third counts as '
            + 'having got there. “Out from the back” is the exception: it is '
            + 'out of the possessions that started there, not out of all of them.',
    },
    { id: 'passing', title: 'Passing' },
    { id: 'attacking', title: 'Attacking' },
    { id: 'defending', title: 'Defending' },
    {
        id: 'keeping',
        title: 'Goalkeeping',
        note: 'The shots and the goals themselves are up in Attacking, from '
            + 'the other side’s point of view; these are what the keeper did '
            + 'about them. Save percentage is out of the shots that reached '
            + 'the line, not out of every shot faced.',
    },
    {
        id: 'shape',
        title: 'Shape',
        note: 'Averaged across the run, and only as good as the calibration. '
            + 'The goalkeepers are left out of all four: a keeper stands '
            + 'behind his defence at every moment, so counting him would pull '
            + 'the back line several metres deeper than it was played.',
    },
];

/**
 * Rows into `[{ id, title, note, rows }]`, in the order above, empties dropped.
 *
 * A group's note is only kept when a row that needs it survived. Both notes
 * above explain a denominator that some rows have and others do not — the
 * thirds, and the shape figures — and a caption explaining a denominator for
 * figures that are not on screen is worse than no caption, because a reader
 * will attach it to whatever is.
 *
 * A row with an unrecognised type is kept, in a group of its own at the end.
 * Dropping it would be the worse failure by a distance: a typo in a type name
 * would silently delete a measured number from a coach's screen and leave the
 * page looking complete. This way it is visibly wrong, and
 * `every row carries a type this module knows` catches it in the test suite
 * before anyone sees it.
 */
export function groupStats(rows) {
    const known = new Map(STAT_TYPES.map((type) => [type.id, { ...type, rows: [] }]));
    const extra = new Map();

    for (const row of rows || []) {
        // Absent is not zero, one more time. A null here is a figure the
        // pipeline could not measure — usually for want of a calibration — and
        // a box reading 0 would say it looked and found none.
        //
        // A comparison row survives on either side having one. The asymmetric
        // case is real: PPDA is null for a side that made no defensive actions
        // at all, and dropping the row would take the opposition's figure down
        // with it — which is the half a coach most wants when their own is
        // missing for that reason.
        if (row == null) continue;
        if (row.value == null && (!row.kind || row.themValue == null)) continue;
        const group = known.get(row.type)
            || extra.get(row.type)
            || extra.set(row.type, { id: row.type, title: row.type, rows: [] })
                .get(row.type);
        group.rows.push(row);
    }

    return [...known.values(), ...extra.values()]
        .filter((group) => group.rows.length)
        .map((group) => (
            group.note && !group.rows.some((row) => row.explained)
                ? { ...group, note: '' }
                : group
        ));
}
