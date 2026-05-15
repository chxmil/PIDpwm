# DevLog — 2026-05-15 — Arm-CNN Material Sorting (Apply)

**Trigger:** `Apply Update_2026-05-15_Arm-CNN Material Sorting Integration`
**Report:** `Claude Report/Update Report/Update_2026-05-15_Arm-CNN Material Sorting Integration.md` (Accepted)
**Pre-change checkpoint:** commit `e1d5614` (pushed to `main`)

## What changed

| File | Type | Change |
|---|---|---|
| `GripHold.py` | **NEW** | Hold-capable grip. Three phases: `grip()` (Stage 1-4, exits when force settles ≥0.95×setpoint for `HOLD_SETTLE_TICKS`, fallback `GRIP_DURATION`), background `_hold_loop()` (keeps PID maintaining force + resends PWM @20 Hz so the ESP32 watchdog holds the object during arm transport; `HOLD_TIMEOUT` safety auto-release), `release()` → Stage-5 drop. Reuses `model/scaler_X/scaler_y` imported from `ModelInclude` (single TF load, byte-identical inference). Reproduces CLAUDE.md §5–§7 contract: conductance shift + `SENSOR_GAIN`, latching `is_press` @ 0.93×baseline, 1.8 Hz, `clip(-pid_output,-255,0)` sign rule, integral-after-contact ±100, LPF, −120 grip floor. |
| `AppSort.py` | **NEW** | PC orchestrator. Imports `SerialPort`/`parse_sensor` from `App.py` (no edits), `GripHold`, and the runtime classifiers. `ArmClient` = stdlib `urllib` JSON-over-HTTP (no new dependency); gated calls use 600 s timeout, moves/status 5 s. Per-object cycle: `arm_goto start` → `arm_grip_gate pregrip` (blocks on human) → `gh.grip()` → classify (CNN-PID primary, RF v4 fallback) → `arm_place <material>` (blocks on human) → `gh.release()` → loop. Writes `data_logs/sort_log_<ts>.csv`. `_safe_cycle` drops the object + stops the arm on any comms/cycle error. Commands `1`/`a`/`q`. |
| `Arm_Control/AppArm.py` | **MODIFIED (additive)** | Added module constants `MATERIAL_BIN={'Hard':'4','Medium':'5','Soft':'6'}` and `REJECT_BIN='7'`. Added `/api` commands `status`, `arm_goto`, `arm_grip_gate`, `arm_place` — each reuses `move_to_pose_sequential` + (for the two gates) `wait_for_permission` **unchanged**, sets `SYSTEM_STATE` RUNNING→STOP around the move so `wait_for_permission` actually blocks (it early-returns when state is STOP). Legacy `task_pick_random_place`, joystick path, and all existing routes are untouched. |

## Not changed (constraint honoured)
`App.py` and `ModelInclude.py` — **zero edits**. All grip logic stays in
`ModelInclude.run_one_grip` (live system) / `GripHold` (sorting); `AppSort.py`
contains no grip math, only orchestration.

## Verification
`py_compile` clean on all three files (`GripHold.py`, `AppSort.py`,
`Arm_Control/AppArm.py`). No hardware/E2E run yet — see report Implementation
Sequence steps 4–5 (dry-run + on-robot test) which require the Pi + gripper.

## Open / follow-up
- Q4 reject-bin pose id: defaulted to `'7'` (`REJECT_BIN`), configurable.
- Researcher must pre-save Pi poses: `start`, `pregrip`, and bins `4/5/6/7`
  in `positions.json` before a real run.
- `requests` was planned in the report; implemented with stdlib `urllib`
  instead to avoid an environment change (functionally equivalent).
