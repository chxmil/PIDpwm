# Daily Report — 2026-05-10

**Researcher:** Chamil Ahlee
**Author:** Claude Code (claude-opus-4-7)
**Branch:** `fix/force-control`
**Combines:** Issue 6 (open, blocker resolved), Issue 7 (resolved), DevLog 2026-05-10 ADS1115 Acquisition Fix
**Carries forward:** Issue 2 (Force Tracking Variance), Issue 3 (Sensor Saturation Silent Failure — Python guard), Issue 4 (Material Classifier Phase B Data Gap), **Issue 6** (Classifier verification on labelled data — pending sanity test)

---

## Headline

A single firmware bug in the ESP32-S3 ADC chain was poisoning every layer above it — force model output, PID behaviour, and the day-9 RF classifier. Today's work isolated, root-caused, and patched it. The ADS1115 was being asked to acquire a high-impedance signal in a window too short for its sample-and-hold cap, producing resistance readings 8–75× too high. After a four-line firmware change, the sensor pipeline matches multimeter ground truth within 7%, and CSV `resistance` values are back inside the training distribution.

**Bottom line of the day:** firmware fix verified at boot self-test and in real grip data. Classifier reliability test on labelled materials is the next-step Update Report; that's what closes the loop on Issue 6.

---

## The Problem

### Symptom that started the day

User flagged that the day-9 prediction sessions in `data_logs/Prediction/{Hard,Medium,Soft}/` did not look reliable. Inspection of the per-trial `_summary.csv` files confirmed:

| True class (folder) | Trials with contact | Predicted correctly | Field accuracy |
|---|---|---|---|
| Hard   | 7 (loop 7 had no contact) | 2 (loops 6, 8) | **29%** |
| Medium | 10 | 1 (loop 3)     | **10%** |
| Soft   | 9 | 7              | **78%** |

Versus the published 5-fold CV baseline of **0.794 ± 0.111** (Research/material_classifier_RF_baseline_2026-05-09.md). Hard and Medium were essentially random; only Soft survived.

### First diagnosis (Issue 6 — filed)

`baseline_res_k = 800.0000` appeared in 21 of 27 prediction trials. `800` is the resistance clamp ceiling at `ModelInclude.py:92-93` and `App.py:330-331`, not a measurement. Stage-2 calibration was silently consuming clamped values, and every downstream feature (`current_sensor_baseline`, `threshold_res_k`, `shifted_cond`, `pred_force_n`, the 5 RF features) was computed from a corrupt baseline.

Filed as **Issue 6 — Classifier Unreliable on New Sessions**.

### Root-cause walk (Issue 7 — filed and resolved)

Working upward through the data chain:

1. **Multimeter ground truth on the sensor (out of circuit):** 0.72 MΩ.
2. **CSV `resistance` column at idle:** firmware reports 5.6–15.1 MΩ, alternating bimodally every other sample.

That's an 8–75× discrepancy. The Python clamp wasn't the cause; it was a downstream symptom of the firmware reporting wrong numbers.

The firmware divider math (`Code Store/PIDpwm.ino:152-157`) was algebraically correct given its assumed inputs, so the bug had to live in one of:
- R_FIXED hardware mismatch
- VIN drift
- Sensor non-linear under DC bias
- ADS1115 reading itself wrong

Filed as **Issue 7 — Firmware Resistance Reading Wrong By Order of Magnitude** with a 5-step bench-triage protocol.

### Triage results — what was confirmed correct vs faulty

| Test | Result | Conclusion |
|---|---|---|
| Multimeter R_FIXED on board | 330 kΩ | Hardware resistor correct — H1 ruled out |
| Multimeter VIN at divider top | 3.3 V | Supply correct — VIN-drift ruled out |
| Multimeter AIN1-to-GND, powered, idle | **2.268 V** | Matches divider math for 720 kΩ exactly. Divider physics is fine. **The ADC reading is what's wrong.** |
| Multimeter ADS1115 GND ↔ ESP32 GND | **0.0001 V** | Clean ground. H1 (GND reference offset) ruled out. |

That left **H2: ADS1115 sample-and-hold cap unable to acquire AIN1 through ~226 kΩ source impedance at 860 SPS.** ADS1115 datasheet recommends source impedance < 10 kΩ; the divider's Thevenin output impedance is ~22× higher than that. The bimodal `adc1_raw` alternation is exactly what "settled vs not-settled sample" looks like.

---

## The Solution

### Firmware patch — `Code Store/PIDpwm.ino`

Four edits, all on the firmware side, no hardware or wiring changes:

