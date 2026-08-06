"""The whole chain from a detected shot to a number, minus the detecting.

`cv/xg_bridge.py` was checked against the browser model long before anything
called it, so what these tests cover is the part nobody had: a `Shot` coming out
of `derive_events`, the other players read out of the frame it happened in, and a
probability landing on the event and summing into the team total.

Everything here is synthetic except the model, which is the real
`xg-sandbox/xg_model8.onnx`. That leaves exactly one link untested — whether the
shot was a shot — and that one needs footage.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_xg_pipeline.py -q
"""

import numpy as np
import pytest

from cv.calibration import Calibration
from cv.events import GOAL, attach_xg, derive_events
from cv.frames import FrameRecord, FrameTable
from cv.keeper import KeeperAssignment
from cv.pipeline import MatchReport
from cv.pitch import MatchOrientation, Pitch
from cv.teams import TEAM_A, TEAM_B
from cv.touches import Touch, TouchConfidence, TouchSequence
from cv.xg_bridge import xg_for_shots

FPS = 30.0
PITCH = Pitch()
L, W = PITCH.length_m, PITCH.width_m
MID_Y = W / 2

# Same fixture scale as tests/test_events.py: 20 pixels to the metre, a player
# 1.8m tall is 36 pixels. Keeping it identical means a shot that reads as a shot
# there reads as one here.
PX_PER_M = 20.0
SCALE = 36.0

ORIENTATION = MatchOrientation(home_attacks_first_half='right')
SIDES = {TEAM_A: 'us', TEAM_B: 'them'}


def px(x_m, y_m):
    return (x_m * PX_PER_M, y_m * PX_PER_M)


def box(track_id, x_m, y_m):
    """A detection box whose bottom centre sits on (x_m, y_m).

    `Calibration.ground_point_to_pitch` reads the bottom-centre of the box, so
    that is the only part of these numbers that has to be right.
    """
    x, y = px(x_m, y_m)
    return [track_id, x - 10, y - SCALE, x + 10, y, 0.9]


def identity_calibration(scale=PX_PER_M):
    homography = np.array([
        [1 / scale, 0.0, 0.0],
        [0.0, 1 / scale, 0.0],
        [0.0, 0.0, 1.0],
    ], dtype=np.float64)
    return Calibration(homography, PITCH, image_size=(1920, 1080))


def strike(x_m=95.0, y_m=MID_Y, others=(), teams=None):
    """One firmly struck ball through the goal mouth, with company.

    `others` is [(track_id, team, x_m, y_m)] — the keeper and defenders, who
    exist only so the model has someone to be blocked by. They are boxes in the
    frame rather than touches, because that is how they reach the pipeline: the
    xG bridge reads the frame the shot happened in, not the touch list.
    """
    shooter = Touch(
        frame_index=0,
        timestamp_s=0.0,
        track_id=7,
        team=TEAM_A,
        ball_xy=px(x_m, y_m),
        ball_m=(x_m, y_m),
        scale_px=SCALE,
        distance_ph=0.5,
        speed_before_ph_s=0.0,
        # Twenty player heights a second is a struck ball, not a roll — the
        # threshold cv/events.py uses to tell those apart.
        speed_after_ph_s=20.0,
        turn_deg=45.0,
        observed=True,
        components=TouchConfidence(1.0, 1.0, 1.0, 1.0),
    )

    team_by_track = {7: TEAM_A}
    rows = [box(7, x_m, y_m)]
    for track_id, team, ox, oy in others:
        rows.append(box(track_id, ox, oy))
        team_by_track[track_id] = team
    team_by_track.update(teams or {})

    records = [FrameRecord(
        frame_index=0,
        timestamp_s=0.0,
        players=np.array(rows, dtype=np.float32).reshape(-1, 6),
    )]

    # The ball carries on past the touch and over the line, which is what makes
    # this a shot rather than a pass to nobody.
    for step in range(1, 20):
        t = step / FPS
        x = x_m + (L + 1.0 - x_m) * step / 12.0
        records.append(FrameRecord(
            frame_index=step,
            timestamp_s=t,
            players=np.empty((0, 6), dtype=np.float32),
            ball_xy=px(min(x, L + 1.0), y_m),
            ball_observed=True,
        ))

    table = FrameTable(
        fps=FPS, frame_width=1920, frame_height=1080,
        records=records,
        team_by_track=team_by_track,
        calibration=identity_calibration(),
    )
    log = derive_events(
        TouchSequence(touches=[shooter]), table,
        pitch=PITCH, orientation=ORIENTATION,
        period='first_half', side_of_team=SIDES,
    )
    return log, table


