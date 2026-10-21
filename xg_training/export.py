"""Write the whole calibrated model to ONNX where the pipeline loads it, then check it.

`export_onnx` converts the calibrated wrapper, isotonic step included, to
`pitchiq/xg_model.MODEL_PATH`; `verify_export` runs the written file on
held-out shots and refuses a file that disagrees with the fitted model.
"""

import numpy as np
from xgboost import XGBClassifier

from features import FEATURES
from pitchiq.xg_model import MODEL_PATH

ONNX_FILE = str(MODEL_PATH)


def export_onnx(model) -> None:
    """Convert the calibrated `model` to ONNX at ONNX_FILE, output `probabilities` as float."""
    # The whole calibrated model, not the classifier inside it: see README.md.
    # Registering the XGBoost converter with skl2onnx lets convert_sklearn walk
    # the whole estimator and emit the isotonic step with it. zipmap=False keeps
    # `probabilities` a plain float tensor, the shape pitchiq/xg_bridge.py reads.
    from onnxmltools.convert.xgboost.operator_converters.XGBoost import (
        convert_xgboost as convert_xgboost_operator,
    )
    from skl2onnx import convert_sklearn, update_registered_converter
    from skl2onnx.common.data_types import FloatTensorType as SklFloatTensorType
    from skl2onnx.common.shape_calculator import (
        calculate_linear_classifier_output_shapes,
    )

    update_registered_converter(
        XGBClassifier,
        'XGBoostXGBClassifier',
        calculate_linear_classifier_output_shapes,
        convert_xgboost_operator,
        options={'nocl': [True, False], 'zipmap': [True, False, 'columns']},
    )

    onnx_model = convert_sklearn(
        model,
        initial_types=[('float_input', SklFloatTensorType([None, len(FEATURES)]))],
        options={id(model): {'zipmap': False}},
        target_opset={'': 17, 'ai.onnx.ml': 3},
    )
    with open(ONNX_FILE, 'wb') as f:
        f.write(onnx_model.SerializeToString())
    print(f'\nSaved ONNX model → {ONNX_FILE}')


def verify_export(model, sample) -> None:
    """Run the file that was just written and compare it to the fitted model.

    The two cannot agree to the last bit: isotonic calibration is a step
    function and the trees are exported with float32 thresholds, so a shot near
    a step edge lands on the other plateau. The thresholds catch a real
    regression (a dropped calibration step moves the mean by ~0.35) and not the
    plateau edges. Measurements: README.md.
    """
    try:
        import onnxruntime as ort
    except ImportError:
        print('onnxruntime not installed — exported model NOT verified.')
        return

    session = ort.InferenceSession(ONNX_FILE, providers=['CPUExecutionProvider'])
    outputs = session.run(None, {'float_input': sample.astype(np.float32)})
    exported = next(
        np.asarray(o)[:, 1]
        for o in outputs
        if np.asarray(o).ndim == 2 and np.asarray(o).shape[1] >= 2
    )
    fitted = model.predict_proba(sample)[:, 1]
    gap = np.abs(exported - fitted)

    print(
        f'Export check → mean {gap.mean():.2e}, p99 {np.percentile(gap, 99):.2e}, '
        f'worst {gap.max():.4f} over {len(gap)} shots'
    )
    print(f'              mean xG: file {exported.mean():.4f}, fitted {fitted.mean():.4f}')

    if gap.mean() > 1e-3 or gap.max() > 0.05:
        raise SystemExit('The exported model does not match the fitted one.')
