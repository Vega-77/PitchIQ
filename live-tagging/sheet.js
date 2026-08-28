// The bottom sheets on the tagging page, and where the keyboard goes.
//
// Five overlays cover the screen while they are open — event, clock, sub, exit,
// log — and until now each one was opened and closed by hand at thirteen
// different call sites, all of them `classList.add('open')`. That works for a
// thumb and for nothing else:
//
//   * A screen reader was never told a dialog had appeared. The overlay is a
//     plain `<div>` painted over the page, so the words behind it stayed in the
//     reading order and the words in front of it were just more page.
//   * The keyboard was never moved into the sheet, so Tab carried on through
//     the buttons underneath it — buttons a sighted user cannot see and cannot
//     reach, because the overlay is in the way.
//   * Escape did nothing. Every other sheet on the web closes on Escape, and a
//     tagger who has opened the wrong one has to find the small Close button.
//
// The markup half of the fix is in `index.html`: `role="dialog"`,
// `aria-labelledby` pointing at the sheet's own heading, and `tabindex="-1"` so
// the sheet itself can hold focus. This file is the behaviour half.
//
// Focus lands on the sheet, not on the first control inside it. On a tablet at
// the side of a pitch, focusing an input opens the on-screen keyboard over the
// very sheet it belongs to; focusing the container announces the dialog and its
// heading and leaves the screen alone.

import { byId } from '../assets/ui.js?v=111';

const KEEP = 'a[href], button, input, select, textarea, [tabindex]';

/** The sheets currently open, oldest first. Only ever one in practice. */
const stack = [];

const sheetOf = (overlay) => overlay.querySelector('.sheet');

/** Everything inside the sheet a Tab could land on right now. */
function tabbable(sheet) {
    return [...sheet.querySelectorAll(KEEP)].filter(
        (el) => !el.disabled
            && el.getAttribute('tabindex') !== '-1'
            && el.offsetParent !== null,
    );
}

/**
 * Hold Tab inside the open sheet.
 *
 * `aria-modal` tells a screen reader to ignore everything outside the dialog,
 * which is only honest if the keyboard cannot get out there either. The two go
 * together or neither should be there at all.
 */
function trap(event) {
    if (event.key !== 'Tab' || !stack.length) return;
    const sheet = sheetOf(stack[stack.length - 1].overlay);
    if (!sheet) return;
    const stops = tabbable(sheet);
    if (!stops.length) {
        event.preventDefault();
        sheet.focus();
        return;
    }
    const first = stops[0];
    const last = stops[stops.length - 1];
    const here = document.activeElement;
    if (event.shiftKey && (here === first || here === sheet)) {
        event.preventDefault();
        last.focus();
    } else if (!event.shiftKey && here === last) {
        event.preventDefault();
        first.focus();
    }
}

function onKey(event) {
    if (event.key === 'Escape') {
        if (closeTop()) event.preventDefault();
        return;
    }
    trap(event);
}

/** Open one sheet by overlay id. Safe to call when it is already open. */
export function openSheet(id) {
    const overlay = byId(id);
    if (!overlay || stack.some((entry) => entry.overlay === overlay)) return;
    stack.push({ overlay, from: document.activeElement });
    overlay.classList.add('open');
    const sheet = sheetOf(overlay);
    if (sheet) sheet.focus({ preventScroll: true });
}

/**
 * Close one sheet by overlay id, putting the keyboard back where it was.
 *
 * Called from every path that used to remove the class directly, including the
 * ones that close a sheet as a side effect of saving — a tagger who tapped
 * "Goal" and got the sheet dismissed under them should find the caret back on
 * the button they pressed, not at the top of the document.
 */
export function closeSheet(id) {
    const overlay = byId(id);
    if (!overlay) return;
    overlay.classList.remove('open');
    const at = stack.findIndex((entry) => entry.overlay === overlay);
    if (at < 0) return;
    const [entry] = stack.splice(at, 1);
    const back = entry.from;
    if (back && back.isConnected && typeof back.focus === 'function') {
        back.focus({ preventScroll: true });
    }
}

/** Close the topmost open sheet. True when there was one. */
function closeTop() {
    if (!stack.length) return false;
    closeSheet(stack[stack.length - 1].overlay.id);
    return true;
}

/** Wire the one document-level listener. Call once, at start-up. */
export function mountSheets() {
    document.addEventListener('keydown', onKey);
}
