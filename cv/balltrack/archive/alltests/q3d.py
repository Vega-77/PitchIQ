"""Queue: every 2D version through the 3D chain on vid1/2/3 as soon as its picks exist.
Also builds hybrids / picks for windows whose GPU inputs have landed.  One chain at a time.

    python q3d.py
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__)); S2 = os.path.dirname(HERE)
PY = sys.executable
TAGS = ['vid1', 'vid2', 'vid3']
ALL = ['pm', 'vid1', 'vid2', 'vid3'] + ['wm%02d' % i for i in range(18)]
CLS = ['A', 'orig', 'h160', 'Aprev', 'Ar3', 'Ar4', 'Bprev', 'Br3', 'Br4']
ORDER = [('cls_B', True), ('hyb90', True), ('hyb32', False), ('hyb90s', False), ('cls_B_nofill', False),
         ('tn_alone', False), ('yolo_trk', False), ('yolo_raw', False), ('blob_v7', False)] + \
        [('cls_' + c, False) for c in CLS] + [('blob_raw', False)]


def have(ver, tag):
    return os.path.exists(os.path.join(HERE, 'p2d', ver, '%s.npz' % tag))


def done(ver, tag, var):
    d = os.path.join(S2, 'h3d_' + ver)
    return os.path.exists(os.path.join(d, '%s_ball3dd.npz' % tag)) and (not var or os.path.exists(os.path.join(d, '%s_ball3dsx.npz' % tag)))


def lock(path):
    try:
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)); return True
    except FileExistsError:
        return False


def locked(ver, tag):
    return os.path.exists(os.path.join(S2, 'h3d_' + ver, '%s.lock' % tag))


def refresh():
    g = os.path.join(HERE, 'q3d_refresh.lock')
    if not lock(g):
        return
    try:
        _refresh()
    finally:
        os.remove(g)


def _refresh():
    for t in ALL:
        if os.path.exists(os.path.join(HERE, 'tn', 'bridges_%s.npy' % t)) and not have('hyb90', t):
            subprocess.run([PY, 'hybrid.py', t], cwd=HERE)
        if os.path.exists(os.path.join(HERE, 'tn', 'alone_%s.npz' % t)) and not have('tn_alone', t):
            subprocess.run([PY, 'hybrid.py', t], cwd=HERE)
        for c in CLS:
            if os.path.exists(os.path.join(HERE, 'cls', c, '%scls.npz' % t)) and not have('cls_' + c, t):
                subprocess.run([PY, '-W', 'ignore', 'mkpicks.py', 'cls_' + c, t], cwd=HERE)


if __name__ == '__main__':
    while True:
        refresh()
        todo = [(v, t, var) for v, var in ORDER for t in TAGS if not done(v, t, var)]
        if not [x for x in todo if not locked(x[0], x[1])]:
            print('Q3D ALL DONE', flush=True); break
        ready = [(v, t, var) for v, t, var in todo if have(v, t) and not locked(v, t)]
        if not ready:
            time.sleep(60); continue
        v, t, var = ready[0]
        os.makedirs(os.path.join(S2, 'h3d_' + v), exist_ok=True)
        if not lock(os.path.join(S2, 'h3d_' + v, '%s.lock' % t)):
            continue
        r = subprocess.run([PY, 'mk3d.py', v, t] + (['--variants'] if var else []), cwd=HERE)
        if r.returncode:
            print('Q3D FAILED %s %s -- version dropped from the queue' % (v, t), flush=True)
            ORDER[:] = [(a, b) for a, b in ORDER if a != v]
