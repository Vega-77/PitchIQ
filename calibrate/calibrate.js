import {
    landmarks, LANDMARK_GROUPS, fitHomography, applyHomography,
} from './pitch-model.js';

const $ = (id) => document.getElementById(id);

const state = {
    image: null,
    imageSize: null,
    points: new Map(),   // landmark -> [x, y] in image pixels
    selected: null,
};

let toastTimer;
function toast(message, isError = false) {
    const el = $('toast');
    el.textContent = message;
    el.classList.toggle('error', isError);
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
}

const pitchDims = () => ({
    length_m: parseFloat($('input-length').value) || 105,
    width_m: parseFloat($('input-width').value) || 68,
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
            $('workspace').classList.remove('hidden');
            const canvas = $('canvas');
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            renderAll();
            toast(`Loaded ${img.naturalWidth}×${img.naturalHeight}`);
        };
        img.onerror = () => toast('Could not read that image.', true);
        img.src = reader.result;
    };
    reader.readAsDataURL(file);
}

// ---------------------------------------------------------------- drawing

function draw() {
    const canvas = $('canvas');
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

function renderQuality() {
    const note = $('preview-note');

    if (state.points.size < 4) {
        note.className = 'empty';
        note.textContent = `Place ${4 - state.points.size} more point(s) to see the preview.`;
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

        note.className = '';
        note.innerHTML = `
            <div class="quality">
                <div class="stat"><div class="value ${ok ? 'good' : 'bad'}">${mean.toFixed(2)}m</div><div class="label">Mean error</div></div>
                <div class="stat"><div class="value ${ok ? 'good' : 'bad'}">${max.toFixed(2)}m</div><div class="label">Worst point</div></div>
                <div class="stat"><div class="value">${state.points.size}</div><div class="label">Points</div></div>
            </div>
            <p class="muted" style="margin-top:12px">${
                exact
                    ? 'With exactly four points the error is zero by construction — it proves nothing. Add a fifth to get a real measurement.'
                    : ok
                        ? 'Looks good. Check the yellow outline sits on the real lines before exporting.'
                        : 'Too high. One of the points is probably misplaced or mislabelled — the outline will show which.'
            }</p>`;
    } catch (err) {
        note.className = 'empty';
        note.textContent = err.message;
    }
}

// ---------------------------------------------------------------- lists

function renderLandmarkList() {
    const list = $('landmark-list');
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
                $('hint').textContent = `Now click "${label}" in the image.`;
            });
            list.appendChild(button);
        }
    }
}

function renderPlaced() {
    const list = $('placed-list');
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
    $('progress').textContent = `${state.points.size} point${state.points.size === 1 ? '' : 's'}`;
    $('btn-export').disabled = state.points.size < 4;
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

    toast('Exported — run it through cv/experiments/calibrate.py');
}

// ---------------------------------------------------------------- init

function init() {
    $('input-image').addEventListener('change', (e) => {
        const file = e.target.files?.[0];
        if (file) loadImage(file);
    });

    $('canvas').addEventListener('click', (e) => {
        if (!state.selected) return toast('Pick a landmark from the list first.', true);

        const canvas = $('canvas');
        const rect = canvas.getBoundingClientRect();
        // The canvas is displayed scaled down; convert back to source pixels.
        const x = (e.clientX - rect.left) * (canvas.width / rect.width);
        const y = (e.clientY - rect.top) * (canvas.height / rect.height);

        state.points.set(state.selected, [x, y]);
        state.selected = null;
        $('hint').textContent = 'Pick the next landmark.';
        renderAll();
    });

    $('btn-clear').addEventListener('click', () => {
        if (state.points.size && !confirm('Remove all placed points?')) return;
        state.points.clear();
        state.selected = null;
        renderAll();
    });

    $('btn-export').addEventListener('click', exportJson);

    for (const id of ['input-length', 'input-width']) {
        $(id).addEventListener('input', () => {
            if (state.image) renderAll();
        });
    }

    renderLandmarkList();
}

init();

// Exposed for the browser-driven tests.
window._calib = { state, renderAll, pitchToPixelHomography };
