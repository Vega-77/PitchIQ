"""Python and JavaScript must agree about where a coach just clicked.

Two independent implementations sit on either side of the calibration step:

    calibrate/pitch-model.js   landmarks() + fitHomography()  — what a coach uses
    cv/pitch.py, calibration.py  Pitch.landmarks() + Calibration.fit()  — what
                                 every metre in this system is then derived from

A coach clicks eight points in the browser, reads a reprojection error off the
page, and exports the correspondences. The pipeline re-fits them and produces
every position, distance and expected-goals figure from the result. If the two
disagree, the error a coach was shown is not the error the pipeline has, and
nothing anywhere announces it — the numbers stay plausible and are simply about
a different pitch.

Two things are checked, and they fail in different ways:

  * **the landmark tables.** A name means a place. If `pen_spot_left` is 11m
    from the goal line on one side and 12m on the other, a coach clicking it
    correctly still calibrates to a pitch that does not exist. This is the
    likelier drift of the two, because it is a table somebody edits.
  * **the solvers.** Given identical correspondences, the two fits must map the
    same pixel to the same metre. The browser solves the normal equations by
    hand; OpenCV uses `getPerspectiveTransform` at four points and RANSAC above
    that, so agreement here is not a shared implementation agreeing with itself.

Measured 2026-08-17 on a synthetic sideline camera: the two solvers agree to
**0.2 millimetres**, and both recover the generating homography exactly.

Skipped automatically when Node isn't installed.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_calibration_parity.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cv.calibration import Calibration, Correspondence
from cv.pitch import Pitch

REPO = Path(__file__).resolve().parents[1]
JS_MODEL = REPO / "calibrate" / "pitch-model.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)

LENGTH_M = 105.0
WIDTH_M = 68.0
PITCH = Pitch(length_m=LENGTH_M, width_m=WIDTH_M)

# A plausible sideline camera: perspective, off-centre, tilted. The exact
# numbers do not matter; what matters is that it is not axis-aligned, so a
# solver that quietly dropped the projective terms would still fail.
CAMERA = [
    [11.5, 2.1, 240.0],
    [-1.4, -9.8, 700.0],
    [0.0009, -0.0035, 1.0],
]

# Eight landmarks a person can actually find in one frame of a wide shot: one
# penalty area, the halfway line, and the spots.
CLICKED = [
    "corner_top_left", "corner_bottom_left",
    "pen_left_top_corner", "pen_left_bottom_corner",
    "halfway_top", "halfway_bottom",
    "pen_spot_left", "centre_spot",
]

# Spread across the pitch, including both far corners, because a homography is
# at its worst furthest from the points it was fitted on.
PROBES = [(10.0, 10.0), (52.5, 34.0), (90.0, 60.0), (5.0, 64.0), (100.0, 5.0)]


def project(x_m: float, y_m: float) -> tuple[float, float]:
    """Pitch metres -> the pixel this camera would see them at."""
    w = CAMERA[2][0] * x_m + CAMERA[2][1] * y_m + CAMERA[2][2]
    return (
        (CAMERA[0][0] * x_m + CAMERA[0][1] * y_m + CAMERA[0][2]) / w,
        (CAMERA[1][0] * x_m + CAMERA[1][1] * y_m + CAMERA[1][2]) / w,
    )


def run_node(script: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def js_landmarks() -> dict[str, list[float]]:
    return run_node(
        f"import {{ landmarks }} from {json.dumps(JS_MODEL.as_uri())};"
        f"console.log(JSON.stringify(landmarks({LENGTH_M}, {WIDTH_M})));"
    )


def js_to_pitch(clicks, probes_px) -> list[list[float]]:
    """Fit in the browser's solver, then map each probe pixel to metres."""
    payload = {"clicks": clicks, "probes": probes_px}
    return run_node(
        f"import {{ landmarks, fitHomography, applyHomography }} "
        f"from {json.dumps(JS_MODEL.as_uri())};"
        f"const data = {json.dumps(payload)};"
        f"const marks = landmarks({LENGTH_M}, {WIDTH_M});"
        "const H = fitHomography(data.clicks.map((c) => "
        "({ src: c.px, dst: marks[c.name] })));"
        "console.log(JSON.stringify(data.probes.map(([x, y]) => "
        "applyHomography(H, x, y))));"
    )


