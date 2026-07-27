import os
import numpy as np
import pandas as pd
from statsbombpy import sb
from xgboost import XGBClassifier
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from sklearn.metrics import roc_auc_score, brier_score_loss
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
import matplotlib.pyplot as plt
import time
import onnxmltools
from onnxmltools.convert.common.data_types import FloatTensorType

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
ONNX_FILE  = "xg_model6.onnx"

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
def load_data():
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
# Main
# ---------------------------------------------------------------------------
df = load_data()

# Drop penalties — near-deterministic, fixed location; train a separate model if needed
df = df[df["is_freekick"] == 0].copy()   # also drop direct free-kick shots
df = df.dropna(subset=["distance_to_goal", "angle_to_goal"])

# Impute missing shot_height with median (z-axis not always present)
df["shot_height"] = df["shot_height"].fillna(df["shot_height"].median())

FEATURES = [
    "distance_to_goal", "angle_to_goal",
    "is_foot", "is_header",
    "under_pressure", "is_open_play",
    "shot_height",
    "keeper_distance_to_goal", "keeper_angle_coverage", "keeper_off_line",
    "defenders_in_cone", "defender_pressure",
]

X = df[FEATURES].values.astype(np.float32)
y = df["is_goal"].values
sample_weight = np.where(df["has_freeze_frame"] == 1, 1.0, 0.7)  # down-weight shots without freeze frames

Xtr, Xte, ytr, yte, wtr, _ = train_test_split(
    X, y, sample_weight, test_size=0.15, random_state=50, stratify=y
)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
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
print(f"\nTest  →  AUC: {roc_auc_score(yte, preds):.4f}   Brier: {brier_score_loss(yte, preds):.4f}")

# 5-fold cross-validated AUC on full dataset
cv      = StratifiedKFold(n_splits=5, shuffle=True, random_state=50)
cv_aucs = cross_val_score(model, X, y, cv=cv, scoring="roc_auc")
print(f"CV    →  AUC: {cv_aucs.mean():.4f} ± {cv_aucs.std():.4f}")

# ---------------------------------------------------------------------------
# Calibration curve
# ---------------------------------------------------------------------------
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
plt.show()

# ---------------------------------------------------------------------------
# Feature importance (Replaces SHAP to avoid the crash)
# ---------------------------------------------------------------------------
from xgboost import plot_importance

# Extract the underlying XGBoost model
xgb_estimator = model.calibrated_classifiers_[0].estimator

# Temporarily set feature names so the plot is readable
xgb_estimator.get_booster().feature_names = FEATURES

plt.figure(figsize=(10, 6))
plot_importance(xgb_estimator, importance_type="weight", max_num_features=len(FEATURES),
                title="XGBoost Feature Importance", show_values=False)
plt.tight_layout()
plt.savefig("feature_importance.png", dpi=150)
plt.show()

# THE FIX: Reset feature names back to default so ONNX doesn't crash!
xgb_estimator.get_booster().feature_names = None

# ---------------------------------------------------------------------------
# ONNX export
# ---------------------------------------------------------------------------
onnx_model = onnxmltools.convert_xgboost(
    xgb_estimator,
    initial_types=[("float_input", FloatTensorType([None, len(FEATURES)]))]
)
with open(ONNX_FILE, "wb") as f:
    f.write(onnx_model.SerializeToString())
print(f"\nSaved ONNX model → {ONNX_FILE}")