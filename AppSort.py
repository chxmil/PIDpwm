"""
AppSort — Arm + CNN material-sorting orchestrator (PC = master)
===============================================================
Per Update_2026-05-15_Arm-CNN Material Sorting Integration (Accepted).

SEMI-AUTOMATIC, NOT autonomous. `wait_for_permission` on the Pi stays:
the operator confirms (joystick btn 9) before every grip and every drop;
this orchestrator BLOCKS on those gated HTTP calls.

Per object:
  1. arm_goto <start>                          (PC → Pi, short timeout)
  2. arm_grip_gate <pregrip>  → blocks on human confirm
  3. GripHold.grip()          → approach + PID, then HOLD (object stays gripped)
  4. classify  (CNN-PID primary, RF v4 fallback)
  5. arm_place <material>     → retract to SAFE (carrying), then to material
                                bin, blocks on human confirm  (servo-by-servo)
  6. GripHold.release()       → Stage-5 drop
  7. arm_goto <start>         → loop

`App.py` / `ModelInclude.py` are NOT modified — SerialPort + parse_sensor are
imported from App.py; grip logic lives in GripHold.py; classifiers are the
existing runtime modules.
"""

import argparse
import csv
import json
import os
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime

from App import SerialPort, parse_sensor          # reuse, no edits to App.py
from GripHold import GripHold

try:
    from MaterialPIDCNNClassifier import classify_pid as _classify_pid
except Exception as e:                              # pragma: no cover
    _classify_pid = None
    print(f"[AppSort] CNN-PID unavailable ({e})")
try:
    from MaterialClassifier import classify_trial as _classify_rf
except Exception as e:                              # pragma: no cover
    _classify_rf = None
    print(f"[AppSort] RF classifier unavailable ({e})")

# Same force/PID config as App.py (App.py is frozen, so duplicated here verbatim).
GRIP_CONFIG = {
    'GRIP_PWM': -180, 'GRIP_DURATION': 8.0,
    'RELEASE_PWM': 200, 'RELEASE_TARGET': 106.0, 'RELEASE_TIMEOUT': 5.0,
    'TARGET_FORCE': 3.5, 'PID_KP': 70.0, 'PID_KI': 20.0, 'PID_KD': 7.0,
    'PID_ALPHA': 0.4, 'SENSOR_GAIN': 1.0,
    'HOLD_TIMEOUT': 30.0, 'HOLD_SETTLE_TICKS': 3,
}

GATED_TIMEOUT = 600.0   # gated calls wait for a human — long
QUICK_TIMEOUT = 5.0     # moves / status — short safety timeout


class ArmClient:
    """Thin JSON-over-HTTP client for AppArm.py's /api endpoint."""

    def __init__(self, host):
        self.url = f"http://{host}/api"

    def _post(self, payload, timeout):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self.url, data=data, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())

    def goto(self, target):
        return self._post({'cmd': 'arm_goto', 'target': target}, QUICK_TIMEOUT)

    def grip_gate(self, target):
        # Blocks until the operator confirms on the Pi (joystick btn 9).
        return self._post({'cmd': 'arm_grip_gate', 'target': target}, GATED_TIMEOUT)

    def place(self, material):
        # Carries to material bin, blocks until operator confirms the drop.
        return self._post({'cmd': 'arm_place', 'material': material}, GATED_TIMEOUT)

    def sort_mode(self, on):
        # Locks out the legacy random pick-place task for the whole session
        # (manual joystick jog + pose re-save stay available).
        try:
            return self._post({'cmd': 'sort_mode', 'on': bool(on)}, QUICK_TIMEOUT)
        except Exception as e:
            print(f"[AppSort] sort_mode({on}) failed: {e}")
            return None

    def stop(self):
        try:
            return self._post({'cmd': 'stop'}, QUICK_TIMEOUT)
        except Exception:
            return None


def _classify(result):
    """CNN-PID primary, RF v4 fallback. Returns (label, pid_probs, rf_probs)."""
    recs, base = result["trial_records"], result["baseline_res_k"]
    pid_label = rf_label = None
    pid_probs = rf_probs = None
    if _classify_pid is not None and recs:
        try:
            pid_label, pid_probs = _classify_pid(recs, base)
        except Exception as e:
            print(f"[AppSort] CNN-PID error: {e}")
    if pid_label is None and _classify_rf is not None and recs:
        try:
            rf_label, rf_probs = _classify_rf(recs, base)
        except Exception as e:
            print(f"[AppSort] RF error: {e}")
    return (pid_label or rf_label), pid_probs, rf_probs


def _p(probs, k):
    if not probs:
        return ""
    v = probs.get(k)
    return "" if v is None else f"{v:.2f}"


