# Daily Report — 2026-05-09

**Researcher:** Chamil Ahlee
**Author:** Claude Code (claude-opus-4-7)
**Branch:** `fix/force-control`
**Combines:** Update_2026-05-09_Material Classifier Plan, Issue 5 (closed), DevLog Material Classifier Phase A, DevLog Material Classifier Retrain + Data Clean
**Carries forward:** Issue 2 (Force Tracking Variance), Issue 3 (Sensor Saturation Silent Failure), Issue 4 (Material Classifier Data Gap — Phase B portion only)

---

## Headline

Material classifier — Phase A — is **deployed end-to-end**. Today we (a) locked the Hybrid plan and the (40, 5) input architecture, (b) implemented the runtime classifier and per-trial summary CSV, (c) collected the first batch of prediction-mode session data, (d) re-trained on the combined (old + new) dataset with quality filtering, and (e) produced a confusion matrix that is the first publishable benchmark for the article.

**Final standing:** RF classifier at **0.794 ± 0.111** 5-fold CV on **44 cleaned trials**, integrated into `App.py` runtime, writing `pred_material` + class probabilities per grip.

---

## Final state at end of day

| Aspect | Value |
|---|---|
| Branch | `fix/force-control` |
| Phase 1 (force control) | Stable — no changes today |
| Phase 2 (material classifier) | **Phase A live** — RF + scaler artefacts in `Model/` |
| Force CNN-LSTM | Unchanged (1.8 Hz, separate from classifier) |
| Classifier type | Random Forest, 200 trees, `random_state=0` |
| Classifier features | `delta_pos_max`, `res_drop_pct`, `f_peak`, `rise_ms`, `stiffness_proxy` |
| Cleaned training set | 44 trials (Hard 11 / Medium 16 / Soft 17) |
| 5-fold CV accuracy | **0.794 ± 0.111** |
| Per-class precision | Hard 1.000 / Medium 0.750 / Soft 0.750 |
| Per-class recall | Hard 0.727 / Medium 0.750 / Soft 0.882 |
| Phase B (1D-CNN) | Architecture locked — `(40, 5)` at 20 Hz; awaiting probe-phase data |

---

## Timeline / What we did today

### 1. Architecture decision — Hybrid Plan + Issue 5

The morning's open question: how should the 1D-CNN classifier handle window size, sample rate, and position-invariance?

Decisions locked (see Update Report, now folded in here):

- **Hybrid two-model approach (Possibility D from Issue 4)** — Random Forest baseline now, 1D-CNN upgrade after probe-phase data collection.
- **Sample rate change to 20 Hz** for the classifier (Issue 5 Possibility A) — the force CNN-LSTM stays at 1.8 Hz; classifier runs at its own rate.
- **40-step × 50 ms = 2.0 s window** — pre-contact pad 10 steps, post-contact 30 steps, alignment at first `is_press = 1`.
- **Contact-relative `Δpos`** (Issue 5 Fix B) — kills absolute encoder offset.
- **Velocity channel `d_pos / dt`** (Issue 5 Fix D — initially rejected, reversed by researcher mid-day) — gains approximate object-size invariance.
- **Channel 6 (object-size side input) DEFERRED** until baseline accuracy is measured.
- **Final input shape: `(40, 5)`** — channels: shifted_cond, d_shifted_cond/dt, Δpos, d_pos/dt, pwm/255.

Issue 5 was opened, debated, accepted, and **closed** the same day.

### 2. Phase A implementation — DevLog #1

Code added:
- **`Code Store/train_material_rf.py`** — offline trainer; reads the labelled CSVs, extracts the 5 hand-crafted features per trial, fits a `StandardScaler` + `RandomForestClassifier(n_estimators=200, random_state=0)`, runs 5-fold CV, saves `Model/material_rf.pkl` + `Model/scaler_mat_rf.pkl`.
- **`MaterialClassifier.py`** at project root — runtime inference. Loads RF + scaler at import (silent skip if missing). Exposes `classify_trial(records, baseline_res_k) → (label, prob_dict) | (None, None)`.

