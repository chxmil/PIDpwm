# Update Report — AppArm.py Sorting Compatibility Hardening

**Date:** 2026-05-15
**Author:** Claude Code
**Status:** Accepted (applied — user "Apply all A–D now" + manual-correction requirement)
**Resolves:** compatibility/safety gaps A–E identified after applying
`Update_2026-05-15_Arm-CNN Material Sorting Integration`.
**Constraint:** additive only. `App.py`, `ModelInclude.py`, legacy
`task_pick_random_place`, joystick jog, and all existing routes unchanged.

## Summary
The core sorting integration worked, but `AppArm.py` had four gaps vs the new
flow (no safe-home on abort; legacy random task reachable mid-sort; poses
validated late; bin map hardcoded) and a fifth requirement surfaced by the
researcher: **pose drift must be manually correctable and re-savable at every
gate**. All five (A–E) are now implemented.

## What was accepted & applied
- **A — safe-home on abort:** `safe_home()` (`safe→home→start`) runs on
  interrupt in `arm_goto`/`arm_grip_gate`/`arm_place`. No more mid-pose freeze.
- **B — legacy task lockout:** `SORT_MODE` + `sort_mode` command; joystick
  btn 0/1/2 and `run_task` locked while sorting. `AppSort.py` toggles it for
  the session. Manual jog unaffected.
- **C — startup pose check:** missing required poses warned at boot.
- **D — config-driven bins:** `MATERIAL_BIN`/`REJECT_BIN` read from
  `positions.json` (`material_bin`/`reject_bin`), defaults as fallback —
  closes Q4 of the integration report.
- **E — correct-and-persist gate:** `sort_gate()` lets the operator jog to
  fix drift every time, btn 8 re-saves the corrected pose to
  `positions.json[pose_key]`, btn 9 confirms. `wait_for_permission` (legacy)
  untouched; web `save_pos` still works.

## Implementation Sequence (applied 2026-05-15)
1. ✅ `safe_home()` + wired into 3 endpoints' interrupt paths.
2. ✅ `SORT_MODE` global, `sort_mode` cmd, `status` reports it, joystick +
   `run_task` guarded; `AppSort.ArmClient.sort_mode()` on start / finally.
3. ✅ Boot-time `REQUIRED_SORT_POSES` presence log.
4. ✅ `material_bin`/`reject_bin` loaded from `positions.json` with fallback.
5. ✅ `sort_gate()` (jog + btn-8 persist + btn-9 confirm) replaces bare
   `wait_for_permission` in `arm_grip_gate`/`arm_place`.
6. ✅ DevLog `DevLog_2026-05-15_AppArm Sorting Hardening.md`; CLAUDE.md §2 synced.
7. ⏳ On-robot dry-run + E2E (needs Pi + gripper) — carried with integration
   report steps 4–5.

## Verification
`py_compile` clean: `Arm_Control/AppArm.py`, `AppSort.py`, `GripHold.py`.

## Researcher action before a real run
Provide on the Pi `positions.json` with channel→angle poses for: `start`,
`pregrip`, bins (`4/5/6/7` or your `material_bin`), optional `safe`. Corrected
poses are written back automatically via `sort_gate` btn 8.
