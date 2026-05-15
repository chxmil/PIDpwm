# Open Issues — Snapshot 2026-05-12 (end of day)

Carried forward from `Open Issues 2026-05-11.md`. Today's v3.1 retrain did not close any of these; v3.1 confirms the diagnosis on Issues 8 and 9.

---

## Issue 2 — Force Tracking Variance Around Setpoint

- **Status:** Open
- **Severity:** Medium — promoted on 2026-05-11 because Issue 9 makes this the upstream cause of Hard/Medium classifier underperformance.
- **Filed:** 2026-05-08
- **Last touched:** 2026-05-12 (PID re-tuned in `App.py`; acceptance criterion not yet measured)

### Summary
Original PID setpoint was 2.5 N; observed `max_force_n` per trial reached 8–17 N in 2026-05-11 sessions (3–7× overshoot). Hard and Medium force distributions overlap almost completely, killing one of the classifier's two top features.

### 2026-05-12 movement
`App.py` constants are now `TARGET_FORCE=3.5 N`, `PID_KP=70`, `PID_KI=20`, `PID_KD=7`, `PID_ALPHA=0.4`. CLAUDE.md §7 brought into sync. **Acceptance criterion not yet measured** — need a labelled session with the new tuning and a check that `max_force_n` stays within ±20% of 3.5 N (i.e. 2.8–4.2 N) per trial.

### Recommended next step
Collect a labelled 3-class session under the current PID values. If `max_force_n` stays in ±20% of setpoint, re-run field verification — Hard recall should recover toward the v2 CV number (0.885) without retraining the RF. Otherwise tighten the anti-windup clamp (±100 → ±30) or add integral leakage.

### Affected files
- `App.py` — `PID_KP`, `PID_KI`, `PID_KD`, `PID_ALPHA`, `TARGET_FORCE` constants
- `ModelInclude.py::run_one_grip()` — anti-windup clamp, integral term

---

## Issue 3 — Saturated Sensor Silent Failure (Python-side guard)

- **Status:** Open — fix designed but not yet merged.
- **Severity:** Medium — promoted on 2026-05-11 because Issue 8 demonstrates Stage-2 is landing on the clamp ceiling silently.
- **Filed:** 2026-05-08
- **Last touched:** 2026-05-11

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

## Issue 4 — Material Classifier Data Gap (Phase B 1D-CNN)

- **Status:** Open — Phase A v3.1 live; Phase B blocked on probe-phase data.
- **Severity:** Blocker for 1D-CNN training; non-blocking for force-control PID.
- **Filed:** 2026-05-09
- **Last touched:** 2026-05-11

### What's resolved
Phase A Random Forest **v3.1 (2026-05-12)** is live, scoring 0.828 ± 0.033 5-fold CV on 203 trials (Hard 70 / Medium 74 / Soft 59). See Daily Report 2026-05-12. **v4 (2026-05-13)** added auto-discovery — same 203 trials, CV 0.838 ± 0.018.

### What's still open
- **Phase B 1D-CNN data gap:** target ≥ 30 trials per class at 20 Hz for the planned `(40, 5)` 1D-CNN.
- **Probe-phase protocol:** not yet implemented in `App.py`. PID-controlled grips do not yield a clean pre-PID deformation trajectory; the 1D-CNN needs that.

### Recommended next step
1. Implement probe-phase mode in `App.py`: after Stage 2 calibration, run a slow constant ramp (e.g. PWM −80 → −150 over 2 s) before PID engages; record trajectory.
2. Collect ~30 trials per class (~90 total) using probe-phase mode.
3. Train Phase B 1D-CNN at `(40, 5)` per the accepted architecture.
4. A/B vs RF v4 baseline; deploy if CNN ≥ RF.

### Affected files
- `App.py` — Stage 2.5 probe-phase insertion, separate CSV
- `Code Store/train_material_cnn.py` (new, when training begins)
- `Model/material_cnn.keras` (new artefact)

---

## Issue 8 — Stage-2 Baseline Calibration Clamping at 800 kΩ

- **Status:** Open
- **Severity:** Medium — silently corrupts `res_drop_pct` feature; no direct effect on force PID.
- **Filed:** 2026-05-11
- **Affected files:** `ModelInclude.py::run_one_grip()` Stage 2; clamp ceiling constant; potentially `Code Store/PIDpwmClaude/PIDpwmClaude.ino`

