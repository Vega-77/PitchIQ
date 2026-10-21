"""The default 3D chain (and the other flight models) for one 2D version, in its own work
folder so the shipped outputs are never touched.

    python -m pitchiq.balltrack.alltests.mk3d <ver> <tag> [<tag> ...] [--variants]

  work   S2/h3d_<ver>/  (labels, registrations): the homog scripts run there with
         PIQ_HOMOG pointing at it and PIQ_P2D=<ver>, so render_review_video.picks_for serves
         alltests/p2d/<ver>/<tag>.npz
  chain  ball_on_pitch -> image_track -> flight_scan 16 -> roll_speed_cache -> default_ball_path (flight_chain + smooth_ball) -> <tag>_ball3dd.npz
  --variants  also flight_scan_ballistic 8 (v1), flight_chain_fast (3, 3k), strict_flights (x1), smooth_ball on x1 -> sx
TAPNext bridge frames (src 3) go in as 'fill' unless BRIDGE_TRACK=1.
"""

import glob
import re
import os
import shutil
import subprocess
import sys
import time

from .. import script

HERE = os.path.dirname(os.path.abspath(__file__))
S2 = os.path.dirname(HERE)
HOM = os.path.join(S2, 'homog')
PY = sys.executable
READY = '.ready'


def setup(ver):
    d = os.path.join(S2, 'h3d_' + ver)
    # folders set up before 2026-10-21 hold rv_shim.py (and script copies) instead
    if any(os.path.exists(os.path.join(d, m)) for m in (READY, 'rv_shim.py')):
        return d
    os.makedirs(d, exist_ok=True)
    try:  # another queue instance may be setting up this copy
        os.close(os.open(os.path.join(d, '.setup'), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        while not os.path.exists(os.path.join(d, READY)):
            time.sleep(5)
        return d
    for p in glob.glob(os.path.join(HOM, '*')):
        b = os.path.basename(p)
        if os.path.isdir(p):
            if b in ('airlabels', 'flightlabels', 'fl2db'):
                shutil.copytree(p, os.path.join(d, b))
            continue
        if (
            b.endswith('.json')
            or (b.endswith('_reg.npz'))
            or (
                b.endswith(('.npz', '.npy'))
                and not b.startswith(('_', 'vid', 'wm', 'vfeats'))
                and not re.search(
                    r'_(ballsb|img|scan|ball3d|smooth|chains|rowspd|flights|fixed)', b
                )
            )
        ):
            # map inputs only: a window's own chain outputs in homog are the SHIPPED picks' 3D,
            # and copying e.g. <tag>_ball3dd.npz would make chain() think this version is done
            shutil.copy2(p, d)
    os.makedirs(os.path.join(d, 'airlab'), exist_ok=True)
    shutil.copy2(os.path.join(HOM, 'airlab', 'manifest.json'), os.path.join(d, 'airlab'))
    open(os.path.join(d, READY), 'w').close()
    return d


def run(d, ver, log, *cmd, env=None):
    """homog script cmd[0] with args cmd[1:], in work folder d on 2D version ver."""
    t = time.time()
    e = dict(os.environ, PIQ_HOMOG=d, PIQ_P2D=ver, **(env or {}))
    r = subprocess.run(
        [PY, '-W', 'ignore'] + script('homog.' + cmd[0]) + list(cmd[1:]),
        cwd=d,
        env=e,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    log.write('$ %s  (%.0f s)\n%s\n' % (' '.join(cmd), time.time() - t, r.stdout))
    log.flush()
    if r.returncode:
        raise SystemExit('FAILED %s in %s:\n%s' % (cmd, d, r.stdout[-2000:]))


def chain(ver, tag, variants):
    d = setup(ver)
    t0 = time.time()
    with open(os.path.join(d, 'chain_%s.log' % tag), 'a') as log:
        if not os.path.exists(os.path.join(d, '%s_ball3dd.npz' % tag)):
            run(d, ver, log, 'ball_on_pitch', tag)
            run(d, ver, log, 'image_track', tag)
            run(d, ver, log, 'flight_scan', tag, '16')
            run(d, ver, log, 'roll_speed_cache', tag)
            run(d, ver, log, 'flight_chain', tag)
            run(d, ver, log, 'smooth_ball', tag, env=dict(VS_B3='%s_ball3d2.npz' % tag))
            run(
                d,
                ver,
                log,
                'merge_air_and_ground',
                '%s_ball3d2.npz' % tag,
                '%s_smooth.npz' % tag,
                '%s_ball3dd.npz' % tag,
            )
            os.replace(
                os.path.join(d, '%s_smooth.npz' % tag), os.path.join(d, '%s_smooth2.npz' % tag)
            )
        if variants and not os.path.exists(os.path.join(d, '%s_ball3dsx.npz' % tag)):
            run(d, ver, log, 'flight_scan_ballistic', tag, '8')
            run(d, ver, log, 'flight_chain_fast', tag, '3')
            run(d, ver, log, 'flight_chain_fast', tag, '3k', env=dict(V3_ERR='4', V3_FAST_ERR='5'))
            run(
                d, ver, log, 'strict_flights', '%s_ball3d3k.npz' % tag, '%s_ball3dx1.npz' % tag, tag
            )
            run(d, ver, log, 'smooth_ball', tag, env=dict(VS_B3='%s_ball3dx1.npz' % tag))
            run(
                d,
                ver,
                log,
                'merge_air_and_ground',
                '%s_ball3dx1.npz' % tag,
                '%s_smooth.npz' % tag,
                '%s_ball3dsx.npz' % tag,
            )
    print('%-16s %-5s 3D done  %.0f s' % (ver, tag, time.time() - t0), flush=True)


if __name__ == '__main__':
    a = [x for x in sys.argv[1:] if not x.startswith('--')]
    for t in a[1:]:
        chain(a[0], t, '--variants' in sys.argv)
