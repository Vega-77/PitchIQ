// Small DOM helpers shared by every page.
//
// Each of these existed as a near-identical private copy in landing.js,
// coach.js, player.js, calibrate.js and live-tagging — five toast functions,
// five `$` shorthands, three "big number over a small label" builders. Having
// one copy means a change to how the app talks (or looks) happens once.

export const byId = (id) => document.getElementById(id);

/** Set text content by element id, tolerating a missing element. */
export function setText(id, value) {
    const el = byId(id);
    if (el) el.textContent = value;
}

// ---------------------------------------------------------------- toast

let toastTimer;

/**
 * Brief message at the bottom of the screen. Every page has a <div id="toast">.
 * The default 2.6s is a reading speed, not a round number: long enough for a
 * sentence, short enough not to sit over the thing you just tapped.
 */
export function toast(message, isError = false, ms = 2600) {
    const el = byId('toast');
    if (!el) return;
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

// ---------------------------------------------------------------- views

/**
 * Show one of a set of sibling sections and hide the rest.
 *
 * Pages that load data also carry a spinner; hiding it here means no caller
 * has to remember to, which was a recurring source of a dashboard stuck on
 * "Loading" after an error.
 */
export function showOnly(visibleId, allIds) {
    for (const id of allIds) {
        byId(id)?.classList.toggle('hidden', id !== visibleId);
    }
    byId('loading')?.classList.add('hidden');
}

// ---------------------------------------------------------------- formatting

/** Seconds as mm:ss, for a match clock. */
export function clockText(seconds) {
    const total = Math.max(0, Math.floor(seconds));
    const mm = String(Math.floor(total / 60)).padStart(2, '0');
    const ss = String(total % 60).padStart(2, '0');
    return `${mm}:${ss}`;
}

/** Goal difference and the like, where the sign is the point. */
export const signed = (n) => (n > 0 ? `+${n}` : String(n));

/**
 * "3 points" / "1 point" — pluralisation that reads as a sentence rather than
 * as "1 point(s)". Pass the plural form for anything the -s default gets wrong.
 */
export function plural(count, singular, pluralForm = `${singular}s`) {
    return `${count} ${count === 1 ? singular : pluralForm}`;
}

// ---------------------------------------------------------------- components

/**
 * A headline figure in a bordered box, for the stat grids. `tone` is one of the
 * `.stat` modifiers in app.css: is-good, is-warn, is-muted.
 */
export function statCard(value, label, tone = '') {
    const el = document.createElement('div');
    el.className = `stat ${tone}`.trim();
    el.innerHTML = '<div class="value"></div><div class="label"></div>';
    el.querySelector('.value').textContent = value ?? 0;
    el.querySelector('.label').textContent = label;
    return el;
}

/**
 * A compact number over a caption, for figures that sit inside a row rather
 * than in their own box. A zero is dimmed so the numbers that mean something
 * carry the eye down a column of matches.
 */
export function figure(value, label, tone = '') {
    const el = document.createElement('div');
    el.className = 'figure';
    el.innerHTML = '<div class="n"></div><div class="k"></div>';

    const n = el.querySelector('.n');
    n.textContent = value;
    if (tone) n.classList.add(tone);
    else if (!value || value === '0') n.classList.add('zero');

    el.querySelector('.k').textContent = label;
    return el;
}

/**
 * One row of a match timeline: clock, a dot on a rail, what happened, and which
 * team it belongs to.
 *
 * Takes finished strings rather than a log entry so this module stays purely
 * about the DOM — deciding what an event *means* is events.js's job.
 * `tone` is '', 'good', 'warn' or 'period'.
 */
export function timelineRow({ clock, text, sideLabel = '', tone = '' }) {
    const row = document.createElement('div');
    row.className = 'tl-row';
    row.innerHTML = `
        <span class="tl-clock"></span>
        <span class="tl-marker"><span class="tl-dot"></span></span>
        <span class="tl-text"></span>
        <span class="tl-side"></span>`;

    row.querySelector('.tl-clock').textContent = clock;
    row.querySelector('.tl-text').textContent = text;
    row.querySelector('.tl-side').textContent = sideLabel;
    if (tone) row.querySelector('.tl-dot').classList.add(tone);

    return row;
}

/** Yellow/red marks for a set of cards. Never a summed count — a yellow and a
 *  red mean completely different things. */
export function cardChips(yellowCards = 0, redCards = 0, labels = {}) {
    const chips = [
        ...Array(yellowCards).fill('yellow'),
        ...Array(redCards).fill('red'),
    ];
    return chips.map((colour) => {
        const chip = document.createElement('span');
        chip.className = `card-chip ${colour}`;
        chip.title = labels[colour]?.label ?? colour;
        return chip;
    });
}
