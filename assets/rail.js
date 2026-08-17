// The column beside a report.
//
// Two things at once, and they are deliberately the same column rather than two:
//
//   * **Facts.** A short list of the figures a reader wants without going to
//     find them — the scoreline, how many minutes, how much of the work is
//     still outstanding. They stay put while the middle of the page changes, so
//     a reader switching sections never loses the frame the section sits in.
//   * **Sections.** One block of the report at a time, loaded in rather than
//     scrolled to. A match report is fifteen blocks and roughly ten screens
//     stacked; a player's season is four. Either way the page should be the
//     height of what is being read.
//
// This began inside coach.js and moved out when the player portal wanted the
// same thing. The two pages had drifted once already — the coach's report grew
// a desktop layout and the player's did not, so the same season was two shapes
// depending on who opened it — and one rail is the way that stops recurring.
//
// Desktop only, and the gate is in CSS (`.section-rail`, `@media (min-width:
// 1180px)`) rather than here: below the breakpoint the page order *is* the
// navigation, and a phone showing one section with no way to reach the others
// would be a page with most of itself missing. The same rule keeps every
// section on the paper when the report is printed.

import { railTarget } from './report.js?v=85';

/**
 * Wire a rail to a report body.
 *
 * `facts` and `counts` are called on every render rather than once, because
 * both change as a coach works — naming a tracked figure moves a count, and
 * confirming an event moves two.
 *
 * Returns a handle: `render()` after anything that adds or removes a block,
 * `show(id)` to move, `section` for the one being read.
 */
export function mountRail({
    body, rail, heading = 'This report', facts = () => [], counts = () => ({}),
}) {
    if (!body || !rail) return { render() {}, show() {}, get section() { return null; } };

    let section = null;

    const RAILED = 'section.block[data-rail], section.block[data-rail-group]';
    const groupOf = (block) => block.dataset.railGroup || block.id;

    /**
     * The rail's entries: one per group, in page order.
     *
     * Most entries are one block. A few are two, because two blocks genuinely
     * are one thing to read — a heatmap and a shot map of the same match are
     * both pictures of the same pitch, and each caps its own width, so showing
     * one alone leaves half a monitor empty beside it while the other sits
     * hidden. A group is declared in the markup with `data-rail-group`; the
     * label comes from whichever block in it carries `data-rail`.
     */
    function entries() {
        const found = new Map();
        for (const block of body.querySelectorAll(RAILED)) {
            if (block.classList.contains('hidden')) continue;
            const id = groupOf(block);
            const entry = found.get(id);
            if (entry) {
                entry.blocks.push(block);
                entry.label ||= block.dataset.rail;
                continue;
            }
            found.set(id, { id, label: block.dataset.rail, blocks: [block] });
        }
        return [...found.values()];
    }

    function show(id) {
        const available = entries();
        if (!available.length) return;

        section = railTarget(available.map((entry) => entry.id), id);

        body.classList.add('is-sectioned');
        for (const block of body.querySelectorAll(RAILED)) {
            block.classList.toggle('is-showing', groupOf(block) === section);
        }
        // A stack of short blocks is one grid cell holding two sections, so it
        // has to show whenever either of them is the one being read.
        for (const stack of body.querySelectorAll('.block-stack')) {
            stack.classList.toggle(
                'is-showing',
                [...stack.querySelectorAll(RAILED)]
                    .some((block) => groupOf(block) === section),
            );
        }

        for (const link of rail.querySelectorAll('.rail-link')) {
            const here = link.dataset.target === section;
            link.classList.toggle('is-here', here);
            // The rail is a set of alternatives with one chosen, which is what
            // aria-current says and what a tab list would be read as. Announced
            // so a screen reader gets the same "you are here" the accent bar
            // gives.
            if (here) link.setAttribute('aria-current', 'true');
            else link.removeAttribute('aria-current');
        }
    }

    /**
     * Arrow keys walk the rail.
     *
     * A rail is a list of alternatives, and every other list of alternatives on
     * a screen — a radio group, a tab strip — moves with the arrows. Without
     * this a keyboard reader tabs through eight buttons to reach the ninth,
     * which is the reason tab strips stopped being made of tab stops.
     */
    function onKey(event) {
        const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[event.key];
        const links = [...rail.querySelectorAll('.rail-link')];
        const at = links.indexOf(event.target);
        if (at < 0) return;

        let next = null;
        if (step) next = links[(at + step + links.length) % links.length];
        else if (event.key === 'Home') next = links[0];
        else if (event.key === 'End') next = links[links.length - 1];
        if (!next) return;

        event.preventDefault();
        next.focus();
        show(next.dataset.target);
    }

    function render() {
        const available = entries();
        const signature = available.map((entry) => entry.id).join('|');

        // Rebuilt only when the set of sections changes, not on every save. The
        // badges tick over constantly as a coach names figures, and throwing the
        // buttons away each time would take the keyboard focus with them —
        // which is the same bug the "same figure" strip already had to fix once.
        if (rail.dataset.signature !== signature) {
            rail.dataset.signature = signature;
            rail.innerHTML = '';
            rail.append(factsBox());

            const head = document.createElement('div');
            head.className = 'rail-head';
            head.textContent = heading;
            rail.append(head);

            const list = document.createElement('div');
            list.className = 'rail-links';
            list.addEventListener('keydown', onKey);
            for (const entry of available) list.append(railLink(entry, show));
            rail.append(list);
        }

        drawFacts(rail.querySelector('.rail-facts'), facts());

        const tally = counts();
        for (const link of rail.querySelectorAll('.rail-link')) {
            const count = tally[link.dataset.target];
            const tag = link.querySelector('.rail-tag');
            tag.textContent = count ? count.n : '';
            tag.hidden = !count;
            // On the button rather than the badge: the badge is 20px of pill and
            // a tooltip you have to hit it exactly to see is not a tooltip.
            if (count) link.title = count.title;
            else link.removeAttribute('title');
        }

        show(section);
    }

    return {
        render,
        show,
        /**
         * Forget where the reader was.
         *
         * For opening a different subject in the same markup — another match,
         * another player. Someone deep in one game's review who opens the next
         * one should land where every report opens, not on whichever section
         * the last one left behind. Deliberately not a `show()` call: the
         * blocks for the new subject have usually not been drawn yet, so there
         * is nothing to choose between until the next `render()`.
         */
        reset() { section = null; },
        get section() { return section; },
    };
}

