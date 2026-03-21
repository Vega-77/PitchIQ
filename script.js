
const canvas = document.getElementById('display');
const ctx = canvas.getContext('2d');

const PITCH_W = 68;
const PITCH_H = 52.5;

const GOAL_CX = PITCH_W / 2;
const GOAL_LEFT = (PITCH_W - 7.32) / 2;
const GOAL_RIGHT = (PITCH_W + 7.32) / 2;

const GOAL_LEFT_N  = GOAL_LEFT / PITCH_W;
const GOAL_RIGHT_N = GOAL_RIGHT / PITCH_W;

let players = { red: [], blue: [] };
let shooter, keeper, defenderA, defenderB;

let mouse = { x: 0, y: 0, down: false };
let dragging = null;
let justPressed = false;

let showLineOfSight = false;

let layout = 'landscape';
let pitchRect = { x: 0, y: 0, w: 0, h: 0 };
let lastCanvasW = 0;
let lastCanvasH = 0;


function init() {
    players.red.push(new Player(new Vector(0.5,  0.08), true,  true));
    players.red.push(new Player(new Vector(0.35, 0.35), false, true));
    players.red.push(new Player(new Vector(0.65, 0.35), false, true));

    players.blue.push(new Player(new Vector(0.5, 0.45), true,  false));
    players.blue.push(new Player(new Vector(0.3, 0.6),  false, false));
    players.blue.push(new Player(new Vector(0.7, 0.6),  false, false));

    keeper = players.red[0];
    defenderA = players.red[1];
    defenderB = players.red[2];
    shooter = players.blue[0];

    canvas.addEventListener('mousemove', e => {
        const r = canvas.getBoundingClientRect();
        mouse.x = e.clientX - r.left;
        mouse.y = e.clientY - r.top;
    });

    canvas.addEventListener('mousedown', () => { mouse.down = true; });
    canvas.addEventListener('mouseup', () => { mouse.down = false; });

    const losBtn = document.getElementById('btn-los');
    losBtn.addEventListener('click', () => {
        showLineOfSight = !showLineOfSight;
        losBtn.textContent = showLineOfSight ? 'Hide line of sight' : 'Show line of sight';
        losBtn.classList.toggle('active', showLineOfSight);
    });

    initSliders();
    loop();
}


function loop() {
    maybeResize();
    handleInputs();
    if (dragging) updateSliders();
    find_xg();
    draw();
    requestAnimationFrame(loop);
}


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
    const pitchAspect = PITCH_W / PITCH_H;
    const canvasAspect = cw / ch;

    let pw, ph;
    if (canvasAspect > pitchAspect) {
        ph = ch - margin * 2;
        pw = ph * pitchAspect;
    } else {
        pw = cw - margin * 2;
        ph = pw / pitchAspect;
    }

    pitchRect = {
        x: (cw - pw) / 2,
        y: (ch - ph) / 2,
        w: pw,
        h: ph,
    };
}


