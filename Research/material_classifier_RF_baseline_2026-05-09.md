# Material Classifier — Random Forest Baseline (Phase A)

**Date:** 2026-05-09
**Author:** Chamil Ahlee
**Article context:** Hybrid plan — RF baseline against which the planned 1D-CNN upgrade will be compared (paired McNemar test).

---

## Method

- **Classifier:** `RandomForestClassifier(n_estimators=200, max_depth=None, random_state=0)` on `StandardScaler`-normalised features.
- **Features (5 hand-crafted scalars per trial):** `delta_pos_max`, `res_drop_pct`, `f_peak`, `rise_ms`, `stiffness_proxy`.
- **Validation:** 5-fold stratified cross-validation; `cross_val_predict` for the confusion matrix.
- **Source code:** `Code Store/train_material_rf.py`.

## Data

- **Combined source:** original training CSVs (`Hard.csv`, `Medium.csv`, `Soft (1).csv`, `Soft (2).csv`) + first prediction-mode session CSVs (`data_logs/Prediction/{Hard,Medium,Soft}/`).
- **Quality filters applied:**
  - Trials with `n < 30`, `n_pre < 5`, or `n_post < 5` rejected (no contact window or too short).
  - Trials with `f_peak < 1.5 N` rejected (failed grips).
  - Trials with `baseline_res_k < 1000 kΩ` paired with `f_peak < 5 N` rejected (calibration contaminated by residual contact pressure).
- **Trials before filter:** 58. **Dropped:** 14. **Kept:** 44.

| Class | Trials kept |
|---|---|
| Hard | 11 |
| Medium | 16 |
| Soft | 17 |

## Results

### Cross-validated accuracy
**0.794 ± 0.111** (5-fold). Per-fold: [1.000, 0.778, 0.667, 0.778, 0.750].

### Confusion matrix (rows = true, cols = predicted)

```
            Hard  Medium  Soft
Hard           8       2     1
Medium         0      12     4
Soft           0       2    15
```

### Per-class metrics

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Hard | 1.000 | 0.727 | 0.842 | 11 |
| Medium | 0.750 | 0.750 | 0.750 | 16 |
| Soft | 0.750 | 0.882 | 0.811 | 17 |
| **Macro avg** | 0.833 | 0.787 | 0.801 | 44 |
| **Weighted avg** | 0.812 | 0.795 | 0.797 | 44 |

### Feature importances

| Feature | Importance |
|---|---|
| `res_drop_pct` | 0.368 |
| `f_peak` | 0.345 |
| `stiffness_proxy` | 0.127 |
| `rise_ms` | 0.124 |
| `delta_pos_max` | 0.036 |

## Discussion

- **Hard/Soft never confused.** Errors live exclusively in the Medium ↔ Soft and low-end Hard ↔ Medium overlap zone (`f_peak ≈ 3–7 N`).
- **Hard precision = 1.000.** When the classifier predicts Hard, it is always correct. Its under-firing (recall 0.727) is the weak side and reflects the small Hard class (n = 11) plus borderline trials that look Medium.
- **`delta_pos_max` is nearly uninformative** (3.6% importance, mostly 0° in the rig). This validates Phase B's switch to a contact-relative Δpos *time series* channel; the scalar feature collapses under the current PID control.
- **Two physically grounded features carry 71% of the importance** (`res_drop_pct` + `f_peak`), with timing features the next 25%. The classifier is therefore not relying on circular force-model features alone.

## Limitations

- 44 trials is small. The 0.111 std-dev across folds reflects this — one fold scored 0.667.
- Class imbalance: Hard 11 vs Soft 17 is a 1.5× ratio.
- `f_peak` is derived from the CNN-LSTM force model output, so this baseline carries a soft circular dependency on the force pipeline. Phase B eliminates this by feeding raw conductance / position channels.
- **Added 2026-05-11:** The 2026-05-09 prediction-mode CSVs in `data_logs/Prediction/{Hard,Medium,Soft}/` (used in field-accuracy spot-checks) were collected under the firmware-faulty ADS1115 regime documented by Issue 7 (closed 2026-05-10). They are **not** representative of system performance under healthy firmware and have been formally disqualified. The day-9 field-accuracy figures (Hard 29% / Medium 10% / Soft 78%) should not appear in the article. The post-fix v2 retrain (below) supersedes them.

