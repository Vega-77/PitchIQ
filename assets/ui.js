// Small DOM helpers shared by every page.
//
// Each of these existed as a near-identical private copy in landing.js,
// coach.js, player.js, calibrate.js and live-tagging — five toast functions,
// five `$` shorthands, three "big number over a small label" builders. Having
// one copy means a change to how the app talks (or looks) happens once.

import { comparePair, verdict, COUNT } from './report.js?v=63';

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

/**
 * The heading over one kind of statistic.
 *
 * Its own element rather than a bare `<h4>` because both shapes of stat list
 * need it — the coach's grid of boxes and the half-time page's column of
 * us-against-them bars — and they should not drift into looking like two
 * different ideas. `note` is for what the figures underneath are a share *of*;
 * putting that once above six boxes beats repeating it in six labels.
 */
export function groupHead(title, note = '') {
    const head = document.createElement('div');
    head.className = 'group-head';
    head.innerHTML = '<h4></h4><p class="group-note"></p>';
    head.querySelector('h4').textContent = title;

    const caption = head.querySelector('.group-note');
    if (note) caption.textContent = note;
    else caption.remove();

    return head;
}

/**
 * One titled block of stat boxes, from a group out of `groupStats`.
 *
 * Anything carrying a confidence mark is muted unless the caller says
 * otherwise: a figure a machine estimated should not sit in the same visual
 * register as a goal somebody pressed a button for, and the mark alone is easy
 * to miss at arm's length.
 */
/**
 * A titled group of figures, as bars where there are two sides and cards where
 * there is one.
 *
 * A row carrying a `kind` is a comparison — the pipeline measured the same
 * thing for both teams — and gets a bar, because the interesting fact about 58%
 * possession is their 42%. A row without one is ours alone (goals we scored,
 * fouls we conceded) and stays a card.
 *
 * The two forms are deliberately different shapes rather than the same box with
 * an extra number in it: a reader should be able to tell at a glance which
 * figures have something to be compared against.
 */
