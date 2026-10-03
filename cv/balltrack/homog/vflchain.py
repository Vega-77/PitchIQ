"""Flights that hand over to each other (a bounce or a touch) fitted as one
chain through shared 3D nodes.  Node j has a free time (near the take-off,
hand-over or landing frame), ground position and height >= 0; between
nodes the ball follows a drag arc
(k per segment), so consecutive flights always meet.  Where a chain starts
or ends in the air, its arc is run on to the ground: the inferred take-off
or landing.

    python vflchain.py [tag]  -> <tag>_chains.npy
"""
import os
import sys

import numpy as np
from scipy.optimize import least_squares

import vflight as V
import vdrag as D

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
HERE = V.HERE
EXT = 1.5
G = V.G


def E(k, s):
    return (1 - np.exp(-k * s)) / k


def seg_pos(P0, P1, T, k, s):
    """Position at time s in a segment from node P0 to node P1 (u, v, z) taking T."""
    k = max(k, 1e-4)
    f = E(k, s) / E(k, T)
    vz = (P1[2] - P0[2] + G * T / k) / E(k, T) - G / k
    z = P0[2] + (vz + G / k) * E(k, s) - G * s / k
    return np.c_[P0[0] + (P1[0] - P0[0]) * f, P0[1] + (P1[1] - P0[1]) * f, z]


def seg_vel(P0, P1, T, k, s):
    k = max(k, 1e-4)
    e = np.exp(-k * s)
    vz = (P1[2] - P0[2] + G * T / k) / E(k, T) - G / k
    return np.r_[(P1[:2] - P0[:2]) / E(k, T) * e, (vz + G / k) * e - G / k]


def unpack(x, m):
    nodes = x[:3 * (m + 1)].reshape(m + 1, 3)
    ts = x[3 * (m + 1):4 * (m + 1)]
    ks = x[4 * (m + 1):]
    return nodes, ts, ks


def chain_eval(tr, x, m, frames):
    """Positions at track frames; node times ts are in track frames.  Before the
    first node / after the last the ball rolls on at the end speed."""
    nodes, ts, ks = unpack(x, m)
    out = np.zeros((len(frames), 3))
    tf = np.asarray(frames, float)
    for j in range(m):
        T = max((ts[j + 1] - ts[j]) / V.FPS, 1e-3)
        sel = (tf >= ts[j]) & (tf <= ts[j + 1])
        if sel.any():
            out[sel] = seg_pos(nodes[j], nodes[j + 1], T, ks[j], (tf[sel] - ts[j]) / V.FPS)
    T0 = max((ts[1] - ts[0]) / V.FPS, 1e-3); Tm = max((ts[m] - ts[m - 1]) / V.FPS, 1e-3)
    b = tf < ts[0]
    if b.any():
        v = seg_vel(nodes[0], nodes[1], T0, ks[0], 0.0)
        out[b] = np.c_[nodes[0, 0] + v[0] * (tf[b] - ts[0]) / V.FPS, nodes[0, 1] + v[1] * (tf[b] - ts[0]) / V.FPS,
                       np.full(b.sum(), nodes[0, 2])]
    b = tf > ts[m]
    if b.any():
        v = seg_vel(nodes[m - 1], nodes[m], Tm, ks[m - 1], Tm)
        out[b] = np.c_[nodes[m, 0] + v[0] * (tf[b] - ts[m]) / V.FPS, nodes[m, 1] + v[1] * (tf[b] - ts[m]) / V.FPS,
                       np.full(b.sum(), nodes[m, 2])]
    return out


def ray_point(tr, i, z):
    r = tr.M[i].dot([tr.px[i, 0], tr.px[i, 1], 1.0])
    return r[:2] / r[2] * (1 - z)


def fit_chain(tr, lo_f, hi_f, t_init, t_lo, t_hi, inits):
    """inits: list of (nodes (m+1, 3), ks (m,)) starting points."""
    m = len(t_init) - 1
    idx = np.arange(lo_f, hi_f + 1); idx = idx[tr.has[idx]]
    obs = tr.px[idx]

    def res(x):
        r = ((tr.project(idx, chain_eval(tr, x, m, idx)) - obs) / V.SIG).ravel()
        ts = x[3 * (m + 1):4 * (m + 1)]
        return np.r_[r, 10 * np.maximum(0, 3 - np.diff(ts))]      # keep nodes >= 3 frames apart
    lo = np.r_[np.tile([-np.inf, -np.inf, 0.0], m + 1), t_lo, np.zeros(m)]
    hi = np.r_[np.tile([np.inf, np.inf, 3.0], m + 1), t_hi, np.full(m, 1.5)]
    best = None
    for nodes, ks in inits:
        x0 = np.clip(np.r_[np.ravel(nodes), t_init, ks], lo + 1e-6, hi - 1e-6)
        try:
            s = least_squares(res, x0, bounds=(lo, hi), loss='soft_l1', f_scale=2.0, max_nfev=100 * len(x0))
        except Exception:
            continue
        if best is None or s.cost < best.cost:
            best = s
    x = best.x
    e = np.hypot(*(tr.project(idx, chain_eval(tr, x, m, idx)) - obs).T)
    return x, e, idx


def ground_hit(P, v, direction):
    """Run the (drag-free, short) arc from P with velocity v to z = 0,
    backwards (direction -1) or forwards (+1).  Returns (time s >= 0, uv)."""
    z, vz = P[2], v[2] * direction
    # z + vz s - G s^2 / 2 = 0 (backwards: velocity reversed, gravity same sign)
    s = (vz + np.sqrt(max(vz * vz + 2 * G * z, 0))) / G
    s = min(s, EXT)
    return s, P[:2] + v[:2] * direction * s


