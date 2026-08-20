import {
    landmarks, LANDMARK_GROUPS, fitHomography, applyHomography, measureField,
} from './pitch-model.js?v=99';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=99';
import { byId, setText, toast, plural } from '../assets/ui.js?v=99';

const state = {
    image: null,
    imageSize: null,
    points: new Map(),   // landmark -> [x, y] in image pixels
    selected: null,
    measured: null,      // last measureField() result, or null
    eyeballed: false,    // the coach ticked "the outline sits on the paint"
    aim: null,           // [x, y] in image pixels the magnifier is showing
    aiming: false,       // a press is down and will place a point on release
    adjusting: null,     // landmark the arrow keys nudge, or null
};

const pitchDims = () => ({
    length_m: parseFloat(byId('input-length').value) || 105,
    width_m: parseFloat(byId('input-width').value) || 68,
});

// ---------------------------------------------------------------- image

function loadImage(file) {
    const reader = new FileReader();
    reader.onload = () => {
        const img = new Image();
        img.onload = () => {
            state.image = img;
            state.imageSize = [img.naturalWidth, img.naturalHeight];
            state.points.clear();
            // The explanation has done its job once there is a picture to work
            // on, so it gets out of the way rather than pushing the tool down.
            byId('intro').classList.add('hidden');
            byId('workspace').classList.remove('hidden');
            // Clicking a landmark means hitting an individual pixel, so on a
            // wide screen the canvas should have the room. The reading pages
            // stay narrow; this one earns the width.
            document.querySelector('main.shell').classList.add('working');
            const canvas = byId('canvas');
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            renderAll();
            window.scrollTo(0, 0);
            toast(`Loaded ${img.naturalWidth}×${img.naturalHeight}`);
        };
        img.onerror = () => toast('Could not read that image.', true);
        img.src = reader.result;
    };
    reader.readAsDataURL(file);
}

// ---------------------------------------------------------------- drawing

function draw() {
    const canvas = byId('canvas');
    const ctx = canvas.getContext('2d');
    if (!state.image) return;

    ctx.drawImage(state.image, 0, 0);

    const scale = Math.max(1, canvas.width / 1000);
    drawPitchOverlay(ctx, scale);
    drawPoints(ctx, scale);
}

/**
 * The placed landmarks, in image pixels.
 *
 * `hollow` drops the filled disc and leaves the ring and the crosshair. The
 * magnifier needs that: at four times life size a solid dot covers the very
 * paint the coach is lining the crosshair up against, which is the only thing
 * they opened the magnifier to see.
 */
function drawPoints(ctx, scale, hollow = false) {
    for (const [name, [x, y]] of state.points) {
        const isLive = name === state.selected || name === state.adjusting;
        const colour = isLive ? 'rgba(107,163,232,.85)' : 'rgba(63,185,107,.85)';

        ctx.beginPath();
        ctx.arc(x, y, 7 * scale, 0, Math.PI * 2);
        if (!hollow) {
            ctx.fillStyle = colour;
            ctx.fill();
        }
        ctx.lineWidth = 2 * scale;
        ctx.strokeStyle = hollow ? colour : '#fff';
        ctx.stroke();

        // Crosshair, so the exact clicked pixel stays visible under the dot.
        ctx.beginPath();
        ctx.moveTo(x - 12 * scale, y);
        ctx.lineTo(x + 12 * scale, y);
        ctx.moveTo(x, y - 12 * scale);
        ctx.lineTo(x, y + 12 * scale);
        ctx.strokeStyle = 'rgba(255,255,255,.6)';
        ctx.lineWidth = 1 * scale;
        ctx.stroke();
    }
}

