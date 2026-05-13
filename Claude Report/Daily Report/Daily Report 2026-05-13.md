# Daily Report — 2026-05-13

**Researcher:** Chamil Ahlee · Walailak University
**Branch:** `fix/force-control`
**Author:** Claude Code (claude-opus-4-7)

---

## Summary

Big classifier day. Four threads ran in sequence:

1. **Phase A RF → v4 auto-discovery refactor.** `train_material_rf.py` now scans `data_logs/datasets/` and infers labels from the `material` column; no more hardcoded `SOURCES`. CV 0.838 ± 0.018 on 203 trials (same data as v3.1, tighter folds).
2. **Phase B 1D-CNN skeleton.** Stage 2.5 probe phase added to `ModelInclude.py` (opt-in via `--probe`), trainer + runtime loader written, awaits probe data collection. Update Report accepted and moved to `Update Report/`.
3. **Phase A-prime 1D-CNN — a deep alternative to RF on the existing PID-grip data.** Same architecture as the planned Phase B, but trained on `data_logs/datasets/` (not probe data). v1 had a bin-skip bug that dropped 222 of 286 trials. v2 (fixed) trains on 260 trials and scores **CV 0.942 ± 0.024**, +0.104 vs RF v4.
4. **Runtime wire-up + back-test + research deliverables.** CNN-PID v2 wired alongside RF in `App.py` (3 classifiers per grip now). In-sample back-test confirms strict dominance (CNN-PID right on every disagreement, 0/11 the other way). Full methodology report produced in markdown + DOCX with 6 embedded figures.

No grip-control or PID code touched today. Force model unchanged. RF v4 remains the contractual primary classifier; CNN-PID v2 runs as a diagnostic until a labelled field A/B session confirms the +0.104 CV advantage holds out-of-distribution.

---

## Changes Made

### 1. RF v4 — Auto-Discovery Refactor

| File | Change |
|---|---|
| `Code Store/train_material_rf.py` | Docstring to v4; `DATA_DIR` → `data_logs/datasets/`; `SOURCES` removed; new `_discover_sources()` scans top-level CSVs (skips `bin/`, `probe/`) and infers label from `material` column. Per-file keep/skip log. |
| `Model/material_rf.pkl`, `Model/scaler_mat_rf.pkl` | Regenerated. |

**Workflow now:** drop a labelled per-packet CSV into `data_logs/datasets/`, re-run trainer. No code edits.

