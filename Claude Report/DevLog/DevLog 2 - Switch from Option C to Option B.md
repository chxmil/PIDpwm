# DevLog 2 — Switch from Option C to Option B

**Author:** Claude Code (claude-opus-4-7)
**Date:** 2026-05-08
**Branch:** `fix/buffer-rate-opt-c` (kept name; superseded by this commit)
**Related issue:** `Claude Report/Issue Report/Issue 1 - Inference Rate Mismatch and Timestamp.md`
**Supersedes:** `Claude Report/DevLog/DevLog 1 - Implement Option C buffer resampling.md`

---

## What changed and why

User-reported result: Option C (packet-rate buffer + resample-on-inference) **did not break the force ceiling** — predictions still capped near the previous range despite the model now receiving a 33 s window via index-resampling.

Switching to **Option B** from Issue 1: gate `data_buffer.append()` to fire only at the 1.8 Hz training cadence. The 60-slot deque then naturally represents ~33 s of context with no resampling step needed. This is structurally simpler and matches what the original implementation likely should have done all along.

---

## Code changes (this commit, on top of Option C)

### `ModelInclude.py`

- Removed `BUFFER_HZ` and `BUFFER_MAXLEN` constants.
- `data_buffer = deque(..., maxlen=SEQ_LEN)` — back to 60-slot deque.
- New state in Stage 3: `last_buffer_append = grip_start - INTERVAL` (so first packet appends immediately).
- Stage 4: `data_buffer.append(...)` is now gated by `(now - last_buffer_append) >= INTERVAL`. Intermediate packets still feed CSV logging and PID state, but are not added to the model input.
- Inference reverts to the original simple "pad-with-baseline-then-scale" path (no resampling). The 60 entries already represent the trained 33 s scale.

### `App.py`

- `prefill_buffer = deque(maxlen=60)` (was 3333).
- New `PREFILL_INTERVAL = 1.0 / 1.8` constant.
- Idle loop still drains all in-waiting packets (avoids backlog), but only appends to `prefill_buffer` once per `PREFILL_INTERVAL` using the latest drained conductance value.

### Documentation

- `CLAUDE.md`: inference snippet, prefill section, and Key Constants table all updated to reflect Option B.
- This DevLog (DevLog 2) supersedes DevLog 1.
- Issue 1 itself remains open — Option C didn't fix the underlying issue, so the rate-mismatch hypothesis is partly disproved (or the fix is needed alongside something else like retraining).

---

## What this means for Issue 1

Two outcomes will tell us where the real problem lies:

| Option B test result | Implication |
|---|---|
| Force breaks the ceiling | Buffer rate WAS the issue; index-resampling in Option C was an inadequate equivalent (likely because the model expects evenly-spaced *averages*, not picks). |
| Force still capped | Rate mismatch is not the blocker. Real problem is either: (a) the model's training distribution has limited force range, or (b) `CLAUDE.md`'s "1.8 Hz training" claim is wrong and the original code was correct. **Path forward: retrain.** |

---

## Roll-back

```bash
git reset --hard fix/force-control      # discard all buffer changes
```

`fix/force-control` remains the validated baseline (PID config from Reports 1-3).

---

## Note on branch naming

This commit lives on `fix/buffer-rate-opt-c` despite implementing Option B. The branch could be renamed to `fix/buffer-rate-opt-b` for clarity:

```bash
git branch -m fix/buffer-rate-opt-c fix/buffer-rate-opt-b
git push origin :fix/buffer-rate-opt-c fix/buffer-rate-opt-b
```

Optional — git history makes the pivot clear regardless.