/** Project the real pitch outline back onto the frame as a visual check. */
function drawPitchOverlay(ctx, scale) {
    if (state.points.size < 4) return;

    let H;
    try {
        H = pitchToPixelHomography();
    } catch {
        return;
    }

    const { length_m: L, width_m: W } = pitchDims();
    const marks = landmarks(L, W);
    const p = (x, y) => applyHomography(H, x, y);

    ctx.save();
    ctx.strokeStyle = 'rgba(255,220,50,.9)';
    ctx.lineWidth = 2 * scale;

    const poly = (pts, close = true) => {
        ctx.beginPath();
        pts.forEach(([x, y], i) => {
            const [px, py] = p(x, y);
            i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
        });
        if (close) ctx.closePath();
        ctx.stroke();
    };

    // Touchlines and goal lines
    poly([[0, 0], [L, 0], [L, W], [0, W]]);
    // Halfway line
    poly([[L / 2, 0], [L / 2, W]], false);
    // Penalty areas
    poly([
        [0, marks.pen_left_bottom_goalline[1]],
        [16.5, marks.pen_left_bottom_goalline[1]],
        [16.5, marks.pen_left_top_goalline[1]],
        [0, marks.pen_left_top_goalline[1]],
    ], false);
    poly([
        [L, marks.pen_right_bottom_goalline[1]],
        [L - 16.5, marks.pen_right_bottom_goalline[1]],
        [L - 16.5, marks.pen_right_top_goalline[1]],
        [L, marks.pen_right_top_goalline[1]],
    ], false);

    // Centre circle
    ctx.beginPath();
    for (let i = 0; i <= 48; i++) {
        const a = (i / 48) * Math.PI * 2;
        const [px, py] = p(L / 2 + 9.15 * Math.cos(a), W / 2 + 9.15 * Math.sin(a));
        i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    }
    ctx.stroke();
    ctx.restore();
}

/** Pitch metres -> image pixels, the direction the overlay needs. */
function pitchToPixelHomography() {
    const { length_m, width_m } = pitchDims();
    const marks = landmarks(length_m, width_m);
    const pairs = [...state.points.entries()].map(([name, px]) => ({
        src: marks[name],
        dst: px,
    }));
    return fitHomography(pairs);
}

// -------------------------------------------------------------- magnifier

// How many source pixels the magnifier shows across its own width. 44 into a
// 168px circle is a bit under four times life size on a 1080p frame: enough to
// resolve the edge of a painted line, not so much that the line stops looking
// like a line. On a narrow phone the circle shrinks with the picture and the
// magnification drops with it, to around three and a half times.
const LOUPE_SPAN = 44;

// Breathing room between the magnifier and the edge of the stage.
const LOUPE_GAP = 12;

// The widest the magnifier is ever drawn, and the narrowest worth drawing.
const LOUPE_MAX = 168;
const LOUPE_MIN = 96;

/**
 * How wide to draw the magnifier on a stage `stageW` display pixels across.
 *
 * The anchor parks it in whichever half of the stage the aim is not in, so it
 * only stays off the aim while it fits inside that half. On a 375px phone the
 * picture is about 340px wide and the full-size magnifier does not fit — it
 * would sit on the paint for every aim near the middle, on the one device
 * where the finger is already covering it.
 *
 * Below `LOUPE_MIN` it stops shrinking and takes the overlap instead: a
 * magnifier too small to read is not a better answer than one in the way.
 */
function loupeSize(stageW, gap = LOUPE_GAP) {
    // Floored, not rounded: rounding a half up gives back the pixel of overlap
    // this whole function exists to avoid.
    return Math.max(LOUPE_MIN, Math.min(LOUPE_MAX, Math.floor(stageW / 2 - gap)));
}

/**
 * Where to park the magnifier inside the stage, in display pixels.
 *
 * It goes to the corner furthest from the aim, so it never covers the spot it
 * is magnifying, and it is clamped so it never hangs off the stage on a narrow
 * phone. Pure, and takes numbers rather than elements, because it is the only
 * part of the magnifier a test can check: the test DOM has no layout, so
 * `getBoundingClientRect` there is all zeros by design.
 */
function loupeAnchor(aimX, aimY, stageW, stageH, size, gap = LOUPE_GAP) {
    const clamp = (v, limit) => Math.max(0, Math.min(v, Math.max(0, limit)));
    return {
        left: clamp(aimX > stageW / 2 ? gap : stageW - size - gap, stageW - size),
        top: clamp(aimY > stageH / 2 ? gap : stageH - size - gap, stageH - size),
    };
}

/**
 * A crosshair with a hole in the middle, so the pixel being aimed at is the
 * one thing on screen not covered by the thing pointing at it.
 *
 * Drawn dark then light: white paint and grass in shadow are both on this
 * picture, and no single colour stays visible over both.
 */
