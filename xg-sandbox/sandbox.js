// The xG sandbox: drag players around an attacking half and watch the shot
// quality change.
//
// This file is the canvas and the controls. Everything about the model itself —
// the coordinate conversion, the feature vector, the ONNX session — lives in
// xg-model.js, so the one part that has to stay in step with the trained model
// is not tangled up with drawing code.

import { Vector, Player } from './geometry.js?v=72';
import { predictXg, buildFeatures, FEATURE_ORDER } from './xg-model.js?v=72';
import { PRESETS, fromMetres } from './presets.js?v=72';

const canvas = document.getElementById('display');
const ctx = canvas.getContext('2d');

// Only the attacking half is drawn: a full pitch would put the shooter in a
// corner of the canvas at half the scale for no extra information.
const WIDTH_M = 68;
const HALF_LENGTH_M = 52.5;

const GOAL_WIDTH_M = 7.32;
const GOAL_CENTRE_M = WIDTH_M / 2;
const GOAL_LEFT_M = (WIDTH_M - GOAL_WIDTH_M) / 2;
const GOAL_RIGHT_M = (WIDTH_M + GOAL_WIDTH_M) / 2;

const PENALTY_AREA_WIDTH_M = 40.32;
const PENALTY_AREA_DEPTH_M = 16.5;
const GOAL_AREA_WIDTH_M = 18.32;
const GOAL_AREA_DEPTH_M = 5.5;
const PENALTY_SPOT_M = 11;
const CENTRE_CIRCLE_RADIUS_M = 9.15;

// Taken from the design system in assets/app.css, so the sandbox reads as part
// of the same app rather than as the separate demo it started life as.
const COLOURS = {
    turf: '#2f7d4f',
    lines: 'rgba(255,255,255,0.75)',
    attack: '#4dd6c1',
    defence: '#e0574f',
    marker: '#fbbf24',
    sightline: 'rgba(251,191,36,0.55)',
    sightFill: 'rgba(251,191,36,0.10)',
    blocked: 'rgba(224,87,79,0.9)',
    blockedFill: 'rgba(224,87,79,0.15)',
};

const PLAYER_RADIUS = 10;
const GRAB_RADIUS = 14;

const players = { defence: [], attack: [] };
let shooter;
let keeper;
let defenderA;
let defenderB;

const mouse = { x: 0, y: 0, down: false };
let dragging = null;
let pressHandled = false;
let showLineOfSight = false;

let layout = 'landscape';
let pitchRect = { x: 0, y: 0, w: 0, h: 0 };
let lastCanvasSize = { w: 0, h: 0 };

const byId = (id) => document.getElementById(id);
const setText = (id, value) => { const el = byId(id); if (el) el.textContent = value; };

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

function init() {
    // Defence: keeper plus four outfield players.
    players.defence = [
        new Player(new Vector(0.50, 0.08), 'defence', true),
        new Player(new Vector(0.35, 0.35), 'defence'),
        new Player(new Vector(0.65, 0.35), 'defence'),
        new Player(new Vector(0.20, 0.45), 'defence'),
        new Player(new Vector(0.80, 0.45), 'defence'),
    ];

    // Attack: the shooter plus four teammates.
    players.attack = [
        new Player(new Vector(0.50, 0.70), 'attack'),
        new Player(new Vector(0.30, 0.60), 'attack'),
        new Player(new Vector(0.70, 0.60), 'attack'),
        new Player(new Vector(0.50, 0.50), 'attack'),
        new Player(new Vector(0.20, 0.80), 'attack'),
    ];

    [keeper, defenderA, defenderB] = players.defence;
    [shooter] = players.attack;

    bindCanvas();
    bindButtons();
    bindSliders();
    bindPresets();

    // Open on a shot rather than on the positions above, which are only there
    // to create the players. They put the shooter 37 metres out, which was a
    // reasonable-looking arrangement while `toStatsBomb` was doubling every
    // distance and is a shot worth 0.011 now that it is not — a first screen
    // showing "about 1 in 90" teaches nobody anything about the model.
    applyPreset(PRESETS.find((preset) => preset.id === 'edge-of-the-box'));
    loop();
}

// ---------------------------------------------------------------------------
// Presets
// ---------------------------------------------------------------------------

