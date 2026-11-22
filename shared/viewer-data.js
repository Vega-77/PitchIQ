// The match viewer's data, without a page: decoding the export, where everyone is at a
// moment of the video, and the stats so far.
//
// `pitchiq/viewer_data.py` writes it (session step 3.9). An index holds the halves, the
// kits, possession in buckets and the moments, and each chunk holds 300 s of samples, five a
// second. A sample has every dot on the map, the 3D ball and who had the ball. The arrays
// are base64 little-endian, read here as typed arrays.
//
// Times are seconds into the video. The map's frame numbers are the video's, so a frame
// is `seconds * fps` and nothing has to be lined up by hand.
//
// No DOM here, so tests/viewer.test.js can check it under Node.

const BALL_STATES = ['none', 'ground', 'air', 'unsure'];
const HOLD_TEAMS = ['a', 'b', 'unknown', 'dead'];
export const NO_XY = -32768;
export const NO_ID = 0xFFFFFFFF;
export const SEEN = 15;
const ROLES = ['player', 'keeper', 'referee', 'other'];

// The moment kinds the stats count, in the order the "so far" table lists them.
export const COUNTED = ['pass', 'carry', 'shot', 'interception', 'tackle', 'recovery', 'duel'];

function bytes(text) {
    const raw = atob(text);
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) out[i] = raw.charCodeAt(i);
    return out;
}

function typed(text, Type) {
    const b = bytes(text);
    return new Type(b.buffer, b.byteOffset, b.byteLength / Type.BYTES_PER_ELEMENT);
}

/** A chunk document as typed arrays, plus where each sample's dots start. */
export function decodeChunk(doc) {
    const n = typed(doc.n, Uint8Array);
    const start = new Uint32Array(n.length + 1);
    for (let i = 0; i < n.length; i += 1) start[i + 1] = start[i] + n[i];
    return {
        id: doc.id,
        tag: doc.tag,
        f0: doc.f0,
        samples: doc.samples,
        start,
        x: typed(doc.x, Int16Array),
        y: typed(doc.y, Int16Array),
        ids: typed(doc.id32, Uint32Array),
        code: typed(doc.code, Uint8Array),
        bx: typed(doc.bx, Int16Array),
        by: typed(doc.by, Int16Array),
        bh: typed(doc.bh, Uint8Array),
        bs: typed(doc.bs, Uint8Array),
        ht: typed(doc.ht, Uint8Array),
        hid: typed(doc.hid, Uint32Array),
    };
}

/** The half a video time falls in, or null between and outside them. */
export function halfAt(index, videoS) {
    return index.halves.find((h) => videoS * h.fps >= h.f0 && videoS * h.fps <= h.f1) || null;
}

/**
 * Where a video time falls among a half's samples: the chunk that holds it, the sample
 * before it and how far towards the next one (0..1). Null outside the half.
 */
export function samplePosition(half, videoS) {
    const s = (videoS * half.fps - half.f0) / half.step;
    if (s < 0) return null;
    const perChunk = half.samplesPerChunk;
    const i = Math.floor(s);
    const k = Math.floor(i / perChunk);
    if (k >= half.chunks.length) return null;
    return { chunk: half.chunks[k], i: i - k * perChunk, frac: s - i, k };
}

function dotsOf(c, i) {
    const out = [];
    for (let j = c.start[i]; j < c.start[i + 1]; j += 1) {
        const code = c.code[j];
        out.push({
            id: c.ids[j],
            x: c.x[j] / 10,
            y: c.y[j] / 10,
            team: 'ab-'[code & 3],
            role: ROLES[(code >> 2) & 3],
            sure: code >> 4,
        });
    }
    return out;
}

function ballOf(c, i) {
    const state = BALL_STATES[c.bs[i]] || 'none';
    const placed = c.bx[i] !== NO_XY && c.by[i] !== NO_XY;
    return {
        state,
        x: placed ? c.bx[i] / 10 : null,
        y: placed ? c.by[i] / 10 : null,
        h: c.bh[i] / 10,
    };
}

function holdOf(c, i) {
    const t = c.ht[i];
    return { team: t === 255 ? null : HOLD_TEAMS[t], id: c.hid[i] === NO_ID ? null : c.hid[i] };
}

const lerp = (a, b, f) => a + (b - a) * f;

/**
 * Everyone at one moment: the dots, the ball and who had it.
 *
 * `here` and `next` are the decoded chunks for the sample and the one after (the same
 * chunk, or the next one at a chunk's end; `next` may be null at a half's end). A dot moves
 * between its two samples. A dot in only one of them stays where it was, since the
 * map lost or found it there. The ball moves only when both samples place it, and the
 * state and the holder are the nearer sample's.
 */
