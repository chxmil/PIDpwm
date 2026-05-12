# Daily Report — 2026-05-11

**Researcher:** Chamil Ahlee
**Author:** Claude Code (claude-opus-4-7)
**Branch:** `fix/force-control`
**Combines:** DevLog 2026-05-11 (Material RF Retrain v2), Update Report 2026-05-11 (Material Classifier v2 Retrain — Accepted), Issue 8 (filed today, open), Issue 9 (filed today, open). Update Report 2026-05-10 (Issue 6 Verification Plan) is also folded in here from the previous-day Update Report folder.
**Carries forward:** Issue 2 (Force Tracking Variance), Issue 3 (Sensor Saturation Python guard), Issue 4 (Phase B classifier data gap), Issue 8 (baseline clamping — new), Issue 9 (Hard/Medium feature overlap — new).
**Closes:** Issue 6 (Classifier Unreliable on New Sessions) — verification completed; residual scope re-filed as Issues 8 and 9.

---

## Headline

The Phase A RF material classifier was retrained on post-firmware-fix labelled data collected today. CV jumped from **0.794 ± 0.111** (v1) to **0.936 ± 0.042** (v2) — but the labelled field verification later the same day landed at **0.714** (Soft 35/35, Medium 13/25, Hard 2/10). v2 is a clear net improvement on Soft (78% → 100%) and Medium (10% → 52%) versus the day-9 pre-fix field run, but a real regression on Hard (29% → 20%). The CV-vs-field gap is upstream of the classifier — Stage-2 baseline calibration is still clamping at 800 kΩ on most trials despite Issue 7's firmware fix, and PID overshoot collapses the Hard/Medium `f_peak` distinction. Both findings filed as new Issues 8 and 9. **Issue 6 closes:** root cause (firmware) was identified and patched, and the classifier-side verification ran; residual error budget now lives in the two new Issues, not in the classifier itself.

**Bottom line of the day:** v2 RF is accepted as the best classifier achievable in the current signal regime. Further gains require fixing baseline calibration (Issue 8) and/or PID overshoot (Issue 9 / Issue 2), not more model retraining.

---

## Changes Made (DevLog roll-up)

### `Code Store/train_material_rf.py` — v2 SOURCES rewrite

Replaced the v1 SOURCES dict (Hard.csv + Medium.csv + Soft (1).csv + Soft (2).csv + Prediction/{Hard,Medium,Soft}/phase1_20260509_*.csv — all pre-firmware-fix and disqualified) with a v2 set pointing at today's post-fix labelled sessions only:

```python
SOURCES = {
    "Hard":   ["phase1_20260511_153751.csv"],
    "Medium": ["Medium (1).csv"],
    "Soft":   ["Soft (3).csv"],
}
```

Docstring updated to mark v2 / 2026-05-11. Feature extraction, RF class, hyperparameters, and saved-artefact format unchanged — so `MaterialClassifier.py` runtime needed no edits.

### `Model/material_rf.pkl`, `Model/scaler_mat_rf.pkl` — v2 artefacts

Overwritten with the v2 RF + scaler. Verified loadable via `MaterialClassifier.py::_load()`. Classes register as `['Hard', 'Medium', 'Soft']`; 5 features; n_estimators=200.

### `Research/material_classifier_RF_baseline_2026-05-09.md` — §Limitations + §Post-Fix Retrain v2 added

- Limitations: day-9 prediction-mode CSVs formally disqualified (Issue 7 regime).
- Post-Fix Retrain v2 section: full CV metrics (0.936 ± 0.042), per-class P/R/F1, confusion matrix, feature importances, field verification (0.714 over 70 trials), discussion of the CV-vs-field gap and how it forwards to Issues 8 and 9.
- Article-reporting recommendation: cite CV as cleaned-data result, cite field as post-fix realistic result, omit day-9 field figures.

### `CLAUDE.md` §1 — model status line

```
Random Forest (5 hand-crafted features) | ✅ Active — runs post-grip;
v2 (2026-05-11): 0.936 ± 0.042 5-fold CV / 0.714 field
(78 train + 70 verif trials). Residual Hard/Medium error tracked
in Issue 8 (baseline clamping) and Issue 9 (PID-overshoot feature overlap).
```

### Files NOT changed

- `MaterialClassifier.py` — same feature extraction, same `classify_trial()` signature. New artefacts plug in via existing `_load()`.
- `ModelInclude.py` — no change.
- `App.py` — no change.
- `Code Store/PIDpwm.ino` — no change (Issue 7 patches from yesterday remain).

---

## Issues Resolved Today

### Issue 6 — Classifier Unreliable on New Sessions — **CLOSED**

- **Root cause:** upstream firmware fault (Issue 7), patched 2026-05-10.
- **Verification done today:** 70 labelled trials on RF v2 → 0.714 combined accuracy. This is the post-fix figure that yesterday's Update Report 2026-05-10 (Issue 6 Verification Plan) was waiting on.
- **Outcome:** Issue 6 closes as a root-cause-identified, root-cause-fixed, classifier-side-best-effort outcome. The residual Hard/Medium error budget that the v2 RF cannot recover is forwarded to Issues 8 and 9 (filed today). Both are about signal quality upstream of the classifier, not about the classifier itself.

---

## Issues Filed Today

### Issue 8 — Stage-2 Baseline Calibration Still Clamping at 800 kΩ — **OPEN**

