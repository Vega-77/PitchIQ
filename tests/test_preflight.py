"""`pitchiq/cli/preflight.py`: the checks process_match makes in its first seconds."""

from __future__ import annotations

import pytest

from pitchiq.cli import preflight as pf

MIN = 60.0


def test_woodbridge_halves_pass():
    # halves.json's wb: 12:50-55:15 and 66:00-111:00, in a 126-minute video
    assert pf.halves_problems([(770.0, 3315.0), (3960.0, 6660.0)], 126 * MIN) == []


def test_a_75_minute_half_is_named():
    # nb's H2 guess (55:00-130:00) took in stoppages or overtime
    found = pf.halves_problems([(125.0, 2625.0), (3300.0, 7800.0)])
    assert len(found) == 1 and found[0].startswith('half 2 is 75 min long')


def test_a_missing_break_and_a_half_past_the_video_end():
    found = pf.halves_problems([(0.0, 42 * MIN), (43 * MIN, 85 * MIN)], 80 * MIN)
    assert [f.split(' ')[1] for f in found] == ['break', '2']


def test_one_half_is_one_problem():
    assert pf.halves_problems([(0.0, 42 * MIN)]) == ['1 half found; a game has two']


def test_pipeline_processes_are_told_from_others():
    assert pf._is_run(['C:/x/python.exe', '-m', 'pitchiq.balltrack.half', 'wb1', '1', '2'])
    assert pf._is_run(['python.exe', '-W', 'ignore', '-m', 'pitchiq', 'process_match', 'v.mp4'])
    assert not pf._is_run(['python.exe', '-m', 'pytest', 'tests'])
    assert not pf._is_run(['notepad.exe', 'process_match.py'])  # an editor on the file


def test_the_disk_check_names_the_drive_and_the_need(tmp_path):
    assert pf.disk_problems(0, tmp_path) == []
    (msg,) = pf.disk_problems(10**9, tmp_path)
    assert 'GB free and this run needs about 1000000000 GB' in msg


@pytest.mark.skipif(not pf.FIT.is_dir(), reason='not the pipeline PC (no PitchIQ-data)')
def test_every_fixed_input_is_on_this_pc():
    assert [p for p in pf.missing_inputs() if 'missing' in p] == []
