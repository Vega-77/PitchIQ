// The calibrate page: pin landmarks on a picture of the pitch, save them for
// the calibrators.
//
// A fixed camera's frame and a panning camera's panorama take the same clicks.
// They differ in what can be checked here. A frame is one plane projection, so
// the page fits a homography as the pins go in and draws the pitch back over
// the picture. A panorama is stitched in angles, where a straight painted line
// is drawn curved, so no homography describes it; that solve happens in Python.
//
// The saved file carries the pins under both calibrators' names (see
// landmarks.js), so the same file feeds either:
//
//   points, image_size, pitch   `python -m pitchiq.experiments.calibrate <file>`
//   pins, w, h                  the pin solve, as `lc/<tag>/pins.json`
//
// Pins are kept in full-resolution image pixels. A draft is kept in this
// browser per picture, so a reload does not lose an hour of clicking.

import { byId, toast, showOnly } from '../assets/ui.js?v=137';
import { PITCH_LENGTH_M, PITCH_WIDTH_M } from '../assets/pitch-backdrop.js?v=137';
import { GROUPS, LANDMARKS, positions, lines } from './landmarks.js?v=137';
import { assess, apply, depth } from './homography.js?v=137';

const VIEWS = ['view-intro', 'view-work'];
const ENOUGH = 6;
// FOOTAGE_DAY.md §2: under half a metre is good, past one and a half is not worth trusting.
const GOOD_M = 0.5;
const POOR_M = 1.5;
// Drawn over the picture, not the page, so these answer to grass, sky and floodlight
// rather than to the theme: yellow pins, the active one a pink no pitch is painted.
const INK = { pin: '#ffe14d', active: '#ff4fa3', halo: 'rgba(10, 14, 10, 0.82)', outline: '#ffe14d' };

const state = {
    kind: 'fixed',
    file: null,            // {name, w, h}
    img: null,
    pins: {},              // pitchiq/pitch.py name -> [x, y] image px
    active: null,
    view: null,            // {s, x, y, fitted}: screen = (image - (x, y)) * s
    length: PITCH_LENGTH_M,
    width: PITCH_WIDTH_M,
    outline: true,
    fit: null,
    loaded: null,          // pins from a saved file, applied when the picture opens
};

const BY_NAME = Object.fromEntries(LANDMARKS.map((l) => [l.name, l]));
const BY_PIN = Object.fromEntries(LANDMARKS.filter((l) => l.pin).map((l) => [l.pin, l]));

// ---------------------------------------------------------------- the draft

const draftKey = () => state.file && `pitchiq.calibrate:${state.file.name}:${state.file.w}x${state.file.h}`;

function saveDraft() {
    try {
        localStorage.setItem(draftKey(), JSON.stringify({
            kind: state.kind, pins: state.pins, length: state.length, width: state.width,
        }));
    } catch { /* private window or storage off: the file save still works */ }
}

function loadDraft() {
    try {
        return JSON.parse(localStorage.getItem(draftKey()) || 'null');
    } catch {
        return null;
    }
}

// ---------------------------------------------------------------- the saved file

/** Pins from a file this page saved, or a bare `{"pins": ...}` from the pin solve. */
function pinsFromFile(data) {
    const out = {};
    for (const p of data.points || []) {
        if (BY_NAME[p.landmark]) out[p.landmark] = [Number(p.x), Number(p.y)];
    }
    for (const [name, xy] of Object.entries(data.pins || {})) {
        const lm = BY_PIN[name] || BY_NAME[name];
        if (lm && !out[lm.name]) out[lm.name] = [Number(xy[0]), Number(xy[1])];
    }
    return out;
}

function payload() {
    const placed = LANDMARKS.filter((l) => state.pins[l.name]);
    const data = {
        camera: state.kind,
        image: state.file.name,
        image_size: [state.file.w, state.file.h],
        points: placed.map((l) => ({ landmark: l.name, x: state.pins[l.name][0], y: state.pins[l.name][1] })),
        pins: Object.fromEntries(placed.filter((l) => l.pin).map((l) => [l.pin, state.pins[l.name]])),
        w: state.file.w,
        h: state.file.h,
        at: new Date().toISOString(),
    };
    // The pin solve measures the pitch itself; a fixed camera's homography needs it given.
    if (state.kind === 'fixed') data.pitch = { length_m: state.length, width_m: state.width };
    return data;
}

