"""The sample match's xG figures are the model's answers, and stay that way.

`assets/sample-report.js` used to carry hand-picked xG values, chosen so the
shot map would look about right. That is a fixture whose numbers are decoration,
and a decorated fixture teaches whoever reads it to check the layout rather than
the figures — which is the opposite of what it is for.

So each shot in the sample now has a **freeze frame** behind it: where the
keeper was standing, and who was between the ball and the goal. Those frames are
below, and they are the record of how each figure was produced. This test rebuilds
them, runs the real `xg-sandbox/xg_model8.onnx`, and asserts the fixture carries
what came back.

Which makes it two guards at once:

* the sample cannot drift away from the model, and
* **the model cannot change without this failing**. It is a golden file for the
  one number in this project that a person cannot sanity-check by eye. These
  ten frames were worth 4.15 xG under xg_model6, 1.83 under xg_model7 and 1.23
  under xg_model8, and before this file nothing in the repo would have noticed
  a swap of that size.

The frames are invented. That is the honest limit of this file: it pins what the
model does with a set of positions, not what a real match looks like. Nobody has
filmed one yet.

Skipped without onnxruntime, which CI does not install — the same rule the rest
of the model tests follow.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_sample_xg.py -q
"""

from __future__ import annotations

import math
import re
from pathlib import Path

import pytest

from cv.pitch import Pitch
from cv.xg_bridge import ShotContext, load_session, predict_xg

SAMPLE_JS = Path(__file__).resolve().parents[1] / 'assets' / 'sample-report.js'

PITCH = Pitch()
GOAL = (PITCH.length_m, PITCH.width_m / 2)

# Where the shots are published rounded to, in cv/report_json.py::shot_marks.
PLACES = 3


def in_front(shot: tuple[float, float], along: float, across: float = 0.0):
    """A defender `along` metres towards the goal from the ball.

    Placed on the line from the ball to the goal centre rather than by eye,
    because that line is the one place a defender is certainly inside the
    shooting cone — and `defenders_in_cone` is a hard in-or-out test that
    ignores anyone a step to either side of it. Half the freeze frames in an
    earlier draft had defenders that looked like they were blocking the shot and
    that the model never saw, including on the shot the fixture calls *blocked*.

    `across` nudges them off that line, which is realistic and, at 29 metres out
    where the cone is a couple of metres wide, quietly stops counting.
    """
    dx, dy = GOAL[0] - shot[0], GOAL[1] - shot[1]
    length = math.hypot(dx, dy)
    ux, uy = dx / length, dy / length
    return (
        round(shot[0] + ux * along - uy * across, 1),
        round(shot[1] + uy * along + ux * across, 1),
    )


# One freeze frame per shot, in the same order and the same coordinates the
# fixture publishes: metres on a 105x68 pitch, already mirrored to attack right.
#
# Each is written to match the outcome the fixture gives the shot. A blocked
# shot has somebody in front of it; the miss from six metres has a keeper who
# has come off his line and nobody in the way, which is why it is the best
# chance in the match and why it is worth the fixture showing a miss there.
FRAMES = [
    # video_s, shooter,        keeper,         defenders (along, across), pressure
    (412.4,  (92.1, 33.8), (103.2, 34.4), [(3.5, 0.4), (6.0, -1.2)], True),
    (631.0,  (84.6, 27.2), (103.9, 33.4), [(2.0, 0.3), (5.5, -0.8)], True),
    (908.7,  (99.2, 38.4), (101.6, 36.4), [(3.4, 3.0)], False),
    (1204.2, (76.3, 41.9), (103.6, 34.0), [(1.2, 0.0), (3.0, 0.1), (9.0, 1.5)], True),
    (1655.8, (95.8, 30.1), (103.4, 33.4), [(2.4, 0.5), (4.6, -0.9)], True),
    (2210.5, (88.4, 34.6), (103.1, 34.0), [(6.5, 0.8)], False),

    (520.3,  (81.7, 24.5), (103.7, 33.6), [(2.6, 0.1), (6.0, -1.0)], True),
    (1420.9, (97.4, 35.2), (102.2, 34.8), [(3.0, 2.6)], False),
    (1888.1, (90.2, 44.8), (103.4, 34.8), [(2.0, 0.1), (4.5, -0.7), (8.0, 1.2)], True),
    (2402.6, (72.9, 31.4), (103.2, 34.0), [(3.0, 0.2), (7.0, -1.0)], False),
]


@pytest.fixture(scope='module')
def session():
    pytest.importorskip('onnxruntime')
    return load_session()


@pytest.fixture(scope='module')
def source() -> str:
    return SAMPLE_JS.read_text(encoding='utf-8')


