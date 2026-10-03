"""Flights v3, set without the Air Lab labels (they are test only):

  gates   the pre-label limits (fit error 3 px, apex 0.08 CH, roll 2.5x / 4 px)
          on the drag model's trimmed error
  1 hops  a flight whose arc rises less than RISE_MIN px in the image is a
          ground hop: the flat reading is already right to within its height
  2 ends  every node (take-off, hand-over, landing) is ON THE GROUND unless
          lifting it pays for itself (BIC) and shows RISE_MIN px of height
  3 bounce at a ground node between two flights the horizontal velocity may
          lose some speed but not turn or speed up much
  4 cap   horizontal speed above VMAX is penalised

    python vflchain2.py [tag] -> <tag>_chains2.npy, <tag>_ball3d2.npz
"""
import os
import sys

import numpy as np
from scipy.optimize import least_squares

import vflight as V
import vdrag as D
import vflchain as Ch
import vroll as R

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
HERE = V.HERE
SIG = V.SIG                    # tracker noise, px
RISE_MIN = 3 * SIG             # px: visible height an arc must show
GATE = dict(err=3.0, apex=0.08, roll_ratio=2.5, roll_min=4.0)
BOUNCE_KEEP = (0.5, 1.1)       # outgoing / incoming horizontal speed at a bounce
BOUNCE_TURN = np.radians(30)
EXT = 1.5
LOST = 6                       # frames unseen beyond an end: the ball is lost there


def is_air(arc_e, roll_e):
    """The arc must beat a slowing roll by 2.5x AND by 2 sigma (pre-label gate)."""
    return roll_e >= max(GATE['roll_ratio'] * arc_e, arc_e + 2 * SIG)


def sb_per_ch(tr):
    """SB units per camera height near the pitch centre (for the speed cap)."""
    c = np.linalg.solve(tr.A, [60, 40, 1]); c = c[:2] / c[2]
    d = tr.to_sb(np.array([c, c + [0.01, 0]]))
    return np.linalg.norm(d[1] - d[0]) / 0.01


def select_flights(S, C, onpitch):
    e = S[:, C['d_trim']]
    ok = ((e <= GATE['err']) & (S[:, C['d_apex']] >= GATE['apex'])
          & (S[:, C['r_trim']] >= np.maximum(GATE['roll_ratio'] * e, GATE['roll_min'])) & onpitch)
    idx = np.where(ok)[0]
    a = S[idx, C['a']]; b = S[idx, C['b']]
    w = S[idx, C['n']] - 0.5 * e[idx]
    o = np.argsort(b); idx, a, b, w = idx[o], a[o], b[o], w[o]
    p = np.searchsorted(b, a, side='right')
    best = np.zeros(len(idx) + 1)
    for j in range(len(idx)):
        best[j + 1] = max(best[j], best[p[j]] + w[j])
    ch = []; j = len(idx)
    while j > 0:
        if best[j] == best[j - 1]:
            j -= 1
        else:
            ch.append(idx[j - 1]); j = p[j - 1]
    return ch[::-1]


def bend_px(tr, P, frame):
    """How far the image path bends off the straight line between its ends
    (camera held at one frame): a rolling ball's path is straight, so this is
    the visible height of the arc, however far along the ray it sits."""
    fr = int(np.clip(round(frame), 0, len(tr.abs) - 1))
    q = tr.project(np.full(len(P), fr), P)
    d = q[-1] - q[0]; L = np.hypot(*d)
    if L < 1e-6:
        return float(np.max(np.hypot(*(q - q[0]).T)))
    return float(np.max(np.abs(d[0] * (q[:, 1] - q[0, 1]) - d[1] * (q[:, 0] - q[0, 0])) / L))


def rise_px(tr, P0, P1, T, k, t0):
    s = np.linspace(0, T, 21)
    return bend_px(tr, Ch.seg_pos(P0, P1, T, k, s), t0 + T * V.FPS / 2)


