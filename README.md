# Haptic Robotic Gripper — Closed-Loop Force Control

A robotic gripper system that uses a resistive tactile sensor and a CNN-LSTM neural network to measure and control grip force in real time via PID feedback.

---

## System Architecture

```
ESP32-S3 (100 Hz)
  │  D:<t>,<adc0>,<pos>,<adc1>,<resistance>,<pwm>
  ▼
App.py  ──────────────────────────────────────────────────
  │  serial setup, CSV logging, user command loop
  ▼
ModelInclude.py  run_one_grip()
  │  Stage 1: seed 60-frame buffer from idle data
  │  Stage 2: calibrate sensor baseline (30 samples at PWM=0)
  │  Stage 3: approach at GRIP_PWM until contact detected
  │  Stage 4: inference @ 1.8 Hz → PID → PWM command
  │  Stage 5: release and home
  ▼
Motor (MX1508)   PWM −255…0 = grip   PWM > 0 = open
```

---

## Hardware

| Component | Details |
|---|---|
| MCU | ESP32-S3 DevModule (USB Native CDC) |
| ADC | ADS1115 16-bit (I2C: SDA=GPIO1, SCL=GPIO2) |
| Motor driver | MX1508 (IN1=GPIO8, IN2=GPIO9) |
| Position sensor | Potentiometer on ADC Ch0 → 0–180° |
| Tactile sensor | Resistive pressure sensor on ADC Ch1 (voltage divider, R_fixed=330 kΩ) |

**Sign convention:** Negative PWM closes the gripper; positive PWM opens it.

---

## Software

### Files

```
App.py               — entry point: serial, CSV, user commands
ModelInclude.py      — run_one_grip(): all grip logic, inference, PID
PIDpwm.ino           — ESP32 firmware (100 Hz sensor stream, PWM commands)
Model/
  my_cnn_lstm_model.keras  — force prediction model
  scaler_X.pkl             — PowerTransformer for [conductance, is_press]
  scaler_y.pkl             — MinMaxScaler for Force_N
data_logs/           — output CSVs
```

### Dependencies

```
Python 3.9+
tensorflow
numpy
scikit-learn
joblib
pyserial
```

Arduino: ESP32 Arduino Core v3.x, Adafruit ADS1X15 library.

---

## Quick Start

```bash
# Single grip per keypress
python App.py --port COM18 --material soft --tag trial1

# Runtime commands:
#   1          → run one grip
#   a          → auto-loop continuously (Enter 'q' to stop)
#   mat <x>    → change material label
#   q          → quit
```

Output CSV: `data_logs/phase1_<timestamp>_<tag>.csv`

---

## Force Model

| Parameter | Value |
|---|---|
| Architecture | CNN-LSTM |
| Input | `(1, 60, 2)` — 60 timesteps × [shifted_conductance, is_press] |
| Output | Predicted force in Newtons |
| Inference rate | 1.8 Hz (must match training sample rate) |
| Inference call | `model(input, training=False)` — not `.predict()` |

### Feature Engineering

**Conductance** (linearizes pressure response):
```
raw_cond = 1.0 / (res_kOhm + 1e-6)
```

**Conductance shift** (aligns session baseline to training distribution):
```
shifted_cond = (raw_cond − session_baseline) + TRAIN_BASELINE_G
```
`TRAIN_BASELINE_G = 0.004369` — never change this.

**is_press** (dynamic contact detection, latching):
```
threshold = baseline_res_k × 0.97    # 3% resistance drop = contact
is_press latches to 1 on first detection and stays 1 for the entire grip
```

---

## PID Controller

All parameters are in `App.py` and passed via `config` dict.

| Parameter | Default | Description |
|---|---|---|
| `TARGET_FORCE` | 3.0 N | Force setpoint |
| `PID_KP` | 30.0 | Proportional gain |
| `PID_KI` | 13.0 | Integral gain |
| `PID_KD` | 20.0 | Derivative gain |
| `PID_ALPHA` | 0.3 | Low-pass filter coefficient on PWM output |
| `GRIP_PWM` | −180 | Approach PWM before contact |
| `RELEASE_PWM` | +200 | Open PWM |
| `RELEASE_TARGET` | 106.0° | Home position threshold |
| `RELEASE_TIMEOUT` | 5.0 s | Release watchdog |
| `GRIP_DURATION` | 10.0 s | Total grip trial duration |

**Sign rule:** `current_pwm = clip(−pid_output, −255, 0)`. Positive error (force below setpoint) must produce negative PWM (tighter grip). Do not remove the negation.

**Anti-windup:** Integral only accumulates after `is_press = 1`, and is clamped to ±100.

**Low-pass filter:** Smooths PWM output to suppress oscillation from high KI/KD gains:
```
smoothed_pwm = target_pwm × ALPHA + smoothed_pwm × (1 − ALPHA)
```

---

## CSV Output Schema

| Column | Source |
|---|---|
| `loop_index` | Trial number |
| `t_ms` | Wall-clock ms since grip started |
| `adc0`, `adc1` | Raw ADC values from ESP32 |
| `pos_deg` | Angular position (degrees) |
| `resistance` | Raw resistance (Ohms) |
| `pwm` | PWM acknowledged by ESP32 |
| `material`, `tag` | CLI labels |
| `train_baseline_g` | `0.004369` (constant) |
| `sensor_baseline_g` | Per-session calibrated baseline conductance |
| `shifted_cond` | Feature fed to the model |
| `is_press` | Contact flag (0 or 1, latching) |
| `pred_force_n` | Last predicted force (N) |
| `pid_pwm_out` | PWM command sent at this timestep |

---

## Serial Protocol

**115200 baud, timeout=0.1 s, default port COM18**

ESP32 → PC (100 Hz):
```
D:<t_ms>,<adc0>,<pos_deg>,<adc1>,<resistance_ohm>,<pwm>
```

PC → ESP32:
```
PWM:<-255 to 255>\n
STOP\n
```

---

## AI Models Roadmap

| Model | Purpose | Status |
|---|---|---|
| CNN-LSTM | Force prediction → PID control | Active |
| 1D-CNN | Material classification (Hard / Medium / Soft) | Planned — data collection phase |

The material classification model will use the Dynamic Force-Deformation Signature at 100 Hz, with `Δpos = pos_current − pos_at_first_contact` as a key feature.

---

## Common Mistakes

1. Using `.predict()` instead of `model(..., training=False)` — adds TF overhead in the serial loop.
2. Hardcoding `is_press = 1` at grip start — the model expects a genuine contact transition.
3. Feeding `raw_cond` directly to the scaler — always use `shifted_cond`.
4. Removing the negation in `clip(−pid_output, −255, 0)` — reverses the control direction.
5. Accumulating integral before contact — causes PWM saturation before the gripper touches anything.
6. Changing `TARGET_HZ` from 1.8 — the LSTM learned temporal patterns at this exact rate.
