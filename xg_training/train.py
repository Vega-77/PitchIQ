"""Fit the calibrated xG classifier and draw its diagnostic plots.

`prepare` filters the parsed shots and returns the feature matrix, labels and
sample weights; `fit` trains an XGBoost classifier wrapped in isotonic
calibration, so its probabilities read as xG, and prints held-out AUC, Brier
score and predicted-against-actual goals; `plot_diagnostics` writes the
calibration curve and feature importance to docs/xg/.
"""

import os

import matplotlib.pyplot as plt
import numpy as np
from sklearn.calibration import CalibratedClassifierCV, calibration_curve
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_score, train_test_split
from xgboost import XGBClassifier

from features import FEATURES

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLOT_DIR = os.path.join(_REPO, 'docs', 'xg')


def prepare(df) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(features as float32, is_goal labels, sample weights) for the shots the model covers."""
    # Direct free kicks are dropped: they have their own physics and a wall in
    # front of them, and one model cannot hold both them and open play.
    # Penalties are type "Penalty", not "Free Kick", so they survive this line
    # and reach the model with is_open_play = 0.
    df = df[df['is_freekick'] == 0].copy()  # drop direct free-kick shots
    df = df.dropna(subset=['distance_to_goal', 'angle_to_goal'])

    # Impute missing shot_height with median (z-axis not always present)
    df['shot_height'] = df['shot_height'].fillna(df['shot_height'].median())

    X = df[FEATURES].values.astype(np.float32)
    y = df['is_goal'].values
    # down-weight shots without freeze frames
    sample_weight = np.where(df['has_freeze_frame'] == 1, 1.0, 0.7)
    return X, y, sample_weight


def fit(X, y, sample_weight, cross_validate=True) -> tuple:
    """(calibrated model, held-out features, held-out labels, held-out xG) from a 85/15 split."""
    Xtr, Xte, ytr, yte, wtr, _ = train_test_split(
        X, y, sample_weight, test_size=0.15, random_state=50, stratify=y
    )

    base_model = XGBClassifier(
        n_estimators=500,
        max_depth=5,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.8,
        scale_pos_weight=(y == 0).sum() / (y == 1).sum(),
        eval_metric='auc',
        random_state=50,
        verbosity=0,
    )

    # Isotonic calibration so predicted probabilities are meaningful as xG values
    model = CalibratedClassifierCV(base_model, method='isotonic', cv=5)
    model.fit(Xtr, ytr, sample_weight=wtr)

    preds = model.predict_proba(Xte)[:, 1]
    print(
        f'\nTest  →  AUC: {roc_auc_score(yte, preds):.4f}   '
        f'Brier: {brier_score_loss(yte, preds):.4f}'
    )

    # A set of xG values that does not add up to the goals actually scored is
    # not xG, whatever else it is, and this says so in one number.
    print(
        f'Sum   →  predicted {preds.sum():.1f} goals over {len(yte)} shots, '
        f'actual {yte.sum()}  (mean xG {preds.mean():.3f})'
    )

    if cross_validate:
        # 5-fold cross-validated AUC on full dataset
        cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=50)
        cv_aucs = cross_val_score(model, X, y, cv=cv, scoring='roc_auc')
        print(f'CV    →  AUC: {cv_aucs.mean():.4f} ± {cv_aucs.std():.4f}')

    return model, Xte, yte, preds


def plot_diagnostics(model, yte, preds, show_plots=True) -> None:
    """Write the calibration curve and the feature importance plot to PLOT_DIR."""
    prob_true, prob_pred = calibration_curve(yte, preds, n_bins=10)
    plt.figure(figsize=(6, 5))
    plt.plot(prob_pred, prob_true, marker='o', label='Model')
    plt.plot([0, 1], [0, 1], '--', color='gray', label='Perfect')
    plt.xlabel('Predicted xG')
    plt.ylabel('Actual conversion rate')
    plt.title('Calibration Curve')
    plt.legend()
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, 'calibration_curve.png'), dpi=150)
    if show_plots:
        plt.show()

    # Feature importance from the booster itself; SHAP crashed here.
    from xgboost import plot_importance

    xgb_estimator = model.calibrated_classifiers_[0].estimator

    # Temporarily set feature names so the plot is readable
    xgb_estimator.get_booster().feature_names = FEATURES

    plt.figure(figsize=(10, 6))
    plot_importance(
        xgb_estimator,
        importance_type='weight',
        max_num_features=len(FEATURES),
        title='XGBoost Feature Importance',
        show_values=False,
    )
    plt.tight_layout()
    plt.savefig(os.path.join(PLOT_DIR, 'feature_importance.png'), dpi=150)
    if show_plots:
        plt.show()

    # Reset the names, or the ONNX export fails on them.
    xgb_estimator.get_booster().feature_names = None
