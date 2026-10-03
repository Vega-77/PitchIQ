"""Fit the soccer pitch to the top-down venue map.

Landmarks were read from the map (circle, halfway, near touchline) and from
single keyframes (goalposts, the bottom penalty box), all in top-down crop
pixels (vtop canvas, y - 350).  Pitch-internal units: centre-circle radii
(R), so the law-of-the-game ratios fix the shape without any metres output.
Unknowns: map->pitch homography P (8) + pitch length Lr and width Wr (in R).
    -> vpitch.npz: P (canvas px -> StatsBomb 120x80), Lr, Wr, landmark resid
"""
import numpy as np
from scipy.optimize import least_squares

GOAL = 0.4       # goal half-width / R  (posts 8 yd apart, circle radius 10 yd)
BOXD = 1.8       # penalty-box depth / R
BOXW = 2.2       # penalty-box half-width / R

obs = []         # (map_xy, fn(Lr, Wr) -> pitch uv in R units, weight)
cx, cy, cr = 314.5, 638.5, 71.2
for a in np.linspace(0, 2 * np.pi, 16, endpoint=False):
    obs.append(((cx + cr * np.cos(a), cy + cr * np.sin(a)),
                lambda L, W, a=a: (L / 2 + np.sin(a), W / 2 + np.cos(a)), 1.0))
obs.append(((cx, cy), lambda L, W: (L / 2, W / 2), 2.0))
for x in range(60, 541, 60):                            # halfway line (u only)
    obs.append(((x, 638.0), lambda L, W: (L / 2, None), 1.0))
for y in range(260, 1041, 100):                         # near touchline (v only)
    obs.append(((41.0, y), lambda L, W: (None, 0.0), 1.0))
obs += [((286.1, 190.7), lambda L, W: (0.0, W / 2 - GOAL), 1.0),
        ((337.4, 208.5), lambda L, W: (0.0, W / 2 + GOAL), 1.0),
        ((303.2, 1101.7), lambda L, W: (L, W / 2 - GOAL), 1.0),
        ((367.1, 1103.0), lambda L, W: (L, W / 2 + GOAL), 1.0),
        ((157.1, 949.3), lambda L, W: (L - BOXD, W / 2 - BOXW), 1.0),
        ((161.2, 1093.7), lambda L, W: (L, W / 2 - BOXW), 1.0)]
X = np.array([o[0] for o in obs], float)


def Hof(p):
    return np.append(p[:8], 1).reshape(3, 3)


def resid(p):
    H = Hof(p); L, W = p[8], p[9]
    q = np.c_[X, np.ones(len(X))].dot(H.T); q = q[:, :2] / q[:, 2:]
    r = []
    for (xy, f, w), (u, v) in zip(obs, q):
        tu, tv = f(L, W)
        r += [w * (u - tu) if tu is not None else 0, w * (v - tv) if tv is not None else 0]
    return np.array(r)


# init: similarity from circle scale (71.2 px per R), u along +y, v along +x
s = 1 / cr
p0 = np.array([0, s, -(cy - 439) * s, s, 0, -41 * s, 0, 0, 878 / cr, 547 / cr])
sol = least_squares(resid, p0, loss='soft_l1', f_scale=0.1)
L, W = sol.x[8], sol.x[9]
e = np.hypot(*sol.fun.reshape(-1, 2).T) * cr          # back in map px
print('length/width %.3f   L=%.2f R  W=%.2f R' % (L / W, L, W))
print('landmark resid (map px): median %.1f  max %.1f' % (np.median(e), e.max()))
for (xy, f, w), ee in zip(obs, e):
    if ee > 6: print('  ', np.round(xy, 1), round(ee, 1))
# canvas px (uncropped vtop canvas) -> StatsBomb 120x80
C = np.array([[1, 0, 0], [0, 1, -350], [0, 0, 1]])
S = np.diag([120 / L, 80 / W, 1])
P = S.dot(Hof(sol.x)).dot(C)
np.savez('vpitch.npz', P=P / P[2, 2], L=L, W=W)
print('saved vpitch.npz')