function drawLoupeCrosshair(ctx, size, zoom) {
    const c = size / 2;
    const hole = Math.max(5, zoom);

    const arms = () => {
        ctx.beginPath();
        ctx.moveTo(0, c); ctx.lineTo(c - hole, c);
        ctx.moveTo(c + hole, c); ctx.lineTo(size, c);
        ctx.moveTo(c, 0); ctx.lineTo(c, c - hole);
        ctx.moveTo(c, c + hole); ctx.lineTo(c, size);
        ctx.stroke();
    };

    ctx.save();
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(0,0,0,.5)';
    arms();
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255,255,255,.95)';
    arms();

    // The single source pixel that will be stored, at its real size. Without
    // it "the exact pixel" is a claim rather than something you can see.
    ctx.strokeStyle = 'rgba(255,220,50,.95)';
    ctx.strokeRect(c - zoom / 2, c - zoom / 2, zoom, zoom);
    ctx.restore();
}

/** Redraw the magnifier over `state.aim`, or hide it when there is no aim. */
function drawLoupe() {
    const loupe = byId('loupe');
    if (!loupe) return;

    if (!state.aim || !state.image) {
        loupe.classList.add('hidden');
        return;
    }

    const canvas = byId('canvas');
    const rect = canvas.getBoundingClientRect();

    const [aimX, aimY] = state.aim;
    // Measured from the stage on every redraw, so a phone turned sideways gets
    // a magnifier that fits the picture it is now looking at rather than one
    // sized for the old orientation.
    const size = rect.width ? loupeSize(rect.width) : LOUPE_MAX;
    if (loupe.width !== size) {
        loupe.width = size;
        loupe.height = size;
    }
    loupe.style.width = `${size}px`;
    loupe.style.height = `${size}px`;
    const zoom = size / LOUPE_SPAN;
    const ctx = loupe.getContext('2d');

    ctx.save();
    ctx.clearRect(0, 0, size, size);
    // Nearest neighbour. A smoothed magnifier invents pixels between the real
    // ones, and the real ones are the entire point of opening it.
    ctx.imageSmoothingEnabled = false;
    ctx.translate(size / 2, size / 2);
    ctx.scale(zoom, zoom);
    ctx.translate(-aimX, -aimY);
    ctx.drawImage(state.image, 0, 0);
    // Widths divided by the zoom, so the outline and the points come out the
    // same thickness on screen here as they are on the picture behind. This is
    // also the only place the yellow outline is legible enough to answer the
    // fifth readiness check honestly.
    drawPitchOverlay(ctx, 1 / zoom);
    drawPoints(ctx, 1 / zoom, true);
    ctx.restore();

    drawLoupeCrosshair(ctx, size, zoom);

    const at = loupeAnchor(
        rect.width ? aimX * (rect.width / canvas.width) : 0,
        rect.height ? aimY * (rect.height / canvas.height) : 0,
        rect.width, rect.height, size,
    );
    loupe.style.left = `${at.left}px`;
    loupe.style.top = `${at.top}px`;
    loupe.classList.remove('hidden');
}

/**
 * Pointer position to source image pixels, or null when the canvas has no
 * measured size — which is every time in the test DOM, and would otherwise
 * write NaN into a landmark.
 */
function toImagePixel(e) {
    const canvas = byId('canvas');
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return [
        (e.clientX - rect.left) * (canvas.width / rect.width),
        (e.clientY - rect.top) * (canvas.height / rect.height),
    ];
}

/**
 * Commit a landmark, and leave the arrow keys pointed at it. A coach who can
 * now see the point is a pixel off should not have to press again to fix it.
 */
function placeAt(name, at) {
    state.points.set(name, at);
    state.selected = null;
    state.adjusting = name;
    state.aim = at;
    byId('hint').textContent =
        'Placed. Arrow keys nudge it a pixel, Shift+arrow ten. '
        + 'Or pick the next landmark.';
    renderAll();
    byId('stage')?.focus({ preventScroll: true });
}

/**
 * Move the last placed point by whole source pixels.
 *
 * The magnifier resolves single pixels, so the moment a coach can see the
 * point is one pixel off they need a way to move it one pixel — which a
 * pointer at a third of life size cannot do. The first nudge rounds to a whole
 * pixel: a press lands on a fraction, the list on the right reports whole
 * numbers, and this way the number on screen is the number that was stored.
 */
function nudge(dx, dy) {
    const name = state.adjusting;
    if (!name || !state.points.has(name)) return false;

    const [x, y] = state.points.get(name);
    const [w, h] = state.imageSize || [Infinity, Infinity];
    const clamp = (v, limit) => Math.max(0, Math.min(v, limit - 1));
    const moved = [clamp(Math.round(x) + dx, w), clamp(Math.round(y) + dy, h)];

    state.points.set(name, moved);
    state.aim = moved;
    renderAll();
    return true;
}

