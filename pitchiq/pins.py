"""Pitch pins: a few clicked landmarks -> the camera position, its aim and the pitch size.

The camera stands still, so every sample's view is one direction field around it
(loop closure gives each sample's rotation from sample 0). A pin is a landmark's
direction in sample 0's camera frame (from the panorama: azimuth, elevation).
Unknowns: camera position C (3, in centre-circle radii), sample 0's rotation R0
(3) and the pitch's length and width (2). Each pin gives 2 equations, so 4
pins solve it and 5+ allow a leave-one-out check. Box, goal-area and penalty-spot
pins carry the scale (their sizes are fixed by the laws); corners and halfway
ends carry L and W.

The names are the site's pin page's (`calibrate/landmarks.js`, `BY_PIN`).
Moved here from the 2026-10-15 scratch `lc/pins.py` unchanged, with
`rescale_chain` from its `pin_cal.py`; `experiments/pincal.py` runs them.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import least_squares

from pitchiq import autocal as A

M_PER_R = 9.15  # metres per centre-circle radius


def landmarks(L: float, W: float) -> dict:
    """Name -> (u, v) on the ground in R. u along the pitch (left goal 0), v from the near touchline."""
    c = W / 2
    out = {
        'centre spot': (L / 2, c),
        'halfway near': (L / 2, 0),
        'halfway far': (L / 2, W),
        'circle near': (L / 2, c - 1),
        'circle far': (L / 2, c + 1),
    }
    for side, g, s in (('left', 0.0, 1.0), ('right', L, -1.0)):
        out[f'{side} corner near'] = (g, 0)
        out[f'{side} corner far'] = (g, W)
        out[f'{side} box near'] = (g + s * A.BOX_D, c - A.BOX_HW)
        out[f'{side} box far'] = (g + s * A.BOX_D, c + A.BOX_HW)
        out[f'{side} box line near'] = (g, c - A.BOX_HW)
        out[f'{side} box line far'] = (g, c + A.BOX_HW)
        out[f'{side} goal area near'] = (g + s * A.GOAL_AREA_D, c - A.GOAL_AREA_HW)
        out[f'{side} goal area far'] = (g + s * A.GOAL_AREA_D, c + A.GOAL_AREA_HW)
        out[f'{side} spot'] = (g + s * A.SPOT_D, c)
    return out


NAMES = tuple(landmarks(12.0, 7.0))


def angles(d):
    """Directions (n, 3) in camera-0 frame -> (azimuth, elevation), the panorama's axes."""
    return np.c_[np.arctan2(d[:, 0], d[:, 2]), np.arctan2(d[:, 1], np.hypot(d[:, 0], d[:, 2]))]


def dirs(az, el):
    """(azimuth, elevation) -> unit directions, the inverse of `angles`."""
    return np.c_[np.sin(az) * np.cos(el), np.sin(el), np.cos(az) * np.cos(el)]


def project(C, R0, L, W, names):
    lm = landmarks(L, W)
    X = np.array([[*lm[n], 0.0] for n in names])
    return angles((X - C) @ R0.T)


def init_pose(names, ang, L=12.0, W=7.0):
    """A start from a plane homography: ground points -> directions, decomposed with K = I."""
    lm = landmarks(L, W)
    g = np.array([lm[n] for n in names])
    az, el = ang[:, 0], ang[:, 1]
    d = dirs(az, el)
    m = d.mean(0)
    m /= np.linalg.norm(m)
    # a virtual camera looking along the mean direction, so the directions become image points
    z = m
    x = np.cross([0, 1.0, 0], z)
    x /= np.linalg.norm(x)
    y = np.cross(z, x)
    Rv = np.stack([x, y, z])
    p = d @ Rv.T
    p = p[:, :2] / p[:, 2:]
    H, _ = A.cv2.findHomography(g.astype(np.float64), p.astype(np.float64), 0)
    h1, h2, h3 = H[:, 0], H[:, 1], H[:, 2]
    s = 2 / (np.linalg.norm(h1) + np.linalg.norm(h2))
    r1, r2, t = h1 * s, h2 * s, h3 * s
    if t[2] < 0:
        r1, r2, t = -r1, -r2, -t
    Rw = np.stack([r1, r2, np.cross(r1, r2)], 1)
    U, _, Vt = np.linalg.svd(Rw)
    Rw = U @ Vt
    if np.linalg.det(Rw) < 0:
        Rw = U @ np.diag([1, 1, -1]) @ Vt
    C = -Rw.T @ t  # world -> virtual camera: x = Rw X + t
    if C[2] < 0:  # the mirror solution: camera under the ground
        C = C * np.array([1, 1, -1])
    # the aim refitted to the clicks from this position (flipping C alone left the old
    # aim behind: WW H2 started 4800 px off and the solve fell into a camera at 0 m)
    Xw = np.c_[g, np.zeros(len(g))] - C
    R0 = A._kabsch(Xw / np.linalg.norm(Xw, axis=1, keepdims=True), d)
    return C, R0


def solve(names, ang, x0=None, L0=12.0, W0=7.0):
    """Pins (names, (n, 2) azimuth/elevation in rad) -> C, R0, L, W, rms residual (rad)."""
    if x0 is None:
        C, R0 = init_pose(names, ang, L0, W0)
    else:
        C, R0 = x0

    def unpack(x):
        return x[:3], A.rodrigues(x[None, 3:6])[0] @ R0, L0 * np.exp(x[6]), W0 * np.exp(x[7])

    def res(x):
        C_, R_, L, W = unpack(x)
        e = project(C_, R_, L, W, names) - ang
        e[:, 0] *= np.cos(ang[:, 1])
        return e.ravel()

    sol = least_squares(
        res,
        np.r_[C, 0, 0, 0, 0, 0],
        x_scale=np.r_[0.1, 0.1, 0.05, 0.01, 0.01, 0.01, 0.01, 0.01],
    )
    C_, R_, L, W = unpack(sol.x)
    return C_, R_, L, W, float(np.sqrt(np.mean(sol.fun**2)))


