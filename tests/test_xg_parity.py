"""Python and JavaScript must agree about what a shot looks like to the model.

`cv/xg_bridge.py` and `xg-sandbox/xg-model.js` each build the same 12-feature
vector for the same xG model, independently, in two languages. The Python side
is checked against the training script by tests/test_xg_bridge.py. The
JavaScript side, until this file, was checked against nothing.

The failure this prevents is quiet and expensive: the sandbox says a chance was
worth 0.31 and the CV pipeline says 0.60 for the same shot, and both look
plausible enough that nobody questions either. Feature order, coordinate
conventions and fallback values all have to match, and none of them announce
themselves when they drift.

Everything is expressed in StatsBomb space and converted into each side's own
input convention, because that is the only place the two agree by definition:

    Python  metres      -> Pitch.to_statsbomb()
    JS      normalised  -> toStatsBomb()   x = (1 - ny) * 120, y = nx * 80

Skipped automatically when Node isn't installed.

Run:  PitchIQHelper/.venv/Scripts/python.exe -m pytest tests/test_xg_parity.py -q
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cv.pitch import STATSBOMB_LENGTH, STATSBOMB_WIDTH, Pitch
from cv.xg_bridge import DEFAULT_SHOT_HEIGHT, FEATURE_ORDER, ShotContext, build_features

REPO = Path(__file__).resolve().parents[1]
JS_MODEL = REPO / "xg-sandbox" / "xg-model.js"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="Node is not installed"
)

PITCH = Pitch()


def metres_from_statsbomb(sx: float, sy: float) -> tuple[float, float]:
    """StatsBomb -> pitch metres, the input Python's build_features wants."""
    return (
        sx / STATSBOMB_LENGTH * PITCH.length_m,
        sy / STATSBOMB_WIDTH * PITCH.width_m,
    )


def normalised_from_statsbomb(sx: float, sy: float) -> dict[str, float]:
    """StatsBomb -> the sandbox's 0-1 half-pitch coordinates.

    The inverse of toStatsBomb() in xg-model.js. The sandbox draws the attack
    moving up the screen, so its y axis runs opposite to StatsBomb's x.
    """
    return {"x": sy / STATSBOMB_WIDTH, "y": 1.0 - sx / STATSBOMB_LENGTH}


