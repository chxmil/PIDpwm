"""
Quick discriminability check: do the Hard/Medium/Soft datasets carry
enough signal for a CNN-based material classifier?

We do NOT train a model here — only test whether the CLAUDE.md-spec
features (Δpos after first contact, conductance trajectory, force
slope) cluster by class.
"""
import os
import numpy as np
import pandas as pd

base = r"C:\Users\charm\OneDrive\documents\Arduino\PIDpwm\data_logs"
files = {
    "Hard":   "Hard.csv",
    "Medium": "Medium.csv",
    "Soft1":  "Soft (1).csv",
    "Soft2":  "Soft (2).csv",
}

print("=" * 78)
print("STAGE 1 — Dataset survey")
print("=" * 78)

dfs = {}
for k, v in files.items():
    df = pd.read_csv(os.path.join(base, v))
    dfs[k] = df
    n_loops = df["loop_index"].nunique()
    n_press = (df["is_press"] == 1).sum()
    f_max = df["pred_force_n"].max() if "pred_force_n" in df.columns else float("nan")
    print(f"{k:8s} rows={len(df):>5d}  loops={n_loops:>3d}  "
          f"is_press_rows={n_press:>4d}  force_max={f_max:.2f} N")

print()
print("=" * 78)
print("STAGE 2 — Per-loop features (key signal: deformation + force trajectory)")
print("=" * 78)


def loop_features(df, label):
    """
    For each grip trial in df, extract:
      - delta_pos_max  : max angular travel after first contact (deformation depth)
      - res_drop_pct   : (baseline_res - min_res) / baseline_res
      - force_peak     : peak predicted force
      - rise_time_ms   : time from is_press=1 to force >= 0.9 * peak
      - slope_force    : peak force / rise_time  (proxy for stiffness)
    """
    feats = []
    for li, g in df.groupby("loop_index"):
        g = g.reset_index(drop=True)
        if len(g) < 30:
            continue
        # Baseline = mean of pre-press resistance
        pre = g[g["is_press"] == 0]
        post = g[g["is_press"] == 1]
        if len(pre) < 5 or len(post) < 5:
            continue

        baseline_res = pre["resistance"].median() / 1000.0  # kΩ
        min_res = post["resistance"].min() / 1000.0
        res_drop_pct = (baseline_res - min_res) / max(baseline_res, 1e-6)

        pos_at_contact = post["pos_deg"].iloc[0]
        delta_pos_max = abs(post["pos_deg"].min() - pos_at_contact)  # close = decreasing pos

        f_peak = post["pred_force_n"].max() if "pred_force_n" in g else 0.0

        # Rise time: ms from contact to 90% peak
        if f_peak > 0.1:
            t_contact = post["t_ms"].iloc[0]
            target = 0.9 * f_peak
            reached = post[post["pred_force_n"] >= target]
            rise_ms = (reached["t_ms"].iloc[0] - t_contact) if len(reached) else np.nan
        else:
            rise_ms = np.nan

        slope = f_peak / rise_ms if rise_ms and rise_ms > 0 else np.nan

        feats.append({
            "label": label,
            "loop": li,
            "delta_pos_max": delta_pos_max,
            "res_drop_pct": res_drop_pct,
            "f_peak": f_peak,
            "rise_ms": rise_ms,
            "stiffness_proxy": slope,
        })
    return pd.DataFrame(feats)


# Treat Soft1+Soft2 as one class
all_feats = []
for k, df in dfs.items():
    label = "Soft" if k.startswith("Soft") else k
    all_feats.append(loop_features(df, label))
F = pd.concat(all_feats, ignore_index=True)
print(F.groupby("label").describe()[["delta_pos_max", "res_drop_pct",
                                       "f_peak", "rise_ms", "stiffness_proxy"]]
      .round(3))

print()
print("=" * 78)
print("STAGE 3 — Class-mean separability (informal but informative)")
print("=" * 78)


def fisher(col, F):
    grp = F.groupby("label")[col].agg(["mean", "std", "count"]).dropna()
    if len(grp) < 2:
        return float("nan")
    overall = F[col].mean()
    between = ((grp["mean"] - overall) ** 2 * grp["count"]).sum() / grp["count"].sum()
    within = (grp["std"].fillna(0) ** 2 * grp["count"]).sum() / grp["count"].sum()
    return between / max(within, 1e-9)


for col in ["delta_pos_max", "res_drop_pct", "f_peak", "rise_ms", "stiffness_proxy"]:
    print(f"  Fisher score [{col}]: {fisher(col, F):.3f}   "
          f"(>1 = decent class separation, >3 = strong)")

print()
print("=" * 78)
print("STAGE 4 — Tiny baseline classifier (sanity: is signal learnable?)")
print("=" * 78)

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import cross_val_score

usable = F.dropna(subset=["delta_pos_max", "res_drop_pct", "f_peak",
                          "rise_ms", "stiffness_proxy"])
print(f"  Usable trials: {len(usable)}  classes: {usable['label'].value_counts().to_dict()}")

if len(usable) >= 15 and usable["label"].nunique() >= 2:
    X = usable[["delta_pos_max", "res_drop_pct", "f_peak",
                "rise_ms", "stiffness_proxy"]].values
    y = usable["label"].values
    clf = RandomForestClassifier(n_estimators=200, random_state=0)
    scores = cross_val_score(clf, X, y, cv=min(5, usable["label"].value_counts().min()))
    print(f"  RF cross-val accuracy: {scores.mean():.3f} ± {scores.std():.3f}")
    print(f"  Per-fold: {scores.round(3).tolist()}")
    clf.fit(X, y)
    fi = pd.Series(clf.feature_importances_,
                   index=["delta_pos_max", "res_drop_pct", "f_peak",
                          "rise_ms", "stiffness_proxy"]).sort_values(ascending=False)
    print("  Feature importances:")
    for n, v in fi.items():
        print(f"    {n:18s} {v:.3f}")
else:
    print("  Not enough trials or only one class — skipping classifier sanity check.")
