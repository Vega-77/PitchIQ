// The match viewer: the video with the top-down map beside it, the stats so far and the
// moments, all moving with the video (session step 3.9).
//
//   const viewer = mountViewer(host, {
//       index,                       // the export's index.json (pitchiq/viewer_data.py)
//       loadChunk: (id) => …,        // resolves a chunk document by id
//       mountVideo: (slot) => handle,// puts the video in `slot`; video.js's handle
//       nameOf: (id, team) => '…',   // optional: a figure's name
//   });
//   viewer.destroy();
//
// The data comes in through `loadChunk` so the coach page can read Firestore and the
// local preview (tools/viewer-preview.html) can read the export's files. The map is a
// canvas redrawn each animation frame while the video plays. With a file video it reads
// the element's own time, so the dots move smoothly. YouTube only reports its time a
// few times a second, so the map steps.
//
// Drawn as the 3D ball says: a solid ball on the ground, a ball in the air with its
// shadow and height, and a dashed ring where the ball was seen but the 3D can't vouch
// for it. Guessed dots (the map's, not seen on that frame) are faded by how sure the map is.

import {
    clock, decodeChunk, frameAt, halfAt, kindWord, prepareIndex, recentMoments,
    samplePosition, soFar, whyText, COUNTED, SEEN,
} from './viewer-data.js?v=156';
import { MARKINGS_M } from './pitch-backdrop.js?v=156';

const MARGIN_M = 3;
const DOT_M = 0.9;
const BALL_M = 0.55;
const LEAD_IN_S = 3; // a moment clicked plays from a little before it
const SHOW_MOMENT_S = 4; // how long a picked moment stays marked on the map
const STATS_EVERY_MS = 250;

const PITCH = '#2f6b45';
const LINES = 'rgba(255, 255, 255, 0.72)';
const INK = '#141820';

const STAT_ROWS = [
    ['Possession', null],
    ['Passes completed', 'pass'],
    ['Carries', 'carry'],
    ['Shots', 'shot'],
    ['Interceptions', 'interception'],
    ['Tackles', 'tackle'],
    ['Recoveries', 'recovery'],
    ['Duels', 'duel'],
];

function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
        if (k === 'class') node.className = v;
        else if (k === 'text') node.textContent = v;
        else node.setAttribute(k, v);
    }
    node.append(...children);
    return node;
}

// The legend's keys are small tiles of the map: the same green and the same marks the
// canvas draws, so they read the same on a light or a dark page.
const KEY_MARKS = {
    ball: `<circle cx="8" cy="8" r="3.2" fill="#fff" stroke="${INK}" stroke-width="1"/>`,
    air: '<ellipse cx="8" cy="12" rx="3" ry="1.3" fill="rgba(0, 0, 0, 0.35)"/>'
        + '<circle cx="8" cy="6.5" r="3" fill="#fff" stroke="#f59e0b" stroke-width="1.5"/>',
    unsure: '<circle cx="8" cy="8" r="4.2" fill="none" stroke="#fff" stroke-width="1.2" stroke-dasharray="2 1.6"/>',
    guess: `<circle cx="8" cy="8" r="4" fill="#d5d9de" opacity="0.45" stroke="${INK}" stroke-width="1"/>`,
};

function keyTile(kind) {
    return `<svg class="viewer-key" viewBox="0 0 16 16" aria-hidden="true">`
        + `<rect width="16" height="16" rx="3" fill="${PITCH}"/>${KEY_MARKS[kind]}</svg>`;
}

function defaultName(id, team) {
    if (team === 'a') return 'one of ours';
    if (team === 'b') return 'one of theirs';
    return 'a figure';
}

