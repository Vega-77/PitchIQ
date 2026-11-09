"""`python -m pitchiq process_match`: the pieces that run before anything long does."""

from __future__ import annotations

import argparse
import json

import pytest

from pitchiq.cli import process_match as pm
from pitchiq.experiments import cached_report, wholemap

HALVES = (
    '{\r\n'
    '  "_note": "test",\r\n'
    '  "wb": {"video": "Woodbridge - Tactical.mp4",\r\n'
    '         "H1_heldout": ["12:50", "55:15", 23100, 99450],\r\n'
    '         "approx": "kept as it was"}\r\n'
    '}\r\n'
)


@pytest.fixture
def halves(tmp_path, monkeypatch):
    path = tmp_path / 'halves.json'
    path.write_bytes(HALVES.encode())
    for module in (pm, cached_report, wholemap):
        monkeypatch.setattr(module, 'HALVES', path)
    monkeypatch.setattr(pm, 'DOWNLOADS', tmp_path / 'Downloads')
    monkeypatch.setattr(cached_report, 'DOWNLOADS', tmp_path / 'Downloads')
    return path


def test_a_new_game_is_appended_and_the_old_text_kept(halves, tmp_path):
    video = tmp_path / 'elsewhere' / 'Home v Metuchen - Tactical.mp4'
    pm.add_game('me', video, [(778.0, 3315.0), (3965.0, 6660.0)], 'given')
    raw = halves.read_bytes().decode()
    assert raw.startswith(HALVES[: HALVES.index('}\r\n}')])  # the old entries, byte for byte
    assert '\n' not in raw.replace('\r\n', '')  # still CRLF throughout
    entry = json.loads(raw)['me']
    assert entry['video'] == video.name  # the overlay lookup matches on the bare name
    assert entry['H1'] == ['12:58', '55:15', 23340, 99450]
    assert entry['H2'] == ['66:05', '111:00', 118950, 199800]
    assert pm.half_frames('me') == [(23340, 99450), (118950, 199800)]


def test_the_other_steps_find_a_video_outside_downloads(halves, tmp_path):
    video = tmp_path / 'elsewhere' / 'match.mp4'
    pm.add_game('me', video, [(60.0, 120.0), (180.0, 240.0)], 'given')
    assert cached_report._half('me2') == (video.resolve(), 5400, 7200)
    assert wholemap.video_of('me1') == video.resolve().as_posix()


def test_a_video_in_downloads_keeps_its_bare_name(halves, tmp_path):
    video = tmp_path / 'Downloads' / 'match.mp4'
    video.parent.mkdir()
    pm.add_game('me', video, [(60.0, 120.0), (180.0, 240.0)], 'given')
    assert 'path' not in json.loads(halves.read_text())['me']
    assert cached_report._half('me1')[0] == tmp_path / 'Downloads' / 'match.mp4'


def test_clock_and_span():
    assert pm.clock('12:58') == 778
    assert pm.clock('1:05:30') == 3930
    assert pm.span('12:58-55:15') == (778, 3315)
    with pytest.raises(argparse.ArgumentTypeError):
        pm.span('55:15-12:58')


@pytest.mark.parametrize('bad', ['wb1', 'W', 'scrim', 'a', 'x-y'])
def test_a_game_code_with_digits_or_symbols_is_refused(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        pm.game_code(bad)


def test_the_parser_wants_both_halves_or_neither():
    with pytest.raises(SystemExit):
        pm.main(['v.mp4', '--game', 'me', '--ours', 'lighter', '--h1', '1:00-2:00'])
