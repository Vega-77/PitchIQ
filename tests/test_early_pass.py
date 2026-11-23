"""process_match starts the passes while the coach pins (session step 3.12): the first half's
pass after the panoramas, its shipped chain held for the camera (half.py --after); the second
half's pass once the first has done everything else; each followed by step_pass."""

import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from pitchiq.cli import process_match as pm

FRAMES = [(0, 99), (100, 199)]


class Proc:
    """A pass's process as start_second, go and stop_early see it: running while rc is None."""

    def __init__(self, rc=None, pid=0):
        self.rc, self.pid = rc, pid

    def poll(self):
        return self.rc


def started(name, rc=None, pid=0):
    return SimpleNamespace(name=name, proc=Proc(rc, pid))


@pytest.fixture
def done(monkeypatch):
    """The halves whose pass has finished (process_match.passed)."""
    halves = set()
    monkeypatch.setattr(pm, 'passed', lambda t: t in halves)
    return halves


@pytest.fixture
def run(tmp_path, monkeypatch, done):
    monkeypatch.setattr(pm, 'DATA', tmp_path)
    monkeypatch.setattr(pm, 'TRACK', tmp_path / 'track')
    monkeypatch.setattr(pm, 'load_halves', lambda: {'zz': None})
    monkeypatch.setattr(pm, 'half_frames', lambda g: FRAMES)
    a = SimpleNamespace(
        game='zz', plan=False, video=tmp_path / 'v.mp4', lines='white', no_early=False
    )
    r = pm.Run(a)
    r.logs.mkdir(parents=True)
    # a module for the real processes below: sleeps, or starts a child that sleeps
    (tmp_path / 'zzsleep.py').write_text(
        'import subprocess, sys, time\n'
        'from pathlib import Path\n'
        "if sys.argv[1] == 'child':\n"
        "    c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        '    Path(sys.argv[2]).write_text(str(c.pid))\n'
        '    c.wait()\n'
        'else:\n'
        '    time.sleep(float(sys.argv[1]))\n'
        '    sys.exit(int(sys.argv[2]))\n'
    )
    r.env['PYTHONPATH'] = str(tmp_path)
    return r


def fake_start(r):
    starts = []

    def start(name, args, low=False, why=''):
        starts.append((name, tuple(args), low))
        return started(name)

    r.start = start
    return starts


def kill(s):
    subprocess.run(['taskkill', '/T', '/F', '/PID', str(s.proc.pid)], capture_output=True)
    s.proc.wait(10)


def run_log(r):
    return (r.out / 'run.log').read_text(encoding='utf-8')


# ---------------------------------------------------------------- start, follow, priority


def test_start_returns_at_once_and_follow_waits_for_the_exit_code(run):
    s = run.start('sleep_zz', ('zzsleep', '2', '3'))
    assert s.proc.poll() is None  # still asleep: start did not wait
    assert run.follow(s) == 3
    assert 'end sleep_zz  rc 3' in run_log(run)


def test_a_low_step_and_its_children_run_below_normal_until_lifted(run, tmp_path):
    psutil = pytest.importorskip('psutil')
    if not hasattr(psutil, 'BELOW_NORMAL_PRIORITY_CLASS'):
        pytest.skip('Windows priority classes')
    flag = tmp_path / 'child.pid'
    s = run.start('sleep_zz', ('zzsleep', 'child', flag), low=True)
    try:
        deadline = time.time() + 30
        while not (flag.exists() and flag.read_text()) and time.time() < deadline:
            time.sleep(0.05)
        parent, child = psutil.Process(s.proc.pid), psutil.Process(int(flag.read_text()))
        assert parent.nice() == child.nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        pm.normal_priority(s.proc.pid)
        assert parent.nice() == child.nice() == psutil.NORMAL_PRIORITY_CLASS
    finally:
        kill(s)


def test_stop_early_kills_a_running_pass_and_starts_no_more(run):
    s = run.start('pass_zz1', ('zzsleep', '60', '0'))
    run.early['zz1'] = s
    pm.stop_early(run)
    assert s.proc.wait(10) != 0  # killed, not asleep for a minute
    assert run.early == {} and run.stopping
    assert 'stopping pass_zz1' in run_log(run)
    pm.early_pass(run, 'zz2', FRAMES[1])  # the early thread, too late
    assert run.early == {}


# ---------------------------------------------------------------- starting early


def test_start_early_clears_old_go_files_and_starts_the_first_half_low(run, monkeypatch):
    starts = fake_start(run)
    monkeypatch.setattr(pm, 'start_second', lambda r, frames: None)
    for t in run.tags:  # an older run's
        pm.ship_file(run, t).touch()
        (pm.TRACK / 'half' / t).mkdir(parents=True)
        (pm.TRACK / 'half' / t / 'early.done').touch()
    pm.start_early(run, FRAMES)
    run.early_watch.join(5)
    ship = run.out / 'ship_zz1'
    assert starts == [('pass_zz1', ('pitchiq.balltrack.half', 'zz1', 0, 99, '--after', ship), True)]
    assert not list(run.out.glob('ship_*'))
    assert not list(pm.TRACK.glob('half/*/early.done'))


@pytest.mark.parametrize('why', ['--no-early', 'first half passed'])
def test_no_early_start_when_asked_or_already_passed(run, done, why):
    starts = fake_start(run)
    if why == '--no-early':
        run.a.no_early = True
    else:
        done.add('zz1')
    pm.start_early(run, FRAMES)
    assert starts == [] and run.early_watch is None


