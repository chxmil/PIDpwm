# Material-Sorting Operating Manual (AppSort + AppArm)

How to run the **Arm + CNN material-sorting** system: the PC grips & classifies
an object, the Raspberry-Pi arm carries it to the bin for its material class.
Semi-automatic — **you confirm every grip and every drop**.

> Scope: `AppSort.py` (PC) + `Arm_Control/AppArm.py` (Pi). `App.py` /
> `ModelInclude.py` are the separate data-collection/force-control entry point
> and are **not** used here. Do **not** run `App.py` and `AppSort.py` on the
> same COM port at the same time.

---

## 1. Architecture at a glance

```
   PC (Windows)                         Raspberry Pi (192.168.50.244:5001)
 ┌───────────────────┐   HTTP /api    ┌──────────────────────────────────┐
 │ AppSort.py         │ ─────────────▶ │ AppArm.py  (Flask + joystick)    │
 │  GripHold (COM18)  │                │  PCA9685 servos 0,1,2,13,14,15   │
 │  CNN-PID / RF      │ ◀───────────── │  wait/confirm gates              │
 └───────────────────┘   responses    └──────────────────────────────────┘
        │ serial COM18
        ▼
   Haptic gripper (ESP32) — grips, holds, releases the object
```

The **haptic gripper** (COM18, PC-controlled) physically holds the object the
whole time. The arm only moves between poses; servo-15 is not used by this flow.

---

## 2. Prerequisites (one-time)

1. **Pi:** `AppArm.py` running — `python3 AppArm.py` (serves `0.0.0.0:5001`).
   Confirm reachable: open `http://192.168.50.244:5001/` from the PC browser.
2. **Gripper:** ESP32 haptic gripper connected to the PC on **COM18**.
3. **Joystick:** connected to the Pi (used for jog / confirm / interrupt).
4. **Models present** on the PC under `Model/`:
   `material_cnn_pid.keras` (+ `scaler_mat_cnn_pid.pkl`) and/or
   `material_rf.pkl` (+ `scaler_mat_rf.pkl`), plus the force model
   `my_cnn_lstm_model.keras` (+ `scaler_X.pkl`, `scaler_y.pkl`).
5. **Poses saved** in `positions.json` on the Pi — see §3.

---

## 3. positions.json (on the Pi, next to AppArm.py)

Each pose is a map of **servo channel → angle (deg)** for channels
`0,1,2,13,14` (channel 15 / gripper is excluded — the haptic gripper handles
gripping). Required keys:

