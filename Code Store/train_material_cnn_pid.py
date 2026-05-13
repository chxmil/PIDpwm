"""
Phase A-prime — 1D-CNN on PID-Grip Data (v1 — 2026-05-13)
=========================================================
A deep-learning alternative to Phase A Random Forest (v4), trained on the
SAME 203-trial PID-grip corpus auto-discovered from data_logs/datasets/.

This is NOT Phase B. Phase B trains on probe-phase data
(data_logs/datasets/probe/) — see train_material_cnn.py for that.

Goal: head-to-head A/B vs RF v4 (CV 0.838 ± 0.018, macro F1 0.842) on the
exact same dataset. If this CNN beats RF v4, propose swapping the runtime
material classifier in a follow-up Update Report.

Inputs : data_logs/datasets/*.csv  (top level only; bin/ and probe/ skipped)
Outputs: Model/material_cnn_pid.keras
         Model/scaler_mat_cnn_pid.pkl   (StandardScaler + classes + window_len)

Window extraction
-----------------
Per trial (grouped by loop_index):
  1. Drop if <30 packets total OR <5 pre OR <5 post (matches RF v4 filter).
  2. Drop if max(pred_force_n) < 1.5 N (failed grip).
  3. Drop if baseline<1000 kΩ AND f_peak<5 N (sensor-saturation guard).
  4. Compute baseline_res_k = median(resistance/1000) of pre-contact rows.
  5. Bin post-contact packets into 50 ms windows (20 Hz); take the FIRST 40.
  6. Per bin compute the 5 features:
        shifted_cond, delta_pos, d_cond_dt, d_dpos_dt, res_norm
     Same definition as Phase B (Update_2026-05-13 §1.2).

Run offline once; do not call from runtime.
"""
import os
import sys
from collections import Counter

import joblib
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold

BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR   = os.path.join(BASE, "data_logs", "datasets")
MODEL_DIR  = os.path.join(BASE, "Model")

LABEL_MAP     = {"hard": "Hard", "medium": "Medium", "soft": "Soft"}
FEATURE_NAMES = ["shifted_cond", "delta_pos", "d_cond_dt", "d_dpos_dt", "res_norm"]
WINDOW_LEN    = 40
BIN_MS        = 50              # 20 Hz
REQUIRED_COLS = {"loop_index", "is_press", "resistance", "pos_deg", "pred_force_n",
                 "t_ms", "material", "shifted_cond"}

MIN_F_PEAK_N             = 1.5
MIN_BASELINE_KOHM        = 1000.0
LOW_BASELINE_F_THRESHOLD = 5.0


def _discover_sources():
    print(f"\n  Scanning {DATA_DIR}")
    out = []
    for fn in sorted(os.listdir(DATA_DIR)):
        full = os.path.join(DATA_DIR, fn)
        if not os.path.isfile(full) or not fn.lower().endswith(".csv"):
            continue
        try:
            head = pd.read_csv(full, nrows=5)
        except Exception as e:
            print(f"    [skip] {fn:40s}  unreadable ({e})")
            continue
        if not REQUIRED_COLS.issubset(head.columns):
            missing = REQUIRED_COLS - set(head.columns)
            print(f"    [skip] {fn:40s}  not per-packet schema (missing: {sorted(missing)})")
            continue
        mats = [m for m in head["material"].dropna().astype(str).str.lower().unique() if m]
        label = LABEL_MAP.get(mats[0]) if mats else None
        if label is None:
            print(f"    [skip] {fn:40s}  no `material` label")
            continue
        print(f"    [keep] {fn:40s}  -> {label}")
        out.append((label, fn))
    return out


