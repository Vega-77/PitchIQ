// What a player is allowed to see of a match they played in.
//
// This module has no imports, which is deliberate. It decides which names and
// which events cross from the coach's record into a document a sixteen-year-old
// can read, and that decision should be testable on its own — not reachable
// only through a module that opens a Firestore connection at import time.
//
// The rule it enforces: a player sees everything they did, plus the goals.
// Goals get named because they are shouted across a pitch and announced to
// everyone present; that justification does not extend to anything else. No
// teammate's minutes, no teammate's fouls, and above all no teammate's cards —
// one minor's disciplinary record is not something to publish to another minor.
//
// Labels are built here rather than on the player's page. That is the
// mechanism, not a convenience: the portal never receives the roster, so the
// only name that can reach it is one this function chose to write.

// A player's report may carry at most this many timeline entries. A tagged
// match produces a few dozen; the cap exists so a runaway log cannot push a
// document towards Firestore's 1MB limit and fail the whole publish batch —
// which would block every player's report, not just one.
export const MAX_TIMELINE = 120;

const PERIOD_TEXT = {
    kickoff_1st: 'Kick-off',
    halftime: 'Half-time',
    kickoff_2nd: 'Second half',
    full_time: 'Full time',
};

const MINE_TEXT = {
    foul: 'You conceded a foul',
    offside: 'You were caught offside',
};

/**
 * The moments from a match that belong to `playerId`, plus the goals.
 *
 * `log` and `roster` are the coach's full record. Nothing from either reaches
 * the caller except through the labels built below.
 */
export function playerTimeline(log, roster, playerId) {
    const nameById = new Map((roster || []).map((r) => [r.id, r.playerName]));
    const entries = [];

    for (const e of log || []) {
        const mine = e.playerId === playerId
            || e.assistPlayerId === playerId
            || e.subInId === playerId
            || e.subOutId === playerId;

        if (e.kind === 'period') {
            entries.push({
                clockS: e.matchClockS, type: e.type, mine: false,
                label: PERIOD_TEXT[e.type] || e.type,
            });
            continue;
        }

        if (e.kind === 'sub') {
            if (!mine) continue;
            entries.push({
                clockS: e.matchClockS, type: 'sub', mine: true,
                label: e.subInId === playerId ? 'Came on' : 'Came off',
            });
            continue;
        }

        if (e.kind !== 'event') continue;

        if (e.type === 'goal') {
            const ours = e.side !== 'them';
            const scorer = nameById.get(e.playerId);
            let label;
            if (e.playerId === playerId) label = 'You scored';
            else if (e.assistPlayerId === playerId) label = `You assisted ${scorer || 'the goal'}`;
            else if (!ours) label = 'Goal conceded';
            else label = scorer ? `${scorer} scored` : 'Goal';
            entries.push({ clockS: e.matchClockS, type: 'goal', mine, label });
            continue;
        }

        // Everything else belongs to whoever it happened to, and is shown only
        // to them. This `continue` is the privacy boundary.
        if (!mine) continue;

        const label = e.type === 'card'
            ? `You were booked${e.cardColor === 'red' ? ' — red' : ''}`
            : MINE_TEXT[e.type] || `You: ${e.type.replace(/_/g, ' ')}`;
        entries.push({ clockS: e.matchClockS, type: e.type, mine: true, label });
    }

    return entries
        .sort((a, b) => a.clockS - b.clockS)
        .slice(0, MAX_TIMELINE);
}
