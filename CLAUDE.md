# CLAUDE.md — Haptic Robotic Gripper: Force Control System

**Researcher:** Chamil Ahlee | Computer Engineering & AI, Walailak University
**Research title:** AI-Adaptive PID Control for Tactile Robotic Grippers

> This file is the authoritative reference for Claude Code working on this project.
> Read it entirely before making any changes. Every constant, formula, and constraint
> listed here reflects a deliberate design decision — do not change them without understanding why.

---

## 1. Project Overview

A robotic gripper uses a **resistive tactile sensor** to measure contact force in real time.
Two AI models are being developed:

| Goal | Model | Status |
|---|---|---|
| **Force Prediction** | CNN-LSTM regression | ✅ Active — used in PID loop |
| **Material Classification** | 1D-CNN (Hard / Medium / Soft) | 🔲 Planned — data collection phase |

The running system (`App.py` + `ModelInclude.py`) does **closed-loop force control**: the CNN-LSTM predicts grip force from the tactile sensor, and a PID controller adjusts motor PWM to hold a target force.

---

## 2. File Structure

```
project/
├── Claude Report               # Claude's self-documentation and research insights
├── App.py                      # Entry point: serial comms, CSV logging, user commands
├── ModelInclude.py             # run_one_grip() — all grip logic, inference, PID
├── Analysis2ndSensor.ipynb     # Data pipeline: Feature Engineering & dataset prep for retraining
└── Model/
    ├── my_cnn_lstm_model.keras   # Force prediction model
    ├── scaler_X.pkl              # PowerTransformer for [Conductance, Is_Press]
    └── scaler_y.pkl              # MinMaxScaler for Force_N target
```

**Rule:** All grip logic lives in `ModelInclude.py::run_one_grip()`. `App.py` only handles serial setup, CSV file creation, and the user command loop. Do not put grip logic in `App.py`.

---

## 3. Hardware & Serial Protocol

**Serial:** `115200 baud`, `timeout=0.1s`, port configurable via `--port` (default `COM18`).

**Incoming packet format** (one line per sample from ESP32):
```
D:<esp_ms>,<adc0>,<pos_deg>,<adc1>,<resistance_ohm>,<pwm>
```

**Parsed fields:**
| Field | Type | Unit | Notes |
|---|---|---|---|
| `esp_ms` | int | ms | ESP32 internal timestamp (not used for control timing) |
| `adc0` | int | raw | ADC channel 0 |
| `pos` | float | degrees | Motor/joint angular position |
| `adc1` | int | raw | ADC channel 1 |
| `res` | float | Ohm | Raw resistance from tactile sensor |
| `pwm` | int | −255…+255 | Last PWM acknowledged by ESP32 |

**Outgoing commands:**
```
PWM:<value>\n      # e.g. "PWM:-180\n" — motor command, range -255 to +255
STOP\n             # sent on clean exit
```

**Sign convention:** Negative PWM = grip (close). Positive PWM = release (open). PWM=0 = hold/stop.

---

## 4. Model Specifications

### Force Model (CNN-LSTM) — Active

| Parameter | Value |
|---|---|
| File | `Model/my_cnn_lstm_model.keras` |
| Input shape | `(1, 60, 2)` — batch=1, sequence of 60 timesteps, 2 features |
| Features (order critical) | `[shifted_conductance, is_press]` |
| Output | Single float: predicted force in Newtons |
| Feature scaler | `scaler_X.pkl` — `PowerTransformer` |
| Target scaler | `scaler_y.pkl` — `MinMaxScaler`, range checked on load |
| Inference call | `model(scaled_input, training=False)` — never use `.predict()` |
| Training sample rate | **1.8 Hz** — inference interval must match: `INTERVAL = 1/1.8 ≈ 0.556 s` |

### Material Classification Model (1D-CNN) — Planned

| Parameter | Value |
|---|---|
| Classes | Hard / Medium / Soft |
| Input | Dynamic Force-Deformation Signature (time series) |
| Target sample rate | 1.8 Hz (0.556 s interval) |
| Key feature | `Δpos = pos_current − pos_at_first_contact` |