### Summary
Across 2026-05-11 AND 2026-05-12 sessions, `baseline_res_k` reads 800.0000 on most trials. Per-packet resistance is healthy (400 kΩ–2.3 MΩ) — Issue 7's firmware fix held at the per-packet level. The downstream problem is that healthy idle resistance for the current sensor unit straddles the 800 kΩ Python clamp ceiling, so Stage-2 averages out at the ceiling.

### v3.1 evidence
v3.1 feature importances show `res_drop_pct` at only 0.119 — well below `f_peak`, `stiffness_proxy`, `rise_ms`. Consistent with the feature being saturated for a large fraction of trials.

### Recommended next step
1. Spot-check `data_logs/datasets/NewModel (1).csv` for adc1 bimodality. If present → re-open Issue 7. If absent → proceed.
2. Multimeter on AIN1-to-GND during a true-idle Stage-2 with the gripper open; compare to per-packet R. If R is 800 kΩ–1.5 MΩ matching multimeter, bump the clamp ceiling to 2000 kΩ.
3. Land Issue 3's saturation guard so future regressions are caught at calibration time.
4. Consider widening Stage-2 to 60 samples with top/bottom 10% trim before averaging.

### Acceptance criteria
- `baseline_res_k` in 400–1500 kΩ range on ≥ 80% of trials in a labelled session, AND
- `res_drop_pct` regains a meaningful spread (currently ≈ constant because numerator and denominator both peg to 800).

---

## Issue 9 — Hard/Medium Feature Overlap Under PID Overshoot

- **Status:** Open
- **Severity:** Medium — caps Hard/Medium recall regardless of which classifier sits on top.
- **Filed:** 2026-05-11
- **Couples to:** Issue 2 (Force Tracking Variance) — Issue 9 is its downstream classification cost.
- **Last touched:** 2026-05-12 (v3.1 confirms diagnosis)

### Summary
Training and field `f_peak` distributions for Hard and Medium overlap almost completely. v3.1 confusion matrix: 24/35 misclassifications are Hard↔Medium with high model confidence. The information loss is in the signal, not the model.

### v3.1 evidence
Hard F1 dropped from v3 0.825 to v3.1 0.763 specifically because the added 2026-05-12 Hard captures include several borderline trials (`f_peak` 6–8 N, `stiffness_proxy` overlapping Medium) — exactly the overshoot pattern this issue describes.

### Three solution paths
- **Path A — Fix the PID (preferred).** Re-tune to keep `max_force_n` within ±20% of setpoint. Likely closes Issue 2 simultaneously and lets the existing v3.1/v4 RF recover its CV-equivalent accuracy. PID was re-tuned 2026-05-12 (KP 50→70, KI 22→20, KD 5→7, TARGET_FORCE 2.5→3.5); verification pending.
- **Path B — New feature set.** Switch from peak-force features to transient-response-shape features (`res_slope_after_contact`, `f_settling_pct`, `pwm_under_contact_median`). Drop `delta_pos_max` (importance 0.003 across v3/v3.1/v4).
- **Path C — Accept the limit.** Document in `Research/material_classifier_RF_baseline_2026-05-09.md` and propose a hardware fix (probe phase per Issue 4, stiffer load cell, higher-resolution encoder).

### Acceptance criteria
On a labelled 3-class field session:
- Hard recall ≥ 0.70 AND Medium recall ≥ 0.70 AND Soft recall ≥ 0.90, OR
- Path C documented and signed off as the article's known limitation.

---

## Closed (kept here for cross-reference)

- **Issue 6 — Classifier Unreliable on New Sessions.** Closed 2026-05-11. Root cause: upstream firmware fault (Issue 7), patched 2026-05-10. Classifier-side verification ran 2026-05-12: 70 labelled trials on RF v2 → 0.714 combined accuracy, Soft 1.000 / Medium 0.520 / Hard 0.200. Residual error split into Issue 8 (baseline clamping) and Issue 9 (PID-overshoot feature overlap). Full record: `Claude Report/Daily Report/Daily Report 2026-05-11.md` and the consolidated v2 Retrain section of `Daily Report 2026-05-12.md`.