| Key | Meaning |
|---|---|
| `start` | Idle/start pose between objects. Arm returns here each cycle. |
| `pregrip` | Pose where the haptic gripper is positioned around the object. |
| `4` `5` `6` | Bin poses for **Hard / Medium / Soft** (defaults). |
| `7` | **Reject** bin pose (object the classifier couldn't label). |
| `safe` | *(optional)* pose used by `safe_home()` on abort. Falls back to `home`→`start` if absent. |
| `material_bin` | *(optional)* override map, e.g. `{"Hard":"4","Medium":"5","Soft":"6"}`. |
| `reject_bin` | *(optional)* override reject pose id, e.g. `"7"`. |

Channels: `0`=Base, `1`=Shoulder, `2`=Elbow, `13`=Wrist-Vertical,
`14`=Wrist-Rotate.

Example:
```json
{
  "start":   {"0": 90, "1": 90, "2": 90, "13": 90, "14": 90},
  "pregrip": {"0": 75, "1": 60, "2": 110, "13": 85, "14": 90},
  "4": {"0": 40, "1": 70, "2": 100, "13": 90, "14": 90},
  "5": {"0": 90, "1": 70, "2": 100, "13": 90, "14": 90},
  "6": {"0": 140,"1": 70, "2": 100, "13": 90, "14": 90},
  "7": {"0": 170,"1": 70, "2": 100, "13": 90, "14": 90},
  "safe": {"0": 90, "1": 90, "2": 90, "13": 90, "14": 90}
}
```

**How to create / fix poses (two ways):**
- **Web dashboard:** open `http://192.168.50.244:5001/`, jog the arm, use the
  Save Position control (`save_pos`) with the pose name.
- **At a sort gate (recommended for drift):** when AppSort pauses at a gate,
  jog with the joystick to correct the pose, then press **joystick button 8**
  — the corrected pose is written straight back to `positions.json` under that
  gate's key (`pregrip`, or the bin id). Press **button 9** to confirm.

On AppArm start it prints which required poses are missing — fix those before a
real run.

---

## 4. Run AppSort (on the PC)

```bash
python AppSort.py --port COM18 --arm-host 192.168.50.244:5001
```

Optional args:

| Arg | Default | Use |
|---|---|---|
| `--port` | `COM18` | Gripper serial port |
| `--arm-host` | `192.168.50.244:5001` | Pi address |
| `--start-pose` | `start` | Pose name returned to each cycle |
| `--pregrip-pose` | `pregrip` | Pose for the grip gate |
| `--tag` | `sort` | Tag in the audit log |
| `--log-dir` | `data_logs` | Where `sort_log_<ts>.csv` is written |

Runtime commands (type then Enter):

| Cmd | Action |
|---|---|
| `1` | Run **one** sort cycle |
| `a` | **Auto** loop cycles (type `q` then Enter to stop) |
| `q` | Quit (releases the legacy-task lock, closes log) |

---

## 5. One cycle — what happens & what you do

| Step | System (auto) | You (joystick on the Pi) |
|---|---|---|
| 1 | Arm → `start` | — |
| 2 | Arm → `pregrip`, **GRIP GATE opens** | Jog to correct drift if needed. **Btn 8** = save corrected `pregrip`. **Btn 9** = confirm grip. |
| 3 | Gripper approaches + PID, then **holds** the object | — |
| 4 | Classify (CNN-PID → RF fallback) | — |
| 5 | Arm carries object → material bin, **DROP GATE opens** | Jog to correct bin pose if needed. **Btn 8** = save corrected bin pose. **Btn 9** = confirm drop. |
| 6 | Gripper releases (drops object) | — |
| 7 | Arm → `start`, log row written | — |

Material → bin (defaults): **Hard→4, Medium→5, Soft→6**, unclassifiable→**7**.

---

## 6. Joystick reference (Pi)

| Control | Function | Active when |
|---|---|---|
| Left stick X (axis 0) | Base (ch 0) jog | Always |
| Left stick Y (axis 1) | Shoulder (ch 1) jog | Always |
| Right stick Y (axis 3) | Elbow (ch 2) jog | Always |
| D-pad X / Y (hat) | Wrist rotate (14) / vertical (13) jog | Always |
| **Button 8** | **Save corrected pose** to `positions.json` at a sort gate | At a gate |
| **Button 9** | **Confirm** (proceed past a gate) | At a gate |
| **Button 14** | **Interrupt** — abort cycle, arm runs `safe_home()` | Always |
| Buttons 0/1/2 | Legacy random task — **locked while AppSort runs** | (disabled in sort) |

Manual jog is **always available**, including at both gates — that is how you
correct pose drift every time.

### 6.1 Web console (no joystick needed for confirm)

Open `http://192.168.50.244:5001/` — the **Material Sorting Console**:

- **Live Status** updates every second: arm state, sort mode, last sorted
  object (material → bin), and a banner that **pulses amber when a gate is
  waiting**.
- **CONFIRM** button — web equivalent of joystick btn 9 (enabled only while a
  gate is actually waiting).
- **EMERGENCY STOP** — interrupt + safe-home.
- **Teach Poses** — labelled save buttons: START, PRE-GRIP, SAFE, and
  HARD→Bin 4 / MEDIUM→Bin 5 / SOFT→Bin 6 / REJECT→Bin 7. Jog with the
  joystick, then click to save (works any time).
- Legacy random pick-place is tucked in a collapsed section and is locked
  while sort mode is on.

You can confirm a gate from **either** the joystick (btn 9) **or** the web
CONFIRM button. Pose drift correction is still done by jogging (joystick);
btn 8 or the matching web Save button persists the corrected pose.

---

## 7. Safety & limits

- **Interrupt (btn 14):** aborts the current step; the arm drives to `safe`
  (→`home`→`start`) instead of freezing mid-air.
- **Hold timeout:** the gripper auto-releases if it holds longer than
  `HOLD_TIMEOUT` (default **30 s**) — i.e. confirm the drop within ~30 s of
  the grip finishing, or the object drops early. To allow slower handling,
  raise `HOLD_TIMEOUT` in `GRIP_CONFIG` at the top of `AppSort.py`.
- **Comms failure / cycle error:** AppSort releases the gripper and sends
  `stop` to the arm (safe-stop), logs the error, and waits for your next
  command.
- **Legacy task lock:** while AppSort is running, joystick btn 0/1/2 and the
  dashboard "run task" are disabled so the random pick-place can't collide.
  Released automatically when you `q` out of AppSort.

---

## 8. Output / audit

`data_logs/sort_log_<timestamp>.csv` — one row per object:
`timestamp, loop_index, pred_label, pid_Hard/Medium/Soft,
rf_Hard/Medium/Soft, chosen, bin, max_force_n, contact, baseline_res_k,
pkt_count`. Use it to check per-class sorting accuracy.

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `no saved pose 'pregrip'` (HTTP 400) | pose missing in `positions.json` | Save it (§3); check AppArm boot log |
| Cycle aborts immediately at a gate | `SYSTEM_STATE` stuck `INTERRUPT` | Press btn 9 once / restart AppArm; btn 14 was latched |
| Gripper never reaches force | object too soft / approach too weak | check `contact` column; tune `GRIP_CONFIG` in AppSort.py |
| Classifier always `Reject` | model files missing / window too short | verify `Model/` artefacts; ensure a real grip (force settles) |
| Object drops before the bin | hold exceeded `HOLD_TIMEOUT` | confirm drop faster, or raise `HOLD_TIMEOUT` |
| Arm doesn't move on confirm | `SYSTEM_STATE`=="STOP" gating, or joystick not detected | check Pi joystick; AppArm logs joystick init |
| `ARM COMM FAILURE` | Pi unreachable / wrong `--arm-host` | ping the Pi; confirm port 5001; check Wi-Fi |

---

*Generated for Update_2026-05-15 Arm-CNN Material Sorting (+ hardening A–E).
Keep this file in sync if `AppSort.py` / `AppArm.py` behaviour changes.*