---

## 5. Feature Engineering

### Conductance

Raw resistance is converted to conductance to linearize the pressure response and suppress open-circuit noise:

```python
raw_cond = 1.0 / (res_k + 1e-6)   # res_k in kΩ
```

### Conductance Shift (Distribution Alignment)

The model was trained with a fixed baseline conductance (`TRAIN_BASELINE_G = 0.004369`).
Every session recalibrates the sensor baseline, so conductance is **shifted** to match training distribution.

**General formula (supports cross-hardware gain scaling):**

```python
shifted_cond = ((raw_cond - current_sensor_baseline) * SENSOR_GAIN) + TRAIN_BASELINE_G
```

| Scenario | `SENSOR_GAIN` | Notes |
|---|---|---|
| Original (training) sensor | `1.0` | Identity — same as old formula |
| New / replacement sensor | `0.08` | Rescales slope to match training distribution |

`shifted_cond` is what goes into the model. `raw_cond` is only used for display. **Never feed raw_cond directly into the model.**

**Why the gain factor?** Different sensor units have different sensitivity slopes. Rather than retraining, a per-sensor `SENSOR_GAIN` rescales the conductance delta so the model sees the same distribution it was trained on. This is set in `App.py` (or `config`) alongside PID parameters.

### `Is_Press` — Dynamic Detection

**Do not hardcode `is_press = 1` when PWM starts.**

```python
# Stage 2: collect 30 samples at PWM=0 → compute baseline_res_k (mean, kΩ)
threshold_res_k = baseline_res_k * 0.97   # 97% of baseline

# Stage 4: per packet
if not detected and res_k < threshold_res_k:
    detected = True
    is_press = 1   # latching — stays 1 for rest of grip
```

`is_press` is a **latching flag** — once contact is detected it never resets to 0 within the same grip.

---

## 6. `run_one_grip()` — Stage-by-Stage Logic

Located in `ModelInclude.py`. Called from `App.py` with signature:

```python
run_one_grip(ser, loop_idx, writer, material, tag, parse_sensor, config, prefill_buffer=None)
```

### Stage 1 — Buffer Seed
```python
data_buffer = deque(list(prefill_buffer) if prefill_buffer else [], maxlen=60)
```
Seeds the 60-frame rolling buffer with idle data from between grips. This prevents the first inference from running on all-zero padding.

### Stage 2 — Baseline Calibration
- Send `PWM:0`, wait 0.2s, drain serial buffer.
- Collect 30 resistance samples (timeout 5s).
- Compute `baseline_res_k = mean(samples)` in kΩ.
- Fallback: `250.0 kΩ` if no samples received.
- Derive `current_sensor_baseline` (conductance) and `threshold_res_k`.

### Stage 3 — Approach
```python
current_pwm = INITIAL_PWM   # from config['GRIP_PWM'], e.g. -210
ser.write(f"PWM:{current_pwm}")
```
Gripper begins closing. PID is **not yet active** — it engages only after `is_press` triggers.

### Stage 4 — Main Loop (Inference + PID @ 1.8 Hz)

Every packet received:
1. Parse sensor line → compute `res_k`, `raw_cond`, `shifted_cond` (apply `SENSOR_GAIN`).
2. Check `Is_Press` threshold → latch `is_press = 1` on first contact.
3. Append `[shifted_cond, is_press]` to `data_buffer`.
4. Write CSV row (uses `last_force` and `current_pwm` from previous inference tick).
5. If `(now - last_infer) >= INTERVAL` → run inference + PID:

**Inference:**
```python
buf_list = list(data_buffer)
n_pad    = 60 - len(buf_list)
padded   = np.array([[TRAIN_BASELINE_G, 0.0]] * n_pad + buf_list, dtype=np.float32)
scaled   = scaler_X.transform(padded).reshape(1, 60, 2)
pred     = model(scaled, training=False)
force    = max(0.0, scaler_y.inverse_transform([[float(np.array(pred).flat[0])]])[0][0])
```

