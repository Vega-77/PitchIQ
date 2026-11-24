"""half.kill_tree: a process and everything it started end at once, with or without taskkill."""

import subprocess
import sys
import time

import pytest

from pitchiq.balltrack import half

psutil = pytest.importorskip('psutil')


def _parent_of_a_sleeper(tmp_path):
    """A process that starts a sleeping child, and the child's pid once it has started."""
    flag = tmp_path / 'child.pid'
    code = (
        'import subprocess, sys; from pathlib import Path\n'
        'c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])\n'
        'Path(sys.argv[1]).write_text(str(c.pid)); c.wait()\n'
    )
    p = subprocess.Popen([sys.executable, '-c', code, str(flag)])
    deadline = time.time() + 30
    while not (flag.exists() and flag.read_text()) and time.time() < deadline:
        time.sleep(0.05)
    return p, int(flag.read_text())


@pytest.mark.parametrize('platform', ['here', 'linux'])
def test_the_whole_tree_ends(tmp_path, monkeypatch, platform):
    if platform != 'here':  # the psutil way, wherever the test runs
        monkeypatch.setattr(half.sys, 'platform', platform)
    p, child = _parent_of_a_sleeper(tmp_path)
    try:
        half.kill_tree(p.pid)
        assert p.wait(10) != 0  # killed, not asleep for a minute
        assert psutil.wait_procs([psutil.Process(child)], timeout=10)[1] == []
    except psutil.NoSuchProcess:
        pass  # the child had already gone
    finally:
        if p.poll() is None:
            p.kill()


def test_a_gone_process_is_nothing_to_do(monkeypatch):
    monkeypatch.setattr(half.sys, 'platform', 'linux')
    p = subprocess.Popen([sys.executable, '-c', 'pass'])
    p.wait()
    half.kill_tree(p.pid)  # no error
