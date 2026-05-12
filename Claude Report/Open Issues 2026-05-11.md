# Open Issues — Snapshot 2026-05-11 (end of day)

After today's daily-clear, the following items remain open. **Issue 6 closed today** (verification ran on RF v2 against 70 labelled trials; root cause was the upstream firmware fault, now patched; residual classifier-side error split into new Issues 8 and 9). The 2026-05-11 Daily Report contains the full context.

---

## Issue 2 — Force Tracking Variance Around Setpoint

- **Status:** Open
- **Severity:** Medium (was Low) — promoted because Issue 9 shows it is the upstream cause of the Hard/Medium classifier underperformance.
- **Filed:** 2026-05-08
- **Last touched:** 2026-05-11 (see Daily Report — Issue 9 couples here)

### Summary
PID setpoint is 2.5 N, but `max_force_n` per trial in today's labelled sessions reached 8–17 N — i.e. 3–7× overshoot. Variance is large enough that Hard and Medium force distributions overlap almost completely, killing one of the classifier's two top features.

### Note 2026-05-11
This is no longer a "force-control nicety" — it is the dominant cause of the Hard/Medium misclassification documented in Issue 9. Re-tuning PID is now the most leverage-per-effort fix for both Issue 2 *and* Issue 9 simultaneously.

### Recommended next step (revised today)
Path A in Issue 9: reduce `PID_KI` from 22 → 8–12, tighten anti-windup clamp from ±100 → ±30, or add integral leakage. Acceptance target: `max_force_n` stays within ±20% of setpoint on a labelled session. If achieved, re-run the 3-class field verification — Hard recall should recover toward the v2 CV number (0.885) without retraining the RF.

### Affected files
- `App.py` — `PID_KP`, `PID_KI`, `PID_KD`, `PID_ALPHA` constants
- `ModelInclude.py::run_one_grip()` — anti-windup clamp, integral term

---

## Issue 3 — Saturated Sensor Silent Failure (Python-side guard)

- **Status:** Open — fix designed but not yet merged.
- **Severity:** Medium (was Low) — promoted because Issue 8 demonstrates Stage-2 calibration is currently landing on the clamp ceiling silently. The guard would catch this.
- **Filed:** 2026-05-08
- **Last touched:** 2026-05-11 (see Daily Report — Issue 8 is the failure pattern this guard was designed for)

### Summary
When ≥ 90% of Stage-2 calibration samples are at the 800 kΩ clamp ceiling, abort the grip with a clear warning instead of silently proceeding with a corrupt baseline.

### Proposed fix (~10 min including verification)
Insert into `ModelInclude.py::run_one_grip()` Stage 2, after baseline computation:

```python
sat_count = sum(1 for r in res_samples if r >= 799.99)
if res_samples and sat_count >= len(res_samples) * 0.9:
    print(f"  ❌ SENSOR SATURATED — {sat_count}/{len(res_samples)} samples at 800 kOhm clamp.")
    print("     Likely causes: tactile sensor disconnected, broken wires,")
    print("     ADS1115 acquisition issue (see Issue 7/8), ADC reading near rail.")
    print("     Aborting grip. Fix the sensor and try again.")
    ser.write("PWM:0")
    return 0
```

### Also worth bundling
- **A.** `csv_file.flush()` after writing the header.
- **B.** Per-packet logging when `res_k > 800` is clamped (warn at end of grip if > 50% clamped).
- **C.** Make the initial verify check abort or pause instead of warning-and-continuing.

### Affected files
- `ModelInclude.py::run_one_grip()` — Stage 2 calibration
- `App.py::main()` — CSV header flush

---

## Issue 4 — Material Classifier Data Gap (Phase B portion only)

- **Status:** Open — **Phase A v2 is live**; Phase B still blocked on probe-phase data.
- **Severity:** Blocker for 1D-CNN training; non-blocking for force-control PID.
- **Filed:** 2026-05-09
- **Last touched:** 2026-05-11

### What's resolved
Phase A Random Forest v2 (2026-05-11) is live in `App.py` runtime, trained on a cleaned 78-trial dataset, scoring **0.936 ± 0.042** 5-fold CV / **0.714** field accuracy on a 70-trial labelled verification. Confusion matrices, per-class metrics, and feature importances are captured in `Research/material_classifier_RF_baseline_2026-05-09.md` §Post-Fix Retrain v2.

### What's still open
- **Phase B (1D-CNN) data gap:** target is ≥ 30 trials per class at 20 Hz for the planned `(40, 5)` 1D-CNN.
- **Probe-phase protocol** (Issue 4 Possibility B): not yet implemented in `App.py`. The 1D-CNN needs a clean, comparable pre-PID deformation trajectory per material; today's PID-controlled grips don't yield this.

### Recommended next step
1. Implement probe-phase mode in `App.py`: after Stage 2 calibration, run a slow constant ramp (e.g. PWM −80 → −150 over 2 s) before PID engages; record trajectory.
2. Collect ~30 trials per class (~90 total) using probe-phase mode.
3. Train Phase B 1D-CNN at `(40, 5)` per the architecture in the accepted plan.
4. A/B vs RF v2 baseline; deploy if CNN ≥ RF.

