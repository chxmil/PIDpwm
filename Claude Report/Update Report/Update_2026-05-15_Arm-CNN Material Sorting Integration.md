# Update Report — Arm + CNN Material-Sorting Integration

**Date:** 2026-05-15
**Author:** Claude Code
**Status:** Accepted (researcher, 2026-05-15)
**Scope:** New orchestration app to make the Raspberry-Pi arm pick an object, have the
computer grip + classify its material, and place it in a material-specific bin.
**Constraint (from user):** `App.py` and `ModelInclude.py` are in active use and must
**not** be modified. Integration is delivered as a **new application**, not edits to
the running collector.

---

## 1. Goal

Close the loop between the two existing subsystems:

| Subsystem | Host | Role today |
|---|---|---|
| `App.py` + `ModelInclude.py` | Computer (serial COM18 → gripper ESP32) | Force control + post-grip material classification (RF / CNN-PID / CNN-probe). Output is **print + summary CSV only** — it goes nowhere. |
| `Arm_Control/AppArm.py` | Raspberry Pi (Flask:5001, PCA9685 servos) | `task_pick_random_place(source)` picks from a source pose and drops at a **random** bin in `['4','5','6']`. |

**Target behaviour:** arm picks object → computer grips & classifies material →
object is placed in the bin that matches its class (Hard / Medium / Soft) instead
of a random bin.

---

## 2. Why a new app (not edits to App.py)

`App.py` is the live data-collector / force-control entry point and must keep
running unchanged. The classification logic it uses already lives in **importable,
side-effect-free modules**:

- `ModelInclude.run_one_grip(...)` — full grip + PID, returns a trial dict.
- `MaterialPIDCNNClassifier.classify_pid(records, baseline_res_k)` — Phase A-prime CNN, **CV 0.942** (best classifier).
- `MaterialClassifier.classify_trial(...)` — RF v4 fallback (CV 0.838).

A new orchestrator can `import` these directly, drive one serial port, and add the
network coordination — **zero changes to `App.py` / `ModelInclude.py`.**

> Open architectural question (does not block this plan): the haptic gripper
> (ESP32/COM18) and the Pi's servo-15 "Gripper" are two different actuators.
> If the haptic gripper is the arm's real end-effector it must **hold force during
> transport**, which `run_one_grip()` does not do today (Stage 5 auto-releases).
> Two delivery options are given in §5 so the plan stands either way.

---

## 3. Proposed components

### 3.1 New PC app — `AppSort.py` (new file, project root)
Thin orchestrator. Responsibilities only:
- Open the gripper serial port (reuse `App.py`'s `SerialPort` + `parse_sensor`,
  or import them — no edits to `App.py` required, just import).
- For each object: call `run_one_grip(...)` exactly as `App.py` does, then run
  `classify_pid` (primary) with `classify_trial` (fallback) on the returned
  `trial_records`.
- Pick the material label, map it to a bin, and drive the arm over HTTP.
- CLI: `--port COM18 --arm-host 192.168.50.244:5001 --material-map Hard:4,Medium:5,Soft:6`
  (Pi address confirmed by researcher).

`AppSort.py` contains **no grip logic** — it calls `run_one_grip()`. This honours
the CLAUDE.md rule ("all grip logic stays in `ModelInclude.py`") because the new
file only orchestrates I/O and network calls.

### 3.2 New Pi endpoints in `AppArm.py` (additive only)
Add to the existing `/api` POST handler — **existing commands untouched**:

| New cmd | Payload | Action | Blocking? |
|---|---|---|---|
| `arm_goto` | `{target:'start'\|'<pre-grip>'\|...}` | `move_to_pose_sequential(saved_positions[target])` — move to a pre-saved pose, no random. | returns when move done |
| `arm_grip_gate` | `{label}` | Move to the object's **pre-grip** pose, then `wait_for_permission("Ready to grip")` — **blocks until the operator confirms via joystick btn 9**, then returns `{status:'confirmed'}`. | **blocks on human** |
| `arm_place` | `{material:'Hard'}` | Map material→bin via `MATERIAL_BIN`, carry held object to its **pre-release** pose, then `wait_for_permission("Ready to drop")` — **blocks until confirm**, returns `{status:'at_bin', bin:'4'}`. | **blocks on human** |
| `status` | `{}` | Return `SYSTEM_STATE` / gate state so the PC can poll if a non-blocking client is used. | instant |

