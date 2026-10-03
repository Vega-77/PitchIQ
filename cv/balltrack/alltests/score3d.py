"""Every 3D output of every version (S2/h3d_<ver>/) against the flight labs.

    python score3d.py           -> results/3d.json + a table

  air    Air Lab (vid1, vscore2.py): air frames P/R/F, true flights hit, false runs
  fl     Flight Lab (vid1, vdev.py --holdout): dev + holdout halves, median in-flight position error (SB)
  fl2    Flight Lab 2 (vid2, vscorev2.py): air P/R/F, flights hit, false runs, spot and track error
  fix    Fix Lab (vid3): share of the kicked-off-screen span (p173199) claimed air; SB error at
         the two map points the user placed (171312 save, 173742 still ball); claimed air runs
         inside the 'wrong' spans (each one a false flight on a known bad ring)
Files: dd = default (chain2 + vsmooth), 2 = chain2 alone, and with --variants also
v1 = vflscan, 3 = chain3, 3k, x1 = strict, sx = strict + vsmooth.
"""
import glob
import json
import os
import re
import subprocess
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
PY = sys.executable
FILES = dict(dd='ball3dd', c2='ball3d2', v1='ball3d', c3='ball3d3', c3k='ball3d3k', x1='ball3dx1', sx='ball3dsx')
FIX = [json.load(open(p)) for p in glob.glob(os.path.join(HERE, 'fixlab', 'fixes', '*.json'))]


def sh(d, *a):
    r = subprocess.run([PY, '-W', 'ignore'] + list(a), cwd=d, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return r.stdout.strip().splitlines()


def nums(line, keys):
    out = {}
    for k, pat in keys.items():
        m = re.search(pat, line)
        out[k] = float(m.group(1)) if m else None
    return out


AIR = dict(P=r'P ([\d.]+) R', R=r' R ([\d.]+) F', F=r' F ([\d.]+)', hit=r'hit (\d+)/', false=r'false (\d+)')
FL = dict(F=r' F ([\d.]+) hit', hit=r'hit +(\d+)/', false=r'false +(\d+)', pos=r'pos med +([\d.na]+)', airpos=r'air-only +([\d.na]+)')
FL2 = dict(P=r'air P ([\d.]+)', R=r' R ([\d.]+) F', F=r' F ([\d.]+) \|', hit=r'hit +(\d+)/', false=r'false +(\d+) \(',
           spots=r'spots med +([\d.na]+)', track=r'track +([\d.na]+)')


def fix3d(p):
    B = np.load(p); ab = B['abs']; air = B['state'] == 'air'; lo = int(ab[0])
    o = {}
    for m in FIX:
        s = slice(m['f0'] - lo, m['f1'] - lo + 1)
        if m['kind'] == 'missair':
            o['missair'] = float(air[s].mean())
        for q in m['pts']:
            if q.get('map') and m['kind'] == 'missed':
                i = q['f'] - lo; e = np.nan
                for d in range(3):
                    for k in (i - d, i + d):
                        if np.isnan(e) and 0 <= k < len(ab) and np.isfinite(B['x'][k]):
                            e = float(np.hypot(B['x'][k] - q['x'], B['y'][k] - q['y']))
                o['map_%s' % m['id']] = e
    w = np.zeros(len(ab), bool)
    for m in FIX:
        if m['kind'] in ('wrong', 'spot') or m['id'] == 'p172290':
            w[m['f0'] - lo:m['f1'] - lo + 1] = True
    o['wrong_air_runs'] = int(sum(1 for i in range(len(air)) if air[i] and w[i] and (i == 0 or not air[i - 1])))
    o['air_runs'] = int(((air[1:] & ~air[:-1]).sum()) + air[0])
    return o


def score(ver):
    d = os.path.join(S2, 'h3d_' + ver); R = {}
    for k, stem in FILES.items():
        r = {}
        if os.path.exists(os.path.join(d, 'vid1_%s.npz' % stem)):
            ln = sh(d, 'vscore2.py', 'vid1_%s.npz' % stem); r['air'] = nums(ln[-1], AIR); r['air_txt'] = ln[-1]
            ln = sh(d, 'vdev.py', '--holdout', 'vid1_%s.npz' % stem)
            r['fl_dev'] = nums(ln[-2], FL); r['fl_hold'] = nums(ln[-1], FL); r['fl_txt'] = ln[-2:]
        if os.path.exists(os.path.join(d, 'vid2_%s.npz' % stem)):
            ln = sh(d, 'vscorev2.py', 'vid2_%s.npz' % stem); r['fl2'] = nums(ln[-1], FL2); r['fl2_txt'] = ln[-1]
        p = os.path.join(d, 'vid3_%s.npz' % stem)
        if os.path.exists(p):
            r['fix'] = fix3d(p)
        if r:
            R[k] = r
    return R


def fmt(v):
    return '  --' if v is None or (isinstance(v, float) and np.isnan(v)) else ('%4.2f' % v if v < 1.0001 and v != int(v) else '%4g' % v)


if __name__ == '__main__':
    vers = [a for a in sys.argv[1:]] or sorted(os.path.basename(d)[4:] for d in glob.glob(os.path.join(S2, 'h3d_*')))
    p = os.path.join(HERE, 'results', '3d.json'); os.makedirs(os.path.dirname(p), exist_ok=True)
    R = json.load(open(p)) if os.path.exists(p) else {}
    for v in vers:
        R[v] = score(v)
        for k, r in R[v].items():
            a = r.get('air', {}); fd = r.get('fl_dev', {}); fh = r.get('fl_hold', {}); f2 = r.get('fl2', {}); fx = r.get('fix', {})
            print('%-14s %-3s | AirLab F %s hit %s false %s | FL dev F %s pos %s hold F %s pos %s | FL2 P %s R %s hit %s false %s spots %s track %s'
                  ' | fix missair %s save %s still %s wrongair %s' % (
                      v, k, fmt(a.get('F')), fmt(a.get('hit')), fmt(a.get('false')), fmt(fd.get('F')), fmt(fd.get('pos')),
                      fmt(fh.get('F')), fmt(fh.get('pos')), fmt(f2.get('P')), fmt(f2.get('R')), fmt(f2.get('hit')), fmt(f2.get('false')),
                      fmt(f2.get('spots')), fmt(f2.get('track')), fmt(fx.get('missair')), fmt(fx.get('map_p171270')),
                      fmt(fx.get('map_p173700')), fmt(fx.get('wrong_air_runs'))), flush=True)
    json.dump(R, open(p, 'w'), indent=1)
