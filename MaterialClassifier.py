"""
Material classifier — runtime inference module
==============================================
Loads a Random Forest (Phase A) classifier at import time. Provides
classify_trial(records, baseline_res_k) which returns (label, prob_dict)
or (None, None) if the model is unavailable.

Phase B (1D-CNN) loading is reserved but not implemented — adding it
later requires only the _load() helper to prefer the CNN file over RF.

Per Update_2026-05-09_Material Classifier Plan.md §4.
"""
import os

import joblib
import numpy as np

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_BASE_DIR, "Model")

FEATURE_NAMES = ["delta_pos_max", "res_drop_pct", "f_peak", "rise_ms", "stiffness_proxy"]

rf_model  = None
rf_scaler = None
classes_  = None


def _load() -> bool:
    """Load RF + scaler. Silent if files missing."""
    global rf_model, rf_scaler, classes_
    rf_path     = os.path.join(_MODEL_DIR, "material_rf.pkl")
    scaler_path = os.path.join(_MODEL_DIR, "scaler_mat_rf.pkl")
    if not (os.path.exists(rf_path) and os.path.exists(scaler_path)):
        print("[MaterialClassifier] No artefacts found; classification disabled.")
        return False
    try:
        rf_model  = joblib.load(rf_path)
        rf_scaler = joblib.load(scaler_path)
        classes_  = list(rf_model.classes_)
        print(f"[MaterialClassifier] RF loaded — classes={classes_}")
        return True
    except Exception as e:
        print(f"[MaterialClassifier] Load failed: {e}")
        return False


_load()


def _features_from_records(records, baseline_res_k):
    """Compute the 5 scalar features from a single grip's per-packet records."""
    if not records:
        return None
    press = [r for r in records if r["is_press"] == 1]
    if len(press) < 5:
        return None

    min_res_k    = min(r["res"] for r in press) / 1000.0
    res_drop_pct = (baseline_res_k - min_res_k) / max(baseline_res_k, 1e-6)

    pos_at_contact = press[0]["pos"]
    delta_pos_max  = max(abs(r["pos"] - pos_at_contact) for r in press)

    forces = [r["pred_force_n"] for r in press]
    f_peak = max(forces) if forces else 0.0

    if f_peak > 0.1:
        t_contact = press[0]["t_ms"]
        target    = 0.9 * f_peak
        rise_ms = next(
            (r["t_ms"] - t_contact for r in press if r["pred_force_n"] >= target),
            0.0,
        )
    else:
        rise_ms = 0.0

    stiffness = f_peak / rise_ms if rise_ms > 0 else 0.0

    return np.array(
        [[delta_pos_max, res_drop_pct, f_peak, rise_ms, stiffness]],
        dtype=np.float32,
    )


def classify_trial(records, baseline_res_k):
    """
    records: list of dicts with keys t_ms, pos, res, is_press, pred_force_n
    baseline_res_k: pre-grip resistance baseline in kΩ

    Returns: (label, {class_name: prob}) or (None, None)
    """
    if rf_model is None:
        return None, None
    feats = _features_from_records(records, baseline_res_k)
    if feats is None:
        return None, None
    Xs    = rf_scaler.transform(feats)
    label = rf_model.predict(Xs)[0]
    probs = rf_model.predict_proba(Xs)[0]
    return str(label), {c: float(p) for c, p in zip(rf_model.classes_, probs)}
