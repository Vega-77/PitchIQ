// A strip of the match with tappable marks on it, and the list underneath.
//
// Lifted out of player.js, which had it as three private functions wired to
// hardcoded element ids and to the shape of a playerReports document. Both
// pages that need it now want the same strip over different data: the portal
// marks a player's own touches, and the coach's review tool marks every
// candidate the pipeline produced.
//
// So the only thing these take is a list of marks:
//
//     { clockS, type, label, id? }
//
// `type` becomes a class — `is-goal`, `is-touch`, `is-pass` — which is how the
// colours are chosen; see `.tick` and `.moment` in app.css. `id` is optional
// and only the review tool uses it, to find the row a mark belongs to.
//
// No imports on purpose, same as report.js: this builds DOM and does nothing
// else, and a module that opens a Firestore connection at import time cannot be
// exercised without one.

// The strip is at least this long, so a nine-minute clip does not stretch to
// fill a full-match-width bar and imply the match was nine minutes.
const FLOOR_S = 90 * 60;

/** Where the strip should end: a full match, or later if anything ran past it. */
export function timelineEnd(marks, floorS = FLOOR_S) {
    let end = floorS;
    for (const mark of marks || []) {
        if (mark?.clockS > end) end = mark.clockS;
    }
    return end || floorS;
}

/**
 * Render the strip.
 *
 * Marks are real buttons rather than styled spans, so the whole thing is
 * reachable from a keyboard — on a phone these are four pixels wide and a tap
 * target is not always the answer.
 */
export function renderStrip(host, { marks, endS, onSeek, halfS = 45 * 60, clockText }) {
    host.innerHTML = '';
    const end = endS || timelineEnd(marks);

    for (const mark of marks || []) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `tick is-${mark.type}`;
        button.style.left = `${Math.min(100, (mark.clockS / end) * 100)}%`;
        button.title = `${clockText(mark.clockS)} — ${mark.label}`;
        button.setAttribute('aria-label', button.title);
        if (mark.id != null) button.dataset.markId = mark.id;
        button.addEventListener('click', () => onSeek?.(mark.clockS, mark));
        host.append(button);
    }

    // Half-time, as a landmark. Not a button: there is nothing to seek to that
    // a viewer wants, and a focusable element with no action is noise.
    if (halfS && halfS < end) {
        const half = document.createElement('span');
        half.className = 'tick-half';
        half.style.left = `${Math.min(100, (halfS / end) * 100)}%`;
        host.append(half);
    }
}

/** Render the list under the strip. Same marks, in clock order. */
export function renderMomentList(host, { marks, onSeek, emptyText, clockText }) {
    host.innerHTML = '';

    const rows = [...(marks || [])].sort((a, b) => a.clockS - b.clockS);
    if (!rows.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = emptyText || 'Nothing here yet.';
        host.append(empty);
        return;
    }

    for (const mark of rows) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = `moment is-${mark.type}`;
        button.innerHTML = '<span class="m-clock"></span><span class="m-label"></span>';
        button.querySelector('.m-clock').textContent = clockText(mark.clockS);
        // textContent, not innerHTML: a label can carry a player's name, and
        // names come from a coach typing into a form.
        button.querySelector('.m-label').textContent = mark.label;
        if (mark.id != null) button.dataset.markId = mark.id;
        button.addEventListener('click', () => onSeek?.(mark.clockS, mark));
        host.append(button);
    }
}