function bindPresets() {
    const row = byId('presets');
    if (!row) return;

    for (const preset of PRESETS) {
        const button = document.createElement('button');
        button.className = 'btn small preset';
        button.type = 'button';
        button.textContent = preset.name;
        button.title = preset.detail;
        button.addEventListener('click', () => applyPreset(preset));
        row.append(button);
    }
}

/**
 * Put every player where a preset says, and set the toggles with them.
 *
 * All ten, not just the three the model weighs. A preset that moved the shooter
 * and left the previous scenario's defenders standing where they were would
 * produce a number belonging to neither.
 */
function applyPreset(preset) {
    keeper.position = fromMetres(preset.keeper);
    preset.defenders.forEach((spot, i) => {
        players.defence[i + 1].position = fromMetres(spot);
    });

    // The shooter is always attack[0], so a preset also undoes any number of
    // presses of "Switch shooter".
    [shooter] = players.attack;
    shooter.position = fromMetres(preset.shooter);
    preset.attackers.forEach((spot, i) => {
        players.attack[i + 1].position = fromMetres(spot);
    });

    byId('is_foot').checked = preset.shot.isFoot;
    byId('is_header').checked = preset.shot.isHeader;
    byId('under_pressure').checked = preset.shot.underPressure;
    byId('is_open_play').checked = preset.shot.isOpenPlay;

    setText('preset-detail', preset.detail);
    for (const button of document.querySelectorAll('.preset')) {
        button.classList.toggle('active', button.textContent === preset.name);
    }

    refreshReadouts();
}

/** A preset stops describing the pitch the moment somebody drags a player. */
function clearPresetMark() {
    for (const button of document.querySelectorAll('.preset.active')) {
        button.classList.remove('active');
    }
    setText('preset-detail', '');
}

function bindCanvas() {
    canvas.addEventListener('mousemove', (e) => {
        const rect = canvas.getBoundingClientRect();
        mouse.x = e.clientX - rect.left;
        mouse.y = e.clientY - rect.top;
    });
    canvas.addEventListener('mousedown', () => { mouse.down = true; });
    canvas.addEventListener('mouseup', () => { mouse.down = false; });
}

