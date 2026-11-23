// Stats per position: the nine places a team's outfielders stand in, from the
// report's `teams.<side>.positions` (pitchiq/positions.py).
//
// A position is named by place, not by the team sheet or a fitted formation:
// the back, middle or front line crossed with left, centre or right, from
// where the player stood in their own team's shape over a few seconds. So
// "left back" here is whoever was playing there, which is the question this
// block answers. Pure functions only; the drawing is coach/match-play.js.

import { NOT_A_PLAYER } from './report-player.js?v=156';

const POSITION_LINES = ['back', 'middle', 'front'];
const POSITION_SIDES = ['left', 'centre', 'right'];

const LINE_NAMES = { back: 'back', middle: 'midfield', front: 'forward' };
const SIDE_NAMES = { left: 'Left', centre: 'Centre', right: 'Right' };

/** "Left back", "Centre midfield", "Right forward". */
export function positionName(role) {
    const [line, side] = String(role).split('_');
    if (!LINE_NAMES[line] || !SIDE_NAMES[side]) return null;
    return `${SIDE_NAMES[side]} ${LINE_NAMES[line]}`;
}

/**
 * The nine roles laid out as the pitch reads attacking right: a row per side
 * (left wing on top, as the heat maps draw it), back line first in each row.
 */
export function positionLayout() {
    return POSITION_SIDES.map((side) => POSITION_LINES.map((line) => `${line}_${side}`));
}

/** True when a team block has roles to show. */
export function hasPositions(block) {
    const roles = block?.roles;
    return Boolean(roles && Object.values(roles).some((row) => (row?.minutes ?? 0) > 0));
}

/** The role with the most minutes, for the first view; null when none. */
export function busiestRole(block) {
    let best = null;
    for (const [role, row] of Object.entries(block?.roles || {})) {
        if ((row?.minutes ?? 0) > 0 && (!best || row.minutes > block.roles[best].minutes)) {
            best = role;
        }
    }
    return best;
}

/** Runs of consecutive minutes: [3, 4, 5, 9] -> [[3, 5], [9, 9]]. */
export function minuteRuns(minutes) {
    const sorted = [...new Set(minutes)].sort((a, b) => a - b);
    const runs = [];
    for (const m of sorted) {
        const last = runs[runs.length - 1];
        if (last && m === last[1] + 1) last[1] = m;
        else runs.push([m, m]);
    }
    return runs;
}

const HALF_NAMES = { 1: '1st half', 2: '2nd half' };

/**
 * "1st half 4–9′, 12′ · 2nd half 1–3′". Minutes count from each half's start
 * (the first is 1′), as the map knows when a half began but not the match clock.
 */
export function whenText(byHalf) {
    return Object.keys(byHalf)
        .map(Number)
        .sort((a, b) => a - b)
        .map((half) => {
            const runs = minuteRuns(byHalf[half]).map(([a, b]) => (
                a === b ? `${a + 1}′` : `${a + 1}–${b + 1}′`
            ));
            return `${HALF_NAMES[half] || `Period ${half}`} ${runs.join(', ')}`;
        })
        .join(' · ');
}

/**
 * Who stood in a role, as the coach's players: per player their minutes there
 * and when, most minutes first.
 *
 * `row.holders` is a list per half of flat parallel lists (minute, cluster_id,
 * seconds): Firestore holds no arrays inside arrays. `mapping` is the coach's
 * cluster-to-player choices (`cvMapping`), `names` a Map from player id to the
 * name to show. A cluster the coach has not matched, or marked as not a
 * player, is `unmatchedMinutes`; time the identity could not name at all
 * (fragments too short to keep) is the report's own `unnamed_minutes`.
 */
export function positionHolders(row, mapping, names = new Map()) {
    const players = new Map();
    let unmatched = 0;
    for (const h of row?.holders || []) {
        const n = Math.min(h?.minute?.length || 0, h?.cluster_id?.length || 0, h?.seconds?.length || 0);
        for (let i = 0; i < n; i += 1) {
            const pid = mapping?.[String(h.cluster_id[i])];
            const seconds = Number(h.seconds[i]) || 0;
            if (!pid || pid === NOT_A_PLAYER) {
                unmatched += seconds;
                continue;
            }
            const acc = players.get(pid) || { playerId: pid, seconds: 0, byHalf: {} };
            acc.seconds += seconds;
            (acc.byHalf[h.half] ||= []).push(h.minute[i]);
            players.set(pid, acc);
        }
    }
    const list = [...players.values()]
        .map((p) => ({
            playerId: p.playerId,
            name: names.get(p.playerId) || 'Unnamed player',
            minutes: p.seconds / 60,
            when: whenText(p.byHalf),
        }))
        .sort((a, b) => b.minutes - a.minutes || a.name.localeCompare(b.name));
    return {
        players: list,
        unmatchedMinutes: unmatched / 60,
        unnamedMinutes: row?.unnamed_minutes ?? null,
    };
}

/** Minutes as "12 min", or "<1 min" for a sliver. */
export function minutesText(minutes) {
    if (minutes == null) return '–';
    if (minutes > 0 && minutes < 1) return '<1 min';
    return `${Math.round(minutes)} min`;
}

/**
 * The rows of the stats table for one role: [label, value] pairs, value text.
 * Distance per 90 minutes in the role, as the raw total mostly says how long
 * someone stood there.
 */
export function roleStatRows(row) {
    if (!row) return [];
    const pct = (share) => (share == null ? '–' : `${Math.round(share * 100)}%`);
    const per90 = row.minutes > 0 ? (row.distance_m / 1000) * (90 / row.minutes) : null;
    const passes = row.passes_attempted
        ? `${row.passes_completed} of ${row.passes_attempted} (${pct(row.passes_completed / row.passes_attempted)})`
        : '0';
    return [
        ['Minutes filled', minutesText(row.minutes)],
        ['Time someone was there', pct(row.occupied_share)],
        ['Players there at once, on average', row.players_mean == null ? '–' : row.players_mean.toFixed(1)],
        ['Touches', String(row.touches)],
        ['Passes completed', passes],
        ['Carries', String(row.carries)],
        ['Shots (goals)', `${row.shots} (${row.goals})`],
        ['Defensive actions', String(row.defensive_actions)],
        ['Distance', `${(row.distance_m / 1000).toFixed(1)} km`],
        ['Distance per 90 minutes there', per90 == null ? '–' : `${per90.toFixed(1)} km`],
        ['Seen by the camera, not guessed', pct(row.seen_share)],
    ];
}
