"""
One-off inspector — scan every trial in the RF training sources, print
per-trial features + quality flags so we can decide cleaning thresholds.
"""
import os, sys
import numpy as np
import pandas as pd

BASE     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE, "data_logs")

SOURCES = {
    "Hard":   ["Hard.csv",
               os.path.join("Prediction", "Hard",   "phase1_20260509_161354.csv")],
    "Medium": ["Medium.csv",
               os.path.join("Prediction", "Medium", "phase1_20260509_160626.csv")],
    "Soft":   ["Soft (1).csv", "Soft (2).csv",
               os.path.join("Prediction", "Soft",   "phase1_20260509_161009.csv")],
}


def feats(g):
    g = g.reset_index(drop=True)
    n = len(g)
    pre  = g[g["is_press"] == 0]
    post = g[g["is_press"] == 1]
    if n < 30 or len(pre) < 5 or len(post) < 5:
        return dict(n=n, n_pre=len(pre), n_post=len(post),
                    base_kohm=np.nan, min_kohm=np.nan,
                    res_drop=np.nan, dpos=np.nan,
                    f_peak=np.nan, rise=np.nan, stiff=np.nan,
                    rejected="too_short_or_no_contact")
    base_k = float(pre["resistance"].median()) / 1000.0
    min_k  = float(post["resistance"].min()) / 1000.0
    res_drop = (base_k - min_k) / max(base_k, 1e-6)
    pos0   = float(post["pos_deg"].iloc[0])
    dpos   = abs(float(post["pos_deg"].min()) - pos0)
    fpk    = float(post["pred_force_n"].max()) if "pred_force_n" in g.columns else 0.0
    if fpk > 0.1:
        t0 = float(post["t_ms"].iloc[0])
        reached = post[post["pred_force_n"] >= 0.9 * fpk]
        rise = float(reached["t_ms"].iloc[0] - t0) if len(reached) else 0.0
    else:
        rise = 0.0
    stiff = (fpk / rise) if rise > 0 else 0.0
    return dict(n=n, n_pre=len(pre), n_post=len(post),
                base_kohm=base_k, min_kohm=min_k,
                res_drop=res_drop, dpos=dpos,
                f_peak=fpk, rise=rise, stiff=stiff,
                rejected="")


def main():
    rows = []
    for label, files in SOURCES.items():
        for fn in files:
            p = os.path.join(DATA_DIR, fn)
            if not os.path.exists(p):
                continue
            df = pd.read_csv(p)
            for li, g in df.groupby("loop_index"):
                f = feats(g)
                f["label"] = label
                f["source"] = fn
                f["loop"] = int(li)
                rows.append(f)

    F = pd.DataFrame(rows)
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 200)
    pd.set_option("display.float_format", lambda x: f"{x:7.3f}")

    print(F[["label", "source", "loop", "n", "n_pre", "n_post",
             "base_kohm", "min_kohm", "res_drop", "dpos",
             "f_peak", "rise", "stiff", "rejected"]].to_string(index=False))

    print("\n--- summary ---")
    print(F.groupby("label").agg(
        n=("n", "count"),
        rejected=("rejected", lambda s: (s != "").sum()),
        f_peak_med=("f_peak", "median"),
        f_peak_max=("f_peak", "max"),
        f_peak_min=("f_peak", "min"),
        res_drop_med=("res_drop", "median"),
    ))


if __name__ == "__main__":
    main()
