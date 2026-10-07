// Movement, for every page.
//
// Five things, each of which says something rather than decorating:
//
//   - Blocks rise into place as they scroll into view, so a long page reads
//     as arriving in order rather than all at once.
//   - A figure counts up to its value the first time it is seen. A season's
//     numbers landing one by one is the moment a coach opens this page for.
//   - The underline under the active tab slides to the next one, so the eye
//     follows which section it is now in.
//   - The pitch markings behind a header lean toward the pointer.
//   - A card lights up under the pointer, so it is clear what a click lands on.
//
// None of it is needed. Without this script, or with reduced motion asked
// for, every page is complete and still: nothing is hidden until a script
// shows it, because this script is the only thing that hides anything, and it
// only does so once it has already set up what brings it back.
//
// The desk pages (landing, coach, player) call startMotion() from their own
// entry module. The touchline pages do not: a page used one-handed in the
// rain has no business moving under the thumb.

// Leaves, not sections: a card inside a block that is itself still rising
// would be animated twice over.
const REVEAL = [
    'section.block > h3', '.card', '.stat', '.list-item', '.team-card', '.flow-step',
    '.action-card', '.quick-link', '.status-row', '.match-card',
].join(',');
const COUNT = '.stat .value, .figure .n, .sample-stat b';
const SPOT = '.card, .team-card, .stat, .action-card, .quick-link, .list-item, .flow-step';

// Whole numbers, with an optional unit the count leaves alone: 78′, 99%, 41.
const NUMBER = /^(-?)(\d{1,4})(\D{0,2})$/;

/**
 * Run a figure up to its value. Stops the moment anything else writes to the
 * element — a page re-rendering the number mid-count is right, and the count
 * is not.
 */
function countUp(el, { win = globalThis, duration = 750 } = {}) {
    const m = NUMBER.exec(el.textContent.trim());
    if (!m) return false;
    const [, sign, digits, unit] = m;
    const target = Number(digits);
    if (target < 2) return false;
    const start = win.performance?.now?.() ?? Date.now();
    let written = el.textContent;
    const step = (now) => {
        if (el.textContent !== written) return;
        const t = Math.min(1, (now - start) / duration);
        const eased = 1 - (1 - t) ** 3;
        written = `${sign}${Math.round(target * eased)}${unit}`;
        el.textContent = written;
        if (t < 1) win.requestAnimationFrame(step);
    };
    written = `${sign}0${unit}`;
    el.textContent = written;
    win.requestAnimationFrame(step);
    return true;
}

/** Where the active tab is, as the custom properties the glider reads. */
function placeGlider(tabs) {
    const active = tabs.querySelector('.tab.active');
    if (!active) return;
    tabs.style.setProperty('--tab-x', `${active.offsetLeft}px`);
    tabs.style.setProperty('--tab-w', `${active.offsetWidth}px`);
    tabs.classList.add('has-glider');
}

export function startMotion(doc = globalThis.document, win = globalThis) {
    if (!doc?.documentElement || !win.matchMedia) return;
    if (win.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const finePointer = win.matchMedia('(hover: hover) and (pointer: fine)').matches;

    const seen = new WeakSet();
    const counted = new WeakSet();

    const io = win.IntersectionObserver && new win.IntersectionObserver((entries) => {
        for (const entry of entries) {
            if (!entry.isIntersecting) continue;
            const el = entry.target;
            io.unobserve(el);
            el.classList.add('is-in');
            for (const n of [el, ...el.querySelectorAll(COUNT)]) {
                if (!counted.has(n) && n.matches?.(COUNT)) {
                    counted.add(n);
                    countUp(n, { win });
                }
            }
        }
    }, { rootMargin: '0px 0px -6% 0px', threshold: 0.08 });

    // Siblings arriving together are staggered, so a grid fills left to right
    // instead of popping in as one slab.
    const enrol = (scope) => {
        if (!io) return;
        const found = [...(scope.matches?.(REVEAL) ? [scope] : []), ...scope.querySelectorAll(REVEAL)];
        let order = 0;
        let parent = null;
        for (const el of found) {
            if (seen.has(el) || el.closest('.hidden, .topbar, dialog')) continue;
            if (el.parentElement?.closest('.reveal')) continue;
            seen.add(el);
            order = el.parentElement === parent ? order + 1 : 0;
            parent = el.parentElement;
            el.style.setProperty('--reveal-i', String(Math.min(order, 8)));
            el.classList.add('reveal');
            io.observe(el);
        }
        for (const tabs of scope.querySelectorAll?.('.tabs') ?? []) placeGlider(tabs);
    };

    enrol(doc.body);

    // Pages render most of what they show after the data arrives, and show
    // views by removing `.hidden`, so both are watched. Batched to a frame so
    // a list of forty rows is enrolled once, not forty times.
    let pending = false;
    const flush = () => {
        pending = false;
        enrol(doc.body);
        for (const tabs of doc.querySelectorAll('.tabs')) placeGlider(tabs);
    };
    if (win.MutationObserver) {
        new win.MutationObserver(() => {
            if (pending) return;
            pending = true;
            win.requestAnimationFrame(flush);
        }).observe(doc.body, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    }
    win.addEventListener?.('resize', () => {
        for (const tabs of doc.querySelectorAll('.tabs')) placeGlider(tabs);
    });

    if (!finePointer) return;

    // The pointer, as fractions: -1..1 across a header for the lean, pixels
    // within a card for the light.
    doc.addEventListener('pointermove', (event) => {
        const target = event.target;
        if (!target?.closest) return;
        const header = target.closest('.has-backdrop');
        if (header) {
            const r = header.getBoundingClientRect();
            header.style.setProperty('--px', (((event.clientX - r.left) / r.width) * 2 - 1).toFixed(3));
            header.style.setProperty('--py', (((event.clientY - r.top) / r.height) * 2 - 1).toFixed(3));
        }
        const card = target.closest(SPOT);
        if (card) {
            const r = card.getBoundingClientRect();
            card.style.setProperty('--mx', `${Math.round(event.clientX - r.left)}px`);
            card.style.setProperty('--my', `${Math.round(event.clientY - r.top)}px`);
        }
    }, { passive: true });
}
