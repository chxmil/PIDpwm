# Daily Report — 2026-05-12

**Researcher:** Chamil Ahlee · Walailak University
**Branch:** `fix/force-control`
**Author:** Claude Code (claude-opus-4-7)

---

## Summary

Two threads ran today.

1. **Phase A RF classifier — v3 then v3.1 retrain.** Field verification of v2 (2026-05-11) showed a 0.936 CV → 0.714 field accuracy gap. v3 folded in today's three labelled `phase1_20260512_*` sessions; v3.1 then expanded the corpus to every labelled raw per-packet CSV in `data_logs/` so the Soft class reached comparable support (24 → 59 trials). v3.1 is live in `Model/material_rf.pkl`.
2. **Update_2026-05-11 v2 Retrain — formally accepted.** The Update Report that had been sitting at the top of `Claude Report/` in "Under Review" status since 2026-05-11 is consolidated here and marked **Accepted (superseded by v3.1)**. v2 served as the deployed model for the day-12 field verification that motivated v3.

No grip-control code (`ModelInclude.py`, `App.py` PID block) was touched. Field verification on 2026-05-12 also revealed that the runtime `App.py` PID had been re-tuned (`TARGET_FORCE=3.5 N`, `KP=70`, `KI=20`, `KD=7`); CLAUDE.md §7 has since been brought into sync.

---

## Changes Made

### 1. Phase A v3 (afternoon) — fold in 2026-05-12 sessions

| File | Change |
|---|---|
| `Code Store/train_material_rf.py` | Docstring → v3; `SOURCES` expanded to include `phase1_20260512_150425.csv` (Hard), `phase1_20260512_151658.csv` (Medium), `phase1_20260512_150309.csv` (Soft) on top of v2's Hard (1) / Medium (1) / Soft (3) |
| `Model/material_rf.pkl` | Regenerated (RF n_estimators=200, max_depth=None, random_state=0) |
| `Model/scaler_mat_rf.pkl` | Regenerated |

**v3 metrics:** 5-fold CV 0.837 ± 0.029 on 153 trials (Hard 70 / Medium 59 / Soft 24).

### 2. Phase A v3.1 (later) — expand to all labelled CSVs in `data_logs/`

User instruction: use every usable raw labelled CSV in `data_logs/` (excluding `Bin/`, summary files, and unlabeled sessions). `NewModel (1).csv` [Soft] and `NewModel (4).csv` [Medium] were added.

```python
# v3.1 SOURCES
SOURCES = {
    "Hard":   ["Hard (1).csv",
               "phase1_20260512_150425.csv"],
    "Medium": ["Medium (1).csv",
               "NewModel (4).csv",
               "phase1_20260512_151658.csv"],
    "Soft":   ["Soft (3).csv",
               "NewModel (1).csv",
               "phase1_20260512_150309.csv"],
}
```

**Files explicitly excluded:** `data_logs/Bin/**` (archived), all `*_summary.csv` (post-grip predictions, not per-packet), `NewModel (6).csv` and `phase1_20260512_165916.csv` (`material` column empty), `phase1_20260512_153026.csv` (header-only, 145 bytes).

**v3.1 metrics:** 5-fold CV **0.828 ± 0.033** on 203 trials (Hard 70 / Medium 74 / Soft 59).

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Hard | 0.808 | 0.843 | 0.825 → **0.763** (v3.1) | 70 |
| Medium | 0.845 | 0.831 | 0.838 → **0.824** (v3.1) | 74 |
| Soft | 0.909 | 0.833 | 0.870 → **0.908** (v3.1) | 59 |

**Feature importances (v3.1):** `f_peak` 0.325 · `stiffness_proxy` 0.318 · `rise_ms` 0.236 · `res_drop_pct` 0.119 · `delta_pos_max` **0.003** (confirmed dead).

### 3. v2 → v3 → v3.1 trajectory

| Metric | v2 (2026-05-11) | v3 | v3.1 |
|---|---|---|---|
| Trials kept | 78 | 153 | **203** |
| Class balance (H/M/S) | 26 / 30 / 22 | 70 / 59 / 24 | 70 / 74 / 59 |
| CV accuracy | **0.936 ± 0.042** | 0.837 ± 0.029 | 0.828 ± 0.033 |
| CV–field gap (v2) | 0.222 (0.714 field) | — | — |
| Soft F1 | 1.000 | 0.870 | **0.908** |
| Medium F1 | 0.918 | 0.838 | 0.824 |
| Hard F1 | 0.902 | 0.825 | 0.763 |

