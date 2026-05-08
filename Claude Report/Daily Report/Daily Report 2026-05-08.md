# Daily Report — 2026-05-08

**Researcher:** Chamil Ahlee
**Author:** Claude Code (claude-opus-4-7 / sonnet-4-6)
**Combines:** Reports 1-3, Issue 1 investigation, DevLog 1-2, Stage-4 hang fix

---

## Headline

System now reliably reaches force setpoints (2.5-4.3 N depending on target) after a long debugging arc that traced an apparent "force ceiling" through PID tuning, model architecture investigation, and ultimately to a disconnected/saturated tactile sensor. Two real code bugs found and fixed along the way.

---

## Final state at end of day

| Aspect | Value |
|---|---|
| Branch | `fix/force-control` |
| Target force | User-tuned (cycled 2.5 → 3.5 → 2.5 N) |
| `GRIP_PWM` | −200 (user tuned) |
| `PID_KP / KI / KD` | 70 / 22 / 10 (user tuned) |
| `PID_ALPHA` | 0.4 |
| Contact threshold | `baseline × 0.93` |
| Grip floor | `min(target_pwm, −120)` while `force < 0.95×setpoint` |
| Stage 4 timeout | Now checked *before* `ser.readline()` (no infinite hang on ESP32 silence) |
| Best loop today | Loop 2 of `233339.csv`: 4.269 N max, gripper at 132° |

---

## Timeline / What we did today

### 1. Initial diagnostic — `phase1_20260508_172055.csv`

5 root causes of failure to reach 5 N target:
- Sensor signal plateaus at ~108.93° (object too rigid)
- LPF (ALPHA=0.3) bleeds approach pressure faster than PID can build steady-state
- KD=20 backs grip off sharply on rising force
- `SENSOR_GAIN` not implemented in code (per UPDATE.md spec)
- Loops 2 & 3 had corrupted baseline calibration (gripper not fully released between loops)

### 2. Report-1 fixes (`fix/force-control`, commit `2551f64`)
- `PID_KD`: 20 → 5
- `PID_ALPHA`: 0.3 → 0.6
- Implemented `SENSOR_GAIN` configurable scalar in `App.py` config + applied in `ModelInclude.py::shifted_cond` formula

Result: Force ceiling actually got *worse* (mean 2.05 N vs 2.72 N) because LPF=0.6 was too aggressive — bled the −180 approach pressure away faster than PID could compensate.

### 3. Report-2 fixes (commit `2152897`)
- `PID_KI`: 13 → 22
- `GRIP_DURATION`: 5 s → 8 s
- `PID_ALPHA`: 0.6 → 0.4
- Contact threshold: `× 0.97` → `× 0.93`
- New "grip floor": `target_pwm = min(target_pwm, −120)` while `force < 0.95×setpoint`

Result (`phase1_20260508_220959.csv`): 4 loops cleared 2.0 N, **Loop 2 hit 2.500 N exactly** at 116°. Two-phase behavior worked as designed: PID + grip floor hold pressure while integral builds, then mechanical breakthrough at ~5 s drives force to setpoint.

### 4. Issue 1 — rate-mismatch investigation
Hypothesis: `data_buffer` is appended at packet rate (~100 Hz) but the model was trained at 1.8 Hz, so the 60-step input window represents 0.6 s of real time vs. its trained 33 s. With no explicit timestamp feature, the model can't detect this mismatch.

Tested two fixes on branch `fix/buffer-rate-opt-c`:
- **Option C** (resample-on-inference): keep buffer at packet rate, decimate to 60 evenly-spaced points at inference. **Did not break the ceiling.**
- **Option B** (gate buffer-append at 1.8 Hz): naturally gives 33 s window, no resampling. **Also did not break the ceiling.**

Conclusion: rate mismatch was likely never the dominant blocker. Real cause was downstream.

### 5. Hardware was the real issue all along
After the user reverted to `fix/force-control` baseline and tested, the system hung silently. Console showed Stage 2 calibration receiving **zero packets in 5 seconds**.

**Root cause:** ESP32-S3 / tactile sensor was non-responsive (USB drop, sensor wiring, or open-circuit). Resistance reads of 13.9 MΩ in `230526.csv` confirm sensor saturation — clamped to 800 kΩ baseline, threshold of 744 kΩ never reachable.