// ---------------------------------------------------------------- quality

// Below this share of the frame, the clicked points are bunched tightly enough
// that the fit is only trustworthy near them.
const POOR_SPREAD = 0.15;
const GOOD_SPREAD = 0.30;

/** A tiny share reads as "<1%" rather than "0%", which looks like a bug. */
const percent = (fraction) =>
    fraction > 0 && fraction < 0.01 ? '<1%' : `${(fraction * 100).toFixed(0)}%`;

/**
 * How much of the picture the clicked points actually enclose, 0 to 1.
 *
 * The page warns in words that four points bunched in one corner "will look
 * perfect and still be wrong everywhere else on the field" — and that is exactly
 * what the error figures do, because they only measure the fit at the points
 * you clicked. This measures the thing the warning is about, so it can be
 * checked rather than merely mentioned.
 */
function pointSpread() {
    if (!state.imageSize || state.points.size < 3) return 0;

    const xs = [...state.points.values()].map((p) => p[0]);
    const ys = [...state.points.values()].map((p) => p[1]);
    const covered = (Math.max(...xs) - Math.min(...xs))
        * (Math.max(...ys) - Math.min(...ys));

    return covered / (state.imageSize[0] * state.imageSize[1]);
}

/**
 * Mean and worst reprojection error for the points as clicked, in metres.
 *
 * Pulled out of renderQuality because the readiness list needs the same two
 * numbers and fitting twice per render invites the two halves of the page to
 * disagree with each other about whether the calibration is good.
 */
function fitErrors() {
    if (state.points.size < 4) return null;
    try {
        const { length_m, width_m } = pitchDims();
        const marks = landmarks(length_m, width_m);
        const H = fitHomography([...state.points.entries()].map(([name, px]) => ({
            src: px, dst: marks[name],
        })));
        const errors = [...state.points.entries()].map(([name, px]) => {
            const [x, y] = applyHomography(H, px[0], px[1]);
            const [tx, ty] = marks[name];
            return Math.hypot(x - tx, y - ty);
        });
        const mean = errors.reduce((a, b) => a + b, 0) / errors.length;
        const max = Math.max(...errors);
        if (!Number.isFinite(mean) || !Number.isFinite(max)) return null;
        return { mean, max, ok: mean <= 0.5 && max <= 1.5 };
    } catch {
        return null;
    }
}