def run_cycle(loop_idx, ser, gh, arm, args, sort_writer, sort_file):
    """One pick→grip→classify→place→release object cycle."""
    print(f"\n{'='*46}\n  SORT CYCLE {loop_idx}\n{'='*46}")

    arm.goto(args.start_pose)
    print(f"  → confirm GRIP on the Pi joystick (btn 9) to continue…")
    arm.grip_gate(args.pregrip_pose)              # blocks on human

    result = gh.grip(loop_idx, None, "", args.tag)   # grip + enter HOLD
    label, pid_probs, rf_probs = _classify(result)
    chosen = label or "Reject"
    print(f"\n  Material → {chosen}  "
          f"(CNN-PID {label or '-'} / RF fallback)  maxF={result['max_force']:.2f}N")

    print(f"  → confirm DROP on the Pi joystick (btn 9) to release…")
    place = arm.place(chosen)                     # blocks on human; maps → bin
    bin_id = (place or {}).get('bin', '?')

    gh.release()                                  # Stage-5 drop
    arm.goto(args.start_pose)

    sort_writer.writerow([
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), loop_idx,
        label or "", _p(pid_probs, "Hard"), _p(pid_probs, "Medium"), _p(pid_probs, "Soft"),
        _p(rf_probs, "Hard"), _p(rf_probs, "Medium"), _p(rf_probs, "Soft"),
        chosen, bin_id, f"{result['max_force']:.4f}",
        int(result["contact_detected"]), f"{result['baseline_res_k']:.4f}",
        result["pkt_count"],
    ])
    sort_file.flush()
    print(f"  CYCLE {loop_idx} DONE → bin {bin_id}\n{'='*46}")


def main():
    ap = argparse.ArgumentParser(description="Arm + CNN material-sorting orchestrator")
    ap.add_argument('--port', default='COM18')
    ap.add_argument('--arm-host', default='192.168.50.244:5001')
    ap.add_argument('--tag', default='sort')
    ap.add_argument('--log-dir', default='data_logs')
    ap.add_argument('--start-pose', default='start')
    ap.add_argument('--pregrip-pose', default='pregrip')
    args = ap.parse_args()

    print("=" * 55)
    print("  AppSort — Arm + CNN material sorting (semi-automatic)")
    print(f"  Gripper port : {args.port}")
    print(f"  Arm host     : {args.arm_host}")
    print("  At each gate: jog to correct drift (joystick), btn 8 = save")
    print("  the corrected pose, btn 9 = confirm. Confirm every GRIP & DROP.")
    print("=" * 55)

    ser = SerialPort(args.port)
    if not ser.open():
        return
    arm = ArmClient(args.arm_host)
    gh  = GripHold(ser, parse_sensor, GRIP_CONFIG)
    arm.sort_mode(True)   # lock legacy random task for the session (Gap B)

    os.makedirs(args.log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    sort_path = os.path.join(args.log_dir, f"sort_log_{ts}.csv")
    sort_file = open(sort_path, 'w', newline='')
    sort_writer = csv.writer(sort_file)
    sort_writer.writerow([
        "timestamp", "loop_index",
        "pred_label", "pid_Hard", "pid_Medium", "pid_Soft",
        "rf_Hard", "rf_Medium", "rf_Soft",
        "chosen", "bin", "max_force_n", "contact", "baseline_res_k", "pkt_count",
    ])
    print(f"  Sort log: {sort_path}\n")
    print("Commands:  1 = one cycle   a = auto loop   q = quit\n")

    input_q = deque()
    running = True

    def _input():
        while running:
            try:
                input_q.append(input().strip())
            except EOFError:
                break
    threading.Thread(target=_input, daemon=True).start()

    loop_idx = 0
    try:
        while running:
            time.sleep(0.05)
            if not input_q:
                continue
            cmd = input_q.popleft()
            if cmd in ('q', 'quit', 'exit'):
                running = False
            elif cmd == '1':
                loop_idx += 1
                _safe_cycle(loop_idx, ser, gh, arm, args, sort_writer, sort_file)
            elif cmd in ('a', 'auto'):
                print("  AUTO loop — type 'q' then Enter to stop\n")
                while running:
                    if input_q and input_q[0] in ('q', 'quit', 'exit', 's', 'stop'):
                        if input_q.popleft() in ('q', 'quit', 'exit'):
                            running = False
                        print("  AUTO loop stopped.")
                        break
                    loop_idx += 1
                    _safe_cycle(loop_idx, ser, gh, arm, args, sort_writer, sort_file)
            else:
                print(f"  Unknown: {cmd}")
    finally:
        arm.sort_mode(False)   # release legacy-task lock on exit
        ser.close()
        sort_file.close()
        print(f"\n  Done: {loop_idx} cycles. Sort log: {sort_path}")


def _safe_cycle(loop_idx, ser, gh, arm, args, sort_writer, sort_file):
    """Run a cycle; on any failure drop the object safely and stop the arm."""
    try:
        run_cycle(loop_idx, ser, gh, arm, args, sort_writer, sort_file)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        print(f"\n  [AppSort] ARM COMM FAILURE: {e}")
        print("  → safe-stop: releasing gripper + stopping arm")
        try:
            gh.release()
        except Exception:
            ser.write("PWM:0")
        arm.stop()
    except Exception as e:
        print(f"\n  [AppSort] cycle error: {e} — safe-stop")
        try:
            gh.release()
        except Exception:
            ser.write("PWM:0")
        arm.stop()


if __name__ == "__main__":
    main()
