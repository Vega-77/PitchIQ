// Substitutions and the football either side of them.
//
// Part of `report.js`, which re-exports it.

import { num, count } from './report-format.js?v=150';
import { FIRST_HALF, HALF_TIME, matchClockMap } from './report-clock.js?v=150';

// ------------------------------------------------ the football either side of
//                                                   a substitution
//
// The stats catalog has asked for this since it was written — *"substitution
// impact: team stats in the window before vs. after each sub (exact sub timing
// comes straight from Phase 3's live log)"* — and it is the one thing on that
// list that pays the tablet back. Somebody stands in the rain for ninety
// minutes tapping subs into an iPad; until now the only thing those taps bought
// was a minutes-played column.
//
//     What this is not.
//
// It is **not** substitution impact, and the block does not use the word. A
// coach makes a change *because* of what is already happening: a side being
// overrun brings on a defender, a side chasing brings on a forward. The change
// and the reason for it arrive together and nothing here can pull them apart.
// Add to that the opponent, who also made changes and whose changes are in
// nobody's log, and the scoreline, which moves on its own and changes how both
// sides play.
//
// So every figure below answers "what did the next ten minutes look like next
// to the last ten", and the note says that in as many words. A block that
// promised impact would be promising a causal claim off a sample of one.
//
//     Three ways a window lies, and what is done about each.
//
// **Unequal spans.** Nine minutes of football has more of everything in it than
// six, so counts either side must cover the same length of clock. Both windows
// are cut to the shorter of the two, rather than converted to per-minute rates
// — `insideNoise` needs counts, and a rate would hide that the comparison rests
// on eleven events.
//
// **Another change inside the window.** If two subs come six minutes apart, the
// ten minutes after the first are mostly the ten minutes around the second, and
// reporting both would serve the same passage of football up twice under two
// different headings. Windows are clipped at the neighbouring change.
//
// **Footage that stopped.** Events only exist where the pipeline was looking.
// A window running past the end of a clip finds nothing, which is a fact about
// the clip and reads on screen as a team that stopped playing. Clipped to the
// processed window, and dropped if what is left is too short to say anything.
//
//     Half-time is not a window, it is a wall.
//
// A change at the interval is the most common change there is, and the least
// measurable one: what sits between the two windows is not the substitution, it
// is fifteen minutes and a team talk. Those changes are listed — a coach should
// see that they happened — and deliberately not scored.
//
// The same wall clips ordinary windows. Ten minutes either side of a change on
// 43 minutes would otherwise compare the end of one half against the start of
// the next, with the oranges in the middle.

/**
 * When the match ended, or null when nothing knows.
 *
 * `matchEndS` comes off the tag log, and a match nobody tagged has one of
 * zero — which is not a final whistle, it is the absence of one. Taken at face
 * value it is a whistle before kick-off: every substitute who came off is
 * dropped for having left "after" it, and every window is clipped to nothing.
 */
const whistle = (value) => (num(value) > 0 ? num(value) : null);

/** How far apart two roster moves can be and still be one decision. */
const CHANGE_GROUP_S = 90;

/** How much football either side of a change, before anything clips it. */
const CHANGE_WINDOW_S = 600;

/**
 * The shortest window worth counting.
 *
 * Four minutes, set from what is in one: at the event rates this pipeline
 * produces a side touches the ball a couple of dozen times, which is already
 * near the floor at which `insideNoise` will ever answer anything but "cannot
 * tell". Below it the honest output is a blank, not a percentage.
 */
const MIN_CHANGE_WINDOW_S = 240;

/**
 * Did our share of the ball really move, or is this the sample size talking?
 *
 * A two-proportion test at the same two-sigma bar `insideNoise` uses, and it
 * has to be that rather than `insideNoise` itself: the claim on screen is about
 * a **share before against a share after**, and `insideNoise` answers a
 * different question — whether one window's two sides differ. Comparing our own
 * count either side would come closer, and would still be wrong, because the
 * total volume of events is not fixed between the two windows either.
 *
 * The bar this sets is high, and knowing how high is the point of writing it
 * down. At an even split, the smallest swing that clears it:
 *
 *      20 events a window   32 points
 *      60                   18
 *     100                   14
 *     150                   12
 *     400                    7
 *
 * Ten minutes of football is somewhere in the low hundreds of on-ball events
 * per side at best, so **this will call most changes a draw, and it is right
 * to.** A tool that flagged a ten-point swing off a hundred events would be
 * pointing a coach at a coin toss and putting a teenager's name on it.
 */
