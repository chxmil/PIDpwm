"""
Material classifier — runtime inference module (Phase A-prime 1D-CNN on PID-grip data)
=====================================================================================
Loads a 1D-CNN trained on the PID-grip per-packet trace. Provides
classify_pid(records, baseline_res_k) which returns (label, prob_dict)
or (None, None) if the model is unavailable or the post-contact window
yields fewer than 40 non-empty 50 ms bins.

Input contract — `records` is the list returned by ModelInclude's Stage 4
(`trial_records`). Each element must have:
    t_ms, pos, res, is_press, pred_force_n, shifted_cond

Same window-extraction logic as `Code Store/train_material_cnn_pid.py`
(post-2026-05-13 bin-skip fix): groupby 50 ms bins by t_ms relative to
first is_press=1 row, take the first 40 NON-EMPTY bins. Five features per
bin: shifted_cond, delta_pos, d_cond_dt, d_dpos_dt, res_norm.

Phase A-prime is independent of Phase B (probe-based 1D-CNN at
MaterialCNNClassifier.py) — both may coexist.

Per DevLog_2026-05-13_Phase A-prime CNN on PID-grip data §8.
"""
import os

import joblib
import numpy as np

_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_BASE_DIR, "Model")

FEATURE_NAMES = ["shifted_cond", "delta_pos", "d_cond_dt", "d_dpos_dt", "res_norm"]
WINDOW_LEN    = 40
BIN_MS        = 50              # 20 Hz

cnn_model  = None
cnn_scaler = None
classes_   = None


def _load() -> bool:
    """Load CNN-PID + per-channel scaler. Silent if files missing."""
    global cnn_model, cnn_scaler, classes_
    cnn_path    = os.path.join(_MODEL_DIR, "material_cnn_pid.keras")
    scaler_path = os.path.join(_MODEL_DIR, "scaler_mat_cnn_pid.pkl")
    if not (os.path.exists(cnn_path) and os.path.exists(scaler_path)):
        print("[MaterialPIDCNNClassifier] No artefacts found; CNN-PID classification disabled.")
        return False
    try:
        from tensorflow.keras.models import load_model
        cnn_model  = load_model(cnn_path)
        bundle     = joblib.load(scaler_path)
        cnn_scaler = bundle["scaler"]
        classes_   = list(bundle["classes"])
        print(f"[MaterialPIDCNNClassifier] CNN-PID loaded — classes={classes_}, window={WINDOW_LEN}")
        return True
    except Exception as e:
        print(f"[MaterialPIDCNNClassifier] Load failed: {e}")
        return False


_load()


def _window_from_records(records):
    """Build a (40, 5) float32 array from trial_records.

    Returns None if no post-contact rows, or fewer than 40 non-empty bins.
    """
    if not records:
        return None
    post = [r for r in records if r.get("is_press", 0) == 1]
    if len(post) < 5:
        return None

    t0             = float(post[0]["t_ms"])
    pos_at_contact = float(post[0]["pos"])

    # Group by 50 ms bin
    bins = {}
    for r in post:
        rel = float(r["t_ms"]) - t0
        bid = int(rel // BIN_MS)
        if bid < 0:
            continue
        bins.setdefault(bid, []).append(r)

    if not bins:
        return None

    rows = []
    prev_cond = None
    prev_dpos = None
    for bid in sorted(bins.keys()):
        if len(rows) >= WINDOW_LEN:
            break
        g = bins[bid]
        mean_cond = float(np.mean([rr["shifted_cond"] for rr in g]))
        mean_pos  = float(np.mean([rr["pos"]          for rr in g]))
        mean_res  = float(np.mean([rr["res"]          for rr in g])) / 1000.0
        delta_pos = mean_pos - pos_at_contact
        d_cond_dt = (mean_cond - prev_cond) / (BIN_MS / 1000.0) if prev_cond is not None else 0.0
        d_dpos_dt = (delta_pos - prev_dpos) / (BIN_MS / 1000.0) if prev_dpos is not None else 0.0
        res_norm  = 1.0  # placeholder, set below
        rows.append([mean_cond, delta_pos, d_cond_dt, d_dpos_dt, mean_res])
        prev_cond = mean_cond
        prev_dpos = delta_pos

    if len(rows) < WINDOW_LEN:
        return None

    arr = np.array(rows, dtype=np.float32)
    # res_norm column: normalise mean_res by baseline_res_k inside classify_pid()
    return arr


def classify_pid(records, baseline_res_k):
    """
    records: list of dicts from ModelInclude Stage 4 (trial_records)
             with keys t_ms, pos, res, is_press, pred_force_n, shifted_cond
    baseline_res_k: pre-grip resistance baseline in kΩ

    Returns: (label, {class_name: prob}) or (None, None)
    """
    if cnn_model is None or cnn_scaler is None:
        return None, None
    win = _window_from_records(records)
    if win is None:
        return None, None

    # Convert column 4 (currently holds mean_res in kΩ) to res_norm
    if baseline_res_k > 0:
        win[:, 4] = np.clip(win[:, 4] / baseline_res_k, 0.0, 1.5)
    else:
        win[:, 4] = 1.0

    flat   = win.reshape(-1, win.shape[-1])
    scaled = cnn_scaler.transform(flat).reshape(1, WINDOW_LEN, len(FEATURE_NAMES))
    probs  = np.array(cnn_model(scaled, training=False)).flatten()
    idx    = int(np.argmax(probs))
    label  = classes_[idx] if classes_ else str(idx)
    return str(label), {c: float(p) for c, p in zip(classes_ or [], probs)}
