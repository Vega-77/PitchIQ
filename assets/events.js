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

/** Which team benefits, for counting things like "corners won". */
export function beneficiary(entry) {
    const spec = EVENTS[entry.type];
    if (!spec) return null;

    // For fouls, cards and offside the tagged side is the offender, so the
    // other team is the one that gains. Counting these naively is how a team
    // ends up credited with the fouls it committed.
    const inverted = ['foul', 'card', 'offside'];
    if (inverted.includes(entry.type)) {
        return entry.side === 'us' ? 'them' : 'us';
    }
    return entry.side;
}