def run(log, table, **kwargs):
    return xg_for_shots(
        log, table, identity_calibration(), ORIENTATION, 'first_half',
        SIDES, kwargs.pop('keepers', None), **kwargs,
    )


class TestTheHarness:
    """If these fail, the tests below are testing nothing."""

    def test_the_fixture_actually_produces_a_shot(self):
        log, _ = strike()
        assert len(log.shots()) == 1
        assert log.shots()[0].outcome == GOAL


class TestAgainstTheRealModel:
    def test_a_shot_gets_a_probability(self):
        pytest.importorskip('onnxruntime')
        log, table = strike(others=[(30, TEAM_B, L - 2.0, MID_Y)])
        xg, warnings = run(log, table)

        assert warnings == []
        assert len(xg) == 1
        value = next(iter(xg.values()))
        assert 0.0 < value < 1.0

    def test_the_number_reaches_the_event_and_the_team_total(self):
        """The point of the whole exercise: `Shot.xg` stops being None."""
        pytest.importorskip('onnxruntime')
        log, table = strike(others=[(30, TEAM_B, L - 2.0, MID_Y)])
        xg, _ = run(log, table)
        attach_xg(log, xg)

        assert log.shots()[0].xg is not None

        report = MatchReport(source='fixture', duration_s=10.0, processing_s=0.1)
        report.events = log
        report.movement_available = True
        data = report.to_json()

        assert data['teams'][TEAM_A]['xg'] == pytest.approx(
            log.shots()[0].xg, abs=1e-3
        )

    def test_a_shot_from_the_six_yard_box_beats_one_from_forty_metres(self):
        pytest.importorskip('onnxruntime')
        """A sanity check on direction, not on calibration.

        If this ever inverts, the attacking end is being resolved backwards and
        every xG in the report describes a shot at the wrong goal.
        """
        close, close_table = strike(x_m=L - 5.0)
        far, far_table = strike(x_m=L - 40.0)

        close_xg = next(iter(run(close, close_table)[0].values()))
        far_xg = next(iter(run(far, far_table)[0].values()))
        assert close_xg > far_xg

    def test_naming_the_keeper_changes_the_answer(self):
        """Which of the defenders is the keeper is not a detail.

        Without being told, the bridge picks whoever is nearest the goal being
        shot at. Here that is a covering centre-back who has dropped onto the
        line, and the actual keeper has come three metres off it — the exact
        case `shot_context_from_tracking` calls occasionally catastrophic. So
        this pins that `cv/keeper.py`'s answer is reaching the model and
        changing it, rather than being accepted and ignored.
        """
        pytest.importorskip('onnxruntime')
        others = [
            (30, TEAM_B, L - 3.0, MID_Y),        # the keeper, off his line
            (20, TEAM_B, L - 0.5, MID_Y + 1.0),  # a defender covering the goal
        ]
        log, table = strike(others=others)

        guessed = next(iter(run(log, table)[0].values()))
        told = next(iter(run(
            log, table, keepers=KeeperAssignment(by_team={TEAM_B: {30}}),
        )[0].values()))

        assert guessed != told


class TestWhenItCannotAnswer:
    """Every one of these must degrade, not raise. A match report that loses a
    column is fine; one that dies on the way to the coach is not."""

    def test_no_shots_means_no_model_and_no_complaint(self):
        log, table = strike()
        log.events = [e for e in log.events if e.type != 'shot']
        assert run(log, table) == ({}, [])

    def test_an_unknown_attacking_end_is_counted_and_explained(self):
        log, table = strike(others=[(30, TEAM_B, L - 2.0, MID_Y)])
        xg, warnings = xg_for_shots(
            log, table, identity_calibration(), ORIENTATION, 'first_half',
            side_of_team=None,
        )
        assert xg == {}
        assert len(warnings) == 1
        assert 'which goal' in warnings[0]

    def test_a_broken_model_warns_instead_of_raising(self):
        class _Broken:
            def run(self, *args, **kwargs):
                raise RuntimeError('no such input')

        log, table = strike(others=[(30, TEAM_B, L - 2.0, MID_Y)])
        xg, warnings = run(log, table, session=_Broken())

        assert xg == {}
        assert warnings and 'no such input' in warnings[0]