export function statGroup({ title, note = '', rows = [] }) {
    const section = document.createElement('section');
    section.className = 'stat-group';
    section.append(groupHead(title, note));

    const cards = rows.filter((row) => !row.kind);
    const bars = rows.filter((row) => row.kind);

    if (cards.length) {
        const grid = document.createElement('div');
        grid.className = 'stat-grid';
        for (const row of cards) {
            grid.append(statCard(
                row.value,
                row.label,
                row.tone ?? (row.confidence ? 'is-muted' : ''),
                row.confidence,
            ));
        }
        section.append(grid);
    }

    if (bars.length) {
        const list = document.createElement('div');
        list.className = 'tally-list';
        for (const row of bars) {
            list.append(tally(row.label, row.usN, row.themN, row.better, row.confidence, {
                kind: row.kind,
                // The already-formatted figures — '58%', '1.42', '38m'. The raw
                // numbers drive the bar and these drive the text, so a bar can
                // never disagree with the number printed beside it.
                usText: row.value,
                themText: row.themValue,
            }));
        }
        section.append(list);
    }

    return section;
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
 * One paired us/them row, drawn as the kind of comparison it actually is.
 *
 * Shared by the half-time view and the coach's match view. The bar matters more
 * than the numbers: at arm's length across a changing room, the gap is visible
 * before either figure is read. Which is exactly why its geometry has to be
 * right — a bar that is easy to read at a glance is a bar that is believed at a
 * glance.
 *
 * Two geometries, chosen by `kind` (see `comparePair` in report.js):
 *
 *   split    — the boundary moves and the track is always full. Correct when
 *              their figure is the rest of ours: possession of the ball, and
 *              counts, where the total is however many there were.
 *   opposed  — each side grows from a fixed centre against its own full of
 *              100%. For a rate, where the two are shares of different
 *              denominators and do not add up at all.
 *
 * Every row was drawn as a split until now, which is how 84% pass accuracy
 * against 71% has been showing as a near dead heat.
 *
 * A count whose lead is smaller than chance would hand out is drawn hollow —
 * see `insideNoise`. Three shots to one is three quarters of the shots and it
 * is also four shots, and the bar is the part a coach reads first.
 *
 * `better` says which direction is good ('high' or 'low'), so fouls conceded
 * and corners won can sit in the same list without one of them being coloured
 * backwards. Null leaves both sides uncoloured, which is the honest answer for
 * a figure like compactness that is not good or bad on its own.
 */
export function tally(label, ours, theirs, better = 'high', confidence = null,
                      options = {}) {
    const { kind = COUNT, usText = null, themText = null } = options;

    const row = document.createElement('div');
    row.className = 'tally';
    row.innerHTML = `
        <span class="t-us num"></span>
        <div class="t-bar">
            <span class="t-half us"><i></i></span>
            <span class="t-half them"><i></i></span>
        </div>
        <span class="t-them num"></span>
        <div class="t-label"></div>`;

    // An em dash rather than a zero for a figure nobody measured. This is the
    // one place the two sides can disagree about whether they exist: the
    // opposition's copy is missing from every report published before both
    // sides were carried.
    const text = (raw, formatted) => (
        formatted != null ? formatted : (raw == null ? '—' : String(raw))
    );
    row.querySelector('.t-us').textContent = text(ours, usText);
    row.querySelector('.t-them').textContent = text(theirs, themText);

    const caption = row.querySelector('.t-label');
    caption.textContent = label;
    if (confidence) caption.append(confidenceMark(confidence));

    const bars = comparePair(ours, theirs, kind);
    const bar = row.querySelector('.t-bar');
    if (!bars) {
        // Nothing honest to draw. The numbers stay; the bar goes, rather than
        // showing a dead heat or an empty track that reads as two zeroes.
        bar.remove();
        row.classList.add('is-flat');
        return row;
    }

    bar.classList.add(`is-${bars.mode}`);
    // Hollow rather than hidden. The split is still the best estimate and
    // removing it would be its own kind of lie; what it must not do is look as
    // solid as a lead that was actually measured.
    row.classList.toggle('is-tentative', Boolean(bars.tentative));
    const [us, them] = bar.querySelectorAll('.t-half');
    if (bars.mode === 'split') {
        // The halves themselves are sized, and each fill is the whole of its
        // half — that is what makes the boundary move.
        us.style.flexGrow = String(bars.us);
        them.style.flexGrow = String(bars.them);
    } else {
        // Equal halves, each filled from the centre outward.
        us.querySelector('i').style.width = `${bars.us}%`;
        them.querySelector('i').style.width = `${bars.them}%`;
    }

    // Colour the side that is ahead where being ahead is good, and the side
    // that is ahead where it is not — but only where that has been earned. The
    // rule is in report.js, where it can be tested without a DOM.
    const called = verdict({
        ours, theirs, usText, themText, better, tentative: bars.tentative,
    });
    if (called) row.classList.add(called);

    return row;
}

/**
 * One track cut into named pieces, with a key underneath.
 *
 * The fourth shape of bar on these pages, and the first that is not about two
 * teams. `tally` draws a claim about a match; this draws the composition of a
 * single quantity — where a whole went, when the whole is the interesting thing
 * and the parts are what it turned out to be made of.
 *
 * Segments arrive already carrying their shares, because deciding what the
 * denominator is is a judgement and belongs in report.js where it can be tested
 * without a DOM. This only draws them.
 *
 * A segment below a couple of percent still gets a visible sliver rather than
 * being rounded out of existence: the key beside it names a real number of
 * seconds, and a key entry with no mark against it is worse than a mark that is
 * slightly too wide.
 */
export function stackBar(segments, options = {}) {
    const { label = '', tone = '' } = options;
    const parts = (segments || []).filter((part) => part && part.share > 0);

    const el = document.createElement('div');
    el.className = 'stack';
    if (tone) el.classList.add(tone);
    if (!parts.length) return el;

    const bar = document.createElement('div');
    bar.className = 's-bar';
    const key = document.createElement('ul');
    key.className = 's-key';

    for (const part of parts) {
        const seg = document.createElement('span');
        seg.className = `s-seg is-${part.key}`;
        seg.style.flexGrow = String(part.share);
        bar.append(seg);

        const item = document.createElement('li');
        const swatch = document.createElement('i');
        swatch.className = `is-${part.key}`;
        const text = document.createElement('span');
        text.textContent = part.label;
        const value = document.createElement('b');
        value.textContent = part.text ?? String(part.seconds ?? '');
        item.append(swatch, text, value);
        key.append(item);
    }

    if (label) {
        const caption = document.createElement('div');
        caption.className = 's-label';
        caption.textContent = label;
        el.append(caption);
    }
    el.append(bar, key);
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
export function timelineRow({ clock, text, sideLabel = '', tone = '', onSeek }) {
    // A button only when there is somewhere to go. A row that looks tappable
    // and does nothing is worse than a row that looks like text, and the
    // half-time view renders this list with no video behind it.
    const row = document.createElement(onSeek ? 'button' : 'div');
    row.className = onSeek ? 'tl-row is-seek' : 'tl-row';
    if (onSeek) {
        row.type = 'button';
        row.addEventListener('click', onSeek);
    }
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
