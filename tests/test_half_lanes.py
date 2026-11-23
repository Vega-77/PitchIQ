"""pitchiq/balltrack/half.py's lanes: every stage once, never before what it reads."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

from pitchiq.balltrack import half

S2 = os.path.dirname(os.path.abspath(half.__file__))
STAGES = ('build', 'classify', 'shipped')  # a piece's stages, in the order they read each other


def lanes(tmp_path, monkeypatch, after=None):
    """A half of four pieces whose every job only records itself, players included, and
    the lanes Half.main starts, not started yet."""
    log, lock = [], threading.Lock()
    dets, feats = set(), []

    def rec(name, key):
        time.sleep(0.01)
        with lock:
            log.append((name, key))

    class Ex:
        def dets_part(self, tag, a, b):
            rec('dets', a)
            dets.add(a)

        def feat_part_from_dets(self, tag, a, b, first_row):
            rec('feat', a)
            feats.append((a, first_row))
            return None, 10

    from pitchiq.experiments import player_lab

    monkeypatch.setattr(player_lab, 'Extractor', Ex)
    monkeypatch.setattr(
        player_lab, '_part', lambda tag, kind, a, b: tmp_path / ('%s_%d' % (kind, a))
    )
    monkeypatch.setattr(half, 'S2', str(tmp_path))
    monkeypatch.setattr(half, 'HOM', str(tmp_path))
    (tmp_path / 't_reg.npz').touch()  # the half's camera
    monkeypatch.setattr(
        half, 'time', SimpleNamespace(time=time.time, sleep=lambda s: time.sleep(0.01))
    )
    h = half.Half('t', 0, 4 * 9000 - 1, 6, 300, after=after)
    h.dir = str(tmp_path)
    h.say = lambda msg: None
    h.detector_res = lambda a, b: []
    h.dets_ready = lambda a, b: all(c in dets for c, d in h.chunks if not (d < a or c > b))
    for stage in STAGES:
        monkeypatch.setattr(h, stage, lambda p, stage=stage: rec(stage, p['ptag']))
    monkeypatch.setattr(h, 'merge_players', lambda: rec('merge', None))
    # the lanes Half.main starts: players, the classifier on its own GPU lane, the CPU jobs
    threads = [
        threading.Thread(target=h.players_loop),
        threading.Thread(target=h.ball_gpu_loop, args=('classify', 'build')),
    ]
    threads += [threading.Thread(target=h.cpu_loop) for _ in range(2)]
    return h, log, feats, threads


def finish(h, threads):
    for t in threads:
        t.join(60)
    assert h.err is None and not any(t.is_alive() for t in threads)


@pytest.fixture
def run(tmp_path, monkeypatch):
    h, log, feats, threads = lanes(tmp_path, monkeypatch)
    for t in threads:
        t.start()
    finish(h, threads)
    return h, log, feats


def test_every_stage_runs_once_after_what_it_reads(run):
    h, log, feats = run
    at = {k: i for i, k in enumerate(log)}
    assert len(at) == len(log)  # nothing twice
    for p in h.pieces:
        P = p['ptag']
        order = [at[(s, P)] for s in STAGES]
        assert order == sorted(order)
        a, b = p['win']
        assert all(at[('dets', c)] < at[('build', P)] for c, d in h.chunks if not (d < a or c > b))
    assert at[('merge', None)] > max(
        at[('build', p['ptag'])] for p in h.pieces
    )  # players merge last
    assert len(log) == len(STAGES) * len(h.pieces) + 2 * len(h.chunks) + 1


def test_features_number_their_rows_in_chunk_order(run):
    h, _, feats = run
    assert feats == [(c, 10 * k) for k, (c, _) in enumerate(h.chunks)]


def test_a_player_lane_that_exits_is_reported(tmp_path, monkeypatch):
    """player_lab raises SystemExit past the video's end; the lane must not die silently."""
    from pitchiq.experiments import player_lab

    class Ex:
        def dets_part(self, tag, a, b):
            raise SystemExit('video ended at %d' % b)

    monkeypatch.setattr(player_lab, 'Extractor', Ex)
    monkeypatch.setattr(
        player_lab, '_part', lambda tag, kind, a, b: tmp_path / ('%s_%d' % (kind, a))
    )
    monkeypatch.setattr(half, 'S2', str(tmp_path))
    h = half.Half('t', 0, 9000 - 1, 6, 300)
    h.players_loop()
    assert isinstance(h.err, SystemExit)


def test_scorable_frames_leave_the_detector_its_lookahead(tmp_path):
    import re

    import cv2
    import numpy as np

    path = str(tmp_path / 'v.avi')
    vw = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*'MJPG'), 30, (64, 48))
    for _ in range(30):
        vw.write(np.zeros((48, 64, 3), np.uint8))
    vw.release()
    assert half.scorable(path) == (10, 19)
    offs = re.search(
        r'^OFFS = list\(range\((-?\d+), (\d+)\)\)',
        open(os.path.join(S2, 'candidate_features.py')).read(),
        re.M,
    )
    assert -int(offs.group(1)) == int(offs.group(2)) - 1 == half.LOOKAHEAD


def test_with_after_only_the_shipped_chain_waits_for_its_file(tmp_path, monkeypatch):
    """--after (process_match's early pass): everything but the shipped chain runs at once."""
    go = tmp_path / 'ship_t'
    h, log, _, threads = lanes(tmp_path, monkeypatch, after=str(go))
    for t in threads:
        t.start()
    want = {('classify', p['ptag']) for p in h.pieces} | {('merge', None)}
    deadline = time.time() + 30
    while not want <= set(log) and time.time() < deadline:
        time.sleep(0.02)
    assert want <= set(log)
    time.sleep(0.3)  # the lanes poll every 10 ms here: time enough to ship too soon
    assert not [k for k in log if k[0] == 'shipped']
    go.touch()
    finish(h, threads)
    at = {k: i for i, k in enumerate(log)}
    assert len(at) == len(log)
    assert all(at[('shipped', p['ptag'])] > at[('classify', p['ptag'])] for p in h.pieces)


def test_a_camera_still_missing_when_its_file_comes_stops_the_half(tmp_path, monkeypatch):
    go = tmp_path / 'ship_t'
    h, log, _, threads = lanes(tmp_path, monkeypatch, after=str(go))
    (tmp_path / 't_reg.npz').unlink()
    go.touch()
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert isinstance(h.err, SystemExit) and 't_reg.npz missing' in str(h.err)
    assert not [k for k in log if k[0] == 'shipped']


def test_the_shipped_chain_copies_the_camera_to_its_piece_first(tmp_path, monkeypatch):
    """Build no longer reads the camera (2026-11-23), so the shipped chain copies it."""
    monkeypatch.setattr(half, 'S2', str(tmp_path))
    h = half.Half('t', 0, 9000 - 1, 6, 300)
    calls = []
    h.registration = lambda p: calls.append('registration')
    h.run = lambda cwd, *cmd, **kw: calls.append(cmd[0])
    h.shipped(h.pieces[0])
    assert calls[0] == 'registration' and calls[1] == 'evaluation.mkpicks_one'
