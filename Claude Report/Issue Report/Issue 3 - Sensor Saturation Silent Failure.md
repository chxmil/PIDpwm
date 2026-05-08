# Issue Report 3 — Saturated Sensor Silent Failure

**Filed by:** Claude Code (claude-opus-4-7)
**Date:** 2026-05-08
**Status:** ⚠️ Open — known footgun, fix proposed but not yet merged
**Severity:** Medium (causes hours of wrong-direction debugging when it strikes)
**Related:** `Claude Report/Daily Report/Daily Report 2026-05-08.md` (Section 5)

---

## TL;DR

When the tactile sensor is disconnected, broken, or open-circuit, the resistance reading saturates above the 800 kΩ clamp limit. The current code silently clamps every reading to exactly 800 kΩ, including all 30 calibration samples. The system then runs a "successful" grip with `baseline = 800 kΩ`, threshold = 744 kΩ, and `is_press` never triggers because every reading is also 800. Force predictions plateau because the model receives baseline-only input.

This caused the entire afternoon's debugging arc: we attributed the resulting "force ceiling" to PID tuning, then to model rate mismatch, before discovering the sensor was the actual problem.

---

## Reproduction

Disconnect the tactile sensor (or pull either voltage-divider wire). Run `python App.py`. Observe:

1. Stage 2 prints 30 calibration samples, all `800.00 kOhm` — no warning.
2. `Baseline : 800.00 kOhm` printed normally.
3. `Threshold : 744.00 kOhm (93%)` — a threshold that can never be reached because clamp ceiling is 800.
4. Grip runs through full duration. `Contact: NEVER DETECTED` printed at the end (the only existing warning) but force predictions sit near zero throughout.
5. CSV looks like a valid grip log. Resistance column shows the actual saturated values (e.g., 13 MΩ raw) which is the *only* place the failure is visible.

---

## Why the current "no contact" warning isn't enough

`run_one_grip` already prints:

```python
if not detected:
    print(f"  ⚠️  Resistance never fell below {threshold_res_k:.2f} kOhm")
    print("      Check: sensor connection and baseline calibration")
```

But this fires *after* the full grip duration (8 s), with the gripper having actuated against nothing. The user has already wasted a trial. And if running `auto` mode, dozens of trials.

Also, this message blames "sensor connection or baseline calibration" generically — it doesn't distinguish between "sensor is dead" and "object too rigid to register".

---

## Proposed fix (was attempted on 2026-05-08, reverted during a separate hang issue)

Detect saturation explicitly during Stage 2 calibration, abort early:

```python
# After collecting res_samples:
sat_count = sum(1 for r in res_samples if r >= 799.99)
if res_samples and sat_count >= len(res_samples) * 0.9:
    print(f"  ❌ SENSOR SATURATED — {sat_count}/{len(res_samples)} samples at 800 kOhm clamp.")
    print("     Likely causes: tactile sensor disconnected, broken wires,")
    print("     voltage divider issue, or ADC reading near rail.")
    print("     Aborting grip. Fix the sensor and try again.")
    ser.write("PWM:0")
    return 0   # caller treats this as "no packets"
```

The threshold `0.9` (90% of samples saturated) leaves room for transient noise without false-positives.

---

## Why this fix was reverted

During testing, the user hit a separate issue where the system appeared to "stop moving" (turned out to be ESP32 not streaming any packets, plus the Stage 4 infinite hang bug fixed separately). To eliminate Claude's changes as a possible cause, both fixes (Bug 1 = CSV header flush, Bug 2 = saturation detection) were reverted to get back to a known baseline.

The Bug 1 fix is uncontroversial and trivial to re-add. The Bug 2 fix needs deliberate re-introduction.

---

## Recommended action

Re-apply the saturation detection in a small dedicated commit, separate from any other changes, on `fix/force-control`:

1. Edit `ModelInclude.py` Stage 2 (between baseline computation and threshold derivation)
2. Add the 7-line block above
3. Test by physically disconnecting the sensor and running one grip — confirm the abort message appears and the program returns to the input prompt without actuating the motor
4. Commit with message `fix: detect sensor saturation during baseline calibration`

Cost: ~10 minutes including verification. Future debugging time saved if sensor ever flakes again: hours.

---

## Also worth adding (separate small fixes)

### A. Flush CSV header immediately
```python
csv_writer.writerow([...])
csv_file.flush()    # Ensure header is on disk even if user exits before any grip
```
Prevents the "0-byte CSV" mystery we saw today.

### B. Distinguish "saturated" from "valid baseline"
In the per-packet path, log whenever `res_k > 800` is clamped. Could add a counter and warn at end of grip if >50% of samples were clamped.

### C. Heartbeat warning if no packets received during initial verify
The current check prints `WARNING: no data!` once, then proceeds anyway. Make it abort or pause.

---

## Affected files

- `ModelInclude.py::run_one_grip()` — Stage 2 calibration block
- `App.py::main()` — CSV header write (for fix A)

## Related

- Daily Report 2026-05-08 (full debugging arc)
- Issue 1 (closed: rate-mismatch hypothesis was a red herring caused by this issue)
- Issue 2 (separate: tracking variance under correct sensor)