export function shareShifted(before, after) {
    const n1 = (before?.us ?? 0) + (before?.them ?? 0);
    const n2 = (after?.us ?? 0) + (after?.them ?? 0);
    if (!n1 || !n2) return false;

    const p1 = before.us / n1;
    const p2 = after.us / n2;
    const pooled = (before.us + after.us) / (n1 + n2);
    const se = Math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2));
    // A side that had every event in both windows has no spread to divide by,
    // and no difference either.
    if (!(se > 0)) return false;
    return Math.abs(p1 - p2) >= 2 * se;
}

/**
 * Every moment the eleven changed, from the roster's own stints.
 *
 * Built from stints rather than from the `sub` entries in the log because the
 * stints are the reconciled record — `onPitchAt` and `minutesFrom` already read
 * them, and a player added to a match after the fact has stints without ever
 * having a log entry.
 *
 * A kick-off is not a change, so `inS === 0` is skipped; neither is the final
 * whistle, so an `outS` at `matchEndS` is skipped too. Moves inside
 * `CHANGE_GROUP_S` of each other are one change: a double substitution goes on
 * at one stoppage and arrives as two taps a few seconds apart, and counting it
 * twice would put two headings over one passage of football.
 *
 * Grouping measures from the group's first move, never from its latest, so a
 * steady trickle of changes a minute apart cannot chain into one group that
 * swallows a half.
 */
export function substitutionChanges(roster, options = {}) {
    const { groupS = CHANGE_GROUP_S } = options;
    // Zero is what `matchEndS` is on a match nobody tagged, and it is not the
    // final whistle — it is the absence of one. Taken literally it deletes
    // every player who came off, because no `outS` is below it.
    const matchEndS = whistle(options.matchEndS);

    const moves = [];
    for (const entry of roster || []) {
        const name = entry.playerName || 'Unnamed';
        for (const stint of entry.stints || []) {
            const inS = num(stint?.inS);
            const outS = num(stint?.outS);
            if (inS != null && inS > 0) moves.push({ clockS: inS, name, on: true });
            if (outS != null && (matchEndS == null || outS < matchEndS)) {
                moves.push({ clockS: outS, name, on: false });
            }
        }
    }
    moves.sort((a, b) => a.clockS - b.clockS);

    const changes = [];
    for (const move of moves) {
        let group = changes[changes.length - 1];
        if (!group || move.clockS - group.clockS > groupS) {
            group = { clockS: move.clockS, on: [], off: [] };
            changes.push(group);
        }
        (move.on ? group.on : group.off).push(move.name);
    }
    return changes;
}

/**
 * What the video found either side of each change.
 *
 * `events` is the published `cvEvents.events` list, in **video** seconds;
 * `roster` carries stints on the **match clock**. `clock` relates them, once,
 * here — the rule this module keeps everywhere the two meet.
 *
 * Returns null when nobody was substituted, so a caller hides the block rather
 * than heading an empty list.
 *
 * **Nothing is scored without a placeable clock.** Second-half stints are match
 * minutes and second-half events are video minutes, and without the second-half
 * anchor the offset relates them wrongly by however long the interval lasted —
 * ten to fifteen minutes, in the direction that quietly slides a window off the
 * football it claims to describe. A report on a first-half clip needs no anchor
 * and is scored normally, which is the case the half-time page cares about.
 *
 * Dead-ball events are left out, matching `possessionIsInPlay`: the share below
 * is a share of football, and a throw-in taken twice is not a side on top.
 */
