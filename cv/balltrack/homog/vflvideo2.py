"""Chain check video: old per-flight arcs (magenta) against the linked-chain
fit (cyan), inferred take-off / landing beyond the seen stretch in yellow.

    python vflvideo2.py [tag]  -> chains_changed.webm (a flight changed direction)
                                  chains_rest.webm
"""
import os
import sys
import time

import cv2
import numpy as np

import vflight as V
import vdrag as D
import vflchain as Ch

tag = sys.argv[1] if len(sys.argv) > 1 else 'vid1'
HERE = V.HERE
STEP = 3
PAD = 30
MW, MH = 768, 432; SC = MW / 1920.
ZR, INSET = 72, 256
W, H = MW + INSET, MH
MAPX, MAPY, MS = MW + 38, INSET + 34, 1.5
CYAN, ORANGE, GREEN, GREY, WHITE, RED = (224, 195, 79), (44, 158, 245), (0, 255, 0), (150, 150, 150), (255, 255, 255), (95, 100, 224)
MAG, YEL = (200, 80, 220), (60, 230, 250)
LABC = {'air': CYAN, 'ground': (115, 191, 111), 'hidden': (133, 119, 107), 'none': GREY}


def radial(P0, P1):
    return np.sign(np.hypot(*P1[:2]) - np.hypot(*P0[:2]))


def extension(c, which, n=20):
    """Inferred path from the ground to the first node (or last node to ground)."""
    nodes, ts, ks = Ch.unpack(c['x'], c['m'])
    m = c['m']
    for w, t, _ in c['ends']:
        if w != which:
            continue
        if which == 'take-off':
            v = Ch.seg_vel(nodes[0], nodes[1], (ts[1] - ts[0]) / V.FPS, ks[0], 0.0)
            s = np.linspace(0, (ts[0] - t) / V.FPS, n)
            P = np.c_[nodes[0, 0] - v[0] * s, nodes[0, 1] - v[1] * s, nodes[0, 2] - v[2] * s - 0.5 * V.G * s * s]
            return P[::-1], t, ts[0]
        Tm = (ts[m] - ts[m - 1]) / V.FPS
        v = Ch.seg_vel(nodes[m - 1], nodes[m], Tm, ks[m - 1], Tm)
        s = np.linspace(0, (t - ts[m]) / V.FPS, n)
        P = np.c_[nodes[m, 0] + v[0] * s, nodes[m, 1] + v[1] * s, nodes[m, 2] + v[2] * s - 0.5 * V.G * s * s]
        return P, ts[m], t
    return None


