"""A missing optional dependency says which extra installs it, and the command
line prints that instead of a traceback (pitchiq/extras.py)."""

from __future__ import annotations

import sys

import pytest

import pitchiq.__main__ as cli_main
from pitchiq import extras
from pitchiq.detector import PersonBallDetector
from pitchiq.extras import MissingExtra


def test_the_detector_names_the_detect_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, 'ultralytics', None)  # import of it fails
    with pytest.raises(MissingExtra) as e:
        PersonBallDetector()
    msg = str(e.value)
    assert '`ultralytics`' in msg
    assert 'pip install -e .[detect]' in msg
    assert 'CUDA' in msg


def test_require_reports_the_module_that_is_actually_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, 'no_such_dependency_of_ours', None)
    with pytest.raises(MissingExtra, match='no_such_dependency_of_ours'):
        extras.require('no_such_dependency_of_ours', 'test', 'This test')


def test_a_missing_extra_is_an_import_error():
    assert issubclass(MissingExtra, ImportError)


def test_the_command_line_prints_the_message_not_a_traceback(monkeypatch, capsys):
    class Command:
        @staticmethod
        def main(args):
            raise MissingExtra('needs the [detect] extra')

    monkeypatch.setattr(cli_main.importlib, 'import_module', lambda name: Command)
    assert cli_main.main(['match_report', '--video', 'clip.mp4']) == 1
    err = capsys.readouterr().err
    assert err.strip() == 'needs the [detect] extra'
    assert 'Traceback' not in err


def test_tapnext_missing_names_the_clone_and_the_checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(extras, 'TAPNEXT', tmp_path)
    missing = extras.tapnext_missing()
    assert len(missing) == 2
    assert 'tapnet clone' in missing[0] and 'checkpoint' in missing[1]
    (tmp_path / 'tapnet' / 'tapnet').mkdir(parents=True)
    (tmp_path / 'tapnextpp_512.ckpt').write_bytes(b'')
    assert extras.tapnext_missing() == []


def test_the_check_command_exits_1_with_the_reason(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, 'ultralytics', None)
    assert extras.main(['detect']) == 1
    assert 'pip install -e .[detect]' in capsys.readouterr().err
    assert extras.main(['nonsense']) == 2