function download() {
    const name = state.kind === 'fixed' ? 'calibration-points.json' : 'pins.json';
    const blob = new Blob([JSON.stringify(payload(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast(`Saved ${name} to your downloads`);
}

// ---------------------------------------------------------------- the fit

function refit() {
    state.fit = null;
    if (state.kind !== 'fixed') return;
    const at = positions(state.length, state.width);
    const pins = LANDMARKS.filter((l) => state.pins[l.name])
        .map((l) => ({ name: l.name, px: state.pins[l.name], m: at[l.name] }));
    state.fit = assess(pins);
}

function verdict(m) {
    if (m <= GOOD_M) return ['good', 'Good'];
    if (m <= POOR_M) return ['warn', 'Usable, could be better'];
    return ['bad', 'Not worth trusting yet'];
}

function drawFit() {
    const box = byId('fit');
    box.replaceChildren();
    if (state.kind !== 'fixed') {
        box.textContent = 'A panorama is checked by the pin solve, not here.';
        return;
    }
    const f = state.fit;
    if (!f) {
        box.textContent = 'Place four pins and the fit appears here.';
        return;
    }
    const honest = f.looMean ?? f.mean;
    const [tone, word] = verdict(honest);
    const head = document.createElement('p');
    head.className = `cal-verdict ${tone}`;
    head.textContent = `${word}: ${honest.toFixed(2)} m ` + (f.looMean === null ? 'mean miss' : 'mean miss, each pin left out');
    box.appendChild(head);
    const worst = f.each.filter((e) => e.loo !== null).sort((a, b) => b.loo - a.loo)[0];
    const note = document.createElement('p');
    note.className = 'muted';
    if (f.looMean === null) {
        note.textContent = 'With five pins or more, each one is checked against the others.';
    } else if (worst && worst.loo > POOR_M && worst.loo > 2 * f.looMean) {
        note.textContent = `${BY_NAME[worst.name].group}: ${BY_NAME[worst.name].label} disagrees with the rest by ${worst.loo.toFixed(1)} m. Check that one first.`;
    } else {
        note.textContent = `Fitted miss ${f.mean.toFixed(2)} m mean, ${f.max.toFixed(2)} m worst.`;
    }
    box.appendChild(note);
}

// ---------------------------------------------------------------- the list

function placedCount() {
    return Object.keys(state.pins).length;
}

function nextUnplaced() {
    const order = LANDMARKS.filter((l) => l.suggested).concat(LANDMARKS.filter((l) => !l.suggested));
    const i = order.findIndex((l) => l.name === state.active);
    for (let k = 1; k <= order.length; k++) {
        const l = order[(i + k) % order.length];
        if (!state.pins[l.name]) return l.name;
    }
    return null;
}

function drawList() {
    const list = byId('list');
    list.replaceChildren();
    const errs = Object.fromEntries((state.fit?.each || []).map((e) => [e.name, e.loo ?? e.err]));
    for (const [group, rows] of GROUPS) {
        const h = document.createElement('h3');
        h.className = 'cal-group';
        h.textContent = group;
        list.appendChild(h);
        for (const [name, , label, suggested] of rows) {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'cal-lm' + (state.pins[name] ? ' set' : '');
            b.setAttribute('aria-pressed', String(name === state.active));
            const dot = document.createElement('span');
            dot.className = 'cal-dot';
            const text = document.createElement('span');
            text.className = 'grow';
            text.textContent = label;
            b.append(dot, text);
            if (errs[name] !== undefined && Number.isFinite(errs[name])) {
                const e = document.createElement('span');
                e.className = `cal-err ${verdict(errs[name])[0]}`;
                e.textContent = `${errs[name].toFixed(1)} m`;
                b.appendChild(e);
            } else if (suggested && !state.pins[name]) {
                const s = document.createElement('span');
                s.className = 'cal-tag';
                s.textContent = 'suggested';
                b.appendChild(s);
            }
            b.addEventListener('click', () => select(name, true));
            list.appendChild(b);
        }
    }
    const n = placedCount();
    byId('pin-count').textContent = `${n} pin${n === 1 ? '' : 's'}`;
    byId('btn-save').disabled = n < 4;
}

function drawPrompt() {
    const p = byId('prompt');
    p.replaceChildren();
    const n = placedCount();
    if (!state.active) {
        p.textContent = 'Pick a landmark from the list.';
        return;
    }
    const lm = BY_NAME[state.active];
    p.append('Click ');
    const b = document.createElement('strong');
    b.textContent = `${lm.group.toLowerCase()}: ${lm.label.toLowerCase()}`;
    p.appendChild(b);
    p.append(state.pins[state.active] ? ' (click again to move it).' : '.');
    if (n < ENOUGH) p.append(` ${ENOUGH - n} more for a sound fit.`);
}

function drawMini() {
    const svg = byId('mini');
    const L = state.length, W = state.width, pad = 4;
    svg.setAttribute('viewBox', `${-pad} ${-pad} ${L + 2 * pad} ${W + 2 * pad}`);
    const y = (v) => W - v;          // far touchline at the top, as the camera sees it
    let s = '';
    for (const line of lines(L, W)) {
        s += `<polyline class="cal-mini-line" points="${line.map(([u, v]) => `${u},${y(v)}`).join(' ')}"/>`;
    }
    const at = positions(L, W);
    for (const lm of LANDMARKS) {
        const [u, v] = at[lm.name];
        const cls = lm.name === state.active ? 'on' : state.pins[lm.name] ? 'set' : '';
        s += `<circle class="cal-mini-pt ${cls}" cx="${u}" cy="${y(v)}" r="${cls === 'on' ? 2.2 : 1.4}"/>`;
    }
    svg.innerHTML = s;
}

function select(name, zoomTo) {
    state.active = name;
    const p = state.pins[name];
    if (zoomTo && p && state.view) {
        const c = byId('canvas');
        state.view.s = Math.max(state.view.s, 0.6);
        state.view.x = p[0] - c.clientWidth / 2 / state.view.s;
        state.view.y = p[1] - c.clientHeight / 2 / state.view.s;
    }
    redraw();
}

function redraw() {
    refit();
    drawList();
    drawMini();
    drawPrompt();
    drawFit();
    draw();
}

// ---------------------------------------------------------------- the canvas

function fitView() {
    const c = byId('canvas');
    if (!state.file || !c.clientWidth || !c.clientHeight) {
        state.view = null;
        return;
    }
    const s = Math.min(c.clientWidth / state.file.w, c.clientHeight / state.file.h);
    state.view = {
        s,
        x: -(c.clientWidth / s - state.file.w) / 2,
        y: -(c.clientHeight / s - state.file.h) / 2,
        fitted: true,          // refit when the stage resizes, until zoomed or panned
    };
}

function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function drawOutline(ctx, v) {
    const f = state.fit;
    if (!f || !state.outline) return;
    const toScreen = (q) => [(q[0] - v.x) * v.s, (q[1] - v.y) * v.s];
    ctx.lineWidth = 2;
    ctx.strokeStyle = INK.outline;
    // The side of the horizon the pitch is on: the centre spot's.
    const front = depth(f.Hinv, positions(state.length, state.width).centre_spot) > 0;
    for (const line of lines(state.length, state.width)) {
        // Densify, and break the line wherever it crosses the horizon.
        ctx.beginPath();
        let pen = false;
        for (let i = 0; i < line.length; i++) {
            const a = line[i], b = line[i + 1] || a;
            const steps = i + 1 < line.length ? 12 : 1;
            for (let k = 0; k < steps; k++) {
                const t = k / steps;
                const m = [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
                const q = (depth(f.Hinv, m) > 0) === front && apply(f.Hinv, m);
                if (!q) {
                    pen = false;
                    continue;
                }
                const [x, y] = toScreen(q);
                if (pen) ctx.lineTo(x, y);
                else ctx.moveTo(x, y);
                pen = true;
            }
        }
        ctx.stroke();
    }
}

function draw() {
    const c = byId('canvas');
    const ctx = c.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = c.clientWidth, h = c.clientHeight;
    if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) {
        c.width = Math.round(w * dpr);
        c.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const v = state.view;
    if (!state.img || !v) return;
    ctx.imageSmoothingEnabled = v.s < 2;
    ctx.drawImage(state.img, -v.x * v.s, -v.y * v.s, state.file.w * v.s, state.file.h * v.s);
    drawOutline(ctx, v);
    ctx.font = `600 13px ${css('--font')}`;
    const { pin: pinInk, active: activeInk, halo } = INK;
    for (const lm of LANDMARKS) {
        const p = state.pins[lm.name];
        if (!p) continue;
        const x = (p[0] - v.x) * v.s, y = (p[1] - v.y) * v.s;
        const on = lm.name === state.active;
        const r = on ? 9 : 7;
        for (const [ink, width] of [[halo, 4], [on ? activeInk : pinInk, 2]]) {
            ctx.strokeStyle = ink;
            ctx.lineWidth = width;
            ctx.beginPath();
            ctx.arc(x, y, r, 0, 2 * Math.PI);
            ctx.moveTo(x - 14, y); ctx.lineTo(x - 4, y);
            ctx.moveTo(x + 4, y); ctx.lineTo(x + 14, y);
            ctx.moveTo(x, y - 14); ctx.lineTo(x, y - 4);
            ctx.moveTo(x, y + 4); ctx.lineTo(x, y + 14);
            ctx.stroke();
        }
        const label = lm.group === 'Halfway' ? lm.label : `${lm.group.split(' ')[0]} ${lm.label.toLowerCase()}`;
        const tw = ctx.measureText(label).width;
        // Right of the pin, unless that runs off the stage.
        const lx = x + 12 + tw + 10 > w ? x - 12 - tw - 10 : x + 12;
        ctx.fillStyle = halo;
        ctx.fillRect(lx, y - 24, tw + 10, 18);
        ctx.fillStyle = on ? activeInk : pinInk;
        ctx.fillText(label, lx + 5, y - 10);
    }
}

function toImage(ev) {
    const r = byId('canvas').getBoundingClientRect(), v = state.view;
    return [v.x + (ev.clientX - r.left) / v.s, v.y + (ev.clientY - r.top) / v.s];
}

function zoomAt(f, cx, cy) {
    const v = state.view;
    if (!v) return;
    const ix = v.x + cx / v.s, iy = v.y + cy / v.s;
    v.s = Math.min(8, Math.max(0.03, v.s * f));
    v.fitted = false;
    v.x = ix - cx / v.s;
    v.y = iy - cy / v.s;
    draw();
}

function place(p) {
    if (!state.active || p[0] < 0 || p[1] < 0 || p[0] >= state.file.w || p[1] >= state.file.h) return;
    // A click on top of another pin is almost always meant for that one: the list moved on.
    const clash = Object.entries(state.pins).find(([n, q]) =>
        n !== state.active && Math.hypot(q[0] - p[0], q[1] - p[1]) * state.view.s < 12);
    if (clash) {
        toast(`That spot already holds ${BY_NAME[clash[0]].label.toLowerCase()}. Pick it in the list to move it.`, true);
        return;
    }
    state.pins[state.active] = [Math.round(p[0] * 10) / 10, Math.round(p[1] * 10) / 10];
    saveDraft();
    state.active = nextUnplaced() || state.active;
    redraw();
}

function removeActive() {
    if (state.active && state.pins[state.active]) {
        delete state.pins[state.active];
        saveDraft();
        redraw();
    }
}

function wireCanvas() {
    const c = byId('canvas');
    const pts = new Map();
    let down = null, pinch = null;
    c.addEventListener('pointerdown', (e) => {
        if (!state.view) return;
        c.setPointerCapture(e.pointerId);
        pts.set(e.pointerId, [e.clientX, e.clientY]);
        if (pts.size === 1) down = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y, moved: false };
        if (pts.size === 2) {
            const [a, b] = [...pts.values()];
            pinch = { d: Math.hypot(a[0] - b[0], a[1] - b[1]) };
            down = null;
        }
    });
    c.addEventListener('pointermove', (e) => {
        if (!pts.has(e.pointerId)) return;
        pts.set(e.pointerId, [e.clientX, e.clientY]);
        if (pinch && pts.size === 2) {
            const [a, b] = [...pts.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
            const r = c.getBoundingClientRect();
            zoomAt(d / pinch.d, (a[0] + b[0]) / 2 - r.left, (a[1] + b[1]) / 2 - r.top);
            pinch.d = d;
            return;
        }
        if (!down) return;
        const dx = e.clientX - down.x, dy = e.clientY - down.y;
        if (Math.hypot(dx, dy) > 5) down.moved = true;
        if (down.moved) {
            state.view.x = down.vx - dx / state.view.s;
            state.view.y = down.vy - dy / state.view.s;
            state.view.fitted = false;
            draw();
        }
    });
    const up = (e) => {
        pts.delete(e.pointerId);
        if (pts.size < 2) pinch = null;
        if (down && !down.moved && e.type === 'pointerup') place(toImage(e));
        down = null;
    };
    c.addEventListener('pointerup', up);
    c.addEventListener('pointercancel', up);
    c.addEventListener('wheel', (e) => {
        e.preventDefault();
        const r = c.getBoundingClientRect();
        zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    new ResizeObserver(() => {
        if (!state.view || state.view.fitted) fitView();
        draw();
    }).observe(c);
}

// ---------------------------------------------------------------- setup

function setKind(kind) {
    state.kind = kind;
    const fixed = kind === 'fixed';
    byId('dims').classList.toggle('hidden', !fixed);
    byId('btn-outline').classList.toggle('hidden', !fixed);
}

function readDims() {
    const L = Number(byId('input-length').value), W = Number(byId('input-width').value);
    if (L > W && W > 0) {
        state.length = L;
        state.width = W;
        saveDraft();
        redraw();
    }
}

function open(file) {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.onload = () => {
        state.img = img;
        state.file = { name: file.name, w: img.naturalWidth, h: img.naturalHeight };
        const draft = loadDraft();
        if (state.loaded) {
            state.pins = state.loaded;
        } else if (draft && draft.kind === state.kind) {
            state.pins = draft.pins || {};
            if (draft.length > draft.width) {
                state.length = draft.length;
                state.width = draft.width;
            }
            if (placedCount()) toast(`Picked up the ${placedCount()} pins left on this picture last time`);
        }
        byId('input-length').value = state.length;
        byId('input-width').value = state.width;
        state.active = nextUnplaced();
        showOnly('view-work', VIEWS);
        fitView();
        redraw();
    };
    img.onerror = () => toast('That file did not open as a picture', true);
    img.src = url;
}

async function readPins(file) {
    try {
        const data = JSON.parse(await file.text());
        state.loaded = pinsFromFile(data);
        if (data.camera === 'fixed' || data.camera === 'panned') {
            byId('view-intro').querySelector(`input[value="${data.camera}"]`).checked = true;
            setKind(data.camera);
        }
        if (data.pitch?.length_m > data.pitch?.width_m) {
            state.length = Number(data.pitch.length_m);
            state.width = Number(data.pitch.width_m);
        }
        const n = Object.keys(state.loaded).length;
        toast(`${n} pins read. Now open the picture they were placed on.`);
    } catch {
        state.loaded = null;
        toast('That file is not a pins file', true);
    }
}

function start() {
    for (const r of document.querySelectorAll('input[name="kind"]')) {
        r.addEventListener('change', () => setKind(r.value));
    }
    byId('input-image').addEventListener('change', (e) => {
        const f = e.target.files[0];
        if (f) open(f);
    });
    byId('input-pins').addEventListener('change', (e) => {
        const f = e.target.files[0];
        if (f) readPins(f);
    });
    byId('input-length').addEventListener('change', readDims);
    byId('input-width').addEventListener('change', readDims);
    byId('btn-save').addEventListener('click', download);
    const c = byId('canvas');
    byId('btn-zoom-in').addEventListener('click', () => zoomAt(1.5, c.clientWidth / 2, c.clientHeight / 2));
    byId('btn-zoom-out').addEventListener('click', () => zoomAt(1 / 1.5, c.clientWidth / 2, c.clientHeight / 2));
    byId('btn-fit').addEventListener('click', () => {
        fitView();
        draw();
    });
    byId('btn-outline').addEventListener('click', (e) => {
        state.outline = !state.outline;
        e.currentTarget.setAttribute('aria-pressed', String(state.outline));
        draw();
    });
    byId('btn-remove').addEventListener('click', removeActive);
    document.addEventListener('keydown', (e) => {
        if (byId('view-work').classList.contains('hidden') || e.target.matches('input')) return;
        if (e.key === 'Delete' || e.key === 'Backspace') {
            e.preventDefault();
            removeActive();
        }
        if (e.key === 'Escape') {
            state.active = null;
            redraw();
        }
    });
    wireCanvas();
    setKind(document.querySelector('input[name="kind"]:checked').value);
}

start();
