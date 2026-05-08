# DevLog 1 — Implement Option C: Buffer Resampling on Inference

**Author:** Claude Code (claude-opus-4-7)
**Date:** 2026-05-08
**Branch:** `fix/buffer-rate-opt-c` (forked from `fix/force-control`)
**Related issue:** `Claude Report/Issue Report/Issue 1 - Inference Rate Mismatch and Timestamp.md`

---

## What changed

### `ModelInclude.py`

1. **New constants in `run_one_grip()`:**
   ```python
   SEQ_LEN       = 60                                       # model expects 60 timesteps
   BUFFER_HZ     = 100                                      # ESP32 packet rate
   BUFFER_MAXLEN = int(SEQ_LEN * (BUFFER_HZ / TARGET_HZ))   # ~3333 packets ≈ 33 s
   ```

2. **`data_buffer` capacity:** `maxlen=60` → `maxlen=BUFFER_MAXLEN (≈3333)`.
   The buffer now holds ~33 s of packet-rate history instead of ~0.6 s.

3. **Inference resampling:** at each 1.8 Hz inference tick, the full buffer is
   resampled to 60 evenly-spaced indices using `np.linspace(...).astype(int)`
   before being passed to `scaler_X.transform()`. If the buffer has fewer
   than 60 samples (cold start), it falls back to baseline-padding.

### `App.py`

1. **`prefill_buffer` capacity:** `maxlen=60` → `maxlen=3333` so prefill
   matches `data_buffer`.

2. **Idle drain loop:** the previous code read at most one packet per
   iteration of the main loop (gated by `time.sleep(0.05)`), so prefill
   captured only ~20 Hz of data despite ESP32 streaming at 100 Hz. The new
   code drains all `in_waiting` packets per iteration, so prefill stays at
   true packet rate.

---

## Why

Per Issue 1, the previous implementation appended to a 60-slot buffer at
~100 Hz, so the model's 60-timestep input window represented only ~0.6 s
of real time — vs. its trained 33 s context. Option C of the issue report
recommends keeping the buffer at packet rate (preserves all data) and
resampling at inference (gives the model the temporal scale it learned on)
so inference can later be run at any rate without further code changes.

---

## Files touched

| File | Lines changed |
|---|---|
| `ModelInclude.py` | +21 / −9 (Stage 1 buffer init + Stage 4 inference) |
| `App.py` | +13 / −7 (prefill loop + maxlen) |

No model file, scaler, or hardware-protocol change.

---

## Expected effect

- Force predictions should track compression depth more linearly (less
  "stepwise" behavior observed in Reports 1 - 3).
- The model may now reach forces above the previous 2.5 - 2.7 N ceiling
  when the gripper actually compresses to that level.
- No change to the 1.8 Hz inference cadence or PID loop.
- First grip after starting the program: predictions during the first
  ~33 s will progressively become more accurate as the buffer fills.
  Subsequent grips inherit this via `prefill_buffer`.

---

## How to verify

1. Run a grip session on the same object used in `phase1_20260508_220959.csv`
   (the Report 3 baseline).
2. Compare per-loop:
   - Max predicted force
   - Force trajectory shape (should be smoother, less stepwise)
   - PWM behavior (PID should converge faster if force tracks reality)
3. If max force exceeds ~2.7 N on objects that previously plateaued: the
   bug is real and Option C fixes it.
4. If predictions look unchanged or worse: the original training was
   actually at packet rate (CLAUDE.md is misleading) and the original
   code was correct. In that case, revert this branch.

Either outcome is informative — both confirm or refute the rate-mismatch
hypothesis.

---

## Roll-back

```bash
git checkout fix/force-control
# or hard reset this branch
git reset --hard fix/force-control
```

The previous-best PID config remains untouched on `fix/force-control`.
