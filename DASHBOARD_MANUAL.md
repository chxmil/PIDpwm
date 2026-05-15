# Material Sorting Console - User Manual

A step-by-step guide to operating the **web dashboard** for AI material
sorting. No programming needed - this manual is for the person running the
robot.

- Dashboard URL: **http://192.168.50.244:5001/**
- Open it in any browser (PC, laptop, phone, or tablet) on the same Wi-Fi /
  network as the Raspberry Pi.

---

## 1. What this system does

You place an object at the pick spot. The PC closes the haptic gripper,
measures the squeeze, and an AI decides the material (Hard / Medium / Soft).
The arm then carries the object to that material's bin. You only have to
**watch and press CONFIRM** at two points (grip and drop) so nothing moves
unexpectedly.

```
  You place object  ->  Arm to pre-grip  ->  [CONFIRM]  ->  PC grips + AI
        ->  Arm carries to material bin  ->  [CONFIRM]  ->  drop  ->  repeat
```

Two machines work together:

| Machine | Runs | You use |
|---|---|---|
| PC (Windows) | `AppSort.py` (gripper + AI) | a terminal window |
| Raspberry Pi | `AppArm.py` (arm + this dashboard) | this **web console** |

---

## 2. Before you start (checklist)

1. Raspberry Pi powered on and `AppArm.py` running.
2. Haptic gripper plugged into the PC (COM18).
3. Joystick connected to the Pi (used to nudge/jog the arm).
4. Open the dashboard - the top-right pill should turn green **online**.
5. Poses taught at least once (Section 4). On first use they are empty.

---

## 3. The dashboard at a glance

```
+--------------------------------------------------------------+
|  Material Sorting Console               (o) online            |  <- connection
+--------------------------------------------------------------+
|  LIVE STATUS                                                  |
|  +--------------------------------------------------------+   |
|  |  No grip in progress   /   [ WAITING ] ...             |   |  <- gate banner
|  +--------------------------------------------------------+   |
|   Arm State        Sort Mode        Last Sorted              |
|   [ RUNNING ]      [ ON ]           [ Hard -> bin 4 ]        |  <- status cards
|                                                              |
|   [   CONFIRM   ]            [  EMERGENCY STOP  ]            |  <- big buttons
+--------------------------------------------------------------+
|  TEACH POSES                                                 |
|   Save START   Save PRE-GRIP   Save SAFE                     |
|   Save HARD->4 Save MEDIUM->5  Save SOFT->6  Save REJECT->7  |
+--------------------------------------------------------------+
|  > Legacy random pick & place (collapsed)                    |
+--------------------------------------------------------------+
```

### Element reference

| Element | Meaning |
|---|---|
| Connection pill (top right) | Green **online** = dashboard is talking to the Pi. Red **offline** = Pi unreachable. |
| Gate banner | Grey "No grip in progress" = nothing to do. **Amber pulsing** = the arm is waiting for you to press CONFIRM. Grey "(confirmed)" = you already confirmed. |
| Arm State | `STOP` idle, `RUNNING` moving, `INTERRUPT` stopped by you. |
| Sort Mode | `ON (sorting)` while AppSort is running (legacy random task is locked). `off` otherwise. |
| Last Sorted | The most recent result, e.g. `Soft -> bin 6`, with no need to read logs. |
| CONFIRM button | Green. Enabled **only** when the banner is amber/waiting. Same as joystick button 9. |
| EMERGENCY STOP | Red. Always works. Stops the arm and sends it to the safe pose. |
| Teach Poses | Save the current arm position into memory (Section 4). |
| Legacy section | Old random pick-place. Disabled while sorting; ignore it for normal use. |

---

## 4. First-time setup - teaching poses

The arm only knows positions you teach it. Do this once (redo if the rig
moves). Use the joystick to move the arm; use the dashboard to save.

Save these poses (each: jog the arm there, then click the button):

| Button | Teach the arm to... |
|---|---|
| **Save START** | the idle/rest position between objects |
| **Save PRE-GRIP** | hover with the gripper around the object (the pick spot) |
| **Save SAFE** | a clear, safe position (used if you hit STOP) |
| **Save HARD -> Bin 4** | above the Hard bin |
| **Save MEDIUM -> Bin 5** | above the Medium bin |
| **Save SOFT -> Bin 6** | above the Soft bin |
| **Save REJECT -> Bin 7** | above the reject bin (unknown material) |

A small toast ("Saved pose: pregrip") confirms each save. Poses are stored on
the Pi and survive restarts.

To check they are all saved: restart `AppArm.py` and read its first lines -
it prints either "All required sort poses present" or a list of missing ones.

---

## 5. Running a sorting session

### Step 1 - start the PC program
On the PC, in a terminal:
```
python AppSort.py --port COM18 --arm-host 192.168.50.244:5001
```
On the dashboard, **Sort Mode** turns `ON`.

