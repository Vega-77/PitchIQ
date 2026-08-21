// What each event type means, and what detail it needs to be unambiguous.
//
// "Corner, us" is not self-explanatory: it could mean we won it or we conceded
// it, and a month later nobody remembers which the tagger meant. Every type
// therefore declares what picking a team actually asserts, and the tagging UI
// shows that wording rather than a bare toggle.

export const EVENTS = {
    goal: {
        label: 'Goal',
        // What choosing a team asserts for this event type.
        sideMeans: 'Scored by',
        needsPlayer: 'Scorer',
        needsAssist: true,
        tone: 'good',
    },
    card: {
        label: 'Card',
        sideMeans: 'Shown to',
        needsPlayer: 'Booked',
        needsCard: true,
        tone: 'warn',
    },
    foul: {
        label: 'Foul',
        sideMeans: 'Committed by',
        needsPlayer: 'Offender',
        tone: 'neutral',
    },
    offside: {
        label: 'Offside',
        sideMeans: 'Called against',
        needsPlayer: 'Player',
        tone: 'neutral',
    },
    corner: {
        label: 'Corner',
        sideMeans: 'Awarded to',
        tone: 'neutral',
    },
    throw_in: {
        label: 'Throw-in',
        sideMeans: 'Awarded to',
        tone: 'neutral',
    },
    goal_kick: {
        label: 'Goal kick',
        sideMeans: 'Awarded to',
        tone: 'neutral',
    },
    free_kick: {
        label: 'Free kick',
        sideMeans: 'Awarded to',
        tone: 'neutral',
    },
    out_of_bounds: {
        label: 'Out of bounds',
        sideMeans: 'Restart to',
        tone: 'neutral',
    },
};

/**
 * Just the type names. Derived rather than written out a second time — db.js
 * used to keep its own copy under this exact name, so `EVENT_TYPES` meant a
 * list of strings in one module and a map of specs in another.
 *
 * The same names appear once more in firestore.rules, which is unavoidable:
 * rules are a separate language and the server has to validate independently.
 */
export const EVENT_TYPES = Object.keys(EVENTS);

export const CARD_COLOURS = {
    yellow: { label: 'Yellow', short: 'Y' },
    second_yellow: { label: 'Second yellow', short: 'Y2' },
    red: { label: 'Red', short: 'R' },
};

export const PERIOD_LABELS = {
    kickoff_1st: 'Kick-off',
    halftime: 'Half-time',
    kickoff_2nd: 'Second half',
    full_time: 'Full time',
};

/** Human sentence for a logged event, e.g. "Corner awarded to Linden". */
export function describeEvent(entry, { usName = 'Us', themName = 'Them', playerName } = {}) {
    if (entry.kind === 'period') {
        return PERIOD_LABELS[entry.type] || entry.type.replace(/_/g, ' ');
    }
    if (entry.kind === 'sub') {
        return entry.label || 'Substitution';
    }

    const spec = EVENTS[entry.type];
    if (!spec) return entry.type.replace(/_/g, ' ');

    const team = entry.side === 'them' ? themName : usName;
    const parts = [spec.label];

    if (entry.type === 'card' && entry.cardColor) {
        parts[0] = `${CARD_COLOURS[entry.cardColor]?.label ?? ''} card`.trim();
    }

    parts.push(`${spec.sideMeans.toLowerCase()} ${team}`);
    if (playerName) parts.push(`— ${playerName}`);

    return parts.join(' ');
}

/**
 * Which colour a timeline dot should take for this entry — '', 'good', 'warn'
 * or 'period'. Only two of the declared tones are visually distinct, so
 * 'neutral' collapses to no modifier.
 */
export function timelineTone(entry) {
    if (entry.kind === 'period') return 'period';
    const tone = EVENTS[entry.type]?.tone;
    return tone === 'good' || tone === 'warn' ? tone : '';
}

// There is deliberately no `beneficiary(entry)` here — no function that
// flips the side on a foul, a card or an offside so the other team is credited
// with having won something. It existed, and `report.js` answers the same
// question a better way: it *labels* the row instead. `taggedTeamRows` prints
// "Corners won" against the side that took them and "Fouls committed" against
// the side that gave them away (`report.js:2800-2802`), so the number under a
// team is always the number that team did.
//
// The two approaches cannot both run. Inverting the side *and* labelling the
// row would invert it twice, and a table saying "Fouls committed" over a
// column of the opponent's fouls is wrong in the way nobody re-reads. If a
// caller ever needs the flip, it needs it instead of the label, not as well.
