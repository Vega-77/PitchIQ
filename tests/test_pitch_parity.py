"""Python and JavaScript must agree about where the pitch is.

`cv/pitch.py` and `calibrate/pitch-model.js` define the same geometry twice,
because the site has no build step and the CV pipeline is Python. Duplication
is fine as long as it is verified, and this is the verification: both are asked
for the same landmarks and the answers are compared.

The failure this prevents is nasty and quiet — a calibration clicked in the
browser would be fitted against slightly different coordinates in Python, and
every position downstream would be off by a metre or two with nothing to
indicate anything went wrong.

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


def js_landmarks(length_m: float, width_m: float) -> dict[str, list[float]]:
    script = (
        f"import {{ landmarks }} from {json.dumps(JS_MODEL.as_uri())};"
        f"console.log(JSON.stringify(landmarks({length_m}, {width_m})));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


@pytest.mark.parametrize(
    "length_m,width_m",
    [
        (105.0, 68.0),   # full size
        (100.0, 64.0),   # a smaller high school field
        (110.0, 70.0),   # a large one
    ],
)
def test_landmarks_match_across_languages(length_m: float, width_m: float):
    python = Pitch(length_m=length_m, width_m=width_m).landmarks()
    javascript = js_landmarks(length_m, width_m)

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


def test_js_homography_matches_opencv():
    """The browser preview and the Python fit must agree on the same points.

    The browser solves the normal equations with h33 fixed to 1; OpenCV uses a
    normalised DLT. Different algorithms, so this checks they land in the same
    place rather than assuming it.
    """
    import numpy as np

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
