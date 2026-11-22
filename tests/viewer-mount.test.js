// The match viewer mounted on a page (session step 3.9): the map faces the way the video
// does, the stats so far and the why fill in, and a picked moment plays from just before.
//
// Under tests/dom-shim.js, whose canvas records what was drawn rather than drawing it, so
// these ask where a dot went, never what it looks like.

import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { installDom } from './dom-shim.js';
import { mountViewer } from '../shared/match-viewer.js';

const b64 = (Type, values) => Buffer.from(new Type(values).buffer).toString('base64');
const code = (team, role, sure) => team | (role << 2) | (sure << 4);

// A 100 x 60 m pitch. Ours (id 3) on the near touchline, 2 m in, with the ball; theirs
// (id 7) 2 m from the far one. Two samples, the same both times.
function chunkDoc() {
    return {
        id: 'xy1_00', tag: 'xy1', k: 0, f0: 300, samples: 2,
        n: b64(Uint8Array, [2, 2]),
        x: b64(Int16Array, [200, 800, 200, 800]),
        y: b64(Int16Array, [20, 580, 20, 580]),
        id32: b64(Uint32Array, [3, 7, 3, 7]),
        code: b64(Uint8Array, [code(0, 0, 15), code(1, 0, 15), code(0, 0, 15), code(1, 0, 15)]),
        bx: b64(Int16Array, [205, 205]),
        by: b64(Int16Array, [25, 25]),
        bh: b64(Uint8Array, [0, 0]),
        bs: b64(Uint8Array, [1, 1]),
        ht: b64(Uint8Array, [0, 0]),
        hid: b64(Uint32Array, [3, 3]),
    };
}

function indexDoc() {
    return {
        hz: 5,
        kits: { team_a: '#d5d9de', team_b: '#b22222' },
        pitch: { length_m: 100, width_m: 60 },
        halves: [{ tag: 'xy1', period: 1, fps: 30, f0: 300, f1: 311, step: 6, per_chunk: 2, chunks: ['xy1_00'] }],
        possession: { bucket_s: 10, halves: [{ f0: 300, a: [1], b: [0] }] },
        moments: [{
            p: 1, t: 10, type: 'pass', team: 'a', id: 3, out: 'completed', s: [20, 2], e: [40, 10],
            len: 21.5, conf: 0.6, why: { ball: 'ground', d: 0.7, near: 7, near_team: 'b', near_d: 60 },
        }],
    };
}

let dom;
let host;
let video;

function fakeVideo() {
    const handle = {
        element: { tagName: 'VIDEO', currentTime: 10.1 },
        seeks: [],
        destroyed: false,
        seek(s) { this.seeks.push(s); this.element.currentTime = s; },
        destroy() { this.destroyed = true; },
    };
    return handle;
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

async function mounted() {
    const viewer = mountViewer(host, {
        index: indexDoc(),
        loadChunk: async () => chunkDoc(),
        mountVideo: () => (video = fakeVideo()),
    });
    await settle(); // the chunk arrives
    dom.window._frames.next();
    return viewer;
}

const canvasOf = () => host.querySelector('canvas');
const calls = (op) => canvasOf().getContext('2d').calls.filter((c) => c[0] === op);

beforeEach(() => {
    dom = installDom('<div id="host"></div>');
    host = dom.document.getElementById('host');
});

afterEach(() => dom.restore());

test('the map says it is loading until the first five minutes arrive', () => {
    mountViewer(host, {
        index: indexDoc(),
        loadChunk: () => new Promise(() => {}),
        mountVideo: () => fakeVideo(),
    });
    assert.equal(host.querySelector('.viewer-map-note').textContent, 'Loading the map…');
});

test('the near touchline is at the bottom of the map, as it is in the video', async () => {
    await mounted();
    const canvas = canvasOf();
    const scale = canvas.width / 106; // 100 m and a 3 m margin each side
    const dots = calls('arc').filter((c) => Math.abs(c[3] - 0.9 * scale) < 1e-6);
    assert.equal(dots.length, 2);
    const [ours, theirs] = dots[0][1] < dots[1][1] ? dots : [dots[1], dots[0]];
    assert.ok(ours[2] > 0.85 * canvas.height, `ours at y ${ours[2]} of ${canvas.height}`);
    assert.ok(theirs[2] < 0.15 * canvas.height, `theirs at y ${theirs[2]}`);
});

test('the stats so far and the latest moment\'s why fill in', async () => {
    await mounted();
    const table = host.querySelector('.viewer-sofar').textContent;
    assert.match(table, /Possession100%0%/);
    assert.match(table, /Passes completed1 \/ 10 \/ 0/);
    const why = host.querySelector('.viewer-why').textContent;
    assert.match(why, /Why: Pass at 0:10/);
    assert.match(why, /Pass by one of ours, completed, 22 m\./);
    assert.match(why, /the next nearest player, one of theirs, was 60 m away/);
});

test('a picked moment plays from three seconds before, its pass drawn up the map', async () => {
    const viewer = await mounted();
    const button = host.querySelector('.viewer-moment');
    assert.ok(button, 'the latest moment is offered');
    button.click();
    assert.deepEqual(video.seeks, [7]);
    video.element.currentTime = 10.1;
    canvasOf().getContext('2d').calls.length = 0;
    dom.window._frames.next();
    // From (20, 2) to (40, 10) m: on the canvas, 3 m of margin in and y counted from the
    // far touchline, so the pass runs right and up.
    const scale = canvasOf().width / 106;
    const at = (c, x, y) => Math.abs(c[1] - x * scale) < 1e-6 && Math.abs(c[2] - y * scale) < 1e-6;
    assert.ok(calls('moveTo').some((c) => at(c, 23, 61)), 'the pass starts at the passer');
    assert.ok(calls('lineTo').some((c) => at(c, 43, 53)), 'and ends where it was received');
    viewer.destroy();
    assert.equal(host.childNodes.length, 0);
    assert.equal(video.destroyed, true);
});
