// The match viewer's data (session step 3.9): decoding the export, where everyone is at a
// video time, and the stats so far.

import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
    decodeChunk, frameAt, halfAt, prepareIndex, recentMoments, samplePosition, soFar, whyText,
    NO_ID, NO_XY, clock,
} from '../shared/viewer-data.js';

const b64 = (Type, values) => Buffer.from(new Type(values).buffer).toString('base64');

// Two samples: ours (id 3) walks 1 m along, theirs (id 7) only in the first, a grey
// referee (id 11) standing. The ball rolls with ours, then goes up 2 m.
function chunk() {
    const code = (team, role, sure) => team | (role << 2) | (sure << 4);
    return {
        id: 'xy1_00', tag: 'xy1', k: 0, f0: 300, samples: 2,
        n: b64(Uint8Array, [3, 2]),
        x: b64(Int16Array, [100, 500, 400, 110, 400]),
        y: b64(Int16Array, [200, 300, 400, 200, 400]),
        id32: b64(Uint32Array, [3, 7, 11, 3, 11]),
        code: b64(Uint8Array, [code(0, 0, 15), code(1, 1, 7), code(2, 2, 15), code(0, 0, 15), code(2, 2, 15)]),
        bx: b64(Int16Array, [100, 110]),
        by: b64(Int16Array, [200, 200]),
        bh: b64(Uint8Array, [0, 20]),
        bs: b64(Uint8Array, [1, 2]),
        ht: b64(Uint8Array, [0, 255]),
        hid: b64(Uint32Array, [3, NO_ID]),
    };
}

function index() {
    return prepareIndex({
        hz: 5,
        halves: [
            { tag: 'xy1', fps: 30, f0: 300, f1: 359, step: 6, per_chunk: 2, chunks: ['xy1_00'] },
            { tag: 'xy2', fps: 30, f0: 900, f1: 1199, step: 6, per_chunk: 2, chunks: ['xy2_00'] },
        ],
        possession: {
            bucket_s: 10,
            halves: [{ f0: 300, a: [200, 100], b: [100, 100] }, { f0: 900, a: [], b: [] }],
        },
        moments: [
            { t: 10.5, type: 'pass', team: 'a', id: 3, out: 'completed' },
            { t: 10.1, type: 'pass', team: 'b', id: 7, out: 'incomplete' },
            { t: 11, type: 'tackle', team: 'a', id: 3 },
            { t: 30.2, type: 'pass', team: 'a', id: 3, out: 'completed' },
            { t: 31, type: 'goal', team: 'a', id: 3 },
        ],
    });
}

test('a chunk decodes into its samples', () => {
    const c = decodeChunk(chunk());
    assert.deepEqual([...c.start], [0, 3, 5]);
    const f = frameAt(c, 0, null, 0, 0);
    assert.deepEqual(f.dots.map((d) => [d.id, d.x, d.y, d.team, d.role, d.sure]), [
        [3, 10, 20, 'a', 'player', 15], [7, 50, 30, 'b', 'keeper', 7], [11, 40, 40, '-', 'referee', 15],
    ]);
    assert.deepEqual(f.ball, { state: 'ground', x: 10, y: 20, h: 0 });
    assert.deepEqual(f.hold, { team: 'a', id: 3 });
});

test('between samples the dots and the ball move, a lost dot stays', () => {
    const c = decodeChunk(chunk());
    const f = frameAt(c, 0, c, 1, 0.25);
    const at = Object.fromEntries(f.dots.map((d) => [d.id, d]));
    assert.equal(at[3].x, 10.25);
    assert.equal(at[7].x, 50); // not in the second sample
    assert.equal(f.ball.x, 10.25);
    assert.equal(f.ball.h, 0.5);
    assert.equal(f.ball.state, 'ground'); // the nearer sample's
    assert.deepEqual(frameAt(c, 0, c, 1, 0.75).hold, { team: null, id: null });
    assert.equal(frameAt(c, 0, c, 1, 0.75).ball.state, 'air');
});