def test_the_second_pass_starts_once_the_first_has_done_all_but_ship(run, monkeypatch):
    monkeypatch.setattr(pm, 'EARLY_POLL_S', 0.01)
    starts = fake_start(run)
    run.early['zz1'] = started('pass_zz1')
    th = threading.Thread(target=pm.start_second, args=(run, FRAMES[1]), daemon=True)
    th.start()
    time.sleep(0.2)
    assert starts == [] and th.is_alive()  # the first is still detecting
    mark = pm.TRACK / 'half' / 'zz1' / 'early.done'
    mark.parent.mkdir(parents=True)
    mark.touch()
    th.join(5)
    assert not th.is_alive()
    ship = run.out / 'ship_zz2'
    want = ('pitchiq.balltrack.half', 'zz2', 100, 199, '--after', ship)
    assert starts == [('pass_zz2', want, True)] and 'zz2' in run.early


@pytest.mark.parametrize('why', ['first ended', 'second passed'])
def test_no_second_pass_when_the_first_ended_or_the_second_is_done(run, done, monkeypatch, why):
    monkeypatch.setattr(pm, 'EARLY_POLL_S', 0.01)
    starts = fake_start(run)
    run.early['zz1'] = started('pass_zz1', rc=0 if why == 'first ended' else None)
    if why == 'second passed':
        done.add('zz2')
        mark = pm.TRACK / 'half' / 'zz1' / 'early.done'
        mark.parent.mkdir(parents=True)
        mark.touch()
    pm.start_second(run, FRAMES[1])
    assert starts == []


def test_a_pass_started_after_its_go_file_runs_at_normal_priority(run):
    starts = fake_start(run)
    pm.ship_file(run, 'zz2').touch()
    pm.early_pass(run, 'zz2', FRAMES[1])
    assert starts[0][2] is False


# ---------------------------------------------------------------- go files


def test_the_camera_step_lets_the_first_half_ship_once_the_camera_is_accepted(run, monkeypatch):
    monkeypatch.setattr(pm, 'registered', lambda t: True)
    monkeypatch.setattr(pm, 'health', lambda r, t: None)
    monkeypatch.setattr(pm, 'venue', lambda r: None)
    pm.step_camera(run)
    assert pm.ship_file(run, 'zz1').exists() and not pm.ship_file(run, 'zz2').exists()


def test_go_lifts_a_running_early_pass_only(run, monkeypatch):
    lifted = []
    monkeypatch.setattr(pm, 'normal_priority', lifted.append)
    run.early['zz1'] = started('pass_zz1', pid=11)
    run.early['zz2'] = started('pass_zz2', rc=1, pid=22)
    pm.go(run, 'zz1')
    pm.go(run, 'zz2')
    assert lifted == [11]
    assert pm.ship_file(run, 'zz1').exists() and pm.ship_file(run, 'zz2').exists()


def test_go_writes_nothing_in_plan(run):
    run.a.plan = True
    pm.go(run, 'zz1')
    assert not pm.ship_file(run, 'zz1').exists()


# ---------------------------------------------------------------- step_pass


def test_step_pass_follows_both_early_passes_and_ships_the_second_after_the_first(
    run, done, monkeypatch
):
    lifted = []
    monkeypatch.setattr(pm, 'normal_priority', lifted.append)
    run.early = {'zz1': started('pass_zz1', pid=11), 'zz2': started('pass_zz2', pid=22)}
    seen = []

    def follow(s):
        seen.append((s.name, pm.ship_file(run, 'zz2').exists(), list(lifted)))
        done.add(s.name[-3:])
        return 0

    run.follow = follow
    run.launch = lambda name, args: pytest.fail(f'{name} started again')
    pm.step_pass(run)
    # the second's shipped chain is held until the first's pass is over, then lifted
    assert seen == [('pass_zz1', False, []), ('pass_zz2', True, [22])]
    assert run.early == {}


def test_a_half_not_started_early_is_passed_as_before(run, done):
    run.early['zz1'] = started('pass_zz1')
    tries = []

    def follow(s):
        done.add('zz1')
        return 0

    def launch(name, args):
        tries.append((name, tuple(args)))
        done.add('zz2')
        return 0

    run.follow, run.launch = follow, launch
    pm.step_pass(run)
    assert tries == [('pass_zz2', ('pitchiq.balltrack.half', 'zz2', 100, 199))]
    assert pm.ship_file(run, 'zz2').exists()  # harmless: that pass has no --after


@pytest.mark.parametrize('rc', [1, 0])  # 0: it said nothing wrong but has no HALF DONE
def test_an_early_pass_that_fails_is_tried_once_more_without_after(run, done, rc):
    run.early['zz1'] = started('pass_zz1')
    done.add('zz2')
    (run.logs / 'pass_zz1.log').write_text('Traceback: boom\n', encoding='utf-8')
    tries = []

    def launch(name, args):
        tries.append((name, tuple(args)))
        done.add('zz1')
        return 0

    run.follow, run.launch = (lambda s: rc), launch
    pm.step_pass(run)
    assert tries == [('pass_zz1', ('pitchiq.balltrack.half', 'zz1', 0, 99))]
    assert 'pass_zz1 failed (rc 1)' in run_log(run)


def test_a_run_that_stops_stops_its_early_passes(run, monkeypatch):
    stopped = []
    monkeypatch.setattr(pm, 'stop_early', stopped.append)

    def check_video(path):
        raise pm.Stop('no video')

    monkeypatch.setattr(pm, 'check_video', check_video)
    run.tell = lambda *a, **k: None
    assert pm.run_steps(run) == 1
    assert stopped == [run]
