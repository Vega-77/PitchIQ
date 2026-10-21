"""Ball (Hybrid 90 + 3D) and player detections over a whole half, pipelined.

    PIQ_VIDEO=<video> python -m pitchiq.balltrack.half <tag> <lo> <hi> [--workers 12] [--overlap 300]

Needs homog/<tag>_reg.npz (and <tag>_venue.npz) from
`python -m pitchiq.experiments.autocal_run <tag> ... --homog`.

drun.py runs over the whole half on the CPU. Each of its 5-minute chunks
becomes a piece as soon as the chunks around it are done: the chunk plus
`overlap` frames each side, run through the same chain hybrid.sh runs on a
lab window (classifier, shipped picks, shipped 3D, TAPNext bridges, hybrid,
hybrid 3D) under its own tag <tag>_q<slot>. Meanwhile the GPU detects the
players chunk by chunk. At the end the pieces are stitched: each seam is cut
at the frame nearest the chunk edge where both neighbours agree on the ball,
in 2D and 3D, for 11 frames running.

Every stage leaves a marker in half/<tag>/, so a rerun after a crash or a
reboot redoes only unfinished stages (drun and the player pieces keep their
own 5-minute parts).

Outputs, as hybrid.sh writes them for a window:
  alltests/p2d/cls_B/<tag>.npz, homog/<tag>_ball3dd.npz        shipped picks + 3D
  alltests/p2d/hyb90/<tag>.npz, h3d_hyb90/<tag>_ball3dd.npz    Hybrid 90 + 3D
  plab/out/<tag>_dets.npz, <tag>_feat.npz                      players
  half/<tag>/seams.json, log.txt
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
import time

import numpy as np

from . import drun, script

S2 = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(S2))
HOM = os.path.join(S2, 'homog')
AT = os.path.join(S2, 'alltests')
TNX = os.path.join(S2, 'tapnext')
H3D = os.path.join(S2, 'h3d_hyb90')
PY = sys.executable

AGREE = 5  # frames each side of a cut that must agree
TOL2, TOL3 = 1.0, 0.5  # agreement: px in 2D, StatsBomb units in 3D
# CPU stages run as many processes as there are cores, so each keeps one BLAS
# thread: 16 scan workers x 28 OpenBLAS threads beside drun ran out of commit
ONE_THREAD = dict(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
MIN_CORE = drun.CHUNK // 5  # a minute: a shorter end chunk joins its neighbour
LANES = 2  # the ball's CPU jobs at a time
LOOKAHEAD = 10  # dense.OFFS: the ball detector reads 10 frames each side


def scorable(video):
    """The first and last frames a pass can score: the video's frames are
    0..count-1 and the ball detector reads LOOKAHEAD past either side."""
    import cv2

    n = int(cv2.VideoCapture(video).get(cv2.CAP_PROP_FRAME_COUNT))
    return LOOKAHEAD, n - 1 - LOOKAHEAD


class Half:
    def __init__(self, tag, lo, hi, workers, overlap, lanes=LANES):
        self.tag, self.lo, self.hi, self.workers, self.ov, self.lanes = (
            tag,
            lo,
            hi,
            workers,
            overlap,
            lanes,
        )
        self.dir = os.path.join(S2, 'half', tag)
        os.makedirs(self.dir, exist_ok=True)
        self.chunks = drun.chunk_edges(lo, hi)
        # a chunk under a minute (the half's ragged ends) joins its neighbour's piece
        cores = []
        for a, b in self.chunks:
            if cores and (b - a + 1 < MIN_CORE or cores[-1][1] - cores[-1][0] + 1 < MIN_CORE):
                cores[-1] = (cores[-1][0], b)
            else:
                cores.append((a, b))
        self.pieces = []
        for k, (a, b) in enumerate(cores):
            self.pieces.append(
                dict(
                    k=k,
                    ptag='%s_q%03d' % (tag, a // drun.CHUNK),
                    core=(a, b),
                    win=(max(lo, a - overlap), min(hi, b + overlap)),
                )
            )
        self.log_f = open(os.path.join(self.dir, 'log.txt'), 'a', encoding='utf-8')
        self.t0 = time.time()
        self.err = None
        self.lock = threading.Lock()
        self.procs = set()
        self.claimed = set()
        self.env = dict(os.environ, PYTHONPATH=REPO + os.pathsep + os.environ.get('PYTHONPATH', ''))

    # ---- bookkeeping ------------------------------------------------------------------

    def say(self, msg):
        line = '[%6.1f min] %s' % ((time.time() - self.t0) / 60.0, msg)
        with self.lock:
            print(line, flush=True)
            self.log_f.write(line + '\n')
            self.log_f.flush()

    def done(self, ptag, stage):
        return os.path.exists(os.path.join(self.dir, '%s.%s.done' % (ptag, stage)))

    def mark(self, ptag, stage):
        open(os.path.join(self.dir, '%s.%s.done' % (ptag, stage)), 'w').close()

    def run(self, cwd, *cmd, env=None, log=None):
        """Tracker script cmd[0] ('homog.vballsb') with args cmd[1:], from folder cwd."""
        e = dict(self.env, **(env or {}))
        proc = subprocess.Popen(
            [PY, '-W', 'ignore'] + script(cmd[0]) + list(cmd[1:]),
            cwd=cwd,
            env=e,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        with self.lock:
            self.procs.add(proc)
        try:
            out, _ = proc.communicate()
        finally:
            with self.lock:
                self.procs.discard(proc)
        if log:
            with open(log, 'a', encoding='utf-8') as f:
                f.write('$ %s\n%s\n' % (' '.join(cmd), out))
        if proc.returncode:
            raise RuntimeError('%s failed in %s:\n%s' % (' '.join(cmd), cwd, out[-2000:]))
        return out

    def kill_all(self):
        """Every child process tree, so a failure never leaves a pool waiting on a dead worker."""
        with self.lock:
            procs = list(self.procs)
        for proc in procs:
            if proc.poll() is None:
                subprocess.run(['taskkill', '/T', '/F', '/PID', str(proc.pid)], capture_output=True)

    # ---- what is ready ----------------------------------------------------------------

    def drun_res(self, a, b):
        """drun's worker results for every chunk touching a..b, or None if one is unfinished."""
        res = []
        for clo, chi in self.chunks:
            if chi < a or clo > b:
                continue
            r = drun.load_chunk(self.tag, clo // drun.CHUNK, clo, chi)
            if r is None:
                return None
            res.extend(r)
        return res

    def dets_ready(self, a, b):
        from pitchiq.experiments import player_lab as L

        return all(
            os.path.exists(L._part(self.tag, 'dets', clo, chi))
            for clo, chi in self.chunks
            if not (chi < a or clo > b)
        )

    # ---- piece stages -----------------------------------------------------------------

    def build(self, p):
        """<ptag>.npz / pan / ppl cut from the finished chunks, registration copied."""
        from pitchiq.experiments import player_lab as L

        a, b = p['win']
        P = p['ptag']
        drun.merge(P, a, b, self.drun_res(a, b))
        F, B, C = [], [], []
        for clo, chi in self.chunks:
            if chi < a or clo > b:
                continue
            with np.load(L._part(self.tag, 'dets', clo, chi)) as d:
                m = (d['conf'] >= 0.25) & (d['frame'] >= a) & (d['frame'] <= b)  # ppl.from_dets
                F.append(d['frame'][m])
                B.append(d['box'][m])
                C.append(d['conf'][m])
        np.savez_compressed(
            os.path.join(S2, '%sppl.npz' % P),
            f=np.concatenate(F).astype(np.int32),
            box=np.concatenate(B).astype(np.float32),
            conf=np.concatenate(C).astype(np.float32),
            lo=a,
            hi=b,
            imgsz=1280,
            minconf=0.25,
        )
        self.registration(p)

    def registration(self, p):
        """The half's registration cut to the piece's window (the chain reads it only there)."""
        a, b = p['win']
        P = p['ptag']
        with np.load(os.path.join(HOM, '%s_reg.npz' % self.tag)) as r:
            m = (r['abs'] >= a) & (r['abs'] <= b)
            np.savez(os.path.join(HOM, '%s_reg.npz' % P), **{k: r[k][m] for k in r.files})
        venue = os.path.join(HOM, '%s_venue.npz' % self.tag)
        if os.path.exists(venue):
            shutil.copy2(venue, os.path.join(HOM, '%s_venue.npz' % P))

    def classify(self, p):
        self.run(S2, 'clsscore', 'cls_B.pt', p['ptag'])

    def shipped(self, p):
        P = p['ptag']
        log = os.path.join(HOM, '%s_chain.log' % P)
        self.run(AT, 'alltests.mkpicks_one', P, log=log, env=ONE_THREAD)
        for cmd in (
            ('homog.vballsb', P),
            ('homog.vfl0', P),
            ('homog.vflscan2', P, '16'),
            ('homog.vrowspd', P),
            ('homog.vflchain2', P),
        ):
            self.run(HOM, *cmd, log=log, env=ONE_THREAD)
        self.run(HOM, 'homog.vsmooth', P, env=dict(ONE_THREAD, VS_B3='%s_ball3d2.npz' % P), log=log)
        self.run(
            HOM,
            'homog._mk',
            '%s_ball3d2.npz' % P,
            '%s_smooth.npz' % P,
            '%s_ball3dd.npz' % P,
            log=log,
            env=ONE_THREAD,
        )

    def bridges(self, p):
        # neighbours already bridged lend their tracks in the overlap (same gap, picks and frames)
        near = [
            q['ptag']
            for q in self.pieces
            if abs(q['k'] - p['k']) == 1 and self.done(q['ptag'], 'bridges')
        ]
        self.run(
            TNX,
            'tapnext.tn_engine',
            'bridges',
            p['ptag'],
            env=dict(TN_NEAR=','.join(near), PYTHONUNBUFFERED='1'),
        )

    def hybrid(self, p):
        P = p['ptag']
        self.run(AT, 'alltests.hybrid', P, env=ONE_THREAD)
        os.makedirs(H3D, exist_ok=True)
        for kind in ('reg', 'venue'):
            src = os.path.join(HOM, '%s_%s.npz' % (P, kind))
            if os.path.exists(src):
                shutil.copy2(src, H3D)
        self.run(AT, 'alltests.mk3d', 'hyb90', P, env=ONE_THREAD)

    # ---- the queues ----------------------------------------------------------------------
    # Four kinds of lane. The players' detector spends four fifths of its time on
    # the CPU (colour samples of ~50 boxes a frame, decoding), so it gets a GPU lane
    # of its own and the ball's GPU jobs fill the card meanwhile. TAPNext alone keeps
    # the card busy, so one bridges lane. The classifier is mostly decoding and crop
    # cutting, so it gets a lane of its own: sharing the bridges' lane, a 15-minute
    # bridges job held every other piece's classify (and so its shipped chain and its
    # bridges) back, ~10 min of a day half (pi1 log, 2026-10-16). The ball's CPU jobs
    # (shipped chain, hybrid, build) run `lanes` at a time. Every job reads
    # only finished files and was checked to give the same bytes whatever runs
    # beside it, so the order changes no output.

    def claim(self, cands):
        """The first (stage, piece) of cands not done and not taken by another lane."""
        with self.lock:
            for stage, p in cands:
                key = (p['ptag'], stage)
                if key not in self.claimed and not self.done(*key):
                    self.claimed.add(key)
                    return stage, p
        return None, None

    def players_loop(self):
        from pitchiq.experiments import player_lab as L

        try:
            ex = L.Extractor()
            first_row = 0
            for clo, chi in self.chunks:
                if self.err is not None:
                    return
                if not os.path.exists(L._part(self.tag, 'dets', clo, chi)):
                    t = time.time()
                    ex.dets_part(self.tag, clo, chi)
                    self.say('players  %d..%d detected  (%.0f s)' % (clo, chi, time.time() - t))
            for clo, chi in self.chunks:
                if self.err is not None:
                    return
                t = time.time()
                _, n = ex.feat_part_from_dets(self.tag, clo, chi, first_row)
                first_row += n
                self.say('players  %d..%d features  (%.0f s)' % (clo, chi, time.time() - t))
            while self.err is None and not all(self.done(p['ptag'], 'build') for p in self.pieces):
                time.sleep(5)
            if self.err is None:
                self.merge_players()
        except BaseException as e:  # noqa: BLE001 - reported and re-raised by main; a thread
            # that let SystemExit (player_lab's 'video ended at') through would die without a word
            self.err = e

    def ball_gpu_loop(self, stage, after):
        """One GPU stage, piece by piece as `after` finishes them: bridges, or the classifier."""
        try:
            while self.err is None:
                stage_, p = self.claim(
                    [(stage, p) for p in self.pieces if self.done(p['ptag'], after)]
                )
                if stage_ is None:
                    if all(self.done(p['ptag'], stage) for p in self.pieces):
                        return
                    time.sleep(5)
                    continue
                self.do(stage, p)
        except BaseException as e:  # noqa: BLE001
            self.err = e

    def cpu_loop(self):
        """Hybrids first (they finish pieces), then the shipped chain, then builds."""
        try:
            while self.err is None:
                stage, p = self.claim(
                    [('hybrid', p) for p in self.pieces if self.done(p['ptag'], 'bridges')]
                    + [
                        ('shipped', p)
                        for p in self.pieces
                        if self.done(p['ptag'], 'build') and self.done(p['ptag'], 'classify')
                    ]
                    + [
                        ('build', p)
                        for p in self.pieces
                        if not self.done(p['ptag'], 'build')
                        and self.drun_res(*p['win']) is not None
                        and self.dets_ready(*p['win'])
                    ]
                )
                if stage is None:
                    if all(self.done(p['ptag'], 'hybrid') for p in self.pieces):
                        return
                    time.sleep(5)
                    continue
                self.do(stage, p)
        except BaseException as e:  # noqa: BLE001
            self.err = e

    def merge_players(self):
        from pitchiq.experiments import player_lab as L

        L.extract(self.tag, self.lo, self.hi - self.lo + 1, 1280, 0.05)  # parts done: merges only
        L.features(self.tag)
        self.say('players merged')

    def do(self, stage, p):
        t = time.time()
        getattr(self, stage)(p)
        self.mark(p['ptag'], stage)
        self.say('%-8s %s  (%.0f s)' % (stage, p['ptag'], time.time() - t))

    # ---- stitching ----------------------------------------------------------------------

    def stitch(self):
        """Join the pieces at seams where both neighbours agree, in 2D and in 3D."""

        def load(ver, P):
            p2 = np.load(os.path.join(AT, 'p2d', ver, '%s.npz' % P))
            d3 = np.load(os.path.join(HOM if ver == 'cls_B' else H3D, '%s_ball3dd.npz' % P))
            return p2, d3

        def same(a, b, keys, tol):
            # floats: missing in the same frames and within tol
            for k in keys:
                if a[k].dtype.kind != 'f':
                    if not np.array_equal(a[k], b[k]):
                        return False
                    continue
                na, nb = np.isnan(a[k]), np.isnan(b[k])
                if not np.array_equal(na, nb) or np.any(np.abs(a[k][~na] - b[k][~nb]) > tol):
                    return False
            return True

        seams, cuts = [], [self.lo]
        for left, right in zip(self.pieces[:-1], self.pieces[1:]):
            edge = right['core'][0]
            lo = max(left['win'][0], right['win'][0]) + AGREE
            hi = min(left['win'][1], right['win'][1]) - AGREE
            V = {v: (load(v, left['ptag']), load(v, right['ptag'])) for v in ('cls_B', 'hyb90')}

            def agree(c):
                for (l2, l3), (r2, r3) in V.values():
                    for A, B, keys, tol in (
                        (l2, r2, ('x', 'y', 'src'), TOL2),
                        (l3, r3, ('x', 'y', 'z', 'state'), TOL3),
                    ):
                        ia = np.searchsorted(A['abs'], c - AGREE)
                        ib = np.searchsorted(B['abs'], c - AGREE)
                        n = 2 * AGREE + 1
                        sa = {k: A[k][ia : ia + n] for k in keys}
                        sb = {k: B[k][ib : ib + n] for k in keys}
                        if (
                            len(sa[keys[0]]) != n
                            or len(sb[keys[0]]) != n
                            or not same(sa, sb, keys, tol)
                        ):
                            return False
                return True

            cut = None
            for d in range(0, max(edge - lo, hi - edge) + 1):
                for c in (edge - d, edge + d):
                    if lo <= c <= hi and agree(c):
                        cut = c
                        break
                if cut is not None:
                    break
            seams.append(
                dict(
                    left=left['ptag'],
                    right=right['ptag'],
                    edge=edge,
                    cut=edge if cut is None else cut,
                    agreed=cut is not None,
                )
            )
            if cut is None:
                self.say(
                    'SEAM %s|%s: no agreeing frame within the overlap; cut at the chunk edge %d'
                    % (left['ptag'], right['ptag'], edge)
                )
            cuts.append(edge if cut is None else cut)
        cuts.append(self.hi + 1)

        for ver in ('cls_B', 'hyb90'):
            two, three = {}, {}
            for p, a, b in zip(self.pieces, cuts[:-1], cuts[1:]):
                p2, d3 = load(ver, p['ptag'])
                m2 = (p2['abs'] >= a) & (p2['abs'] < b)
                m3 = (d3['abs'] >= a) & (d3['abs'] < b)
                for k in p2.files:
                    two.setdefault(k, []).append(p2[k][m2])
                for k in d3.files:
                    three.setdefault(k, []).append(d3[k][m3])
            two = {k: np.concatenate(v) for k, v in two.items()}
            three = {k: np.concatenate(v) for k, v in three.items()}
            assert np.array_equal(two['abs'], np.arange(self.lo, self.hi + 1)), (
                'stitched 2D has gaps'
            )
            np.savez(os.path.join(AT, 'p2d', ver, '%s.npz' % self.tag), **two)
            np.savez(
                os.path.join(HOM if ver == 'cls_B' else H3D, '%s_ball3dd.npz' % self.tag), **three
            )
        # the smoothed camera (H per frame) that match_video, spotlab and panning_camera draw with,
        # and the ball track (per-frame H + image point) that vflight.Track reads
        for name in ('smooth2', 'ballsb', 'img'):
            sm = {}
            for p, a, b in zip(self.pieces, cuts[:-1], cuts[1:]):
                with np.load(os.path.join(H3D, '%s_%s.npz' % (p['ptag'], name))) as s:
                    m = (s['abs'] >= a) & (s['abs'] < b)
                    for k in s.files:
                        sm.setdefault(k, []).append(s[k][m])
            sm = {k: np.concatenate(v) for k, v in sm.items()}
            assert np.array_equal(sm['abs'], np.arange(self.lo, self.hi + 1)), (
                'stitched %s has gaps' % name
            )
            np.savez(os.path.join(H3D, '%s_%s.npz' % (self.tag, name)), **sm)
        for kind in ('reg', 'venue'):
            src = os.path.join(HOM, '%s_%s.npz' % (self.tag, kind))
            if os.path.exists(src):
                shutil.copy2(src, H3D)
        json.dump(seams, open(os.path.join(self.dir, 'seams.json'), 'w'), indent=1)
        self.say(
            'stitched %d pieces, %d of %d seams cut where both sides agree'
            % (len(self.pieces), sum(s['agreed'] for s in seams), len(seams))
        )

    # ---- the run ----------------------------------------------------------------------------

    def main(self):
        from pitchiq.experiments import player_lab as L

        if L.CHUNK != drun.CHUNK:
            raise SystemExit('PLAB_CHUNK and DRUN_CHUNK must match')
        if not os.path.exists(os.path.join(HOM, '%s_reg.npz' % self.tag)):
            raise SystemExit('homog/%s_reg.npz missing: run autocal_run --homog first' % self.tag)
        if 'PIQ_VIDEO' not in os.environ:
            self.say('PIQ_VIDEO is not set: this is the scrimmage video')
        first, last = scorable(L.VIDEO)
        if not first <= self.lo <= self.hi <= last:
            raise SystemExit(
                '%d..%d is not inside %d..%d, the frames this video lets a pass score'
                % (self.lo, self.hi, first, last)
            )
        self.say(
            'HALF %s  %d..%d  %d pieces, overlap %d frames, drun %d workers, %d lanes'
            % (self.tag, self.lo, self.hi, len(self.pieces), self.ov, self.workers, self.lanes)
        )
        dlog = open(os.path.join(self.dir, 'drun.log'), 'a', encoding='utf-8')
        drun_p = subprocess.Popen(
            [
                PY,
                '-W',
                'ignore',
                *script('drun'),
                str(self.lo),
                str(self.hi),
                str(self.workers),
                self.tag,
            ],
            cwd=S2,
            env=self.env,
            stdout=dlog,
            stderr=subprocess.STDOUT,
        )
        threads = [threading.Thread(target=self.players_loop)]
        threads += [threading.Thread(target=self.ball_gpu_loop, args=('bridges', 'shipped'))]
        threads += [threading.Thread(target=self.ball_gpu_loop, args=('classify', 'build'))]
        threads += [threading.Thread(target=self.cpu_loop) for _ in range(self.lanes)]
        for t in threads:
            t.start()
        with self.lock:
            self.procs.add(drun_p)
        killed = False
        while any(t.is_alive() for t in threads):
            time.sleep(5)
            if drun_p.poll() not in (None, 0) and self.err is None:
                self.err = RuntimeError(
                    'drun.py exited with %d (half/%s/drun.log)' % (drun_p.returncode, self.tag)
                )
            if self.err is not None and not killed:
                self.kill_all()  # the other thread may be blocked in a child
                killed = True
        if self.err is not None:
            self.say('FAILED: %s' % self.err)
            raise SystemExit(1)
        drun_p.wait()
        self.say('drun done')
        self.stitch()
        self.say('HALF DONE')


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('tag')
    ap.add_argument('lo', type=int)
    ap.add_argument('hi', type=int)
    ap.add_argument('--workers', type=int, default=12)
    ap.add_argument('--overlap', type=int, default=300)
    ap.add_argument('--lanes', type=int, default=LANES, help="the ball's CPU jobs at a time")
    ap.add_argument(
        '--stitch-only', action='store_true', help='re-stitch finished pieces, run nothing'
    )
    a = ap.parse_args()
    h = Half(a.tag, a.lo, a.hi, a.workers, a.overlap, a.lanes)
    h.stitch() if a.stitch_only else h.main()


if __name__ == '__main__':
    main()
