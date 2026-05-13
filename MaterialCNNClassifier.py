"""
Material classifier — runtime inference module (Phase B 1D-CNN)
================================================================
Loads a 1D-CNN classifier + StandardScaler at import time. Provides
classify_probe(probe_records, baseline_res_k) which returns
(label, prob_dict) or (None, None) if the model is unavailable or the
probe window is too short.

Input contract — `probe_records` is the list returned by ModelInclude's
Stage 2.5 (Update_2026-05-13_1D-CNN Phase B Plan §1.1). Each element is
a dict with keys: shifted_cond, delta_pos, d_cond_dt, d_dpos_dt, res_norm.
The classifier expects 40 timesteps; shorter sequences return (None, None).

Per Update_2026-05-13_1D-CNN Phase B Plan §1.5.
"""
import os

import joblib
import numpy as np

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_BASE_DIR, "Model")

FEATURE_NAMES = ["shifted_cond", "delta_pos", "d_cond_dt", "d_dpos_dt", "res_norm"]
WINDOW_LEN    = 40

cnn_model  = None
cnn_scaler = None
classes_   = None


def _load() -> bool:
    """Load CNN + per-channel scaler. Silent if files missing."""
    global cnn_model, cnn_scaler, classes_
    cnn_path    = os.path.join(_MODEL_DIR, "material_cnn.keras")
    scaler_path = os.path.join(_MODEL_DIR, "scaler_mat_cnn.pkl")
    if not (os.path.exists(cnn_path) and os.path.exists(scaler_path)):
        print("[MaterialCNNClassifier] No artefacts found; CNN classification disabled.")
        return False
    try:
        from tensorflow.keras.models import load_model
        cnn_model  = load_model(cnn_path)
        bundle     = joblib.load(scaler_path)
        cnn_scaler = bundle["scaler"]
        classes_   = list(bundle["classes"])
        print(f"[MaterialCNNClassifier] CNN loaded — classes={classes_}, window={WINDOW_LEN}")
        return True
    except Exception as e:
        print(f"[MaterialCNNClassifier] Load failed: {e}")
        return False


_load()


def _window_from_records(probe_records):
    """Build a (40, 5) float32 array from probe_records. Returns None if too short."""
    if not probe_records or len(probe_records) < WINDOW_LEN:
        return None
    rows = probe_records[:WINDOW_LEN]
    arr  = np.array(
        [[r["shifted_cond"], r["delta_pos"], r["d_cond_dt"], r["d_dpos_dt"], r["res_norm"]]
         for r in rows],
        dtype=np.float32,
    )
    return arr


def classify_probe(probe_records, baseline_res_k):
    """
    probe_records: list of dicts from ModelInclude Stage 2.5
    baseline_res_k: pre-grip resistance baseline in kΩ (unused at inference;
                    accepted so the call signature matches MaterialClassifier).

    Returns: (label, {class_name: prob}) or (None, None)
    """
    del baseline_res_k  # reserved for future channel additions
    if cnn_model is None or cnn_scaler is None:
        return None, None
    win = _window_from_records(probe_records)
    if win is None:
        return None, None
    flat   = win.reshape(-1, win.shape[-1])           # (40, 5)
    scaled = cnn_scaler.transform(flat).reshape(1, WINDOW_LEN, len(FEATURE_NAMES))
    probs  = np.array(cnn_model(scaled, training=False)).flatten()
    idx    = int(np.argmax(probs))
    label  = classes_[idx] if classes_ else str(idx)
    return str(label), {c: float(p) for c, p in zip(classes_ or [], probs)}
