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
| **Material Classification (Phase A)** | Random Forest (5 hand-crafted features) | ✅ Active — runs post-grip; **v4 (2026-05-13): 0.838 ± 0.018 5-fold CV** on 203 trials (Hard 70 / Medium 74 / Soft 59) auto-discovered from `data_logs/datasets/`. Soft F1 0.933 · Medium F1 0.819 · Hard F1 0.774. Residual Hard↔Medium error tracked in Issue 8 (baseline clamping) and Issue 9 (PID-overshoot feature overlap). |
| **Material Classification (Phase A-prime)** | 1D-CNN on PID-grip data, (40, 5) @ 20 Hz | 🔵 Research artefact 2026-05-13 — `Model/material_cnn_pid.keras` (v2). **CV 0.942 ± 0.024** on 260 trials (Hard 79 / Medium 112 / Soft 69). Macro F1 0.944 · Hard F1 0.929 · Medium F1 0.938 · Soft F1 0.964. **+0.104 CV vs RF v4** on comparable corpus. Not yet loaded by runtime — pending runtime swap proposal. v1 was a bug (bin-skip dropped 222/286 trials); see DevLog_2026-05-13_Phase A-prime §8. |
| **Material Classification (Phase B)** | 1D-CNN (40, 5) at 20 Hz | 🟡 Skeleton landed 2026-05-13 (Update_2026-05-13_1D-CNN Phase B Plan, Accepted). Stage 2.5 probe phase + trainer + runtime loader in place; `Model/material_cnn.keras` is generated when ≥ 30 trials/class arrive in `data_logs/datasets/probe/`. |

The running system (`App.py` + `ModelInclude.py`) does **closed-loop force control**: the CNN-LSTM predicts grip force from the tactile sensor, and a PID controller adjusts motor PWM to hold a target force.

---

## 2. File Structure

