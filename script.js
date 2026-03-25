const canvas = document.getElementById('display');
const ctx = canvas.getContext('2d');

const PITCH_W = 68;
const PITCH_H = 52.5;

const GOAL_CX    = PITCH_W / 2;
const GOAL_LEFT  = (PITCH_W - 7.32) / 2;
const GOAL_RIGHT = (PITCH_W + 7.32) / 2;

const GOAL_LEFT_N  = GOAL_LEFT  / PITCH_W;
const GOAL_RIGHT_N = GOAL_RIGHT / PITCH_W;

let players = { red: [], blue: [] };
let shooter, keeper, defenderA, defenderB;

let mouse = { x: 0, y: 0, down: false };
let dragging  = null;
let justPressed = false;

let showLineOfSight = false;

let layout = 'landscape';
let pitchRect = { x: 0, y: 0, w: 0, h: 0 };
let lastCanvasW = 0;
let lastCanvasH = 0;


// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
function init() {
    // Red (defence): keeper + 4 defenders
    players.red.push(new Player(new Vector(0.5,  0.08), true,  true));
    players.red.push(new Player(new Vector(0.35, 0.35), false, true));
    players.red.push(new Player(new Vector(0.65, 0.35), false, true));
    players.red.push(new Player(new Vector(0.20, 0.45), false, true));
    players.red.push(new Player(new Vector(0.80, 0.45), false, true));

    // Blue (attack): shooter + 4 teammates
    players.blue.push(new Player(new Vector(0.5,  0.7),  false, false));
    players.blue.push(new Player(new Vector(0.3,  0.6),  false, false));
    players.blue.push(new Player(new Vector(0.7,  0.6),  false, false));
    players.blue.push(new Player(new Vector(0.5,  0.5),  false, false));
    players.blue.push(new Player(new Vector(0.2,  0.8),  false, false));

    keeper    = players.red[0];
    defenderA = players.red[1];
    defenderB = players.red[2];
    shooter   = players.blue[0];

    canvas.addEventListener('mousemove', e => {
        const r = canvas.getBoundingClientRect();
        mouse.x = e.clientX - r.left;
        mouse.y = e.clientY - r.top;
    });
    canvas.addEventListener('mousedown', () => { mouse.down = true; });
    canvas.addEventListener('mouseup',   () => { mouse.down = false; });

    document.getElementById('btn-los').addEventListener('click', function () {
        showLineOfSight = !showLineOfSight;
        this.textContent = showLineOfSight ? 'Hide line of sight' : 'Show line of sight';
        this.classList.toggle('active', showLineOfSight);
    });

    document.getElementById('btn-switch-shooter').addEventListener('click', () => {
        const i = players.blue.indexOf(shooter);
        shooter = players.blue[(i + 1) % players.blue.length];
        updateSliders();
    });

    // Recompute xG whenever any toggle/slider changes
    document.querySelectorAll('.toggle-grid input[type=checkbox], #shot_height')
        .forEach(el => el.addEventListener('change', find_xg));

    initSliders();
    loop();
}


// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------
function loop() {
    maybeResize();
    handleInputs();
    if (dragging) updateSliders();
    find_xg();
    draw();
    requestAnimationFrame(loop);
}


// ---------------------------------------------------------------------------
// Resize / layout
// ---------------------------------------------------------------------------
function maybeResize() {
    const cw = canvas.offsetWidth;
    const ch = canvas.offsetHeight;
    if (cw === lastCanvasW && ch === lastCanvasH) return;

    const newLayout = window.innerWidth >= window.innerHeight ? 'landscape' : 'portrait';
    if (newLayout !== layout) {
        layout = newLayout;
        document.body.classList.toggle('portrait', layout === 'portrait');
    }

    canvas.width  = cw;
    canvas.height = ch;
    lastCanvasW = cw;
    lastCanvasH = ch;

    const margin = 24;
    const pitchAspect  = PITCH_W / PITCH_H;
    const canvasAspect = cw / ch;
    let pw, ph;
    if (canvasAspect > pitchAspect) { ph = ch - margin * 2; pw = ph * pitchAspect; }
    else                            { pw = cw - margin * 2; ph = pw / pitchAspect; }

    pitchRect = { x: (cw - pw) / 2, y: (ch - ph) / 2, w: pw, h: ph };
}


