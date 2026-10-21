"""Retrain the xG model from StatsBomb open data and export it where the pipeline loads it.

Run from this folder: `python main.py` downloads any missing matches, trains,
writes the plots to docs/xg/ and the model to `pitchiq/<MODEL_VERSION>.onnx`,
then checks the written file. `--offline` works from the cached shots alone and
draws no windows; `--fast` skips the five-fold cross-validation. See README.md.
"""

import sys

import matplotlib.pyplot as plt

from data import load_data
from export import export_onnx, verify_export
from train import fit, plot_diagnostics, prepare


def main(offline=False, show_plots=True, cross_validate=True) -> object:
    """Load, train, plot, export and verify; returns the fitted model."""
    df = load_data(offline=offline)
    X, y, sample_weight = prepare(df)
    model, Xte, yte, preds = fit(X, y, sample_weight, cross_validate=cross_validate)
    plot_diagnostics(model, yte, preds, show_plots=show_plots)
    export_onnx(model)

    # Check what was written, not what was fitted.
    verify_export(model, Xte[:2000])
    return model


if __name__ == '__main__':
    # Every print has an arrow in it, and a Windows console defaults to cp1252,
    # which cannot encode one.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    offline = '--offline' in sys.argv
    fast = '--fast' in sys.argv  # skip the 25-fit cross-validation
    if offline:
        plt.switch_backend('Agg')  # no window to block on a headless run

    main(offline=offline, show_plots=not offline, cross_validate=not fast)
