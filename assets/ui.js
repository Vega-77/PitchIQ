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
 *
 * `confidence` marks a figure the video pipeline estimated rather than one a
 * human tapped. Numbers from tagging and numbers from footage sit in the same
 * grids, and a coach has no way to tell them apart otherwise — a pass count
 * derived from a half-seen ball looks exactly like a goal somebody pressed a
 * button for.
 */
export function statCard(value, label, tone = '', confidence = null) {
    const el = document.createElement('div');
    el.className = `stat ${tone}`.trim();
    el.innerHTML = '<div class="value"></div><div class="label"></div>';
    el.querySelector('.value').textContent = value ?? 0;
    el.querySelector('.label').textContent = label;
    if (confidence) el.append(confidenceMark(confidence));
    return el;
}

/** Confidence levels, worst first, so a caller can compare them. */
export const CONFIDENCE_LEVELS = ['low', 'medium', 'high'];

const CONFIDENCE_TEXT = {
    high: 'Estimated from video. Good coverage — treat as reliable.',
    medium: 'Estimated from video. Patchy coverage — treat as indicative.',
    low: 'Estimated from video. Poor coverage — treat as a rough hint only.',
};

/**
 * The little bar that says "a machine worked this out".
 *
 * Three filled segments rather than a percentage: the underlying uncertainty is
 * not calibrated well enough to justify a number, and printing "67% confident"
 * would claim a precision this pipeline does not have.
 */
export function confidenceMark(level = 'low', note = '') {
    const filled = Math.max(1, CONFIDENCE_LEVELS.indexOf(level) + 1);
    const el = document.createElement('span');
    el.className = `cv-mark is-${level}`;
    el.title = note || CONFIDENCE_TEXT[level] || CONFIDENCE_TEXT.low;
    el.setAttribute('aria-label', el.title);
    for (let i = 0; i < 3; i += 1) {
        const pip = document.createElement('i');
        if (i < filled) pip.className = 'on';
        el.append(pip);
    }
    return el;
}

/**
 * One paired us/them row with a proportional bar.
 *
 * Shared by the halftime view and the coach's match view. The bar matters more
 * than the numbers: at arm's length across a changing room, the gap is visible
 * before either figure is read.
 *
 * `better` says which direction is good ('high' or 'low'), so fouls conceded
 * and corners won can sit in the same list without one of them being coloured
 * backwards.
 */
export function tally(label, ours = 0, theirs = 0, better = 'high', confidence = null) {
    const row = document.createElement('div');
    row.className = 'tally';
    row.innerHTML = `
        <span class="t-us num"></span>
        <div class="t-bar"><div class="t-fill us"></div><div class="t-fill them"></div></div>
        <span class="t-them num"></span>
        <div class="t-label"></div>`;

    row.querySelector('.t-us').textContent = ours;
    row.querySelector('.t-them').textContent = theirs;

    const caption = row.querySelector('.t-label');
    caption.textContent = label;
    if (confidence) caption.append(confidenceMark(confidence));

    const total = ours + theirs;
    const share = total ? (ours / total) * 100 : 50;
    row.querySelector('.t-fill.us').style.width = `${share}%`;
    row.querySelector('.t-fill.them').style.width = `${100 - share}%`;

    // Colour the side that is ahead on a count where being ahead is good, and
    // the side that is ahead on a count where it is not.
    if (total && ours !== theirs) {
        const weLead = ours > theirs;
        const goodForUs = better === 'high' ? weLead : !weLead;
        row.classList.add(goodForUs ? 'ours-good' : 'ours-bad');
    }

    return row;
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

// ---------------------------------------------------------------- charts

const SVG_NS = 'http://www.w3.org/2000/svg';

function svgEl(name, attrs = {}) {
    const node = document.createElementNS(SVG_NS, name);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
}

/**
 * Minutes played per match, oldest on the left.
 *
 * Lives here because two pages want the same picture: a player looking at their
 * own season, and a coach looking at one of theirs. A shape answers "are the
 * minutes climbing?" in a way a column of numbers does not, and bars from
 * matches with a goal or an assist are picked out so the good days are
 * findable without reading.
 *
 * Takes report documents — anything with minutesPlayed, goals, assists,
 * matchDate and opponentName. Drawn with a viewBox so one piece of markup
 * scales from a phone to a monitor with no redraw on resize.
 */
export function minutesChart(reports, { fullMatchMinutes = 90 } = {}) {
    const season = reports.slice().reverse();          // oldest first
    const involved = (r) => (r.goals || 0) + (r.assists || 0);
    const longest = Math.max(fullMatchMinutes, ...season.map((r) => r.minutesPlayed || 0));

    const W = 100;
    const H = 34;
    const gap = season.length > 1 ? 1.2 : 0;
    const barW = Math.max(1.2, (W - gap * (season.length - 1)) / season.length);

    const svg = svgEl('svg', {
        viewBox: `0 0 ${W} ${H}`,
        preserveAspectRatio: 'none',
        role: 'img',
        'aria-label': `Minutes played in each of ${season.length} matches`,
    });

    // A full-match reference line, so bar heights mean something absolute
    // rather than only relative to each other.
    const fullY = H - (fullMatchMinutes / longest) * H;
    svg.append(svgEl('line', {
        x1: 0, y1: fullY, x2: W, y2: fullY,
        class: 'chart-full-line',
        'vector-effect': 'non-scaling-stroke',
    }));

    season.forEach((report, i) => {
        const minutes = report.minutesPlayed || 0;
        const height = Math.max(0.6, (minutes / longest) * H);

        const bar = svgEl('rect', {
            x: i * (barW + gap),
            y: H - height,
            width: barW,
            height,
            rx: 0.5,
            class: involved(report) ? 'chart-bar scored' : 'chart-bar',
        });

        const label = svgEl('title');
        label.textContent =
            `${report.matchDate || ''} vs ${report.opponentName || '—'}: ${minutes}′`
            + (involved(report) ? `, ${involved(report)} G+A` : '');
        bar.append(label);

        svg.append(bar);
    });

    return svg;
}
