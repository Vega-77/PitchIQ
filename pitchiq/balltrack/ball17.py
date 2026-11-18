"""The look #17 ball (ball push, 2026-11-17): the shipped ball since 2026-11-18, one game at a time.

    PIQ_VIDEO=<video> python -m pitchiq.balltrack.ball17 <game> <tag>[,<tag>...]  [--clicked-gaps] [--no-3d]

Runs after `half.py` has passed every half of the game (with PIQ_GIANT=50000, the pale-surface finder
rule, in its detection; process_match sets it).  half.py's pieces <tag>_qNNN.npz, its cls_C scores
<tag>_qNNNcls.npz (the base scores here) and its shipped 3D pitch_registration/<tag>_ball3dd.npz (the
bridges' air mask) are the inputs.  Measured on the five held-out halves: 133 right / 12 wrong / 121 missed
of the Ball Clicker answers against 62 / 10 / 192 for the ball it replaces (PitchIQ-data/HANDOFF-2026-11-16-ball.md).

  1 crop caches        the game's candidates as 40 px crops + 4 neighbour frames (selftrain cache.py, tcache.py)
  2 three rounds       each round mines the game's own confident tracks under the previous round's scores
                       (round 1: cls_C), fits a ResNet-18 crop classifier on them plus the dev rows (two seeds,
                       0 and 10; selftrain held.fit, ~5 min each), averages the two and maps the raw scores onto
                       cls_C's with the look's own label-free affine for that round (ROUND_CAL)
  3 two runs           round-1 scores (A) and round-3 scores (B), each: tracker skip:2+enter:1+kin:4, lost-ball
                       gate bar 1.0 on its own scores, TAPNext bridges over every gap up to 180 frames
  4 merge              a frame keeps a ring where either run rings; where both ring > 15 px apart, none
  5 3D                 the shipped 3D chain (evaluation.mk3d) on the merged picks, one half at a time

Outputs (version VER):
  evaluation/p2d/v17/<tag>.npz      abs, x, y, src (0 where there is no ring), ring
  h3d_v17/<tag>_ball3dd.npz, _smooth2.npz
  PitchIQ-data/ball17/<game>/       working files and log.txt; every stage is skipped when its output exists.
--clicked-gaps bridges only the gaps that hold a Ball Clicker answer (the look's own scoring shortcut, minutes
instead of hours); --no-3d stops after the merge; --work <dir> keeps everything (merged picks too) there and
--bt <dir> reads pieces from another folder.  For checking against the look only.

Readers (match_video, panning_camera, process_match) call version(tag) and lost(tag, ...): v17 where its files
exist, else Hybrid 90.  The v17 rings already hold their own lost-ball gate, so lost() is empty for them; the
old gate reads cls_C and would blank stretches the new classifier is sure of.
"""
import argparse
import glob
import io
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np

S2 = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(S2))
DATA = os.path.join(os.path.dirname(REPO), 'PitchIQ-data')
BP = os.path.join(REPO, 'pitchiq', 'experiments', 'ballpush')
SELF = os.path.join(BP, 'selftrain')
HOM = os.path.join(S2, 'pitch_registration')
PY = sys.executable

VER = 'v17'
TRACKER = 'skip:2+enter:1+kin:4'
LOST_P = 1.0  # lost-ball gate bar on the run's own scores (shipped ballgate: 1.5 on cls_C)
MAXGAP = 180  # TAPNext bridges, frames
MERGE_TOL = 15.0  # px
SEEDS = (0, 10)
# round -> (a, b): raw ResNet score -> cls_C scale, fitted label-free in the look (frame-top quantiles, the five
# held-out halves pooled).  Fixed here: one label-free fit per half was worse on dev (idea c24).
ROUND_CAL = {
    1: (0.5030845279852327, -1.27646924842615),  # hkr3a+hkr3b (held.py finish)
    2: (0.4439377037884541, -1.691359287226853),  # hko2 (giant/hko_hko2_cal.json)
    3: (0.4473842576118006, -1.582752357765509),  # hko3 (giant/hko_hko3_cal.json)
}
FIT_ARGS = dict(w=0.5, epochs=12, dev_pseudo='oof_k3', arch='resnet18', conv_lr=0.3, wide=0, rank=0.0, kmax=24,
                gbatch=16)
