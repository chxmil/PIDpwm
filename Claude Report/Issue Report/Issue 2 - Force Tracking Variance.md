# Issue Report 2 — Force Tracking Variance Around Setpoint

**Filed by:** Claude Code (claude-opus-4-7)
**Date:** 2026-05-08
**Status:** ⚠️ Open — system functional but inconsistent loop-to-loop
**Severity:** Low (system reaches setpoint on average; variance is the issue)
**Related:** `Claude Report/Daily Report/Daily Report 2026-05-08.md`

---

## TL;DR

After hardware was fixed and sensor reads normally, the system reaches force setpoints (2.5-3.5 N range) but with significant loop-to-loop variance:

- Some loops bottom out at ~122° → force lands **under** target
- Some loops close to 130-158° → force lands **over** target (up to 4.3 N seen against 3.5 N target)

Variance is mostly position-driven, not controller-driven. The PID can hit target accurately when the gripper happens to break through to the right depth, but the breakthrough timing varies between loops.

---

## Evidence

From today's runs (target = 3.5 N for `233339`/`233216`/`233039`):

| File | Loop | MaxForce | MaxPos | Note |
|---|---|---|---|---|
| `233339` | 1 | 3.708 N | 131.74° | slight over |
| `233339` | 2 | **4.269 N** | 132.46° | over |
| `233339` | 3 | 3.665 N | 137.55° | slight over |
| `233216` | 1 | 3.343 N | 158.15° | close |
| `233216` | 2 | 2.681 N | 157.38° | under (despite deep position) |
| `233216` | 3 | **4.326 N** | 129.38° | over |
| `233039` | 1 | 1.491 N | 140.29° | well under |
| `233039` | 4 | 3.643 N | 125.21° | on target |

Ranges seen:
- MaxPos: **121-158°** (37° spread)
- MaxForce: **1.49 - 4.33 N** (~2.8 N spread against a 3.5 N target)
- MinResistance: **216-377 kΩ** (varies with how deep gripper closes)

---

## Hypothesised mechanism

The system has **two-phase behavior** by design (introduced by Report 2 fixes):

1. **Phase 1 — Approach + integral build:** PID + grip floor (`min(target_pwm, −120)`) maintain firm pressure while integral term accumulates. Position holds near initial contact (~109°).
2. **Phase 2 — Mechanical breakthrough:** Once integral pushes PWM below some threshold, the gripper "punches through" to a deeper position (114-158°). Force then lands wherever the new position dictates.

**Where the breakthrough lands is mechanical, not controlled.** Material compliance, sensor placement, and the exact integral value at breakthrough all contribute. The controller has no feedback path to correct the depth — it can only adjust PWM after the fact, but PWM is already saturated by the grip floor.

---

## Proposed mitigations (in order of expected effect)

### 1. Stronger approach to push past the variable breakthrough point
Increase `GRIP_PWM` from −180 → −210 (or −230) so the approach phase reaches deeper into the object before PID engages, reducing the importance of the unpredictable breakthrough event.

**Risk:** harder approach may damage softer materials. May overshoot more on soft objects.

### 2. Adaptive grip floor based on contact phase
Currently `target_pwm = min(target_pwm, −120)` is a fixed minimum. Try:
```python
if force < SETPOINT_FORCE * 0.95:
    floor = -140 if (force < SETPOINT_FORCE * 0.5) else -100
    target_pwm = min(target_pwm, floor)
```
Strong push when far from target, gentler near it. Reduces overshoot.

### 3. Tighter contact threshold
Currently `baseline × 0.93`. Move to `× 0.95` so PID engages at consistent compression depth (later, after gripper has reached firmer contact). Reduces variance at the cost of slower convergence.

### 4. Position-aware feed-forward
Add a feed-forward term proportional to `(setpoint - force) × (some_pos_term)` so the controller pushes harder when it knows the gripper hasn't reached deep enough yet. Requires kinematic model.

---

## Recommended next experiment

Apply mitigation #1 (`GRIP_PWM = -210` or note the user is already at -200) on a fixed test object, run 6 grips at target 3.5 N, measure:

1. MaxForce mean and stdev
2. MaxPos mean and stdev
3. Whether all loops land within ±0.5 N of target

If stdev drops meaningfully, ship it. If not, try mitigation #2.

---

## Why this is low-priority

The system **does** hit target — it's just inconsistent. For a research-stage prototype, ±1 N variance is acceptable. For a production gripper, it's not. Tuning further requires deciding the use-case tolerance.

If retraining the model (Model/Train work) succeeds with better force-position relationship, this issue may resolve itself.

---

## Affected files (no code change yet)

- `App.py` — `GRIP_PWM`, currently −200 (user-tuned)
- `ModelInclude.py::run_one_grip()` — grip floor, contact threshold logic

## Related

- Daily Report 2026-05-08 — full context
- Issue 3 — Saturated sensor not detected (separate concern)