### Affected files
- `App.py` — Stage 2.5 probe-phase insertion, separate CSV
- `Code Store/train_material_cnn.py` (new, when training begins)
- `Model/material_cnn.keras` (new artefact)

---

## Issue 8 — Stage-2 Baseline Calibration Still Clamping at 800 kΩ

- **Status:** Open (filed today)
- **Severity:** Medium — silently corrupts `res_drop_pct` feature; no direct effect on force PID.
- **Filed:** 2026-05-11
- **Affected files:** `ModelInclude.py::run_one_grip()` Stage 2; `App.py` / `ModelInclude.py` clamp ceiling constant; potentially `Code Store/PIDpwm.ino`

### Summary
Across 2026-05-11 training AND verification sessions, `baseline_res_k` reads 800.0000 on most trials. Per-packet resistance is healthy (400 kΩ–2.3 MΩ depending on contact) — Issue 7's firmware fix held at the per-packet level. The downstream problem is that healthy idle resistance for the current sensor unit straddles the 800 kΩ Python clamp ceiling, so Stage-2 calibration averages out at the ceiling.

### Probable causes (ranked)
1. Clamp ceiling too low for this sensor's healthy idle range (likely).
2. Stage-2 30-sample / 600 ms window too short (possible).
3. Bimodal residue from Issue 7 returning under motor load (low probability; ruled in/out by spot-checking `NewModel (1).csv` for adc1 alternation).

### Recommended next step
1. Spot-check `data_logs/NewModel (1).csv` for adc1 bimodality. If present → re-open Issue 7. If absent → proceed.
2. Multimeter on AIN1-to-GND during a true-idle Stage-2 with the gripper open; compare to per-packet R. If R is 800 kΩ–1.5 MΩ matching multimeter, bump the clamp ceiling to 2000 kΩ.
3. Land Issue 3's saturation guard so future regressions are caught at calibration time.
4. Consider widening Stage-2 to 60 samples with top/bottom 10% trim before averaging.

### Acceptance criteria
- `baseline_res_k` in 400–1500 kΩ range on ≥ 80% of trials in a labelled session, AND
- `res_drop_pct` regains a meaningful spread (currently ≈ constant because the numerator and denominator both peg to 800).

---

## Issue 9 — Hard/Medium Feature Overlap Under PID Overshoot

- **Status:** Open (filed today)
- **Severity:** Medium — caps Hard/Medium recall at ~50% regardless of which classifier sits on top.
- **Filed:** 2026-05-11
- **Couples to:** Issue 2 (Force Tracking Variance) — Issue 9 is its downstream classification cost.
- **Affected files:** `App.py` (PID), `ModelInclude.py::run_one_grip()` (PID), `MaterialClassifier.py` + `Code Store/train_material_rf.py` (feature set, only if Path B)

### Summary
Training and field `f_peak` distributions for Hard and Medium are nearly identical (training medians 13.4 vs 13.2 N — within 0.25 N). Of 8 field-misclassified Hard trials, all 8 went to Medium with high confidence (prob_Medium 0.53–0.84). The information loss is in the signal, not the model — no supervised classifier can recover separability that's physically absent.

### Root cause
PID setpoint 2.5 N → field max forces 8–17 N (3–7× overshoot). Both classes saturate at similar mechanical / force-model ceilings, collapsing the discriminative gap.

### Three solution paths
- **Path A — Fix the PID (preferred).** Re-tune to keep `max_force_n` within ±20% of setpoint. Likely closes Issue 2 simultaneously and lets the existing v2 RF recover its CV-equivalent accuracy.
- **Path B — New feature set.** Switch from peak-force features to transient-response-shape features (`res_slope_after_contact`, `f_settling_pct`, `pwm_under_contact_median`). Drop the dead `delta_pos_max` (importance 0.000).
- **Path C — Accept the limit.** Document in `Research/material_classifier_RF_baseline_2026-05-09.md` and propose a hardware fix (probe phase per Issue 4, stiffer load cell, higher-resolution encoder).

### Acceptance criteria
On a labelled 3-class field session:
- Hard recall ≥ 0.70 AND Medium recall ≥ 0.70 AND Soft recall ≥ 0.90, OR
- Path C documented and signed off as the article's known limitation.

---

## Closed today (kept here for cross-reference)

- **Issue 6 — Classifier Unreliable on New Sessions.** Closed 2026-05-11. Root cause: upstream firmware fault (Issue 7), patched 2026-05-10. Classifier-side verification ran today: 70 labelled trials on RF v2 → 0.714 combined accuracy, Soft 1.000 / Medium 0.520 / Hard 0.200. v2 RF accepted as the best classifier achievable in the current signal regime. Residual Hard/Medium error budget split into Issue 8 (baseline clamping) and Issue 9 (PID-overshoot feature overlap). Full record: `Claude Report/Daily Report/Daily Report 2026-05-11.md` and `Claude Report/Update Report/Update_2026-05-11_Material Classifier v2 Retrain.md` (Accepted).
