# Issue Report 1 — Inference Rate Mismatch & Missing Timestamp

**Filed by:** Claude Code (claude-opus-4-7)
**Date:** 2026-05-08
**Status:** ⚠️ Open — bug suspected, fix proposed but not yet validated
**Severity:** Medium-High (model may be operating off-distribution; force predictions may be capped artificially)

---

## TL;DR

The CNN-LSTM force model was trained at **1.8 Hz** (60 timesteps × 556 ms ≈ 33 s of context per sequence) but in production the rolling input buffer is **filled at packet rate (~100 Hz)**, so the model is fed a window that represents only **~0.6 seconds of real time** — a temporal scale ~55× smaller than its training distribution.

Because the model has no explicit timestamp feature, it cannot detect this rate mismatch and silently produces predictions in the wrong regime. This may explain the consistent ~2.5-2.7 N force ceiling observed across multiple PID tuning iterations (Reports 1 - 3).

---

## Background

### Model spec (from `CLAUDE.md`)

| Property | Value |
|---|---|
| Architecture | CNN-LSTM regression |
| Input shape | `(1, 60, 2)` |
| Features | `[shifted_conductance, is_press]` |
| **Training sample rate** | **1.8 Hz** |
| Implied context window | 60 / 1.8 ≈ **33 seconds** |
| Output | Force in Newtons |

### How the buffer is currently filled (`ModelInclude.py` Stage 4)

```python
while True:
    line = ser.readline()                # ESP32 packet @ ~100 Hz
    ...
    data_buffer.append([shifted_cond, float(is_press)])   # ← every packet

    if model is not None and (now - last_infer) >= INTERVAL:   # 1.8 Hz inference
        buf_list = list(data_buffer)
        # ...
        pred = model(scaled, training=False)
```

`data_buffer` is a `deque(maxlen=60)`. It is appended **every packet** (~100 Hz), but only the most recent 60 entries are read at inference time.

### The bug

```
60 samples / 100 Hz = 0.6 s of real time
```

Training context: 33 s. Inference context: 0.6 s. Ratio: 55×.

Because the model uses **sequence position as an implicit time index** (no explicit timestamp feature), it has no way to detect that the window's time scale is wrong. Hidden-state dynamics learned at one tick = 556 ms are being applied to data sampled at one tick = 10 ms.

---

## Symptoms (consistent with this hypothesis)

1. **Force predictions ceiling around 2.5 - 2.7 N** across all PID configurations (Reports 1 - 3) regardless of mechanical compression depth.
2. **Predictions feel "stepwise"** — force jumps from ~1.4 N to ~2.5 N once mechanical breakthrough happens, with little intermediate behavior.
3. **Loop 6 of the Report-2 test reached 6.5 N briefly** when contact detection was delayed and the gripper closed deeper than usual — suggesting the model CAN predict higher forces when the buffer happens to contain genuinely deeper compression history.
4. **shifted_cond delta from baseline maxes out around 0.0018** (vs. training distribution that likely covered a wider range over 33 s of dynamic gripping).

These symptoms are also consistent with mechanical sensor saturation, but the rate-mismatch hypothesis is independent and additive.

---

## Why the model isn't completely broken

Even with off-distribution input:

- `shifted_cond` is a quasi-stationary feature — its instantaneous value carries information independent of time
- `is_press` is a step function — once latched, recent 0.6 s and recent 33 s look identical
- The CNN front-end is convolutional and partially scale-tolerant
- The LSTM may have learned more from local patterns than long-range dependencies

So predictions are **plausible but bounded** — likely operating in a degraded regime rather than failing outright.

---

## Three fix options

### Option A — Decouple PID from inference (no model change)

Keep inference at 1.8 Hz; run PID at packet rate (100 Hz) using the most recent held force value.

**Pros:** zero risk to model; trivial code change.
**Cons:** does NOT fix the rate-mismatch bug — model still sees 0.6 s window.

### Option B — Down-sample buffer fill to 1.8 Hz (FIXES the bug)

Append to `data_buffer` only at the 1.8 Hz cadence so the 60-step window represents the trained 33 s context.

```python
last_buffer_append = grip_start
...
if (now - last_buffer_append) >= INTERVAL:
    data_buffer.append([shifted_cond, float(is_press)])
    last_buffer_append = now
```

Same change required for `prefill_buffer` in `App.py`.

**Pros:** restores correct training distribution; ~5-line change; no retraining.
**Cons:** inference still at 1.8 Hz (does not enable faster control).

### Option C — Resample-on-inference (most flexible)

Keep buffer at packet rate (3300 entries = 33 s × 100 Hz), but at inference time resample to 60 evenly-spaced points.

```python
data_buffer = deque(maxlen=3300)
data_buffer.append([shifted_cond, float(is_press)])   # every packet

# Inference (can fire at any rate):
buf = list(data_buffer)
if len(buf) >= 60:
    idx = np.linspace(0, len(buf)-1, 60).astype(int)
    model_input = np.array([buf[i] for i in idx])
else:
    model_input = pad_then_use(buf)
```

**Pros:** inference can run at 100 Hz with correct temporal scale; smoother control.
**Cons:** more code; 33 s warmup needed (partially covered by prefill); resampling-by-pick loses information (could average instead).

---

## Verification plan

To confirm the rate-mismatch hypothesis is real and not just a theoretical concern:

1. **Verify training rate.** Inspect `Analysis2ndSensor.ipynb` (and any prior training scripts) for the exact resampling step. Confirm whether training data was 1.8 Hz directly, or 100 Hz down-sampled later.
2. **A/B test Option B.** Implement on a new branch (`fix/buffer-rate`), run 5 grips on the same object/sensor combo, compare:
   - Max force per loop
   - Force trajectory shape (smooth vs. stepwise)
   - Predicted force at known mechanical positions
3. **If Option B improves predictions,** commit it. The bug is real.
4. **If Option B makes things worse,** the training was actually at packet rate and `CLAUDE.md` is misleading — update docs to reflect true training rate, and the current code is correct.

---

## Long-term recommendation

For the next training cycle (when retraining the force model or training the material classifier):

1. **Add an explicit timestamp feature** — e.g. `dt_since_first_sample` or `dt_since_contact` as a third input channel. This makes the model robust to arbitrary sample rates and prevents this class of bug recurring.
2. **Document the expected sample rate prominently** in the model file metadata, not just `CLAUDE.md`.
3. **Add a runtime sanity check** — at inference, log the actual buffer time span (`buffer[-1].t - buffer[0].t`) and warn if it deviates from the expected window by >20 %.

---

## Affected files

- `ModelInclude.py` — Stage 4 main loop (line ~158: `data_buffer.append`)
- `App.py` — main loop (line ~258: `prefill_buffer.append`)

## Related reports

- `Claude Report/Claude Report.md` — initial root-cause analysis (sensor saturation hypothesis)
- `Claude Report/Claude Report 2 - Fix Branch Test.md` — PID tuning iteration
- `Claude Report/Claude Report 3 - Fix Confirmed.md` — current best-known PID config

This issue is **independent of the PID tuning work** in Reports 1 - 3 and may explain why Reports 1 and 2 hit a force ceiling regardless of control gains.