function bindButtons() {
    byId('btn-los').addEventListener('click', function () {
        showLineOfSight = !showLineOfSight;
        this.textContent = showLineOfSight ? 'Hide line of sight' : 'Show line of sight';
        this.classList.toggle('active', showLineOfSight);
    });

    byId('btn-switch-shooter').addEventListener('click', () => {
        const next = (players.attack.indexOf(shooter) + 1) % players.attack.length;
        shooter = players.attack[next];
        refreshReadouts();
    });

    for (const input of document.querySelectorAll('.toggle-grid input[type=checkbox]')) {
        input.addEventListener('change', updateXg);
    }
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

function loop() {
    resizeIfNeeded();
    handleDragging();
    if (dragging) refreshReadouts();
    updateXg();
    draw();
    requestAnimationFrame(loop);
}

function resizeIfNeeded() {
    const w = canvas.offsetWidth;
    const h = canvas.offsetHeight;

    // A canvas with no box yet would give a zero-sized pitch and put every
    // player on top of each other in the corner. Wait for a real layout.
    if (w === 0 || h === 0) return;
    if (w === lastCanvasSize.w && h === lastCanvasSize.h) return;

    const nextLayout = window.innerWidth >= window.innerHeight ? 'landscape' : 'portrait';
    if (nextLayout !== layout) {
        layout = nextLayout;
        document.body.classList.toggle('portrait', layout === 'portrait');
    }

    canvas.width = w;
    canvas.height = h;
    lastCanvasSize = { w, h };

    const margin = 24;
    const pitchAspect = WIDTH_M / HALF_LENGTH_M;
    let pitchW;
    let pitchH;

    if (w / h > pitchAspect) {
        pitchH = h - margin * 2;
        pitchW = pitchH * pitchAspect;
    } else {
        pitchW = w - margin * 2;
        pitchH = pitchW / pitchAspect;
    }

    pitchRect = { x: (w - pitchW) / 2, y: (h - pitchH) / 2, w: pitchW, h: pitchH };
}

// ---------------------------------------------------------------------------
// Coordinates
// ---------------------------------------------------------------------------

/** Normalised pitch position to canvas pixels. Portrait rotates a quarter turn. */
function toScreen(position) {
    if (layout === 'landscape') {
        return new Vector(
            pitchRect.x + position.x * pitchRect.w,
            pitchRect.y + position.y * pitchRect.h,
        );
    }
    return new Vector(
        pitchRect.x + (1 - position.y) * pitchRect.w,
        pitchRect.y + position.x * pitchRect.h,
    );
}

function toPitch(screen) {
    if (layout === 'landscape') {
        return new Vector(
            (screen.x - pitchRect.x) / pitchRect.w,
            (screen.y - pitchRect.y) / pitchRect.h,
        );
    }
    return new Vector(
        (screen.y - pitchRect.y) / pitchRect.h,
        1 - (screen.x - pitchRect.x) / pitchRect.w,
    );
}

/** Metres to canvas pixels, for drawing the markings. */
function metresToScreen(x, y) {
    return toScreen(new Vector(x / WIDTH_M, y / HALF_LENGTH_M));
}

const allPlayers = () => [...players.defence, ...players.attack];

// ---------------------------------------------------------------------------
// Dragging
// ---------------------------------------------------------------------------

function handleDragging() {
    if (mouse.down && !pressHandled) {
        pressHandled = true;
        for (const player of allPlayers()) {
            if (Vector.dist(toScreen(player.position), mouse) < GRAB_RADIUS) {
                dragging = player;
                dragging.isDragging = true;
                clearPresetMark();
                break;
            }
        }
    }

    if (!mouse.down) {
        if (dragging) dragging.isDragging = false;
        dragging = null;
        pressHandled = false;
    }

    if (dragging) dragging.position = toPitch(mouse).clamped();
}

// ---------------------------------------------------------------------------
// Derived measurements, in metres
// ---------------------------------------------------------------------------

const shooterMetres = () => ({
    x: shooter.position.x * WIDTH_M,
    y: shooter.position.y * HALF_LENGTH_M,
});

function distanceToGoal() {
    const s = shooterMetres();
    return Math.hypot(s.x - GOAL_CENTRE_M, s.y);
}

/** Angle the goalmouth subtends from the shooter, in degrees. */
function angleToGoal() {
    const s = shooterMetres();
    const toLeft = { x: GOAL_LEFT_M - s.x, y: -s.y };
    const toRight = { x: GOAL_RIGHT_M - s.x, y: -s.y };

    const magLeft = Math.hypot(toLeft.x, toLeft.y);
    const magRight = Math.hypot(toRight.x, toRight.y);
    if (magLeft < 0.01 || magRight < 0.01) return 0;

    const cosine = (toLeft.x * toRight.x + toLeft.y * toRight.y) / (magLeft * magRight);
    return Math.acos(Math.max(-1, Math.min(1, cosine))) * (180 / Math.PI);
}

function nearestDefender() {
    const from = shooter.position;
    return Vector.dist(from, defenderA.position) <= Vector.dist(from, defenderB.position)
        ? defenderA
        : defenderB;
}

const nearestDefenderDistance = () =>
    Vector.dist(shooter.position, nearestDefender().position) * HALF_LENGTH_M;

const defenderDistanceToGoalLine = () =>
    nearestDefender().position.y * HALF_LENGTH_M;

const keeperDistanceToGoal = () => Math.hypot(
    keeper.position.x * WIDTH_M - GOAL_CENTRE_M,
    keeper.position.y * HALF_LENGTH_M,
);

/**
 * How much of the goalmouth the keeper's body blocks as seen from the shooter,
 * as a percentage of the goal width.
 */
function keeperAngleCoverage() {
    const s = shooterMetres();
    const keeperX = keeper.position.x * WIDTH_M;
    const keeperY = keeper.position.y * HALF_LENGTH_M;
    const keeperHalfWidthM = 1.0;

    // A keeper behind the shooter blocks nothing.
    if (keeperY >= s.y) return 0;

    const shadow = [keeperX - keeperHalfWidthM, keeperX + keeperHalfWidthM].map((edge) => {
        const t = -s.y / (keeperY - s.y);
        return s.x + (edge - s.x) * t;
    });

    const left = Math.max(Math.min(...shadow), GOAL_LEFT_M);
    const right = Math.min(Math.max(...shadow), GOAL_RIGHT_M);
    if (right <= left) return 0;

    return Math.min(100, ((right - left) / GOAL_WIDTH_M) * 100);
}

// ---------------------------------------------------------------------------
// Sliders
//
// Each row either reports a measurement taken off the pitch, or drives one by
// sliding the relevant player along a line. This table is the only place that
// mapping is written down — it used to be spread over three near-identical
// blocks that had to be kept in step by hand.
// ---------------------------------------------------------------------------

const SLIDERS = [
    {
        id: 'distance_to_goal',
        display: 'val-distance',
        unit: ' m',
        measure: distanceToGoal,
        move: (metres) => {
            shooter.position = moveAlongRay(shooter.position, GOAL_CENTRE_M, 0, metres);
        },
    },
    {
        id: 'angle_to_goal',
        display: 'val-angle',
        unit: '°',
        measure: angleToGoal,
        readOnly: true,
    },
    {
        id: 'nearest_defender_distance',
        display: 'val-def-dist',
        unit: ' m',
        measure: nearestDefenderDistance,
        move: (metres) => {
            const defender = nearestDefender();
            const s = shooterMetres();
            defender.position = moveAlongRay(defender.position, s.x, s.y, metres);
        },
    },
    {
        id: 'defender_distance_to_goal_line',
        display: 'val-def-goal',
        unit: ' m',
        measure: defenderDistanceToGoalLine,
        move: (metres) => {
            nearestDefender().position.y = Math.min(1, metres / HALF_LENGTH_M);
        },
    },
    {
        id: 'keeper_distance_to_goal',
        display: 'val-keeper-dist',
        unit: ' m',
        measure: keeperDistanceToGoal,
        move: (metres) => {
            keeper.position = moveAlongRay(keeper.position, GOAL_CENTRE_M, 0, metres);
        },
    },
    {
        id: 'keeper_angle_coverage',
        display: 'val-keeper-angle',
        unit: '%',
        measure: keeperAngleCoverage,
        readOnly: true,
        decimals: 0,
    },
];

function bindSliders() {
    for (const slider of SLIDERS) {
        const input = byId(slider.id);

        if (slider.readOnly) {
            // Derived from where the players are, so it reports rather than
            // controls. Disabling it says so; the old version left it draggable
            // and simply sprang back.
            input.disabled = true;
            input.title = 'Measured from the pitch — drag the players to change it';
            continue;
        }

        input.addEventListener('input', () => {
            const value = parseFloat(input.value);
            slider.move(value);
            showValue(slider, value);
            refreshReadouts([slider.id]);
            clearPresetMark();
        });
    }

}

function showValue(slider, value) {
    setDisplay(slider.display, value, slider.unit, slider.decimals ?? 1);
}

function setDisplay(displayId, value, unit, decimals) {
    byId(displayId).textContent = parseFloat(value).toFixed(decimals) + unit;
}

/** Put every slider back in line with the pitch, except the one being dragged. */
function refreshReadouts(skipIds = []) {
    for (const slider of SLIDERS) {
        if (skipIds.includes(slider.id)) continue;
        const value = slider.measure();
        byId(slider.id).value = value;
        showValue(slider, value);
    }
}

/**
 * Move a player to `targetDistance` metres from an origin, keeping the
 * direction they were already in. This is what a distance slider does.
 */
function moveAlongRay(position, originX, originY, targetDistance) {
    const dx = position.x * WIDTH_M - originX;
    const dy = position.y * HALF_LENGTH_M - originY;
    const current = Math.hypot(dx, dy);
    if (current <= 0.01) return position;

    const scale = targetDistance / current;
    return new Vector(
        (originX + dx * scale) / WIDTH_M,
        (originY + dy * scale) / HALF_LENGTH_M,
    ).clamped();
}

// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------

function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawPitch();
    if (showLineOfSight) drawLineOfSight();
    for (const player of allPlayers()) drawPlayer(player);
}

