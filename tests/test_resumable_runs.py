"""Long runs resume: a crash or reboot mid-half must lose one 5-minute chunk, not the half.

drun.py cuts its window on absolute 5-minute boundaries and records each chunk
when its last worker returns; autocal_run caches each stage. Both are only
worth anything if a rerun trusts exactly the finished work and nothing else.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'cv' / 'balltrack'))
import drun  # noqa: E402

from cv.experiments import autocal_run as R  # noqa: E402


# ---- drun chunks ---------------------------------------------------------------------


def test_chunks_cover_the_window_once_on_absolute_boundaries():
    for lo, hi in ((7200, 79200), (150828, 152628), (8999, 9000), (9000, 9000)):
        ch = drun.chunk_edges(lo, hi)
        assert ch[0][0] == lo and ch[-1][1] == hi
        assert all(b + 1 == c for (_, b), (c, _) in zip(ch, ch[1:]))
        assert all(a // drun.CHUNK == b // drun.CHUNK for a, b in ch)
        assert all(b - a + 1 <= drun.CHUNK for a, b in ch)


def test_the_same_frames_make_the_same_chunk_whatever_window_asks():
    a = drun.chunk_edges(7200, 79200)
    b = drun.chunk_edges(9000, 40000)
    assert set(a) >= set(b[:-1])     # the inner chunks are shared exactly


def _manifest(tmp, ctag, res, parts=True):
    if parts:
        for r in res:
            for p in r['parts']:
                (tmp / p).write_bytes(b'x')
    (tmp / ('%s.json' % ctag)).write_text(json.dumps(res))


def test_a_chunk_counts_as_done_only_when_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(drun, 'S2', str(tmp_path))
    c = 18000 // drun.CHUNK
    ctag = 't_k%03d' % c
    res = [dict(wid=0, tag=ctag, lo=18000, hi=22499, parts=['a.npz']),
           dict(wid=1, tag=ctag, lo=22500, hi=26999, parts=['b.npz'])]
    assert drun.load_chunk('t', c, 18000, 26999) is None          # nothing yet

    _manifest(tmp_path, ctag, res)
    assert drun.load_chunk('t', c, 18000, 26999) == res

    os.remove(tmp_path / 'b.npz')                                  # a part went missing
    assert drun.load_chunk('t', c, 18000, 26999) is None

    _manifest(tmp_path, ctag, res)
    assert drun.load_chunk('t', c, 20000, 26999) is None           # another window's piece of the slot


def test_save_chunk_leaves_no_half_written_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(drun, 'S2', str(tmp_path))
    drun.save_chunk('t_k002', [dict(wid=0, lo=1, hi=2, parts=[])])
    assert sorted(os.listdir(tmp_path)) == ['t_k002.json']


# ---- autocal_run stages ------------------------------------------------------------------


@pytest.fixture
def stubbed(monkeypatch):
    """Every stage replaced by a recorder, so the run's bookkeeping is all that is tested."""
    calls = []
    monkeypatch.setenv('PIQ_VIDEO', 'v.mp4')
    monkeypatch.delenv('PIQ_LINES', raising=False)
    monkeypatch.setattr(R, 'gather', lambda lo, hi, log: calls.append('gather') or dict(frames=[lo, hi]))
    monkeypatch.setattr(R, 'prep', lambda D, log: calls.append('prep') or (dict(frames=D['frames']),
                                                                           np.zeros((2, 2, 3), np.uint8)))
    monkeypatch.setattr(R, 'calibrate', lambda Q, cam, log: calls.append(('cal', cam)) or (np.ones(3), np.eye(3), 12.3, 7.5))
    monkeypatch.setattr(R, 'height', lambda tag, Q, cal, log: calls.append('height') or cal)
    monkeypatch.setattr(R, 'write', lambda tag, Q, pose, homog, log: calls.append('write') or {})
    return calls


def test_a_rerun_skips_finished_stages(tmp_path, stubbed):
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path)
    assert stubbed == ['gather', 'prep', ('cal', None), 'height', 'write']
    stubbed.clear()
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path)
    assert stubbed == ['write']


def test_redo_reruns_that_stage_and_everything_after(tmp_path, stubbed):
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path)
    stubbed.clear()
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path, redo='cal')
    assert stubbed == [('cal', None), 'height', 'write']


def test_a_missing_stage_file_reruns_the_later_stages_too(tmp_path, stubbed):
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path)
    os.remove(tmp_path / 't' / 'prep.pkl')
    stubbed.clear()
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path)
    assert stubbed == ['prep', ('cal', None), 'height', 'write']


def test_a_different_stretch_or_paint_is_refused_not_mixed(tmp_path, stubbed):
    R.run('t', 0, 100, work=tmp_path, homog=tmp_path)
    with pytest.raises(SystemExit):
        R.run('t', 0, 200, work=tmp_path, homog=tmp_path)
    with pytest.raises(SystemExit):
        R.run('t', 0, 100, work=tmp_path, homog=tmp_path, lines='white')
    R.run('t', 0, 200, work=tmp_path, homog=tmp_path, redo='gather')    # unless asked to start over


def test_a_borrowed_camera_solves_rotation_only(tmp_path, stubbed):
    R.run('home', 0, 100, work=tmp_path, homog=tmp_path)
    stubbed.clear()
    R.run('later', 500, 600, work=tmp_path, homog=tmp_path, camera='home')
    assert stubbed[:2] == ['gather', 'prep']
    assert stubbed[2][0] == 'cal' and stubbed[2][1] is not None
    assert 'height' not in stubbed


def test_no_video_no_run(monkeypatch):
    monkeypatch.delenv('PIQ_VIDEO', raising=False)
    with pytest.raises(SystemExit):
        R._video()


# ---- player lab extract / features ------------------------------------------------------


def test_player_lab_pieces_match_drun_chunks():
    from cv.experiments import player_lab as L
    for lo, hi in ((7200, 79200), (40500, 42299), (8999, 9000)):
        assert L._chunks(lo, hi) == drun.chunk_edges(lo, hi)


def test_player_lab_saves_leave_no_half_written_file(tmp_path):
    from cv.experiments import player_lab as L
    L._save_atomic(tmp_path / 'a.npz', x=np.arange(3))
    assert sorted(os.listdir(tmp_path)) == ['a.npz']
