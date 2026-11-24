"""A whole half's ball candidates, shipped ball and player detections, pipelined.

    PIQ_VIDEO=<video> python -m pitchiq.balltrack.half <tag> <lo> <hi> [--workers 12] [--overlap 300]
        [--after <file>]

Needs pitch_registration/<tag>_reg.npz (and <tag>_venue.npz), from the coach's pins
(`experiments/pincal.py`) or `python -m pitchiq.experiments.autocal_run <tag> ... --homog`.
Only the shipped chain reads them (it copies them to each piece first), so with --after
the pass starts without them: the detectors, the players and the classifier run, and
the shipped chain waits until <file> exists. process_match writes that file once the
coach's pins are in and the camera is accepted, so a half is detected while the coach
pins (session step 3.12). half/<tag>/early.done says everything else has finished.

detect_candidates.py runs over the whole half on the CPU. Each of its 5-minute chunks
becomes a piece as soon as the chunks around it are done: the chunk plus
`overlap` frames each side, run through the first part of the chain hybrid.sh runs on a
lab window (classifier, shipped picks, shipped 3D) under its own tag <tag>_q<slot>.
Meanwhile the GPU detects the players chunk by chunk. At the end the pieces are
stitched: each seam is cut at the frame nearest the chunk edge where both neighbours
agree on the shipped ball, in 2D and 3D, for 11 frames running.

The ball that the stats use is ball17's (`ball17.py`, after this, from these pieces).
Until 2026-11-21 the pass also built the Hybrid 90 ball it replaced (TAPNext bridges on
the GPU, 63-68 min a Woodbridge half; hybrid and its own 3D, 12-17 CPU min) and cut the
seams where both balls agreed. Nothing read that ball by then but the map's camera, which
now comes from the shipped chain's pieces (`camera.py`).

Every stage leaves a marker in half/<tag>/, so a rerun after a crash or a
reboot redoes only unfinished stages (detect_candidates and the player pieces keep their
own 5-minute parts).

Outputs:
  evaluation/p2d/cls_B/<tag>.npz, pitch_registration/<tag>_ball3dd.npz   shipped picks + 3D
  pitch_registration/<tag>_camera.npz                          the camera on every frame (camera.py)
  plab/out/<tag>_dets.npz, <tag>_feat.npz                      players
  half/<tag>/seams.json, log.txt
and per piece, what ball17 reads: <tag>_qNNN.npz (candidates), <tag>_qNNNcls.npz (scores).
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

from . import camera, config, detect_candidates, script
from ..extras import MissingExtra, check

S2 = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(S2))
HOM = os.path.join(S2, 'pitch_registration')
AT = os.path.join(S2, 'evaluation')
PY = sys.executable

AGREE = 5  # frames each side of a cut that must agree
TOL2, TOL3 = 1.0, 0.5  # agreement: px in 2D, StatsBomb units in 3D
# CPU stages run as many processes as there are cores, so each keeps one BLAS
# thread: 16 scan workers x 28 OpenBLAS threads beside detect_candidates ran out of commit
ONE_THREAD = dict(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
MIN_CORE = detect_candidates.CHUNK // 5  # a minute: a shorter end chunk joins its neighbour
LANES = 2  # the ball's CPU jobs at a time
# PIQ_CHAIN_LOW=1: the shipped chain below normal priority (session step 3.17). The detector is
# the pass's critical path, and each chain's 16 flight-search workers take cores from it: its
# workers ran 29% slower inside the wbt pass than alone (wbt2 k015, 362 s against 280 s).
CHAIN_LOW = os.environ.get('PIQ_CHAIN_LOW', '0') == '1'
LOOKAHEAD = 10  # candidate_features.OFFS: the ball detector reads 10 frames each side


def kill_tree(pid):
    """A process and everything it started, at once: taskkill /T on Windows, psutil elsewhere
    (Linux has no taskkill: the CI's tests stop a pass there too)."""
    if sys.platform == 'win32':
        subprocess.run(['taskkill', '/T', '/F', '/PID', str(pid)], capture_output=True)
        return
    import psutil

    try:
        root = psutil.Process(pid)
        procs = [*root.children(recursive=True), root]
    except psutil.Error:
        return
    for p in procs:
        try:
            p.kill()
        except psutil.Error:  # it ended between the listing and the kill
            pass


def free_gpu():
    """Hand the card's cached memory back once this process's networks are gone."""
    torch = sys.modules.get('torch')
    if torch is not None and torch.cuda.is_available():
        import gc

        gc.collect()
        torch.cuda.empty_cache()


def scorable(video):
    """The first and last frames a pass can score: the video's frames are
    0..count-1 and the ball detector reads LOOKAHEAD past either side."""
    import cv2

    n = int(cv2.VideoCapture(video).get(cv2.CAP_PROP_FRAME_COUNT))
    return LOOKAHEAD, n - 1 - LOOKAHEAD


def piece_ball(ptag):
    """A piece's (or a half's) shipped ball: its 2D picks and its 3D ball, as dicts of arrays."""
    with np.load(os.path.join(AT, 'p2d', 'cls_B', '%s.npz' % ptag)) as p2:
        two = dict(p2)
    with np.load(os.path.join(HOM, '%s_ball3dd.npz' % ptag)) as d3:
        return two, dict(d3)


def same(a, b, keys, tol):
    """Whether two runs of frames hold the same ball: equal, or for floats missing in the same
    frames and within tol."""
    for k in keys:
        if a[k].dtype.kind != 'f':
            if not np.array_equal(a[k], b[k]):
                return False
            continue
        na, nb = np.isnan(a[k]), np.isnan(b[k])
        if not np.array_equal(na, nb) or np.any(np.abs(a[k][~na] - b[k][~nb]) > tol):
            return False
    return True


def seam_cut(left, right, edge, lo, hi):
    """The frame nearest `edge` within lo..hi where two neighbouring pieces' balls (each a
    piece_ball) agree in 2D and 3D for the AGREE frames each side; None if none does."""
    n = 2 * AGREE + 1

    def agree(c):
        for A, B, keys, tol in (
            (left[0], right[0], ('x', 'y', 'src'), TOL2),
            (left[1], right[1], ('x', 'y', 'z', 'state'), TOL3),
        ):
            ia = np.searchsorted(A['abs'], c - AGREE)
            ib = np.searchsorted(B['abs'], c - AGREE)
            sa = {k: A[k][ia : ia + n] for k in keys}
            sb = {k: B[k][ib : ib + n] for k in keys}
            if len(sa[keys[0]]) != n or len(sb[keys[0]]) != n or not same(sa, sb, keys, tol):
                return False
        return True

    for d in range(0, max(edge - lo, hi - edge) + 1):
        for c in (edge - d, edge + d):
            if lo <= c <= hi and agree(c):
                return c
    return None


class Half:
    def __init__(self, tag, lo, hi, workers, overlap, lanes=LANES, after=None):
        """Split lo..hi into pieces (one per detector chunk, ragged ends merged into a
        neighbour), each with its core and its window widened by `overlap`; open half/<tag>/log.txt.
        With `after`, the shipped chain waits until that file exists (camera_ready).
        """
        self.tag, self.lo, self.hi, self.workers, self.ov, self.lanes = (
            tag,
            lo,
            hi,
            workers,
            overlap,
            lanes,
        )
        self.after = after
        self.ready = False  # camera_ready has said yes
        self.dir = os.path.join(S2, 'half', tag)
        os.makedirs(self.dir, exist_ok=True)
        self.chunks = detect_candidates.chunk_edges(lo, hi)
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
                    ptag='%s_q%03d' % (tag, a // detect_candidates.CHUNK),
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
        """Print msg with the minutes since start, and append it to the half's log."""
        line = '[%6.1f min] %s' % ((time.time() - self.t0) / 60.0, msg)
        with self.lock:
            print(line, flush=True)
            self.log_f.write(line + '\n')
            self.log_f.flush()

    def done(self, ptag, stage):
        """Whether `stage` has finished for piece ptag (its marker file exists)."""
        return os.path.exists(os.path.join(self.dir, '%s.%s.done' % (ptag, stage)))

    def mark(self, ptag, stage):
        """Record that `stage` has finished for piece ptag."""
        open(os.path.join(self.dir, '%s.%s.done' % (ptag, stage)), 'w').close()

    def run(self, cwd, *cmd, env=None, log=None, low=False):
        """Tracker script cmd[0] ('pitch_registration.ball_on_pitch') with args cmd[1:], from folder
        cwd; `low`: it and everything it starts below normal priority (Windows only)."""
        e = dict(self.env, **(env or {}))
        proc = subprocess.Popen(
            [PY, '-W', 'ignore'] + script(cmd[0]) + list(cmd[1:]),
            cwd=cwd,
            env=e,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0) if low else 0,
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
                kill_tree(proc.pid)

    # ---- what is ready ----------------------------------------------------------------

    def detector_res(self, a, b):
        """The detector's worker results for every chunk touching a..b, or None if one is unfinished."""
        res = []
        for clo, chi in self.chunks:
            if chi < a or clo > b:
                continue
            r = detect_candidates.load_chunk(self.tag, clo // detect_candidates.CHUNK, clo, chi)
            if r is None:
                return None
            res.extend(r)
        return res

    def dets_ready(self, a, b):
        """Whether the players' detections cover every chunk that frames a..b touch."""
        from pitchiq.experiments import player_lab

        return all(
            os.path.exists(player_lab._part(self.tag, 'dets', clo, chi))
            for clo, chi in self.chunks
            if not (chi < a or clo > b)
        )

    def camera_ready(self):
        """Whether the shipped chain may start: at once without --after, else once that file
        exists. The half's camera must be there by then."""
        if not self.ready:
            if self.after is not None and not os.path.exists(self.after):
                return False
            if not os.path.exists(os.path.join(HOM, '%s_reg.npz' % self.tag)):
                raise SystemExit(
                    'pitch_registration/%s_reg.npz missing: calibrate the half first (pincal, '
                    'or autocal_run --homog)' % self.tag
                )
            self.ready = True
        return True

    # ---- piece stages -----------------------------------------------------------------

    def build(self, p):
        """<ptag>.npz / pan / ppl cut from the finished chunks. (The registration is copied
        by `shipped`, the first stage that reads it; until 2026-11-23 build copied it.)"""
        from pitchiq.experiments import player_lab

        a, b = p['win']
        P = p['ptag']
        detect_candidates.merge(P, a, b, self.detector_res(a, b))
        F, B, C = [], [], []
        for clo, chi in self.chunks:
            if chi < a or clo > b:
                continue
            with np.load(player_lab._part(self.tag, 'dets', clo, chi)) as d:
                m = (
                    (d['conf'] >= 0.25) & (d['frame'] >= a) & (d['frame'] <= b)
                )  # person_boxes.from_dets
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
        """Crop classifier log-odds for the piece's candidates (score_candidates; cls_C since the
        2026-10-26 retrain on Ball Clicker answers, its outputs still under the cls_B names)."""
        self.run(S2, 'score_candidates', 'cls_C.pt', p['ptag'])

    def shipped(self, p):
        """The shipped tracker's picks and the 3D chain on them, for one piece."""
        self.registration(p)
        P = p['ptag']
        log = os.path.join(HOM, '%s_chain.log' % P)
        low = CHAIN_LOW
        self.run(AT, 'evaluation.mkpicks_one', P, log=log, env=ONE_THREAD, low=low)
        for cmd in (
            ('pitch_registration.ball_on_pitch', P),
            ('pitch_registration.image_track', P),
            ('pitch_registration.flight_scan', P, '16'),
            ('pitch_registration.roll_speed_cache', P),
            ('pitch_registration.flight_chain', P),
        ):
            self.run(HOM, *cmd, log=log, env=ONE_THREAD, low=low)
        self.run(
            HOM,
            'pitch_registration.smooth_ball',
            P,
            env=dict(ONE_THREAD, VS_B3='%s_ball3d2.npz' % P),
            log=log,
            low=low,
        )
        self.run(
            HOM,
            'pitch_registration.merge_air_and_ground',
            '%s_ball3d2.npz' % P,
            '%s_smooth.npz' % P,
            '%s_ball3dd.npz' % P,
            '%s_scan2.npz' % P,
            '%s_venue.npz' % P,
            log=log,
            env=ONE_THREAD,
            low=low,
        )

    # ---- the queues ----------------------------------------------------------------------
    # Three kinds of lane. The players' detector spends four fifths of its time on
    # the CPU (colour samples of ~50 boxes a frame, decoding), so it gets a GPU lane
    # of its own and the ball's classifier fills the card meanwhile, in a lane of its own
    # (mostly decoding and crop cutting). The ball's CPU jobs (build, shipped chain) run
    # `lanes` at a time. Every job reads only finished files and was checked to give the
    # same bytes whatever runs beside it, so the order changes no output. (Until 2026-11-21
    # a fourth lane ran TAPNext bridges for the Hybrid 90 ball; see the docstring.)

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
        """The players' lane: detect every chunk, then features, then merge once every
        piece is built.  An exception is stored in self.err for main to report.
        """
        from pitchiq.experiments import player_lab

        try:
            ex = player_lab.Extractor()
            first_row = 0
            for clo, chi in self.chunks:
                if self.err is not None:
                    return
                if not os.path.exists(player_lab._part(self.tag, 'dets', clo, chi)):
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
            del ex  # the players' networks: a pass waiting for its camera (--after) leaves
            free_gpu()  # the card to the next half's
        except BaseException as e:  # noqa: BLE001 - reported and re-raised by main; a thread
            # that let SystemExit (player_lab's 'video ended at') through would die without a word
            self.err = e

    def ball_gpu_loop(self, stage, after):
        """One GPU stage, piece by piece as `after` finishes them: the classifier."""
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
        """The shipped chain first (it finishes pieces) once the camera is ready, then builds."""
        try:
            while self.err is None:
                ready = self.camera_ready()
                stage, p = self.claim(
                    [
                        ('shipped', p)
                        for p in self.pieces
                        if ready
                        and self.done(p['ptag'], 'build')
                        and self.done(p['ptag'], 'classify')
                    ]
                    + [
                        ('build', p)
                        for p in self.pieces
                        if not self.done(p['ptag'], 'build')
                        and self.detector_res(*p['win']) is not None
                        and self.dets_ready(*p['win'])
                    ]
                )
                if stage is None:
                    if all(self.done(p['ptag'], 'shipped') for p in self.pieces):
                        return
                    time.sleep(5)
                    continue
                self.do(stage, p)
        except BaseException as e:  # noqa: BLE001
            self.err = e

    def merge_players(self):
        """Merge the players' chunk parts into the half's detections and features."""
        from pitchiq.experiments import player_lab

        player_lab.extract(
            self.tag, self.lo, self.hi - self.lo + 1, 1280, 0.05
        )  # parts done: merges only
        player_lab.features(self.tag)
        self.say('players merged')

    def do(self, stage, p):
        """Run `stage` on piece p, mark it done and log how long it took."""
        t = time.time()
        getattr(self, stage)(p)
        self.mark(p['ptag'], stage)
        self.say('%-8s %s  (%.0f s)' % (stage, p['ptag'], time.time() - t))

    # ---- stitching ----------------------------------------------------------------------

    def stitch(self):
        """Join the pieces at seams where both neighbours agree on the shipped ball, in 2D
        and in 3D; write the half's picks, 3D ball and camera, and seams.json."""
        balls = [piece_ball(p['ptag']) for p in self.pieces]
        seams, cuts = [], [self.lo]
        for k, (left, right) in enumerate(zip(self.pieces[:-1], self.pieces[1:])):
            edge = right['core'][0]
            lo = max(left['win'][0], right['win'][0]) + AGREE
            hi = min(left['win'][1], right['win'][1]) - AGREE
            cut = seam_cut(balls[k], balls[k + 1], edge, lo, hi)
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

        two, three = {}, {}
        for (p2, d3), a, b in zip(balls, cuts[:-1], cuts[1:]):
            m2 = (p2['abs'] >= a) & (p2['abs'] < b)
            m3 = (d3['abs'] >= a) & (d3['abs'] < b)
            for k in p2:
                two.setdefault(k, []).append(p2[k][m2])
            for k in d3:
                three.setdefault(k, []).append(d3[k][m3])
        two = {k: np.concatenate(v) for k, v in two.items()}
        three = {k: np.concatenate(v) for k, v in three.items()}
        assert np.array_equal(two['abs'], np.arange(self.lo, self.hi + 1)), 'stitched 2D has gaps'
        np.savez(os.path.join(AT, 'p2d', 'cls_B', '%s.npz' % self.tag), **two)
        np.savez(os.path.join(HOM, '%s_ball3dd.npz' % self.tag), **three)
        # the map's camera, from the shipped chain's pieces (camera.py)
        camera.stitch(self.tag, [p['ptag'] for p in self.pieces], cuts)
        json.dump(seams, open(os.path.join(self.dir, 'seams.json'), 'w'), indent=1)
        self.say(
            'stitched %d pieces, %d of %d seams cut where both sides agree'
            % (len(self.pieces), sum(s['agreed'] for s in seams), len(seams))
        )

    # ---- the run ----------------------------------------------------------------------------

    def main(self):
        """Run the whole half: start the detector, the players', GPU and CPU lanes, wait for
        them (stopping everything on the first error), then stitch. With --after, the
        shipped chain waits for that file and half/<tag>/early.done is written once
        everything else is done (process_match starts the next half then).
        """
        from pitchiq.experiments import player_lab

        if player_lab.CHUNK != detect_candidates.CHUNK:
            raise SystemExit('PLAB_CHUNK and DRUN_CHUNK must match')
        if self.after is None:
            self.camera_ready()  # says now, not after the detector, if the camera is missing
        early = os.path.join(self.dir, 'early.done')
        if os.path.exists(early):
            os.remove(early)  # written again below once this run has seen it so
        if 'PIQ_VIDEO' not in os.environ:
            self.say('PIQ_VIDEO is not set: this is the scrimmage video')
        first, last = scorable(player_lab.VIDEO)
        if not first <= self.lo <= self.hi <= last:
            raise SystemExit(
                '%d..%d is not inside %d..%d, the frames this video lets a pass score'
                % (self.lo, self.hi, first, last)
            )
        self.say(
            'HALF %s  %d..%d  %d pieces, overlap %d frames, detector %d workers, %d lanes'
            % (self.tag, self.lo, self.hi, len(self.pieces), self.ov, self.workers, self.lanes)
            + (', the shipped chain after %s' % self.after if self.after else '')
        )
        dlog = open(os.path.join(self.dir, 'drun.log'), 'a', encoding='utf-8')
        detector_p = subprocess.Popen(
            [
                PY,
                '-W',
                'ignore',
                *script('detect_candidates'),
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
        threads += [threading.Thread(target=self.ball_gpu_loop, args=('classify', 'build'))]
        threads += [threading.Thread(target=self.cpu_loop) for _ in range(self.lanes)]
        for t in threads:
            t.start()
        with self.lock:
            self.procs.add(detector_p)
        killed = False
        while any(t.is_alive() for t in threads):
            time.sleep(5)
            if detector_p.poll() not in (None, 0) and self.err is None:
                self.err = RuntimeError(
                    'detect_candidates.py exited with %d (half/%s/drun.log)'
                    % (detector_p.returncode, self.tag)
                )
            if self.err is not None and not killed:
                self.kill_all()  # the other thread may be blocked in a child
                killed = True
            if (
                self.err is None
                and not os.path.exists(early)
                and detector_p.poll() == 0
                and not any(t.is_alive() for t in threads[:2])  # players merged, all classified
            ):
                open(early, 'w').close()
                if self.after and not os.path.exists(self.after):  # (never raises here)
                    self.say(
                        'waiting for the camera (%s): only the shipped chain is left' % self.after
                    )
        if self.err is not None:
            self.say('FAILED: %s' % self.err)
            raise SystemExit(1)
        detector_p.wait()
        self.say('detector done')
        self.stitch()
        self.say('HALF DONE')


def main():
    """Command line: <tag> <lo> <hi> [--workers] [--overlap] [--lanes] [--after] [--stitch-only]."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('tag')
    ap.add_argument('lo', type=int)
    ap.add_argument('hi', type=int)
    ap.add_argument('--workers', type=int, default=config.HALF_WORKERS)
    ap.add_argument('--overlap', type=int, default=300)
    ap.add_argument('--lanes', type=int, default=LANES, help="the ball's CPU jobs at a time")
    ap.add_argument(
        '--after',
        help='hold the shipped chain (the only stage that reads the camera) until this file exists',
    )
    ap.add_argument(
        '--stitch-only', action='store_true', help='re-stitch finished pieces, run nothing'
    )
    a = ap.parse_args()
    if not a.stitch_only:
        try:
            check('balltrack')
        except MissingExtra as e:
            sys.exit(str(e))  # the message, with no traceback; exit status 1
    h = Half(a.tag, a.lo, a.hi, a.workers, a.overlap, a.lanes, a.after)
    h.stitch() if a.stitch_only else h.main()


if __name__ == '__main__':
    main()