function renderQuality() {
    const note = byId('preview-note');

    if (state.points.size < 4) {
        const left = 4 - state.points.size;
        note.className = 'empty';
        note.textContent = `Place ${plural(left, 'more point')} before we can check the fit.`;
        return;
    }

    let pixelToPitch;
    try {
        const { length_m, width_m } = pitchDims();
        const marks = landmarks(length_m, width_m);
        pixelToPitch = fitHomography(
            [...state.points.entries()].map(([name, px]) => ({
                src: px,
                dst: marks[name],
            }))
        );

        const errors = [...state.points.entries()].map(([name, px]) => {
            const [x, y] = applyHomography(pixelToPitch, px[0], px[1]);
            const [tx, ty] = marks[name];
            return Math.hypot(x - tx, y - ty);
        });

        const mean = errors.reduce((a, b) => a + b, 0) / errors.length;
        const max = Math.max(...errors);
        const exact = state.points.size === 4;
        const ok = mean <= 0.5 && max <= 1.5;

        const spread = pointSpread();
        const spreadTone = spread >= GOOD_SPREAD ? 'good'
            : spread >= POOR_SPREAD ? '' : 'bad';

        note.className = '';
        note.innerHTML = `
            <div class="quality">
                <div class="stat"><div class="value ${ok ? 'good' : 'bad'}">${mean.toFixed(2)}m</div><div class="label">Average error</div></div>
                <div class="stat"><div class="value ${ok ? 'good' : 'bad'}">${max.toFixed(2)}m</div><div class="label">Worst point</div></div>
                <div class="stat"><div class="value ${spreadTone}">${percent(spread)}</div><div class="label">Frame covered</div></div>
                <div class="stat"><div class="value">${state.points.size}</div><div class="label">Points placed</div></div>
            </div>
            <p class="verdict"></p>`;

        const verdict = note.querySelector('.verdict');

        // Order matters. Bunched points make the error figures meaningless, so
        // that gets said before any verdict about how small they are — a tight
        // cluster reports a beautiful error and is wrong everywhere else.
        if (spread < POOR_SPREAD) {
            verdict.className = 'verdict bad';
            verdict.textContent =
                `Your points only cover ${percent(spread)} of the picture. `
                + 'The error figures above are close to meaningless while they are '
                + 'bunched like that — the fit is only trustworthy near them. Add '
                + 'points at the far end of the field.';
        } else if (exact) {
            verdict.className = 'verdict';
            verdict.textContent =
                'With exactly four points these numbers are always zero, so they '
                + "don't tell you anything yet. Add a fifth to get a real check.";
        } else if (ok) {
            verdict.className = 'verdict good';
            verdict.textContent =
                'Good fit. Have a look at the yellow outline — if it sits on the '
                + 'painted lines, you can save it.';
        } else {
            // Three causes, and this page cannot tell them apart. It used to
            // say "one point is probably in the wrong place", which is a
            // confident single-cause diagnosis and is wrong for a whole class
            // of camera: measured on a synthetic wide-angle lens, barrel
            // distortion as mild as k1 = -0.03 gives about 1.1m of error with
            // every landmark clicked perfectly. A coach with a GoPro would
            // re-click forever and never fix it, because nothing is wrong with
            // the clicking.
            //
            // Three statistics were tried as a discriminator and all three
            // failed against the least-squares fit above — see ROADMAP Phase 1
            // for the numbers. So the page lists the candidates and does not
            // pick, with the lens first because it is the only one where the
            // obvious next action is the wrong one.
            verdict.className = 'verdict bad';
            verdict.innerHTML =
                'Something is off, and these numbers cannot say which of three '
                + 'things it is:<br>'
                + '<b>A wide-angle lens.</b> Action cameras and phone "wide" '
                + 'modes bend straight lines, and no amount of re-clicking '
                + 'fixes it — switch the camera to its narrow or linear '
                + 'setting and grab a new frame.<br>'
                + '<b>A misplaced or mis-named point.</b> Check the yellow '
                + 'outline against the painted lines; where it sits wrong is '
                + 'where to look.<br>'
                + '<b>The pitch size above.</b> If it is a guess rather than a '
                + 'measurement, every metre here is scaled by that guess.';
        }
    } catch (err) {
        note.className = 'empty';
        note.textContent = err.message;
    }
}

// ------------------------------------------------------------- field size

// How far the typed size may sit from the measured one before the page calls
// it a disagreement. Half a metre is below what the measurement itself can
// resolve on a good frame, so anything tighter would flag its own noise.
const SIZE_TOLERANCE_M = 0.5;

const round1 = (v) => Math.round(v * 10) / 10;

/**
 * Measure the field from the clicks and say so, or say why it cannot.
 *
 * This block exists because of a real failure. The size inputs used to live in
 * the Start card, which is hidden the moment a picture loads — so a coach
 * who needed a smaller pitch than 105 × 68 could not reach the inputs while
 * clicking, and had no way to tell that the size was what was wrong. The
 * inputs now sit here, and the page volunteers the answer rather than waiting
 * to be asked.
 */
