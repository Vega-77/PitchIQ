"""`pitchiq/cli/resume.py`: a run a reboot cut off is started again at logon."""

from __future__ import annotations

import json

import pytest

from pitchiq.cli import process_match as pm
from pitchiq.cli import resume


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setattr(resume, 'DATA', tmp_path)
    monkeypatch.setattr(pm, 'DATA', tmp_path)
    return tmp_path


def cut_off(data, game, created, resumes=0):
    run = data / 'stats' / game / 'run'
    run.mkdir(parents=True)
    info = dict(argv=['v.mp4', '--game', game], cwd=str(data), env={}, pid=2**22 + 1,
                created=created, started='then', resumes=resumes)  # fmt: skip
    (run / resume.MARK).write_text(json.dumps(info))
    return run


def test_the_mark_keeps_the_command_and_only_the_pipeline_settings(data, monkeypatch):
    monkeypatch.setenv('PIQ_MAP_PROCS', '6')
    monkeypatch.setenv('PITCHIQ_SA_KEY', 'C:/elsewhere/key.json')
    monkeypatch.setenv('SOME_TOKEN', 'not kept')
    out = data / 'stats' / 'me' / 'run'
    resume.mark(out, ['v.mp4', '--game', 'me'])
    info = json.loads((out / resume.MARK).read_text())
    assert info['argv'] == ['v.mp4', '--game', 'me'] and info['resumes'] == 0
    assert info['env']['PIQ_MAP_PROCS'] == '6' and 'SOME_TOKEN' not in info['env']
    assert resume.alive(info)  # this test's own process
    resume.clear(out)
    assert not (out / resume.MARK).exists()


def test_the_newest_cut_off_run_is_started_again(data, monkeypatch):
    started = []
    monkeypatch.setattr(resume, 'launch', lambda path, info: started.append(info['argv'][2]) or 7)
    cut_off(data, 'old', created=1.0)
    new = cut_off(data, 'new', created=2.0)
    assert resume.resume() == 0
    assert started == ['new']
    assert 'started it again as PID 7' in (new / 'run.log').read_text()


def test_a_run_cut_off_too_often_is_left_for_a_person(data, monkeypatch):
    monkeypatch.setattr(resume, 'launch', lambda path, info: pytest.fail('relaunched'))
    run = cut_off(data, 'me', created=1.0, resumes=resume.MAX_RESUMES)
    resume.resume()
    assert not (run / resume.MARK).exists()
    assert 'NOT RESUMED' in (run / 'run.log').read_text()


def test_a_run_that_stops_on_its_own_clears_its_mark(data, tmp_path, monkeypatch):
    def refuse(path):
        assert (data / 'stats' / 'me' / 'run' / resume.MARK).exists()  # marked while it runs
        raise pm.Stop('no video')

    monkeypatch.setattr(pm, 'check_video', refuse)
    rc = pm.main([str(tmp_path / 'v.mp4'), '--game', 'me', '--ours', 'darker', '--no-publish'])
    assert rc == 1
    assert not (data / 'stats' / 'me' / 'run' / resume.MARK).exists()


@pytest.fixture
def checks(data, tmp_path, monkeypatch):
    """check_inputs on a new game whose preflight answers come from `answers` in turn."""
    from types import SimpleNamespace

    from pitchiq.cli import preflight

    answers = []
    monkeypatch.setattr(preflight, 'problems', lambda need: answers.pop(0) if answers else [])
    naps = []
    monkeypatch.setattr(pm.time, 'sleep', naps.append)
    a = SimpleNamespace(game='zz', plan=False, video=tmp_path / 'v.mp4', lines='white',
                        pins_from=None, trust_halves=False)  # fmt: skip
    return SimpleNamespace(run=pm.Run(a), answers=answers, naps=naps)


def test_a_resumed_run_waits_for_the_pc_to_settle_before_its_checks(checks, monkeypatch):
    monkeypatch.setenv('PIQ_RESUMED', '1')
    checks.answers += [['only 9 GB of memory is free'], ['only 11 GB of memory is free'], []]
    pm.check_inputs(checks.run)  # no Stop
    assert checks.naps == [pm.RESUME_POLL_S] * 2
    assert 'waiting up to 30 min' in (checks.run.out / 'run.log').read_text()


def test_a_resumed_run_stops_when_the_pc_never_settles(checks, monkeypatch):
    monkeypatch.setenv('PIQ_RESUMED', '1')
    monkeypatch.setattr(pm, 'RESUME_WAIT_S', 0)
    checks.answers += [['only 9 GB of memory is free']] * 2
    with pytest.raises(pm.Stop, match='only 9 GB'):
        pm.check_inputs(checks.run)


def test_a_run_started_by_hand_stops_at_once(checks, monkeypatch):
    monkeypatch.delenv('PIQ_RESUMED', raising=False)
    checks.answers += [['only 9 GB of memory is free']]
    with pytest.raises(pm.Stop, match='only 9 GB'):
        pm.check_inputs(checks.run)
    assert checks.naps == []