export function frameAt(here, i, next, j, frac) {
    const a = dotsOf(here, i);
    const nearer = frac < 0.5 || !next ? [here, i] : [next, j];
    if (!next || frac <= 0) {
        return { dots: a, ball: ballOf(here, i), hold: holdOf(here, i) };
    }
    const after = new Map(dotsOf(next, j).map((d) => [d.id, d]));
    const dots = a.map((d) => {
        const b = after.get(d.id);
        return b ? { ...d, x: lerp(d.x, b.x, frac), y: lerp(d.y, b.y, frac) } : d;
    });
    const ba = ballOf(here, i);
    const bb = ballOf(next, j);
    const ball = { ...ballOf(...nearer) };
    if (ba.x !== null && bb.x !== null) {
        ball.x = lerp(ba.x, bb.x, frac);
        ball.y = lerp(ba.y, bb.y, frac);
        ball.h = lerp(ba.h, bb.h, frac);
    }
    return { dots, ball, hold: holdOf(...nearer) };
}

/** The index made ready for lookups: moments in time order. */
export function prepareIndex(index) {
    for (const h of index.halves) h.samplesPerChunk = h.per_chunk;
    index.moments.sort((a, b) => a.t - b.t);
    return index;
}

/**
 * The stats so far at a video time: each counted moment kind by team (passes split into
 * attempted and completed) and possession, from the buckets up to the time.
 * Possession is each team's share of the frames either team had the ball.
 */
export function soFar(index, videoS) {
    const counts = { a: {}, b: {} };
    for (const side of ['a', 'b']) {
        for (const kind of COUNTED) counts[side][kind] = 0;
        counts[side].completed = 0;
    }
    for (const m of index.moments) {
        if (m.t > videoS) break;
        const side = counts[m.team];
        if (!side || !(m.type in side)) continue;
        side[m.type] += 1;
        if (m.type === 'pass' && m.out === 'completed') side.completed += 1;
    }
    let a = 0;
    let b = 0;
    const size = index.possession.bucket_s;
    index.halves.forEach((h, k) => {
        const p = index.possession.halves[k];
        if (!p || !p.a.length) return;
        const into = videoS - p.f0 / h.fps;
        if (into <= 0) return;
        const whole = Math.min(p.a.length, Math.floor(into / size));
        for (let i = 0; i < whole; i += 1) {
            a += p.a[i];
            b += p.b[i];
        }
        if (whole < p.a.length) {
            const part = (into - whole * size) / size;
            a += p.a[whole] * part;
            b += p.b[whole] * part;
        }
    });
    const held = a + b;
    return {
        counts,
        possession: held > 0 ? { a: a / held, b: b / held } : null,
    };
}

/** The last `n` moments up to a video time, newest first. */
export function recentMoments(index, videoS, n = 6) {
    const out = [];
    for (let k = index.moments.length - 1; k >= 0 && out.length < n; k -= 1) {
        if (index.moments[k].t <= videoS) out.push(index.moments[k]);
    }
    return out;
}

const KIND_WORDS = {
    pass: 'Pass', carry: 'Carry', shot: 'Shot', interception: 'Interception',
    tackle: 'Tackle', recovery: 'Recovery', duel: 'Duel', goal: 'Goal',
};

/** A moment's kind as a word. */
export function kindWord(type) {
    return KIND_WORDS[type] || type.charAt(0).toUpperCase() + type.slice(1);
}

const metres = (d) => `${d < 10 ? d.toFixed(1) : Math.round(d)} m`;

/**
 * Why a moment was given to its player, in sentences.
 *
 * `nameOf(id, team)` names a track: a roster name once the coach has matched the figure,
 * otherwise "one of ours" or "one of theirs". Whatever the why is missing (no dot on the frame,
 * no other player near) is left out rather than guessed at.
 */
export function whyText(m, nameOf) {
    const who = nameOf(m.id, m.team);
    const w = m.why || {};
    const lines = [];
    let first = `${kindWord(m.type)} by ${who}`;
    if (m.out && m.out !== 'unknown') first += `, ${m.out}`;
    if (m.type === 'pass' && m.len) first += `, ${metres(m.len)}`;
    lines.push(`${first}.`);
    if (w.d !== undefined) {
        lines.push(`They were ${metres(w.d)} from the ball, the closest to it.`);
        if (w.near !== undefined) {
            const other = nameOf(w.near, w.near_team);
            const gap = w.near_d - w.d;
            lines[1] = gap >= 0
                ? `They were ${metres(w.d)} from the ball; the next nearest player, ${other}, was ${metres(w.near_d)} away.`
                : `They were ${metres(w.d)} from the ball, though ${other} was nearer, at ${metres(w.near_d)}.`;
        }
    } else {
        lines.push('The map has no dot for them on that frame.');
    }
    const ball = {
        air: 'The ball was in the air.',
        ground: 'The ball was on the ground.',
        unsure: 'The ball was seen, but the 3D can’t say whether it was on the ground.',
        none: 'The ball wasn’t seen on that frame.',
    }[w.ball];
    if (ball) lines.push(ball);
    if (m.conf !== undefined) lines.push(`How sure: ${Math.round(m.conf * 100)}%.`);
    return lines;
}

/** Seconds as m:ss on the video clock. */
export function clock(videoS) {
    const s = Math.max(0, Math.floor(videoS));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