Pose names used: per object the researcher pre-saves **`start`**, a
**pre-grip** pose, and a **pre-release** pose (researcher confirmed they will set
these). The two `wait_for_permission` gates are the existing AppArm mechanism —
**reused, not replaced**. Gated endpoints use a long PC-side HTTP timeout (no
fixed 2 s) because they intentionally wait for a human; only `status`/`arm_goto`
use the 2 s safety timeout.

Plus one module-level constant in `AppArm.py`:
```python
MATERIAL_BIN = {'Hard': '4', 'Medium': '5', 'Soft': '6'}  # configurable
```
The legacy `task_pick_random_place` and joystick path stay as-is for manual use.

### 3.3 Transport: HTTP over LAN
The Pi already serves Flask on `0.0.0.0:5001`. The PC posts JSON with the
`requests` library (add to env). No new broker, no sockets to write. A 2 s
network timeout on every call → on failure the PC sends `PWM:0` and the Pi
endpoint sets `SYSTEM_STATE='INTERRUPT'` (safe stop).

---

## 4. Orchestration sequence (PC = master) — SEMI-AUTOMATIC, NOT autonomous

**Confirmed requirement:** `wait_for_permission()` (AppArm.py:158) stays. The
operator must confirm (joystick **button 9** → `CONFIRMATION_RECEIVED`) before
**every grip** and before **every release/drop**. The PC orchestrator does not
bypass these gates — gated HTTP calls **block** until the Pi reports the gate
cleared (or interrupt). There is no fully autonomous run.

Per object, `AppSort.py` runs (★ = human confirmation gate on the Pi):

1. `POST /api {cmd:'arm_goto', target:'start'}` → arm to the object's start pose.
2. `POST /api {cmd:'arm_goto', target:'<pre-grip>'}` → arm to that object's
   pre-grip pose. **★ Pi runs `wait_for_permission("Ready to grip")`** — call
   blocks until operator confirms via joystick.
3. **PC-local:** `GripHold.grip_and_hold(ser, ..., config)` → approach + PID to
   `TARGET_FORCE`, then **hold** (PID keeps maintaining force; no auto-release).
4. **PC-local:** `label = classify_pid(...) or classify_trial(...)` on the
   captured `trial_records` (Phase A-prime CNN-PID first, RF v4 fallback).
5. `POST /api {cmd:'arm_place', material:label}` → arm carries the held object to
   that material's bin (its pre-release pose). **★ Pi runs
   `wait_for_permission("Ready to drop at <bin>")`** — blocks until confirm.
6. **PC-local:** `GripHold.release(ser, config)` → Stage-5 release drops the object.
7. `POST /api {cmd:'arm_goto', target:'start'}` → loop next object.

Fallback: `label is None` (no contact / short window) → carry to a configurable
**reject** bin (still gated by confirmation at step 5) instead of aborting.

---

## 5. The one real decision — how the object is held during transport

`run_one_grip()` grips **and then releases** (Stage 5) inside one call. To carry
an object the hold must outlast the move. Because we cannot edit `ModelInclude.py`,
two options:

**Option A — Pi servo-15 jaw carries (no PC code constraint touched).**
Sequence: arm closes servo-15 on the object → PC runs `run_one_grip()` purely to
**sense + classify** (haptic gripper presses, reads, releases as normal) → arm
transports with servo-15 → `arm_release` opens servo-15 at the bin.
*Pros:* zero risk to the force-control code; only additive Pi endpoints + new PC app.
*Cons:* the classified grip and the carrying grip are different actuators; needs
the object reachable by both, or the haptic press done first then servo-15 closes.

