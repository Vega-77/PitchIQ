"""Score ball positions against the Flight Lab marks (test only).

Truth per frame inside a marked flight: the ground track through the take-off
spot, any points under the ball, and the landing spot, linear in time.

  calibration  your spot vs the tracker's flat reading on the frames where the
               ball really is on the grass (kick take-offs, landings)
  flights      per frame error (SB units) of the flat reading, the label-free
               3D chains and the older label-tuned 3D output
  direction    angle between your take-off->landing and each method's
"""
import glob
import json
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def load_truth():
    F = [json.load(open(p)) for p in glob.glob(os.path.join(HERE, 'flightlabels', 'flights', '*.json'))]
    return [f for f in F if not f.get('deleted')]


def track(f):
    P = []
    if f.get('p0'):
        P.append((f['f0'], f['p0']['x'], f['p0']['y']))
    P += [(q['f'], q['x'], q['y']) for q in f.get('pts', [])]
    if f.get('p1'):
        P.append((f['f1'], f['p1']['x'], f['p1']['y']))
    return np.array(sorted(P)) if P else np.zeros((0, 3))


def main():
    fl = load_truth()
    z = np.load(os.path.join(HERE, 'vid1_ballsb.npz'))
    A0 = int(z['abs'][0])
    flat = np.c_[z['x'], z['y']]; has = z['has'].astype(bool)
    meths = {'flat reading': (flat, has)}
    for name, fn in (('3D label-free', 'vid1_ball3d2.npz'), ('3D label-tuned', 'vid1_ball3d.npz')):
        B = np.load(os.path.join(HERE, fn))
        xy = np.c_[B['x'], B['y']]
        air = B['state'] == 'air'
        # a 3D method uses its air position in flight frames and the flat reading elsewhere
        meths[name] = (np.where(air[:, None], xy, flat), has | (air & np.isfinite(xy[:, 0])))
        meths[name + ' (air frames)'] = (xy, air & np.isfinite(xy[:, 0]))

    def near(i, r=2):
        """flat reading at abs i, or the nearest seen frame within r."""
        for d in range(r + 1):
            for j in (i - d, i + d):
                k = j - A0
                if 0 <= k < len(has) and has[k]:
                    return flat[k]
        return None

    print('== calibration: your spot vs the tracker where the ball is on the grass (SB units)')
    cal = []
    for f in fl:
        for key, fr, ok in (('p0', f['f0'], f['k0'] == 'kick'), ('p1', f['f1'], f['k1'] == 'lands')):
            if ok and f.get(key):
                q = near(fr)
                if q is not None:
                    cal.append((f['id'], key, q[0] - f[key]['x'], q[1] - f[key]['y']))
    d = np.array([[c[2], c[3]] for c in cal])
    e = np.hypot(d[:, 0], d[:, 1])
    print('  n %d  median %.1f  p75 %.1f  max %.1f   mean offset (x %+.1f, y %+.1f)' % (
        len(e), np.median(e), np.percentile(e, 75), e.max(), d[:, 0].mean(), d[:, 1].mean()))

    print('\n== in-flight frames (between your take-off and landing spots)')
    rows = {m: [] for m in meths}
    per = []
    for f in fl:
        T = track(f)
        if len(T) < 2:
            continue
        fr = np.arange(int(T[0, 0]) + 1, int(T[-1, 0]))
        if not len(fr):
            continue
        tx, ty = np.interp(fr, T[:, 0], T[:, 1]), np.interp(fr, T[:, 0], T[:, 2])
        k = fr - A0
        ok = (k >= 0) & (k < len(has))
        rec = dict(id=f['id'], dur=(f['f1'] - f['f0']) / 30, h=f.get('h', ''), len=np.hypot(*(T[-1, 1:] - T[0, 1:])))
        for m, (xy, v) in meths.items():
            kk = k[ok]; sel = v[kk]
            if not sel.any():
                continue
            err = np.hypot(xy[kk[sel], 0] - tx[ok][sel], xy[kk[sel], 1] - ty[ok][sel])
            rows[m].append(err)
            rec[m] = (float(np.median(err)), int(sel.sum()), int(len(kk)))
        per.append(rec)
    for m, R in rows.items():
        if R:
            a = np.concatenate(R)
            print('  %-28s flights %2d  frames %4d   median %5.1f  p75 %5.1f  p90 %5.1f' % (
                m, len(R), len(a), np.median(a), np.percentile(a, 75), np.percentile(a, 90)))

    print('\n== same frames only: flights the label-free 3D calls air')
    both = [r for r in per if '3D label-free (air frames)' in r and 'flat reading' in r]
    for r in both:
        pass
    fa = [r['flat reading'][0] for r in both]; fb = [r['3D label-free (air frames)'][0] for r in both]
    print('  flights %d: 3D better on %d, flat better on %d; median of per-flight medians flat %.1f vs 3D %.1f' % (
        len(both), sum(b < a for a, b in zip(fa, fb)), sum(b >= a for a, b in zip(fa, fb)), np.median(fa), np.median(fb)))

    print('\n== direction (take-off -> landing), degrees off yours')
    for m in ('flat reading', '3D label-free'):
        xy, v = meths[m]
        ang = []
        for f in fl:
            if not (f.get('p0') and f.get('p1')):
                continue
            ks = [kk for kk in range(f['f0'] - A0, f['f1'] - A0 + 1) if 0 <= kk < len(v) and v[kk]]
            if len(ks) < 4:
                continue
            a, b = xy[ks[0]], xy[ks[-1]]
            u = np.array([f['p1']['x'] - f['p0']['x'], f['p1']['y'] - f['p0']['y']]); w = b - a
            if np.hypot(*u) < 3:
                continue
            c = np.dot(u, w) / max(np.hypot(*u) * np.hypot(*w), 1e-9)
            ang.append(np.degrees(np.arccos(np.clip(c, -1, 1))))
        ang = np.array(ang)
        print('  %-16s flights %2d  median %4.0f   within 30: %d   reversed (>120): %d' % (
            m, len(ang), np.median(ang), (ang <= 30).sum(), (ang > 120).sum()))

    print('\n== per flight (median SB error in flight; frames used / frames)')
    print('  %-6s %4s %5s %-6s  %-18s %-18s' % ('id', 'dur', 'len', 'height', 'flat', '3D label-free air'))
    for r in sorted(per, key=lambda r: int(r['id'].lstrip('u'))):
        g = lambda m: ('%5.1f (%d/%d)' % r[m]) if m in r else '-'
        print('  %-6s %4.1f %5.1f %-6s  %-18s %-18s' % (r['id'], r['dur'], r['len'], r['h'] or '-', g('flat reading'), g('3D label-free (air frames)')))
    json.dump(per, open(os.path.join(HERE, 'flightscore.json'), 'w'), indent=1, default=float)


if __name__ == '__main__':
    main()
