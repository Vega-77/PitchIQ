// The match page's "Video processing" card: how long turning the video into
// stats takes, the button that asks for it, the call to pin the pitch, the
// progress bar while it runs, and "done".
//
// The run happens on the processing computer (pitchiq/cli/process_match.py),
// which writes its progress to the match; this listens to it. What the card
// says for each state is shared/run-view.js; this only draws it.
//
// One of the coach page's modules; `coach.js` loads them and wires the page up.

import { watchRun, requestRun, cancelRunRequest } from '../shared/db.js?v=155';
import { runView } from '../shared/run-view.js?v=155';
import { byId, toast } from '../shared/ui.js?v=155';
import { state } from './shell.js?v=155';

const HALF_NAME = { h1: 'first half', h2: 'second half' };
let steps = null;
let stop = null;
let last = null; // { matchId, request, progress, phase }

// Not stamped: the stamper versions imports and page attributes, not fetches,
// so this asks the server whether its copy is still current instead.
async function loadSteps() {
    if (!steps) {
        const res = await fetch('../shared/run-steps.json', { cache: 'no-cache' });
        steps = (await res.json()).steps;
    }
    return steps;
}

/** Start listening for this match; the card redraws on every change. */
export async function renderRun(matchId, onDone) {
    leaveRun();
    const block = byId('match-run');
    block.classList.add('hidden');
    try {
        await loadSteps();
    } catch {
        return; // no step list, no estimate: better no card than a wrong one
    }
    if (state.match?.id !== matchId) return;
    last = { matchId, request: null, progress: null, phase: null, onDone };
    stop = watchRun(state.team.id, matchId, ({ request, progress }) => {
        if (!last || last.matchId !== matchId) return;
        Object.assign(last, { request, progress });
        draw();
    }, () => block.classList.add('hidden'));
    // Between snapshots the "updated 3 min ago" line and the quiet-run warning
    // still have to move on.
    const tick = setInterval(() => last && draw(), 60000);
    const unwatch = stop;
    stop = () => { unwatch(); clearInterval(tick); };
}

export function leaveRun() {
    stop?.();
    stop = null;
    last = null;
}

/** The video link changed: the card's first state depends on it. */
export function redrawRun() {
    if (last) draw();
}

function draw() {
    const v = runView({
        steps,
        request: last.request,
        progress: last.progress,
        hasVideo: !!state.match?.videoUrl,
    });
    const was = last.phase;
    last.phase = v.phase;
    const card = byId('run-card');
    card.replaceChildren(...PHASES[v.phase](v).filter(Boolean));
    byId('match-run').classList.remove('hidden');
    // Seen finishing while the page was open: the report on it is the old one.
    if (v.phase === 'done' && was && was !== 'done') {
        toast('The video’s stats are in.');
        last.onDone?.();
    }
}

// ---------------------------------------------------------------- pieces

function el(tag, attrs = {}, ...kids) {
    const e = document.createElement(tag);
    for (const [k, val] of Object.entries(attrs)) {
        if (k === 'class') e.className = val;
        else if (k === 'text') e.textContent = val;
        else e.setAttribute(k, val);
    }
    e.append(...kids.filter((k) => k != null));
    return e;
}

function when(ms) {
    return new Date(ms).toLocaleString([], {
        weekday: 'short', day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit',
    });
}