```
project/
├── Arm_Control/
|    ├── AppArm.py                      # Raspberry Pi arm control (Flask:5001). + additive arm_goto/arm_grip_gate/arm_place/status/sort_mode/confirm endpoints, MATERIAL_BIN/REJECT_BIN (config-driven via positions.json), safe_home(), sort_gate() jog+persist gate, GATE_PROMPT/LAST_SORT web state (Update_2026-05-15 + hardening A–E + sorting console); legacy joystick jog/random task/routes untouched
|    ├── templates/                     # index.html = Material Sorting Console (live status, web Confirm/Stop, teach poses); dashboard.html = ESP32-S3 PID dashboard
|    └── positions.json                 # Pi runtime poses: start, pregrip, bins (4/5/6/7), optional safe; optional material_bin/reject_bin overrides — written back by sort_gate btn 8
├── App.py                      # Entry point: serial comms, CSV logging, user commands, post-grip material classification (RF + CNN) — FROZEN
├── ModelInclude.py             # run_one_grip() — all grip logic, inference, PID, optional Stage 2.5 probe; returns trial dict (incl. probe_records) — FROZEN
├── AppSort.py                  # Arm+CNN material-sorting orchestrator (PC=master): drives AppArm over HTTP, grips via GripHold, classifies, sorts to material bin (Update_2026-05-15)
├── GripHold.py                 # Hold-capable grip for sorting: grip()→hold(bg thread)→release(); reuses ModelInclude model/scalers; reproduces CLAUDE.md §5–§7 (Update_2026-05-15)
├── MaterialClassifier.py       # Phase A (RF) runtime inference; loads Model/material_rf.pkl
├── MaterialPIDCNNClassifier.py # Phase A-prime (1D-CNN on PID-grip trace) runtime inference; loads Model/material_cnn_pid.keras
├── MaterialCNNClassifier.py    # Phase B (1D-CNN) runtime inference; loads Model/material_cnn.keras; opt-in via --probe
├── Claude Report/              # Claude's diagnostic reports (one .md per iteration)
│   ├── Issue Report/           # Open issues and architectural concerns (one .md per issue) — cleared each `today` run
│   ├── DevLog/                 # Change Report (When AI change something in code it must update here) — cleared each `today` run
│   ├── Update Report/          # Accepted fix/improvement summaries
│   ├── Daily Report/           # Combined daily archives (Daily Report YYYY-MM-DD.md)
│   └── Open Issues YYYY-MM-DD.md  # End-of-day open-issues snapshot, written by `today` clear protocol
├── Code Store/                 # Archived/reference code + offline trainers
│   ├── train_material_rf.py        # Phase A trainer (v4 — auto-discovers data_logs/datasets/; saves Model/material_rf.pkl + scaler)
│   ├── train_material_cnn_pid.py   # Phase A-prime trainer (v1 — same dataset as RF v4, 1D-CNN; saves Model/material_cnn_pid.keras + scaler)
│   ├── train_material_cnn.py       # Phase B trainer (v1 — auto-discovers data_logs/datasets/probe/; saves Model/material_cnn.keras + scaler)
│   ├── inspect_material_data.py# Per-trial QC dump for ongoing data quality audits
│   ├── analyze_material_data.py# Dataset-wide statistical audit
│   └── (Analysis2ndSensor.ipynb, PIDpwmClaude.ino, Tune.py, JupyterPython.ipynb, tes.py, ...)
├── data_logs/                  # CSV output per grip session (phase1_<timestamp>_<tag>.csv)
│   ├── datasets/               # Authoritative training corpus
│   │   ├── *.csv               # Phase A raw per-packet CSVs — auto-discovered by train_material_rf.py
│   │   ├── bin/                # Files excluded from Phase A training
│   │   └── probe/              # Phase B probe-phase CSVs (40 rows × 5 features per trial, concatenated)
│   ├── sort_log_<ts>.csv       # AppSort per-object audit (pred/probs/bin/force) — Update_2026-05-15
│   └── (live session captures: phase1_<ts>.csv + phase1_<ts>_summary.csv + optional phase1_<ts>_probe.csv)
├── Model/
│   ├── my_cnn_lstm_model.keras # Force prediction model (active)
│   ├── scaler_X.pkl            # PowerTransformer for [Conductance, Is_Press]
│   ├── scaler_y.pkl            # MinMaxScaler for Force_N target
│   ├── material_rf.pkl         # Phase A material classifier (RandomForest, 5 features)
│   ├── scaler_mat_rf.pkl       # StandardScaler for material classifier features
│   ├── material_cnn.keras      # Phase B material classifier (1D-CNN, (40,5)) — generated by Code Store/train_material_cnn.py
│   ├── scaler_mat_cnn.pkl      # Per-channel StandardScaler bundle for Phase B (incl. classes_ and window_len)
│   ├── material_cnn_pid.keras  # Phase A-prime CNN (1D-CNN on PID-grip data, research artefact) — generated by Code Store/train_material_cnn_pid.py
│   ├── scaler_mat_cnn_pid.pkl  # Per-channel StandardScaler bundle for Phase A-prime
│   ├── Train/                  # Training notebook (CNNLstm.ipynb) and processed datasets
│   └── ModelV1/                # Archived previous-version model + scalers
├── Research/                   # Article-quality artefacts (benchmarks, baselines)
│   └── material_classifier_RF_baseline_2026-05-09.md
├── .claude/
│   ├── settings.local.json
│   └── skills/
│       └── Skill.md            # Project management rules (report lifecycle, git workflow, trigger words)
├── bin/                        # Archived scripts and backup files
├── SORTING_MANUAL.md           # Operator guide for AppSort + AppArm material sorting (poses, joystick, gates, safety)
├── README.md                   # All about current project
└── CLAUDE.md                   # This file — authoritative spec for Claude Code
```

