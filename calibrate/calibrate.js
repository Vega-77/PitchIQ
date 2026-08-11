import {
    landmarks, LANDMARK_GROUPS, fitHomography, applyHomography,
} from './pitch-model.js?v=58';
import { mountPitchBackdrop } from '../assets/pitch-backdrop.js?v=58';
import { byId, setText, toast, plural } from '../assets/ui.js?v=58';

const state = {
    image: null,
    imageSize: null,
    points: new Map(),   // landmark -> [x, y] in image pixels
    selected: null,
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

    for (const [name, [x, y]] of state.points) {
        const isSelected = name === state.selected;
        ctx.beginPath();
        ctx.arc(x, y, 7 * scale, 0, Math.PI * 2);
        ctx.fillStyle = isSelected ? 'rgba(107,163,232,.85)' : 'rgba(63,185,107,.85)';
        ctx.fill();
        ctx.lineWidth = 2 * scale;
        ctx.strokeStyle = '#fff';
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
            verdict.className = 'verdict bad';
            verdict.textContent =
                'Something is off. One point is probably in the wrong place or '
                + 'named wrong; the yellow outline should show you which.';
        }
    } catch (err) {
        note.className = 'empty';
        note.textContent = err.message;
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
                renderAll();
                byId('hint').textContent = `Now click "${label}" in the image.`;
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
    renderQuality();
    draw();
    setText('progress', plural(state.points.size, 'point'));
    const count = byId('placed-count');
    if (count) count.textContent = state.points.size;
    byId('btn-export').disabled = state.points.size < 4;
}

// ---------------------------------------------------------------- export

function exportJson() {
    const payload = {
        image_size: state.imageSize,
        pitch: pitchDims(),
        points: [...state.points.entries()].map(([landmark, [x, y]]) => ({
            landmark, x, y,
        })),
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

function init() {
    mountPitchBackdrop(byId('calib-hero'), { opacity: 0.18 });

    byId('input-image').addEventListener('change', (e) => {
        const file = e.target.files?.[0];
        if (file) loadImage(file);
    });

    byId('canvas').addEventListener('click', (e) => {
        if (!state.selected) return toast('Pick a landmark from the list first.', true);

        const canvas = byId('canvas');
        const rect = canvas.getBoundingClientRect();
        // The canvas is displayed scaled down; convert back to source pixels.
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const y = (e.clientY - rect.top) * (canvas.height / rect.height);

        state.points.set(state.selected, [x, y]);
        state.selected = null;
        byId('hint').textContent = 'Pick the next landmark.';
        renderAll();
    });

    byId('btn-clear').addEventListener('click', () => {
        if (state.points.size && !confirm('Remove all placed points?')) return;
        state.points.clear();
        state.selected = null;
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
        byId('input-image').value = '';
        byId('workspace').classList.add('hidden');
        byId('intro').classList.remove('hidden');
        document.querySelector('main.shell').classList.remove('working');
        renderAll();
        window.scrollTo(0, 0);
    });

    byId('btn-export').addEventListener('click', exportJson);

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
window._calib = { state, renderAll, pitchToPixelHomography };