def leave_one_out(names, ang):
    """Each pin's miss when the others place the pitch (rad); a mis-click stands out."""
    out = {}
    for i, n in enumerate(names):
        keep = [k for k in range(len(names)) if k != i]
        C, R0, L, W, _ = solve([names[k] for k in keep], ang[keep])
        p = project(C, R0, L, W, [n])[0]
        out[n] = float(np.hypot((p[0] - ang[i, 0]) * np.cos(ang[i, 1]), p[1] - ang[i, 1]))
    return out


def solve_joint(sets, L0=None, W0=None, scale=False):
    """Several halves of one game, the camera and pitch shared: sets = [(names, ang), ...]
    each in its own half's frame. Unknowns: C, L, W once and each half's aim; with
    scale, each half's angle scale k too (true angle = k * panorama angle). A
    loop-closed half's zoom is only known to ~1% (WW: the halves' keyframes disagree
    by 0.9%), which a single half soaks into the pitch size (WW 108.4 vs 110.6 m);
    on WW's 29 pins it took the leave-one-out miss p90 from 19.5 px (halves alone)
    and 24.8 (shared, no scale) to 11.7.
    Returns C, [R0 per set], L, W, rms (rad), per-set rms, [k per set]."""
    starts = [solve(n, a) for n, a in sets]
    C = np.mean([s[0] for s in starts], 0)
    L0 = L0 or float(np.mean([s[2] for s in starts]))
    W0 = W0 or float(np.mean([s[3] for s in starts]))
    Rs = [s[1] for s in starts]
    m = len(sets)

    def unpack(x):
        k = np.exp(x[5 + 3 * m :]) if scale else np.ones(m)
        return (
            C + x[:3],
            [A.rodrigues(x[None, 5 + 3 * i : 8 + 3 * i])[0] @ Rs[i] for i in range(m)],
            L0 * np.exp(x[3]),
            W0 * np.exp(x[4]),
            k,
        )

    def res(x):
        C_, R_, L, W, k = unpack(x)
        out = []
        for (names, ang), R0, kk in zip(sets, R_, k):
            aa = ang * kk
            e = project(C_, R0, L, W, names) - aa
            e[:, 0] *= np.cos(aa[:, 1])
            out.append(e.ravel())
        return np.concatenate(out)

    nk = m if scale else 0
    sol = least_squares(
        res,
        np.zeros(5 + 3 * m + nk),
        x_scale=np.r_[0.1, 0.1, 0.05, 0.01, 0.01, [0.01] * (3 * m + nk)],
    )
    C_, R_, L, W, k = unpack(sol.x)
    sizes = np.cumsum([0] + [2 * len(n) for n, _ in sets])
    per = [float(np.sqrt(np.mean(sol.fun[sizes[i] : sizes[i + 1]] ** 2))) for i in range(m)]
    return C_, R_, L, W, float(np.sqrt(np.mean(sol.fun**2))), per, [float(v) for v in k]


def leave_one_out_joint(sets, scale=True):
    """leave_one_out over several halves solved jointly: {(set index, name): miss (rad)}."""
    out = {}
    for i, (names, ang) in enumerate(sets):
        for j, n in enumerate(names):
            keep = [t for t in range(len(names)) if t != j]
            sub = [
                s if h != i else ([names[t] for t in keep], ang[keep]) for h, s in enumerate(sets)
            ]
            C, R, L, W, _, _, k = solve_joint(sub, scale=scale)
            p = project(C, R[i], L, W, [n])[0] / k[i]
            out[(i, n)] = float(np.hypot((p[0] - ang[j, 0]) * np.cos(ang[j, 1]), p[1] - ang[j, 1]))
    return out


def rescale_chain(R, f, k):
    """The loop-closed rotations and focals with every angle from sample 0's axis scaled by k
    (k > 1 widens the panorama): each frame's zoom becomes f / k and its rotation the one
    that best puts a grid of its pixels where the scaled angles say. Returns R', f' and the
    worst grid miss in px (how far the scaling is from a rotation and zoom per frame)."""
    gx, gy = np.meshgrid(np.linspace(0, 1920, 9), np.linspace(0, 1080, 5))
    px = np.c_[gx.ravel(), gy.ravel()]
    R2, f2, worst = np.empty_like(R), np.asarray(f, float) / k, 0.0
    for j in range(len(R)):
        r = A.rays(px, f[j])
        r /= np.linalg.norm(r, axis=1, keepdims=True)
        d = r @ R[j]  # sample 0's frame, as the panorama sees it
        az = np.arctan2(d[:, 0], d[:, 2]) * k
        el = np.arctan2(d[:, 1], np.hypot(d[:, 0], d[:, 2])) * k
        d2 = dirs(az, el)
        r2 = A.rays(px, f2[j])
        r2 /= np.linalg.norm(r2, axis=1, keepdims=True)
        R2[j] = A._kabsch(d2, r2)
        back = A.to_px(d2 @ R2[j].T, f2[j])
        worst = max(worst, float(np.abs(back - px).max()))
    return R2, f2, worst


def describe(C, L, W) -> str:
    return 'camera u %.2f v %.2f h %.3f R (%.1f m up); pitch %.1f x %.1f m' % (
        C[0],
        C[1],
        C[2],
        C[2] * M_PER_R,
        L * M_PER_R,
        W * M_PER_R,
    )
