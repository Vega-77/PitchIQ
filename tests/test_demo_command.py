"""`python -m pitchiq demo` runs the README quickstart end to end and keeps
what --out asks for (pitchiq/cli/demo.py)."""

from __future__ import annotations

import json

import pytest

from pitchiq import demo
from pitchiq.cli import demo as demo_command


def test_the_demo_prints_a_summary_and_keeps_its_files(tmp_path, capsys):
    if not demo.write_clip(tmp_path / 'probe.mp4'):
        pytest.skip('no mp4v encoder available in this OpenCV build')
    out = tmp_path / 'demo'
    assert demo_command.main(['--out', str(out)]) == 0
    printed = capsys.readouterr().out
    assert 'scripted' in printed
    assert 'players tracked' in printed
    for name in ('demo.mp4', 'demo.calib.json', 'report.json'):
        assert (out / name).is_file(), name
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert report
