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

// How far past a mark the video can be and still be "at" it. Twenty-five
// seconds because a coach seeking to a goal lands a few seconds before the ball
// crosses the line and then watches the celebration, and a highlight that
// un-highlights itself halfway through the thing it points at is worse than no
// highlight at all. Nothing before the mark counts: the video has not got there
// yet, and lighting up a moment that has not happened gives it away.
const NOW_WINDOW_S = 25;

/**
 * Which mark the video is currently inside, or -1.
 *
 * Pure, and separate from the rendering, because the interesting cases are all
 * about arithmetic rather than about DOM: two marks a second apart, a position
 * before the first mark, a position long after the last one. `marks` is taken
 * in whatever order it was given and the index returned indexes that same
 * array, so a caller that sorted for display gets an answer about its own rows.
 */
export function nowIndex(marks, clockS, windowS = NOW_WINDOW_S) {
    if (typeof clockS !== 'number' || !Number.isFinite(clockS)) return -1;

    let best = -1;
    let bestAt = -Infinity;
    (marks || []).forEach((mark, i) => {
        const at = mark?.clockS;
        if (typeof at !== 'number') return;
        if (at > clockS || at < clockS - windowS) return;
        // The latest qualifying mark wins, so two events a second apart resolve
        // to the one the video has most recently passed.
        if (at >= bestAt) { best = i; bestAt = at; }
    });
    return best;
}

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
 *
 * Returns `{ setNow(clockS) }`. Pass a match-clock reading to move the playhead
 * and light up the mark the video is inside; pass null to take it away.
 */
export function renderStrip(host, { marks, endS, onSeek, halfS = 45 * 60, clockText }) {
    host.innerHTML = '';
    const end = endS || timelineEnd(marks);
    const ticks = [];

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
        ticks.push(button);
    }

    // Half-time, as a landmark. Not a button: there is nothing to seek to that
    // a viewer wants, and a focusable element with no action is noise.
    if (halfS && halfS < end) {
        const half = document.createElement('span');
        half.className = 'tick-half';
        half.style.left = `${Math.min(100, (halfS / end) * 100)}%`;
        host.append(half);
    }

    // Built on the first reading rather than up front, because `.scrubber:empty`
    // is what hides the bar on a match with nothing marked on it, and an always-
    // appended playhead would leave an empty rail on every such page.
    let head = null;
    let lit = null;

    return {
        setNow(clockS) {
            const off = clockS == null || !Number.isFinite(clockS);

            if (!off && !head) {
                head = document.createElement('span');
                head.className = 'tick-now';
                host.append(head);
            }
            if (head) {
                head.classList.toggle('hidden', off);
                if (!off) head.style.left = `${Math.min(100, Math.max(0, (clockS / end) * 100))}%`;
            }

            const at = off ? -1 : nowIndex(marks, clockS);
            const next = at >= 0 ? ticks[at] : null;
            if (next === lit) return;
            lit?.classList.remove('is-now');
            next?.classList.add('is-now');
            lit = next;
        },
    };
}

/**
 * Render the list under the strip. Same marks, in clock order.
 *
 * Returns `{ setNow(clockS) }` like the strip does, so the row the video is
 * inside can be picked out of a list of thirty that all look alike.
 */
export function renderMomentList(host, { marks, onSeek, emptyText, clockText }) {
    host.innerHTML = '';
    const buttons = [];

    const rows = [...(marks || [])].sort((a, b) => a.clockS - b.clockS);
    if (!rows.length) {
        const empty = document.createElement('div');
        empty.className = 'empty';
        empty.textContent = emptyText || 'Nothing here yet.';
        host.append(empty);
        return { setNow() {} };
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
        buttons.push(button);
    }

    let lit = null;
    return {
        setNow(clockS) {
            const at = clockS == null ? -1 : nowIndex(rows, clockS);
            const next = at >= 0 ? buttons[at] : null;
            if (next === lit) return;
            lit?.classList.remove('is-now');
            next?.classList.add('is-now');
            lit = next;
        },
    };
}
