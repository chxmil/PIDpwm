# Material Classification — Full Method Report

**Date:** 2026-05-13
**Researcher:** Chamil Ahlee · Walailak University
**Research title:** AI-Adaptive PID Control for Tactile Robotic Grippers
**Article context:** Comprehensive methodology for the three material-classification pipelines (Phase A RF, Phase A-prime 1D-CNN, Phase B 1D-CNN) and the underlying CNN-LSTM force-prediction model that feeds them.

---

## 0. System Overview

The system contains **four learned models** running in two coupled roles:

| Role | Model | Input | Output | Active runtime |
|---|---|---|---|---|
| Force prediction (closed-loop PID setpoint tracking) | CNN-LSTM regression | `(60, 2)` time series @ 1.8 Hz | scalar force in N | ✅ |
| Material classification — Phase A | Random Forest | 5 scalar features per trial | softmax over {Hard, Medium, Soft} | ✅ |
| Material classification — Phase A-prime | 1D-CNN on PID-grip trace | `(40, 5)` time series @ 20 Hz | softmax over {Hard, Medium, Soft} | ✅ (alongside RF) |
| Material classification — Phase B | 1D-CNN on probe-phase trace | `(40, 5)` time series @ 20 Hz | softmax over {Hard, Medium, Soft} | 🟡 awaiting probe data |

The force model is a **hard prerequisite** for the classifiers — `pred_force_n` is one of the scalar features in RF (`f_peak`, `rise_ms`, `stiffness_proxy`) and feeds the input contract that `shifted_cond` participates in. The classifiers consume the data produced by the PID loop driven by the force model.

---

## 1. Data Acquisition

### 1.1 Hardware

- **Tactile sensor:** resistive (force ∝ 1/R). Raw resistance reported in Ohms by the ESP32; signal range during contact ≈ 200–800 kΩ.
- **Encoder:** absolute angular position of the gripper joint in degrees. Idle ≈ 151.5°; full close ≈ 95°; release target 106°.
- **Actuator:** brushed DC motor driven by PWM in `[−255, +255]` (negative = close, positive = open, 0 = hold).
- **MCU:** ESP32; firmware in `Code Store/PIDpwmClaude/PIDpwmClaude.ino`.

### 1.2 Serial Protocol

USB-serial, **115 200 baud**, line-oriented. Each sample is one line:

```
D:<esp_ms>,<adc0>,<pos_deg>,<adc1>,<resistance_ohm>,<pwm>
```

Parsed into `{esp_ms, adc0, pos, adc1, res, pwm}` by `App.py::parse_sensor()`. Outgoing commands are `PWM:<value>\n` and `STOP\n`.

### 1.3 Effective Sample Rate

- ESP32 nominally emits **~65 Hz**. Empirical rate varies with USB scheduler jitter and Python loop turnaround.
- Force inference runs at **1.8 Hz** (= 1 / 0.556 s) and **must match the training rate of the CNN-LSTM exactly** — see §2.5.
- Material-classifier feature extraction targets **20 Hz** via 50 ms bin-mean accumulation — see §4.

### 1.4 Trial Granularity

A *trial* = one grip cycle: baseline calibration → approach → PID grip → release. Trials are indexed by `loop_index` (1, 2, 3, …) within a session. Each grip lasts `GRIP_DURATION = 8.0 s` (post-2026-05-11 retune, was 5 s). Multiple trials within one session are concatenated into a single CSV file.

---

## 2. Pre-processing

### 2.1 Resistance Clamping

```python
res_k = res_ohm / 1000.0
if res_k <= 0 or res_k > 800:
    res_k = 800.0
```

Suppresses open-circuit spikes and infinities. The 800 kΩ ceiling is the **only sensor-saturation handling in the Python layer**; Issue 8 (open) flags that this ceiling clips a fraction of healthy idle readings and corrupts `res_drop_pct` for Phase A.

### 2.2 Conductance Computation

```python
raw_cond = 1.0 / (res_k + 1e-6)
```

Conductance linearises the pressure response and damps the open-circuit noise floor (where `R` is large, small changes in `R` produce large changes in `1/R` — bad for the model — so we normalise away from that regime in §2.3).

### 2.3 Baseline Correction & Distribution Alignment (Stage 2)

The force model (`Model/my_cnn_lstm_model.keras`) was trained against a **fixed baseline conductance** `TRAIN_BASELINE_G = 0.004369`. Every new session recalibrates the sensor baseline because sensor unit, mounting, and temperature drift the resting `R`. The session baseline is corrected to the training distribution before features are computed:

