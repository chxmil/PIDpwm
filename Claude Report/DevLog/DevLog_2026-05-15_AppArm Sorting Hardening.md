# DevLog — 2026-05-15 — AppArm.py Sorting Compatibility Hardening (A–E)

**Trigger:** user "Apply all A–D now" + follow-up requirement: operator must be
able to manually correct pose drift every time and re-save the corrected pose.
**Files:** `Arm_Control/AppArm.py` (modified, additive), `AppSort.py` (modified).
`App.py` / `ModelInclude.py` / legacy AppArm task + routes — unchanged.

## Changes

| Gap | Change | Where |
|---|---|---|
| **A** | `safe_home()` helper (`'safe'→'home'→'start'`, force-driven). Called on interrupt in `arm_goto`/`arm_grip_gate`/`arm_place` so the arm no longer freezes mid-pose holding an object on abort. | AppArm.py |
| **B** | `SORT_MODE` global + `sort_mode` `/api` command. Joystick btn 0/1/2 and `run_task` are locked out while sort mode is on (returns `{'status':'locked'}`). `AppSort.py` enables it for the whole session and releases on exit. Manual jog is **not** affected. | AppArm.py, AppSort.py |
| **C** | Startup pose check: boot logs missing required sort poses (`start`, `pregrip`, bins) instead of failing mid-cycle after a grip. | AppArm.py |
| **D** | `MATERIAL_BIN` / `REJECT_BIN` overridable from `positions.json` keys `material_bin` / `reject_bin` (defaults retained as fallback) — closes report Q4; bin layout no longer hardcoded. | AppArm.py |
| **E** | `sort_gate(pose_key, step_name)` — confirmation gate **with manual correction**: operator jogs to fix drift (jog is always-on via joystick_thread), btn 8 re-saves the corrected pose to `positions.json[pose_key]` (persists for next run), btn 9 confirms, interrupt aborts. `arm_grip_gate`/`arm_place` now use `sort_gate` instead of bare `wait_for_permission`. `wait_for_permission` (legacy task) left untouched. Web `save_pos` is unchanged and still works for ad-hoc saves. | AppArm.py |

## Why E
User: poses drift from saved position; manual correction is required **every
time**, and the corrected pose must be saveable from the controller so it
sticks. Gap-B lock only blocks the *random task*, never manual jog — so jog +
btn-8 re-save + btn-9 confirm gives correct-and-persist at every gate.

## positions.json contract (researcher must provide on the Pi)
Pose entries: `{"<channel>": angle}` for channels 0,1,2,13,14. Required keys:
`start`, `pregrip`, and bin poses (default `4`,`5`,`6`,`7`). Optional: `safe`
(for safe_home), `material_bin` (dict, e.g. `{"Hard":"4",...}`), `reject_bin`
(string). Corrected poses are written back here by `sort_gate` btn 8.

## Verification
`py_compile` clean: `Arm_Control/AppArm.py`, `AppSort.py`, `GripHold.py`.
On-robot dry-run / E2E still pending (needs Pi + gripper) — report steps 4–5.