def _bin_mean(post_df: pd.DataFrame, baseline_res_k: float, pos_at_contact: float):
    """Bin post-contact rows into 50 ms windows and emit feature rows.

    Returns a (T, 5) ndarray (T may be < WINDOW_LEN).
    """
    t0     = float(post_df["t_ms"].iloc[0])
    rel    = post_df["t_ms"].astype(float).values - t0
    bin_id = (rel // BIN_MS).astype(int)
    post_df = post_df.assign(_bin=bin_id)
    rows = []
    prev_cond = None
    prev_dpos = None
    # Iterate non-empty bins in order; collect the first WINDOW_LEN of them.
    # (Sparse serial-packet drops leave some 50 ms bins empty; groupby skips
    # those, so the prior `bid >= WINDOW_LEN` break short-circuited at 39
    # rows when even one bin in the first 40 was empty. Fixed 2026-05-13.)
    for bid, g in post_df.groupby("_bin"):
        if len(rows) >= WINDOW_LEN:
            break
        mean_cond = float(g["shifted_cond"].mean())
        mean_pos  = float(g["pos_deg"].mean())
        mean_res  = float(g["resistance"].mean()) / 1000.0
        delta_pos = mean_pos - pos_at_contact
        d_cond_dt = (mean_cond - prev_cond) / (BIN_MS / 1000.0) if prev_cond is not None else 0.0
        d_dpos_dt = (delta_pos - prev_dpos) / (BIN_MS / 1000.0) if prev_dpos is not None else 0.0
        res_norm  = float(np.clip(mean_res / max(baseline_res_k, 1e-6), 0.0, 1.5))
        rows.append([mean_cond, delta_pos, d_cond_dt, d_dpos_dt, res_norm])
        prev_cond = mean_cond
        prev_dpos = delta_pos
    return np.array(rows, dtype=np.float32)


def _extract_trial_window(trial_df: pd.DataFrame):
    """Return ((40, 5) ndarray, baseline_res_k) or (None, reason)."""
    g = trial_df.reset_index(drop=True)
    if len(g) < 30:
        return None, "n<30"
    pre  = g[g["is_press"] == 0]
    post = g[g["is_press"] == 1]
    if len(pre) < 5 or len(post) < 5:
        return None, "no contact window"

    baseline_res_k = float(pre["resistance"].median()) / 1000.0
    f_peak = float(post["pred_force_n"].max()) if "pred_force_n" in g.columns else 0.0
    if f_peak < MIN_F_PEAK_N:
        return None, f"f_peak={f_peak:.2f}<{MIN_F_PEAK_N}"
    if baseline_res_k < MIN_BASELINE_KOHM and f_peak < LOW_BASELINE_F_THRESHOLD:
        return None, f"baseline={baseline_res_k:.0f}kOhm + low f_peak={f_peak:.2f}"

    pos_at_contact = float(post["pos_deg"].iloc[0])
    window = _bin_mean(post, baseline_res_k, pos_at_contact)
    if window.shape[0] < WINDOW_LEN:
        return None, f"only {window.shape[0]} bins (<{WINDOW_LEN})"
    return window[:WINDOW_LEN], ""


def collect_windows():
    X_rows, y_rows, info_rows, dropped = [], [], [], []
    for label, fn in _discover_sources():
        path = os.path.join(DATA_DIR, fn)
        df   = pd.read_csv(path)
        for li, g in df.groupby("loop_index"):
            win, reason = _extract_trial_window(g)
            if win is None:
                dropped.append({"label": label, "source": fn, "loop": int(li), "reason": reason})
                continue
            X_rows.append(win)
            y_rows.append(label)
            info_rows.append({"label": label, "source": fn, "loop": int(li)})
    X = np.stack(X_rows, axis=0) if X_rows else np.zeros((0, WINDOW_LEN, len(FEATURE_NAMES)), np.float32)
    y = np.array(y_rows)
    return X, y, pd.DataFrame(info_rows), pd.DataFrame(dropped)


def build_cnn(input_shape, n_classes):
    from tensorflow.keras import layers, models
    m = models.Sequential([
        layers.Input(shape=input_shape),
        layers.Conv1D(32, 5, padding='same', activation='relu'),
        layers.Conv1D(64, 3, padding='same', activation='relu'),
        layers.MaxPool1D(2),
        layers.Conv1D(64, 3, padding='same', activation='relu'),
        layers.GlobalAveragePooling1D(),
        layers.Dropout(0.3),
        layers.Dense(32, activation='relu'),
        layers.Dense(n_classes, activation='softmax'),
    ])
    m.compile(optimizer='adam', loss='sparse_categorical_crossentropy', metrics=['accuracy'])
    return m


def main() -> int:
    print("=" * 60)
    print("  Phase A-prime — 1D-CNN on PID-Grip Data (v1)")
    print("=" * 60)
    print(f"  Data dir : {DATA_DIR}")
    print(f"  Model dir: {MODEL_DIR}")

    X, y, info, dropped = collect_windows()
    if len(X) == 0:
        print("\n  [ERROR] No usable trials. Aborting.")
        return 1

    if len(dropped):
        print(f"\n  Dropped {len(dropped)} trial(s):")
        for _, r in dropped.iterrows():
            print(f"    [{r['label']:6s}] {r['source']:40s} loop={r['loop']:<3d}  reason={r['reason']}")

    print(f"\n  Trials kept: {len(X)}  per class:")
    for k, v in Counter(y).items():
        print(f"    {k:8s} {v}")

    scaler = StandardScaler().fit(X.reshape(-1, X.shape[-1]))
    Xs = scaler.transform(X.reshape(-1, X.shape[-1])).reshape(X.shape).astype(np.float32)

    classes = sorted(set(y.tolist()))
    cls_idx = {c: i for i, c in enumerate(classes)}
    yi      = np.array([cls_idx[c] for c in y], dtype=np.int64)

    from sklearn.metrics import confusion_matrix, classification_report
    cv     = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    scores = []
    y_pred = np.full_like(yi, fill_value=-1)
    for fold, (tr, te) in enumerate(cv.split(Xs, yi), start=1):
        model = build_cnn(Xs.shape[1:], len(classes))
        model.fit(Xs[tr], yi[tr], epochs=80, batch_size=16, verbose=0, shuffle=True)
        acc = float(model.evaluate(Xs[te], yi[te], verbose=0)[1])
        scores.append(acc)
        y_pred[te] = np.array(model.predict(Xs[te], verbose=0)).argmax(axis=1)
        print(f"  fold {fold}: acc={acc:.3f}")

    print(f"\n  5-fold CV accuracy: {np.mean(scores):.3f} +/- {np.std(scores):.3f}")
    print(f"  Per-fold: {[round(s, 3) for s in scores]}")

    cm = confusion_matrix(yi, y_pred, labels=list(range(len(classes))))
    print("\n  Confusion matrix (rows=true, cols=pred):")
    print(f"          {'  '.join(f'{c:>6s}' for c in classes)}")
    for i, c in enumerate(classes):
        print(f"  {c:6s}  {'  '.join(f'{v:>6d}' for v in cm[i])}")

    print("\n  Per-class report:")
    print(classification_report(yi, y_pred, target_names=classes, digits=3, zero_division=0))

    miss = [(info.iloc[k], classes[y_pred[k]]) for k in range(len(yi)) if y_pred[k] != yi[k]]
    if miss:
        print("  Misclassified trials:")
        for r, pred in miss:
            print(f"    true={r['label']:6s} pred={pred:6s}  {r['source']}  loop={r['loop']}")

    print("\n  Refitting on full dataset for deployment baseline...")
    final = build_cnn(Xs.shape[1:], len(classes))
    final.fit(Xs, yi, epochs=80, batch_size=16, verbose=0, shuffle=True)

    os.makedirs(MODEL_DIR, exist_ok=True)
    model_path  = os.path.join(MODEL_DIR, "material_cnn_pid.keras")
    scaler_path = os.path.join(MODEL_DIR, "scaler_mat_cnn_pid.pkl")
    final.save(model_path)
    joblib.dump({"scaler": scaler, "classes": classes, "window_len": WINDOW_LEN}, scaler_path)
    print(f"\n  Saved {model_path}")
    print(f"  Saved {scaler_path}")

    print("\n  A/B vs RF v4 baseline:")
    print(f"    RF v4   : CV 0.838 +/- 0.018  (macro F1 0.842)")
    print(f"    CNN-PID : CV {np.mean(scores):.3f} +/- {np.std(scores):.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
