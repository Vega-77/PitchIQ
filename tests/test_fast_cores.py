"""pitchiq.fast_cores: a run's processes leave Windows' efficiency mode; nothing elsewhere."""

import subprocess
import sys
import time

import pytest

from pitchiq import fast_cores


def _throttled(pid: int) -> tuple[int, int]:
    """(ControlMask, StateMask) of a process's power throttling, read back from Windows."""
    import ctypes
    from ctypes import wintypes

    class State(ctypes.Structure):
        _fields_ = [
            ('Version', wintypes.ULONG),
            ('ControlMask', wintypes.ULONG),
            ('StateMask', wintypes.ULONG),
        ]

    k = ctypes.WinDLL('kernel32', use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.GetProcessInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    s = State(Version=1)
    assert k.GetProcessInformation(h, 4, ctypes.byref(s), ctypes.sizeof(s))
    k.CloseHandle(h)
    return s.ControlMask, s.StateMask


def _parent_of_a_sleeper() -> subprocess.Popen:
    """A process that starts a child and waits for it: a step and its worker. The base
    interpreter, since a venv's python.exe on Windows is a launcher that starts it as one
    more process (the tree would grow while the test looks at it)."""
    exe = getattr(sys, '_base_executable', sys.executable)
    code = 'import subprocess, sys; subprocess.run([sys.executable, "-c", "import time; time.sleep(30)"])'
    return subprocess.Popen([exe, '-c', code])


def _settled(root) -> list:
    """root's descendants once two looks 0.2 s apart agree (the child has started)."""
    last = None
    for _ in range(100):
        kids = sorted(k.pid for k in root.children(recursive=True))
        if kids and kids == last:
            return kids
        last = kids
        time.sleep(0.2)
    raise AssertionError('the child never settled: %s' % last)


def test_off_windows_nothing_happens(monkeypatch):
    monkeypatch.setattr(fast_cores.sys, 'platform', 'linux')
    done = set()
    fast_cores.tree(1, done)
    assert done == set()
    assert fast_cores.switch(1) is False


def test_off_by_default_in_a_step(monkeypatch):
    """A step run by hand: nothing switches its processes, so it must not act as if."""
    monkeypatch.delenv('PIQ_FAST_CORES', raising=False)
    import importlib

    assert importlib.reload(fast_cores).ON is False


def test_wanted(monkeypatch):
    monkeypatch.delenv('PIQ_FAST_CORES', raising=False)
    assert fast_cores.wanted('1') is True
    assert fast_cores.wanted('0') is False
    monkeypatch.setenv('PIQ_FAST_CORES', '0')
    assert fast_cores.wanted('1') is False
    monkeypatch.setenv('PIQ_FAST_CORES', '1')
    assert fast_cores.wanted('0') is True


@pytest.mark.parametrize('fast, seen', [(True, '1'), (False, '0')])
def test_a_run_tells_its_steps(tmp_path, monkeypatch, fast, seen):
    """process_match's choice reaches the steps' environment (the ball step's worker count
    reads it), whatever the shell had."""
    from types import SimpleNamespace

    from pitchiq.cli import process_match as pm

    monkeypatch.setattr(pm, 'DATA', tmp_path)
    monkeypatch.setattr(pm, 'FAST', fast)
    monkeypatch.setenv('PIQ_FAST_CORES', 'x')
    a = SimpleNamespace(game='zz', plan=False, video=tmp_path / 'v.mp4', lines='white')
    assert pm.Run(a).env['PIQ_FAST_CORES'] == seen


@pytest.mark.parametrize('env, fast', [(None, 'True'), ('0', 'False'), ('1', 'True')])
def test_a_run_has_it_on_unless_told(env, fast):
    """process_match's default: on (the owner's OK, 23 Nov 2026); PIQ_FAST_CORES=0 keeps it
    off. In a fresh process, since FAST is read once at import."""
    import os

    e = {k: v for k, v in os.environ.items() if k != 'PIQ_FAST_CORES'}
    if env is not None:
        e['PIQ_FAST_CORES'] = env
    code = 'from pitchiq.cli import process_match as pm; print(pm.FAST)'
    out = subprocess.run([sys.executable, '-c', code], env=e, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == fast


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows power throttling')
def test_a_tree_leaves_efficiency_mode_once():
    p = _parent_of_a_sleeper()
    try:
        import psutil

        kids = _settled(psutil.Process(p.pid))
        done = set()
        fast_cores.tree(p.pid, done)
        assert {pid for pid, _ in done} == {p.pid, *kids}
        for q in (p.pid, *kids):
            assert _throttled(q) == (1, 0)  # this program controls execution speed: not throttled
        again = set(done)
        fast_cores.tree(p.pid, again)
        assert again == done  # nothing new, nothing switched twice
    finally:
        for q in psutil.Process(p.pid).children(recursive=True):
            q.kill()
        p.kill()
        p.wait()


def test_a_gone_process_is_skipped():
    p = subprocess.Popen([sys.executable, '-c', 'pass'])
    p.wait()
    done = set()
    fast_cores.tree(p.pid, done)
    assert done == set()
