"""Spot-check lab media helpers (cv/experiments/spotlab.py)."""

import numpy as np
import pytest

from cv.experiments import spotlab as S


def test_crop_rect_is_16_by_9_and_inside_the_frame():
    for cx, cy, ch in [
        (960, 540, 300),
        (5, 5, 300),
        (1915, 1075, 300),
        (960, 540, 50),
        (960, 540, 5000),
    ]:
        x0, y0, cw, h = S._crop_rect(cx, cy, ch)
        assert cw == pytest.approx(h * 16 / 9)
        assert 120 <= h <= 1080
        assert x0 >= 0 and y0 >= 0 and x0 + cw <= 1920 + 1e-6 and y0 + h <= 1080 + 1e-6


def test_near_line():
    assert S._near_line((2, 40)) and S._near_line((60, 78)) and S._near_line((117, 40))
    assert not S._near_line((60, 40))


def test_iou():
    a = (0, 0, 10, 10)
    assert S._iou(a, a) == pytest.approx(1)
    assert S._iou(a, (20, 20, 30, 30)) == 0
    assert S._iou(a, (5, 0, 15, 10)) == pytest.approx(50 / 150)


def test_rgb_swatch():
    assert S._rgb(np.array([255.0, 128, 128])) == '#ffffff'
    assert S._rgb(np.array([0.0, 128, 128])) == '#000000'
    assert S._rgb(np.array([np.nan, 128, 128])) == '#808080'


def test_model_answers_are_page_options():
    """Every model answer must be one the page offers for that question type."""
    page = {'role': {'player', 'referee', 'other'}, 'team': {'A', 'B', 'none'}}
    yn = {'yes', 'no'}

    class Ident:
        role = 'keeper'

    class Res:
        @staticmethod
        def team_at(ident, f):
            return 'team_b'

    w = S._Window.__new__(S._Window)
    w.res = Res()
    assert S._Window.model_answer(w, 'role', 0, Ident()) in page['role']
    assert S._Window.model_answer(w, 'team', 0, Ident()) in page['team'] | {'unsure'}
    for t in ('same', 'lines', 'missing', 'ballring', 'ballmiss'):
        assert S._Window.model_answer(w, t, 0, None) in yn


def test_parser_defaults():
    a = S.build_parser().parse_args(['vid1', 'out'])
    assert a.per_type == 100 and a.windows == 6 and tuple(a.types) == S.TYPES
