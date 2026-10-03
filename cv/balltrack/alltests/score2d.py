"""Every 2D version (p2d/<ver>/<tag>.npz) against every labelled 2D test.

    python score2d.py [ver ...]      -> results/2d.json + a table

Tests (all labels are the user's, none used in training):
  truth   Ball Truth db, wm00-17: 'ball' marks right (<= 60 px) / wrong (ring elsewhere) /
          none; 'off' marks ok when no ring.  acc = (right + off ok) / (ball + off).  H1 = wm00-08.
  find    Find the Ball, TEST pool only (pm): same rule, 47 ball + 3 none.
  pass    Pass Truth Bench (pm): 16 ball points (release + arrival of 8 passes), a point is hit
          when a ring is within 60 px on its frame or +-2 frames; a pass is 'carried' when both
          ends are hit.  /8 raw, /7 without p5 (release off-frame).
  fix     Fix Lab (vid3): true-ball points of the 'missed'/'missair'/'other' marks (60 px; map
          points skipped in 2D); coverage of the 'missed'/'missair' spans; and inside the
          'wrong'/'spot' spans the share of frames that repeat the shipped (known bad) ring
          (within 30 px of it) -- lower is better.
"""
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
TOL = 60.0
SX, SY, FIRST = 1920 / 1120, 1080 / 630, 186600


def load(ver, tag, cache={}):
    k = (ver, tag)
    if k not in cache:
        p = os.path.join(HERE, 'p2d', ver, '%s.npz' % tag)
        cache[k] = None
        if os.path.exists(p):
            z = np.load(p); cache[k] = (int(z['abs'][0]), z['x'], z['y'], z['src'])
    return cache[k]


def at(P, f):
    lo, x, y, _ = P; i = f - lo
    if not 0 <= i < len(x) or np.isnan(x[i]):
        return None
    return x[i], y[i]


def hit(P, f, px, py, slack=0):
    for g in range(f - slack, f + slack + 1):
        q = at(P, g)
        if q is not None and np.hypot(q[0] - px, q[1] - py) <= TOL:
            return True
    return False


def marks(dirn):
    out = []
    for f in sorted(glob.glob(os.path.join(dirn, '*.json'))):
        t = json.load(open(f)); out.append(t.get('data', t))
    return out


TRUTH = marks(os.path.join(S2, 'truthdb', 'truth'))
FIND = [m for m in marks(os.path.join(HERE, 'findball', 'labels')) if m.get('pool') == 'test' and m['kind'] in ('ball', 'none')]
PASS = marks(os.path.join(HERE, 'passbench', 'truth'))
FIX = marks(os.path.join(HERE, 'fixlab', 'fixes'))


def point_marks(ver, items, tagof):
    c = dict(ball=0, right=0, wrong=0, none=0, off=0, offok=0)
    for m in items:
        P = load(ver, tagof(m))
        if P is None:
            return None
        if m['kind'] == 'ball':
            c['ball'] += 1; q = at(P, m['abs'])
            if q is None:
                c['none'] += 1
            elif np.hypot(q[0] - m['x'], q[1] - m['y']) <= TOL:
                c['right'] += 1
            else:
                c['wrong'] += 1
        else:
            c['off'] += 1; c['offok'] += at(P, m['abs']) is None
    c['acc'] = (c['right'] + c['offok']) / max(1, c['ball'] + c['off'])
    return c


def truth(ver):
    out = {}
    for h, keep in (('all', lambda t: True), ('H1', lambda t: int(t[2:]) < 9), ('H2', lambda t: int(t[2:]) >= 9)):
        items = [m for m in TRUTH if m['kind'] in ('ball', 'off') and keep(m['clip'])]
        out[h] = point_marks(ver, items, lambda m: m['clip'])
    return out


def find(ver):
    return point_marks(ver, FIND, lambda m: 'pm')


def passes(ver):
    P = load(ver, 'pm')
    if P is None:
        return None
    pts = 0; car = 0; car7 = 0; per = {}
    for p in PASS:
        a = hit(P, FIRST + p['ballFrom']['f'], p['ballFrom']['x'] * SX, p['ballFrom']['y'] * SY, 2)
        b = hit(P, FIRST + p['ballTo']['f'], p['ballTo']['x'] * SX, p['ballTo']['y'] * SY, 2)
        pts += a + b; car += a and b; car7 += (a and b) and p['id'] != 'p5'
        per[p['id']] = (bool(a), bool(b))
    return dict(points=pts, carried=car, carried7=car7, per=per)


