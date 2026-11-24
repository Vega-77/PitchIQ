"""half.py's PIQ_CHAIN_LOW: every process of a piece's shipped chain starts below normal priority."""

import subprocess

import pytest

from pitchiq.balltrack import half


class _Proc:
    returncode = 0

    def communicate(self):
        return '', None

    def poll(self):
        return 0


@pytest.mark.parametrize('low', [True, False])
def test_the_shipped_chain_starts_low_only_when_asked(tmp_path, monkeypatch, low):
    seen = []

    def popen(cmd, **kw):
        seen.append((cmd, kw.get('creationflags', 0)))
        return _Proc()

    monkeypatch.setattr(half, 'S2', str(tmp_path))
    monkeypatch.setattr(half, 'HOM', str(tmp_path))
    monkeypatch.setattr(half.subprocess, 'Popen', popen)
    monkeypatch.setattr(half, 'CHAIN_LOW', low)
    h = half.Half('t', 0, 9000 - 1, 6, 300)
    monkeypatch.setattr(h, 'registration', lambda p: None)
    h.shipped({'ptag': 't_q000', 'win': (0, 8999)})
    below = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)  # 0 off Windows
    assert len(seen) == 8  # picks, five 3D steps, the smoother, the merge
    assert all(flags == (below if low else 0) for _, flags in seen)


@pytest.mark.parametrize('env, low', [(None, 'False'), ('0', 'False'), ('1', 'True')])
def test_off_unless_asked(env, low):
    """In a fresh process, since CHAIN_LOW is read once at import."""
    import os
    import sys

    e = {k: v for k, v in os.environ.items() if k != 'PIQ_CHAIN_LOW'}
    if env is not None:
        e['PIQ_CHAIN_LOW'] = env
    code = 'from pitchiq.balltrack import half; print(half.CHAIN_LOW)'
    out = subprocess.run([sys.executable, '-c', code], env=e, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == low