**Rule:** All grip logic lives in `ModelInclude.py::run_one_grip()`. `App.py` only handles serial setup, CSV file creation, and the user command loop. Do not put grip logic in `App.py`.
**Rule:** After Any Report the file structure may be changed, always update claude.md to reflect the current file structure And report to Claude Report folder.
**Rule:** When User command `today` → accept all Update Reports, combine every report from today into one file under `Claude Report/Daily Report/`, clear all other files in `Claude Report/` (Issue Report, DevLog, Update Report), write a remaining open-issues file, and if any mandatory research-article data was collected today add it to the `Research/` directory.
**Rule:** When User command `code is clear` and report is finished → git force-merge branch `fix/force-control` into `main`.
**Rule:** When GitHub push is needed before a code change → commit and push current work to `main` first, then apply the new code changes.
**Rule:** When User ask for Update Report claude must open report with Status Under Review at Claude Report/folder, After User accept it, claude must change the status to Accepted and move the report to `Claude Report/Update Report/` folder.
**Rule:** When user command `Issue N Review` where N is the issue number, claude must update the issue report with Status Review, After User accept it, claude must Revise Issue Report and move it to `Claude Report/Update Report/` folder, and close Issue N. and Update the status of Issue N to Accepted.
**Rule:** When user command `Apply [Any Report]` claude must apply the changes in the code and update the report to Status Accepted, And always update Implementation Sequence in Update Report. always update claude.md to reflect the current file structure.
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

### Material Classification Model (1D-CNN) — Phase B Skeleton (Update_2026-05-13)

| Parameter | Value |
|---|---|
| File | `Model/material_cnn.keras` (generated by `Code Store/train_material_cnn.py`) |
| Input shape | `(1, 40, 5)` — batch=1, 40 timesteps @ 20 Hz, 5 features |
| Features (order critical) | `[shifted_cond, delta_pos, d_cond_dt, d_dpos_dt, res_norm]` |
| Output | 3-class softmax: Hard / Medium / Soft |
| Feature scaler | `Model/scaler_mat_cnn.pkl` — per-channel `StandardScaler` (bundle includes `classes` and `window_len`) |
| Window alignment | First sample = first packet with `res_k < 0.93 × baseline_res_k` (same as PID `is_press` latch) |
| Sample rate | 20 Hz — bin-mean accumulation over 50 ms windows of raw packets |
| Architecture | Conv1D(32, k=5) → Conv1D(64, k=3) → MaxPool(2) → Conv1D(64, k=3) → GlobalAvgPool → Dropout(0.3) → Dense(32) → Dense(3, softmax). ~25k params. |
| Data source | `data_logs/datasets/probe/*.csv` — auto-discovered |

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
threshold_res_k = baseline_res_k * 0.93   # 93% of baseline (Report 2 tuning)

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

### Stage 2.5 — Probe Phase (optional, Phase B 1D-CNN)
Runs **only when `config["PROBE_ENABLED"]=True`** (set by `App.py --probe`). Between baseline calibration and approach, the gripper performs a slow constant-velocity press to capture a pre-PID deformation trajectory:

- PWM ramps linearly from `PROBE_PWM_START` (default `−80`) to `PROBE_PWM_END` (default `−150`) over `PROBE_DURATION` seconds (default `2.0`).
- Contact detection trigger is identical to Stage 4's `is_press` latch: `res_k < 0.93 × baseline_res_k`.
- After contact, packets are accumulated into 50 ms bins (20 Hz); each bin emits one (5-feature) row: `shifted_cond`, `delta_pos = pos − pos_at_contact`, `d_cond_dt`, `d_dpos_dt`, `res_norm = res_k / baseline_res_k` clipped `[0, 1.5]`.
- Capture stops at 40 rows or `PROBE_DURATION + 2.0 s` timeout (whichever first). PWM is dropped to 0 for 50 ms before Stage 3 starts.
- Output: `probe_records` field in the returned trial dict; `App.py` persists it to `data_logs/datasets/probe/phase1_<ts>_<tag>_probe.csv` and runs `MaterialCNNClassifier.classify_probe` post-grip.

