import os
import numpy as np
import pandas as pd
from xgboost import XGBClassifier
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import matplotlib.pyplot as plt
import time

# ---------------------------------------------------------------------------
# Pitch constants  (StatsBomb coordinate system: 120x80)
# ---------------------------------------------------------------------------
GOAL   = np.array([120.0, 40.0])
POST_L = np.array([120.0, 36.0])
POST_R = np.array([120.0, 44.0])

# ---------------------------------------------------------------------------
# Data loading  (cached after first run)
# ---------------------------------------------------------------------------
CACHE_FILE = "xg_raw_shots_cache.pkl"

# Bumped from xg_model6 on 2026-08-06, when the export below was found to be
# throwing away the calibration step. See the note above the ONNX export.
ONNX_FILE  = "xg_model7.onnx"

# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def dist(a, b=GOAL):
    return np.linalg.norm(np.array(a) - np.array(b))


def shot_angle(loc):
    loc = np.array(loc)
    a = POST_L - loc
    b = POST_R - loc
    cos = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9)
    return np.arccos(np.clip(cos, -1, 1))


def in_shot_cone(p, ball):
    """Barycentric test — is point p inside the ball→post_L→post_R triangle?"""
    ball, p = np.array(ball), np.array(p)
    v0, v1, v2 = POST_R - ball, POST_L - ball, p - ball
    d00, d01, d02 = np.dot(v0, v0), np.dot(v0, v1), np.dot(v0, v2)
    d11, d12      = np.dot(v1, v1), np.dot(v1, v2)
    inv = 1.0 / (d00 * d11 - d01 * d01 + 1e-9)
    u = (d11 * d02 - d01 * d12) * inv
    v = (d00 * d12 - d01 * d02) * inv
    return (u >= 0) and (v >= 0) and (u + v < 1)


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------
def parse(row):
    shot = row["shot"]
    loc  = np.array(row["location"])
    freeze = shot.get("freeze_frame", [])

    keeper = None
    defenders_in_cone   = 0
    defender_pressure   = 0.0   # weighted: closer defenders count more

    for player in freeze:
        if not player.get("location"):
            continue
        p_loc = np.array(player["location"])
        pos   = player.get("position", {}).get("name", "")

        if pos == "Goalkeeper":
            keeper = p_loc
            continue

        if not player.get("teammate") and in_shot_cone(p_loc, loc):
            defenders_in_cone += 1
            defender_pressure += 1.0 / (dist(p_loc, loc) + 1e-9)

    # --- body part flags (3-way split, not 2-way) ---
    body = shot.get("body_part", {}).get("name", "")
    is_foot   = 1 if body in ("Right Foot", "Left Foot") else 0
    is_header = 1 if body == "Head" else 0

    # --- shot end height (z-axis when available) ---
    end_loc      = shot.get("end_location", [])
    shot_height  = float(end_loc[2]) if len(end_loc) > 2 else np.nan

    # --- goalkeeper features ---
    if keeper is not None:
        keeper_dist = dist(keeper)
        keeper_ang  = shot_angle(keeper)
        keeper_off_line = 1 if keeper_dist > 3.0 else 0
    else:
        keeper_dist     = 5.0
        keeper_ang      = shot_angle([115.0, 40.0])
        keeper_off_line = 0

    return {
        # location
        "distance_to_goal"      : dist(loc),
        "angle_to_goal"         : shot_angle(loc),
        # body part
        "is_foot"               : is_foot,
        "is_header"             : is_header,
        # context
        "under_pressure"        : 1 if row.get("under_pressure") else 0,
        "is_freekick"           : 1 if shot.get("type", {}).get("name") == "Free Kick" else 0,
        "is_open_play"          : 1 if shot.get("type", {}).get("name") == "Open Play" else 0,
        # shot quality
        "shot_height"           : shot_height,
        # goalkeeper
        "keeper_distance_to_goal": keeper_dist,
        "keeper_angle_coverage" : keeper_ang,
        "keeper_off_line"       : keeper_off_line,
        # defenders
        "defenders_in_cone"     : defenders_in_cone,
        "defender_pressure"     : defender_pressure,
        # data quality flag (used for weighting, not a feature)
        "has_freeze_frame"      : 1 if len(freeze) > 0 else 0,
        # label
        "is_goal"               : 1 if shot.get("outcome", {}).get("name") == "Goal" else 0,
    }





