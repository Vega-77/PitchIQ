"""Port step 2: the shipped 3D chain (mk3d's default chain) on the ball-push picks, then the real scorer's ring rule.

Runs the MAIN checkout's code read-only (it holds the data files the chain reads: <tag>pan.npz, registrations).
Every output goes under PitchIQ-data/ball-push/port:
  p2d/<ver>/<tag>.npz     picks (abs, x, y, src) from tnclick EXPORT (src 1 track, 2 fill, 3 bridge)
  h3d_<ver>/              work folder (PIQ_HOMOG): copies of this tag's map inputs + the chain's outputs
PIQ_P2D is given as an absolute path: os.path.join drops S2 before it, so saved_picks reads our file.

    python mk3d_bp.py <ver> <export dir> <tag> [...]     (PYTHONPATH = main checkout)
    python mk3d_bp.py score <ver> <tag> [...]           ring rule of clickscore.rings on our B and P
"""
import glob
import os
import re
import shutil
import subprocess
import sys
import time

import numpy as np

MAIN = 'C:/Users/alexv/Desktop/Repos/PitchIQ'
BT = MAIN + '/pitchiq/balltrack'
HOM = BT + '/pitch_registration'
PORT = 'C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push/port'
PY = sys.executable
DATA_BP = 'C:/Users/alexv/Desktop/Repos/PitchIQ-data/ball-push'
GATE_RUN = os.environ.get('GATE_RUN', 'ko1_s10')  # '' = the shipped classifier's scores
GATE_NPZ = os.environ.get('GATE_NPZ')  # pattern with %s for the tag (held out: replay3_hk1/replay_cache/bp_cls_%s.npz)


def setup(ver, tag):
    d = os.path.join(PORT, 'h3d_' + ver)
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(os.path.join(d, 'labeling')):
        for lab in ('air_labels', 'flight_labels', 'flight_lab2_labels'):
            shutil.copytree(os.path.join(HOM, 'labeling', lab), os.path.join(d, 'labeling', lab))
        os.makedirs(os.path.join(d, 'labeling', 'air_lab'), exist_ok=True)
        shutil.copy2(os.path.join(HOM, 'labeling', 'air_lab', 'manifest.json'), os.path.join(d, 'labeling', 'air_lab'))
        for p in glob.glob(os.path.join(HOM, '*.json')):
            shutil.copy2(p, d)
    for p in glob.glob(os.path.join(HOM, '*')):
        b = os.path.basename(p)
        if os.path.isdir(p) or not b.endswith(('.npz', '.npy')) or b.startswith(('_', 'vid', 'wm', 'vfeats')):
            continue
        m = re.match(r'^([A-Za-z]+\d+)_', b)
        if m and m.group(1) != tag:
            continue  # another half's map inputs
        if re.match(r'%s_q\d' % tag, b) or re.search(r'_(ballsb|img|scan|ball3d|smooth|chains|rowspd|flights|fixed)', b):
            continue
        if not os.path.exists(os.path.join(d, b)):
            shutil.copy2(p, d)
    # nb2's reg has one ok registration 6 frames past its pan's end (the shipped pass ran nb2 in pieces and never
    # reached it); ball_on_pitch indexes the pan cum with it.  Drop ok registrations outside the pan, in our copy only.
    rp, pp = os.path.join(d, '%s_reg.npz' % tag), os.path.join(BT, '%span.npz' % tag)
    if os.path.exists(rp) and os.path.exists(pp):
        r, pn = dict(np.load(rp)), np.load(pp)
        bad = r['ok'] & ((r['abs'] < int(pn['lo'])) | (r['abs'] > int(pn['hi'])))
        if bad.any():
            r['ok'] = r['ok'] & ~bad
            np.savez(rp, **r)
            print('%s: dropped %d registrations outside the pan' % (tag, bad.sum()), flush=True)
    return d