def fix(ver):
    P = load(ver, 'vid3'); S = load('cls_B', 'vid3')
    if P is None:
        return None
    lo = P[0]; pt = 0; ptn = 0; cov = []; rep = []; per = {}
    for m in FIX:
        s = slice(m['f0'] - lo, m['f1'] - lo + 1)
        if m['kind'] in ('missed', 'missair', 'other'):
            if m['id'] != 'p172290':           # its points are the coach's foot, i.e. our wrong ring
                for p in m['pts']:
                    if 'px' in p:
                        ptn += 1; pt += hit(P, p['f'], p['px'], p['py'])
                if m['kind'] != 'other':
                    cov.append((~np.isnan(P[1][s])).mean()); per[m['id']] = round(float(cov[-1]), 2)
        if m['kind'] in ('wrong', 'spot') or m['id'] == 'p172290':
            d = np.hypot(P[1][s] - S[1][s], P[2][s] - S[2][s])
            r = float(np.nanmean(np.where(np.isnan(S[1][s]), np.nan, (d <= 30).astype(float))))
            rep.append(r); per[m['id']] = round(r, 2)
    return dict(points=pt, npoints=ptn, cover=float(np.mean(cov)), repeat=float(np.mean(rep)), per=per)


def jitter(ver):
    """Median 2nd difference (px/frame^2) over runs of 3 consecutive rings, all windows -- a smoothness read, not a test."""
    v = []
    for f in glob.glob(os.path.join(HERE, 'p2d', ver, '*.npz')):
        z = np.load(f); x, y = z['x'], z['y']
        ax = x[2:] - 2 * x[1:-1] + x[:-2]; ay = y[2:] - 2 * y[1:-1] + y[:-2]
        a = np.hypot(ax, ay); v.append(a[~np.isnan(a)])
    v = np.concatenate(v) if v else np.array([np.nan])
    return float(np.median(v)) if len(v) else None


def cover(ver):
    n = r = 0
    for f in glob.glob(os.path.join(HERE, 'p2d', ver, '*.npz')):
        z = np.load(f); n += len(z['x']); r += (~np.isnan(z['x'])).sum()
    return r / max(1, n)


def score(ver):
    return dict(truth=truth(ver), find=find(ver), passes=passes(ver), fix=fix(ver), jitter=jitter(ver), cover=cover(ver),
                tags=sorted(os.path.basename(f)[:-4] for f in glob.glob(os.path.join(HERE, 'p2d', ver, '*.npz'))))


def row(v, r):
    t = r['truth']['all']; h1 = r['truth']['H1']; h2 = r['truth']['H2']; f = r['find']; p = r['passes']; x = r['fix']
    s = '%-16s' % v
    s += (' truth %3.0f%% R%3d W%3d N%3d off%2d/%2d  H1 %3.0f%% H2 %3.0f%%' % (100 * t['acc'], t['right'], t['wrong'], t['none'], t['offok'], t['off'],
                                                                         100 * h1['acc'], 100 * h2['acc'])) if t else ' truth  --' + ' ' * 47
    s += (' | find %2d/%2d W%2d none %d/%d' % (f['right'], f['ball'], f['wrong'], f['offok'], f['off'])) if f else ' | find --' + ' ' * 17
    s += (' | pass pts %2d/16 car %d/8 %d/7' % (p['points'], p['carried'], p['carried7'])) if p else ' | pass --' + ' ' * 20
    s += (' | fix pts %d/%d cov %.2f rep %.2f' % (x['points'], x['npoints'], x['cover'], x['repeat'])) if x else ' | fix --'
    s += ' | jit %.2f cov %.2f' % (r['jitter'] or np.nan, r['cover'])
    return s


if __name__ == '__main__':
    vers = sys.argv[1:] or sorted(os.listdir(os.path.join(HERE, 'p2d')))
    os.makedirs(os.path.join(HERE, 'results'), exist_ok=True)
    p = os.path.join(HERE, 'results', '2d.json')
    R = json.load(open(p)) if os.path.exists(p) else {}
    for v in vers:
        R[v] = score(v); print(row(v, R[v]), flush=True)
    json.dump(R, open(p, 'w'), indent=1, default=lambda o: o.item() if hasattr(o, 'item') else str(o))
