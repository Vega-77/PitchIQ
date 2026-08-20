"""The `--frame` half of the calibrate CLI: paint as evidence.

Everything the tool printed before this existed was measured on the coach's own
clicks. `Calibration.error` is fitted and graded on the same points, so it is
optimistic by construction; `holdout_error` is honest and still only knows what
the human typed in. A frame of the pitch is the first thing in this tool that
the human did not supply, and these tests are about the wrapper around it: does
it refuse a frame that does not belong to the calibration, does it save the
homography it actually graded, and does it survive a frame with nothing in it.

What a line fit *means* is `tests/test_lines.py`'s job, and the synthetic camera
is imported from there rather than built twice -- two renderers drifting apart
would make one of these files quietly stop testing anything.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_calibrate_cli.py -q
"""

from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from cv.calibration import Calibration
from cv.experiments.calibrate import main
from cv.pitch import Pitch
from test_lines import (
    FRAME_H,
    FRAME_W,
    camera,
    landmark_error,
    render,
    rough_clicks,
)


@pytest.fixture(scope="module")
def scene():
    pitch = Pitch()
    matrix = camera(pitch)
    return pitch, matrix, render(pitch, matrix)


def write_points(path, clicks, pitch, size=(FRAME_W, FRAME_H)):
    """The browser picker's export format, which is what the CLI reads."""
    path.write_text(json.dumps({
        "pitch": {"length_m": pitch.length_m, "width_m": pitch.width_m},
        "image_size": list(size) if size else None,
        "points": [
            {"landmark": c.landmark, "x": c.pixel[0], "y": c.pixel[1]}
            for c in clicks
        ],
    }), encoding="utf-8")
    return path