@pytest.fixture(scope='module')
def published(source) -> dict[float, dict]:
    """The shots as the fixture publishes them, keyed by their video second.

    Read out of the JavaScript with a regular expression rather than by running
    it. There is no build step in this project and no JS runtime is a Python
    test dependency — the same call tests/test_sample_report.py makes, for the
    same reason.
    """
    shots = {}
    for line in source.splitlines():
        found = re.search(
            r"video_s:\s*([\d.]+),\s*x_m:\s*([\d.]+),\s*y_m:\s*([\d.]+),\s*"
            r"xg:\s*([\d.]+)",
            line,
        )
        if found:
            shots[float(found.group(1))] = {
                'x_m': float(found.group(2)),
                'y_m': float(found.group(3)),
                'xg': float(found.group(4)),
            }
    return shots


def scored(session, frame) -> float:
    _, shooter, keeper, defenders, pressure = frame
    return predict_xg(
        ShotContext(
            shooter_m=shooter,
            attacking_end='right',
            keeper_m=keeper,
            defenders_m=[in_front(shooter, along, across)
                         for along, across in defenders],
            under_pressure=pressure,
            # One camera cannot see the ball's height, so the pipeline never
            # says otherwise and neither does the fixture.
            is_header=False,
        ),
        PITCH,
        session=session,
    )


class TestTheFramesMatchTheFixture:
    def test_the_fixture_has_a_shot_for_every_frame(self, published):
        assert set(published) == {frame[0] for frame in FRAMES}

    def test_the_shooter_is_where_the_fixture_puts_the_shot(self, published):
        """A frame that scores a different spot to the one drawn on the map
        would give the map a number belonging to a shot nobody can see."""
        for video_s, shooter, *_ in FRAMES:
            shot = published[video_s]
            assert (shot['x_m'], shot['y_m']) == shooter, video_s

    def test_every_xg_is_what_the_model_says(self, session, published):
        """The whole point of the file.

        A tolerance of half a rounding step, because the fixture carries what
        `shot_marks` would publish — three decimal places — and not the raw
        float.
        """
        for frame in FRAMES:
            video_s = frame[0]
            assert published[video_s]['xg'] == pytest.approx(
                round(scored(session, frame), PLACES), abs=5e-4
            ), f'shot at {video_s}s'


class TestTheFramesSayWhatTheFixtureSays:
    """The freeze frames have to tell the same story the outcomes do."""

    def test_the_blocked_shot_has_somebody_blocking_it(self, session):
        """`defenders_in_cone` is a hard in-or-out test and a defender a step
        wide of the line does not count. A fixture calling a shot blocked with
        nobody in the cone would be scoring an open shot and labelling it a
        block."""
        from cv.xg_bridge import build_features

        blocked = next(f for f in FRAMES if f[0] == 1204.2)
        _, shooter, keeper, defenders, pressure = blocked
        features = build_features(
            ShotContext(
                shooter_m=shooter, attacking_end='right', keeper_m=keeper,
                defenders_m=[in_front(shooter, a, c) for a, c in defenders],
                under_pressure=pressure,
            ),
            PITCH,
        )
        assert features['defenders_in_cone'] >= 1
        assert features['defender_pressure'] > 0

    def test_the_best_chance_is_the_one_that_was_missed(self, session, published):
        """0.535 from six metres, off target — which is the argument for
        putting xG on a coach's screen at all. If some other shot were the
        biggest number, the fixture would be making a duller point badly."""
        best = max(published.items(), key=lambda pair: pair[1]['xg'])
        assert best[0] == 908.7

    def test_a_shot_worth_nothing_is_zero_and_not_missing(self, published):
        """The distinction the whole project turns on, exercised end to end in
        the preview: 0.0 is a calibrated model saying a blocked shot from 29
        metres is worth nothing, and null would say it never ran."""
        assert published[1204.2]['xg'] == 0.0

    def test_the_match_is_a_believable_one(self, session):
        """Six shots at 0.80 xG, four at 0.43 — a match, not a highlight reel.

        This is the check the old hand-picked figures could never fail, and the
        one that would have caught both model bugs. The same ten frames are
        worth:

            xg_model6   4.15    uncalibrated
            xg_model7   1.83    calibration restored
            xg_model8   1.23    shot_height dropped

        Ten chances worth four goals is not a match anybody has played in.

        The band is wide because what it is testing is the order of magnitude,
        not the digits — those are pinned shot by shot above.
        """
        ours = sum(scored(session, f) for f in FRAMES[:6])
        theirs = sum(scored(session, f) for f in FRAMES[6:])

        assert 0.5 < ours < 2.0, ours
        assert 0.2 < theirs < 1.2, theirs