**Option B — Haptic gripper carries (needs a hold-capable grip).**
Requires a grip that maintains force until externally released. Since
`ModelInclude.py` is frozen, deliver this as a **new sibling module**
`GripHold.py` (new file) implementing approach → PID-hold → external release,
reusing the same constants/formulae documented in CLAUDE.md §6–§7. `AppSort.py`
calls `GripHold` instead of `run_one_grip` only for the carry case; classification
still uses the existing classifier modules.
*Pros:* true haptic pick-and-place.
*Cons:* a new grip implementation must be validated against the CLAUDE.md spec
(sign rule, integral anti-windup, 1.8 Hz, conductance shift).

**DECISION (researcher, 2026-05-15): Option B.** The haptic gripper (COM18) is
the arm's real end-effector and physically carries the object. Therefore a
**hold-capable grip is required** and is delivered as the new sibling module
`GripHold.py` — `App.py` / `ModelInclude.py` remain untouched. `GripHold.py`
must reproduce the CLAUDE.md §5–§7 contract exactly: conductance shift +
`SENSOR_GAIN`, dynamic latching `is_press`, 1.8 Hz inference, the
`clip(-pid_output, -255, 0)` sign rule, integral-after-contact + `±100`
anti-windup. It adds: (a) a **hold phase** that keeps the PID loop maintaining
`TARGET_FORCE` after reaching setpoint, ended only by an external
`release()` call (not a timer); (b) a hold watchdog/timeout for safety; (c)
returns the same `trial_records`/`baseline_res_k` shape the classifiers expect.

---

## 6. Material → bin mapping

Single source of truth, configurable, default:

| Material | Bin pose | Notes |
|---|---|---|
| Hard | `4` | from `positions.json` |
| Medium | `5` | |
| Soft | `6` | |
| (unknown / None) | configurable reject | classifier returned `(None, None)` |

Defined once in `AppArm.py` (`MATERIAL_BIN`) and overridable from `AppSort.py`
via `--material-map`. Replaces `random.choice(['4','5','6'])` **only in the new
`arm_place` path** — the old random task is left intact.

---

## 7. Classifier selection

Use **Phase A-prime CNN-PID** as primary (`classify_pid`, CV 0.942, Hard F1
0.929 / Medium 0.938 / Soft 0.964 per CLAUDE.md §1). Fall back to **RF v4**
(`classify_trial`, CV 0.838) when CNN-PID returns `None` (post-contact window
< 40 bins). Log both predictions + probabilities per object to a new
`data_logs/sort_log_<ts>.csv` for audit (does not touch existing summary CSV).

---

## 8. Failure / safety handling

| Failure | Detection | Response |
|---|---|---|
| Grip never reaches force | `contact_detected == False` | skip place, arm → home, log |
| Classifier `None` | `(None,None)` from both | place in reject bin |
| Pi unreachable / HTTP timeout | `requests` exception (2 s) | PC sends `PWM:0`; Pi `arm_goto` failures set INTERRUPT |
| User stop | existing Pi joystick btn14 / `cmd:'stop'` | both sides safe-stop |

---

## 9. File-structure impact (for CLAUDE.md §2 after acceptance)

```
+ AppSort.py                         # NEW — PC orchestrator (arm ↔ grip ↔ classify)
+ GripHold.py                        # NEW — hold-capable grip (Option B, confirmed)
~ Arm_Control/AppArm.py              # MODIFIED — additive endpoints + MATERIAL_BIN
+ Arm_Control/positions.json         # runtime file on Pi (start/pre-grip/pre-release/bins)
+ data_logs/sort_log_<ts>.csv        # NEW — per-object sort audit log
  App.py, ModelInclude.py            # UNCHANGED (constraint)
```

---

## 10. Implementation Sequence (applied 2026-05-15)