export function substitutionWindows(roster, events, options = {}) {
    const {
        clock = matchClockMap({}),
        period = null,
        window = null,
        truncated = false,
        windowS = CHANGE_WINDOW_S,
        minWindowS = MIN_CHANGE_WINDOW_S,
    } = options;

    const matchEndS = whistle(options.matchEndS);
    const changes = substitutionChanges(roster, { matchEndS });
    if (!changes.length) return null;

    const placeable = Boolean(clock.knowsSecondHalf) || period === FIRST_HALF;
    const breakAt = clock.knowsSecondHalf ? clock.secondHalfClockS : null;

    // The footage, on the match clock. A window is only as long as the part of
    // it somebody actually looked at.
    const seenFrom = num(window?.start_s) == null
        ? null : clock.toClock(window.start_s).clockS;
    const seenTo = num(window?.end_s) == null
        ? null : clock.toClock(window.end_s).clockS;

    const tally = (from, to) => {
        const out = {
            us: 0, them: 0, shots: 0, shotsAgainst: 0, xg: 0, xgAgainst: 0,
        };
        for (const event of events || []) {
            if (event?.inPlay === false) continue;
            const at = num(event?.timestampS);
            if (at == null) continue;
            const read = clock.toClock(at);
            if (read.period === HALF_TIME) continue;
            if (!(read.clockS >= from && read.clockS < to)) continue;

            const ours = event.team === 'team_a';
            if (!ours && event.team !== 'team_b') continue;

            if (ours) out.us += 1; else out.them += 1;
            if (event.type === 'shot') {
                if (ours) {
                    out.shots += 1;
                    out.xg += num(event.xg) || 0;
                } else {
                    out.shotsAgainst += 1;
                    out.xgAgainst += num(event.xg) || 0;
                }
            }
        }
        return out;
    };

    const share = (w) => (w.us + w.them > 0 ? w.us / (w.us + w.them) : null);

    const rows = changes.map((change, i) => {
        const at = change.clockS;
        const prev = i > 0 ? changes[i - 1].clockS : null;
        const next = i < changes.length - 1 ? changes[i + 1].clockS : null;
        const atBreak = breakAt != null && Math.abs(at - breakAt) <= CHANGE_GROUP_S;

        const halfStart = breakAt != null && at >= breakAt ? breakAt : 0;
        const halfEnd = breakAt != null && at < breakAt
            ? breakAt : (matchEndS ?? Infinity);

        const lo = Math.max(
            at - windowS, halfStart,
            prev ?? -Infinity, seenFrom ?? -Infinity,
        );
        const hi = Math.min(
            at + windowS, halfEnd,
            next ?? Infinity, seenTo ?? Infinity,
        );
        const spanS = Math.min(at - lo, hi - at);

        const row = {
            clockS: at,
            on: change.on,
            off: change.off,
            atBreak,
            spanS: spanS > 0 ? spanS : 0,
            scored: false,
            reason: null,
            before: null,
            after: null,
            shareBefore: null,
            shareAfter: null,
            swing: null,
            tentative: true,
        };

        // Ordered by which answer is the more useful to read. "It was at the
        // break" explains itself; "no clock" is a field the coach can fill in;
        // "another change" and "the footage ends" are facts about this match.
        if (!placeable) row.reason = 'clock';
        else if (atBreak) row.reason = 'break';
        else if (spanS < minWindowS) {
            const crowded = (prev != null && at - prev < minWindowS)
                || (next != null && next - at < minWindowS);
            // Only blame the footage where the footage is genuinely the
            // tighter limit. A clip that runs to the final whistle ends at the
            // same second the match does, and "the processed footage runs out"
            // would send a coach looking for a problem with their video.
            const cut = (seenFrom != null && seenFrom > halfStart
                    && at - seenFrom < minWindowS)
                || (seenTo != null && seenTo < halfEnd && seenTo - at < minWindowS);
            row.reason = crowded ? 'crowded' : (cut ? 'footage' : 'edge');
        }
        if (row.reason) return row;

        row.scored = true;
        row.spanS = spanS;
        row.before = tally(at - spanS, at);
        row.after = tally(at, at + spanS);
        row.shareBefore = share(row.before);
        row.shareAfter = share(row.after);
        row.swing = row.shareBefore == null || row.shareAfter == null
            ? null : row.shareAfter - row.shareBefore;
        row.tentative = !shareShifted(row.before, row.after);
        return row;
    });

    return {
        rows,
        placeable,
        truncated: Boolean(truncated),
        scored: rows.filter((r) => r.scored).length,
        atBreak: rows.filter((r) => r.atBreak).length,
    };
}