function clockAt(ms) {
    return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

function bar(v) {
    const fill = el('span', { class: 'run-bar-fill' });
    fill.style.width = `${v.percent}%`;
    return el('div', {
        class: 'run-bar', role: 'progressbar', 'aria-label': 'Video processing',
        'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(v.percent),
    }, fill);
}

function stepList(v) {
    const items = v.list.map((s) => el('li', { class: 'run-step', 'data-status': s.status },
        el('span', { class: 'run-step-mark', 'aria-hidden': 'true',
            text: s.status === 'done' ? '✓' : s.status === 'now' ? '●' : '○' }),
        el('span', { text: s.label + (s.status === 'now' ? ' (now)' : '') })));
    return el('details', { class: 'run-all' },
        el('summary', { text: 'Every step' }), el('ol', { class: 'run-steps' }, ...items));
}

function updated(v) {
    if (v.updatedAgo == null) return '';
    const m = Math.round(v.updatedAgo);
    return m < 1 ? ' Updated just now.' : ` Updated ${m} min ago.`;
}

function pinLinks(halves) {
    return el('p', { class: 'run-actions' }, ...halves.map((half) => el('a', {
        class: 'btn small primary',
        href: `../calibrate/?team=${encodeURIComponent(state.team.id)}`
            + `&match=${encodeURIComponent(last.matchId)}&half=${half}`,
        text: `Pin the ${HALF_NAME[half]}`,
    })));
}

// ---------------------------------------------------------------- the states

const PHASES = {
    novideo: (v) => [
        el('p', { class: 'muted run-text', text:
            'Add the match video link (Match video, further down) and this is where you turn '
            + `it into stats: ${v.estimate.text} of processing.` }),
    ],

    offer: (v) => {
        const choice = (value, label) => el('label', {},
            el('input', { type: 'radio', name: 'run-ours', value }), ` ${label}`);
        const ours = el('fieldset', { class: 'run-ours' },
            el('legend', { text: 'Our shirts in this video are' }),
            choice('lighter', 'lighter than the other team’s'),
            choice('darker', 'darker than the other team’s'));
        const go = el('button', { class: 'btn primary', type: 'button', text: 'Process this match' });
        go.addEventListener('click', () => ask(ours, go));
        return [
            el('p', { class: 'run-text' },
                'Turning this video into stats takes ', el('strong', { text: v.estimate.text }),
                ' on the processing computer. About ', v.estimate.pinsAfter, ' in, you are asked '
                + 'to pin the pitch: a few minutes clicking landmarks on a picture of the field. '
                + 'The rest runs by itself, and this card shows how far it has got.'),
            ours,
            el('p', { class: 'run-actions' }, go),
        ];
    },

    requested: (v) => {
        const undo = el('button', { class: 'btn small ghost', type: 'button', text: 'Cancel request' });
        undo.addEventListener('click', async () => {
            undo.disabled = true;
            try {
                await cancelRunRequest(state.team.id, last.matchId);
            } catch {
                undo.disabled = false;
                toast('Could not cancel the request.', true);
            }
        });
        return [
            el('p', { class: 'run-text' },
                el('strong', { text: 'Asked for' }),
                v.askedAt ? ` on ${when(v.askedAt)}` : '',
                v.ours ? `, our shirts the ${v.ours}. ` : '. ',
                'It starts when the processing computer picks it up. For now that is done by hand, '
                + 'so it may not be straight away; this card shows the progress once it begins.'),
            el('p', { class: 'run-actions' }, undo),
        ];
    },

    running: (v) => [
        bar(v),
        el('p', { class: 'run-now' }, el('strong', { text: `${v.percent}%` }),
            v.step ? ` · Step ${v.step.n} of ${v.step.of}: ${v.step.label}` : '',
            v.step && v.stepPercent ? ` (${v.stepPercent}% of this step)` : ''),
        el('p', { class: 'muted run-text', text:
            (v.leftText ? `About ${v.leftText} left`
                + (v.etaAt ? `, done around ${clockAt(v.etaAt)}.` : '.') : '')
            + updated(v) }),
        v.stale ? el('p', { class: 'run-warn', text:
            `No word from the processing computer for ${Math.round(v.updatedAgo)} min. It may `
            + 'have stopped; when it is started again it carries on from this step.' }) : null,
        stepList(v),
    ],

    pins: (v) => {
        const halves = v.pinHalves.length ? v.pinHalves : ['h1'];
        const which = halves.map((h) => HALF_NAME[h]).join(' and the ');
        return [
            bar(v),
            el('p', { class: 'run-you' },
                el('strong', { text: 'Your turn: pin the pitch. ' }),
                `Processing has stitched the camera’s view of the ${which} into one picture and `
                + 'waits here for you to pin six or more landmarks on it (corners, the halfway '
                + 'line, the boxes). It carries on by itself once they are saved.'),
            v.message ? el('p', { class: 'run-warn', text:
                `The last pins saved did not fit: ${v.message}. Move or remove that pin and save `
                + 'again.' }) : null,
            pinLinks(halves),
            el('p', { class: 'muted run-text', text:
                v.leftText ? `About ${v.leftText} of processing after that.` : '' }),
            stepList(v),
        ];
    },

    stopped: (v) => [
        bar(v),
        el('p', { class: 'run-warn', text:
            `Processing stopped${v.step ? ` during “${v.step.label}”` : ''}. The processing `
            + 'computer has the details; when it is started again it carries on from this step.' }),
        stepList(v),
    ],

    done: (v) => [
        el('p', { class: 'run-text' }, el('strong', { text: 'Done' }),
            v.finishedAt ? ` on ${when(v.finishedAt)}` : '',
            '. The stats on this page come from the video.'),
    ],
};

async function ask(fieldset, button) {
    const ours = fieldset.querySelector('input:checked')?.value;
    if (!ours) {
        toast('Say which shirts are ours first: the video cannot tell.', true);
        return;
    }
    button.disabled = true;
    try {
        await requestRun(state.user, state.team.id, last.matchId, ours);
    } catch {
        button.disabled = false;
        toast('The request did not save. Are you signed in as this team’s coach?', true);
    }
}