// ---------------------------------------------------------------------------
// Coordinate helpers
// ---------------------------------------------------------------------------
function toScreen(pos) {
    if (layout === 'landscape') {
        return new Vector(pitchRect.x + pos.x * pitchRect.w, pitchRect.y + pos.y * pitchRect.h);
    }
    return new Vector(pitchRect.x + (1 - pos.y) * pitchRect.w, pitchRect.y + pos.x * pitchRect.h);
}

function toWorld(screen) {
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

// pitch metres → screen (shorthand)
function m(mx, my) { return toScreen(new Vector(mx / PITCH_W, my / PITCH_H)); }


// ---------------------------------------------------------------------------
// Input handling
// ---------------------------------------------------------------------------
function handleInputs() {
    if (mouse.down && !justPressed) {
        justPressed = true;
        for (const p of [...players.red, ...players.blue]) {
            if (Vector.dist(toScreen(p.position), mouse) < 14) {
                dragging = p;
                dragging.isDragging = true;
                break;
            }
        }
    }
    if (!mouse.down) {
        if (dragging) { dragging.isDragging = false; dragging = null; }
        justPressed = false;
    }
    if (dragging) {
        dragging.position = toWorld(mouse);
        dragging.position.x = Math.max(0, Math.min(1, dragging.position.x));
        dragging.position.y = Math.max(0, Math.min(1, dragging.position.y));
    }
}


// ---------------------------------------------------------------------------
// Metric calculations (canvas coords → pitch metres)
// ---------------------------------------------------------------------------
function shooterMetres() {
    return { x: shooter.position.x * PITCH_W, y: shooter.position.y * PITCH_H };
}

function calcDistanceToGoal() {
    const s = shooterMetres();
    return Math.hypot(s.x - GOAL_CX, s.y);
}

function calcAngleToGoal() {
    const s = shooterMetres();
    const lx = GOAL_LEFT  - s.x, ly = -s.y;
    const rx = GOAL_RIGHT - s.x, ry = -s.y;
    const dot = lx * rx + ly * ry;
    const magL = Math.hypot(lx, ly), magR = Math.hypot(rx, ry);
    if (magL < 0.01 || magR < 0.01) return 0;
    return Math.acos(Math.max(-1, Math.min(1, dot / (magL * magR)))) * (180 / Math.PI);
}

function nearestDefender() {
    const s = shooter.position;
    return Vector.dist(s, defenderA.position) <= Vector.dist(s, defenderB.position)
        ? defenderA : defenderB;
}

function calcNearestDefenderDist() {
    return Vector.dist(shooter.position, nearestDefender().position) * PITCH_H;
}

function calcDefenderDistToGoalLine() {
    return nearestDefender().position.y * PITCH_H;
}

function calcKeeperDistToGoal() {
    return Math.hypot(keeper.position.x * PITCH_W - GOAL_CX, keeper.position.y * PITCH_H);
}

function calcKeeperAngleCoverage() {
    const s  = shooterMetres();
    const kx = keeper.position.x * PITCH_W;
    const ky = keeper.position.y * PITCH_H;
    const keeperBodyM = 1.0;
    if (ky >= s.y) return 0;
    const projected = [kx - keeperBodyM, kx + keeperBodyM].map(ex => {
        const t = -s.y / (ky - s.y);
        return s.x + (ex - s.x) * t;
    });
    const bL = Math.max(Math.min(...projected), GOAL_LEFT);
    const bR = Math.min(Math.max(...projected), GOAL_RIGHT);
    if (bR <= bL) return 0;
    return Math.min(100, ((bR - bL) / 7.32) * 100);
}


// ---------------------------------------------------------------------------
// Sliders
// ---------------------------------------------------------------------------
function initSliders() {
    updateSliders();

    // Shot height display
    document.getElementById('shot_height').addEventListener('input', function () {
        setDisplay('val-shot-height', this.value, ' m');
    });

    // Distance slider moves the shooter
    document.getElementById('distance_to_goal').addEventListener('input', function () {
        setDisplay('val-distance', this.value, ' m');
        const target = parseFloat(this.value);
        const s = shooterMetres();
        const dx = s.x - GOAL_CX, dy = s.y;
        const cur = Math.hypot(dx, dy);
        if (cur > 0.01) {
            const sc = target / cur;
            shooter.position.x = Math.max(0, Math.min(1, (GOAL_CX + dx * sc) / PITCH_W));
            shooter.position.y = Math.max(0, Math.min(1, (dy * sc) / PITCH_H));
        }
        refreshOtherSliders(['distance_to_goal']);
    });

    // Angle is read-only (derived from pitch position)
    const angleEl = document.getElementById('angle_to_goal');
    angleEl.style.pointerEvents = 'none';
    angleEl.style.opacity = '0.4';

    // Nearest defender distance slider
    document.getElementById('nearest_defender_distance').addEventListener('input', function () {
        setDisplay('val-def-dist', this.value, ' m');
        const target = parseFloat(this.value);
        const def = nearestDefender();
        const sx = shooter.position.x * PITCH_W, sy = shooter.position.y * PITCH_H;
        const dxM = def.position.x * PITCH_W - sx;
        const dyM = def.position.y * PITCH_H - sy;
        const cur = Math.hypot(dxM, dyM);
        if (cur > 0.01) {
            const sc = target / cur;
            def.position.x = Math.max(0, Math.min(1, (sx + dxM * sc) / PITCH_W));
            def.position.y = Math.max(0, Math.min(1, (sy + dyM * sc) / PITCH_H));
        }
        refreshOtherSliders(['nearest_defender_distance']);
    });

    document.getElementById('defender_distance_to_goal_line').addEventListener('input', function () {
        setDisplay('val-def-goal', this.value, ' m');
        nearestDefender().position.y = Math.min(1, parseFloat(this.value) / PITCH_H);
        refreshOtherSliders(['defender_distance_to_goal_line']);
    });

    document.getElementById('keeper_distance_to_goal').addEventListener('input', function () {
        setDisplay('val-keeper-dist', this.value, ' m');
        const target = parseFloat(this.value);
        const kx = keeper.position.x * PITCH_W - GOAL_CX;
        const ky = keeper.position.y * PITCH_H;
        const cur = Math.hypot(kx, ky);
        if (cur > 0.01) {
            const sc = target / cur;
            keeper.position.x = Math.max(0, Math.min(1, (GOAL_CX + kx * sc) / PITCH_W));
            keeper.position.y = Math.max(0, Math.min(1, (ky * sc) / PITCH_H));
        }
        refreshOtherSliders(['keeper_distance_to_goal']);
    });

    // Keeper angle coverage is read-only
    const kacEl = document.getElementById('keeper_angle_coverage');
    kacEl.style.pointerEvents = 'none';
    kacEl.style.opacity = '0.4';
}

function updateSliders() {
    setSliderAndDisplay('distance_to_goal',               calcDistanceToGoal(),         'val-distance',     ' m', 1);
    setSliderAndDisplay('angle_to_goal',                  calcAngleToGoal(),            'val-angle',        '°',  1);
    setSliderAndDisplay('nearest_defender_distance',      calcNearestDefenderDist(),    'val-def-dist',     ' m', 1);
    setSliderAndDisplay('defender_distance_to_goal_line', calcDefenderDistToGoalLine(), 'val-def-goal',     ' m', 1);
    setSliderAndDisplay('keeper_distance_to_goal',        calcKeeperDistToGoal(),       'val-keeper-dist',  ' m', 1);
    setSliderAndDisplay('keeper_angle_coverage',          calcKeeperAngleCoverage(),    'val-keeper-angle', '%',  0);
    // shot_height is user-driven only; just keep display in sync
    setDisplay('val-shot-height', document.getElementById('shot_height').value, ' m');
}

function refreshOtherSliders(skip) {
    const all = [
        ['distance_to_goal',               calcDistanceToGoal,          'val-distance',     ' m', 1],
        ['angle_to_goal',                  calcAngleToGoal,             'val-angle',        '°',  1],
        ['nearest_defender_distance',      calcNearestDefenderDist,     'val-def-dist',     ' m', 1],
        ['defender_distance_to_goal_line', calcDefenderDistToGoalLine,  'val-def-goal',     ' m', 1],
        ['keeper_distance_to_goal',        calcKeeperDistToGoal,        'val-keeper-dist',  ' m', 1],
        ['keeper_angle_coverage',          calcKeeperAngleCoverage,     'val-keeper-angle', '%',  0],
    ];
    for (const [id, fn, display, suffix, dec] of all) {
        if (skip.includes(id)) continue;
        setSliderAndDisplay(id, fn(), display, suffix, dec);
    }
}

function setSliderAndDisplay(id, value, displayId, suffix, decimals) {
    document.getElementById(id).value = value;
    setDisplay(displayId, value, suffix, decimals);
}

function setDisplay(displayId, value, suffix, decimals = 1) {
    document.getElementById(displayId).textContent = parseFloat(value).toFixed(decimals) + suffix;
}


// ---------------------------------------------------------------------------
// Drawing
// ---------------------------------------------------------------------------
function draw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    drawPitch();
    if (showLineOfSight) drawLineOfSight();
    players.red.forEach(drawPlayer);
    players.blue.forEach(drawPlayer);
}