**Metrics:** 5-fold CV **0.838 ± 0.018** (tighter than v3.1's ±0.033). Soft F1 0.933 · Medium F1 0.819 · Hard F1 0.774. Same 203 trials as v3.1.

### 2. Phase B Skeleton — Probe-Phase 1D-CNN

| File | Change |
|---|---|
| `ModelInclude.py` | **New Stage 2.5 probe phase** between Stage 2 and Stage 3 in `run_one_grip()`. Opt-in via `config["PROBE_ENABLED"]`. PWM ramps `−80 → −150` over 2.0 s; 20 Hz bin-mean from first contact; 5 features per row (`shifted_cond`, `delta_pos`, `d_cond_dt`, `d_dpos_dt`, `res_norm`); up to 40 timesteps. PID path untouched. Trial dict gains `probe_records`. Also added `shifted_cond` to `trial_records` (used later in §4). |
| `App.py` | `--probe` CLI flag; threads probe config through; creates `data_logs/datasets/probe/`; writes per-trial probe CSV; imports `MaterialCNNClassifier`; summary CSV gains CNN-probe columns. |
| `MaterialCNNClassifier.py` | **New.** Runtime loader for `Model/material_cnn.keras`. Returns `(None, None)` gracefully if artefacts missing or window < 40 rows. |
| `Code Store/train_material_cnn.py` | **New.** Auto-discovers `data_logs/datasets/probe/`. 5-fold CV; saves `Model/material_cnn.keras` + `scaler_mat_cnn.pkl`. Currently exits cleanly with "collect probe data first" — `Model/material_cnn.keras` does not yet exist. |

### 3. Phase A-prime — 1D-CNN on PID-Grip Data

User chose Option C of the morning's PID-data-for-CNN decision: train a deep alternative to RF v4 on the **same** PID-grip dataset (`data_logs/datasets/*.csv`), not the probe data. Phase B remains scoped separately.

| File | Change |
|---|---|
| `Code Store/train_material_cnn_pid.py` | **New.** Same auto-discovery as RF v4; extracts (40, 5) windows from post-contact rows; same 1D-CNN architecture as Phase B; 5-fold CV + per-class metrics. Saves `Model/material_cnn_pid.keras` + `Model/scaler_mat_cnn_pid.pkl`. |
| `Model/material_cnn_pid.keras`, `Model/scaler_mat_cnn_pid.pkl` | Generated; ~25k-parameter model. |

#### v1 → v2 progression

**v1 (buggy, morning):** 64 trials kept (222 dropped). Reason: trainer's break condition was `bid >= WINDOW_LEN`; `pandas.groupby` doesn't emit empty groups, so any 50 ms bin with no packets in the first 40 bin-slots caused the loop to short-circuit one row early. The 38–39-bin drops were not data shortage — they were the bug.

**v2 (fixed, afternoon):** changed break condition to `len(rows) >= WINDOW_LEN`. **260 trials kept** (Hard 79 / Medium 112 / Soft 69). 5-fold CV **0.942 ± 0.024**. Hard F1 0.929 · Medium F1 0.938 · Soft F1 0.964 · macro F1 0.944. Std barely moved vs v1 (0.024 vs 0.037) despite quadrupling sample size.

#### Final A/B vs RF v4

| Metric | RF v4 | CNN-PID v2 | Δ |
|---|---|---|---|
| 5-fold CV accuracy | 0.838 ± 0.018 | **0.942 ± 0.024** | **+0.104** |
| Macro F1 | 0.842 | **0.944** | +0.102 |
| Hard F1 | 0.774 | **0.929** | +0.155 |
| Medium F1 | 0.819 | **0.938** | +0.119 |
| Soft F1 | 0.933 | **0.964** | +0.031 |
| Trials | 203 | 260 | +57 |

Issue 9 (Hard↔Medium feature overlap) not closed but absolute error count is now small: 15 misclassifications, 9 of which are Hard↔Medium.

### 4. CNN-PID Runtime Wire-Up

| File | Change |
|---|---|
| `MaterialPIDCNNClassifier.py` | **New.** Mirrors `MaterialCNNClassifier.py` shape; loads `Model/material_cnn_pid.keras` + `scaler_mat_cnn_pid.pkl`. Single entry point `classify_pid(trial_records, baseline_res_k)`. Reuses the same 20 Hz bin-mean and first-40-non-empty-bins extraction. Returns `(None, None)` when artefacts missing or fewer than 40 bins. |
| `App.py` | Imports `classify_pid`. `_post_grip_classify` now runs **three** classifiers per grip (RF, CNN-PID, CNN-probe). Summary CSV gains 4 `_cnnpid` columns (12 prediction columns total). Display line: `RF=Hard(...)   CNN-PID=Hard(...)   CNN-probe=-(//)`. |
| `ModelInclude.py` | `trial_records` entries now include `shifted_cond` so the CNN-PID loader doesn't need to recompute it (avoids re-threading `SENSOR_GAIN`). |

Smoke test: `python -c "import MaterialPIDCNNClassifier"` → `CNN-PID loaded — classes=['Hard', 'Medium', 'Soft'], window=40` ✅. Hardware verification deferred to user.

### 5. Back-Test Notebook

`Code Store/backtest_material_classifiers.ipynb` — auto-discovers `data_logs/datasets/`, runs RF v4 and CNN-PID v2 on all 260 surviving trials, plots confusion matrices and per-class F1, writes `Research/backtest_predictions_2026-05-13.csv`.

**In-sample numbers** (will exceed CV; expected):

| | RF v4 | CNN-PID v2 |
|---|---|---|
| Accuracy | 0.954 | **0.996** |
| Macro F1 | 0.954 | **0.996** |
| Hard F1 | 0.988 | **1.000** |
| Medium F1 | 0.948 | **0.996** |
| Soft F1 | 0.925 | **0.993** |

**Agreement: 95.8% (249/260).** Of the 11 disagreements: **CNN-PID right and RF wrong on all 11**; zero cases of RF-right-CNN-wrong. CNN-PID strictly dominates RF on this corpus — the +0.104 CV gap and the 11/0 disagreement asymmetry tell the same story.

### 6. Research Deliverables

| File | Purpose |
|---|---|
| `Research/Material_Classification_Methods_2026-05-13.md` | Article-grade methodology covering data acquisition, pre-processing, baseline correction, feature engineering, window extraction (incl. bin-skip fix), quality filtering, scaling, architectures, training, inference path, metrics, open issues, reproducibility, and version lifecycle. |
| `Research/Material_Classification_Methods_2026-05-13.docx` | DOCX rendering of the same with 6 embedded figures. 376 KB, 208 paragraphs, 9 tables, 46 headings. |
| `Research/backtest_predictions_2026-05-13.csv` | Per-trial predictions and probabilities for RF + CNN-PID (260 rows). For paired McNemar test or ad-hoc inspection. |
| `Research/figures/fig1..6.png` | Example signal trace · class-mean trajectories · confusion matrices · per-class F1 · RF feature importance · example (40, 5) windows per class. |
| `Code Store/generate_report_figures.ipynb` | Idempotent figure generator. |
| `Code Store/build_methods_docx.py` | Markdown → HTML → DOCX builder. Idempotent. |

`python-docx` (and its lxml dependency) installed into the `PyAienv` conda env.

---

## Update Report — 2026-05-13 — Phase B 1D-CNN Plan (Accepted)

> **Status:** Accepted — code steps 2/4/5/6 landed; steps 3 (probe data collection) and 7 (field A/B) await hardware.

Defaults from the plan stand: probe-alongside-PID (not replace), feature Option A (`shifted_cond`, `delta_pos`, `d_cond_dt`, `d_dpos_dt`, `res_norm`), ramp `−80 → −150` over 2.0 s.

**Implementation sequence — current state:**

1. ✅ Accept plan.
2. ✅ App.py / ModelInclude.py Stage 2.5 probe phase.
3. ⏳ Probe data collection (≥ 30 trials/class).
4. ✅ `Code Store/train_material_cnn.py`.
5. ✅ `MaterialCNNClassifier.py` runtime loader.
6. ✅ `App.py` integration (RF + CNN-PID + CNN-probe all wired).
7. ⏳ Field A/B & deploy decision.

Decision points (§5 of the plan) resolved with defaults; the bin-skip fix from §3 above means the probe trainer inherits the corrected window-extraction logic — no follow-up patch needed when probe data arrives.

---

## Issues Resolved

**None closed.** Today's work was forward-progress on Issue 4 (Phase B data gap), but the dependency on probe data collection persists.

Issue 9 (Hard/Medium overlap) is materially reduced by CNN-PID v2 (15 errors vs RF v4's 33) but the structural cause (PID overshoot collapsing the discriminative gap) remains; closing this still requires either the PID-tuning verification (Issue 2) holding `max_force_n` within ±20% of setpoint, or Phase B's probe data sidestepping PID entirely.

---

## Issues Still Open

Carried into `Open Issues 2026-05-13.md` with one notable update:

- **Issue 4 — Material Classifier Data Gap (Phase B portion).** Phase A-prime CNN-PID v2 is now an alternative path that beats RF v4 by 0.104 CV on the existing dataset; Phase B (probe-based) is the *better-physics* path and remains scoped as before. The two paths can be deployed independently.

Issues 2, 3, 8 unchanged from yesterday's snapshot. Issue 9 status updated to reflect CNN-PID v2's mitigation.

---

## Research Data Collected

Today's deliverables, all in `Research/`:

- `Material_Classification_Methods_2026-05-13.md`
- `Material_Classification_Methods_2026-05-13.docx`
- `backtest_predictions_2026-05-13.csv`
- `figures/fig1_example_signal.png`
- `figures/fig2_class_mean_trajectories.png`
- `figures/fig3_confusion_matrices.png`
- `figures/fig4_per_class_f1.png`
- `figures/fig5_rf_feature_importance.png`
- `figures/fig6_example_windows.png`

These are article-track materials, not just internal logs. The DOCX is ready for direct insertion into the paper's methodology section.

---

## Three New Labelled Sessions Captured Today

Auto-discovered into `data_logs/datasets/`:

| File | Class | Approx. trials |
|---|---|---|
| `phase1_20260513_150041.csv` | Soft | ~10 |
| `phase1_20260513_150318.csv` | Medium | ~37 |
| `phase1_20260513_151134.csv` | Hard | ~9 |

These contributed to CNN-PID v2's expanded training set (260 vs v3.1's 203). RF v4's published 0.838 ± 0.018 was computed before these arrived; a refresh of RF v4 on the same 11-file corpus would be the cleanest apples-to-apples baseline before a production swap.