/**
 * The one change worth a sentence, if any of them are.
 *
 * The largest swing that survived `insideNoise`, and nothing at all otherwise —
 * which is the common answer and the correct one. Ten minutes of football is a
 * small sample and most changes will not move it detectably; a read that fired
 * every match would be measuring the noise and would be believed anyway.
 *
 * Worded as a coincidence in time, never as a consequence. See the block
 * comment above.
 */
export function substitutionRead(result, clockText = (s) => `${Math.round(s / 60)}'`) {
    const scored = (result?.rows || []).filter(
        (r) => r.scored && !r.tentative && r.swing != null,
    );
    if (!scored.length) return null;

    const biggest = scored.reduce(
        (a, b) => (Math.abs(b.swing) > Math.abs(a.swing) ? b : a),
    );
    const pct = (v) => Math.round(v * 100);
    const who = biggest.on.length ? biggest.on.join(' and ') : 'the change';
    const minutes = Math.round(biggest.spanS / 60);

    return {
        title: biggest.swing > 0
            ? `You saw more of the ball after ${who} came on`
            : `You saw less of the ball after ${who} came on`,
        detail: `${pct(biggest.shareBefore)}% of the ball in the ${minutes} minutes `
            + `before ${clockText(biggest.clockS)}, ${pct(biggest.shareAfter)}% in `
            + 'the same span after. What changed alongside the substitution, not '
            + 'because of it.',
    };
}

/** Why a change has no figures beside it, in the coach's words. */
export const CHANGE_REASONS = {
    clock: 'no second-half kick-off saved, so the clock cannot place this',
    break: 'made at half-time, so the team talk is in the middle of it',
    crowded: 'another change too close to it',
    footage: 'the processed footage runs out',
    edge: 'too near kick-off or the final whistle',
};

/**
 * The caveats under the list. Always returns the causal one.
 *
 * That one is not decoration and is not conditional: it is the only thing
 * standing between a table of before-and-after percentages and a coach
 * concluding that a sixteen-year-old won them twenty minutes of possession.
 */
export function substitutionNote(result) {
    if (!result) return null;
    const parts = [];

    if (!result.placeable) {
        parts.push('Nothing here is measured, because the second-half kick-off '
            + 'has not been saved against the video — without it a match minute '
            + 'and a video minute are a whole interval apart.');
    } else if (!result.scored) {
        parts.push('None of these changes has enough uninterrupted football '
            + 'either side of it to compare.');
    } else {
        parts.push('Each pair counts the same length of clock either side of the '
            + 'change, cut to whichever side was shorter, and stops at the next '
            + 'change, at half-time and at the ends of the processed footage.');
    }

    if (result.atBreak) {
        parts.push(`${count(result.atBreak, 'change')} at half-time `
            + `${result.atBreak === 1 ? 'is' : 'are'} listed and not measured: `
            + 'what sits between those two windows is fifteen minutes and a team '
            + 'talk, not a substitution.');
    }

    if (result.truncated) {
        parts.push('The event list was truncated to its most confident, so it is '
            + 'not an even sample of the match and these shares lean towards the '
            + 'passages the video read best.');
    }

    if (result.scored) {
        const told = result.rows.filter((r) => r.scored && !r.tentative).length;
        parts.push(told
            ? 'A swing marked "within chance" is smaller than one this many '
                + 'events would hand out on their own.'
            : 'Every swing here is smaller than one this many events would hand '
                + 'out on their own — at a hundred events a window it takes '
                + 'about fourteen percentage points before a difference is '
                + 'worth reading, and most changes will not move it that far.');
    }

    parts.push('Shots are shown as counts because ten minutes holds one or two '
        + 'of them and a comparison of one against two is not a comparison.');

    parts.push('A change is made because of what is already happening, the '
        + 'opposition made changes nobody logged, and the scoreline moved on its '
        + 'own. This says what the football either side looked like — it does '
        + 'not say the substitution did it.');

    return parts.join(' ');
}
