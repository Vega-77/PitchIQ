"""cv/balltrack/half.py's lanes: every stage once, never before what it reads."""
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

S2 = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'cv', 'balltrack')
sys.path.insert(0, S2)
import half  # noqa: E402


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A half of four pieces whose every job only records itself, players included."""
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

    from cv.experiments import player_lab as L
    monkeypatch.setattr(L, 'Extractor', Ex)
    monkeypatch.setattr(L, '_part', lambda tag, kind, a, b: tmp_path / ('%s_%d' % (kind, a)))
    monkeypatch.setattr(half, 'S2', str(tmp_path))
    monkeypatch.setattr(half, 'time', SimpleNamespace(time=time.time, sleep=lambda s: time.sleep(0.01)))
    h = half.Half('t', 0, 4 * 9000 - 1, 6, 300)
    h.dir = str(tmp_path)
    h.say = lambda msg: None
    h.drun_res = lambda a, b: []
    h.dets_ready = lambda a, b: all(c in dets for c, d in h.chunks if not (d < a or c > b))
    for stage in ('build', 'classify', 'shipped', 'bridges', 'hybrid'):
        monkeypatch.setattr(h, stage, lambda p, stage=stage: rec(stage, p['ptag']))
    threads = [threading.Thread(target=h.players_loop), threading.Thread(target=h.ball_gpu_loop)]
    threads += [threading.Thread(target=h.cpu_loop) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert h.err is None and not any(t.is_alive() for t in threads)
    return h, log, feats


def test_every_stage_runs_once_after_what_it_reads(run):
    h, log, feats = run
    at = {k: i for i, k in enumerate(log)}
    assert len(at) == len(log)                                   # nothing twice
    for p in h.pieces:
        P = p['ptag']
        order = [at[(s, P)] for s in ('build', 'classify', 'shipped', 'bridges', 'hybrid')]
        assert order == sorted(order)
        a, b = p['win']
        assert all(at[('dets', c)] < at[('build', P)] for c, d in h.chunks if not (d < a or c > b))
    assert len(log) == 5 * len(h.pieces) + 2 * len(h.chunks)


def test_features_number_their_rows_in_chunk_order(run):
    h, _, feats = run
    assert feats == [(c, 10 * k) for k, (c, _) in enumerate(h.chunks)]