class Chain:
    def __init__(self, tr, flights, vmax):
        self.tr, self.fl, self.m = tr, flights, len(flights)
        self.vmax = vmax
        lo, hi = flights[0]['a'], flights[-1]['b']
        idx = np.arange(lo, hi + 1)
        self.idx = idx[tr.has[idx]]
        self.obs = tr.px[self.idx]
        m = self.m
        th0, th1 = flights[0]['th'], flights[-1]['th']
        t_init = [flights[0]['a'] + th0[4] * V.FPS] + [f['b'] for f in flights[:-1]] + \
                 [flights[-1]['a'] + (th1[4] + th1[5]) * V.FPS]
        # 2: an end may leave the seen stretch (and the ground) only where the ball is lost
        n = len(tr.abs)
        lost0 = not tr.has[max(0, lo - LOST):lo].any()
        lost1 = not tr.has[hi + 1:min(n, hi + 1 + LOST)].any()
        self.open = np.r_[lost0, np.ones(m - 1, bool), lost1]
        self.t_lo = np.r_[lo - (EXT * V.FPS if lost0 else 2), [t - 6 for t in t_init[1:-1]], flights[-1]['a'] + 3]
        self.t_hi = np.r_[flights[0]['b'] - 3, [t + 6 for t in t_init[1:-1]], hi + (EXT * V.FPS if lost1 else 2)]
        self.t_init = np.clip(t_init, self.t_lo + 0.5, self.t_hi - 0.5)

        def at(f, t):
            return D.drag(f['th'], np.array([(t - f['a']) / V.FPS]))[0]
        n0 = np.array([at(flights[0], self.t_init[0])] +
                      [0.5 * (at(flights[j], self.t_init[j + 1]) + at(flights[j + 1], self.t_init[j + 1])) for j in range(m - 1)] +
                      [at(flights[-1], self.t_init[-1])])
        self.n_init = n0
        self.k_init = np.array([f['th'][6] for f in flights])

    def residuals(self, x, free):
        tr, m = self.tr, self.m
        r = ((tr.project(self.idx, Ch.chain_eval(tr, x, m, self.idx)) - self.obs) / SIG).ravel()
        nodes, ts, ks = Ch.unpack(x, m)
        extra = [10 * np.maximum(0, 3 - np.diff(ts))]
        vs = []
        for j in range(m):
            T = max((ts[j + 1] - ts[j]) / V.FPS, 1e-3)
            v0 = Ch.seg_vel(nodes[j], nodes[j + 1], T, ks[j], 0.0)
            v1 = Ch.seg_vel(nodes[j], nodes[j + 1], T, ks[j], T)
            vs.append((v0, v1))
            sp = np.hypot(*v0[:2]) * self.vmax[1]         # SB units / s
            extra.append([np.maximum(0, sp - self.vmax[0]) / 2.0])
        for j in range(1, m):                             # 3: bounces keep their line
            if free[j]:
                continue
            vin, vout = vs[j - 1][1][:2], vs[j][0][:2]
            si, so = np.hypot(*vin), np.hypot(*vout)
            if si < 1e-6:
                continue
            ratio = so / si
            ang = np.arccos(np.clip(vin.dot(vout) / max(si * so, 1e-9), -1, 1))
            extra.append([5 * max(0, ratio - BOUNCE_KEEP[1]), 5 * max(0, BOUNCE_KEEP[0] - ratio),
                          5 * max(0, ang - BOUNCE_TURN)])
        return np.r_[r, np.concatenate([np.ravel(e) for e in extra])]

    def fit(self, free):
        m = self.m
        zhi = np.where(free, 3.0, 1e-9)
        lo = np.r_[np.ravel(np.c_[np.full(m + 1, -np.inf), np.full(m + 1, -np.inf), np.zeros(m + 1)]), self.t_lo, np.zeros(m)]
        hi = np.r_[np.ravel(np.c_[np.full(m + 1, np.inf), np.full(m + 1, np.inf), zhi]), self.t_hi, np.full(m, 1.5)]
        best = None
        starts = [0.0, 0.15, 0.4]
        for dz in starts:
            nn = self.n_init.copy()
            for q in range(m + 1):
                z = min(zhi[q] * 0.5, nn[q, 2] + dz) if free[q] else 0.0
                w = 1 - nn[q, 2]
                if w > 0:                                 # slide along the ray to height z
                    nn[q, :2] *= (1 - z) / w
                nn[q, 2] = z
            x0 = np.clip(np.r_[np.ravel(nn), self.t_init, self.k_init], lo + 1e-12, np.maximum(hi - 1e-12, lo + 2e-12))
            try:
                s = least_squares(lambda x: self.residuals(x, free), x0, bounds=(lo, hi), loss='soft_l1',
                                  f_scale=2.0, max_nfev=80 * len(x0))
            except Exception:
                continue
            if best is None or s.cost < best.cost:
                best = s
        return best

    def rises(self, x):
        nodes, ts, ks = Ch.unpack(x, self.m)
        return [rise_px(self.tr, nodes[j], nodes[j + 1], (ts[j + 1] - ts[j]) / V.FPS, ks[j], ts[j]) for j in range(self.m)]


