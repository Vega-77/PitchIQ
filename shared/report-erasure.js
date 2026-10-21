// Taking a player off the record.
//
// Part of `report.js`, which re-exports it.

import { count } from './report-format.js?v=146';

// ------------------------------------------------------- taking a player off
//
// "Remove" was one button doing one thing: deleting `teams/{t}/players/{p}`,
// the squad-list document. Everything else about the student stayed exactly
// where it was — their name, shirt number and stints in the roster of every
// match they had played, their whole published report including minutes,
// distance, heatmap and shot map, their email as an invite key, and their id in
// `cvMapping.byCluster`, which is what ties a person to a cropped photograph cut
// out of the footage. The coach was shown "Player removed".
//
// The rules were never the problem: a coach has always been allowed to delete
// every one of those. Nothing asked them to.
//
//     Two intentions wearing one word.
//
// A coach who says "remove Jordan" may mean *they have left the team* — in
// which case the match reports must stay, because a report is a record of a
// match that happened and deleting it would falsify the team's own results —
// or they may mean *a guardian asked us to hold nothing*, in which case
// keeping any of it is the failure. A single button cannot serve both, and the
// one that existed served neither.
//
//     What an erase can and cannot reach.
//
// The match log stays. It records substitutions by player id, never by name, so
// with the named documents gone it holds an id that resolves to nobody — and
// the log is also the arithmetic behind *every other player's* minutes. Deleting
// the entry that put Jordan on would silently take time off whoever came off
// for them. Pseudonymous and load-bearing is a good reason to leave something
// alone; it is not a good reason to pretend it was deleted, so the coach is told.

/**
 * The cluster mapping with every reference to one player taken out.
 *
 * A new object, never the one passed in: the caller holds this in `state` and
 * mutating it in place would leave the screen agreeing with a write that has
 * not happened yet. Other players' figures are untouched — the whole point is
 * that erasing one student does not quietly re-attribute another's match.
 */
export function mappingWithout(byCluster, playerId) {
    const out = {};
    for (const [cluster, id] of Object.entries(byCluster || {})) {
        if (id !== playerId) out[cluster] = id;
    }
    return out;
}

/**
 * What is about to be deleted, in the coach's words, before they confirm it.
 *
 * `footprint` is `{ matches: [{ label, hasRoster, hasReport, clusters }],
 * hasInvite }` — what was actually found by reading, not what ought to exist.
 * A match the player was named in but never played still holds their name in
 * its roster, and a coach who is told "2 matches" when the answer is 5 has been
 * given a number they cannot act on.
 *
 * Returns `{ lines, matchCount, reportCount }`. Zero matches is a real and
 * ordinary answer — a player added last week and removed today — and it reads
 * as "nothing but their squad entry", not as an empty list.
 */
export function erasureNote(footprint = {}) {
    const matches = footprint.matches || [];
    const named = matches.filter((m) => m.hasRoster || m.hasReport);
    const reports = matches.filter((m) => m.hasReport);
    const clusters = matches.reduce((n, m) => n + (m.clusters || 0), 0);

    const lines = [];
    lines.push(named.length
        ? `Their name and shirt number come out of ${count(named.length, 'match', 'matches')}.`
        : 'They have not been named in a match yet, so there is nothing but '
          + 'their squad entry.');

    if (reports.length) {
        lines.push(`${count(reports.length, 'published report')} `
            + `${reports.length === 1 ? 'is' : 'are'} deleted — minutes, goals, `
            + 'and anything measured from video. If they have signed in, it '
            + 'disappears from their account too.');
    }
    if (clusters) {
        lines.push(`${count(clusters, 'tracked figure')} stop pointing at them, `
            + 'so the pictures cut from the footage are no longer tied to a name.');
    }
    if (footprint.hasInvite) {
        lines.push('Their invitation goes, which is the last place their email '
            + 'address is stored.');
    }

    // Only when they were actually in a match. Said unconditionally it would
    // imply something of theirs survives an erase that in fact takes
    // everything \u2014 a player added last week and removed today leaves nothing at
    // all, and a caveat about what is kept would be a caveat about nothing.
    if (named.length) {
        lines.push('The substitutions stay in the match log. They record who came '
            + 'on by an internal id rather than by name, and they are what every '
            + 'other player\u2019s minutes are counted from.');
    }

    return { lines, matchCount: named.length, reportCount: reports.length };
}