def main():
    import vfeats
    tr = V.Track(tag)
    A0 = int(tr.abs[0])
    T = np.load(os.path.join(HERE, 'airtruth.npz'))
    truth = dict(zip(T['abs'].tolist(), T['truth'].tolist()))
    chains = list(np.load(os.path.join(HERE, '%s_chains.npy' % tag), allow_pickle=True))
    fx = {(f['a'], f['b']): f for f in np.load(os.path.join(HERE, '%s_fixed.npy' % tag), allow_pickle=True)}
    changed, rest = [], []
    no = 0
    for c in chains:
        nodes, ts, ks = Ch.unpack(c['x'], c['m'])
        c['old'] = [fx[ab]['th_old'] for ab in c['flights']]
        flips = []
        for j, th in enumerate(c['old']):
            no += 1
            P = D.drag(th, np.array([th[4], th[4] + th[5]]))
            flips.append(radial(P[0], P[1]) != radial(nodes[j], nodes[j + 1]))
        c['nos'] = list(range(no - c['m'] + 1, no + 1))
        c['flips'] = flips
        (changed if any(flips) else rest).append(c)
    print('%d chains, %d with a flight that changed direction (%d flights)' % (
        len(chains), len(changed), sum(sum(c['flips']) for c in chains)))
    cap = cv2.VideoCapture(vfeats.VIDEO)
    for name, sel in (('changed', changed), ('rest', rest)):
        p = os.path.join(HERE, 'chains_%s.webm' % name)
        vw = cv2.VideoWriter(p, cv2.VideoWriter_fourcc(*'VP80'), 30 // STEP, (W, H))
        t0 = time.time(); nfr = 0
        for c in sel:
            nfr += segment(cap, vw, tr, c, no, truth, A0)
        vw.release()
        print('%s: %d chains, %d frames, %.1f MB, %.0f s' % (p, len(sel), nfr, os.path.getsize(p) / 1e6, time.time() - t0), flush=True)


def segment(cap, vw, tr, c, nfl, truth, A0):
    nodes, ts, ks = Ch.unpack(c['x'], c['m'])
    m = c['m']
    pre, post = extension(c, 'take-off'), extension(c, 'landing')
    t_start = pre[1] if pre else ts[0]
    t_end = post[2] if post else ts[m]
    lo = max(0, int(t_start) - PAD); hi = min(len(tr.abs) - 1, int(np.ceil(t_end)) + PAD)
    tt = np.linspace(ts[0], ts[m], 30 * m)
    arc = Ch.chain_eval(tr, c['x'], m, tt)
    olds = []
    for (a, b), th in zip(c['flights'], c['old']):
        olds.append(D.drag(th, th[4] + np.linspace(0, th[5], 30)))
    cap.set(cv2.CAP_PROP_POS_FRAMES, A0 + lo)
    last = None; n = 0
    sbl = lambda P: tr.to_sb(P[:, :2])
    for i in range(lo, hi + 1):
        ok, fr = cap.read()
        if not ok:
            break
        if tr.has[i]:
            last = tr.px[i]
        if (i - lo) % STEP:
            continue
        img = fr.copy()
        ii = lambda P: np.full(len(P), i)
        for P in olds:
            cv2.polylines(img, [np.round(tr.project(ii(P), P)).astype(np.int32)], False, MAG, 2, cv2.LINE_AA)
        G0 = arc.copy(); G0[:, 2] = 0
        cv2.polylines(img, [np.round(tr.project(ii(G0), G0)).astype(np.int32)], False, GREY, 2, cv2.LINE_AA)
        cv2.polylines(img, [np.round(tr.project(ii(arc), arc)).astype(np.int32)], False, CYAN, 3, cv2.LINE_AA)
        for e in (pre, post):
            if e:
                cv2.polylines(img, [np.round(tr.project(ii(e[0]), e[0])).astype(np.int32)], False, YEL, 3, cv2.LINE_AA)
        for q in nodes:
            cv2.circle(img, tuple(np.round(tr.project(np.array([i]), q[None])[0]).astype(int)), 7, WHITE, 2, cv2.LINE_AA)
        # the model ball now
        P = None
        if ts[0] <= i <= ts[m]:
            P = Ch.chain_eval(tr, c['x'], m, np.array([i]))[0]
        elif pre and pre[1] <= i < ts[0]:
            k = int(round((i - pre[1]) / max(ts[0] - pre[1], 1e-6) * (len(pre[0]) - 1))); P = pre[0][k]
        elif post and ts[m] < i <= post[2]:
            k = int(round((i - ts[m]) / max(post[2] - ts[m], 1e-6) * (len(post[0]) - 1))); P = post[0][k]
        if P is not None:
            b = tr.project(np.array([i]), P[None])[0]
            g = tr.project(np.array([i]), np.r_[P[:2], 0][None])[0]
            cv2.line(img, tuple(np.round(b).astype(int)), tuple(np.round(g).astype(int)), ORANGE, 2, cv2.LINE_AA)
            cv2.circle(img, tuple(np.round(b).astype(int)), 9, ORANGE, -1, cv2.LINE_AA)
        if tr.has[i]:
            cv2.circle(img, tuple(np.round(tr.px[i]).astype(int)), 26, GREEN, 2, cv2.LINE_AA)
        out = np.zeros((H, W, 3), np.uint8); out[:] = (28, 22, 18)
        out[:, :MW] = cv2.resize(img, (MW, MH), interpolation=cv2.INTER_AREA)
        if last is not None:
            cx, cy = int(np.clip(last[0], ZR, 1920 - ZR)), int(np.clip(last[1], ZR, 1080 - ZR))
            out[:INSET, MW:] = cv2.resize(img[cy - ZR:cy + ZR, cx - ZR:cx + ZR], (INSET, INSET), interpolation=cv2.INTER_CUBIC)
        x0, y0 = MAPX, MAPY
        cv2.rectangle(out, (x0, y0), (x0 + int(120 * MS), y0 + int(80 * MS)), (60, 110, 60), -1)
        cv2.rectangle(out, (x0, y0), (x0 + int(120 * MS), y0 + int(80 * MS)), WHITE, 1)
        cv2.line(out, (x0 + int(60 * MS), y0), (x0 + int(60 * MS), y0 + int(80 * MS)), WHITE, 1)
        mp = lambda q: (int(x0 + np.clip(q[0], -4, 124) * MS), int(y0 + (80 - np.clip(q[1], -4, 84)) * MS))
        poly = lambda P, col, w: cv2.polylines(out, [np.array([mp(q) for q in sbl(P)], np.int32)], False, col, w, cv2.LINE_AA)
        for Po in olds:
            poly(Po, MAG, 1)
        poly(arc, CYAN, 2)
        for e in (pre, post):
            if e:
                poly(e[0], YEL, 2)
        if P is not None:
            cv2.circle(out, mp(sbl(P[None])[0]), 4, ORANGE, -1, cv2.LINE_AA)
        cv2.putText(out, 'magenta old  cyan new  yellow inferred', (MW + 6, y0 + int(80 * MS) + 16), 0, 0.38, (200, 200, 200), 1, cv2.LINE_AA)
        # text
        ab = A0 + i
        lab = truth.get(ab, 'none')
        seg = [j for j in range(m) if ts[j] <= i <= ts[j + 1]]
        head = 'flights %s' % ', '.join(str(k) for k in c['nos'])
        if any(c['flips']):
            head += '   direction changed: %s' % ', '.join(str(k) for k, f in zip(c['nos'], c['flips']) if f)
        cv2.rectangle(out, (0, 0), (MW, 26), (20, 16, 12), -1)
        cv2.putText(out, head, (8, 18), 0, 0.52, WHITE, 1, cv2.LINE_AA)
        cv2.rectangle(out, (0, MH - 30), (MW, MH), (20, 16, 12), -1)
        cv2.putText(out, 'label: %s' % ('not visible' if lab == 'hidden' else lab), (8, MH - 10), 0, 0.55, LABC.get(lab, GREY), 2, cv2.LINE_AA)
        if P is not None:
            det = 'flight %d  h %.2f' % (c['nos'][seg[0]], P[2] / V.R_CH) if seg else 'inferred  h %.2f' % (P[2] / V.R_CH)
        else:
            det = '-'
        cv2.putText(out, det, (230, MH - 10), 0, 0.55, CYAN if P is not None else WHITE, 1, cv2.LINE_AA)
        cv2.putText(out, 'frame %d' % ab, (MW - 130, MH - 10), 0, 0.55, WHITE, 1, cv2.LINE_AA)
        vw.write(out); n += 1
    return n


if __name__ == '__main__':
    main()