export function mountViewer(host, options) {
    const index = prepareIndex(options.index);
    const nameOf = options.nameOf || defaultName;
    const kits = { a: index.kits.team_a, b: index.kits.team_b };
    const names = options.teamNames || { a: 'Us', b: 'Them' };
    const L = index.pitch.length_m;
    const W = index.pitch.width_m;

    // ---------------------------------------------------------------- layout
    host.innerHTML = '';
    const videoSlot = el('div', { class: 'video-frame' });
    const canvas = el('canvas', { class: 'viewer-canvas', role: 'img', 'aria-label': 'Top-down map of the players and the ball' });
    const mapNote = el('p', { class: 'viewer-map-note', 'aria-live': 'polite' });
    const legend = el('p', { class: 'viewer-legend' });
    legend.innerHTML = ''
        + `<span><i class="viewer-swatch" style="background:${kits.a}"></i>${names.a}</span>`
        + `<span><i class="viewer-swatch" style="background:${kits.b}"></i>${names.b}</span>`
        + `<span>${keyTile('ball')}ball on the ground</span>`
        + `<span>${keyTile('air')}in the air</span>`
        + `<span>${keyTile('unsure')}seen, not sure where</span>`
        + `<span>${keyTile('guess')}map’s guess</span>`;
    const map = el('div', { class: 'viewer-map' }, canvas, mapNote);
    const clockLine = el('p', { class: 'viewer-clock' });
    const table = el('table', { class: 'viewer-sofar' });
    const head = el('tr', {}, el('th', { text: 'So far' }),
        el('th', {}, el('i', { class: 'viewer-swatch', style: `background:${kits.a}` }), names.a),
        el('th', {}, el('i', { class: 'viewer-swatch', style: `background:${kits.b}` }), names.b));
    table.append(el('thead', {}, head));
    const body = el('tbody');
    const cells = STAT_ROWS.map(([label]) => {
        const a = el('td');
        const b = el('td');
        body.append(el('tr', {}, el('th', { text: label, scope: 'row' }), a, b));
        return [a, b];
    });
    table.append(body);
    const strips = el('div', { class: 'viewer-strips' });
    const recent = el('ol', { class: 'viewer-recent', 'aria-label': 'Latest moments' });
    const why = el('div', { class: 'viewer-why', 'aria-live': 'polite' });
    const side = el('div', {}, clockLine, table, why);
    host.append(el('div', { class: 'viewer' },
        el('div', { class: 'viewer-top' }, videoSlot, map),
        legend,
        el('div', { class: 'viewer-bottom' }, el('div', {}, strips, recent), side)));

    const video = options.mountVideo(videoSlot);

    // ---------------------------------------------------------------- data
    const cache = new Map();
    function chunk(id) {
        if (!id) return null;
        const got = cache.get(id);
        if (got && !(got instanceof Promise)) return got;
        if (!got) {
            const p = Promise.resolve(options.loadChunk(id)).then((doc) => {
                cache.set(id, decodeChunk(doc));
                dirty = true;
            }).catch(() => cache.delete(id));
            cache.set(id, p);
        }
        return null;
    }

    // ---------------------------------------------------------------- time
    function now() {
        const v = video?.element;
        if (v && v.tagName === 'VIDEO') return v.currentTime;
        return video?.currentTime?.() ?? 0;
    }

    let picked = null; // the moment whose why is shown
    let pickedAt = -Infinity;

    function seek(s) {
        video?.seek?.(Math.max(0, s));
        dirty = true;
    }

    function pick(m, andSeek = true) {
        picked = m;
        pickedAt = m.t;
        showWhy(m);
        if (andSeek) seek(m.t - LEAD_IN_S);
    }

    // ---------------------------------------------------------------- timeline
    const halfStrips = index.halves.map((h, k) => {
        const t0 = h.f0 / h.fps;
        const t1 = h.f1 / h.fps;
        const strip = el('div', { class: 'viewer-strip', role: 'group', 'aria-label': `Half ${k + 1} moments` });
        const play = el('div', { class: 'viewer-playhead' });
        strip.append(el('span', { class: 'viewer-strip-label', text: `${k + 1}H` }), play);
        const frag = document.createDocumentFragment();
        for (const m of index.moments) {
            if (m.t < t0 || m.t > t1) continue;
            const tick = el('button', {
                class: `viewer-tick viewer-tick-${m.team}`,
                style: `left:${(100 * (m.t - t0)) / (t1 - t0)}%;background:${kits[m.team] || '#888'}`,
                title: `${clock(m.t)} ${kindWord(m.type)}`,
                'aria-label': `${clock(m.t)} ${kindWord(m.type)}, ${names[m.team] || ''}`,
                tabindex: '-1',
            });
            tick.addEventListener('click', (e) => { e.stopPropagation(); pick(m); });
            frag.append(tick);
        }
        strip.append(frag);
        strip.addEventListener('click', (e) => {
            const box = strip.getBoundingClientRect();
            seek(t0 + ((e.clientX - box.left) / box.width) * (t1 - t0));
        });
        strips.append(strip);
        return { strip, play, t0, t1 };
    });

    function drawTimeline(t) {
        for (const s of halfStrips) {
            const inside = t >= s.t0 && t <= s.t1;
            s.play.style.display = inside ? '' : 'none';
            if (inside) s.play.style.left = `${(100 * (t - s.t0)) / (s.t1 - s.t0)}%`;
        }
    }

    let recentKey = '';
    function drawRecent(t) {
        const list = recentMoments(index, t, 6);
        const key = list.map((m) => m.t).join(',');
        if (key === recentKey) return;
        recentKey = key;
        recent.innerHTML = '';
        for (const m of list) {
            const b = el('button', { class: 'viewer-moment' },
                el('span', { class: 'viewer-moment-time', text: clock(m.t) }),
                el('i', { class: 'viewer-swatch', style: `background:${kits[m.team] || '#888'}` }),
                el('span', { text: `${kindWord(m.type)}${m.out && m.out !== 'unknown' ? `, ${m.out}` : ''}` }));
            b.addEventListener('click', () => pick(m));
            recent.append(el('li', {}, b));
        }
        if (!picked && list[0]) showWhy(list[0]);
    }

    function showWhy(m) {
        why.innerHTML = '';
        why.append(el('h4', { text: `Why: ${kindWord(m.type)} at ${clock(m.t)}` }));
        for (const line of whyText(m, nameOf)) why.append(el('p', { text: line }));
    }

    // ---------------------------------------------------------------- so far
    let statsAt = -Infinity; // so the first frame fills the table, however soon after load
    function drawStats(t) {
        const s = soFar(index, t);
        STAT_ROWS.forEach(([, kind], r) => {
            const [a, b] = cells[r];
            if (kind === null) {
                a.textContent = s.possession ? `${Math.round(100 * s.possession.a)}%` : '–';
                b.textContent = s.possession ? `${Math.round(100 * s.possession.b)}%` : '–';
            } else if (kind === 'pass') {
                a.textContent = `${s.counts.a.completed} / ${s.counts.a.pass}`;
                b.textContent = `${s.counts.b.completed} / ${s.counts.b.pass}`;
            } else if (COUNTED.includes(kind)) {
                a.textContent = s.counts.a[kind];
                b.textContent = s.counts.b[kind];
            }
        });
    }

    // ---------------------------------------------------------------- map
    const ctx = canvas.getContext('2d');
    let scale = 1;
    function resize() {
        const width = map.clientWidth || 600;
        const ratio = window.devicePixelRatio || 1;
        const heightCss = (width * (W + 2 * MARGIN_M)) / (L + 2 * MARGIN_M);
        canvas.style.height = `${heightCss}px`;
        canvas.width = Math.round(width * ratio);
        canvas.height = Math.round(heightCss * ratio);
        scale = canvas.width / (L + 2 * MARGIN_M);
        dirty = true;
    }
    // x along the pitch; y across it, 0 on the camera's side, drawn at the bottom (as the
    // formation video draws it), so the map faces the way the video does.
    const px = (m) => (m + MARGIN_M) * scale;
    const py = (m) => (W - m + MARGIN_M) * scale;

    function drawPitch() {
        ctx.fillStyle = PITCH;
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.strokeStyle = LINES;
        ctx.lineWidth = Math.max(1, 0.12 * scale);
        const box = (x, y, w, h) => ctx.strokeRect(px(x), py(y + h), w * scale, h * scale);
        box(0, 0, L, W);
        ctx.beginPath();
        ctx.moveTo(px(L / 2), py(0));
        ctx.lineTo(px(L / 2), py(W));
        ctx.stroke();
        ctx.beginPath();
        ctx.arc(px(L / 2), py(W / 2), MARKINGS_M.centreCircleRadius * scale, 0, 2 * Math.PI);
        ctx.stroke();
        const pa = MARKINGS_M.penaltyAreaWidth;
        const ga = MARKINGS_M.goalAreaWidth;
        const gw = MARKINGS_M.goalWidth;
        for (const [x0, dir] of [[0, 1], [L, -1]]) {
            const pl = MARKINGS_M.penaltyAreaLength * dir;
            const gl = MARKINGS_M.goalAreaLength * dir;
            box(Math.min(x0, x0 + pl), (W - pa) / 2, Math.abs(pl), pa);
            box(Math.min(x0, x0 + gl), (W - ga) / 2, Math.abs(gl), ga);
            box(Math.min(x0, x0 - 1.5 * dir), (W - gw) / 2, 1.5, gw);
            ctx.beginPath();
            ctx.arc(px(x0 + MARKINGS_M.penaltySpot * dir), py(W / 2), 0.25 * scale, 0, 2 * Math.PI);
            ctx.fillStyle = LINES;
            ctx.fill();
        }
    }

    function dot(d, holder) {
        if (d.role === 'other') return;
        const x = px(d.x);
        const y = py(d.y);
        const r = (d.role === 'referee' ? 0.6 : DOT_M) * scale;
        ctx.globalAlpha = d.sure >= SEEN ? 1 : 0.2 + (0.5 * d.sure) / 14;
        if (holder) {
            ctx.beginPath();
            ctx.arc(x, y, r + 0.7 * scale, 0, 2 * Math.PI);
            ctx.fillStyle = 'rgba(255, 255, 255, 0.55)';
            ctx.fill();
        }
        ctx.beginPath();
        if (d.role === 'keeper') ctx.rect(x - r, y - r, 2 * r, 2 * r);
        else ctx.arc(x, y, r, 0, 2 * Math.PI);
        ctx.fillStyle = d.role === 'referee' ? '#111' : (kits[d.team] || '#999');
        ctx.fill();
        ctx.lineWidth = Math.max(1, 0.15 * scale);
        ctx.strokeStyle = d.role === 'referee' ? '#fbbf24' : INK;
        ctx.stroke();
        ctx.globalAlpha = 1;
    }

    function ball(b) {
        if (b.x === null) return;
        const x = px(b.x);
        const y = py(b.y);
        const r = BALL_M * scale;
        ctx.lineWidth = Math.max(1, 0.12 * scale);
        if (b.state === 'unsure') {
            ctx.setLineDash([0.35 * scale, 0.3 * scale]);
            ctx.beginPath();
            ctx.arc(x, y, r * 1.4, 0, 2 * Math.PI);
            ctx.strokeStyle = '#fff';
            ctx.stroke();
            ctx.setLineDash([]);
            return;
        }
        if (b.state === 'air') {
            ctx.beginPath();
            ctx.ellipse(x, y, r * 1.2, r * 0.7, 0, 0, 2 * Math.PI);
            ctx.fillStyle = 'rgba(0, 0, 0, 0.35)';
            ctx.fill();
            const lift = Math.min(6, b.h) * 0.6 * scale;
            ctx.beginPath();
            ctx.arc(x, y - lift, r, 0, 2 * Math.PI);
            ctx.fillStyle = '#fff';
            ctx.fill();
            ctx.strokeStyle = '#f59e0b';
            ctx.lineWidth = Math.max(1.5, 0.2 * scale);
            ctx.stroke();
            ctx.font = `${Math.max(10, 1.6 * scale)}px Saira, system-ui, sans-serif`;
            ctx.fillStyle = '#fff';
            ctx.fillText(`${b.h.toFixed(1)} m`, x + r * 1.6, y - lift - r);
            return;
        }
        ctx.beginPath();
        ctx.arc(x, y, r, 0, 2 * Math.PI);
        ctx.fillStyle = '#fff';
        ctx.fill();
        ctx.strokeStyle = INK;
        ctx.stroke();
    }

    function drawPicked(t, frame) {
        if (!picked || Math.abs(t - pickedAt) > SHOW_MOMENT_S) return;
        if (picked.s && picked.e && picked.type === 'pass') {
            ctx.beginPath();
            ctx.moveTo(px(picked.s[0]), py(picked.s[1]));
            ctx.lineTo(px(picked.e[0]), py(picked.e[1]));
            ctx.strokeStyle = 'rgba(255, 255, 255, 0.8)';
            ctx.setLineDash([0.8 * scale, 0.5 * scale]);
            ctx.lineWidth = Math.max(1, 0.15 * scale);
            ctx.stroke();
            ctx.setLineDash([]);
        }
        const d = frame?.dots.find((x) => x.id === picked.id);
        if (d) {
            ctx.beginPath();
            ctx.arc(px(d.x), py(d.y), (DOT_M + 1.1) * scale, 0, 2 * Math.PI);
            ctx.strokeStyle = '#f59e0b';
            ctx.lineWidth = Math.max(1.5, 0.25 * scale);
            ctx.stroke();
        }
    }

    function frameFor(t) {
        const half = halfAt(index, t);
        if (!half) return { note: index.halves.length && t < index.halves[0].f0 / index.halves[0].fps ? 'Before kick-off' : 'Between or after the halves' };
        const p = samplePosition(half, t);
        if (!p) return { note: 'Between or after the halves' };
        const here = chunk(p.chunk);
        const nextId = half.chunks[p.k + 1];
        chunk(nextId); // fetched ahead, so the next five minutes don't wait
        if (!here) return { note: 'Loading the map…' };
        let next = null;
        let j = p.i + 1;
        if (j < here.samples) next = here;
        else {
            next = chunk(nextId);
            j = 0;
        }
        return { frame: frameAt(here, p.i, next, j, p.frac) };
    }

    // ---------------------------------------------------------------- loop
    let dirty = true;
    let last = -1;
    let raf = 0;
    function tick() {
        raf = requestAnimationFrame(tick);
        const t = now();
        if (t === last && !dirty) return;
        last = t;
        dirty = false;
        const { frame, note } = frameFor(t);
        drawPitch();
        if (frame) {
            const holder = frame.hold.id;
            for (const d of frame.dots) if (d.role === 'referee') dot(d, false);
            for (const d of frame.dots) if (d.role !== 'referee') dot(d, d.id === holder);
            ball(frame.ball);
            drawPicked(t, frame);
        }
        mapNote.textContent = note || (frame && frame.ball.state === 'none' ? 'Ball not seen' : '');
        mapNote.hidden = !mapNote.textContent;
        clockLine.textContent = `Video ${clock(t)}`;
        drawTimeline(t);
        const ms = performance.now();
        if (ms - statsAt > STATS_EVERY_MS) {
            statsAt = ms;
            drawStats(t);
            drawRecent(t);
        }
    }

    const observer = typeof ResizeObserver === 'function' ? new ResizeObserver(resize) : null;
    observer?.observe(map);
    resize();
    tick();

    return {
        video,
        seek,
        destroy() {
            cancelAnimationFrame(raf);
            observer?.disconnect();
            video?.destroy?.();
            host.innerHTML = '';
        },
    };
}