function toScreen(pos) {
    if (layout === 'landscape') {
        return new Vector(
            pitchRect.x + pos.x * pitchRect.w,
            pitchRect.y + pos.y * pitchRect.h,
        );
    }
    return new Vector(
        pitchRect.x + (1 - pos.y) * pitchRect.w,
        pitchRect.y + pos.x * pitchRect.h,
    );
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

function m(mx, my) {
    return toScreen(new Vector(mx / PITCH_W, my / PITCH_H));
}


function handleInputs() {
    if (mouse.down && !justPressed) {
        justPressed = true;
        for (const player of [...players.red, ...players.blue]) {
            const sp = toScreen(player.position);
            if (Vector.dist(sp, mouse) < 14) {
                dragging = player;
                dragging.isDragging = true;
                break;
            }
        }
    }

    if (!mouse.down) {
        if (dragging) {
            dragging.isDragging = false;
            dragging = null;
        }
        justPressed = false;
    }

    if (dragging) {
        dragging.position = toWorld(mouse);
        dragging.position.x = Math.max(0, Math.min(1, dragging.position.x));
        dragging.position.y = Math.max(0, Math.min(1, dragging.position.y));
    }
}


function shooterMetres() {
    return {
        x: shooter.position.x * PITCH_W,
        y: shooter.position.y * PITCH_H,
    };
}

function calcDistanceToGoal() {
    const s = shooterMetres();
    const dx = s.x - GOAL_CX;
    return Math.sqrt(dx * dx + s.y * s.y);
}

function calcAngleToGoal() {
    const s = shooterMetres();
    const lx = GOAL_LEFT - s.x, ly = -s.y;
    const rx = GOAL_RIGHT - s.x, ry = -s.y;
    const dot = lx * rx + ly * ry;
    const magL = Math.sqrt(lx * lx + ly * ly);
    const magR = Math.sqrt(rx * rx + ry * ry);
    if (magL < 0.01 || magR < 0.01) return 0;
    return Math.acos(Math.max(-1, Math.min(1, dot / (magL * magR)))) * (180 / Math.PI);
}

function calcNearestDefenderDist() {
    const s = shooter.position;
    const dA = Vector.dist(s, defenderA.position) * PITCH_H;
    const dB = Vector.dist(s, defenderB.position) * PITCH_H;
    return Math.min(dA, dB);
}

function nearestDefender() {
    const s = shooter.position;
    const dA = Vector.dist(s, defenderA.position);
    const dB = Vector.dist(s, defenderB.position);
    return dA <= dB ? defenderA : defenderB;
}

function calcDefenderDistToGoalLine() {
    return nearestDefender().position.y * PITCH_H;
}

function calcKeeperDistToGoal() {
    const kx = keeper.position.x * PITCH_W - GOAL_CX;
    const ky = keeper.position.y * PITCH_H;
    return Math.sqrt(kx * kx + ky * ky);
}

function calcKeeperAngleCoverage() {
    const s = shooterMetres();
    const kx = keeper.position.x * PITCH_W;
    const ky = keeper.position.y * PITCH_H;
    const keeperBodyM = 1.0;

    if (ky >= s.y) return 0;

    const edges = [kx - keeperBodyM, kx + keeperBodyM];
    const projected = edges.map(ex => {
        const dx = ex - s.x;
        const dy = ky  - s.y;
        const t  = -s.y / dy;
        return s.x + dx * t;
    });

    const blockedL = Math.max(Math.min(projected[0], projected[1]), GOAL_LEFT);
    const blockedR = Math.min(Math.max(projected[0], projected[1]), GOAL_RIGHT);
    if (blockedR <= blockedL) return 0;

    return Math.min(100, ((blockedR - blockedL) / 7.32) * 100);
}


function initSliders() {
    updateSliders();

    document.getElementById('distance_to_goal').addEventListener('input', function () {
        setDisplay('val-distance', this.value, ' m');
        const targetDist = parseFloat(this.value);
        const sx = shooter.position.x * PITCH_W;
        const sy = shooter.position.y * PITCH_H;
        const dx = sx - GOAL_CX;
        const dy = sy;
        const currentDist = Math.sqrt(dx * dx + dy * dy);
        if (currentDist > 0.01) {
            const scale = targetDist / currentDist;
            shooter.position.x = Math.max(0, Math.min(1, (GOAL_CX + dx * scale) / PITCH_W));
            shooter.position.y = Math.max(0, Math.min(1, (dy     * scale)       / PITCH_H));
        }
        refreshOtherSliders(['distance_to_goal']);
    });

    const angleEl = document.getElementById('angle_to_goal');
    angleEl.style.pointerEvents = 'none';
    angleEl.style.opacity       = '0.4';

    document.getElementById('nearest_defender_distance').addEventListener('input', function () {
        setDisplay('val-def-dist', this.value, ' m');
        const targetDistM  = parseFloat(this.value);
        const def = nearestDefender();
        const sx = shooter.position.x * PITCH_W;
        const sy = shooter.position.y * PITCH_H;
        const dxM = def.position.x * PITCH_W - sx;
        const dyM = def.position.y * PITCH_H - sy;
        const currentDistM = Math.sqrt(dxM * dxM + dyM * dyM);
        if (currentDistM > 0.01) {
            const scale = targetDistM / currentDistM;
            def.position.x = Math.max(0, Math.min(1, (sx + dxM * scale) / PITCH_W));
            def.position.y = Math.max(0, Math.min(1, (sy + dyM * scale) / PITCH_H));
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
        const targetDist  = parseFloat(this.value);
        const kx = keeper.position.x * PITCH_W - GOAL_CX;
        const ky = keeper.position.y * PITCH_H;
        const currentDist = Math.sqrt(kx * kx + ky * ky);
        if (currentDist > 0.01) {
            const scale = targetDist / currentDist;
            keeper.position.x = Math.max(0, Math.min(1, (GOAL_CX + kx * scale) / PITCH_W));
            keeper.position.y = Math.max(0, Math.min(1, (ky      * scale)       / PITCH_H));
        }
        refreshOtherSliders(['keeper_distance_to_goal']);
    });

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
    document.getElementById(displayId).textContent =
        parseFloat(value).toFixed(decimals) + suffix;
}


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

    for (let i = 0; i < 9; i++) {
        if (i % 2 !== 0) continue;
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
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
}

function rect(tl, br) {
    ctx.strokeRect(tl.x, tl.y, br.x - tl.x, br.y - tl.y);
}

function drawPenaltyArc(paDepth) {
    const spotScreen = toScreen(new Vector(0.5, 11 / PITCH_H));
    const dy = (paDepth - 11) / PITCH_H;
    const r  = 9.15 / PITCH_H;
    if (Math.abs(dy) >= r) return;
    const halfAngle = Math.acos(dy / r);
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.beginPath();
    if (layout === 'landscape') {
        ctx.arc(spotScreen.x, spotScreen.y, r * pitchRect.h, Math.PI / 2 + halfAngle, Math.PI / 2 - halfAngle, false);
    } else {
        ctx.arc(spotScreen.x, spotScreen.y, r * pitchRect.w, halfAngle, -halfAngle, false);
    }
    ctx.stroke();
}

function drawCentreArc() {
    const cs = toScreen(new Vector(0.5, 1));
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.beginPath();
    if (layout === 'landscape') {
        ctx.arc(cs.x, cs.y, (9.15 / PITCH_H) * pitchRect.h, Math.PI, 0, false);
    } else {
        ctx.arc(cs.x, cs.y, (9.15 / PITCH_H) * pitchRect.w, Math.PI / 2, -Math.PI / 2, false);
    }
    ctx.stroke();
}

function drawGoal() {
    const gL = (PITCH_W - 7.32) / 2;
    const g0 = m(gL, -2.44);
    const g1 = m(gL + 7.32, 0);
    ctx.fillStyle = 'rgba(255,255,255,0.08)';
    ctx.fillRect(g0.x, g0.y, g1.x - g0.x, g1.y - g0.y);
    ctx.strokeStyle = 'rgba(255,255,255,0.75)';
    ctx.strokeRect(g0.x, g0.y, g1.x - g0.x, g1.y - g0.y);
}

function drawLineOfSight() {
    const shooterS = toScreen(shooter.position);
    const postL    = toScreen(new Vector(GOAL_LEFT_N,  0));
    const postR    = toScreen(new Vector(GOAL_RIGHT_N, 0));

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(shooterS.x, shooterS.y);
    ctx.lineTo(postL.x, postL.y);
    ctx.lineTo(postR.x, postR.y);
    ctx.closePath();
    ctx.fillStyle   = 'rgba(255, 220, 50, 0.10)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(255, 220, 50, 0.55)';
    ctx.lineWidth   = 1.2;
    ctx.setLineDash([6, 4]);
    ctx.stroke();
    ctx.restore();

    const keeperS = toScreen(keeper.position);
    const keeperR = 10;
    const dx   = keeperS.x - shooterS.x;
    const dy   = keeperS.y - shooterS.y;
    const dist = Math.sqrt(dx * dx + dy * dy);

    if (dist > keeperR) {
        const angle  = Math.atan2(dy, dx);
        const spread = Math.asin(Math.min(keeperR / dist, 1));
        const tanL   = new Vector(Math.cos(angle - spread), Math.sin(angle - spread));
        const tanR   = new Vector(Math.cos(angle + spread), Math.sin(angle + spread));

        const goalLineY = postL.y;
        const hitL = rayToY(shooterS, tanL, goalLineY);
        const hitR = rayToY(shooterS, tanR, goalLineY);

        if (hitL && hitR) {
            const gMinX   = Math.min(postL.x, postR.x);
            const gMaxX   = Math.max(postL.x, postR.x);
            const shadowL = Math.max(Math.min(hitL.x, hitR.x), gMinX);
            const shadowR = Math.min(Math.max(hitL.x, hitR.x), gMaxX);

            if (shadowR > shadowL) {
                ctx.save();

                ctx.beginPath();
                ctx.moveTo(shooterS.x, shooterS.y);
                ctx.lineTo(hitL.x, goalLineY);
                ctx.lineTo(hitR.x, goalLineY);
                ctx.closePath();
                ctx.fillStyle = 'rgba(255, 80, 80, 0.13)';
                ctx.fill();

                ctx.beginPath();
                ctx.moveTo(shadowL, goalLineY);
                ctx.lineTo(shadowR, goalLineY);
                ctx.strokeStyle = 'rgba(255, 80, 80, 0.9)';
                ctx.lineWidth   = 4;
                ctx.setLineDash([]);
                ctx.stroke();

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
    const r = 10 + (player.isDragging ? 4 : 0);

    ctx.fillStyle = player.red ? '#e05252' : '#4a9de0';
    ctx.beginPath();
    ctx.arc(pos.x, pos.y, r, 0, Math.PI * 2);
    ctx.fill();

    if (player.goalkeeper) {
        ctx.fillStyle = '#f0c040';
        ctx.beginPath();
        ctx.arc(pos.x, pos.y, 4, 0, Math.PI * 2);
        ctx.fill();
    }
}

async function find_xg(){
    if (!window._xgSession) {
        try {
            window._xgSession = await ort.InferenceSession.create('./xg_model.onnx');
        } catch (e) {
            console.error('Failed to load model:', e);
            return;
        }
    }

    const features = new Float32Array([
        calcDistanceToGoal(),
        calcAngleToGoal() * (Math.PI / 180),
        document.getElementById('is_foot').checked        ? 1 : 0,
        document.getElementById('under_pressure').checked ? 1 : 0,
        document.getElementById('is_penalty').checked     ? 1 : 0,
        document.getElementById('is_freekick').checked    ? 1 : 0,
        calcNearestDefenderDist(),
        calcDefenderDistToGoalLine(),
        calcKeeperDistToGoal(),
        calcKeeperAngleCoverage() / 100 * Math.PI,
    ]);

    const tensor  = new ort.Tensor('float32', features, [1, 10]);
    const results = await window._xgSession.run({ float_input: tensor });

    console.log('Output keys:', Object.keys(results));

    // const xg      = results.probabilities.data[1];

    // document.getElementById('xg-value').textContent = xg.toFixed(3);

}


init();