HGAMES = ['nb', 'pi', 'wh', 'co', 'wb']  # held.HGAMES: a game's seed is its index there + the offset (else 0)
FS_ENV = dict(FS_WORKERS='6', OPENBLAS_NUM_THREADS='1')  # 3D: one half at a time, 6 scan workers (commit limit)


# ---- readers --------------------------------------------------------------------------------------------------

def version(tag):
    """'v17' when this half has the new ball, else 'hyb90'."""
    ok = all(os.path.exists(p) for p in (
        os.path.join(S2, 'evaluation', 'p2d', VER, '%s.npz' % tag),
        os.path.join(S2, 'h3d_' + VER, '%s_ball3dd.npz' % tag),
        os.path.join(S2, 'h3d_' + VER, '%s_smooth2.npz' % tag)))
    return VER if ok else 'hyb90'


def lost(tag, abs0, picks, ver=None):
    """ballgate.lost for Hybrid 90; nothing for v17 (its rings carry their own gate)."""
    from . import ballgate

    if (ver or version(tag)) == VER:
        return np.zeros(len(picks['src']), bool)
    return ballgate.lost(tag, abs0, ballgate.scored(picks))


# ---- the step -------------------------------------------------------------------------------------------------

class Game:
    def __init__(self, game, tags, bt, clicked_gaps=False, work=None):
        self.game, self.tags, self.bt = game, tags, bt
        self.W = work or os.path.join(DATA, 'ball17', game)
        # a check run (--work) keeps its merged picks in its own folder, away from the shipped ones
        self.p2d = os.path.join(work, 'p2d') if work else os.path.join(S2, 'evaluation', 'p2d', VER)
        self.clicked = clicked_gaps
        os.makedirs(self.W, exist_ok=True)
        self.logf = open(os.path.join(self.W, 'log.txt'), 'a', encoding='utf-8')

    def say(self, s):
        line = '%s  %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), s)
        print(line, flush=True)
        self.logf.write(line + '\n')
        self.logf.flush()

    def env(self, own_bt=True, **kw):
        """The parent's environment without the ball push's knobs, plus kw.  own_bt=False: the fit, whose dev rows
        and crop labels live beside the main checkout's balltrack (it gets the pieces' folder as an argument)."""
        knobs = ('ST_', 'BP_', 'RP_', 'TN_', 'MAXGAP', 'ALLGAPS', 'RC', 'BPNAME', 'EXPORT', 'ENDP', 'PARTIAL', 'BACK')
        e = {k: v for k, v in os.environ.items() if not k.startswith(knobs)}
        e.update(PYTHONPATH=REPO, BP_HELD=','.join(self.tags), PYTHONIOENCODING='utf-8')
        if own_bt and os.path.normcase(os.path.abspath(self.bt)) != os.path.normcase(S2):
            e['BP_BT'] = self.bt
        e.update({k: str(v) for k, v in kw.items()})
        return e

    def run(self, what, args, cwd, env):
        t = time.time()
        with open(os.path.join(self.W, '%s.log' % what), 'a', encoding='utf-8') as log:
            r = subprocess.run([PY, '-W', 'ignore'] + args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT)
        if r.returncode:
            raise SystemExit('BALL17 FAILED %s (rc %d): see %s' % (what, r.returncode, os.path.join(self.W, what + '.log')))
        self.say('%-14s %.0f s' % (what, time.time() - t))

    def pieces(self):
        return [os.path.basename(p)[:-4] for t in self.tags
                for p in sorted(glob.glob(os.path.join(self.bt, '%s_q[0-9][0-9][0-9].npz' % t)))]

    def st_env(self, oof):
        return self.env(ST_HELD=1, ST_OUT=self.W, ST_OOF=oof)

    # 1
    def caches(self):
        pts = self.pieces()
        assert pts, 'no pieces for %s in %s' % (self.tags, self.bt)
        miss = [pt for pt in pts if not os.path.exists(os.path.join(self.bt, '%scls.npz' % pt))]
        assert not miss, 'pieces without cls_C scores: %s' % miss[:3]
        if not all(os.path.exists(os.path.join(self.W, 'cache', pt + '.npz')) for pt in pts):
            self.run('cache', ['cache.py'] + self.tags, SELF, self.st_env(self.bt))
        if not all(os.path.exists(os.path.join(self.W, 'tcache', pt + '.npy')) for pt in pts):
            self.run('tcache', ['tcache.py'] + self.tags, SELF, self.st_env(self.bt))

    # 2
    def scores_dir(self, r):
        return self.bt if r == 0 else os.path.join(self.W, 'r%d' % r)

    def round(self, r):
        out = self.scores_dir(r)
        done = os.path.join(out, '.done')
        if os.path.exists(done):
            return
        self.run('mine_r%d' % r, ['hko.py', 'mine', ','.join(self.tags)], SELF, self.st_env(self.scores_dir(r - 1)))
        for s in SEEDS:
            run = 'r%ds%d' % (r, s)
            if not all(os.path.exists(os.path.join(self.W, 'runs', run, 'raw', pt + '.npy')) for pt in self.pieces()):
                self.run('fit_' + run, ['-m', 'pitchiq.balltrack.ball17', '_fit', self.game, ','.join(self.tags),
                                        self.bt, self.W, run, str(s)], REPO, self.env(own_bt=False))
        self.finish(r)
        open(done, 'w').close()

    def finish(self, r):
        sys.path.insert(0, SELF)
        os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
        aa, bb = ROUND_CAL[r]
        out = self.scores_dir(r)
        os.makedirs(out, exist_ok=True)
        for pt in self.pieces():
            z = np.load(os.path.join(self.W, 'cache', pt + '.npz'))
            cf, cj = z['f'].astype(np.int64), z['j'].astype(np.int64)
            raw = np.mean([np.load(os.path.join(self.W, 'runs', 'r%ds%d' % (r, s), 'raw', pt + '.npy')) for s in SEEDS], 0)
            b = np.load(os.path.join(self.bt, '%scls.npz' % pt))
            f, j, p = b['f'].astype(np.int64), b['j'].astype(np.int64), b['p'].astype(np.float32)
            o = np.lexsort((j, f))
            f, j, p = f[o], j[o], p[o].copy()
            i = np.searchsorted(f * 1024 + j, cf * 1024 + cj)
            assert (f[i] == cf).all() and (j[i] == cj).all(), pt
            p[i] = aa * raw + bb
            np.savez(os.path.join(out, '%scls.npz' % pt), f=f.astype(np.int32), j=j.astype(np.int16), p=p)
        self.say('round %d scores -> %s (a %.4f b %.4f)' % (r, out, aa, bb))

    # 3
    def track(self, r):
        name = 'r%d' % r
        exp = os.path.join(self.W, 'export_' + name)
        if all(os.path.exists(os.path.join(exp, t + '.npz')) for t in self.tags):
            return exp
        rp = os.path.join(self.W, 'replay_' + name)
        nm = TRACKER.replace(':', '_')
        if not all(os.path.exists(os.path.join(rp, 'replay_cache', '%s_%s_%s.npz' % (name, nm, t))) for t in self.tags):
            self.run('replay_' + name, [os.path.join(BP, 'replay.py'), self.scores_dir(r), TRACKER], REPO,
                     self.env(RP_TAGS=','.join(self.tags), RP_OUT=rp, RP_LOSTP=LOST_P))
        tn = dict(TN_SET='held', TN_TAGS=','.join(self.tags), RC=os.path.join(rp, 'replay_cache'), BPNAME=name,
                  MAXGAP=MAXGAP, RP_LOSTP=LOST_P, TN_ROOT=os.path.join(self.W, 'tn'))
        if not self.clicked:
            tn['ALLGAPS'] = 1
        run = 'b17_%s_%s' % (self.game, name)
        self.run('bridges_' + name, [os.path.join(BP, 'tnclick.py'), 'run', run, TRACKER], REPO, self.env(**tn))
        self.run('score_' + name, [os.path.join(BP, 'tnclick.py'), 'score', run, TRACKER], REPO, self.env(EXPORT=exp, **tn))
        return exp

    # 4
    def merge(self, a, b):
        sys.path.insert(0, BP)
        import mergerings

        od = self.p2d
        os.makedirs(od, exist_ok=True)
        for t in self.tags:
            m = mergerings.merge(np.load(os.path.join(a, t + '.npz')), np.load(os.path.join(b, t + '.npz')),
                                 'drop', MERGE_TOL)
            st = m.pop('stats')
            ring = m['ring']
            np.savez(os.path.join(od, t + '.npz'), abs=m['abs'].astype(np.int64), x=m['x'].astype(np.float64),
                     y=m['y'].astype(np.float64), src=np.where(ring, m['src'], 0).astype(np.int8), ring=ring)
            self.say('%s merged: rings A %d B %d both %d apart %d -> %d of %d frames' % (
                t, st['a'], st['b'], st['both'], st['apart'], st['out'], len(ring)))

    # 5
    def three_d(self, t):
        from .evaluation import mk3d

        d = mk3d.setup(VER)
        if os.path.exists(os.path.join(d, '%s_smooth2.npz' % t)):
            return
        for kind in ('reg', 'venue'):
            p = os.path.join(HOM, '%s_%s.npz' % (t, kind))
            if os.path.exists(p):
                shutil.copy2(p, d)
        # a ok registration past the pan's last frame (nb2 has one) breaks ball_on_pitch: drop it, in the copy
        rp, pp = os.path.join(d, '%s_reg.npz' % t), os.path.join(S2, '%span.npz' % t)
        if os.path.exists(rp) and os.path.exists(pp):
            r, pn = dict(np.load(rp)), np.load(pp)
            bad = r['ok'] & ((r['abs'] < int(pn['lo'])) | (r['abs'] > int(pn['hi'])))
            if bad.any():
                r['ok'] = r['ok'] & ~bad
                np.savez(rp, **r)
                self.say('%s: dropped %d registrations outside the pan' % (t, bad.sum()))
        self.run('3d_' + t, ['-m', 'pitchiq.balltrack.evaluation.mk3d', VER, t], REPO, self.env(**FS_ENV))

    def go(self, three_d=True):
        self.say('START %s %s (pieces from %s)%s' % (self.game, ','.join(self.tags), self.bt,
                                                   ' clicked gaps only' if self.clicked else ''))
        self.caches()
        for r in (1, 2, 3):
            self.round(r)
        a, b = self.track(1), self.track(3)
        self.merge(a, b)
        if three_d:
            for t in self.tags:
                self.three_d(t)
        self.say('BALL17 DONE %s' % self.game)


