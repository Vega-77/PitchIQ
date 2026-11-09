"""`MatchContext.ours_kit`: the coach's shirts come out as TEAM_A whichever way clustering numbered them."""

from __future__ import annotations

import numpy as np
import pytest

from pitchiq.frame_table import FrameTable
from pitchiq.pipeline import MatchReport, _split_teams
from pitchiq.teams import TEAM_A, TEAM_B, assign_teams

# Lab, OpenCV's 0-255 scale: a white shirt and a red one.
WHITE = (215.0, 128.0, 130.0)
RED = (110.0, 185.0, 160.0)


def samples(order):
    rng = np.random.default_rng(0)
    return {
        track: [np.array(kit) + rng.normal(0, 3, 3) for _ in range(8)]
        for track, kit in enumerate(order)
    }


def split(order, ours):
    table = FrameTable(fps=30.0, frame_width=1920, frame_height=1080)
    report = MatchReport(source='test', duration_s=0.0, processing_s=0.0)
    _split_teams(report, table, samples(order), ours)
    return table.team_by_track


@pytest.mark.parametrize('order', [[WHITE] * 4 + [RED] * 4, [RED] * 4 + [WHITE] * 4])
def test_lighter_is_team_a(order):
    teams = split(order, 'lighter')
    assert {teams[i] for i, kit in enumerate(order) if kit == WHITE} == {TEAM_A}
    assert {teams[i] for i, kit in enumerate(order) if kit == RED} == {TEAM_B}


@pytest.mark.parametrize('order', [[WHITE] * 4 + [RED] * 4, [RED] * 4 + [WHITE] * 4])
def test_darker_is_team_a(order):
    teams = split(order, 'darker')
    assert {teams[i] for i, kit in enumerate(order) if kit == RED} == {TEAM_A}


def test_without_it_nothing_is_relabelled():
    order = [RED] * 4 + [WHITE] * 4
    assert split(order, None) == assign_teams(samples(order)).by_track


def test_a_typo_is_refused():
    with pytest.raises(ValueError):
        split([WHITE] * 4 + [RED] * 4, 'white')