1. ✅ **DONE** — `GripHold.py` built: `grip()` (approach + PID, exits on force-settle or `GRIP_DURATION`) → background `_hold_loop()` (maintains force + 20 Hz PWM resend, `HOLD_TIMEOUT` safety) → `release()` Stage-5. Reuses `ModelInclude` model/scalers; sign rule + ±100 anti-windup + LPF + −120 floor copied verbatim from spec.
2. ✅ **DONE** — `Arm_Control/AppArm.py`: added `status` / `arm_goto` / `arm_grip_gate` / `arm_place` + `MATERIAL_BIN` + `REJECT_BIN`, reusing `wait_for_permission` (state set RUNNING→STOP so it actually blocks). Legacy joystick/random task + all existing routes unchanged.
3. ✅ **DONE** — `AppSort.py`: imports `SerialPort`/`parse_sensor` from `App.py` (no edits), `GripHold`, classifiers; stdlib `urllib` HTTP client (600 s gated / 5 s quick); CNN-PID→RF fallback; `data_logs/sort_log_<ts>.csv`; `_safe_cycle` safe-stop. `App.py`/`ModelInclude.py` untouched.
4. ⏳ **PENDING (needs Pi+gripper)** — Dry-run: pose moves + both confirmation gates + reject-bin path.
5. ⏳ **PENDING (needs Pi+gripper)** — End-to-end on-robot test: per-class sort accuracy + hold stability during transport.
6. ✅ **DONE** — CLAUDE.md §2 updated (AppSort/GripHold/MaterialPIDCNNClassifier/sort_log + AppArm note + App/ModelInclude marked FROZEN); DevLog `DevLog_2026-05-15_Arm-CNN Material Sorting.md` written.

**Static verification:** `py_compile` clean on `GripHold.py`, `AppSort.py`,
`Arm_Control/AppArm.py`. Steps 4–5 require the physical Pi + gripper and the
researcher's pre-saved poses (`start`, `pregrip`, bins `4/5/6/7`).

---

## 11. Open questions for the researcher

1. **End-effector:** is the haptic gripper (COM18) the arm's real jaw, or is
   Pi servo-15 the carrying jaw? → selects Option A vs B (§5).
   End-effector is the haptic gripper COM18
2. **Pi network address** of the Raspberry Pi on the lab LAN (for `--arm-host`).
   ip address of the raspberry pi is: 192.168.50.244
3. **Source poses:** are object pick poses pre-saved in `positions.json`, or is
   the joystick "manual adjust + confirm" step still required per object? Fully
   autonomous sorting needs pre-saved source poses (skip the manual confirm).
   I will set position for start, pre-grip and pre-release for each object
4. **Reject-bin** pose id for unclassifiable objects.  *(still open)*
  try 3 time with no per mission, if False grip without move arm until 3 time if still no pick to bin i will provide position

### Resolved by researcher — 2026-05-15
- **Q1 End-effector:** haptic gripper (COM18) → **Option B** (`GripHold.py`, hold-capable).
- **Q2 Pi address:** `192.168.50.244:5001`.
- **Q3 Poses:** researcher pre-saves **start / pre-grip / pre-release** per object.
- **Confirmation gate (researcher emphasis):** **NOT autonomous** —
  `wait_for_permission` is **mandatory before every grip and every drop**; reused
  as-is. The orchestrator blocks on these gates (§4).
- **Still open:** Q4 reject-bin pose id (proceed with a configurable default,
  e.g. bin `7`, until specified).
  4. **Reject-bin** pose id for unclassifiable objects.  *(still open)*
  Q4 Answer:try 3 time with no per mission, if False grip without move arm until 3 time if still no pick to bin i will provide position

---

*Status: **Accepted** (researcher, 2026-05-15). The plan/design is approved and
this report has moved to `Claude Report/Update Report/`. No code has been written
yet — implementation is deferred until the `Apply` command, at which point the
Implementation Sequence is filled, a DevLog is written, and CLAUDE.md §2 is
updated.*