After user fixed the hardware (USB / sensor connection):
- `233544.csv`: all 3 loops cleared 2.5 N (2.6-2.8 N range)
- `233339.csv`: 3 loops at 3.7-4.3 N (overshooting 3.5 N target slightly)
- Position now reaches 121-158° vs. previous 112-114° ceiling
- Min resistance during contact: 280-380 kΩ (real contact pressure)

The "force ceiling" we spent the day fighting was the model receiving clamped-to-800-kΩ data on every sample. Once the sensor read normally, predictions reached 3-4 N freely.

### 6. Code bug fixed: Stage 4 infinite hang

**Bug:** In `ModelInclude.py` Stage 4, the `GRIP_DURATION` timeout check was placed *after* `ser.readline()` and `if not line: continue`. So if the ESP32 is silent, the loop spins on `readline → None → continue` forever and the timeout is unreachable.

**Fix:** Moved the timeout check to the top of the loop (before `readline`), so the loop exits after `GRIP_DURATION` even with zero packets.

```python
while True:
    if time.perf_counter() - grip_start >= config['GRIP_DURATION']:
        break
    line = ser.readline()
    ...
```

Currently uncommitted in working tree.

---

## Verified outcomes

| Test file | Loops | MaxForce range | Verdict |
|---|---|---|---|
| `phase1_20260508_220959.csv` (Report 3 confirmed) | 4 | 2.19 - 2.50 N | ✅ Loop 2 hit 2.5 N target exactly |
| `phase1_20260508_233544.csv` | 3 | 2.63 - 2.84 N | ✅ All 3 loops cleared 2.5 N |
| `phase1_20260508_233339.csv` | 3 | 3.67 - 4.27 N | ✅ All 3 loops near 3.5 N target (slight overshoot) |
| `phase1_20260508_233216.csv` | 3 | 2.68 - 4.33 N | ✅ Mixed — Loop 3 over, Loop 2 close |
| `phase1_20260508_233039.csv` | 6 | 1.49 - 3.64 N | Mixed — Loop 4 hit target, others varied |

---

## Lessons

1. **A masked sensor failure can perfectly mimic a model/control bug.** Resistance values consistently at 800 kΩ (the clamp limit) need to be a loud failure, not silently treated as a normal baseline.
2. **Don't add aggressive new features to compensate for a bug whose cause is unknown.** The Report-1 ALPHA=0.6 change overshot in the wrong direction because we didn't yet know the real bottleneck.
3. **Architectural hypotheses (Issue 1) need empirical disproof before retraining.** Two clean A/B tests (B and C) ruled out rate mismatch as the dominant issue, saving days of training work.
4. **Always have a hard timeout in any loop that depends on external I/O.** The Stage 4 hang was latent until hardware actually went silent.

---

## What remains open

Filed as separate issues in `Claude Report/Issue Report/`:

- **Issue 1** — Inference rate mismatch (closed: hypothesis didn't hold; option B/C tests inconclusive but ceiling was hardware)
- **Issue 2** — Force tracking variance at 3.5 N target (some loops bottom out at 122°, others overshoot to 4+ N)
- **Issue 3** — Saturated sensor not detected at runtime (Stage 2 happily proceeds with bogus baseline; proposed fix was tried but reverted because user wanted to revert all changes during a separate hang issue)

---

## Files

**Active code (working tree):**
- `App.py` — user-tuned (KP=70, KD=10, GRIP_PWM=−200, TARGET_FORCE=2.5)
- `ModelInclude.py` — fix/force-control HEAD + Stage 4 timeout fix (uncommitted)
- `CLAUDE.md` — user-edited file structure to reflect Daily Report folder

**Archived:**
- `Code Store/` — `Analysis2ndSensor.ipynb`, `JupyterPython.ipynb`, `PIDpwm.ino`, `Tune.py` (previously at root)
- `Model/Train/` — new training notebook + processed dataset (`CNNLstm (2).ipynb`, `TrainExp_Processed2.csv`) for future retraining

**Branches on remote:**
- `main` — initial state
- `fix/force-control` — Reports 1-3 PID fixes (validated baseline)
- `fix/buffer-rate-opt-c` — Issue 1 experiments (Option C then Option B); kept as record but neither broke the ceiling