function renderFieldSize() {
    const box = byId('size-measured');
    if (!box) return;

    const measured = measureField(state.points);
    state.measured = measured;

    if (!measured) {
        const left = 5 - state.points.size;
        box.className = 'measured';
        box.textContent = left > 0
            ? `Place ${plural(left, 'more point')} and the page will try to `
                + 'measure the field for you.'
            : 'Not enough usable points to measure the field.';
        return;
    }

    const { length_m, width_m } = pitchDims();
    const parts = [];
    if (measured.lengthConfident) {
        parts.push({
            id: 'length', label: 'length',
            got: round1(measured.lengthM), typed: length_m,
        });
    }
    if (measured.widthConfident) {
        parts.push({
            id: 'width', label: 'width',
            got: round1(measured.widthM), typed: width_m,
        });
    }

    if (!parts.length) {
        // Not a failure of the clicking, and it must not read like one. Corners
        // are wherever you say they are, so a set of corners fits every size
        // equally well; the penalty box, the goal and the penalty spot are
        // fixed distances in the Laws, so one of those is what pins the scale.
        box.className = 'measured';
        box.innerHTML = 'These points cannot measure the field — corners fit '
            + 'any size equally well. Add a <b>penalty box corner</b>, a '
            + '<b>penalty spot</b> or a <b>goalpost</b>: those are fixed sizes, '
            + 'so they are what sets the scale.';
        return;
    }

    const off = parts.filter((d) => Math.abs(d.got - d.typed) > SIZE_TOLERANCE_M);
    const said = parts.map((d) => `${d.label} <b>${d.got.toFixed(1)}m</b>`).join(', ');
    const missing = parts.length === 1
        ? ` (the ${parts[0].id === 'length' ? 'width' : 'length'} is not `
            + 'measurable from these points)'
        : '';

    box.className = off.length ? 'measured is-off' : 'measured is-ok';
    box.innerHTML = `<div>Your points measure ${said}${missing}.</div>`;

    if (off.length) {
        const btn = document.createElement('button');
        btn.id = 'btn-apply-size';
        btn.className = 'btn small primary';
        btn.style.marginTop = '8px';
        btn.textContent = `Use ${off.map((d) => `${d.got.toFixed(1)}m`).join(' and ')}`;
        btn.addEventListener('click', () => {
            for (const d of off) byId(`input-${d.id}`).value = d.got;
            renderAll();
            toast('Field size updated');
        });
        box.appendChild(btn);
    } else {
        const agrees = document.createElement('div');
        agrees.className = 'muted';
        agrees.style.marginTop = '4px';
        agrees.textContent = 'That agrees with what you typed.';
        box.appendChild(agrees);
    }
}

// --------------------------------------------------------------- readiness

/**
 * The five checks, and whether this calibration has passed them.
 *
 * The page could already tell you your average error was 1.77m; what it could
 * not tell you was whether you were finished. Every row is something that has
 * actually gone wrong on a real frame, and the last one is deliberately not
 * something software can check — a homography fitted to eight points that
 * were all clicked in the wrong place fits them beautifully.
 */
function readinessRows() {
    const fit = fitErrors();
    const spread = pointSpread();
    const measured = state.measured;
    const { length_m, width_m } = pitchDims();

    let size;
    if (!measured) {
        size = ['todo', 'Field size measured from your points'];
    } else if (!measured.lengthConfident && !measured.widthConfident) {
        size = ['warn', 'Field size cannot be measured — add a box corner, '
            + 'penalty spot or goalpost'];
    } else {
        const disagrees = (measured.lengthConfident
                && Math.abs(round1(measured.lengthM) - length_m) > SIZE_TOLERANCE_M)
            || (measured.widthConfident
                && Math.abs(round1(measured.widthM) - width_m) > SIZE_TOLERANCE_M);
        size = disagrees
            ? ['bad', 'Field size disagrees with your points — see above']
            : ['good', 'Field size agrees with your points'];
    }

    const four = state.points.size === 4;
    return [
        [state.points.size >= 5 ? 'good' : four ? 'warn' : 'todo',
            four
                ? 'Five points or more — four always score a perfect zero'
                : `Five points or more (${state.points.size} placed)`],
        [spread >= GOOD_SPREAD ? 'good' : spread >= POOR_SPREAD ? 'warn' : 'bad',
            `Points spread across the picture (${percent(spread)} covered)`],
        [!fit ? 'todo' : fit.ok ? 'good' : 'bad',
            fit
                ? `Within half a metre (${fit.mean.toFixed(2)}m average, `
                    + `${fit.max.toFixed(2)}m worst)`
                : 'Within half a metre'],
        size,
        [state.eyeballed ? 'good' : 'todo',
            'You checked the yellow outline against the painted lines'],
    ];
}

const MARKS = { good: '✓', warn: '!', bad: '✗', todo: '○' };

function renderReadiness() {
    const list = byId('readiness');
    if (!list) return;

    const rows = readinessRows();
    list.innerHTML = '';

    for (const [tone, label] of rows) {
        const row = document.createElement('li');
        row.className = `ready-row is-${tone}`;
        const mark = document.createElement('span');
        mark.className = 'mark';
        mark.textContent = MARKS[tone];
        const text = document.createElement('span');
        text.textContent = label;
        row.append(mark, text);
        list.appendChild(row);
    }

    // The point of the whole block: one sentence that says finished, or says
    // what is left. The Save button stays live either way — this page
    // informs rather than holding the file hostage — but when there is work
    // outstanding it stops looking like the obvious next thing to press.
    const left = rows.filter(([tone]) => tone !== 'good').length;
    const done = left === 0;

    const summary = document.createElement('li');
    summary.className = `ready-summary is-${done ? 'good' : 'todo'}`;
    summary.textContent = done
        ? 'Done. This calibration is ready to save.'
        : `${plural(left, 'check')} left before this is ready to save.`;
    list.appendChild(summary);

    const save = byId('btn-export');
    if (save) {
        save.textContent = done ? 'Save calibration' : 'Save anyway';
        // Only the emphasis moves. Ghost would be near-invisible up in the
        // topbar, and a coach who decides to save an imperfect calibration
        // anyway should not have to hunt for the button.
        save.classList.toggle('primary', done);
    }
}