function drawPitch() {
    const { x, y, w, h } = pitchRect;
    ctx.fillStyle = '#2a7a36';
    ctx.fillRect(x, y, w, h);

    for (let i = 0; i < 9; i += 2) {
        const p0 = toScreen(new Vector(0, i / 9));
        const p1 = toScreen(new Vector(1, (i + 1) / 9));
        ctx.fillStyle = 'rgba(0,0,0,0.04)';
        ctx.fillRect(p0.x, p0.y, p1.x - p0.x, p1.y - p0.y);
    }

    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.lineWidth   = 1.5;
    ctx.strokeRect(x, y, w, h);
    line(toScreen(new Vector(0, 1)), toScreen(new Vector(1, 1)));

    const paL = (PITCH_W - 40.32) / 2;
    rect(m(paL, 0), m(paL + 40.32, 16.5));
    const sbL = (PITCH_W - 18.32) / 2;
    rect(m(sbL, 0), m(sbL + 18.32, 5.5));

    const spot = m(PITCH_W / 2, 11);
    ctx.beginPath();
    ctx.arc(spot.x, spot.y, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(255,255,255,0.75)';
    ctx.fill();

    drawPenaltyArc(16.5);
    drawCentreArc();
    drawGoal();
}

function line(a, b) {
    ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
}

function rect(tl, br) {
    ctx.strokeRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
}

function drawPenaltyArc(paDepth) {
    const s = toScreen(new Vector(0.5, 11 / PITCH_H));
    const dy = (paDepth - 11) / PITCH_H;
    const r  = 9.15 / PITCH_H;
    if (Math.abs(dy) >= r) return;
    const ha = Math.acos(dy / r);
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.beginPath();
    if (layout === 'landscape') ctx.arc(s.x, s.y, r * pitchRect.h, Math.PI / 2 + ha, Math.PI / 2 - ha, false);
    else                        ctx.arc(s.x, s.y, r * pitchRect.w, ha, -ha, false);
    ctx.stroke();
}

function drawCentreArc() {
    const cs = toScreen(new Vector(0.5, 1));
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.beginPath();
    if (layout === 'landscape') ctx.arc(cs.x, cs.y, (9.15 / PITCH_H) * pitchRect.h, Math.PI, 0, false);
    else                        ctx.arc(cs.x, cs.y, (9.15 / PITCH_H) * pitchRect.w, Math.PI / 2, -Math.PI / 2, false);
    ctx.stroke();
}

function drawGoal() {
    const gL = (PITCH_W - 7.32) / 2;
    const g0 = m(gL, -2.44), g1 = m(gL + 7.32, 0);
    ctx.fillStyle = 'rgba(255,255,255,0.08)';
    ctx.fillRect(g0.x, g0.y, g1.x - g0.x, g1.y - g0.y);
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.strokeRect(g0.x, g0.y, g1.x - g0.x, g1.y - g0.y);
}

function drawLineOfSight() {
    const sS   = toScreen(shooter.position);
    const postL = toScreen(new Vector(GOAL_LEFT_N,  0));
    const postR = toScreen(new Vector(GOAL_RIGHT_N, 0));

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(sS.x, sS.y); ctx.lineTo(postL.x, postL.y);
    ctx.lineTo(postR.x, postR.y); ctx.closePath();
    ctx.fillStyle   = 'rgba(255,220,50,0.10)'; ctx.fill();
    ctx.strokeStyle = 'rgba(255,220,50,0.55)'; ctx.lineWidth = 1.2;
    ctx.setLineDash([6, 4]); ctx.stroke();
    ctx.restore();

    const kS   = toScreen(keeper.position);
    const kR   = 10;
    const dx   = kS.x - sS.x, dy = kS.y - sS.y;
    const dist = Math.hypot(dx, dy);

    if (dist > kR) {
        const ang    = Math.atan2(dy, dx);
        const spread = Math.asin(Math.min(kR / dist, 1));
        const tanL   = new Vector(Math.cos(ang - spread), Math.sin(ang - spread));
        const tanR   = new Vector(Math.cos(ang + spread), Math.sin(ang + spread));
        const goalY  = postL.y;
        const hitL   = rayToY(sS, tanL, goalY);
        const hitR   = rayToY(sS, tanR, goalY);

        if (hitL && hitR) {
            const gMinX = Math.min(postL.x, postR.x), gMaxX = Math.max(postL.x, postR.x);
            const sL = Math.max(Math.min(hitL.x, hitR.x), gMinX);
            const sR = Math.min(Math.max(hitL.x, hitR.x), gMaxX);
            if (sR > sL) {
                ctx.save();
                ctx.beginPath();
                ctx.moveTo(sS.x, sS.y); ctx.lineTo(hitL.x, goalY); ctx.lineTo(hitR.x, goalY); ctx.closePath();
                ctx.fillStyle = 'rgba(255,80,80,0.13)'; ctx.fill();
                ctx.beginPath(); ctx.moveTo(sL, goalY); ctx.lineTo(sR, goalY);
                ctx.strokeStyle = 'rgba(255,80,80,0.9)'; ctx.lineWidth = 4; ctx.setLineDash([]); ctx.stroke();
                ctx.restore();
            }
        }
    }
}

function rayToY(origin, dir, targetY) {
    if (Math.abs(dir.y) < 1e-6) return null;
    const t = (targetY - origin.y) / dir.y;
    if (t < 0) return null;
    return new Vector(origin.x + dir.x * t, targetY);
}

function drawPlayer(player) {
    const pos = toScreen(player.position);
    const r   = 10 + (player.isDragging ? 4 : 0);
    ctx.fillStyle = player.red ? '#e05252' : '#4a9de0';
    ctx.beginPath(); ctx.arc(pos.x, pos.y, r, 0, Math.PI * 2); ctx.fill();
    if (player.goalkeeper || player === shooter) {
        ctx.fillStyle = '#f0c040';
        ctx.beginPath(); ctx.arc(pos.x, pos.y, 4, 0, Math.PI * 2); ctx.fill();
    }
}


// ---------------------------------------------------------------------------
// xG inference
// ---------------------------------------------------------------------------
const dot2   = (a, b) => a.x * b.x + a.y * b.y;
const hypot2 = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);