v3.1 trades CV accuracy for class balance and Soft generalisation — CV should now track field accuracy more honestly than v2's narrow-set 0.936.

---

## Update Report — 2026-05-11 v2 Retrain (Accepted, superseded by v3.1)

> **Status change:** Under Review → **Accepted (superseded by v3.1)** on 2026-05-12.
> Originally drafted 2026-05-11; kept at top of `Claude Report/` pending field verification. Verification ran 2026-05-12 (70-trial labelled set) → 0.714 field accuracy, which motivated the v3 / v3.1 retrains above.

**Resolves:** Issue 6 — Classifier Unreliable on New Sessions (closed 2026-05-11; the day-9 field regression was an upstream firmware fault, patched in Issue 7).

**What was done in v2:**
- Restricted training to post-firmware-fix labelled CSVs only (78 trials across `phase1_20260511_153751.csv` / `Medium (1).csv` / `Soft (3).csv`).
- Same feature extractor, same RF hyperparameters as v1; only `SOURCES` changed.
- `Model/material_rf.pkl` + `scaler_mat_rf.pkl` regenerated and loaded successfully via the unchanged `MaterialClassifier.py` runtime path.

**v2 metrics (preserved for reference):**

```
              precision    recall  f1-score   support
        Hard      0.920     0.885     0.902        26
      Medium      0.903     0.933     0.918        30
        Soft      1.000     1.000     1.000        22
    accuracy                          0.936        78
```

Confusion matrix (v2): only error mode was **Hard ↔ Medium**, 5 borderline-stiffness trials. No Soft cross-talk.

**Field verification (2026-05-12, before v3 retrain):** combined accuracy 0.714 on 70 trials. Soft 1.000 / Medium 0.520 / Hard 0.200. The CV–field gap motivated v3.

**Implementation sequence (final, all complete):**
1. ✅ Confirm Issue 7 firmware fix held.
2. ✅ Collect 2026-05-11 labelled sessions.
3. ✅ Rewrite `SOURCES` (v2).
4. ✅ Train, save artefacts.
5. ✅ Runtime sanity-load.
6. ✅ Accepted today, superseded by v3.1.

---

## Issues Resolved

None **closed** today. v3.1 reduces Soft misclassification materially but does not resolve any of the open issues filed on 2026-05-11.

---

## Issues Still Open

Carried forward from `Open Issues 2026-05-11.md` (snapshot updated below to 2026-05-12). Brief status:

| # | Title | Status | Touched today |
|---|---|---|---|
| 2 | Force Tracking Variance Around Setpoint | Open | Indirect — observed in 2026-05-12 sessions; PID re-tune (`TARGET_FORCE` 2.5→3.5, KP 50→70, KD 5→7) noted in CLAUDE.md §7 but acceptance criterion (±20% of setpoint) not yet measured |
| 3 | Saturated Sensor Silent Failure (Python guard) | Open | Not touched |
| 4 | Material Classifier Data Gap — Phase B (1D-CNN) | Open | Not touched |
| 8 | Stage-2 Baseline Calibration Clamping at 800 kΩ | Open | Not touched — v3.1 misclassifications still concentrated on Hard, consistent with `res_drop_pct` saturating |
| 9 | Hard/Medium Feature Overlap Under PID Overshoot | Open | Confirmed by v3.1: 24/35 misclassifications are Hard↔Medium with high model confidence on borderline `f_peak` |

See `Open Issues 2026-05-12.md` for full text.

---

## Research Data Collected

Three new labelled raw per-packet CSVs entered the training corpus today and are preserved in `data_logs/datasets/` (auto-discovered by the v4 trainer that landed 2026-05-13):

| File | Class | Trials |
|---|---|---|
| `phase1_20260512_150309.csv` | Soft | 2 |
| `phase1_20260512_150425.csv` | Hard | ~46 |
| `phase1_20260512_151658.csv` | Medium | ~32 |

Two unusable captures from the same session (left in `data_logs/` for now, both excluded from training): `phase1_20260512_165916.csv` (no `--material` flag) and `phase1_20260512_153026.csv` (header-only).

No update to `Research/material_classifier_RF_baseline_2026-05-09.md` today — v3.1 is a working iteration, not the article baseline. Article-grade update postponed pending PID re-tune verification (Issue 2/9) and Phase B 1D-CNN landing (Issue 4).