def _fit(game, tags, bt, W, run, seed):
    """One seed of one round: held.fit for this game with W as its folder (run with no ST_/BP_ env)."""
    sys.path.insert(0, SELF)
    import held

    held.HO = W
    held.hpieces = lambda g: [os.path.basename(p)[:-4] for t in tags.split(',')
                              for p in sorted(glob.glob(os.path.join(bt, '%s_q[0-9][0-9][0-9].npz' % t)))]
    if game not in held.HGAMES:
        held.HGAMES = [game]
    a = argparse.Namespace(cmd='fit', run=run, games=game, seed_offset=int(seed), **FIT_ARGS)
    held.fit(a)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == '_fit':
        _fit(*argv[1:])
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('game')
    ap.add_argument('tags')
    ap.add_argument('--bt', default=S2, help='folder of the pieces and cls_C scores (default: this folder)')
    ap.add_argument('--clicked-gaps', action='store_true')
    ap.add_argument('--no-3d', action='store_true')
    ap.add_argument('--work', help='a check run: working files and merged picks here, nothing shipped')
    a = ap.parse_args(argv)
    if a.work and not a.no_3d:
        ap.error('--work is for check runs: give --no-3d too')
    Game(a.game, a.tags.split(','), a.bt, a.clicked_gaps, a.work).go(not a.no_3d)
    return 0


if __name__ == '__main__':
    sys.exit(main())