// ---------------------------------------------------------------- lists

function renderLandmarkList() {
    const list = byId('landmark-list');
    list.innerHTML = '';

    for (const group of LANDMARK_GROUPS) {
        const heading = document.createElement('div');
        heading.className = 'landmark-group';
        heading.textContent = group.name;
        list.appendChild(heading);

        for (const [key, label] of group.items) {
            const button = document.createElement('button');
            button.className = 'landmark-btn';
            if (key === state.selected) button.classList.add('active');
            if (state.points.has(key)) button.classList.add('done');
            button.innerHTML = `<span></span><span class="tick"></span>`;
            button.querySelector('span').textContent = label;
            button.querySelector('.tick').textContent = state.points.has(key) ? '✓' : '';
            button.addEventListener('click', () => {
                state.selected = key;
                // Re-picking a placed landmark opens the magnifier on it and
                // points the arrow keys at it, which is the whole of "that one
                // is slightly off, let me fix it".
                state.adjusting = state.points.has(key) ? key : null;
                state.aim = state.points.get(key) ?? null;
                renderAll();
                byId('hint').textContent =
                    `Now press and hold "${label}" on the picture.`;
            });
            list.appendChild(button);
        }
    }
}

function renderPlaced() {
    const list = byId('placed-list');
    list.innerHTML = '';

    if (!state.points.size) {
        list.innerHTML = '<div class="empty" style="padding:14px">No points yet.</div>';
        return;
    }

    for (const [name, [x, y]] of state.points) {
        const row = document.createElement('div');
        row.className = 'placed-row';
        row.innerHTML = `
            <span class="name"></span>
            <span class="coord"></span>
            <button title="Remove">×</button>`;
        row.querySelector('.name').textContent = name;
        row.querySelector('.coord').textContent = `${Math.round(x)}, ${Math.round(y)}`;
        row.querySelector('button').addEventListener('click', () => {
            state.points.delete(name);
            renderAll();
        });
        list.appendChild(row);
    }
}

function renderAll() {
    renderLandmarkList();
    renderPlaced();
    // Field size first: it writes state.measured, which the readiness list
    // reads. Quality between them, so the two error figures on screen come
    // from the same fit as the row that grades them.
    renderFieldSize();
    renderQuality();
    renderReadiness();
    draw();
    drawLoupe();
    // Only with a landmark selected does a drag on the picture mean aiming;
    // the rest of the time a swipe there has to keep scrolling the page.
    byId('canvas').classList.toggle('aiming', !!state.selected);
    setText('progress', plural(state.points.size, 'point'));
    const count = byId('placed-count');
    if (count) count.textContent = state.points.size;
    byId('btn-export').disabled = state.points.size < 4;
}

// ---------------------------------------------------------------- export

