"""The default 3D chain (and the other flight models) for one 2D version, in its own copy of
homog so the shipped outputs are never touched.

    python mk3d.py <ver> <tag> [<tag> ...] [--variants]

  copy   S2/h3d_<ver>/  (scripts, labels, registrations; every `import render_vid as RV`
         rewritten to `import rv_shim as RV`, which serves alltests/p2d/<ver>/<tag>.npz)
  chain  vballsb -> vfl0 -> vflscan2 8 -> vrowspd -> vdefault (chain2 + vsmooth) -> <tag>_ball3dd.npz
  --variants  also vflscan 8 (v1), vflchain3 (3, 3k), vstrict (x1), vsmooth on x1 -> sx
TAPNext bridge frames (src 3) go in as 'fill' unless BRIDGE_TRACK=1.
"""
import glob
import re
import io
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
HOM = os.path.join(S2, 'homog')
PY = sys.executable

SHIM = '''"""render_vid.picks_for served from a fixed 2D version (written by alltests/mk3d.py)."""
import os
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
VER = %r
BRIDGE_TRACK = os.environ.get('BRIDGE_TRACK') == '1'


def picks_for(tag):
    z = np.load(os.path.join(S2, 'alltests', 'p2d', VER, '%%s.npz' %% tag))
    by = {}
    for f, x, y, s in zip(z['abs'], z['x'], z['y'], z['src']):
        rec = {}
        if s > 0 and np.isfinite(x):
            rec = dict(src='track' if s == 1 or (s == 3 and BRIDGE_TRACK) else 'fill', x=float(x), y=float(y), p=0.0)
        by[int(f)] = rec
    return by
'''


def setup(ver):
    d = os.path.join(S2, 'h3d_' + ver)
    if os.path.exists(os.path.join(d, 'rv_shim.py')):
        return d
    os.makedirs(d, exist_ok=True)
    try:                                   # another queue instance may be setting up this copy
        os.close(os.open(os.path.join(d, '.setup'), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        while not os.path.exists(os.path.join(d, 'rv_shim.py')):
            time.sleep(5)
        return d
    for p in glob.glob(os.path.join(HOM, '*')):
        b = os.path.basename(p)
        if os.path.isdir(p):
            if b in ('airlabels', 'flightlabels', 'fl2db'):
                shutil.copytree(p, os.path.join(d, b))
            continue
        if b.endswith('.py') or b.endswith('.sh'):
            s = io.open(p, newline='', encoding='utf-8', errors='surrogateescape').read()
            s = s.replace('import render_vid as RV', 'import rv_shim as RV')
            io.open(os.path.join(d, b), 'w', newline='', encoding='utf-8', errors='surrogateescape').write(s)
        elif b.endswith('.json') or (b.endswith('_reg.npz')) or (
                b.endswith(('.npz', '.npy')) and not b.startswith(('_', 'vid', 'wm', 'vfeats'))
                and not re.search(r'_(ballsb|img|scan|ball3d|smooth|chains|rowspd|flights|fixed)', b)):
            # map inputs only: a window's own chain outputs in homog are the SHIPPED picks' 3D,
            # and copying e.g. <tag>_ball3dd.npz would make chain() think this version is done
            shutil.copy2(p, d)
    os.makedirs(os.path.join(d, 'airlab'), exist_ok=True)
    shutil.copy2(os.path.join(HOM, 'airlab', 'manifest.json'), os.path.join(d, 'airlab'))
    io.open(os.path.join(d, 'rv_shim.py'), 'w', newline='').write(SHIM % ver)
    return d


def run(d, log, *cmd, env=None):
    t = time.time()
    e = dict(os.environ, **(env or {}))
    r = subprocess.run([PY, '-W', 'ignore'] + list(cmd), cwd=d, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.write('$ %s  (%.0f s)\n%s\n' % (' '.join(cmd), time.time() - t, r.stdout)); log.flush()
    if r.returncode:
        raise SystemExit('FAILED %s in %s:\n%s' % (cmd, d, r.stdout[-2000:]))


def chain(ver, tag, variants):
    d = setup(ver); t0 = time.time()
    with open(os.path.join(d, 'chain_%s.log' % tag), 'a') as log:
        if not os.path.exists(os.path.join(d, '%s_ball3dd.npz' % tag)):
            run(d, log, 'vballsb.py', tag)
            run(d, log, 'vfl0.py', tag)
            run(d, log, 'vflscan2.py', tag, '8')
            run(d, log, 'vrowspd.py', tag)
            run(d, log, 'vflchain2.py', tag)
            run(d, log, 'vsmooth.py', tag, env=dict(VS_B3='%s_ball3d2.npz' % tag))
            run(d, log, '_mk.py', '%s_ball3d2.npz' % tag, '%s_smooth.npz' % tag, '%s_ball3dd.npz' % tag)
            os.replace(os.path.join(d, '%s_smooth.npz' % tag), os.path.join(d, '%s_smooth2.npz' % tag))
        if variants and not os.path.exists(os.path.join(d, '%s_ball3dsx.npz' % tag)):
            run(d, log, 'vflscan.py', tag, '8')
            run(d, log, 'vflchain3.py', tag, '3')
            run(d, log, 'vflchain3.py', tag, '3k', env=dict(V3_ERR='4', V3_FAST_ERR='5'))
            run(d, log, 'vstrict.py', '%s_ball3d3k.npz' % tag, '%s_ball3dx1.npz' % tag, tag)
            run(d, log, 'vsmooth.py', tag, env=dict(VS_B3='%s_ball3dx1.npz' % tag))
            run(d, log, '_mk.py', '%s_ball3dx1.npz' % tag, '%s_smooth.npz' % tag, '%s_ball3dsx.npz' % tag)
    print('%-16s %-5s 3D done  %.0f s' % (ver, tag, time.time() - t0), flush=True)


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    for t in a[1:]:
        chain(a[0], t, '--variants' in sys.argv)