# ---------------------------------------------------------------------------
# Data loading  (caches RAW data so parse() can be changed freely)
# ---------------------------------------------------------------------------
def load_data(offline=False):
    # Offline re-runs the whole thing from the cache and touches no network.
    # The cache is the raw shot events, so parse() and everything below it can
    # be changed and re-run without re-downloading two thousand matches — which
    # is what this file was already structured for, and what makes fixing an
    # export bug a five-minute job rather than an afternoon.
    if offline:
        if not os.path.exists(CACHE_FILE):
            raise SystemExit(f"No {CACHE_FILE} to work from — run without --offline once.")
        shots = pd.read_pickle(CACHE_FILE)
        print(f"Offline: {len(shots)} cached shots from "
              f"{shots['match_id'].nunique()} matches.")
        print("Parsing features from raw data...")
        return pd.DataFrame([parse(r) for _, r in shots.iterrows()])

    # Imported here rather than at the top so a machine that only wants to
    # re-export from the cache does not need the StatsBomb client installed.
    from statsbombpy import sb

    print("Fetching match lists from StatsBomb (with safety delays)...")

    tournaments = [
        # --- Your Original 5 Seasons ---
        (11, 90), (12, 27), (2, 27), (7, 235), (9, 281),

        # --- The First Expansion (Internationals) ---
        (43, 106), (43, 3), (55, 43), (16, 4), (72, 30),

        # --- THE NEW MASSIVE EXPANSION ---
        # Historical La Liga (The Messi Data)
        (11, 42), (11, 4), (11, 1), (11, 2), (11, 27), (11, 26), (11, 25), (11, 24),

        # FA Women's Super League
        (37, 90), (37, 42), (37, 4),

        # Historical Champions League
        (16, 37), (16, 39), (16, 27), (16, 26), (16, 22),

        # Premier League (Arsenal Invincibles 03/04)
        (2, 44),

        # Indian Super League (2021/2022)
        (1238, 108)
    ]

    match_dfs = []
    for comp_id, season_id in tournaments:
        try:
            match_dfs.append(sb.matches(competition_id=comp_id, season_id=season_id))
            time.sleep(0.5)  # The magic anti-crash delay
        except Exception as e:
            print(f"Skipping {comp_id}/{season_id}: {e}")

    matches = pd.concat(match_dfs, ignore_index=True)
    all_match_ids = set(matches["match_id"])

    # 1. Load what we already have
    if os.path.exists(CACHE_FILE):
        print("Loading existing cache to check for missing data...")
        shots = pd.read_pickle(CACHE_FILE)
        cached_match_ids = set(shots["match_id"].unique())
    else:
        shots = pd.DataFrame()
        cached_match_ids = set()

    # 2. Find the difference
    missing_match_ids = list(all_match_ids - cached_match_ids)

    # 3. Download only what's missing
    if missing_match_ids:
        print(f"Found {len(missing_match_ids)} NEW matches to download. Skipping the rest!")
        new_shots_list = []

        for i, mid in enumerate(missing_match_ids):
            if i % 50 == 0:
                print(f"  {i}/{len(missing_match_ids)} new matches downloaded...")
            try:
                events = sb.events(match_id=mid, split=True, flatten_attrs=False)
                if "shots" in events:
                    new_shots_list.append(events["shots"])
                time.sleep(0.5)  # Delay for the actual downloads too
            except:
                pass

        if new_shots_list:
            new_shots = pd.concat(new_shots_list, ignore_index=True)
            shots = pd.concat([shots, new_shots], ignore_index=True)

        # Resave the updated master cache
        shots.to_pickle(CACHE_FILE)
        print(f"Appended new data and updated cache → {CACHE_FILE}")
    else:
        print("Cache is already 100% up to date!")

    # 4. Parse features
    print("Parsing features from raw data...")
    df = pd.DataFrame([parse(r) for _, r in shots.iterrows()])

    return df


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------
#
# Order matters. The model takes a bare 12-wide float array with no names
# attached, so this list is mirrored by hand in two other places — FEATURE_ORDER
# in cv/xg_bridge.py and in xg-sandbox/xg-model.js — and a reordering here is
# silent everywhere else. tests/test_xg_parity.py checks the other two against
# each other; nothing can check them against this one but a person.
FEATURES = [
    "distance_to_goal", "angle_to_goal",
    "is_foot", "is_header",
    "under_pressure", "is_open_play",
    "shot_height",
    "keeper_distance_to_goal", "keeper_angle_coverage", "keeper_off_line",
    "defenders_in_cone", "defender_pressure",
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(offline=False, show_plots=True, cross_validate=True):
    df = load_data(offline=offline)

    # Direct free kicks are dropped: they have their own physics and a wall in
    # front of them, and one model cannot hold both them and open play. The
    # comment here used to say penalties were dropped too. They are not — a
    # penalty is type "Penalty", not "Free Kick", so it survives this line and
    # reaches the model with is_open_play = 0.
    df = df[df["is_freekick"] == 0].copy()   # drop direct free-kick shots
    df = df.dropna(subset=["distance_to_goal", "angle_to_goal"])

    # Impute missing shot_height with median (z-axis not always present)
    df["shot_height"] = df["shot_height"].fillna(df["shot_height"].median())

    X = df[FEATURES].values.astype(np.float32)
    y = df["is_goal"].values
    # down-weight shots without freeze frames
    sample_weight = np.where(df["has_freeze_frame"] == 1, 1.0, 0.7)

    Xtr, Xte, ytr, yte, wtr, _ = train_test_split(
        X, y, sample_weight, test_size=0.15, random_state=50, stratify=y
    )

    # -----------------------------------------------------------------------
    # Model
    # -----------------------------------------------------------------------
    base_model = XGBClassifier(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=(y == 0).sum() / (y == 1).sum(),
        eval_metric="auc",
        random_state=50,
        verbosity=0,
    )

    # Isotonic calibration so predicted probabilities are meaningful as xG values
    model = CalibratedClassifierCV(base_model, method="isotonic", cv=5)
    model.fit(Xtr, ytr, sample_weight=wtr)

    preds = model.predict_proba(Xte)[:, 1]
    print(f"\nTest  →  AUC: {roc_auc_score(yte, preds):.4f}   "
          f"Brier: {brier_score_loss(yte, preds):.4f}")

    # The one line that would have caught the export bug. A set of xG values
    # that does not add up to the goals actually scored is not xG, whatever
    # else it is, and this says so in one number.
    print(f"Sum   →  predicted {preds.sum():.1f} goals over {len(yte)} shots, "
          f"actual {yte.sum()}  (mean xG {preds.mean():.3f})")

    if cross_validate:
        # 5-fold cross-validated AUC on full dataset
        cv      = StratifiedKFold(n_splits=5, shuffle=True, random_state=50)
        cv_aucs = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
        print(f"CV    →  AUC: {cv_aucs.mean():.4f} ± {cv_aucs.std():.4f}")

    # -----------------------------------------------------------------------
    # Calibration curve
    # -----------------------------------------------------------------------
    prob_true, prob_pred = calibration_curve(yte, preds, n_bins=10)
    plt.figure(figsize=(6, 5))
    plt.plot(prob_pred, prob_true, marker="o", label="Model")
    plt.plot([0, 1], [0, 1], "--", color="gray", label="Perfect")
    plt.xlabel("Predicted xG")
    plt.ylabel("Actual conversion rate")
    plt.title("Calibration Curve")
    plt.legend()
    plt.tight_layout()
    plt.savefig("calibration_curve.png", dpi=150)
    if show_plots:
        plt.show()

    # -----------------------------------------------------------------------
    # Feature importance (Replaces SHAP to avoid the crash)
    # -----------------------------------------------------------------------
    from xgboost import plot_importance

    xgb_estimator = model.calibrated_classifiers_[0].estimator

    # Temporarily set feature names so the plot is readable
    xgb_estimator.get_booster().feature_names = FEATURES

    plt.figure(figsize=(10, 6))
    plot_importance(xgb_estimator, importance_type="weight",
                    max_num_features=len(FEATURES),
                    title="XGBoost Feature Importance", show_values=False)
    plt.tight_layout()
    plt.savefig("feature_importance.png", dpi=150)
    if show_plots:
        plt.show()

    # THE FIX: Reset feature names back to default so ONNX doesn't crash!
    xgb_estimator.get_booster().feature_names = None

    # -----------------------------------------------------------------------
    # ONNX export
    # -----------------------------------------------------------------------
    #
    # The whole calibrated model — not the classifier inside it.
    #
    # Until 2026-08-06 this line exported `xgb_estimator`, which is one of the
    # five fold estimators CalibratedClassifierCV fitted, with the isotonic step
    # left behind in this process. Every figure printed above was measured on
    # `model`; every figure the app has ever shown came out of that estimator.
    # They are not the same model, and the difference is not subtle: the base is
    # deliberately reweighted by scale_pos_weight (about 9, since roughly one
    # shot in ten is a goal) and the calibration exists to undo exactly that.
    # Exported raw, it read about six times high near goal — 0.69 for a clear
    # shot from 14 metres, 0.89 for a penalty.
    #
    # onnxmltools.convert_xgboost cannot see a calibrated wrapper, which is
    # presumably how it happened. Registering the XGBoost converter with
    # skl2onnx lets convert_sklearn walk the whole estimator and emit the
    # isotonic step with it. zipmap=False keeps `probabilities` a plain float
    # tensor, which is the only shape onnxruntime-web can read in the browser.
    from skl2onnx import convert_sklearn, update_registered_converter
    from skl2onnx.common.data_types import FloatTensorType as SklFloatTensorType
    from skl2onnx.common.shape_calculator import (
        calculate_linear_classifier_output_shapes,
    )
    from onnxmltools.convert.xgboost.operator_converters.XGBoost import (
        convert_xgboost as convert_xgboost_operator,
    )

    update_registered_converter(
        XGBClassifier, "XGBoostXGBClassifier",
        calculate_linear_classifier_output_shapes, convert_xgboost_operator,
        options={"nocl": [True, False], "zipmap": [True, False, "columns"]},
    )

    onnx_model = convert_sklearn(
        model,
        initial_types=[("float_input", SklFloatTensorType([None, len(FEATURES)]))],
        options={id(model): {"zipmap": False}},
        target_opset={"": 17, "ai.onnx.ml": 3},
    )
    with open(ONNX_FILE, "wb") as f:
        f.write(onnx_model.SerializeToString())
    print(f"\nSaved ONNX model → {ONNX_FILE}")

    # Check what was written, not what was fitted. The bug this replaces was
    # invisible precisely because nobody ever ran the exported file.
    verify_export(model, Xte[:2000])

    return model


def verify_export(model, sample):
    """Run the file that was just written and compare it to the fitted model.

    Not a formality. The bug this replaces was invisible for as long as it was
    because the exported file was never once run against the model it came from
    — every figure anyone checked came from `model`, and every figure the app
    showed came from the file.

    The two do not agree to the last bit, and cannot. Isotonic calibration is a
    step function, the trees are exported with float32 thresholds, and a shot
    whose raw margin lands within rounding distance of a step edge comes out on
    the other plateau. Measured over 8001 held-out shots: identical to 3.7e-10
    for 98% of them, over 0.001 for 0.7%, and one shot at 0.0129. The mean
    agrees to five decimal places, so nothing that sums a match's worth of shots
    can tell the difference.

    So the thresholds are set to catch a real regression — a dropped calibration
    step moves the mean by ~0.35, an ordering or feature mistake moves
    everything — and not the plateau edges.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        print("onnxruntime not installed — exported model NOT verified.")
        return

    session = ort.InferenceSession(ONNX_FILE, providers=["CPUExecutionProvider"])
    outputs = session.run(None, {"float_input": sample.astype(np.float32)})
    exported = next(
        np.asarray(o)[:, 1] for o in outputs
        if np.asarray(o).ndim == 2 and np.asarray(o).shape[1] >= 2
    )
    fitted = model.predict_proba(sample)[:, 1]
    gap = np.abs(exported - fitted)

    print(f"Export check → mean {gap.mean():.2e}, p99 {np.percentile(gap, 99):.2e}, "
          f"worst {gap.max():.4f} over {len(gap)} shots")
    print(f"              mean xG: file {exported.mean():.4f}, "
          f"fitted {fitted.mean():.4f}")

    if gap.mean() > 1e-3 or gap.max() > 0.05:
        raise SystemExit("The exported model does not match the fitted one.")


if __name__ == "__main__":
    import sys

    # Every print below has an arrow in it, and a Windows console defaults to
    # cp1252, which cannot encode one. Without this the script trains for two
    # minutes and then dies on its first line of output.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    # --offline re-runs everything from the cached shots and touches no
    # network, which is what re-exporting after a bug fix needs.
    offline = "--offline" in sys.argv
    fast = "--fast" in sys.argv          # skip the 25-fit cross-validation
    if offline:
        plt.switch_backend("Agg")        # no window to block on a headless run

    main(offline=offline, show_plots=not offline, cross_validate=not fast)