function drawPitch() {
    const { x, y, w, h } = pitchRect;

    ctx.fillStyle = COLOURS.turf;
    ctx.fillRect(x, y, w, h);

    // Mower stripes, so the turf does not read as a flat green slab.
    for (let i = 0; i < 9; i += 2) {
        const from = toScreen(new Vector(0, i / 9));
        const to = toScreen(new Vector(1, (i + 1) / 9));
        ctx.fillStyle = 'rgba(0,0,0,0.04)';
        ctx.fillRect(from.x, from.y, to.x - from.x, to.y - from.y);
    }

    ctx.strokeStyle = COLOURS.lines;
    ctx.lineWidth = 1.5;
    ctx.strokeRect(x, y, w, h);
    strokeLine(toScreen(new Vector(0, 1)), toScreen(new Vector(1, 1)));

    const penaltyLeft = (WIDTH_M - PENALTY_AREA_WIDTH_M) / 2;
    strokeRect(
        metresToScreen(penaltyLeft, 0),
        metresToScreen(penaltyLeft + PENALTY_AREA_WIDTH_M, PENALTY_AREA_DEPTH_M),
    );

    const goalAreaLeft = (WIDTH_M - GOAL_AREA_WIDTH_M) / 2;
    strokeRect(
        metresToScreen(goalAreaLeft, 0),
        metresToScreen(goalAreaLeft + GOAL_AREA_WIDTH_M, GOAL_AREA_DEPTH_M),
    );

    const spot = metresToScreen(WIDTH_M / 2, PENALTY_SPOT_M);
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = COLOURS.lines;
    ctx.fill();

    drawPenaltyArc();
    drawCentreArc();
    drawGoal();
}