| # | Line | Before | After | Reason |
|---|---|---|---|---|
| 1 | 46 | `SENSOR_INTERVAL_US 10000` (100 Hz) | `SENSOR_INTERVAL_US 20000` (50 Hz) | Two 7.8 ms ADC conversions + 200 µs settle = ~15.8 ms/cycle, doesn't fit 10 ms |
| 2 | 97 | `RATE_ADS1115_860SPS` | `RATE_ADS1115_128SPS` | 7.8 ms/conv vs 1.16 ms — gives S/H time to acquire through 226 kΩ source |
| 3 | ~152 | (none) | `delayMicroseconds(200)` between ADC0 and ADC1 reads | Mux + S/H settling between channels |
| 4 | end of `setup()` | (none) | Boot self-test prints `I:CALIB,adc1=…,Vout=…,R_kohm=…` | Operator catches future ADC-chain regressions at boot, not from CSV post-hoc |

Sensor sample rate drops 100 Hz → 50 Hz. Still ~28× the 1.8 Hz model rate, no impact on PID or model.

### Verification — boot self-test

After re-flash, first serial line:

```
I:CALIB,adc1=17709,Vout=2.214,R_kohm=672.4
READY
```

| | Multimeter | ADC reports | Δ |
|---|---|---|---|
| `adc1_raw` | (expected ~18,144) | 17,709 | −2.4% |
| Vout | 2.268 V | 2.214 V | −2.4% |
| R | 720 kΩ | 672 kΩ | −6.6% |

All three within 7%, well inside reproducibility. Per-session conductance shift in `ModelInclude.py` normalises this exact kind of offset back to training distribution.

### Verification — real grip data (`phase1_20260510_224014.csv`)

**Per-packet idle resistance (Loop 1 pre-contact):** 678,800 / 678,917 / 677,750 / 667,709 / 670,574 / 679,618 / 679,267 / 675,076 Ω. All in the 660–680 kΩ band. **No 5–55 MΩ values. No bimodal alternation** — `adc1_raw` deltas are < 100 LSB (real noise floor).

**Resistance under contact:** 679 → 609 → 588 → 491 → 404 kΩ. Monotonic dynamic response — sensor is finally being read correctly.

**Summary baselines across 5 trials:** 679.8 / 700.9 / 699.8 / 696.9 / 722.9 kΩ. Not 800. Not clamped.

**`shifted_cond` distribution:** idles at ~0.00437 (matches `TRAIN_BASELINE_G = 0.004369` to four decimals), rises to ~0.00537 under contact. This is precisely the distribution the CNN-LSTM was trained on — the original goal of the conductance-shift design.

---

## Issues Resolved Today

### Issue 7 — Firmware Resistance Reading Wrong By Order of Magnitude — **CLOSED**

- Root cause: H2 (ADS1115 S/H acquisition through ~226 kΩ source impedance at 860 SPS).
- Patch: 4 edits in `Code Store/PIDpwm.ino`. See "The Solution" above.
- Verification: boot self-test within 7% of multimeter; real grip CSV shows healthy values across the full pre-contact + contact + release window.
- Closes: removes the upstream blocker for Issue 6.

---

## Issues Carried Forward

### Issue 2 — Force Tracking Variance Around Setpoint
- **Status:** Open (carried forward unchanged from 2026-05-08).
- **Note today:** New 2026-05-10 grip data shows max-force values 5.8–9.5 N at 3.5 N setpoint — i.e. the variance issue is still present and may now even appear larger because the force model is no longer being suppressed by clamped sensor input. Worth re-running the original mitigation #1 (raise GRIP_PWM, measure stdev) once labelled sanity test is complete.

### Issue 3 — Sensor Saturation Silent Failure (Python-side guard)
- **Status:** Open — fix designed but not yet merged. Still valuable as defence-in-depth even after Issue 7's firmware fix, in case the sensor ever genuinely disconnects in the future.
- **Action:** Land the `ModelInclude.py` Stage 2 saturation guard in a small dedicated commit on `fix/force-control`.

### Issue 4 — Material Classifier Data Gap (Phase B portion)
- **Status:** Open — Phase A live, Phase B blocked on probe-phase data (≥ 30 trials per class at 20 Hz). Unchanged from 2026-05-09.

### Issue 6 — Classifier Unreliable on New Sessions
- **Status:** **Blocker (Issue 7) cleared. Classifier-side verification still pending.**
- **What's left:** run the labelled 5-grip × 3-class sanity test (see today's Update Report) and confirm field accuracy lands inside the article CV band (≥ 0.7 per class). If yes, Issue 6 closes. If Hard/Medium recall still sub-baseline, the residual issue is force-model overshoot or PID interaction, not signal corruption.

---

## Research Data Collected

No mandatory research-article data added today. The 2026-05-09 prediction-mode CSVs are now formally **disqualified** from any field-accuracy reporting in the article — they were collected under the firmware-faulty regime documented by Issue 7 and do not represent system performance under healthy conditions. Add a §Limitations note to `Research/material_classifier_RF_baseline_2026-05-09.md` referencing this when the article is drafted.

---

## File Structure Changes

No changes to file layout today. CLAUDE.md §2 remains accurate.

---

## Next Step

See `Claude Report/Update Report/Update_2026-05-10_Issue 6 Verification Plan.md` (status: Under Review) — labelled 5-grip × 3-class sanity test, to formally close Issue 6 and produce a post-fix accuracy number for the article.