### Step 2 - place an object
Put one object at the pick spot (the PRE-GRIP location).

### Step 3 - start a cycle
In the PC terminal type:
- `1` then Enter = sort **one** object
- `a` then Enter = **auto** loop (keep sorting; type `q` then Enter to stop)
- `q` then Enter = quit

### Step 4 - the GRIP gate
The arm moves to PRE-GRIP. The dashboard banner turns **amber: "[ WAITING ]
Ready to grip - press CONFIRM"**.

- If the gripper is correctly around the object: press **CONFIRM**.
- If it drifted: nudge the arm with the **joystick** to fix it, then either
  press joystick **button 8** (saves the corrected PRE-GRIP) or just press
  **CONFIRM** to continue without saving.

### Step 5 - automatic grip + AI
The PC closes the gripper, holds the object, and the AI classifies the
material. Nothing for you to do. The result will appear in **Last Sorted**.

### Step 6 - the DROP gate
The arm carries the object to the matching bin. Banner: **amber "[ WAITING ]
Ready to drop at bin X"**.

- Press **CONFIRM** to release the object into the bin.
- Correct the bin position first with the joystick if needed (button 8 saves
  the corrected bin pose).

### Step 7 - repeat
The arm returns to START. Place the next object (or, in auto mode, the cycle
repeats automatically). **Last Sorted** shows what just happened.

---

## 6. Confirming - web or joystick

You can clear a gate either way, whichever is handier:

- **Dashboard:** the green **CONFIRM** button (only lights up when waiting).
- **Joystick:** **button 9**.

Correcting drift (nudging the arm) is **joystick only** - the dashboard has
no jog controls. Saving a corrected pose: joystick **button 8**, or a Teach
Poses button on the dashboard.

---

## 7. Stopping and recovery

- **EMERGENCY STOP** (dashboard) or joystick **button 14**: the arm aborts
  the current move and drives itself to the SAFE pose. Arm State shows
  `INTERRUPT`, then `STOP`.
- The current object may still be in the gripper - it is released
  automatically by the PC safe-stop.
- To continue: place an object and start a new cycle (`1`) from the PC.
- The gripper also auto-releases if it holds longer than ~30 seconds, so
  confirm the DROP reasonably promptly after a grip.

---

## 8. Reading results

- **Last Sorted** on the dashboard = the most recent `material -> bin`.
- Full history: on the PC, file `data_logs/sort_log_<timestamp>.csv` - one
  row per object (predicted material, probabilities, bin, grip force).

---

## 9. Troubleshooting

| You see | Meaning | Do this |
|---|---|---|
| Pill red **offline** | Browser can't reach the Pi | Check Wi-Fi; confirm `AppArm.py` is running; refresh page |
| All status shows `--` | Pi reachable but old server version | Pi must run the current `AppArm.py` (with status/confirm) |
| CONFIRM stays greyed out | No gate is waiting yet | Wait for the amber banner; start a cycle on the PC |
| Press CONFIRM -> "No gate is waiting" | You clicked too early | Wait until the banner is amber, then press it |
| Banner never turns amber | PC cycle not started, or arm still moving | Type `1` in the PC terminal; check Arm State |
| Sort Mode stays `off` | `AppSort.py` not running on the PC | Start it (Section 5 Step 1) |
| Legacy buttons say "Locked" | Sort mode is on (correct/expected) | Ignore - use the sorting flow |
| Object dropped early | Held longer than the hold timeout | Confirm the DROP sooner after the grip |
| "no saved pose 'pregrip'" error | Pose not taught | Teach it (Section 4) |

---

## 10. Good to know (limits)

- One **pick spot** per run (PRE-GRIP). Objects must arrive at the same
  place; the system sorts unlimited objects one after another.
- Four destinations: Hard (4), Medium (5), Soft (6), Reject (7).
- Bin numbers can be remapped on the Pi via `positions.json`
  (`material_bin` / `reject_bin`) - ask the engineer if you need this.
- The dashboard never moves the arm by itself - the PC drives it and you
  approve every grip and drop.

---

## Quick reference card

```
START PC:   python AppSort.py --port COM18 --arm-host 192.168.50.244:5001
PC keys:    1 = one object   a = auto loop   q = quit
DASHBOARD:  http://192.168.50.244:5001/
WAIT amber  -> check arm -> CONFIRM (or joystick btn 9)
Drifted?    -> joystick to fix -> btn 8 to save -> CONFIRM
STOP        -> EMERGENCY STOP (or joystick btn 14) -> arm goes SAFE
Result      -> "Last Sorted" card / data_logs/sort_log_*.csv
```

*For setup/engineering details see `SORTING_MANUAL.md`. Keep this file in
sync if the dashboard changes.*