test('an unplaced ball has no position', () => {
    const doc = chunk();
    doc.bx = b64(Int16Array, [NO_XY, 110]);
    const c = decodeChunk(doc);
    assert.equal(frameAt(c, 0, null, 0, 0).ball.x, null);
    assert.equal(frameAt(c, 0, c, 1, 0.25).ball.x, null); // nothing to move from
});

test('video times find their half and sample', () => {
    const ix = index();
    assert.equal(halfAt(ix, 9), null);
    assert.equal(halfAt(ix, 10).tag, 'xy1');
    assert.equal(halfAt(ix, 20), null); // half-time
    assert.equal(halfAt(ix, 31).tag, 'xy2');
    assert.deepEqual(samplePosition(ix.halves[0], 10.3), { chunk: 'xy1_00', i: 1, frac: 0.5, k: 0 });
    assert.equal(samplePosition(ix.halves[0], 9), null);
    assert.equal(samplePosition(ix.halves[0], 10.25).i, 1);
    assert.equal(samplePosition(ix.halves[0], 10.5), null); // sample 2 would be in a chunk 1
});

test('the stats so far count moments up to the time', () => {
    const ix = index();
    const s = soFar(ix, 10.6);
    assert.equal(s.counts.a.pass, 1);
    assert.equal(s.counts.a.completed, 1);
    assert.equal(s.counts.b.pass, 1);
    assert.equal(s.counts.b.completed, 0);
    assert.equal(s.counts.a.tackle, 0);
    const later = soFar(ix, 40);
    assert.equal(later.counts.a.pass, 2);
    assert.equal(later.counts.a.tackle, 1);
    assert.ok(!('goal' in later.counts.a)); // not a kind the table counts
});

test('possession so far adds whole buckets and a share of the current one', () => {
    const ix = index();
    assert.equal(soFar(ix, 5).possession, null);
    const first = soFar(ix, 20).possession; // the whole first bucket: 200 to 100
    assert.ok(Math.abs(first.a - 2 / 3) < 1e-9);
    const half = soFar(ix, 25).possession; // + half the second: 250 to 150
    assert.ok(Math.abs(half.a - 250 / 400) < 1e-9);
    assert.ok(Math.abs(soFar(ix, 99).possession.a - 300 / 500) < 1e-9);
});

test('recent moments are newest first', () => {
    assert.deepEqual(recentMoments(index(), 11, 2).map((m) => m.type), ['tackle', 'pass']);
    assert.equal(recentMoments(index(), 1).length, 0);
});

test('why says what the map saw, and nothing it did not', () => {
    const name = (id, team) => `#${id}${team}`;
    const m = { type: 'pass', id: 3, team: 'a', out: 'completed', len: 12.4, conf: 0.41,
        why: { ball: 'ground', d: 1.5, near: 7, near_team: 'b', near_d: 3.5 } };
    assert.deepEqual(whyText(m, name), [
        'Pass by #3a, completed, 12 m.',
        'They were 1.5 m from the ball; the next nearest player, #7b, was 3.5 m away.',
        'The ball was on the ground.',
        'How sure: 41%.',
    ]);
    const nearer = { ...m, why: { ball: 'air', d: 4, near: 7, near_team: 'b', near_d: 2 } };
    assert.match(whyText(nearer, name)[1], /though #7b was nearer, at 2.0 m/);
    const lost = { type: 'tackle', id: 3, team: 'a', why: { ball: 'none' } };
    assert.deepEqual(whyText(lost, name), [
        'Tackle by #3a.', 'The map has no dot for them on that frame.', 'The ball wasn’t seen on that frame.',
    ]);
});

test('the clock', () => {
    assert.equal(clock(0), '0:00');
    assert.equal(clock(754.9), '12:34');
});