function strokeLine(a, b) {
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
}

function strokeRect(topLeft, bottomRight) {
    ctx.strokeRect(
        topLeft.x, topLeft.y,
        bottomRight.x - topLeft.x, bottomRight.y - topLeft.y,
    );
}

/** The arc of the penalty-spot circle that pokes out of the penalty area. */
function drawPenaltyArc() {
    const centre = toScreen(new Vector(0.5, PENALTY_SPOT_M / HALF_LENGTH_M));
    const offset = (PENALTY_AREA_DEPTH_M - PENALTY_SPOT_M) / HALF_LENGTH_M;
    const radius = CENTRE_CIRCLE_RADIUS_M / HALF_LENGTH_M;
    if (Math.abs(offset) >= radius) return;

    const halfAngle = Math.acos(offset / radius);
    ctx.strokeStyle = COLOURS.lines;
    ctx.beginPath();
    if (layout === 'landscape') {
        ctx.arc(centre.x, centre.y, radius * pitchRect.h,
            Math.PI / 2 + halfAngle, Math.PI / 2 - halfAngle, false);
    } else {
        ctx.arc(centre.x, centre.y, radius * pitchRect.w, halfAngle, -halfAngle, false);
    }
    ctx.stroke();
}

/** Half the centre circle, on the halfway line at the far edge of the view. */
function drawCentreArc() {
    const centre = toScreen(new Vector(0.5, 1));
    const radius = CENTRE_CIRCLE_RADIUS_M / HALF_LENGTH_M;

    ctx.strokeStyle = COLOURS.lines;
    ctx.beginPath();
    if (layout === 'landscape') {
        ctx.arc(centre.x, centre.y, radius * pitchRect.h, Math.PI, 0, false);
    } else {
        ctx.arc(centre.x, centre.y, radius * pitchRect.w, Math.PI / 2, -Math.PI / 2, false);
    }
    ctx.stroke();
}

function drawGoal() {
    const topLeft = metresToScreen(GOAL_LEFT_M, -2.44);
    const bottomRight = metresToScreen(GOAL_RIGHT_M, 0);
    const w = bottomRight.x - topLeft.x;
    const h = bottomRight.y - topLeft.y;

    ctx.fillStyle = 'rgba(255,255,255,0.08)';
    ctx.fillRect(topLeft.x, topLeft.y, w, h);
    ctx.strokeStyle = COLOURS.lines;
    ctx.strokeRect(topLeft.x, topLeft.y, w, h);
}

/**
 * The shooting angle, and the slice of it the keeper's body takes away — the
 * thing the model is reacting to, made visible.
 */
function drawLineOfSight() {
    const from = toScreen(shooter.position);
    const postLeft = toScreen(new Vector(GOAL_LEFT_M / WIDTH_M, 0));
    const postRight = toScreen(new Vector(GOAL_RIGHT_M / WIDTH_M, 0));

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(postLeft.x, postLeft.y);
    ctx.lineTo(postRight.x, postRight.y);
    ctx.closePath();
    ctx.fillStyle = COLOURS.sightFill;
    ctx.fill();
    ctx.strokeStyle = COLOURS.sightline;
    ctx.lineWidth = 1.2;
    ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.restore();

    drawKeeperShadow(from, postLeft, postRight);
}