**PID:**
```python
error      = SETPOINT_FORCE - force
derivative = (error - last_error) / dt   # dt = actual elapsed since last inference

if is_press:                             # integral only accumulates after contact
    error_integral += error * dt
    error_integral  = clip(error_integral, -100, 100)

pid_output = (KP * error) + (KI * error_integral) + (KD * derivative)

# SIGN: positive error → need more grip → more negative PWM
# Negate pid_output before clamping:
if is_press:
    current_pwm = int(clip(-pid_output, -255, 0))
else:
    current_pwm = INITIAL_PWM           # keep closing until contact
```

**Critical sign rule:** `current_pwm = clip(-pid_output, -255, 0)`. The negation is intentional and must not be removed. Positive error (force below setpoint) must produce negative PWM (tighter grip).

### Stage 5 — Release & Home
- Send `PWM:0` to stop grip.
- Send `PWM:{RELEASE_PWM}` (positive, e.g. +200) to open gripper.
- Re-send release PWM every 50ms until `pos <= RELEASE_TARGET` or timeout.
- Send `PWM:0`, drain 0.5s, send `PWM:0` again.

---

## 7. PID Configuration

All PID parameters live in `App.py` and are passed via `config` dict:

| Constant | Default | Notes |
|---|---|---|
| `TARGET_FORCE` | `3.0` N | Setpoint |
| `PID_KP` | `30.0` | Proportional gain |
| `PID_KI` | `13.0` | Integral gain |
| `PID_KD` | `20.0` | Derivative gain |
| `PID_ALPHA` | `0.3` | Low-pass filter coefficient on PWM output (0=smooth, 1=raw) |
| `SENSOR_GAIN` | `1.0` | Conductance slope scalar; set to `0.08` for new/replacement sensor |
| `GRIP_PWM` | `−180` | Approach PWM (before contact) |
| `RELEASE_PWM` | `+200` | Open PWM |
| `RELEASE_TARGET` | `106.0°` | Home position threshold |
| `RELEASE_TIMEOUT` | `5.0 s` | Release watchdog |
| `GRIP_DURATION` | `10.0 s` | Total grip trial duration |

**Tuning note:** Integral wind-up is prevented by: (a) only accumulating after `is_press=1`, and (b) clamping to `±100`. If steady-state error persists, increase `KI`. If oscillation occurs, reduce `KP` or increase `KD`.

**Low-Pass Filter on PWM output:** High KI/KD values can produce jerky PWM commands. `PID_ALPHA` smooths the output before sending to the motor:
```python
smoothed_pwm = (target_pwm * ALPHA) + (smoothed_pwm * (1 - ALPHA))
```
Lower alpha = smoother but slower response. The filter resets to `INITIAL_PWM` before contact is detected.

---

## 8. CSV Output Schema

Written by `App.py` (header) and `ModelInclude.py` (rows). One row per sensor packet.

| Column | Source | Notes |
|---|---|---|
| `loop_index` | App | Trial number, increments per grip |
| `t_ms` | ModelInclude | Wall-clock ms since `PWM:{GRIP_PWM}` sent (resets each loop) |
| `adc0` | ESP32 | Raw ADC channel 0 |
| `pos_deg` | ESP32 | Angular position in degrees |
| `adc1` | ESP32 | Raw ADC channel 1 |
| `resistance` | ESP32 | Raw resistance in Ohms |
| `pwm` | ESP32 | PWM value acknowledged by ESP32 |
| `material` | CLI arg | Label for training data |
| `tag` | CLI arg | Additional tag |
| `train_baseline_g` | Constant | Always `0.004369` |
| `sensor_baseline_g` | Calibration | Per-session baseline conductance |
| `shifted_cond` | Computed | Feature fed to model |
| `is_press` | Computed | Contact flag (0 or 1, latching) |
| `pred_force_n` | Model | Last predicted force (empty before first inference) |
| `pid_pwm_out` | PID | PWM value sent to motor at this timestep |