function railLink(entry, show) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'rail-link';
    button.dataset.target = entry.id;

    const label = document.createElement('span');
    label.className = 'rail-label';
    label.textContent = entry.label;
    const tag = document.createElement('span');
    tag.className = 'rail-tag';
    tag.hidden = true;
    button.append(label, tag);

    button.addEventListener('click', () => show(entry.id));

    return button;
}

function factsBox() {
    const box = document.createElement('div');
    box.className = 'rail-facts';
    return box;
}

/**
 * The standing figures at the top of the rail.
 *
 * A fact is `{ label, value, note, tone }`. `value` is already formatted —
 * this draws, it does not decide — and a null one is drawn as an em dash rather
 * than dropped, because a fact that vanishes when it is unknown makes the rail
 * a different length on every match.
 */
function drawFacts(host, facts) {
    if (!host) return;
    host.innerHTML = '';
    host.hidden = !facts?.length;
    if (!facts?.length) return;

    for (const fact of facts) {
        if (!fact) continue;
        const row = document.createElement('div');
        row.className = `rail-fact ${fact.tone || ''}`.trim();

        const label = document.createElement('span');
        label.className = 'rail-fact-k';
        label.textContent = fact.label;

        const value = document.createElement('span');
        value.className = 'rail-fact-v';
        value.textContent = fact.value ?? '—';

        row.append(label, value);

        if (fact.note) {
            const note = document.createElement('span');
            note.className = 'rail-fact-n';
            note.textContent = fact.note;
            row.append(note);
        }

        host.append(row);
    }
}