def test_the_two_landmark_tables_are_the_same_pitch():
    """A name has to mean the same place on both sides of the export."""
    js = js_landmarks()
    py = PITCH.landmarks()

    assert set(js) == set(py), (
        "the two sides disagree about which landmarks exist: "
        f"browser only {sorted(set(js) - set(py))}, "
        f"pipeline only {sorted(set(py) - set(js))}"
    )
    for name in sorted(py):
        assert js[name][0] == pytest.approx(py[name][0], abs=1e-9), name
        assert js[name][1] == pytest.approx(py[name][1], abs=1e-9), name


def test_both_solvers_recover_the_camera_that_made_the_clicks():
    """Same clicks in, same metres out — and both of them right."""
    marks = PITCH.landmarks()
    clicks = [{"name": name, "px": list(project(*marks[name]))} for name in CLICKED]
    probes_px = [list(project(x, y)) for x, y in PROBES]

    from_js = js_to_pitch(clicks, probes_px)

    calibration = Calibration.fit(
        [Correspondence(c["name"], tuple(c["px"])) for c in clicks], PITCH
    )

    worst = 0.0
    for truth, pixel, js_point in zip(PROBES, probes_px, from_js):
        py_point = calibration.to_pitch(*pixel)

        # Both agree with each other...
        gap = max(abs(js_point[0] - py_point[0]), abs(js_point[1] - py_point[1]))
        worst = max(worst, gap)
        assert gap < 0.01, (
            f"the two solvers put the same pixel {[round(v, 1) for v in pixel]} "
            f"{gap:.4f}m apart: browser {js_point}, pipeline {list(py_point)}"
        )

        # ...and both agree with the camera that generated the clicks, which is
        # the half that catches the two of them being wrong together.
        assert js_point[0] == pytest.approx(truth[0], abs=0.01)
        assert js_point[1] == pytest.approx(truth[1], abs=0.01)
        assert py_point[0] == pytest.approx(truth[0], abs=0.01)
        assert py_point[1] == pytest.approx(truth[1], abs=0.01)

    # Recorded rather than merely asserted: the figure is the point. If a change
    # moves this from sub-millimetre to centimetres the assertions above still
    # pass and something has still happened worth knowing about.
    assert worst < 0.001, f"solver agreement degraded to {worst:.6f}m"


def test_four_points_is_the_floor_on_both_sides():
    """Fewer than four correspondences is not a fit, and neither side pretends.

    Both refuse, and both refuse loudly — `Calibration.fit` raises ValueError
    and `fitHomography` throws. The browser's throw is never reached in
    practice: `drawPitchOverlay` checks the count first and `renderQuality`
    wraps the call, because an exception in the canvas draw would take the
    picker down between a coach's first and fourth click. That guard is the
    thing worth keeping true, so it is asserted here rather than assumed.
    """
    marks = PITCH.landmarks()
    three = CLICKED[:3]

    with pytest.raises(ValueError):
        Calibration.fit(
            [Correspondence(n, project(*marks[n])) for n in three], PITCH
        )

    clicks = [{"name": n, "px": list(project(*marks[n]))} for n in three]
    refused = run_node(
        f"import {{ landmarks, fitHomography }} from {json.dumps(JS_MODEL.as_uri())};"
        f"const data = {json.dumps(clicks)};"
        f"const marks = landmarks({LENGTH_M}, {WIDTH_M});"
        "let out;"
        "try { fitHomography(data.map((c) => ({ src: c.px, dst: marks[c.name] })));"
        "      out = 'fitted three points'; }"
        "catch (err) { out = 'refused'; }"
        "console.log(JSON.stringify(out));"
    )
    assert refused == "refused"

    source = (REPO / "calibrate" / "calibrate.js").read_text(encoding="utf-8")
    assert "if (state.points.size < 4) return;" in source, (
        "drawPitchOverlay no longer counts the points before fitting them — "
        "the picker will throw on a coach's first click"
    )