function drawKeeperShadow(from, postLeft, postRight) {
    const keeperScreen = toScreen(keeper.position);
    const keeperRadius = 10;

    const dx = keeperScreen.x - from.x;
    const dy = keeperScreen.y - from.y;
    const distance = Math.hypot(dx, dy);
    if (distance <= keeperRadius) return;

    const angle = Math.atan2(dy, dx);
    const spread = Math.asin(Math.min(keeperRadius / distance, 1));
    const goalY = postLeft.y;

    const hitLeft = rayToY(from, angle - spread, goalY);
    const hitRight = rayToY(from, angle + spread, goalY);
    if (!hitLeft || !hitRight) return;

    const goalMin = Math.min(postLeft.x, postRight.x);
    const goalMax = Math.max(postLeft.x, postRight.x);
    const shadowLeft = Math.max(Math.min(hitLeft.x, hitRight.x), goalMin);
    const shadowRight = Math.min(Math.max(hitLeft.x, hitRight.x), goalMax);
    if (shadowRight <= shadowLeft) return;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(from.x, from.y);
    ctx.lineTo(hitLeft.x, goalY);
    ctx.lineTo(hitRight.x, goalY);
    ctx.closePath();
    ctx.fillStyle = COLOURS.blockedFill;
    ctx.fill();

    ctx.beginPath();
    ctx.moveTo(shadowLeft, goalY);
    ctx.lineTo(shadowRight, goalY);
    ctx.strokeStyle = COLOURS.blocked;
    ctx.lineWidth = 4;
    ctx.setLineDash([]);
    ctx.stroke();
    ctx.restore();
}

/** Where a ray leaving `origin` at `angle` crosses the horizontal line y. */
function rayToY(origin, angle, targetY) {
    const dirY = Math.sin(angle);
    if (Math.abs(dirY) < 1e-6) return null;

    const t = (targetY - origin.y) / dirY;
    if (t < 0) return null;

    return new Vector(origin.x + Math.cos(angle) * t, targetY);
}

function drawPlayer(player) {
    const at = toScreen(player.position);
    const radius = PLAYER_RADIUS + (player.isDragging ? 4 : 0);

    ctx.fillStyle = player.isDefending ? COLOURS.defence : COLOURS.attack;
    ctx.beginPath();
    ctx.arc(at.x, at.y, radius, 0, Math.PI * 2);
    ctx.fill();

    // The keeper and the shooter are the two the model weighs most heavily, so
    // they carry a mark rather than being two of ten identical dots.
    if (player.isGoalkeeper || player === shooter) {
        ctx.fillStyle = COLOURS.marker;
        ctx.beginPath();
        ctx.arc(at.x, at.y, 4, 0, Math.PI * 2);
        ctx.fill();
    }
}

// ---------------------------------------------------------------------------
// xG
// ---------------------------------------------------------------------------

/** Everything the model is about to be told, from where the players are. */
function shotSetup() {
    return {
        shooter: shooter.position,
        keeper: keeper.position,
        defenders: players.defence
            .filter((player) => !player.isGoalkeeper)
            .map((player) => player.position),
        shot: {
            isFoot: byId('is_foot').checked,
            isHeader: byId('is_header').checked,
            underPressure: byId('under_pressure').checked,
            isOpenPlay: byId('is_open_play').checked,
        },
    };
}

// One inference at a time, and always the newest one.
//
// An onnxruntime session cannot run twice at once — the second call fails with
// "Session already started" and then the whole session goes to "Session
// mismatch". This loop runs sixty times a second and used to fire a run each
// time, which worked only because the old model answered inside a frame.
// the current model averages five folds and does not, so every frame of a drag failed
// and the readout sat on "—".
//
// Latest-wins rather than a queue: while a run is in flight, later frames
// overwrite `pending`, so the number that lands is the one for where the
// players are now and not for a position the mouse left forty frames ago.
let running = false;
let pending = null;

async function updateXg() {
    const setup = shotSetup();
    // Cheap arithmetic, no model. This can and should keep up with the drag.
    renderFeatures(buildFeatures(setup));

    pending = setup;
    if (running) return;

    running = true;
    try {
        while (pending) {
            const next = pending;
            pending = null;
            const xg = await predictXg(next);
            byId('xg-value').textContent = xg === null ? '—' : xg.toFixed(3);
            setText('xg-odds', xg === null ? 'chance of scoring' : odds(xg));
        }
    } finally {
        running = false;
    }
}

