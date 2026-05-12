# DevLog — Material Classifier Retrain on Combined Data + Rubbish Filter

**Date:** 2026-05-09
**Status:** Under Review
**Author:** Claude
**Touches:** `Code Store/train_material_rf.py`, `Code Store/inspect_material_data.py` (new), `Model/material_rf.pkl`, `Model/scaler_mat_rf.pkl`

---

## 1. What Changed

1. Extended `train_material_rf.py` SOURCES dict to include the 3 prediction-folder CSVs alongside the original 4 training CSVs.
2. Added two quality filters to `extract_trial_features()`:
   - **`MIN_F_PEAK_N = 1.5`** — drop trials whose peak predicted force is below 1.5 N (failed grips).
   - **`MIN_BASELINE_KOHM = 1000` paired with `f_peak < 5.0 N`** — drop trials where the calibration baseline collapsed below 1 kΩ AND the resulting force peak stayed low. These were sessions where the gripper was already in contact when the baseline was sampled, so the conductance-shift pipeline produced off-distribution features.
3. Added `cross_val_predict` + `confusion_matrix` + `classification_report` to the trainer so per-class behavior is visible at every retrain.
4. Added a one-off inspector script `Code Store/inspect_material_data.py` that prints per-trial features and quality flags — used to set the filter thresholds.

`extract_trial_features()` now returns `(features_dict, "")` on accept and `(None, "<reason>")` on reject so the dropped-trial table can be printed.

---

## 2. Data Inventory

| Source | Class | Trials | Kept | Dropped |
|---|---|---|---|---|
| `Hard.csv` | Hard | 9 | 4 | 5 (3 no-contact, 2 f_peak<1.5) |
| `Prediction/Hard/phase1_20260509_161354.csv` | Hard | 8 | 7 | 1 (no contact) |
| `Medium.csv` | Medium | 10 | 10 | 0 |
| `Prediction/Medium/phase1_20260509_160626.csv` | Medium | 10 | 6 | 4 (low baseline + low f_peak) |
| `Soft (1).csv` | Soft | 7 | 6 | 1 (low baseline) |
| `Soft (2).csv` | Soft | 5 | 5 | 0 |
| `Prediction/Soft/phase1_20260509_161009.csv` | Soft | 9 | 6 | 3 (2 no-contact, 1 low baseline) |
| **Total** | | **58** | **44** | **14** |

**Per-class kept:** Hard 11, Medium 16, Soft 17.

---

## 3. Dropped Trials (Rubbish List)

| Class | Source | Loop | Reason |
|---|---|---|---|
| Hard | Hard.csv | 1 | no contact window (`is_press` never latched) |
| Hard | Hard.csv | 3 | no contact window |
| Hard | Hard.csv | 6 | no contact window |
| Hard | Hard.csv | 7 | f_peak=1.10 N < 1.5 |
| Hard | Hard.csv | 8 | f_peak=1.10 N < 1.5 |
| Hard | Prediction/Hard | 7 | no contact window |
| Medium | Prediction/Medium | 1 | baseline=701 kΩ + f_peak=3.31 N |
| Medium | Prediction/Medium | 2 | baseline=725 kΩ + f_peak=4.03 N |
| Medium | Prediction/Medium | 4 | baseline=732 kΩ + f_peak=3.05 N |
| Medium | Prediction/Medium | 5 | baseline=727 kΩ + f_peak=4.75 N |
| Soft | Soft (1).csv | 3 | baseline=735 kΩ + f_peak=3.47 N |
| Soft | Prediction/Soft | 1 | baseline=710 kΩ + f_peak=4.14 N |
| Soft | Prediction/Soft | 3 | no contact window |
| Soft | Prediction/Soft | 5 | no contact window |

---

## 4. Training Result

**5-fold CV accuracy:** **0.794 ± 0.111**
**Per-fold:** [1.000, 0.778, 0.667, 0.778, 0.750]

For reference:
- Pre-clean (52 trials, both old + new): **0.825 ± 0.111**
- Original (31 old trials only, baseline in plan doc): **0.820 ± 0.107**

The cleaned-data score is ~3 pp lower because removing the 14 borderline trials also removes some "easy wins" from the CV folds and shrinks Hard from 17 → 11 examples. The remaining 44 trials are a more honest representation of the in-class feature distribution.

---

## 5. Confusion Matrix (5-fold cross-val predictions)