const SB_POST_L = { x: 120.0, y: 36.0 };
const SB_POST_R = { x: 120.0, y: 44.0 };

// Canvas normalised → StatsBomb 120×80
function toSB(pos) {
    return { x: (1.0 - pos.y) * 120.0, y: pos.x * 80.0 };
}

function sbAngle(loc) {
    const vL = { x: SB_POST_L.x - loc.x, y: SB_POST_L.y - loc.y };
    const vR = { x: SB_POST_R.x - loc.x, y: SB_POST_R.y - loc.y };
    return Math.acos(
        Math.max(-1, Math.min(1, dot2(vL, vR) / (Math.hypot(vL.x, vL.y) * Math.hypot(vR.x, vR.y) + 1e-9)))
    );
}

function inShotCone(p, ball) {
    const v0 = { x: SB_POST_R.x - ball.x, y: SB_POST_R.y - ball.y };
    const v1 = { x: SB_POST_L.x - ball.x, y: SB_POST_L.y - ball.y };
    const v2 = { x: p.x - ball.x,         y: p.y - ball.y          };
    const d00 = dot2(v0, v0), d01 = dot2(v0, v1), d11 = dot2(v1, v1);
    const d02 = dot2(v0, v2), d12 = dot2(v1, v2);
    const inv = 1.0 / (d00 * d11 - d01 * d01 + 1e-9);
    const u = (d11 * d02 - d01 * d12) * inv;
    const v = (d00 * d12 - d01 * d02) * inv;
    return u >= 0 && v >= 0 && (u + v) < 1;
}

