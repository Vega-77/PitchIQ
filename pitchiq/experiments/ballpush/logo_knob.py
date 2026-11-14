"""Honest choice of one tracker knob: leave one game out.

For each dev game G, pick the variant that scores best on the OTHER games (right - PEN * wrong, before bridges),
then score G with that pick.  Summed over games this is an honest dev number for "choose the knob on dev".

    RC=<replay_cache dir> BPNAME=<bp name> python logo_knob.py <run prefix> <variant> [<variant> ...]
    PEN (env, default 4): how many missed balls one wrong ring is worth in the choice.
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tnclick as TN  # noqa: E402

PEN = float(os.environ.get('PEN', '4'))


def game(tag):
    return tag.rstrip('0123456789').replace('scrimh', 'scrim')


def per_half(run, nm):
    from pitchiq.balltrack import ballgate
    D, cs = TN.load(run, nm)
    out = {}
    for tag, (z, want) in D.items():
        lo, n = int(z['lo']), len(z['src'])
        b = np.load('%s/bp_%s_%s.npz' % (TN.RC, TN.BPNAME, tag))
        bp = dict(zip(b['f'].tolist(), b['p'].tolist()))
        ballgate.best_p = lambda t, bp=bp: bp
        x, y, s, ct = z['x'], z['y'], z['src'], z['cont'].astype(bool)
        on = (s > 0) & np.isfinite(x)
        flash = ballgate.short_runs(on | ct) | ballgate.lost(tag, lo, on)
        c = dict(right=0, wrong=0, missed=0)
        for _, a, fr in want:
            i = fr - lo
            ring = bool(0 <= i < n and on[i] and not flash[i])
            ok = cs.judge(a, (x[i], y[i]) if ring else None, 'ground' if ring else '')[1]
            if a['v'] == 'ball':
                c['right'] += ok
                c['wrong'] += ring and not ok
                c['missed'] += not ring
            else:
                c['wrong'] += ring
        out[tag] = c
    return out


def main():
    run, names = sys.argv[1], sys.argv[2:]
    R = {nm: per_half(run, nm) for nm in names}
    tags = list(R[names[0]])
    games = sorted({game(t) for t in tags})
    tot = dict(right=0, wrong=0, missed=0)
    for g in games:
        def val(nm, inside):
            h = [t for t in tags if (game(t) == g) == inside]
            r = sum(R[nm][t]['right'] for t in h)
            w = sum(R[nm][t]['wrong'] for t in h)
            return r - PEN * w
        pick = max(names, key=lambda nm: (val(nm, False), -names.index(nm)))
        c = {k: sum(R[pick][t][k] for t in tags if game(t) == g) for k in tot}
        for k in tot:
            tot[k] += c[k]
        print('%-6s pick %-28s  %s' % (g, pick, c), flush=True)
    for nm in names:
        a = {k: sum(R[nm][t][k] for t in tags) for k in tot}
        print('fixed %-28s %s' % (nm, a))
    print('LOGO choice (PEN %g): %s' % (PEN, tot))


if __name__ == '__main__':
    main()