Report — Using Existing Phase A CSVs to Train the 1D-CNN

  1. The Core Problem in One Sentence

  The CNN would be trained on PID-controlled grip data but at inference time would be fed pre-PID probe-ramp data — two fundamentally different signal 
  distributions.

  This is a distribution shift problem, also called train/inference mismatch. The model would learn the wrong thing.

  ---
  2. Why The Signals Look Different

  What's in the existing CSVs (data_logs/datasets/*.csv)

  Every trial has this timeline (rate ≈ 65 Hz raw):

     ┌─approach─┐  ┌─PID engages here ────────────────────┐
     │PWM=-180  │  │ Force target=3.5N, KP=70, KI=20, KD=7│
     │constant  │  │ + LPF α=0.4, grip floor -120         │
  ───┴──────────┴──┴──────────────────────────────────────┴──── t
     PWM=-180        ↑ contact (is_press=1)        PWM grip end

  After contact, three things hit the time series at once:
  1. Force-tracking overshoot — the PID drives the gripper past TARGET_FORCE, then backs off. Forces of 8–17 N for a 3.5 N setpoint (Issue 2/9).
  2. LPF + grip-floor stitching — PID_ALPHA=0.4 smooths PWM commands and target_pwm = min(target_pwm, −120) forces continued compression. Both produce a
  characteristic step + ramp pattern.
  3. Integral wind-up & 1.8 Hz inference cadence — PWM only updates every 556 ms, so the time series has a visible 1.8 Hz "stair-step" texture.

  What probe data will look like (data_logs/datasets/probe/)

     ┌─approach─┐  ┌─probe ramp ─────────────┐  ┌─Stage 3 (PID)─┐
     │PWM=0     │  │ PWM smoothly -80 → -150 │  │ as before     │
     │baseline  │  │ over 2.0 s, NO PID      │  │               │
  ───┴──────────┴──┴─────────────────────────┴──┴────────────────
                    ↑ contact, window starts

  After contact in probe mode:
  - PWM continues its linear ramp (no PID feedback)
  - No force target, no overshoot, no LPF, no grip floor
  - Compression rate ≈ constant for a given material — Hard plateaus quickly, Soft compresses smoothly through the full window
  - No 1.8 Hz cadence — pure 20 Hz bin-means of the raw stream

  Side-by-side feel for the same material (qualitative)

                      PID-grip window (existing)        Probe window (planned)
  shifted_cond        ▁▃▆█▇▆▇▆▇▆ (overshoot)            ▁▂▃▄▅▆▆▆▆▆ (monotone)
  delta_pos           ▁▂▃▃▃▃▃▃▃▃ (PID limits compression)  ▁▂▃▄▅▆▆▇▇▇ (full ramp)
  d(cond)/dt          steep then oscillating               smooth bell curve
  d(Δpos)/dt          near-zero with PID jitter            slowly decreasing (Hookean-ish)
  res_norm            saturated dynamics                   gradual descent

  These are not "noisy versions of the same thing". They're qualitatively different signals.

  ---
  3. How Bad Is It?

  Severity: Large. Concrete prediction if we did this and deployed the result without retraining:

  What: Training CV accuracy
  Estimate: ~85–95%
  Why: The CNN can absolutely learn to fingerprint PID overshoot patterns on the training distribution. Each class has its own PID signature because the force    
    trajectory depends on stiffness.
  ────────────────────────────────────────
  What: Field accuracy on probe data
  Estimate: 30–50%
  Why: The PID-specific features the CNN locks onto (oscillation amplitude, overshoot timing, 1.8 Hz step pattern) do not exist in probe data. The model would    
    mostly fall back to its bias term.
  ────────────────────────────────────────
  What: Failure mode
  Estimate: Confidently wrong
  Why: Softmax confidence will look high (the input still has 5 channels in the expected shape), but predictions become near-random. The worst kind of bug for a  
    published-result paper.

  Why it matters more than the v2 → v3 CV gap we already saw: v2's 0.936 → 0.714 was a signal-quality gap (firmware artefacts). A train-on-PID / infer-on-probe   
  model would be a categorical mismatch — different physics generating the data.

  ---
  4. Solution Choices

  I see four, ranked from cheapest-but-useless to cleanest-but-slowest. My recommendation is C.

  A. Train on PID data, deploy on probe data

  Don't do this. This is the silent failure described in §3. Listed only so it's explicit.

  B. Pretrain on PID data, deploy when probe data arrives (mark file clearly)

  - What: Write a --source=pid flag on train_material_cnn.py that extracts (40, 5) windows from existing Phase A CSVs. Save as Model/material_cnn_pid.keras       
  (separate file from material_cnn.keras). Runtime ignores it.
  - Pros: Validates the trainer pipeline end-to-end now; gives a baseline number to compare against once probe data exists.
  - Cons: The model has zero deployment value. Effort is mostly architectural sanity checking.
  - When to choose: You want to be sure the training script and CNN architecture work before investing 90 trials of hardware time.

  C. Train a "Phase A-prime" 1D-CNN on existing data — a deep alternative to RF v4 (recommended)

  - What: Repurpose the 1D-CNN architecture, but treat it as a direct competitor to Phase A RF v4 on the existing dataset, not as Phase B. Take the same 203      
  PID-grip trials, extract (40, 5) windows from the post-contact PID period (this is the only signal there is), train, A/B against RF v4 on the same 5-fold CV.   
  - Pros:
    - Uses the data we already have — no hardware time.
    - Honest scientific question: "does a 1D-CNN beat hand-crafted RF features on the same signal?"
    - If it beats RF v4's 0.838 CV, we have an immediate runtime upgrade for the PID-grip path (the same path RF v4 runs on today).
    - Independent of Phase B — the probe-based Phase B CNN is still built later, on its own data.
  - Cons: Doesn't address Issue 9. Still capped by Hard↔Medium overlap under PID overshoot. Best-case CV is roughly RF v4's number plus a few points.
  - When to choose: You want progress today, an honest research result (deep-vs-shallow on the same dataset), AND you keep Phase B on track for separate
  probe-data collection.

  D. Skip — collect probe data first

  - What: Wait for step 3 of the accepted plan. Don't train anything until data_logs/datasets/probe/ has ≥ 30 trials per class.
  - Pros: Cleanest. No risk of confusing the two model files. No "research artefact noise" in the Update Report trail.
  - Cons: Zero CNN progress until hardware time happens. The skeleton sits unused.
  - When to choose: You can run hardware sessions soon (today/tomorrow) and don't need a baseline number first.

  ---
  5. What I'd Pick If You Asked

  Option C. Here's the chain of reasoning:

  1. The existing data is genuinely useful for a different question: does 1D-CNN > RF v4 on the same PID-grip signal? — that's a publishable comparison.
  2. It does not corrupt Phase B (probe-based), which proceeds separately when hardware time arrives.
  3. The runtime stays clean: MaterialClassifier.py keeps using RF (or we swap in the Phase A-prime CNN if it wins the A/B), and MaterialCNNClassifier.py keeps   
  waiting for the probe-trained model.
  4. Pipeline validation comes for free.

  It would mean:
  - New trainer file: Code Store/train_material_cnn_pid.py (or a --source flag).
  - New artefact: Model/material_cnn_pid.keras, separate from the probe-trained model.
  - A small Update Report on whether to swap RF v4 for the deep model on the PID-grip path.

  Tell me B, C, or D and I'll proceed. Or push back on any of the assumptions in §2–3.

  ● Fix + retrain done. The bin-skip bug was the only thing holding back v1.

  Final A/B (fair-sample):

  ┌─────────────┬───────────────┬───────────────┐
  │             │     RF v4     │ 1D-CNN-PID v2 │
  ├─────────────┼───────────────┼───────────────┤
  │ CV accuracy │ 0.838 ± 0.018 │ 0.942 ± 0.024 │
  ├─────────────┼───────────────┼───────────────┤
  │ Trials      │ 203           │ 260           │
  ├─────────────┼───────────────┼───────────────┤
  │ Hard F1     │ 0.774         │ 0.929         │
  ├─────────────┼───────────────┼───────────────┤
  │ Medium F1   │ 0.819         │ 0.938         │
  ├─────────────┼───────────────┼───────────────┤
  │ Soft F1     │ 0.933         │ 0.964         │
  ├─────────────┼───────────────┼───────────────┤
  │ Macro F1    │ 0.842         │ 0.944         │
  └─────────────┴───────────────┴───────────────┘