Trials without contact or with fewer than 40 timesteps produce an empty `probe_records` and the CNN returns `(None, None)` — the main grip continues normally.

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
| `TARGET_FORCE` | `3.5` N | Setpoint (2026-05-12: raised from 2.5 N) |
| `PID_KP` | `70.0` | Proportional gain (2026-05-12: raised from 50 with TARGET_FORCE→3.5 N) |
| `PID_KI` | `20.0` | Integral gain (2026-05-12: 22→20 after re-tune; Report 2: originally 13→22) |
| `PID_KD` | `7.0` | Derivative gain (2026-05-12: 5→7; Report 1 had lowered from 20) |
| `PID_ALPHA` | `0.4` | LPF on PWM output (Report 2: tuned down from 0.6 — was bleeding approach pressure too fast) |
| `SENSOR_GAIN` | `1.0` | Conductance slope scalar; set to `0.08` for new/replacement sensor |
| `GRIP_PWM` | `−180` | Approach PWM (before contact) |
| `RELEASE_PWM` | `+200` | Open PWM |
| `RELEASE_TARGET` | `106.0°` | Home position threshold |
| `RELEASE_TIMEOUT` | `5.0 s` | Release watchdog |
| `GRIP_DURATION` | `8.0 s` | Total grip trial duration (Report 2: raised from 5s for integral build-up) |
| Contact threshold | `baseline × 0.93` | Resistance drop required for `is_press` (Report 2: tightened from 0.97 so approach reaches deeper compression) |
| Grip floor | `target_pwm = min(target_pwm, −120)` while `force < 0.95×setpoint` | Report 2: prevents LPF from bleeding grip below the level needed to reach setpoint |

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
| `threshold_res_k` | `baseline × 0.93` | ModelInclude | 7% drop = contact detection threshold (Report 2: was 0.97) |
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

`Code Store/Analysis2ndSensor.ipynb` handles the data pipeline between phases: it takes Phase 1 CSVs, applies feature engineering (`shifted_cond`, `is_press`), and produces a dataset ready for model retraining or evaluation. The training notebook lives at `Model/Train/CNNLstm.ipynb`.

### Phase A Material Classifier — Retrain Workflow (v4+)

`Code Store/train_material_rf.py` is **auto-discovery**: it scans the top level of `data_logs/datasets/` and infers each CSV's class from its `material` column. To retrain:

1. Collect grips via `App.py --material <hard|medium|soft>`.
2. Move the resulting raw `phase1_<ts>.csv` (not `_summary.csv`) into `data_logs/datasets/`.
3. Optionally drop summary CSVs / retired captures into `data_logs/datasets/bin/` (ignored by the scan).
4. Run `python "Code Store/train_material_rf.py"` — it prints a keep/skip log, refits the RF + scaler, and overwrites `Model/material_rf.pkl` and `Model/scaler_mat_rf.pkl`.

No `SOURCES` edit required. CSVs without a `material` label or missing required columns are skipped with a printed reason.

---

## 13. Running the System

```bash
# Single manual grip per keypress (Phase A only — no probe)
python App.py --port COM18 --material soft --tag trial1

# Phase B probe collection / live CNN inference (enables Stage 2.5):
python App.py --port COM18 --material hard --tag probe_train --probe

# Commands at runtime:
#   1        → run one grip loop
#   a        → auto-loop continuously (Enter 'q' to stop)
#   mat <x>  → change material label mid-session
#   q        → quit and close CSV
```

Output CSVs:
- `data_logs/phase1_<ts>_<tag>.csv` — per-packet (Phase A source data + live force log)
- `data_logs/phase1_<ts>_<tag>_summary.csv` — per-trial (RF + CNN predictions, probe length)
- `data_logs/datasets/probe/phase1_<ts>_<tag>_probe.csv` — per-probe-timestep (Phase B training corpus, only when `--probe`)