def js_features(shooter, keeper, defenders, shot) -> dict[str, float]:
    payload = {
        "shooter": normalised_from_statsbomb(*shooter),
        "keeper": normalised_from_statsbomb(*keeper) if keeper else None,
        "defenders": [normalised_from_statsbomb(*d) for d in defenders],
        "shot": shot,
    }
    script = (
        f"import {{ buildFeatures }} from {json.dumps(JS_MODEL.as_uri())};"
        f"const setup = {json.dumps(payload)};"
        "console.log(JSON.stringify(buildFeatures(setup)));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def python_features(shooter, keeper, defenders, shot) -> dict[str, float]:
    context = ShotContext(
        shooter_m=metres_from_statsbomb(*shooter),
        keeper_m=metres_from_statsbomb(*keeper) if keeper else None,
        defenders_m=[metres_from_statsbomb(*d) for d in defenders],
        is_header=shot["isHeader"],
        under_pressure=shot["underPressure"],
        is_open_play=shot["isOpenPlay"],
        shot_height=shot["height"],
    )
    return build_features(context, PITCH)


FOOT = {"isFoot": True, "isHeader": False, "underPressure": False,
        "isOpenPlay": True, "height": DEFAULT_SHOT_HEIGHT}
HEADER = {**FOOT, "isFoot": False, "isHeader": True, "height": 1.9}
PRESSURED = {**FOOT, "underPressure": True}
SET_PIECE = {**FOOT, "isOpenPlay": False}

# Shooter, keeper and defenders, all in StatsBomb coordinates.
SCENARIOS = {
    "close central": ((114.0, 40.0), (118.0, 40.0), [(116.0, 38.0)], FOOT),
    "penalty spot": ((108.0, 40.0), (119.0, 40.0), [], FOOT),
    "edge of the box": ((102.0, 40.0), (117.0, 40.0), [(108.0, 39.0), (110.0, 44.0)], FOOT),
    "tight angle": ((114.0, 62.0), (118.0, 44.0), [(116.0, 58.0)], FOOT),
    "long range": ((85.0, 30.0), (117.0, 40.0), [(95.0, 33.0), (100.0, 28.0)], FOOT),
    "header": ((112.0, 41.0), (118.0, 40.0), [(114.0, 40.0)], HEADER),
    "under pressure": ((105.0, 36.0), (117.0, 40.0), [(107.0, 37.0)], PRESSURED),
    "set piece": ((100.0, 44.0), (117.0, 40.0), [], SET_PIECE),
    "keeper off line": ((104.0, 40.0), (108.0, 40.0), [], FOOT),
    "defender behind shooter": ((100.0, 40.0), (118.0, 40.0), [(95.0, 40.0)], FOOT),
    # The case the sandbox never reaches, because it always draws a keeper —
    # but the CV pipeline will, whenever the keeper is out of frame or missed.
    "no keeper seen": ((104.0, 40.0), None, [(108.0, 41.0)], FOOT),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_features_match_across_languages(name: str):
    shooter, keeper, defenders, shot = SCENARIOS[name]

    python = python_features(shooter, keeper, defenders, shot)
    javascript = js_features(shooter, keeper, defenders, shot)

    assert set(python) == set(javascript), (
        "feature names differ between cv/xg_bridge.py and xg-sandbox/xg-model.js: "
        f"python-only={sorted(set(python) - set(javascript))}, "
        f"js-only={sorted(set(javascript) - set(python))}"
    )

    for feature in FEATURE_ORDER:
        assert python[feature] == pytest.approx(javascript[feature], abs=1e-6), (
            f"{name}: {feature} disagrees — "
            f"python={python[feature]!r} js={javascript[feature]!r}"
        )


def test_feature_order_matches_across_languages():
    """The model takes a bare array, so order is as load-bearing as the values."""
    script = (
        f"import {{ FEATURE_ORDER }} from {json.dumps(JS_MODEL.as_uri())};"
        "console.log(JSON.stringify(FEATURE_ORDER));"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(f"node failed: {result.stderr.strip()}")

    assert json.loads(result.stdout) == FEATURE_ORDER


# ---------------------------------------------------------------- prediction

def test_the_bridge_can_actually_run_the_model():
    """cv/xg_bridge.py's prediction path, executed for the first time.

    Until onnxruntime was installed, `_predict` had never run at all — the
    module was tested up to the feature vector and no further. This checks the
    last step: that the vector the bridge builds is one the real model accepts,
    and that what comes back is a probability rather than a class label.
    """
    pytest.importorskip('onnxruntime')
    from cv.xg_bridge import _predict, feature_vector, load_session

    session = load_session()

    context = ShotContext(shooter_m=metres_from_statsbomb(108.0, 40.0))
    probability = _predict(session, feature_vector(context, PITCH))

    assert 0.0 <= probability <= 1.0, f'not a probability: {probability}'


def test_closer_shots_score_higher_through_the_real_model():
    """A sanity check on the whole chain, not on the model's accuracy.

    If distance came through the vector in the wrong column, or the goal ended
    up at the wrong end, this ordering is what would break — and it would break
    while every individual number still looked plausible.
    """
    pytest.importorskip('onnxruntime')
    from cv.xg_bridge import _predict, feature_vector, load_session

    session = load_session()

    def xg_from(sx: float, sy: float) -> float:
        context = ShotContext(
            shooter_m=metres_from_statsbomb(sx, sy),
            keeper_m=metres_from_statsbomb(118.0, 40.0),
        )
        return _predict(session, feature_vector(context, PITCH))

    six_yards = xg_from(114.0, 40.0)
    penalty_spot = xg_from(108.0, 40.0)
    long_range = xg_from(85.0, 30.0)

    assert six_yards > penalty_spot > long_range, (
        f'6yd={six_yards:.3f} spot={penalty_spot:.3f} long={long_range:.3f}'
    )


def test_the_pipeline_and_the_browser_load_the_same_model():
    """Two files named xg_model6.onnx exist and are not the same model.

    xg-sandbox/ holds the one the browser fetches; PitchIQHelper/ holds the
    output of a later training run that was never adopted. They disagree by up
    to 0.29 xG on the same shot. This pins the pipeline to the browser's copy,
    so a coach cannot be shown one number by the sandbox and a different one by
    the CV path for the same chance.
    """
    from cv.xg_bridge import MODEL_PATH

    browser_model = REPO / 'xg-sandbox' / 'xg_model6.onnx'
    assert MODEL_PATH == browser_model, (
        f'the pipeline would load {MODEL_PATH}, the browser loads {browser_model}'
    )
    assert MODEL_PATH.exists(), f'missing model: {MODEL_PATH}'