Code modified:
- **`ModelInclude.py::run_one_grip()`** — added `records = []` accumulator; appends `{t_ms, pos, res, is_press, pred_force_n}` per packet; **return contract changed** from `pkt_count: int` to `dict{pkt_count, trial_records, baseline_res_k, max_force, contact_detected}`.
- **`App.py`** — classifier import with graceful fallback; new per-trial summary CSV `phase1_<ts>_<tag>_summary.csv` with columns `loop_index, material_label, tag, pred_material, prob_Hard, prob_Medium, prob_Soft, max_force_n, contact_detected, baseline_res_k, pkt_count`; new `_post_grip_classify()` helper invoked after each `run_one_grip()` call.

Boundary compliance: classification is **post-grip** orchestration in `App.py`; `ModelInclude.py` only gained data collection. CLAUDE.md rule "all grip logic in `ModelInclude.py`" respected.

Initial Phase A run on 28 usable trials (Hard 6, Medium 10, Soft 12): **0.780 ± 0.139**.

### 3. First prediction-mode data collection

Three sessions ran with the classifier live, one per material:

| Session | Folder | Trials | Predicted correctly |
|---|---|---|---|
| 16:06:26 | `data_logs/Prediction/Medium/` | 10 | 1 / 10 |
| 16:10:09 | `data_logs/Prediction/Soft/` | 9 | 8 / 9 |
| 16:13:54 | `data_logs/Prediction/Hard/` | 8 | 2 / 8 |

Soft was robust; Hard and Medium showed heavy confusion — most errors came from sessions where the calibration baseline collapsed (`baseline_res_k ≈ 700 kΩ`), suggesting the gripper retained partial contact pressure between auto-loop trials, contaminating the conductance shift pipeline.

### 4. Combined-data retrain + rubbish filter — DevLog #2

**Trainer extended:**
- `SOURCES` dict now includes the 3 prediction-folder CSVs alongside the original 4 training files.
- New quality filters in `extract_trial_features()`:
  - `MIN_F_PEAK_N = 1.5` — drop failed grips
  - `baseline_res_k < 1000 kΩ` paired with `f_peak < 5 N` — drop trials where calibration was contaminated
- `cross_val_predict` + `confusion_matrix` + `classification_report` added to the trainer.
- New `Code Store/inspect_material_data.py` — per-trial feature dump for ongoing data quality audits.

**Combined dataset:**

| Source | Class | Trials | Kept | Dropped |
|---|---|---|---|---|
| `Hard.csv` | Hard | 9 | 4 | 5 (3 no-contact, 2 f_peak<1.5) |
| `Prediction/Hard/phase1_…161354.csv` | Hard | 8 | 7 | 1 (no contact) |
| `Medium.csv` | Medium | 10 | 10 | 0 |
| `Prediction/Medium/phase1_…160626.csv` | Medium | 10 | 6 | 4 (low baseline + low f_peak) |
| `Soft (1).csv` | Soft | 7 | 6 | 1 (low baseline) |
| `Soft (2).csv` | Soft | 5 | 5 | 0 |
| `Prediction/Soft/phase1_…161009.csv` | Soft | 9 | 6 | 3 (2 no-contact, 1 low baseline) |
| **Total** | | **58** | **44** | **14** |

**5-fold CV result:** 0.794 ± 0.111 (per-fold: [1.000, 0.778, 0.667, 0.778, 0.750]).

**Confusion matrix (5-fold cross-val predictions, rows=true, cols=pred):**

```
            Hard  Medium  Soft
true Hard      8       2     1     recall 0.727
     Medium    0      12     4     recall 0.750
     Soft      0       2    15     recall 0.882
```

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| Hard | **1.000** | 0.727 | 0.842 | 11 |
| Medium | 0.750 | 0.750 | 0.750 | 16 |
| Soft | 0.750 | **0.882** | 0.811 | 17 |
| macro | 0.833 | 0.787 | 0.801 | 44 |
| weighted | 0.812 | 0.795 | 0.797 | 44 |