- **Severity:** Medium — silently corrupts the `res_drop_pct` feature used by the RF; no direct effect on force PID.
- **Symptom:** across both today's training and verification sessions, `baseline_res_k` reads 800.0000 on most trials. Per-packet resistance is healthy post-Issue-7 (400 kΩ–2.3 MΩ depending on contact), so this is **not** an Issue 7 regression — it's a downstream Stage-2 calibration / clamp-ceiling problem.
- **Probable causes:** 800 kΩ clamp ceiling is too low for the new sensor's healthy idle range; or Stage-2 30-sample / 600 ms window is too short.
- **Proposed investigation:** spot-check NewModel (1).csv for adc1 bimodality (rules in/out Issue 7 regression under motor load); read raw R during a true-idle Stage-2 with multimeter on AIN1; land Issue 3's saturation guard; consider widening Stage-2 to 60 samples with trim-mean.
- **Acceptance:** `baseline_res_k` in 400–1500 kΩ on ≥ 80% of trials AND `res_drop_pct` regains a normal spread.

### Issue 9 — Hard/Medium Feature Overlap Under PID Overshoot — **OPEN**

- **Severity:** Medium — caps Hard/Medium recall at ~50% regardless of which classifier (RF, 1D-CNN) trains on top.
- **Symptom:** Hard-train `f_peak` median 13.4 N vs Medium-train median 13.2 N — within 0.25 N. Field Hard recall 2/10 with 8 confident misclassifications as Medium (prob_Medium 0.53–0.84).
- **Root cause:** PID setpoint 2.5 N but actual peak forces 8–17 N (3–7× overshoot, **couples to Issue 2**). Both classes saturate at similar mechanical/force-model ceilings, collapsing the discriminative gap.
- **Proposed paths:** A — re-tune PID (likely reduce `PID_KI` from 22, tighten anti-windup, add integral leakage) so `max_force_n` stays within ±20% of setpoint; B — change features to transient-response shape (`res_slope_after_contact`, `f_settling_pct`, `pwm_under_contact_median`); C — document the limit and propose hardware fix (probe phase per Issue 4, stiffer load cell, higher-res encoder).
- **Acceptance:** Hard ≥ 0.70 AND Medium ≥ 0.70 AND Soft ≥ 0.90 field recall in a labelled 3-class session.

---

## Issues Carried Forward

### Issue 2 — Force Tracking Variance Around Setpoint
- **Status:** Open (carried forward from 2026-05-08).
- **Note today:** Issue 9 is the downstream classification cost of this. Setpoint 2.5 N → field peaks 8–17 N → Hard/Medium force overlap → Hard recall 20%. Tightening PID is now a classifier-accuracy lever, not just a force-control nicety.

### Issue 3 — Sensor Saturation Silent Failure (Python-side guard)
- **Status:** Open. Defence-in-depth fix still designed but not merged.
- **Relevance to today:** would have caught today's clamped-baseline trials (≥ 90% Stage-2 samples ≥ 799.99 kΩ → abort & warn) instead of silently producing 800-baseline training rows. Worth landing in the next code change session.

### Issue 4 — Material Classifier Data Gap (Phase B portion)
- **Status:** Open — Phase A live (v2); Phase B blocked on 20 Hz probe-phase data.

### Issue 8 — Baseline calibration clamping (new today, see above)

### Issue 9 — Hard/Medium feature overlap (new today, see above)

---

## Research Data Collected

Today's classifier work counts as mandatory research-article data:

- **`Research/material_classifier_RF_baseline_2026-05-09.md`** — updated with §Limitations (day-9 data disqualified) and §Post-Fix Retrain v2 (full CV + field metrics, confusion matrices, feature importances, CV-vs-field discussion, article reporting recommendation). This single document now covers v1 baseline + v2 retrain + post-fix verification and is the canonical Phase A artefact for the paper.

No new file added under `Research/` today; the existing baseline doc was extended in place per the §Post-Fix Retrain v2 implementation plan.

---

## File Structure Changes

No directory-level changes today.

- `Code Store/train_material_rf.py` — content updated, path unchanged.
- `Model/material_rf.pkl`, `Model/scaler_mat_rf.pkl` — content updated, paths unchanged.
- `Research/material_classifier_RF_baseline_2026-05-09.md` — content extended, path unchanged.

CLAUDE.md §2 file structure remains accurate (only §1 model-status line was updated, see above).

---

## Verification CSVs (data record)

Field-verification sessions logged in `data_logs/`:

| File pair | Class | Trials | Field accuracy |
|---|---|---|---|
| NewModel (1).csv + (2).csv | Soft   | 35 | 1.000 |
| NewModel (4).csv + (5).csv | Medium | 25 | 0.520 |
| NewModel (3).csv (summary only) | Hard | 10 | 0.200 |
| NewModel (6).csv | (aborted — material=NaN) | 10 | — |

Training-set CSVs from the same day:
- `phase1_20260511_153751.csv` + `_summary.csv` — Hard, 29 trials, 26 kept after quality filter.
- `Medium (1).csv` + `Medium (2).csv` — Medium, 33 trials, 30 kept.
- `Soft (3).csv` + `Soft (4).csv` — Soft, 31 trials, 22 kept.

---

## Next Step

See `Claude Report/Open Issues 2026-05-11.md` for the carried-forward backlog. The most leverage-per-effort next move is **Issue 8 step 1** (spot-check NewModel (1) for adc1 bimodality under motor load) — costs nothing if the firmware fix held, and unblocks Issue 8 investigation if it didn't. Then **Issue 9 Path A** (PID re-tune for tighter overshoot) — likely closes Issue 2 and recovers Hard recall in one fix.