def main():
    tr = V.Track(tag); V.ground_all(tr)
    fx = list(np.load(os.path.join(HERE, '%s_fixed.npy' % tag), allow_pickle=True))
    fx.sort(key=lambda f: f['a'])
    # chains of flights that hand over
    chains = [[fx[0]]]
    for f in fx[1:]:
        if f['a'] <= chains[-1][-1]['b'] + 1:
            chains[-1].append(f)
        else:
            chains.append([f])
    out = []
    print('chain  flights  err(old->new)  node heights (circle radii)          inferred ends')
    for c in chains:
        m = len(c)
        # node times: take-off of the first flight, each hand-over frame, landing of the last
        t_init = [c[0]['a'] + c[0]['th_old'][4] * V.FPS] + [f['b'] for f in c[:-1]] +                  [c[-1]['a'] + (c[-1]['th_old'][4] + c[-1]['th_old'][5]) * V.FPS]
        t_lo = [c[0]['a'] - EXT * V.FPS] + [t - 6 for t in t_init[1:-1]] + [c[-1]['a'] + 3]
        t_hi = [c[0]['b'] - 3] + [t + 6 for t in t_init[1:-1]] + [c[-1]['b'] + EXT * V.FPS]
        t_init = np.clip(t_init, np.add(t_lo, 0.5), np.subtract(t_hi, 0.5))
        # starting nodes from the old per-flight fits (each node from the flight it starts / ends)
        def at(f, t):
            return D.drag(f['th_old'], np.array([(t - f['a']) / V.FPS]))[0]
        n_old = np.array([at(c[0], t_init[0])] + [0.5 * (at(c[j], t_init[j + 1]) + at(c[j + 1], t_init[j + 1]))
                                                   for j in range(m - 1)] + [at(c[-1], t_init[-1])])
        n_old[:, 2] = np.maximum(n_old[:, 2], 0)
        k_old = np.array([f['th_old'][6] for f in c])
        inits = [(n_old, k_old)]
        for dz in (0.15, 0.4):
            for which in ('first', 'last', 'inner'):
                nn = n_old.copy()
                j = [0] if which == 'first' else [m] if which == 'last' else list(range(1, m))
                if not j:
                    continue
                for q in j:          # lift node q along its ray (same pixel, nearer the camera)
                    w = 1 - nn[q, 2]
                    nn[q, :2] *= (1 - nn[q, 2] - dz) / w if w > 0 else 1
                    nn[q, 2] += dz
                inits.append((nn, k_old))
        lo_f = max(0, int(np.floor(min(t_lo[0], c[0]['a']))))
        hi_f = min(len(tr.abs) - 1, int(np.ceil(max(t_hi[-1], c[-1]['b']))))
        lo_f, hi_f = c[0]['a'], c[-1]['b']
        x, e, idx = fit_chain(tr, lo_f, hi_f, t_init, t_lo, t_hi, inits)
        nodes, ts, ks = unpack(x, m)
        old_e = []
        for f in c:
            ii = np.arange(f['a'], f['b'] + 1); ii = ii[tr.has[ii]]
            old_e.append(np.hypot(*(tr.project(ii, D.drag(f['th_old'], (ii - f['a']) / V.FPS)) - tr.px[ii]).T))
        old_e = np.concatenate(old_e)
        tq = lambda v: float(np.sqrt(np.mean(np.sort(v ** 2)[:max(3, int(0.8 * len(v)))])))
        ends = []
        if nodes[0, 2] > 0.03:
            v = seg_vel(nodes[0], nodes[1], (ts[1] - ts[0]) / V.FPS, ks[0], 0.0)
            s_, uv = ground_hit(nodes[0], v, -1)
            ends.append(('take-off', ts[0] - s_ * V.FPS, tr.to_sb(uv[None])[0]))
        if nodes[-1, 2] > 0.03:
            Tm = (ts[-1] - ts[-2]) / V.FPS
            v = seg_vel(nodes[-2], nodes[-1], Tm, ks[-1], Tm)
            s_, uv = ground_hit(nodes[-1], v, +1)
            ends.append(('landing', ts[-1] + s_ * V.FPS, tr.to_sb(uv[None])[0]))
        out.append(dict(x=x, m=m, err=tq(e), err_old=tq(old_e), ends=ends, lo=lo_f, hi=hi_f,
                        flights=[(f['a'], f['b']) for f in c]))
        print('%5d  %3d      %5.1f -> %5.1f   %-36s %s' % (
            tr.abs[lo_f], m, tq(old_e), tq(e), ' '.join('%.2f' % (z / V.R_CH) for z in nodes[:, 2]),
            '; '.join('%s at (%.0f,%.0f), %.1f s %s' % (w, p[0], p[1], abs(t - ts[0 if w == 'take-off' else -1]) / V.FPS,
                                                     'before first node' if w == 'take-off' else 'after last node')
                      for w, t, p in ends)))
    np.save(os.path.join(HERE, '%s_chains.npy' % tag), np.array(out, dtype=object), allow_pickle=True)
    allo = np.array([c['err_old'] for c in out]); alln = np.array([c['err'] for c in out])
    print('chains %d; error median old %.1f new %.1f px; worse by >1 px: %d' % (
        len(out), np.median(allo), np.median(alln), (alln > allo + 1).sum()))


if __name__ == '__main__':
    main()
