# DevLog - 2026-05-15 - Authentic cycle + per-servo order (corrected)

**Trigger:** user correction (my earlier SAFE-retract guess was wrong; the
prior DevLog_2026-05-15_Safe Retract Before Bin is superseded by this).
Researcher-specified motion logic.
**Files:** `Arm_Control/AppArm.py`, `AppSort.py`. Docs synced.

## Spec implemented (researcher)
- **Cycle:** `Safe -> Pregrip -> Predict -> Safe -> Bin -> Safe`
- **To pregrip:** ch `0>1>2>14` -> permission -> ch `13` -> permission -> grip
- **To safe:** ch `13>14>2>1>0` (retract, no permission)
- **To bin:** ch `0>1>2>14>13` -> permission -> release
  (researcher chose "forward, one confirm")

## AppArm.py
- New ordered movers: `_move_channels(pose, channels)` (servo-by-servo,
  interrupt-aware), `_resolve_safe()` (safe->home->start), `move_to_safe_ordered()`
  (`SAFE_ORDER=[13,14,2,1,0]`). Constants `PREGRIP_ORDER_COARSE=[0,1,2,14]`,
  `PREGRIP_ORDER_FINE=[13]`, `BIN_ORDER=[0,1,2,14,13]`.
- `arm_grip_gate`: coarse move (0,1,2,14) -> `sort_gate` (permission 1, jog +
  btn-8 save) -> fine move (13, re-read pose in case it was saved) ->
  `sort_gate` (permission 2) -> confirmed. Interrupt -> `safe_home()`.
- `arm_place`: `move_to_safe_ordered()` (retract while carrying) -> bin move
  `[0,1,2,14,13]` -> one `sort_gate` -> `LAST_SORT`. Interrupt -> `safe_home()`.
- New `arm_safe` command: `move_to_safe_ordered()` then STOP.
- Legacy `move_to_pose_sequential`, joystick, routes, Gap A-E, wait_for_permission
  unchanged. `App.py`/`ModelInclude.py` frozen.

## AppSort.py
- `ArmClient.safe()` -> `arm_safe`.
- `run_cycle`: `arm.safe()` -> `arm.grip_gate(pregrip)` (2 perms) ->
  `gh.grip()` (Predict) -> `arm.place()` (retract Safe -> Bin, 1 perm) ->
  `gh.release()` -> `arm.safe()`. `--start-pose` now unused (Pi resolves safe).

## Docs synced
`AppSort.py` docstring; `SORTING_MANUAL.md` §5 (new cycle table + servo
orders, `--start-pose` marked unused); `DASHBOARD_MANUAL.md` flow + Steps 4-7
(two pregrip confirms, one bin confirm).

## Verification
`py_compile` clean: `Arm_Control/AppArm.py`, `AppSort.py`. On-robot retest
pending (integration report steps 4-5).

## Note
Two confirm gates now occur at pregrip (coarse, then wrist) and one at the
bin. The web CONFIRM button + joystick btn 9 both clear each gate; btn 8
still persists the corrected pose at any gate.