class TestFrameArgument:
    def test_it_grades_the_calibration_against_the_paint(self, scene, tmp_path, capsys):
        pitch, matrix, frame = scene
        points = write_points(
            tmp_path / "p.json", rough_clicks(pitch, matrix, 1.0, 0), pitch
        )
        image = tmp_path / "frame.png"
        cv2.imwrite(str(image), frame)

        main([str(points), "--out", str(tmp_path / "c.json"),
              "--frame", str(image)])
        out = capsys.readouterr().out

        assert "line segments" in out
        assert "against paint" in out
        assert "% matched)" in out
        # The order is the point: a coach who reads the metres first and the
        # coverage second can be fooled by a calibration that matched nothing.
        assert out.index("against paint") < out.index("Read coverage before")

    def test_a_frame_of_the_wrong_size_is_refused(self, scene, tmp_path, capsys):
        """Not a warning, and not silently rescaled.

        A homography is in the pixels of one particular image. Graded against a
        720p export of a 1080p frame every number would be wrong by exactly the
        ratio between them -- and wrong in the direction that looks fine.
        """
        pitch, matrix, frame = scene
        points = write_points(
            tmp_path / "p.json", rough_clicks(pitch, matrix, 1.0, 0), pitch
        )
        image = tmp_path / "half.png"
        cv2.imwrite(str(image), cv2.resize(frame, (FRAME_W // 2, FRAME_H // 2)))
        out_path = tmp_path / "c.json"

        assert main([str(points), "--out", str(out_path), "--frame", str(image)]) == 1
        assert "but these points were clicked on" in capsys.readouterr().out
        assert not out_path.exists(), "a refused run must not leave a file behind"

    def test_an_export_with_no_frame_size_says_so(self, scene, tmp_path, capsys):
        """Older exports carry no size, so the check cannot run. Saying nothing
        would leave the reader thinking it had."""
        pitch, matrix, frame = scene
        points = write_points(
            tmp_path / "p.json", rough_clicks(pitch, matrix, 1.0, 0), pitch, size=None
        )
        image = tmp_path / "frame.png"
        cv2.imwrite(str(image), frame)

        main([str(points), "--out", str(tmp_path / "c.json"), "--frame", str(image)])
        assert "taken on trust" in capsys.readouterr().out

    def test_a_mistyped_frame_path_stops_before_any_work(self, scene, tmp_path, capsys):
        """Checked up front, next to the points file, so a typo costs a second
        rather than a fitted-and-saved calibration nobody asked for."""
        pitch, matrix, _ = scene
        points = write_points(
            tmp_path / "p.json", rough_clicks(pitch, matrix, 1.0, 0), pitch
        )
        out_path = tmp_path / "c.json"

        assert main([str(points), "--out", str(out_path),
                     "--frame", str(tmp_path / "typo.png")]) == 1
        assert "typo.png not found" in capsys.readouterr().out
        assert not out_path.exists()

    def test_a_frame_with_no_paint_costs_nothing(self, scene, tmp_path, capsys):
        """A frame grabbed during a stoppage, or from a camera pointed at the
        wrong half. The calibration is still saved, and the reader is told why
        there is no line fit rather than left to wonder."""
        pitch, matrix, _ = scene
        points = write_points(
            tmp_path / "p.json", rough_clicks(pitch, matrix, 1.0, 0), pitch
        )
        image = tmp_path / "blank.png"
        cv2.imwrite(str(image), render(pitch, matrix, paint=False))
        out_path = tmp_path / "c.json"

        main([str(points), "--out", str(out_path), "--frame", str(image)])
        assert "No paint at all" in capsys.readouterr().out
        assert out_path.exists()


class TestRefine:
    def test_it_saves_the_homography_it_graded(self, scene, tmp_path, capsys):
        """The whole reason `--refine` is worth having, end to end.

        Three pixels of jitter on eight landmarks is a coach concentrating on a
        phone screen, and it is enough to put the worst landmark past the bar
        `CalibrationError.is_usable` sets. The paint, which nobody clicked,
        pulls it back -- and what lands on disk has to be that corrected
        homography, not the clicks it started from.
        """
        pitch, matrix, frame = scene
        clicks = rough_clicks(pitch, matrix, 3.0, 0)
        points = write_points(tmp_path / "p.json", clicks, pitch)
        image = tmp_path / "frame.png"
        cv2.imwrite(str(image), frame)
        out_path = tmp_path / "c.json"

        main([str(points), "--out", str(out_path),
              "--frame", str(image), "--refine"])

        before = Calibration.fit(clicks, pitch, (FRAME_W, FRAME_H))
        saved = Calibration.load(out_path)
        assert not np.allclose(saved.H, before.H), "the refined fit was not saved"
        assert (landmark_error(saved, pitch, matrix)[1]
                < landmark_error(before, pitch, matrix)[1])
        assert "refined       median" in capsys.readouterr().out

    def test_clicks_that_cannot_be_improved_are_left_alone(self, scene, tmp_path, capsys):
        """A refusal costs a coach nothing; a silent bad refinement costs them
        every number in the match report and gives them no way to find out."""
        pitch, matrix, frame = scene
        clicks = rough_clicks(pitch, matrix, 0.0, 0)
        points = write_points(tmp_path / "p.json", clicks, pitch)
        image = tmp_path / "frame.png"
        cv2.imwrite(str(image), frame)
        out_path = tmp_path / "c.json"

        main([str(points), "--out", str(out_path),
              "--frame", str(image), "--refine"])
        out = capsys.readouterr().out

        if "refined       no." in out:
            assert np.allclose(
                Calibration.load(out_path).H,
                Calibration.fit(clicks, pitch, (FRAME_W, FRAME_H)).H,
            )
        else:
            # Accepting is allowed -- rasterised paint has width and perfect
            # clicks do not -- but only if it really did land closer.
            assert landmark_error(
                Calibration.load(out_path), pitch, matrix
            )[1] < 0.5

    def test_refine_needs_a_frame(self, tmp_path):
        """`--refine` alone is a typo, not a request. Argparse exits 2."""
        with pytest.raises(SystemExit) as err:
            main([str(tmp_path / "p.json"), "--refine"])
        assert err.value.code == 2


class TestOverlay:
    def test_it_writes_a_picture_of_the_answer(self, scene, tmp_path, capsys):
        """Worth more than any number the tool prints: an outline that sits on
        the paint is right, and one that does not is wrong in a way a coach can
        point at without knowing what a homography is."""
        pitch, matrix, frame = scene
        points = write_points(
            tmp_path / "p.json", rough_clicks(pitch, matrix, 1.0, 0), pitch
        )
        image = tmp_path / "frame.png"
        cv2.imwrite(str(image), frame)
        overlay = tmp_path / "sub" / "overlay.png"

        main([str(points), "--out", str(tmp_path / "c.json"),
              "--frame", str(image), "--overlay", str(overlay)])

        assert overlay.exists(), "the parent directory should have been created"
        drawn = cv2.imread(str(overlay))
        assert drawn.shape == frame.shape
        assert not np.array_equal(drawn, frame), "nothing was drawn"
        assert "overlay    ->" in capsys.readouterr().out