```
            pred
            Hard  Medium  Soft
true Hard      8       2     1     (recall 0.727)
     Medium    0      12     4     (recall 0.750)
     Soft      0       2    15     (recall 0.882)
```

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Hard | **1.000** | 0.727 | 0.842 | 11 |
| Medium | 0.750 | 0.750 | 0.750 | 16 |
| Soft | 0.750 | **0.882** | 0.811 | 17 |
| **macro** | 0.833 | 0.787 | 0.801 | 44 |
| **weighted** | 0.812 | 0.795 | 0.797 | 44 |

**Reading the matrix:**
- **Hard has perfect precision** (1.000): when the model says "Hard" it is always Hard. But it under-fires (recall 0.727) — 3 Hard trials get pushed into Medium/Soft because their `f_peak` is low (~4–7 N, overlapping the Medium band).
- **Soft has the highest recall** (0.882) — Soft trials are the easiest to recognize (lowest force, longest rise time).
- **Medium is the weakest class** (0.75 / 0.75): 4 Medium trials drift into Soft because their f_peak is in the 3–5 N range that Soft also occupies.
- The model never confuses Hard ↔ Soft directly (as expected — they sit at opposite ends of every feature axis).

---

## 6. Misclassified Trials

| True | Pred | Source | Loop | f_peak | stiffness | rise_ms |
|---|---|---|---|---|---|---|
| Hard | Medium | Hard.csv | 2 | 7.50 | 0.0023 | 3256 |
| Hard | Soft | Hard.csv | 9 | 4.24 | 0.0041 | 1026 |
| Hard | Medium | Prediction/Hard | 1 | 4.70 | 0.0107 | 441 |
| Medium | Soft | Medium.csv | 5 | 4.54 | 0.0112 | 407 |
| Medium | Soft | Medium.csv | 10 | 4.71 | 0.0050 | 933 |
| Medium | Soft | Prediction/Medium | 8 | 3.20 | 0.0006 | 5496 |
| Medium | Soft | Prediction/Medium | 10 | 3.00 | 0.0007 | 4367 |
| Soft | Medium | Prediction/Soft | 2 | 4.72 | 0.0047 | 997 |
| Soft | Medium | Prediction/Soft | 7 | 5.08 | 0.0064 | 793 |

**Pattern:** all 9 errors live in the Medium↔Soft and low-end-Hard↔Medium overlap zone (f_peak ≈ 3–7 N). Hard-vs-Soft is never confused.

---

## 7. Feature Importances (post-clean)

| Feature | Importance | Δ vs pre-clean |
|---|---|---|
| `res_drop_pct` | 0.368 | +0.06 |
| `f_peak` | 0.345 | +0.04 |
| `stiffness_proxy` | 0.127 | −0.03 |
| `rise_ms` | 0.124 | −0.06 |
| `delta_pos_max` | 0.036 | −0.005 |

Cleaning increased reliance on `res_drop_pct` and `f_peak` (the two most physically grounded features) and decreased reliance on the timing features. `delta_pos_max` is still nearly useless — the gripper's encoder mostly sits at 0° change after contact, so this feature carries almost no signal in the current rig.

---

## 8. Recommendations

1. **More Hard trials.** With only 11 retained, Hard is the smallest class and drives the per-fold variance (one fold scored 0.667). Target ≥20 for a tighter CV interval.
2. **Sensor calibration discipline.** 7 of 14 dropped trials came from sessions where `baseline_res_k < 1500 kΩ`. The likely cause is the gripper retaining residual contact pressure between trials in auto-loop mode. Confirm `Stage 5 — Release & Home` clears all contact before Stage 2 calibration (CLAUDE.md §6).
3. **`delta_pos_max` is dead weight.** It carries 3.6% importance and is mostly 0.000 across trials. Either drop it or replace with the planned Channel-3 contact-relative `Δpos` series (Phase B).
4. **The Medium↔Soft boundary is the next bottleneck.** All 4 Medium-recall errors and 2 of 2 Soft-recall errors live there. Consider a `force_above_2N_duration_ms` feature (steady-state hold time) — Hard and Medium hold force longer than Soft once contact is achieved.
5. **Phase B trigger.** RF cleaned-CV is at 79.4%. The Phase B 1D-CNN target in the existing plan is ≥85%. With 44 trials we are below the 90-trial target in the plan; collect more before training the CNN, or the result will overfit.

---

## 9. Files Touched

- `Code Store/train_material_rf.py` — added filters, dropped-trial reporting, confusion matrix, misclassified-trial dump
- `Code Store/inspect_material_data.py` — new inspector for ongoing data quality audits
- `Model/material_rf.pkl` — overwritten (new artefact, 44-trial training)
- `Model/scaler_mat_rf.pkl` — overwritten

`MaterialClassifier.py` requires no change; it picks up the new artefacts on next `App.py` start.
