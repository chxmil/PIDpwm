"""
Phase B — 1D-CNN Material Classifier Trainer (v1 — 2026-05-13)
==============================================================
Trains a 1D-CNN on (40, 5) probe-phase tensors per Update_2026-05-13
1D-CNN Phase B Plan §1.3.

Input  : data_logs/datasets/probe/*.csv  (auto-discovered; bin/ ignored)
Output : Model/material_cnn.keras
         Model/scaler_mat_cnn.pkl    (StandardScaler + classes_ + WINDOW_LEN)

Each input CSV contains many trials concatenated; one row per probe timestep.
Required columns:
    loop_index, step_idx, material,
    shifted_cond, delta_pos, d_cond_dt, d_dpos_dt, res_norm

Trials with fewer than WINDOW_LEN (40) timesteps are dropped.

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
DATA_DIR   = os.path.join(BASE, "data_logs", "datasets", "probe")
MODEL_DIR  = os.path.join(BASE, "Model")

LABEL_MAP     = {"hard": "Hard", "medium": "Medium", "soft": "Soft"}
FEATURE_NAMES = ["shifted_cond", "delta_pos", "d_cond_dt", "d_dpos_dt", "res_norm"]
WINDOW_LEN    = 40
REQUIRED_COLS = {"loop_index", "step_idx", "material", *FEATURE_NAMES}


def _discover_sources():
    print(f"\n  Scanning {DATA_DIR}")
    if not os.path.isdir(DATA_DIR):
        print(f"    [error] directory missing — create it and add probe CSVs.")
        return []
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
            print(f"    [skip] {fn:40s}  missing cols: {sorted(missing)}")
            continue
        mats = [m for m in head["material"].dropna().astype(str).str.lower().unique() if m]
        label = LABEL_MAP.get(mats[0]) if mats else None
        if label is None:
            print(f"    [skip] {fn:40s}  no `material` label")
            continue
        print(f"    [keep] {fn:40s}  -> {label}")
        out.append((label, fn))
    return out


def collect_windows():
    """Return X (N, 40, 5) and y (N,) arrays plus a per-trial info dataframe."""
    X_rows, y_rows, info_rows, dropped = [], [], [], []
    for label, fn in _discover_sources():
        path = os.path.join(DATA_DIR, fn)
        df   = pd.read_csv(path)
        for li, g in df.groupby("loop_index"):
            g = g.sort_values("step_idx").reset_index(drop=True)
            if len(g) < WINDOW_LEN:
                dropped.append({"label": label, "source": fn, "loop": int(li),
                                "reason": f"n={len(g)}<{WINDOW_LEN}"})
                continue
            window = g.iloc[:WINDOW_LEN][FEATURE_NAMES].to_numpy(dtype=np.float32)
            if not np.isfinite(window).all():
                dropped.append({"label": label, "source": fn, "loop": int(li),
                                "reason": "non-finite values"})
                continue
            X_rows.append(window)
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
    print("  Phase B — 1D-CNN Material Classifier Trainer (v1)")
    print("=" * 60)
    print(f"  Data dir : {DATA_DIR}")
    print(f"  Model dir: {MODEL_DIR}")

    X, y, info, dropped = collect_windows()

    if len(X) == 0:
        print("\n  [ERROR] No usable trials. Collect probe data first:")
        print("           python App.py --port COM18 --material <hard|medium|soft> --probe")
        return 1

    if len(dropped):
        print(f"\n  Dropped {len(dropped)} trial(s):")
        for _, r in dropped.iterrows():
            print(f"    [{r['label']:6s}] {r['source']:40s} loop={r['loop']:<3d}  reason={r['reason']}")

    print(f"\n  Trials kept: {len(X)}  per class:")
    for k, v in Counter(y).items():
        print(f"    {k:8s} {v}")

    # Per-channel StandardScaler fit on flattened training rows
    scaler = StandardScaler().fit(X.reshape(-1, X.shape[-1]))

    def _scale(arr):
        flat = arr.reshape(-1, arr.shape[-1])
        return scaler.transform(flat).reshape(arr.shape)

    Xs = _scale(X).astype(np.float32)

    classes = sorted(set(y.tolist()))
    cls_idx = {c: i for i, c in enumerate(classes)}
    yi      = np.array([cls_idx[c] for c in y], dtype=np.int64)

    # 5-fold stratified CV
    from sklearn.metrics import confusion_matrix, classification_report
    cv      = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    scores  = []
    y_pred  = np.full_like(yi, fill_value=-1)
    for fold, (tr, te) in enumerate(cv.split(Xs, yi), start=1):
        model = build_cnn(Xs.shape[1:], len(classes))
        model.fit(Xs[tr], yi[tr], epochs=80, batch_size=16, verbose=0,
                  validation_split=0.0, shuffle=True)
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

    # Final fit on all data
    print("\n  Refitting on full dataset for deployment...")
    final = build_cnn(Xs.shape[1:], len(classes))
    final.fit(Xs, yi, epochs=80, batch_size=16, verbose=0, shuffle=True)

    os.makedirs(MODEL_DIR, exist_ok=True)
    model_path  = os.path.join(MODEL_DIR, "material_cnn.keras")
    scaler_path = os.path.join(MODEL_DIR, "scaler_mat_cnn.pkl")
    final.save(model_path)
    joblib.dump({"scaler": scaler, "classes": classes, "window_len": WINDOW_LEN}, scaler_path)
    print(f"\n  Saved {model_path}")
    print(f"  Saved {scaler_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