async function find_xg() {
    // Load model once
    if (!window._xgSession) {
        try {
            window._xgSession = await ort.InferenceSession.create('./xg_model6.onnx');
        } catch (e) {
            console.error('Failed to load ONNX model:', e);
            return;
        }
    }

    // --- Ball location in StatsBomb space ---
    const ball = toSB(shooter.position);

    // --- Base geometry ---
    const distanceToGoal = Math.hypot(120.0 - ball.x, 40.0 - ball.y);
    const angleToGoal    = sbAngle(ball);

    // --- Toggle / slider inputs ---
    const isFoot      = document.getElementById('is_foot')?.checked      ? 1 : 0;
    const isHeader    = document.getElementById('is_header')?.checked    ? 1 : 0;
    const underPressure = document.getElementById('under_pressure')?.checked ? 1 : 0;
    const isOpenPlay  = document.getElementById('is_open_play')?.checked  ? 1 : 0;
    const isFreekick  = document.getElementById('is_freekick')?.checked   ? 1 : 0;
    const shotHeight  = parseFloat(document.getElementById('shot_height')?.value ?? '0');

    // --- Goalkeeper ---
    let keeperDistToGoal = 5.0;
    let keeperAngleCover = angleToGoal;
    let keeperOffLine    = 0;

    if (keeper) {
        const k = toSB(keeper.position);
        keeperDistToGoal = Math.hypot(120.0 - k.x, 40.0 - k.y);
        keeperAngleCover = sbAngle(k);
        keeperOffLine    = keeperDistToGoal > 3.0 ? 1 : 0;
    }

    // --- Defenders in cone + weighted pressure ---
    let defendersInCone  = 0;
    let defenderPressure = 0.0;

    for (const opp of players.red.filter(p => !p.goalkeeper)) {
        const p = toSB(opp.position);
        if (inShotCone(p, ball)) {
            defendersInCone++;
            defenderPressure += 1.0 / (Math.hypot(p.x - ball.x, p.y - ball.y) + 1e-9);
        }
    }

    // --- Feature vector (order must match training exactly) ---
    // ["distance_to_goal", "angle_to_goal",
    //  "is_foot", "is_header",
    //  "under_pressure", "is_open_play",
    //  "shot_height",
    //  "keeper_distance_to_goal", "keeper_angle_coverage", "keeper_off_line",
    //  "defenders_in_cone", "defender_pressure"]
    const features = new Float32Array([
        distanceToGoal,
        angleToGoal,
        isFoot,
        isHeader,
        underPressure,
        isOpenPlay,
        shotHeight,
        keeperDistToGoal,
        keeperAngleCover,
        keeperOffLine,
        defendersInCone,
        defenderPressure,
    ]);

    const tensor = new ort.Tensor('float32', features, [1, 12]);

    // --- Inference ---
    try {
// --- 8. Run inference & extract probability ---
        const results = await window._xgSession.run({ float_input: tensor });

        let xg = null;
        for (const name of window._xgSession.outputNames) {
            const out = results[name];
            if (!out?.data) continue;
            
            // We ONLY want the probability array, which will have 2 values [prob_no_goal, prob_goal]
            if (out.data.length >= 2) { 
                xg = Number(out.data[1]); // Grab the probability of a goal
                break; 
            }
        }

        if (xg === null || !Number.isFinite(xg)) {
            console.error('Could not extract probability from ONNX output:', results);
            return;
        }

        document.getElementById('xg-value').textContent = xg.toFixed(3);

    } catch (err) {
        console.error('Inference failed:', err);
    }
}

init();