---

## 9. `prefill_buffer` — Inter-Grip Context

`App.py` maintains a `deque(maxlen=60)` that collects `[conductance, 0]` samples from idle periods (between grips). This is passed to `run_one_grip()` as `prefill_buffer`.

- Provides the model with recent sensor context before the grip starts.
- `is_press=0` for all prefill rows (no contact during idle).
- After each grip, `prefill_buffer.clear()` is called so the next grip starts fresh.

---

## 10. Key Constants (Do Not Change Without Reason)

| Constant | Value | Location | Why |
|---|---|---|---|
| `TRAIN_BASELINE_G` | `0.004369` | ModelInclude | Mean conductance from training data; anchors distribution shift |
| `SENSOR_GAIN` | `1.0` (original) / `0.08` (new sensor) | App.py → config | Rescales conductance slope for cross-hardware compatibility |
| `TARGET_HZ` | `1.8` | ModelInclude | Must match training data sample rate exactly |
| `INTERVAL` | `1 / 1.8 ≈ 0.556 s` | ModelInclude | Derived from TARGET_HZ |
| `maxlen=60` | `60` | ModelInclude | Model input sequence length |
| `threshold_res_k` | `baseline × 0.97` | ModelInclude | 3% drop = contact detection threshold |
| Resistance clamp | `0 < res_k ≤ 800 kΩ` | ModelInclude | Suppresses open-circuit spikes |
| Integral clamp | `±100` | ModelInclude | Anti-windup |
| PWM clamp | `−255…0` | ModelInclude | Grip direction only; positive = release (handled by Stage 5) |

---

## 11. Common Mistakes to Avoid

1. **Using `.predict()` instead of `model(..., training=False)`** — adds significant TF overhead inside the serial loop.
2. **Hardcoding `is_press = 1` at grip start** — the model expects a genuine contact transition; faking it corrupts the sequence.
3. **Forgetting the conductance shift** — feeding `raw_cond` directly into `scaler_X` will produce wrong predictions because the training distribution used `shifted_cond`.
3a. **Using the wrong `SENSOR_GAIN`** — with the new/replacement sensor, omitting `SENSOR_GAIN=0.08` leaves the conductance delta on the wrong scale and produces systematically wrong force predictions.
4. **Removing the negation in `clip(-pid_output, -255, 0)`** — positive error would produce zero PWM instead of tighter grip.
5. **Using global PID state** — `error_integral` and `last_error` must be local to `run_one_grip()` and reset to `0.0` at the start of each call.
6. **Changing `TARGET_HZ`** — the LSTM layers learned temporal patterns at 1.8 Hz. A different inference rate changes the effective time scale and breaks predictions.
7. **Accumulating integral before contact** — causes PWM to saturate before the gripper touches anything.

---

## 12. Operational Modes

| Mode | Entry point | Purpose |
|---|---|---|
| **Phase 1 — Data Collection** | `App.py` | Collect raw sensor data with labelled material/tag for model training |
| **Phase 2 — AI Control** | `App.py` | Run closed-loop force control with CNN-LSTM inference + PID |

Phase 1 and Phase 2 use the same `App.py`. The difference is whether the model is loaded and PID is active (always active when `Model/` files are present) versus the experiment being run purely for CSV data capture.

`Analysis2ndSensor.ipynb` handles the data pipeline between phases: it takes Phase 1 CSVs, applies feature engineering (`shifted_cond`, `is_press`), and produces a dataset ready for model retraining or evaluation.

---

## 13. Running the System

```bash
# Single manual grip per keypress
python App.py --port COM18 --material soft --tag trial1

# Commands at runtime:
#   1        → run one grip loop
#   a        → auto-loop continuously (Enter 'q' to stop)
#   mat <x>  → change material label mid-session
#   q        → quit and close CSV
```

Output CSV is saved to `data_logs/phase1_<timestamp>_<tag>.csv`.