function exportJson() {
    const fit = fitErrors();
    const payload = {
        image_size: state.imageSize,
        pitch: pitchDims(),
        points: [...state.points.entries()].map(([landmark, [x, y]]) => ({
            landmark, x, y,
        })),
        // What the page thought of this calibration at the moment it was
        // saved. `from_picker_export` reads pitch, points and image_size and
        // ignores the rest, so this travels with the file without changing
        // how it loads — and a file that turns out to be wrong later can be
        // asked whether the page said so at the time.
        quality: {
            mean_error_m: fit ? fit.mean : null,
            worst_error_m: fit ? fit.max : null,
            frame_coverage: pointSpread(),
            measured: state.measured && {
                length_m: state.measured.lengthConfident
                    ? round1(state.measured.lengthM) : null,
                width_m: state.measured.widthConfident
                    ? round1(state.measured.widthM) : null,
            },
            outline_checked: state.eyeballed,
        },
    };

    const blob = new Blob([JSON.stringify(payload, null, 2)], {
        type: 'application/json',
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'calibration-points.json';
    a.click();
    URL.revokeObjectURL(a.href);

    toast('Saved to your downloads');
}

// ---------------------------------------------------------------- init

// Clearing the points invalidates the one check software cannot make: the
// tick said *those* points sat on the paint, and they are gone.
function forgetAim() {
    state.aim = null;
    state.aiming = false;
    state.adjusting = null;
}

function clearEyeball() {
    state.eyeballed = false;
    const box = byId('chk-eyeball');
    if (box) box.checked = false;
}

function init() {
    mountPitchBackdrop(byId('calib-hero'), { opacity: 0.18 });

    byId('input-image').addEventListener('change', (e) => {
        const file = e.target.files?.[0];
        if (file) loadImage(file);
    });

    // Press, look, slide, release — rather than a single click that commits
    // wherever it landed. On a phone this is the difference between usable and
    // not: the finger covers the exact pixel it is trying to hit, so a plain
    // tap cannot be accurate there however steady the hand is.
    const canvas = byId('canvas');

    canvas.addEventListener('pointerdown', (e) => {
        if (!state.image) return;
        if (!state.selected) return toast('Pick a landmark from the list first.', true);

        const at = toImagePixel(e);
        if (!at) return;
        e.preventDefault();
        // Optional: a pointer that left the canvas mid-drag should keep
        // aiming, but not every environment running this has capture.
        canvas.setPointerCapture?.(e.pointerId);
        state.aiming = true;
        state.aim = at;
        drawLoupe();
    });

    canvas.addEventListener('pointermove', (e) => {
        if (!state.aiming) return;
        const at = toImagePixel(e);
        if (!at) return;
        e.preventDefault();
        state.aim = at;
        drawLoupe();
    });

    canvas.addEventListener('pointerup', (e) => {
        if (!state.aiming) return;
        state.aiming = false;
        try {
            canvas.releasePointerCapture?.(e.pointerId);
        } catch {
            // Never captured it. Nothing to release, and nothing to say.
        }
        // Fall back to the last aim: a release just outside the canvas still
        // means "place it where the magnifier was showing".
        const at = toImagePixel(e) ?? state.aim;
        if (state.selected && at) placeAt(state.selected, at);
    });

    canvas.addEventListener('pointercancel', () => { state.aiming = false; });

    // Element-level, on the stage, so the arrows only move a point when the
    // picture is what the coach is working in — and so preventDefault stops
    // the page scrolling out from under them while they nudge.
    const NUDGES = {
        ArrowLeft: [-1, 0], ArrowRight: [1, 0],
        ArrowUp: [0, -1], ArrowDown: [0, 1],
    };

    byId('stage').addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            state.aim = null;
            state.selected = null;
            state.adjusting = null;
            renderAll();
            return;
        }
        const step = NUDGES[e.key];
        if (!step) return;
        const by = e.shiftKey ? 10 : 1;
        if (nudge(step[0] * by, step[1] * by)) e.preventDefault();
    });

    byId('btn-clear').addEventListener('click', () => {
        if (state.points.size && !confirm('Remove all placed points?')) return;
        state.points.clear();
        state.selected = null;
        forgetAim();
        clearEyeball();
        renderAll();
    });

    byId('btn-new-image').addEventListener('click', () => {
        if (state.points.size
            && !confirm('Start over with a different picture? Your points will be lost.')) {
            return;
        }
        state.points.clear();
        state.selected = null;
        state.image = null;
        forgetAim();
        clearEyeball();
        byId('input-image').value = '';
        byId('workspace').classList.add('hidden');
        byId('intro').classList.remove('hidden');
        document.querySelector('main.shell').classList.remove('working');
        renderAll();
        window.scrollTo(0, 0);
    });

    byId('btn-export').addEventListener('click', exportJson);

    byId('chk-eyeball').addEventListener('change', (e) => {
        state.eyeballed = e.target.checked;
        renderAll();
    });

    for (const id of ['input-length', 'input-width']) {
        byId(id).addEventListener('input', () => {
            if (state.image) renderAll();
        });
    }

    renderLandmarkList();
}

init();

// Deliberate test seam, so the picker can be driven from a browser without a
// human clicking landmarks. Local UI state only; nothing here touches the
// database.
window._calib = {
    state, renderAll, pitchToPixelHomography, loupeAnchor, loupeSize, nudge,
};
