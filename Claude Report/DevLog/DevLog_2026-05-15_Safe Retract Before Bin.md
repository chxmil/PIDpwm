# DevLog - 2026-05-15 - Authentic transit: SAFE retract before bin

**Trigger:** user feedback - "you should move back to safe before push it to
bin and move by servo by servo."
**File:** `Arm_Control/AppArm.py` (`arm_place`). Docs synced.

## Problem
`arm_place` moved the arm **directly from the pre-grip pose to the bin pose**
while carrying the object. This is wrong vs the authentic motion logic: the
legacy `task_pick_random_place` always returns to `home` *before* going to the
destination, so the arm lifts/retracts clear of the workspace instead of
sweeping straight across (collision / object-knock risk).

## Fix
In `arm_place`, before moving to the bin: retract to the SAFE pose first,
**then** move to the bin. Both moves use `move_to_pose_sequential`, which
already drives **servo-by-servo** (channel order `[0,14,13,2,1]` with eased
single-channel moves) - satisfying "move by servo by servo".

```
pre-grip --(grip+hold by PC)--> [SAFE retract] --> [bin] --> drop gate --> release
```

SAFE pose resolution: first present of `safe` -> `home` -> `start` in
`positions.json` (same precedence as `safe_home()`). Interrupt is re-checked
between the SAFE move and the bin move so STOP/btn-14 still aborts cleanly
(then `safe_home()` runs as before).

Unchanged: grip gate, classification, Gap A-E behaviour, legacy task,
`wait_for_permission`, all existing routes. `App.py`/`ModelInclude.py` frozen.

## Docs synced
`AppSort.py` per-cycle docstring, `SORTING_MANUAL.md` §5,
`DASHBOARD_MANUAL.md` flow diagram + Step 6.

## Verification
`py_compile` clean on `Arm_Control/AppArm.py`. On-robot retest pending
(integration report steps 4-5).