def fit_chain(tr, flights, vmax):
    """Greedy: all nodes on the ground; lift one node at a time while BIC improves
    and the lifted node shows RISE_MIN px of height."""
    c = Chain(tr, flights, vmax)
    free = np.zeros(c.m + 1, bool)
    best = c.fit(free)
    n = 2 * len(c.idx)
    while True:
        cand = None
        for q in np.where(~free & c.open)[0]:
            f2 = free.copy(); f2[q] = True
            s = c.fit(f2)
            if s is None:
                continue
            gain = 2 * (best.cost - s.cost)               # soft_l1 "chi-square" drop
            r = c.rises(s.x)
            lift = min(r[j] for j in (q - 1, q) if 0 <= j < c.m)
            if gain > np.log(n) and lift >= RISE_MIN and (cand is None or s.cost < cand[1].cost):
                cand = (q, s)
        if cand is None:
            break
        free[cand[0]] = True; best = cand[1]
    return c, best, free


def main():
    tr = V.Track(tag); V.ground_all(tr)
    n = len(tr.abs)
    Z = np.load(os.path.join(HERE, '%s_scan2.npz' % tag))
    S, C = Z['S'], {c: i for i, c in enumerate(Z['cols'].tolist())}
    S = S[np.isfinite(S[:, C['d_rms']])]
    TH = S[:, [C[c] for c in 'u0 v0 vu vv tau Tf k'.split()]]
    P0 = np.array([D.drag(th, np.array([th[4]]))[0, :2] for th in TH])
    P1 = np.array([D.drag(th, np.array([th[4] + th[5]]))[0, :2] for th in TH])
    sb0, sb1 = tr.to_sb(P0), tr.to_sb(P1)
    onp = np.all([(sb[:, 0] > -10) & (sb[:, 0] < 130) & (sb[:, 1] > -10) & (sb[:, 1] < 90) for sb in (sb0, sb1)], 0)
    rows = select_flights(S, C, onp)
    fl = [dict(a=int(S[r, C['a']]), b=int(S[r, C['b']]), th=TH[r], err=S[r, C['d_trim']]) for r in rows]
    print('pre-label gates: %d flights' % len(fl))
    # 1: single-flight rise test before chaining
    keep = []
    for f in fl:
        f['roll'] = R.droll(tr, f['a'], f['b'])[0]
        if is_air(f['err'], f['roll']):
            keep.append(f)
    print('after the slowing-roll test: %d flights' % len(keep))
    fl = keep
    vmax = (40.0, sb_per_ch(tr))            # 40 SB units/s: a very hard kick
    chains = [[fl[0]]] if fl else []
    for f in fl[1:]:
        if f['a'] <= chains[-1][-1]['b'] + 1:
            chains[-1].append(f)
        else:
            chains.append([f])
    out = []
    for cf in chains:
        c, s, free = fit_chain(tr, cf, vmax)
        x = s.x
        nodes, ts, ks = Ch.unpack(x, c.m)
        e = np.hypot(*(tr.project(c.idx, Ch.chain_eval(tr, x, c.m, c.idx)) - c.obs).T)
        ends = []
        if free[0] and nodes[0, 2] > 0.02:
            v = Ch.seg_vel(nodes[0], nodes[1], (ts[1] - ts[0]) / V.FPS, ks[0], 0.0)
            s_, uv = Ch.ground_hit(nodes[0], v, -1); ends.append(('take-off', ts[0] - s_ * V.FPS, tr.to_sb(uv[None])[0]))
        if free[-1] and nodes[-1, 2] > 0.02:
            Tm = (ts[-1] - ts[-2]) / V.FPS
            v = Ch.seg_vel(nodes[-2], nodes[-1], Tm, ks[-1], Tm)
            s_, uv = Ch.ground_hit(nodes[-1], v, +1); ends.append(('landing', ts[-1] + s_ * V.FPS, tr.to_sb(uv[None])[0]))
        seg_air = []
        for j in range(c.m):
            a_, b_ = int(np.ceil(ts[j])), int(np.floor(ts[j + 1]))
            ii = np.arange(max(a_, 0), min(b_, n - 1) + 1); ii = ii[tr.has[ii]]
            if len(ii) < 4:
                seg_air.append(bool(free[j] or free[j + 1])); continue    # unseen: trust the chain
            ea = R.trim(np.hypot(*(tr.project(ii, Ch.chain_eval(tr, x, c.m, ii)) - tr.px[ii]).T))
            seg_air.append(is_air(ea, R.droll(tr, a_, b_)[0]))
        out.append(dict(x=x, m=c.m, free=free, seg_air=seg_air, err=float(np.sqrt(np.mean(np.sort(e ** 2)[:max(3, int(0.8 * len(e)))]))),
                        ends=ends, rises=c.rises(x), flights=[(f['a'], f['b']) for f in cf],
                        old=[f['th'] for f in cf]))
        sb = tr.to_sb(nodes[:, :2])
        print('%5d  %d flights  err %4.1f  air %s  nodes %s' % (tr.abs[cf[0]['a']], c.m, out[-1]['err'],
              ''.join('A' if q else 'g' for q in seg_air),
              '  '.join('(%.0f,%.0f%s)' % (p[0], p[1], ' h%.2f' % (z / V.R_CH) if fr_ else '')
                        for p, z, fr_ in zip(sb, nodes[:, 2], free))))
    np.save(os.path.join(HERE, '%s_chains2.npy' % tag), np.array(out, dtype=object), allow_pickle=True)
    # per-frame state and position
    state = np.where(tr.has, 'ground', 'none').astype('<U8')
    xyz = np.full((n, 3), np.nan)
    g = np.array([tr.ground(i) if tr.has[i] else [np.nan, np.nan] for i in range(n)])
    xyz[:, :2] = np.where(np.isfinite(g), g, np.nan); xyz[tr.has, 2] = 0
    for c in out:
        nodes, ts, ks = Ch.unpack(c['x'], c['m'])
        for j in range(c['m']):
            if not c['seg_air'][j]:
                continue
            fr = np.arange(max(0, int(np.ceil(ts[j]))), min(n, int(np.floor(ts[j + 1])) + 1))
            xyz[fr] = Ch.chain_eval(tr, c['x'], c['m'], fr)
            state[fr] = 'air'
        for w, t, _ in c['ends']:                         # inferred parts
            if not c['seg_air'][0 if w == 'take-off' else -1]:
                continue
            rng = np.arange(max(0, int(np.ceil(t))), min(n, int(np.ceil(ts[0])))) if w == 'take-off' else \
                np.arange(max(0, int(np.floor(ts[-1])) + 1), min(n, int(np.floor(t)) + 1))
            state[rng] = 'air'
    sbx = np.full((n, 2), np.nan); m_ = np.isfinite(xyz[:, 0]); sbx[m_] = tr.to_sb(xyz[m_, :2])
    np.savez(os.path.join(HERE, '%s_ball3d2.npz' % tag), abs=tr.abs, x=sbx[:, 0], y=sbx[:, 1], z=xyz[:, 2], state=state)
    print('chains %d, flights %d, lifted nodes %d of %d, air frames %d' % (
        len(out), sum(c['m'] for c in out), sum(c['free'].sum() for c in out), sum(c['m'] + 1 for c in out), (state == 'air').sum()))


if __name__ == '__main__':
    main()