def run(d, ver, log, *cmd, env=None):
    t = time.time()
    e = dict(os.environ, PIQ_HOMOG=d, PIQ_P2D=os.path.join(PORT, 'p2d', ver), PYTHONPATH=MAIN, **(env or {}))
    r = subprocess.run([PY, '-W', 'ignore', '-m', 'pitchiq.balltrack.pitch_registration.' + cmd[0]] + list(cmd[1:]),
                       cwd=d, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log.write('$ %s  (%.0f s)\n%s\n' % (' '.join(cmd), time.time() - t, r.stdout))
    log.flush()
    if r.returncode:
        raise SystemExit('FAILED %s:\n%s' % (cmd, r.stdout[-2000:]))


def chain(ver, exp, tag):
    os.makedirs(os.path.join(PORT, 'p2d', ver), exist_ok=True)
    z = np.load(os.path.join(exp, '%s.npz' % tag))
    np.savez(os.path.join(PORT, 'p2d', ver, '%s.npz' % tag), abs=z['abs'].astype(np.int64),
             x=z['x'].astype(np.float64), y=z['y'].astype(np.float64), src=z['src'].astype(np.int8))
    d = setup(ver, tag)
    t0 = time.time()
    with open(os.path.join(d, 'chain_%s.log' % tag), 'a') as log:
        if not os.path.exists(os.path.join(d, '%s_ball3dd.npz' % tag)):
            run(d, ver, log, 'ball_on_pitch', tag)
            run(d, ver, log, 'image_track', tag)
            run(d, ver, log, 'flight_scan', tag, '16')
            run(d, ver, log, 'roll_speed_cache', tag)
            run(d, ver, log, 'flight_chain', tag)
            run(d, ver, log, 'smooth_ball', tag, env=dict(VS_B3='%s_ball3d2.npz' % tag))
            run(d, ver, log, 'merge_air_and_ground', '%s_ball3d2.npz' % tag, '%s_smooth.npz' % tag, '%s_ball3dd.npz' % tag)
    print('%s %s 3D done %.0f s' % (ver, tag, time.time() - t0), flush=True)


def score(ver, tags):
    """clickscore.rings with B = our 3D and P = our picks; judged by clickscore.judge on the clicker answers."""
    sys.path.insert(0, MAIN)
    import json
    from pitchiq.balltrack import match_video
    from pitchiq.experiments.clicker import clickscore as cs
    items = {it['id']: it for it in json.load(open(os.path.join(cs.ROOT, 'items_all.json')))['items']}
    A = cs.answers(os.path.join(cs.ROOT, 'db'))
    tot = dict(right=0, wrong=0, missed=0, out_wrong=0, air=0)
    for tag in tags:
        tr = match_video.flight_model.Track(tag)
        a0 = int(tr.abs[0])
        if ver == 'V23':  # the shipped version, read only, for a same-rule reference
            B = np.load(os.path.join(BT, 'h3d_hyb90', '%s_ball3dd.npz' % tag))
            P = np.load(os.path.join(BT, 'evaluation', 'p2d', 'hyb90', '%s.npz' % tag))
        else:
            B = np.load(os.path.join(PORT, 'h3d_' + ver, '%s_ball3dd.npz' % tag))
            P = np.load(os.path.join(PORT, 'p2d', ver, '%s.npz' % tag))
        assert int(P['abs'][0]) == a0 and len(P['abs']) == len(B['x']), (tag, P['abs'][0], a0, len(P['abs']), len(B['x']))
        air = (B['state'] == 'air') & np.isfinite(B['x'])
        flash = match_video.short_runs(air | ((P['src'] > 0) & np.isfinite(P['x'])))
        if GATE_RUN and ver != 'V23':
            # the gate reads the classifier that made the picks (the self-trained one), not the shipped cls_C
            b = np.load(GATE_NPZ % tag if GATE_NPZ else
                        '%s/trk2/%s/replay_cache/bp_%s_global_%s.npz' % (DATA_BP, GATE_RUN, GATE_RUN, tag))
            bp = dict(zip(b['f'].tolist(), b['p'].tolist()))
            match_video.ballgate.best_p = lambda t, bp=bp: bp
        g = match_video.ballgate.lost(tag, a0, match_video.ballgate.scored(P))
        if os.environ.get('GATE_SKIP_BRIDGE'):
            g &= P['src'] != 3
        flash |= g
        c = dict(right=0, wrong=0, missed=0, out_wrong=0, air=0)
        for iid, a in A.items():
            it = items.get(iid)
            if not it or it['half'] != tag or a['v'] not in ('ball', 'out'):
                continue
            i = int(it['f']) - a0
            pt, kind = None, ''
            if not flash[i]:
                if air[i]:
                    q = np.linalg.solve(tr.A, [B['x'][i], B['y'][i], 1.0])
                    uv = q[:2] / q[2]
                    b_, _ = tr.project(np.full(2, i), np.array([[uv[0], uv[1], B['z'][i]], [uv[0], uv[1], 0.0]]))
                    pt, kind = b_, 'air'
                elif P['src'][i] > 0 and np.isfinite(P['x'][i]):
                    pt, kind = (P['x'][i], P['y'][i]), 'ground'
            if a['v'] == 'out':
                c['out_wrong'] += pt is not None
                continue
            if pt is None:
                c['missed'] += 1
            elif np.hypot(float(np.ravel(pt)[0]) - a['x'], float(np.ravel(pt)[1]) - a['y']) <= cs.TOL:
                c['right'] += 1
                c['air'] += kind == 'air'
            else:
                c['wrong'] += 1
        print('%-8s %-6s right %3d  wrong on ball %2d  missed %3d  | out rings %2d | right in air %d' % (
            ver, tag, c['right'], c['wrong'], c['missed'], c['out_wrong'], c['air']), flush=True)
        for k in tot:
            tot[k] += c[k]
    print('%-8s TOTAL  right %3d  wrong %3d (on ball %d + out %d)  missed %3d' % (
        ver, tot['right'], tot['wrong'] + tot['out_wrong'], tot['wrong'], tot['out_wrong'], tot['missed']))


if __name__ == '__main__':
    if sys.argv[1] == 'score':
        score(sys.argv[2], sys.argv[3:])
    else:
        for t in sys.argv[3:]:
            chain(sys.argv[1], sys.argv[2], t)
