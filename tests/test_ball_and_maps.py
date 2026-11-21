"""process_match runs both maps beside the ball step (step_ball_and_maps), with fake steps."""

import os
import threading
import time
from types import SimpleNamespace

import pytest

from pitchiq.balltrack import ball17
from pitchiq.cli import preflight
from pitchiq.cli import process_match as pm


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, 'DATA', tmp_path)
    monkeypatch.setattr(pm, 'MAPS', tmp_path / 'map')
    monkeypatch.setattr(pm, 'DETS', tmp_path / 'dets')
    (tmp_path / 'map').mkdir()
    (tmp_path / 'dets').mkdir()
    for t in ('zz1', 'zz2'):
        (tmp_path / 'dets' / f'{t}_feat.npz').write_bytes(b'')
    a = SimpleNamespace(game='zz', plan=False, final=False, video=tmp_path / 'v.mp4', lines='white')
    r = pm.Run(a)
    r.logs.mkdir(parents=True)
    r.told = []
    r.tell = lambda what, text, priority='default': r.told.append(what)
    r.reserves = []

    def procs_for(want, each_gb, reserve_gb=3.0):
        r.reserves.append(reserve_gb)
        return 2

    monkeypatch.setattr(preflight, 'procs_for', procs_for)
    r.balled = set()
    monkeypatch.setattr(ball17, 'version', lambda t: ball17.VER if t in r.balled else 'hyb90')
    return r


def fake_steps(r, ball_rc=0, map_rc=0, ball_waits_for_map=True):
    """Each fake step records (name, beside?) and makes its output; the ball waits until a map
    has started, which only happens if the two run at once."""
    started = threading.Event()
    seen = []

    def launch(name, args):
        seen.append((name, getattr(r.beside, 'on', False)))
        with open(r.logs / f'{name}.log', 'a', encoding='utf-8') as f:
            f.write('a fake step\n')
        if name.startswith('map_'):
            started.set()
            time.sleep(0.05)
            if not map_rc:
                (pm.MAPS / f'{name[4:]}.npz').write_bytes(b'')
            return map_rc
        if name.startswith('ball17_'):
            if ball_waits_for_map:
                assert started.wait(5), 'no map started while the ball ran'
            if not ball_rc:
                r.balled.update(r.tags)
            return ball_rc
        return 0

    r.launch = launch
    return seen


def test_the_maps_run_beside_the_ball_and_leave_it_the_bar(run):
    seen = fake_steps(run)
    pm.step_ball_and_maps(run)
    assert set(seen) == {('ball17_zz', False), ('map_zz1', True), ('map_zz2', True)}
    assert [n for n, _ in seen if n.startswith('map_')] == ['map_zz1', 'map_zz2']
    assert run.reserves == [3.0 + pm.BALL_GB] * 2  # the maps keep memory free for the ball
    assert all((pm.MAPS / f'{t}.npz').exists() for t in run.tags)


def test_with_the_ball_done_the_maps_run_alone_with_all_the_memory(run):
    run.balled.update(run.tags)
    seen = fake_steps(run, ball_waits_for_map=False)
    pm.step_ball_and_maps(run)
    assert seen == [('map_zz1', False), ('map_zz2', False)]  # they keep the bar themselves
    assert run.reserves == [3.0, 3.0]


def test_a_failed_map_is_told_at_once_and_stops_the_run_after_the_ball(run):
    seen = fake_steps(run, map_rc=1)
    with pytest.raises(pm.Stop, match='map_zz1 failed twice'):
        pm.step_ball_and_maps(run)
    assert ('ball17_zz', False) in seen and run.balled == set(run.tags)  # the ball finished
    assert run.told == ['map stopped']


def test_a_failed_ball_waits_for_the_maps_before_it_stops_the_run(run):
    fake_steps(run, ball_rc=1)
    with pytest.raises(pm.Stop, match='ball17_zz failed twice'):
        pm.step_ball_and_maps(run)
    assert all((pm.MAPS / f'{t}.npz').exists() for t in run.tags)  # no map left running


def test_missing_features_are_made_first_alone(run):
    (pm.DETS / 'zz2_feat.npz').unlink()
    seen = fake_steps(run)
    pm.step_ball_and_maps(run)
    assert seen[0] == ('features_zz2', False)


def test_a_held_out_half_without_final_stops_before_anything_runs(run, monkeypatch):
    monkeypatch.setattr(pm, 'HELD_OUT', {'zz2'})
    seen = fake_steps(run)
    with pytest.raises(pm.Stop, match='held out'):
        pm.step_ball_and_maps(run)
    assert seen == []


def test_the_plan_says_what_runs_beside_what(run, capsys):
    run.a.plan = True
    (pm.MAPS / 'zz1.npz').write_bytes(b'')
    pm.step_ball_and_maps(run)
    out = capsys.readouterr().out
    assert 'ball zz: to do' in out and 'map zz1: done' in out
    assert 'map zz2: to do (15-30 min, beside the ball)' in out


def test_memory_of_a_process_tree():
    use, commit = pm.memory_gb(os.getpid())
    assert use > 0.01 and commit >= 0
    assert pm.memory_gb(2**31 - 1) == (0.0, 0.0)  # gone (or never there)
