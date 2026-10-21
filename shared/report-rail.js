// Which section the report rail shows.
//
// Part of `report.js`, which re-exports it.

// ---------------------------------------------------------- the section rail
//
// The rail itself is `shared/rail.js`, which needs a DOM. This is the one
// decision in it that does not: which section a rail should be showing.

/**
 * The section to show, given the ones that exist and the one that was asked for.
 *
 * Falls back to the first rather than to nothing. The set of sections changes
 * underneath a reader — turning the sample preview off takes four blocks away,
 * publishing a report adds six — and one of the ones that goes may be the one
 * being read. Landing on the first section is where every report opens anyway;
 * landing on none of them would be a page that had gone blank.
 *
 * Returns null only when there is genuinely nothing to show, which is a report
 * with no blocks on it at all.
 */
export function railTarget(ids, wanted) {
    const available = (ids || []).filter((id) => id);
    if (!available.length) return null;
    return available.includes(wanted) ? wanted : available[0];
}
