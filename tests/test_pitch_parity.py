"""Python and JavaScript must agree about where the pitch is.

`cv/pitch.py` and `calibrate/pitch-model.js` define the same geometry twice,
because the site has no build step and the CV pipeline is Python. Duplication
is fine as long as it is verified, and this is the verification: both are asked
for the same landmarks and the answers are compared.

The failure this prevents is nasty and quiet — a calibration clicked in the
browser would be fitted against slightly different coordinates in Python, and
every position downstream would be off by a metre or two with nothing to
indicate anything went wrong.

Both models now take the markings as parameters rather than baking in the
Laws, because school pitches are painted with a tape and a guess. That doubled
the surface this has to cover: a disagreement about the *defaults* would show
up on any pitch and be caught in a minute, but a disagreement about how a
*passed* marking is applied would only appear on the odd-sized fields, which
are exactly the ones nobody has a reference measurement for.

Skipped automatically when Node isn't installed.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cv.pitch import Pitch

REPO = Path(__file__).resolve().parents[1]
JS_MODEL = REPO / "calibrate" / "pitch-model.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)


# The same seven dimensions, spelled the way each language spells them.
# `Pitch` keeps snake_case fields and the browser keeps camelCase keys because
# each is idiomatic where it lives; this table is the seam, and it is checked
# against both sides below rather than trusted.
MARK_FIELDS = {
    "penaltyAreaLengthM": "penalty_area_length_m",
    "penaltyAreaWidthM": "penalty_area_width_m",
    "goalAreaLengthM": "goal_area_length_m",
    "goalAreaWidthM": "goal_area_width_m",
    "penaltySpotM": "penalty_spot_m",
    "goalWidthM": "goal_width_m",
    "centreCircleRadiusM": "centre_circle_radius_m",
}

# Two fields nobody paints to the Laws. The first is measured off a real
# under-16 pitch; the second is deliberately absurd, because a model that only
# agrees near its defaults is a model that is still hard-coding them.
SCHOOL_MARKS = {
    "penaltyAreaLengthM": 15.0,
    "penaltyAreaWidthM": 38.0,
    "penaltySpotM": 10.0,
    "goalAreaLengthM": 5.0,
    "goalAreaWidthM": 16.0,
}
STRANGE_MARKS = {
    "penaltyAreaLengthM": 21.5,
    "penaltyAreaWidthM": 47.0,
    "penaltySpotM": 14.0,
    "goalAreaLengthM": 8.5,
    "goalAreaWidthM": 25.0,
    "goalWidthM": 5.5,
    "centreCircleRadiusM": 12.0,
}


def node(script: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def js_landmarks(
    length_m: float, width_m: float, marks: dict[str, float] | None = None
) -> dict[str, list[float]]:
    return node(
        f"import {{ landmarks }} from {json.dumps(JS_MODEL.as_uri())};"
        f"console.log(JSON.stringify(landmarks("
        f"{length_m}, {width_m}, {json.dumps(marks)})));"
    )


def js_default_marks() -> dict[str, float]:
    return node(
        f"import {{ DEFAULT_MARKS }} from {json.dumps(JS_MODEL.as_uri())};"
        "console.log(JSON.stringify(DEFAULT_MARKS));"
    )


def python_pitch(length_m: float, width_m: float, marks: dict[str, float] | None):
    kwargs = {MARK_FIELDS[k]: v for k, v in (marks or {}).items()}
    return Pitch(length_m=length_m, width_m=width_m, **kwargs)


def assert_landmarks_agree(length_m, width_m, marks):
    python = python_pitch(length_m, width_m, marks).landmarks()
    javascript = js_landmarks(length_m, width_m, marks)

    assert set(python) == set(javascript), (
        "landmark names differ between cv/pitch.py and calibrate/pitch-model.js: "
        f"python-only={sorted(set(python) - set(javascript))}, "
        f"js-only={sorted(set(javascript) - set(python))}"
    )

    for name, (px, py) in python.items():
        jx, jy = javascript[name]
        assert (px, py) == pytest.approx((jx, jy), abs=1e-9), (
            f"{name} disagrees: python={(px, py)} js={(jx, jy)}"
        )


@pytest.mark.parametrize(
    "length_m,width_m",
    [
        (105.0, 68.0),   # full size
        (100.0, 64.0),   # a smaller high school field
        (110.0, 70.0),   # a large one
    ],
)
def test_landmarks_match_across_languages(length_m: float, width_m: float):
    assert_landmarks_agree(length_m, width_m, None)


@pytest.mark.parametrize(
    "marks",
    [
        pytest.param(SCHOOL_MARKS, id="school"),
        pytest.param(STRANGE_MARKS, id="strange"),
        # One dimension at a time, so a failure names the culprit instead of
        # saying only that seven of them together came out wrong.
        *(
            pytest.param({key: value}, id=key)
            for key, value in STRANGE_MARKS.items()
        ),
    ],
)
def test_landmarks_match_with_custom_markings(marks: dict[str, float]):
    """Every marking has to travel across the seam, not just the pitch size."""

    assert_landmarks_agree(100.0, 64.0, marks)


def test_marking_names_cover_both_models():
    """The translation table is complete in both directions.

    A marking added to one language and forgotten in the other would otherwise
    slip through every test above, because they only ever ask about the
    dimensions this table already knows.
    """

    javascript = js_default_marks()
    assert set(javascript) == set(MARK_FIELDS), (
        "DEFAULT_MARKS and MARK_FIELDS disagree: "
        f"js-only={sorted(set(javascript) - set(MARK_FIELDS))}, "
        f"table-only={sorted(set(MARK_FIELDS) - set(javascript))}"
    )
    for field in MARK_FIELDS.values():
        assert hasattr(Pitch(), field), f"Pitch has no {field}"


def test_default_markings_match():
    """The Laws values themselves, before anyone overrides them."""

    javascript = js_default_marks()
    pitch = Pitch()
    for key, field in MARK_FIELDS.items():
        assert getattr(pitch, field) == pytest.approx(javascript[key], abs=1e-9), (
            f"{key}: python={getattr(pitch, field)} js={javascript[key]}"
        )
    assert pitch.markings_are_standard


def test_js_homography_matches_opencv():
    """The browser preview and the Python fit must agree on the same points.

    The browser solves the normal equations with h33 fixed to 1; OpenCV uses a
    normalised DLT. Different algorithms, so this checks they land in the same
    place rather than assuming it.
    """

    from cv.calibration import Calibration, Correspondence

    pitch = Pitch()
    marks = pitch.landmarks()

    # A plausible camera view.
    pixel_by_name = {
        "corner_bottom_left": (140.0, 640.0),
        "corner_bottom_right": (1150.0, 640.0),
        "corner_top_right": (980.0, 300.0),
        "corner_top_left": (310.0, 300.0),
        "halfway_bottom": (645.0, 640.0),
        "halfway_top": (645.0, 300.0),
    }

    calib = Calibration.fit(
        [Correspondence(n, p) for n, p in pixel_by_name.items()], pitch
    )

    pairs = [
        {"src": list(pixel_by_name[n]), "dst": list(marks[n])} for n in pixel_by_name
    ]
    script = (
        f"import {{ fitHomography, applyHomography }} from {json.dumps(JS_MODEL.as_uri())};"
        f"const H = fitHomography({json.dumps(pairs)});"
        "const probes = [[400,500],[800,450],[645,620],[1000,350]];"
        "console.log(JSON.stringify(probes.map(([x,y]) => applyHomography(H,x,y))));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")

    js_points = json.loads(result.stdout)
    probes = [(400, 500), (800, 450), (645, 620), (1000, 350)]

    for (px, py), js in zip(probes, js_points):
        python_result = calib.to_pitch(px, py)
        assert python_result == pytest.approx(tuple(js), abs=0.01), (
            f"pixel {(px, py)}: python={python_result} js={tuple(js)}"
        )
