"""The halves fit: warm-ups and overtime fall outside, and a wrong-looking fit says so."""

import json
from pathlib import Path

import numpy as np
import pytest

from pitchiq.experiments import halves as h

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / 'pitchiq' / 'balltrack' / 'plab' / 'out'


def signals(spans, minutes, step=5.0, seed=0):
    """Finder samples: busy, spread out and panning inside `spans` (minutes), quiet outside."""
    rng = np.random.default_rng(seed)
    t = np.arange(0, minutes * 60, step)
    on = np.zeros(len(t), bool)
    for a, b in spans:
        on |= (t >= a * 60) & (t < b * 60)
    count = np.where(on, rng.integers(16, 30, len(t)), rng.integers(0, 8, len(t)))
    spread = np.where(on, rng.uniform(0.5, 0.8, len(t)), rng.uniform(0.0, 0.3, len(t)))
    pan = np.where(on & (rng.random(len(t)) < 0.8), rng.uniform(30, 400, len(t)), 0.0)
    return dict(t=t, count=count, spread=spread, pan=pan)


def test_warm_up_and_overtime_fall_outside_the_halves():
    # a warm-up, a 4 min lineup, two halves and a break, then two overtime periods
    z = signals([(0, 30), (34, 78), (88, 134), (136, 146), (147, 157)], 160)
    halves, info = h.fit(z)
    want = [(34, 78), (88, 134)]
    for (s, e), (ws, we) in zip(halves, want):
        assert abs(s / 60 - ws) <= 1 and abs(e / 60 - we) <= 1
    assert h.problems(halves, info) == []


def test_a_video_with_one_long_stretch_has_no_halves():
    z = signals([(5, 120)], 130)
    halves, info = h.fit(z)
    assert halves == []
    assert 'no two halves' in h.problems(halves, info)[0]


KNOWN = ['ww', 'eb', 'co', 'wb', 'pi', 'wh']  # nb's H2 is held out and was never placed


def _sec(text):
    m, s = text.split(':')
    return int(m) * 60 + int(s)


@pytest.mark.skipif(not (OUT / 'wb_halves.npz').exists(), reason='needs the finder samples')
@pytest.mark.parametrize('g', KNOWN)
def test_known_games_are_within_three_minutes(g):
    H = json.loads((REPO / 'pitchiq' / 'balltrack' / 'halves.json').read_text())[g]
    truth = [
        (_sec(H[k][0]), _sec(H[k][1])) for k in ('H1', 'H1_heldout', 'H2', 'H2_heldout') if k in H
    ]
    halves, info = h.fit(np.load(OUT / f'{g}_halves.npz'))
    assert h.problems(halves, info) == []
    for (s, e), (ts, te) in zip(halves, truth):
        assert abs(s - ts) <= 180 and abs(e - te) <= 180