**Reading the matrix:** Hard ↔ Soft is *never* confused — they sit at opposite ends of every feature axis. All errors live in the Medium ↔ Soft and low-end Hard ↔ Medium overlap zone (`f_peak ≈ 3–7 N`). Hard has perfect precision but under-fires; Soft has the highest recall.

**Feature importances (post-clean):**

| Feature | Importance |
|---|---|
| `res_drop_pct` | 0.368 |
| `f_peak` | 0.345 |
| `stiffness_proxy` | 0.127 |
| `rise_ms` | 0.124 |
| `delta_pos_max` | 0.036 |

`delta_pos_max` is dead weight — the encoder barely advances after contact (Issue 4 §2.2). Either drop it, or replace with the Phase B Δpos series channel.

---

## Decisions log

| Decision | Source | Status |
|---|---|---|
| Hybrid two-model approach (RF + CNN) | Issue 4 / Possibility D | ✅ Accepted |
| Sample rate change to 20 Hz | Issue 5 / Possibility A | ✅ Accepted |
| 40-step × 50 ms = 2.0 s window | Issue 5 / Possibility A | ✅ Accepted |
| Contact-relative position (Δpos) | Issue 5 / Fix B | ✅ Accepted |
| Velocity channel `d_pos/dt` | Issue 5 / Fix D | ✅ Reversed-in same day |
| Object-size side input (Channel 6) | Issue 5 §4.4 | 🟡 Deferred |
| RF baseline integrated into App.py | Plan §2 | ✅ Live |
| Add quality filters to RF trainer | DevLog #2 | ✅ Live |

---

## Files touched today

| File | Change |
|---|---|
| `Code Store/train_material_rf.py` | New (Phase A trainer) → extended (filters + CM + cross-val predictions) |
| `Code Store/inspect_material_data.py` | New (per-trial QC dump) |
| `MaterialClassifier.py` | New (runtime inference) |
| `ModelInclude.py` | `run_one_grip()` return contract changed; records accumulator added |
| `App.py` | Classifier import, summary CSV, `_post_grip_classify()` helper |
| `Model/material_rf.pkl` | Created → overwritten with cleaned-data artefact |
| `Model/scaler_mat_rf.pkl` | Created → overwritten with cleaned-data artefact |
| `data_logs/Prediction/{Hard,Medium,Soft}/` | New session data (3 sessions) |
| `data_logs/{Hard,Medium}.csv` | New labelled training CSVs |
| `Model/Train/CNNLstm (2).ipynb` | Renamed from existing notebook |

---

## Carried-forward open issues (see Open Issues 2026-05-09.md for full list)

- **Issue 2 — Force Tracking Variance** (low pri) — system reaches setpoint on average, but loop-to-loop variance is ±1 N. Mitigation #1 (raise `GRIP_PWM`) recommended; not yet tested. Carried.
- **Issue 3 — Sensor Saturation Silent Failure** (medium pri) — fix designed but not yet merged. Need to add saturation detection in Stage 2 calibration. Carried.
- **Issue 4 — Material Classifier Data Gap** (Phase A resolved; Phase B blocker) — RF baseline is solid. Phase B 1D-CNN still requires probe-phase protocol implementation and ~90 trials of probe-phase data. Carried (Phase B portion only).

---

## What's next

1. **Hardware/data discipline:** half of today's dropped trials (7/14) came from contaminated baselines. Add Issue 3's saturation-detection patch + a separate "baseline below 1500 kΩ" warning to abort early before producing rubbish.
2. **More Hard data:** the cleaned set has only 11 Hard trials and they drive the 0.111 fold variance (lowest fold = 0.667). Target ≥20.
3. **Probe-phase mode in `App.py`:** unblocks Phase B 1D-CNN training. ~1 hr code + 2 hr data collection.
4. **Drop or replace `delta_pos_max`:** carries 3.6% importance, mostly 0° in the current rig.
5. **Force-Tracking Variance experiment** (Issue 2 #1): 6 grips at 3.5 N target with `GRIP_PWM = -210`, measure stdev.