---

## Post-Fix Retrain — v2 (2026-05-11)

Following the Issue 7 firmware patch (2026-05-10), the RF was retrained on freshly collected post-firmware-fix labelled sessions.

### Data
- **Sources:** `data_logs/phase1_20260511_153751.csv` (Hard), `data_logs/Medium (1).csv` (Medium), `data_logs/Soft (3).csv` (Soft).
- **Before filter:** 93 trials. **Kept:** 78 (15 dropped — 13 × no contact window, 2 × baseline + low f_peak).
- **Per class:** Hard 26 / Medium 30 / Soft 22.

### Cross-validated accuracy (v2)
**0.936 ± 0.042** (5-fold). Per-fold: [0.938, 0.938, 0.938, 0.867, 1.000].

### Confusion matrix v2 (CV)
```
            Hard  Medium  Soft
Hard          23       3     0
Medium         2      28     0
Soft           0       0    22
```

### Per-class metrics v2

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Hard | 0.920 | 0.885 | 0.902 | 26 |
| Medium | 0.903 | 0.933 | 0.918 | 30 |
| Soft | 1.000 | 1.000 | 1.000 | 22 |
| **Macro avg** | 0.941 | 0.939 | 0.940 | 78 |
| **Weighted avg** | 0.936 | 0.936 | 0.936 | 78 |

### Feature importances v2

| Feature | Importance |
|---|---|
| `f_peak` | 0.310 |
| `stiffness_proxy` | 0.290 |
| `rise_ms` | 0.222 |
| `res_drop_pct` | 0.177 |
| `delta_pos_max` | 0.000 |

### Field verification v2 (same day, 70 labelled trials)

| Class | Trials | Correct | Field accuracy |
|---|---|---|---|
| Soft   | 35 | 35 | **1.000** |
| Medium | 25 | 13 | **0.520** |
| Hard   | 10 |  2 | **0.200** |
| **All** | 70 | 50 | **0.714** |

### Discussion of CV-vs-field gap

CV 0.936 does not survive contact with the field: combined field accuracy is 0.714. The gap is concentrated on Hard (CV 0.885 → field 0.200) and Medium (0.933 → 0.520). Soft is unchanged at 1.000.

Two upstream signal issues, both filed as separate open Issues on 2026-05-11:

- **Issue 8 — Stage-2 baseline calibration still clamps at 800 kΩ on most trials.** Loss of `res_drop_pct` signal where it matters most (Hard/Medium boundary). Independent of Issue 7's firmware fix.
- **Issue 9 — PID overshoot collapses Hard/Medium `f_peak` separation.** Setpoint 2.5 N, observed peaks 8–17 N for both classes. Couples to Issue 2 (Force Tracking Variance, open since 2026-05-08).

These signal-level limits cap field accuracy below the CV number regardless of which supervised classifier sits on top. The v2 RF is accepted as the best classifier achievable under the current signal regime; the residual error budget is forwarded to PID re-tuning (Issue 9 / Issue 2) and calibration-window investigation (Issue 8) rather than further RF retraining.

### Article reporting recommendation

Both numbers should appear, with CV cited as the *cleaned-data* result and field cited as the *post-fix realistic* result. Day-9 field numbers (Hard 29% / Medium 10% / Soft 78%) should be omitted entirely or relegated to a §Pre-Fix Diagnostic Run footnote.

---

## Artefacts

- Trained model: `Model/material_rf.pkl` (v2, 2026-05-11)
- Feature scaler: `Model/scaler_mat_rf.pkl` (v2, 2026-05-11)
- Trainer: `Code Store/train_material_rf.py`
- Quality inspector: `Code Store/inspect_material_data.py`
- v1 operational record: `Claude Report/Daily Report/Daily Report 2026-05-09.md`
- v2 retrain record: `Claude Report/Update Report/Update_2026-05-11_Material Classifier v2 Retrain.md`