```python
shifted_cond = ((raw_cond − current_sensor_baseline) × SENSOR_GAIN) + TRAIN_BASELINE_G
```

where `current_sensor_baseline = 1 / (baseline_res_k + 1e-6)` and `baseline_res_k` is the per-session mean of 30 resistance samples collected at `PWM:0` (Stage 2 of `run_one_grip()`). If Stage 2 fails to receive enough samples within 5 s, a fallback of 250 kΩ is used and a warning is printed.

**Why a shift, not a normalisation?** The model was trained on a *fixed-baseline* training set, so the inference input must live in the same affine subspace. A shift (additive) is sufficient because the training feature is the conductance *delta* from baseline plus the baseline itself.

### 2.4 `SENSOR_GAIN` — Cross-Hardware Compatibility

| Scenario | `SENSOR_GAIN` |
|---|---|
| Original training sensor | `1.0` |
| New / replacement sensor | `0.08` |

Different sensor units have different sensitivity slopes (Ω/N). Rather than retraining the force model per sensor, the conductance delta is **rescaled** by `SENSOR_GAIN` so the model sees a distribution matched to its training. This is fixed in `App.py` config and persists for the whole session.

### 2.5 `is_press` — Dynamic Contact Detection (Latching)

`is_press` is **not hardcoded** to 1 at the start of a grip. It transitions from 0 → 1 at the first packet where:

```python
res_k < 0.93 × baseline_res_k
```

(7% resistance drop = contact). Once latched, `is_press` stays at 1 for the rest of the trial. The 0.93 threshold was tightened from 0.97 in Claude Report 2 to push the approach deeper before PID engages, recovering grip pressure that was being bled by the LPF.

This flag is **the alignment anchor** for both feature extraction (§4) and the PID integral wind-up guard (§5).

---

## 3. Force Prediction Model (CNN-LSTM)

### 3.1 Architecture

- **File:** `Model/my_cnn_lstm_model.keras`
- **Input:** `(1, 60, 2)` — batch=1, sequence length 60, features `[shifted_cond, is_press]`.
- **Output:** scalar predicted force in Newtons.
- **Inference call:** `model(scaled_input, training=False)` — never `.predict()` (the latter adds significant TF retracing overhead inside the serial loop).

### 3.2 Scalers

| Scaler | Type | Purpose |
|---|---|---|
| `Model/scaler_X.pkl` | `sklearn.PowerTransformer` | Yeo-Johnson normalisation of `[shifted_cond, is_press]`. Stored λ values: `[-51.9, 4.33]`. |
| `Model/scaler_y.pkl` | `sklearn.MinMaxScaler` | Normalises force target into `[0, 1]`. Original range `[0, 49 N]` (checked at load time). |

Inference inverts the y-scaler and clamps to ≥ 0 N (negative outputs are unphysical; the clamp masks a small known issue where the model occasionally emits a slightly negative value below 0.05 N).

### 3.3 Rolling-Buffer Inference

