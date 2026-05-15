# Daily Report — 2026-05-15

**Researcher:** Chamil Ahlee | Walailak University
**Theme of the day:** Arm + CNN material-sorting integration (PC grips &
classifies, Raspberry-Pi arm sorts the object into its material bin) — design,
implementation, hardening, web console, and motion-logic correction.

---

## Summary

The material classifier was previously a dead end — predictions only printed
and went to a summary CSV. Today closed the loop end-to-end: a new PC
orchestrator drives the Pi arm over HTTP so a gripped, classified object is
physically sorted into a per-material bin, fully semi-automatic (operator
confirms every grip and drop).

Two Update Reports were accepted and applied; six DevLogs recorded. `App.py`
and `ModelInclude.py` were **never modified** (hard constraint) — all grip
logic for sorting lives in the new `GripHold.py`. Work progressed through:
plan → accept → apply → compatibility hardening (A–E) → user-friendly web
console → UTF-8 bug fix → and a corrected, researcher-specified authentic
motion cycle with exact per-servo ordering.

No force-control or classifier-accuracy issues were addressed today; the
carried-forward issues (2, 3, 4, 8, 9) are orthogonal to this feature and
remain open.

---

## Changes Made

### New files
- **`GripHold.py`** — hold-capable grip for sorting. `grip()` (approach + PID
  to setpoint, settle detection) → background hold thread (maintains force +
  20 Hz PWM so the ESP32 keeps holding during transport; `HOLD_TIMEOUT`
  safety auto-release) → `release()` Stage-5 drop. Reuses `ModelInclude`'s
  loaded model/scalers (single TF load, byte-identical inference). Reproduces
  CLAUDE.md §5–§7 verbatim (conductance shift + `SENSOR_GAIN`, latching
  `is_press`, 1.8 Hz, `clip(-pid_output,-255,0)`, ±100 anti-windup, LPF, −120
  grip floor).
- **`AppSort.py`** — PC orchestrator. Imports `SerialPort`/`parse_sensor`
  from `App.py` (no edits), `GripHold`, and the classifiers. stdlib `urllib`
  HTTP client (gated calls 600 s, quick calls 5 s). CNN-PID primary → RF v4
  fallback. `data_logs/sort_log_<ts>.csv` audit. `_safe_cycle` safe-stop.
  Commands `1`/`a`/`q`.
- **`SORTING_MANUAL.md`** — engineering/setup guide (positions.json schema,
  CLI, architecture, safety).
- **`DASHBOARD_MANUAL.md`** — end-user guide for the web console.

### Modified
- **`Arm_Control/AppArm.py`** (additive only — legacy task / joystick jog /
  existing routes / `wait_for_permission` all untouched):
  - Endpoints: `arm_goto`, `arm_grip_gate`, `arm_place`, `arm_safe`,
    `status`, `sort_mode`, `confirm`.
  - `MATERIAL_BIN`/`REJECT_BIN` config-driven via `positions.json`.
  - `safe_home()` (interrupt → known pose), `sort_gate()` (jog-correct +
    btn-8 persist + confirm), startup required-pose check.
  - `GATE_PROMPT`/`LAST_SORT` web state; `status` returns
    gate/awaiting/last_sort/sort_mode.
  - **Authentic motion** (researcher spec): `_move_channels`, `_resolve_safe`,
    `move_to_safe_ordered`; cycle `Safe→Pregrip→Predict→Safe→Bin→Safe`; to
    pregrip `0>1>2>14`→perm→`13`→perm→grip; to safe `13>14>2>1>0`; to bin
    `0>1>2>14>13`→perm→release.
- **`Arm_Control/templates/index.html`** — rebuilt as the **Material Sorting
  Console**: 1 Hz live status (state/sort_mode/last-sorted), pulsing gate
  banner, web CONFIRM + EMERGENCY STOP, labelled teach-pose buttons, legacy
  task collapsed. Rewritten **pure ASCII** after a cp1252 byte (`0x85`) broke
  the Jinja2 UTF-8 loader on the Pi.
- **`CLAUDE.md`** §2 — file structure kept in sync (new files, AppArm
  endpoints, templates, positions.json contract, App/ModelInclude marked
  FROZEN).

### Verification
`py_compile` clean on `GripHold.py`, `AppSort.py`, `Arm_Control/AppArm.py`
throughout. `index.html` byte-scanned pure ASCII. **No on-robot run yet** —
end-to-end test needs the Pi + gripper (integration report steps 4–5).

### Git
Pushed to `main` across the day: `e1d5614` (pre-apply checkpoint), `09111e2`
(apply), `0084e2d` (hardening A–E), `7a5100a` (SORTING_MANUAL), `10580cc`
(console UI), `5db9959` (UTF-8 fix), `1316795` (DASHBOARD_MANUAL), `8bc7c54`
(superseded safe-retract), `f056cce` (authentic cycle + servo order).

---

## Issues Resolved

No previously-tracked Issue Reports were resolved today (today's scope was a
new feature, orthogonal to the open force/classifier issues).

Internal to the sorting feature, the following were identified **and fixed
same-day** (not separate Issue Reports):
- Compatibility/safety gaps A–E (no safe-home on abort; legacy task
  collision; late pose validation; hardcoded bins; no drift-correct-and-save
  at gates) — all fixed.
- `index.html` UnicodeDecodeError on the Pi — fixed (pure ASCII).
- Incorrect transit logic (straight pre-grip→bin sweep, then a wrong
  safe-retract guess) — corrected to the researcher's authentic
  `Safe→Pregrip→Predict→Safe→Bin→Safe` cycle with exact per-servo ordering.

---

## Issues Still Open

Carried forward unchanged from `Open Issues 2026-05-13.md` (today's work did
not touch force control or classifier accuracy):

- **Issue 2** — Force Tracking Variance Around Setpoint (Open; PID re-tune
  acceptance not yet measured on a labelled session).
- **Issue 3** — Saturated Sensor Silent Failure (Open; Python-side guard
  designed, not merged).
- **Issue 4** — Material Classifier Data Gap (Open; Phase B 1D-CNN needs ≥30
  probe trials/class).
- **Issue 8** — Stage-2 Baseline Calibration Clamping at 800 kΩ (Open).
- **Issue 9** — Hard/Medium Feature Overlap Under PID Overshoot (Open;
  partially mitigated by CNN-PID v2).

New non-blocking follow-up opened by today's feature:
- **Sorting on-robot validation** — integration report Implementation
  Sequence steps 4–5 (dry-run + end-to-end on the Pi + gripper) still pending;
  needs researcher's `positions.json` poses and the deployed `AppArm.py`.

Full detail in `Open Issues 2026-05-15.md`.

---

## Research Data Collected

None. Today's deliverables are an integration feature + tooling + docs, not
article-quality benchmark/metric data. Nothing added to `Research/`.

---

*Archived from 6 DevLogs and 2 accepted Update Reports dated 2026-05-15.
Source report files cleared per the `today` protocol; this file is the
authoritative record for the day.*
