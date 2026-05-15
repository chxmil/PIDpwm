# Open Issues — Snapshot 2026-05-15 (end of day)

Carried forward from `Open Issues 2026-05-13.md`. **Today's work
(Arm-CNN material sorting) did not touch any of these** — it is a new feature
orthogonal to force control and classifier accuracy. Statuses are unchanged
from 2026-05-13; see `Daily Report/Daily Report 2026-05-13.md` for full
analysis of each.

---

## Issue 2 — Force Tracking Variance Around Setpoint
- **Status:** Open · Medium · last touched 2026-05-12.
- PID re-tuned (`TARGET_FORCE=3.5`, `KP=70`, `KI=20`, `KD=7`); acceptance
  criterion (`max_force_n` within ±20% of setpoint) not yet measured on a
  controlled labelled session.
- Files: `App.py` PID consts; `ModelInclude.py::run_one_grip()` anti-windup.

## Issue 3 — Saturated Sensor Silent Failure (Python-side guard)
- **Status:** Open · Medium · fix designed, not merged.
- Abort grip when ≥90% of Stage-2 samples sit at the 800 kΩ clamp; bundle
  header flush + clamp logging.
- Files: `ModelInclude.py::run_one_grip()` Stage 2; `App.py::main()`.

## Issue 4 — Material Classifier Data Gap
- **Status:** Open · blocker for Phase B 1D-CNN only.
- RF v4 (CV 0.838) and CNN-PID v2 (CV 0.942) done; Phase B skeleton complete
  and awaits ≥30 probe trials/class in `data_logs/datasets/probe/`.
- Files: Phase B trainer/loader (done); `Model/material_cnn.keras` (pending data).

## Issue 8 — Stage-2 Baseline Calibration Clamping at 800 kΩ
- **Status:** Open · Medium.
- `baseline_res_k` reads 800.0 on most trials; corrupts `res_drop_pct`.
  Needs multimeter check → possibly raise clamp ceiling to ~2000 kΩ; land
  Issue 3 guard.
- Files: `ModelInclude.py::run_one_grip()` Stage 2; clamp constant.

## Issue 9 — Hard/Medium Feature Overlap Under PID Overshoot
- **Status:** Open · Medium · partially mitigated by CNN-PID v2.
- Structural `f_peak` overlap persists; CNN-PID v2 (trajectory shape) reduces
  Hard↔Medium error materially. Field A/B + clean-PID verification pending.
- Couples to Issue 2.

---

## New follow-up (opened 2026-05-15) — non-blocking

### Sorting on-robot validation
- **Status:** Open · non-blocking · feature complete, untested on hardware.
- `Update_2026-05-15_Arm-CNN Material Sorting Integration` Implementation
  Sequence **steps 4–5** still pending: dry-run (pose moves + both confirm
  gates + reject path) and end-to-end on the Pi + gripper (per-class sort
  accuracy + hold stability during transport).
- Prerequisites: researcher saves `positions.json` poses (`start`/`pregrip`/
  `safe` + bins `4/5/6/7`); the Pi must run the **current `AppArm.py`** (with
  `status`/`confirm`/`sort_mode`/`arm_safe` endpoints), not an older `app.py`.
- Files: `AppSort.py`, `GripHold.py`, `Arm_Control/AppArm.py`,
  `Arm_Control/templates/index.html`.

---

## Closed (cross-reference)
- **Issue 6** — Classifier Unreliable on New Sessions. Closed 2026-05-11
  (root cause = firmware Issue 7, patched 2026-05-10; residual split into
  Issues 8 & 9).