- 60-frame `deque(maxlen=60)` of `[shifted_cond, is_press]` rows accumulates within `run_one_grip()`.
- Seeded between grips by `prefill_buffer` — recent idle samples from the post-release period — so the first inference of the next grip doesn't run on zero-padding.
- When fewer than 60 rows are present, the deficit is **front-padded** with `[TRAIN_BASELINE_G, 0.0]` (the model's training-distribution null state), not zeros.

### 3.4 PID Coupling

The force model's output drives the PID controller (`KP=70, KI=20, KD=7, alpha=0.4`) which writes new PWM commands at the inference rate of 1.8 Hz. Integral accumulation **only begins after `is_press` latches**, preventing windup during free-air approach. The PID output is sign-negated (`current_pwm = clip(-pid_output, -255, 0)`) so positive force-error produces tighter grip (more negative PWM).

PID parameters in §5 of this report; they matter for classification only because they determine the shape of the post-contact trajectory the Phase A-prime CNN learns from.

---

## 4. Feature Engineering

### 4.1 Phase A — RF, Hand-Crafted Scalars

Five scalar features extracted per trial (`Code Store/train_material_rf.py::extract_trial_features`):

| Feature | Formula | Intuition |
|---|---|---|
| `delta_pos_max` | `max(|pos_t − pos_at_first_contact|)` over post-contact rows | Maximum geometric compression depth |
| `res_drop_pct` | `(baseline_res_k − min(res_k)) / baseline_res_k` over post-contact | Normalised conductance peak |
| `f_peak` | `max(pred_force_n)` over post-contact | Peak observed grip force |
| `rise_ms` | `t_ms` at which `pred_force_n ≥ 0.9 × f_peak`, minus `t_ms` at contact | Time to reach 90% of peak force |
| `stiffness_proxy` | `f_peak / rise_ms` | Effective Newtons per millisecond — a coarse stiffness measure |

These are computed from the **per-packet PID trace** (`trial_records` field of the dict returned by `run_one_grip`).

**Feature importance (v4, 5-fold CV mean):** `stiffness_proxy 0.318 · f_peak 0.316 · rise_ms 0.238 · res_drop_pct 0.125 · delta_pos_max 0.003`. The position-encoder feature is effectively dead — see §11 Open Items.

### 4.2 Phase A-prime and Phase B — CNN, Time-Series Channels

Five channels emitted per 50 ms bin (`Code Store/train_material_cnn_pid.py::_bin_mean`):

| Channel | Formula | Range (typical) |
|---|---|---|
| `shifted_cond` | mean of `shifted_cond` within the bin | `~0.004 – 0.030` |
| `delta_pos` | bin-mean `pos_deg` − `pos_at_first_contact` | `~0 – −10°` |
| `d_cond_dt` | `(bin[k].shifted_cond − bin[k−1].shifted_cond) / 0.050 s`; 0 at `k=0` | `~−0.01 – +0.06 / s` |
| `d_dpos_dt` | `(bin[k].delta_pos − bin[k−1].delta_pos) / 0.050 s`; 0 at `k=0` | `~−10 – 0 °/s` |
| `res_norm` | `(bin-mean res_k) / baseline_res_k`, clipped to `[0, 1.5]` | `0 – 1.5` |

`is_press`, `pred_force_n`, and `pid_pwm_out` are **excluded by design** from CNN inputs — `is_press` is constant 1 inside the window (so non-informative); `pred_force_n` is downstream of `shifted_cond` (no additional information); `pid_pwm_out` is the PID's *response* to the input, not the material itself.

Phase A-prime and Phase B share the feature definition. They differ only in **what the underlying packets are**:
- **Phase A-prime:** post-`is_press` packets from a PID-controlled grip (`trial_records`).
- **Phase B:** packets from the pre-PID probe ramp `−80 → −150 PWM` (`probe_records` from Stage 2.5).

---

## 5. Window Extraction (Time-Series Classifiers)

Both 1D-CNN trainers and their runtime loaders use the same extraction logic.

### 5.1 Contact Alignment

`t = 0` is the first packet with `is_press == 1` (or, in probe mode, the first packet with `res_k < 0.93 × baseline_res_k`, which is the same threshold). This invariance to approach distance is critical — operator-placed objects vary by several degrees, so a sample-index-aligned window would mix Hard-at-95° with Hard-at-110° as if they were different.

### 5.2 Binning at 20 Hz (50 ms)

Raw packets are accumulated into 50 ms bins by `bin_id = floor((t_ms − t0) / 50)`. Each bin emits one feature row (the channel-wise **mean** of its packets). 20 Hz is locked: probe phase samples at fixed 20 Hz in real time, so the trainer must match for distribution alignment.

### 5.3 Empty-Bin Handling (Bin-Skip Fix, 2026-05-13)

`pandas.groupby("_bin")` does **not** emit empty groups. The ESP32 serial stream drops a packet every few hundred milliseconds (USB scheduling, TF inference latency at 1.8 Hz), leaving ~1–2 bins per trial with zero packets in the first 2 s.

A naive `for bid, g in groupby(): if bid >= 40: break` short-circuits one row early at every empty bin, producing 39 rows instead of 40 → the trial is dropped. This bug killed 222 of 286 trials in the first CNN-PID training run.

**Fixed implementation:**

```python
for bid, g in post_df.groupby("_bin"):
    if len(rows) >= WINDOW_LEN:
        break
    # ... emit feature row ...
```

The window now uses the first **40 non-empty bins** regardless of bin-ids. Time-axis drift at a gap is bounded by `BIN_MS = 50 ms` per gap; gap rate is sparse (< 5% of bins in practice); CV variance was *unaffected*.

### 5.4 Probe Phase (Stage 2.5)

Used only by Phase B (not Phase A-prime). Implementation in `ModelInclude.py`:

- Triggered by `config["PROBE_ENABLED"] = True` (set by `App.py --probe`).
- PWM ramps linearly from `PROBE_PWM_START = −80` to `PROBE_PWM_END = −150` over `PROBE_DURATION = 2.0 s`.
- Contact detection uses the same threshold as Stage 4 (`res_k < 0.93 × baseline_res_k`).
- After contact, packets are bin-averaged in real time at 20 Hz (50 ms wall-clock window per bin), feature row emitted only when at least one packet is in the bin.
- Capture stops at 40 rows OR `PROBE_DURATION + 2.0 s` timeout.
- PWM is dropped to 0 for 50 ms before Stage 3 (approach + PID) begins. Stage 3 continues normally — both classifiers can run on the same physical grip.

### 5.5 Why probe data is needed (Phase B vs Phase A-prime)

Phase A-prime trains on the PID-controlled trajectory, where overshoot (`max_force_n` 8–17 N for a 3.5 N setpoint), LPF smoothing, grip-floor clamps, and 1.8 Hz integral updates all imprint a **PID signature** on top of the material signature. Phase B uses the pre-PID probe ramp to capture the material response *uncontaminated* by control overshoot. The CNN architecture is identical; only the input distribution differs.

---

## 6. Quality Filtering

Applied identically across all three classifiers (with feature-set-specific exceptions noted):

| Drop criterion | Reason |
|---|---|
| `n_packets < 30` | Trial too short to contain a meaningful grip. |
| `n_pre < 5` OR `n_post < 5` | Contact never tripped, or grip ended immediately — no usable signal. |
| `f_peak < 1.5 N` (RF & A-prime only) | Failed grip (sensor never loaded). Phase B doesn't use `f_peak`. |
| `baseline_res_k < 1000 kΩ AND f_peak < 5 N` | Calibration was contaminated by residual contact pressure from the previous trial. |
| `n_non_empty_bins < 40` (CNN trainers only) | Window contract violation. After the bin-skip fix this is effectively only "no contact" trials. |

Quality filter outcomes are logged per-trial in the trainer output for audit (`[Hard ] Hard (1).csv loop=3 reason=no contact window`).

---

## 7. Scaling

### 7.1 Phase A RF

```python
scaler = StandardScaler().fit(X)   # X shape (N, 5)
Xs     = scaler.transform(X)
```

Stored as `Model/scaler_mat_rf.pkl`. Fit on the full training set; the same instance is used at inference.

### 7.2 Phase A-prime / Phase B CNN

**Per-channel** `StandardScaler`, fit on the *flattened* training tensor:

```python
X.shape == (N, 40, 5)
scaler = StandardScaler().fit(X.reshape(-1, 5))   # fit on (N×40, 5) rows
Xs     = scaler.transform(X.reshape(-1, 5)).reshape(N, 40, 5)
```

This estimates one mean/std per channel across *all timesteps and trials* — appropriate because each timestep within a trial is a realisation of the same underlying random process, not a separate variable. Stored as a bundle `{scaler, classes, window_len}` in `Model/scaler_mat_cnn{,_pid}.pkl`.

---

## 8. Model Architectures

### 8.1 Phase A — Random Forest

```python
RandomForestClassifier(
    n_estimators = 200,
    max_depth    = None,
    random_state = 0,
)
```

Trained directly on `(N, 5)` scalar features. No regularisation tuning — defaults perform well on the small feature set; the variance comes from the data, not the model.

### 8.2 Phase A-prime & Phase B — 1D-CNN (identical architecture, different data)

```python
Sequential([
    Input(shape=(40, 5)),
    Conv1D(32, kernel_size=5, padding='same', activation='relu'),
    Conv1D(64, kernel_size=3, padding='same', activation='relu'),
    MaxPool1D(pool_size=2),                                    # → (20, 64)
    Conv1D(64, kernel_size=3, padding='same', activation='relu'),
    GlobalAveragePooling1D(),                                  # → (64,)
    Dropout(0.3),
    Dense(32, activation='relu'),
    Dense(3, activation='softmax'),                            # Hard / Medium / Soft
])
```

- **~25 000 trainable parameters.** Sized for ~200–300 training trials.
- **Receptive field after the three Conv1Ds and one MaxPool:** ~14 input timesteps = ~700 ms of post-contact dynamics per output unit before pooling. Easily enough to span the rising-edge plateau of any single grip.
- **GlobalAveragePooling vs Flatten:** GAP gives translation invariance along the time axis (the model is told "the order matters but the absolute timing within the window is not critical"). Empirically this matches the alignment-on-contact preprocessing.
- **Dropout 0.3** between the conv and dense stages prevents overfitting on the small dataset.

### 8.3 Force Model — CNN-LSTM

External architecture (defined in `Model/Train/CNNLstm.ipynb`, not in this report). Treated as a frozen module. Sanity-checked at load time by feeding a synthetic grip (idle conductance × 30 frames → rising conductance × 30 frames) and confirming output > 5 N — a "model is healthy" pass/fail.

---

## 9. Training Procedure

### 9.1 5-Fold Stratified Cross-Validation

```python
StratifiedKFold(n_splits=5, shuffle=True, random_state=0)
```

Applied to **all three** material classifiers. `random_state=0` so folds are reproducible across trainer runs. Per-fold accuracy and `cross_val_predict` over the union of held-out folds give the confusion matrix.

### 9.2 RF Training

`cross_val_score` and `cross_val_predict` for evaluation; final `.fit(Xs, y)` on the full data for the deployed artefact.

### 9.3 CNN Training

Per fold:

```python
model.fit(
    Xs_train, y_train,
    epochs        = 80,
    batch_size    = 16,
    optimizer     = 'adam',          # lr=1e-3
    loss          = 'sparse_categorical_crossentropy',
    metrics       = ['accuracy'],
    shuffle       = True,
)
acc = model.evaluate(Xs_test, y_test)[1]
```

After CV, the architecture is rebuilt and refit on the full dataset (same hyperparameters) and saved as the deployment artefact. **No early stopping** in the current version — 80 epochs at batch 16 saturates on this dataset size; loss-curve inspection in CV folds shows convergence by epoch ~30 with no overfitting trend.

### 9.4 Data Augmentation

**None** in v1 of the CNN trainers. The plan acknowledges time-warp (±10%) and Gaussian channel noise (σ=0.02) as candidates; current performance hits the ceiling implied by Issue 9 (Hard↔Medium signal overlap), which augmentation can't fix.

---

## 10. Inference Path (Runtime)

The three classifiers run **in parallel** at the end of each grip, sharing the same `result` dict returned by `run_one_grip()`:

```
run_one_grip() ─┬─► trial_records  ─►  MaterialClassifier.classify_trial()  ─►  RF prediction
                ├─► trial_records  ─►  MaterialPIDCNNClassifier.classify_pid() ─►  CNN-PID prediction
                └─► probe_records  ─►  MaterialCNNClassifier.classify_probe() ─►  CNN-probe prediction
```

Each loader returns `(label, {Hard, Medium, Soft → probability})` or `(None, None)` on failure (artefacts missing, fewer than 40 non-empty bins, etc.). `App.py::_post_grip_classify` runs all three, writes the predictions to the per-trial summary CSV, and prints a one-liner to the console:

```
Material prediction:  RF=Hard(0.85/0.04/0.11)   CNN-PID=Hard(0.92/0.06/0.02)   CNN-probe=-(//)
```

The three are independent — failure of any one does not affect the others. RF is currently the *contractual* primary (its label is written first in the CSV); CNN-PID is diagnostic until a field A/B confirms its CV advantage holds out-of-distribution.

---

## 11. Metrics

Each trainer emits:

1. **Per-fold accuracy** (5 numbers) and aggregate mean ± std.
2. **Confusion matrix** over the union of held-out folds (`cross_val_predict`).
3. **Per-class classification report** — precision, recall, F1, support.
4. **Misclassification log** — every wrong prediction with `(true, pred, source_file, loop_index, f_peak, stiffness, rise_ms)` so individual trials can be re-examined.
5. **Feature importances** (RF only) — `sklearn`'s impurity-decrease importance, useful for identifying dead features (`delta_pos_max` is the recurring dead one at 0.003).

### 11.1 Current Benchmarks

| Model | CV Accuracy | Macro F1 | Trials | Hard F1 | Medium F1 | Soft F1 |
|---|---|---|---|---|---|---|
| RF v4 | 0.838 ± 0.018 | 0.842 | 203 | 0.774 | 0.819 | 0.933 |
| **CNN-PID v2** | **0.942 ± 0.024** | **0.944** | **260** | **0.929** | **0.938** | **0.964** |
| CNN-probe | pending | pending | pending | pending | pending | pending |

CNN-PID v2 is **+0.104 CV / +0.102 macro F1** over RF v4. Dominant residual error mode: Hard↔Medium (9 of 15 misclassifications) — Issue 9, not closed.

---

## 12. Open Methodological Items

These are flagged here so reviewers don't have to chase them from issue logs:

1. **Issue 8 — Stage-2 baseline clamping at 800 kΩ.** The 800 kΩ ceiling in §2.1 is clipping a fraction of healthy idle readings. `res_drop_pct` (one of RF's five features) is partially saturated as a result. Workaround: bump the ceiling to 2000 kΩ pending hardware verification.
2. **Issue 9 — Hard/Medium feature overlap under PID overshoot.** Setpoint 3.5 N, observed peaks 8–15 N. Both classes saturate similar mechanical/force-model ceilings; the discriminative signal is lost in the over-pressure response. Phase B's probe mode is the intended fix — material response *before* PID engages.
3. **`delta_pos_max` is dead** (RF importance 0.003 across v3 / v3.1 / v4). The position encoder may not resolve sub-degree compression; or the PID setpoint forces depth into a class-invariant range. Candidate for removal in a future RF, OR for replacement with a probe-derived `delta_pos_at_contact_velocity` feature.
4. **No held-out test set.** All metrics in §11.1 are 5-fold CV. Field accuracy may differ — the v2 → v3 history showed CV 0.936 dropping to field 0.714 because of a firmware-induced distribution shift. A held-out 70-trial field session per classifier deploy is the standard discipline (next planned post-Issue-2 PID re-tune verification).
5. **PID tuning sensitivity (Phase A-prime).** CNN-PID is trained on the *current* PID parameters (`KP=70, KI=20, KD=7, alpha=0.4`). Re-tuning PID will shift the input distribution and likely require CNN-PID retraining. Phase B (probe-based) is immune to this and is the long-term safer path.

---

## 13. Reproducibility

| Aspect | How |
|---|---|
| Source code | `App.py`, `ModelInclude.py`, `MaterialClassifier.py`, `MaterialPIDCNNClassifier.py`, `MaterialCNNClassifier.py`, and the trainers in `Code Store/`. |
| Training data | All raw per-packet CSVs in `data_logs/datasets/` (top level; `bin/` ignored; `probe/` for Phase B). Auto-discovered — drop a labelled CSV in, re-run the trainer, done. |
| Random seeds | `RandomForestClassifier(random_state=0)`, `StratifiedKFold(random_state=0)`. CNN training has no explicit seed — TensorFlow defaults; weight init varies between runs but CV mean is stable to ±0.01 in repeated experiments. |
| Python env | `C:\Users\charm\anaconda3\envs\PyAienv\python.exe` — TensorFlow + Keras + scikit-learn + joblib + pandas + numpy. |
| Saved artefacts | All in `Model/`: `material_rf.pkl + scaler_mat_rf.pkl` (Phase A), `material_cnn_pid.keras + scaler_mat_cnn_pid.pkl` (Phase A-prime), `material_cnn.keras + scaler_mat_cnn.pkl` (Phase B, when trained). |

---

## 14. Lifecycle Notes

| Version | Date | Trials | CV | Notes |
|---|---|---|---|---|
| RF v1 | 2026-05-09 | 44 | 0.794 ± 0.111 | Pre-firmware-fix data, original baseline. |
| RF v2 | 2026-05-11 | 78 | 0.936 ± 0.042 | Post-firmware-fix; narrow corpus; field accuracy was 0.714 → motivated v3. |
| RF v3 | 2026-05-12 | 153 | 0.837 ± 0.029 | Added 2026-05-12 sessions; class imbalance (Soft 24). |
| RF v3.1 | 2026-05-12 | 203 | 0.828 ± 0.033 | Added NewModel (1) + (4) for Soft/Medium balance. |
| **RF v4** | 2026-05-13 | **203** | **0.838 ± 0.018** | Auto-discovery refactor; same data as v3.1, tighter folds. **Current production.** |
| CNN-PID v1 | 2026-05-13 | 64 | 0.891 ± 0.037 | First A-prime CNN; 222 trials lost to bin-skip bug. |
| **CNN-PID v2** | **2026-05-13** | **260** | **0.942 ± 0.024** | Bin-skip fix; +0.104 CV vs RF v4; **wired into runtime alongside RF**. |
| CNN-probe v1 | — | — | — | Awaiting probe-mode data collection. |
