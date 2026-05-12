"""
Phase A — Random Forest Material Classifier Trainer (v2 — 2026-05-11)
======================================================================
Trains a RandomForestClassifier on 5 hand-crafted features per grip trial:
  delta_pos_max, res_drop_pct, f_peak, rise_ms, stiffness_proxy

v2 retrains on **post-firmware-fix** labelled data only. The pre-fix May/
Prediction CSVs were disqualified by Daily Report 2026-05-10 (Issue 7).

Inputs : data_logs/phase1_20260511_153751.csv      (Hard)
         data_logs/Medium (1).csv                  (Medium)
         data_logs/Soft (3).csv                    (Soft)
Outputs: Model/material_rf.pkl, Model/scaler_mat_rf.pkl

Run offline once; do not call from runtime.
"""
import os
import sys
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
from sklearn.metrics import confusion_matrix, classification_report

BASE      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR  = os.path.join(BASE, "data_logs")
MODEL_DIR = os.path.join(BASE, "Model")

SOURCES = {
    "Hard":   ["phase1_20260511_153751.csv"],
    "Medium": ["Medium (1).csv"],
    "Soft":   ["Soft (3).csv"],
}

FEATURE_NAMES = ["delta_pos_max", "res_drop_pct", "f_peak", "rise_ms", "stiffness_proxy"]

# Quality filters — reject trials that are clearly rubbish before training.
MIN_F_PEAK_N      = 1.5     # <1.5 N peak = failed grip (no real contact)
MIN_BASELINE_KOHM = 1000.0  # below this paired with low f_peak = sensor in contact at calibration
LOW_BASELINE_F_THRESHOLD = 5.0


def extract_trial_features(trial_df: pd.DataFrame):
    """Return a 5-feature dict for one grip trial, or (None, reason) if unusable."""
    g = trial_df.reset_index(drop=True)
    if len(g) < 30:
        return None, "n<30"
    pre  = g[g["is_press"] == 0]
    post = g[g["is_press"] == 1]
    if len(pre) < 5 or len(post) < 5:
        return None, "no contact window"

    baseline_res_k = float(pre["resistance"].median()) / 1000.0
    min_res_k      = float(post["resistance"].min()) / 1000.0
    res_drop_pct   = (baseline_res_k - min_res_k) / max(baseline_res_k, 1e-6)

    pos_at_contact = float(post["pos_deg"].iloc[0])
    delta_pos_max  = abs(float(post["pos_deg"].min()) - pos_at_contact)

    f_peak = float(post["pred_force_n"].max()) if "pred_force_n" in g.columns else 0.0

    if f_peak < MIN_F_PEAK_N:
        return None, f"f_peak={f_peak:.2f}<{MIN_F_PEAK_N}"
    if baseline_res_k < MIN_BASELINE_KOHM and f_peak < LOW_BASELINE_F_THRESHOLD:
        return None, f"baseline={baseline_res_k:.0f}kOhm + low f_peak={f_peak:.2f}"

    if f_peak > 0.1:
        t_contact = float(post["t_ms"].iloc[0])
        target    = 0.9 * f_peak
        reached   = post[post["pred_force_n"] >= target]
        rise_ms   = float(reached["t_ms"].iloc[0] - t_contact) if len(reached) else 0.0
    else:
        rise_ms = 0.0

    stiffness_proxy = (f_peak / rise_ms) if rise_ms > 0 else 0.0

    return {
        "delta_pos_max":   delta_pos_max,
        "res_drop_pct":    res_drop_pct,
        "f_peak":          f_peak,
        "rise_ms":         rise_ms,
        "stiffness_proxy": stiffness_proxy,
    }, ""


def collect_dataset():
    rows, dropped = [], []
    for label, files in SOURCES.items():
        for fn in files:
            path = os.path.join(DATA_DIR, fn)
            if not os.path.exists(path):
                print(f"  [skip] {fn} not found")
                continue
            df = pd.read_csv(path)
            for li, g in df.groupby("loop_index"):
                feats, reason = extract_trial_features(g)
                if feats is None:
                    dropped.append({"label": label, "source": fn, "loop": int(li), "reason": reason})
                    continue
                feats["label"] = label
                feats["source"] = fn
                feats["loop"]  = int(li)
                rows.append(feats)
    return pd.DataFrame(rows), pd.DataFrame(dropped)


def main() -> int:
    print("=" * 60)
    print("  Phase A — Random Forest Material Classifier Trainer")
    print("=" * 60)
    print(f"  Data dir : {DATA_DIR}")
    print(f"  Model dir: {MODEL_DIR}")

    F, D = collect_dataset()
    if len(F) == 0:
        print("  [ERROR] No usable trials. Aborting.")
        return 1

    if len(D):
        print(f"\n  Dropped {len(D)} rubbish trial(s):")
        for _, r in D.iterrows():
            print(f"    [{r['label']:6s}] {r['source']:55s} loop={r['loop']:<3d}  reason={r['reason']}")

    print(f"\n  Trials kept: {len(F)}  per class:")
    for k, v in F["label"].value_counts().items():
        print(f"    {k:8s} {v}")

    X = F[FEATURE_NAMES].fillna(0.0).values.astype(np.float32)
    y = F["label"].values

    scaler = StandardScaler().fit(X)
    Xs     = scaler.transform(X)

    clf = RandomForestClassifier(n_estimators=200, max_depth=None, random_state=0)

    cv     = StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
    scores = cross_val_score(clf, Xs, y, cv=cv)
    print(f"\n  5-fold CV accuracy: {scores.mean():.3f} +/- {scores.std():.3f}")
    print(f"  Per-fold: {[round(s, 3) for s in scores]}")

    y_pred = cross_val_predict(clf, Xs, y, cv=cv)
    classes = sorted(set(y))
    cm = confusion_matrix(y, y_pred, labels=classes)
    print("\n  Confusion matrix (rows=true, cols=pred):")
    print(f"          {'  '.join(f'{c:>6s}' for c in classes)}")
    for i, c in enumerate(classes):
        print(f"  {c:6s}  {'  '.join(f'{v:>6d}' for v in cm[i])}")

    print("\n  Per-class report:")
    print(classification_report(y, y_pred, digits=3, zero_division=0))

    print("  Misclassified trials:")
    F_eval = F.reset_index(drop=True).copy()
    F_eval["pred"] = y_pred
    miss = F_eval[F_eval["label"] != F_eval["pred"]]
    if len(miss) == 0:
        print("    (none)")
    else:
        for _, r in miss.iterrows():
            print(f"    true={r['label']:6s} pred={r['pred']:6s}  {r['source']}  loop={r['loop']}  "
                  f"f_peak={r['f_peak']:.2f} stiff={r['stiffness_proxy']:.4f} rise={r['rise_ms']:.0f}")

    clf.fit(Xs, y)
    fi = sorted(zip(FEATURE_NAMES, clf.feature_importances_), key=lambda x: -x[1])
    print("\n  Feature importances:")
    for n, v in fi:
        print(f"    {n:18s} {v:.3f}")

    os.makedirs(MODEL_DIR, exist_ok=True)
    model_path  = os.path.join(MODEL_DIR, "material_rf.pkl")
    scaler_path = os.path.join(MODEL_DIR, "scaler_mat_rf.pkl")
    joblib.dump(clf, model_path)
    joblib.dump(scaler, scaler_path)
    print(f"\n  Saved {model_path}")
    print(f"  Saved {scaler_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