/**
 * The same probability said in a way a person can hold: "about 1 in 5".
 *
 * 0.216 is a number a coach has no feel for, and the feel is the thing worth
 * having — it is what makes "we had four of those" mean something. Rounded to a
 * whole number of shots on purpose, because the model is nowhere near precise
 * enough to distinguish one in six from one in seven.
 */
function odds(xg) {
    if (xg == null) return '';
    if (xg >= 0.995) return 'a certainty';
    if (xg < 0.005) return 'not worth a number';
    const one_in = Math.round(1 / xg);
    return one_in <= 1 ? 'better than even' : `about 1 in ${one_in}`;
}

// ---------------------------------------------------------------------------
// What the model sees
//
// The eleven numbers, as they are handed over. This is the panel that would
// have made two separate bugs obvious the day they were introduced: a distance
// of 45.7 where the slider above it said 20 m, and a keeper feature standing in
// for a keeper who was never found. Both were invisible while the only thing on
// screen was the answer.
//
// Deliberately in the model's own units rather than converted to metres.
// StatsBomb space is 120x80 over a full pitch, so a distance here is about 0.88
// of a metre, and rewriting it into metres would hide exactly the mismatch this
// is for.
// ---------------------------------------------------------------------------

const FEATURE_LABELS = {
    distance_to_goal: 'Distance to goal',
    angle_to_goal: 'Angle of the goalmouth',
    is_foot: 'Struck with the foot',
    is_header: 'Header',
    under_pressure: 'Under pressure',
    is_open_play: 'Open play',
    keeper_distance_to_goal: "Keeper's distance to goal",
    keeper_angle_coverage: 'Angle the keeper covers',
    keeper_off_line: 'Keeper off his line',
    defenders_in_cone: 'Defenders in the lane',
    defender_pressure: 'Weighted defender pressure',
};

// Radians on the wire, degrees on the screen. Nobody reads 0.35 rad.
const IN_DEGREES = new Set(['angle_to_goal', 'keeper_angle_coverage']);
const FLAGS = new Set([
    'is_foot', 'is_header', 'under_pressure', 'is_open_play', 'keeper_off_line',
]);

function featureText(name, value) {
    if (FLAGS.has(name)) return value ? 'yes' : 'no';
    if (IN_DEGREES.has(name)) return `${(value * (180 / Math.PI)).toFixed(1)}°`;
    if (name === 'defenders_in_cone') return String(Math.round(value));
    return value.toFixed(2);
}

let featureRows = null;

function renderFeatures(features) {
    const host = byId('features');
    if (!host) return;

    // Built once and then only written to. This runs inside the animation
    // loop, and rebuilding twelve rows sixty times a second would make the
    // page's own drawing the slowest thing on it.
    if (!featureRows) {
        featureRows = new Map();
        for (const name of FEATURE_ORDER) {
            const row = document.createElement('div');
            row.className = 'feature-row';
            row.innerHTML = '<span class="f-name"></span><span class="f-value num"></span>';
            row.querySelector('.f-name').textContent = FEATURE_LABELS[name] || name;
            host.append(row);
            featureRows.set(name, row.querySelector('.f-value'));
        }
    }

    for (const name of FEATURE_ORDER) {
        featureRows.get(name).textContent = featureText(name, features[name]);
    }
}

init();

// Deliberate test seam, matching calibrate.js and live-tagging/tagging.js.
// The page is a requestAnimationFrame loop, which a headless or backgrounded
// browser suspends outright — without this there is no way to drive a single
// frame and inspect what was drawn. Local rendering state only.
window._sandbox = {
    players,
    /** One iteration of the render loop, for when rAF is not running. */
    frame: () => { resizeIfNeeded(); draw(); return updateXg(); },
    measurements: () => Object.fromEntries(
        SLIDERS.map((slider) => [slider.id, slider.measure()]),
    ),
    // So a preset can be placed and its measurements read back without
    // synthesising a click on a button whose text may change.
    applyPreset,
    /** The eleven numbers, for checking them against the metres on screen. */
    features: () => buildFeatures(shotSetup()),
};
