"""
Run this once to re-export xg_model.onnx with zipmap=False so that
the 'probabilities' output is a plain float32 tensor instead of a
sequence-of-dicts (which ORT.js cannot read).

Requirements: pip install scikit-learn onnxmltools skl2onnx
"""
import onnx
from onnx import numpy_helper, TensorProto
from skl2onnx.helpers.onnx_helper import select_model_inputs_outputs
import sys

# --- patch the existing ONNX graph ---
# We add a Gather node after the ZipMap to extract column 1 (p_goal)
# from the sequence. Easier: just reload the model and re-export if
# you have the original sklearn model object.
#
# If you have the original model saved as a .pkl, use this approach:
try:
    import pickle, numpy as np
    from skl2onnx import convert_sklearn
    from skl2onnx.common.data_types import FloatTensorType

    with open('xg_model.pkl', 'rb') as f:
        model = pickle.load(f)

    initial_type = [('float_input', FloatTensorType([None, 10]))]
    options = {type(model): {'zipmap': False}}
    onx = convert_sklearn(model, initial_types=initial_type, options=options)

    with open('xg_model.onnx', 'wb') as f:
        f.write(onx.SerializeToString())

    print("Re-exported xg_model.onnx with zipmap=False. probabilities is now a float32 tensor.")

except FileNotFoundError:
    print("xg_model.pkl not found.")
    print()
    print("Alternative: patch the existing ONNX to remove the ZipMap node.")

    model = onnx.load('xg_model.onnx')
    print("Output names:", [o.name for o in model.graph.output])
    print("Nodes:", [(n.op_type, list(n.input), list(n.output)) for n in model.graph.node])
    print()
    print("Run your original training code and add options={'zipmap': False} to convert_sklearn().")
