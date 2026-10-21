"""Score ball3d files on the Flight Lab 2 labels (vid2, 80:00-85:00, TEST ONLY).

The user marked every flight in the five minutes, so any claimed air outside a
marked flight is wrong (ends get TOL frames of slack).

  air    per frame precision / recall / F of 'air'
  hit    marked flights with >= half their frames claimed air
  false  claimed air runs that touch no marked flight (the user's worst case)
  spots  error (SB units) at the user's ground spots: kick take-offs and landings
  track  per flight median error along the marked take-off -> landing line
         (the model's air position where it claims air, else its ground position)
  air-only  the same, on claimed air frames only

    python -m pitchiq.balltrack.pitch_registration.score_flight_lab2 a.npz b.npz ...      ('flat' scores the raw ground reading)
"""

import json
import sys

import numpy as np

from .smooth_ball import runs_of

TOL = 3
L = json.load(open('flightlab2_labels.json'))['flights']
Z = np.load('vid2_ballsb.npz')
A0 = int(Z['abs'][0])
n = len(Z['abs'])
flat = np.c_[Z['x'], Z['y']]
has = Z['has'].astype(bool)
truth = np.zeros(n, bool)
near_t = np.zeros(n, bool)
for f in L:
    a, b = f['f0'] - A0, f['f1'] - A0
    truth[a : b + 1] = True
    near_t[max(0, a - TOL) : b + TOL + 1] = True


def near(xy, ok, i, r=2):
    """xy at frame i, or at the nearest frame within r where ok; None if there is none."""
    for d in range(r + 1):
        for k in (i - d, i + d):
            if 0 <= k < n and ok[k]:
                return xy[k]
    return None


def score(p):
    """One line of Flight Lab 2 scores (air, hit, false, spots, track, air-only) for ball3d
    file p, or for the flat ground reading when p is 'flat'.
    """
    if p == 'flat':
        air = np.zeros(n, bool)
        xy = flat.copy()
    else:
        B = np.load(p)
        air = B['state'] == 'air'
        xy = np.c_[B['x'], B['y']]
    okair = air & np.isfinite(xy[:, 0])
    # ground position: the model's own (smoothed files carry it), else the raw reading
    gpos = np.where(np.isfinite(xy[:, :1]) & ~air[:, None], xy, flat)
    gok = np.isfinite(gpos[:, 0])
    pos = np.where(okair[:, None], xy, gpos)
    pok = okair | gok
    tp = (air & near_t).sum()
    P = tp / max(1, air.sum())
    R = (air & truth).sum() / truth.sum()
    hit = sum(air[f['f0'] - A0 : f['f1'] - A0 + 1].mean() >= 0.5 for f in L)
    anyh = sum(air[f['f0'] - A0 : f['f1'] - A0 + 1].any() for f in L)
    fr = [(a, b) for a, b in runs_of(air) if not near_t[a : b + 1].any()]
    S = []
    for f in L:
        for fr_, q, k in ((f['f0'], f.get('p0'), f['k0']), (f['f1'], f.get('p1'), f['k1'])):
            if q and k in ('kick', 'lands'):
                v = near(pos, pok, fr_ - A0)
                if v is not None:
                    S.append(np.hypot(v[0] - q['x'], v[1] - q['y']))
    E, EA = [], []
    for f in L:
        if not (f.get('p0') and f.get('p1')):
            continue
        fr_ = np.arange(f['f0'] + 1, f['f1']) - A0
        if not len(fr_):
            continue
        t = (fr_ + A0 - f['f0']) / (f['f1'] - f['f0'])
        tx = f['p0']['x'] + t * (f['p1']['x'] - f['p0']['x'])
        ty = f['p0']['y'] + t * (f['p1']['y'] - f['p0']['y'])
        e = np.hypot(pos[fr_, 0] - tx, pos[fr_, 1] - ty)
        if pok[fr_].any():
            E.append(np.median(e[pok[fr_]]))
        if okair[fr_].any():
            EA.append(np.median(e[okair[fr_]]))

    def md(a):
        return np.median(a) if len(a) else np.nan

    return (
        '%-20s air P %.2f R %.2f F %.2f | hit %2d/%d (touched %2d) false %2d (%3d fr) | spots med %4.1f mean %4.1f n%d'
        ' | track %4.1f  air-only %4.1f on %2d'
        % (
            p.replace('vid2_ball3d', '').replace('.npz', '') or 'v1scan',
            P,
            R,
            2 * P * R / max(P + R, 1e-9),
            hit,
            len(L),
            anyh,
            len(fr),
            sum(b - a + 1 for a, b in fr),
            md(S),
            np.mean(S),
            len(S),
            md(E),
            md(EA),
            len(EA),
        )
    )


if __name__ == '__main__':
    print(
        'vid2: %d marked flights, %d air frames of %d (%.1f%%)'
        % (len(L), truth.sum(), n, 100 * truth.mean())
    )
    for p in sys.argv[1:]:
        print(score(p))
