"""
============================================================
  PID Hyper-Parameter Tuner (Phase 1)
============================================================
  เป้าหมาย:
    หา (KP, KI, KD) ที่ทำให้ Force ไปถึง TARGET_FORCE
    ได้เร็วที่สุด โดย overshoot/oscillation อยู่ในเกณฑ์ดี

  วิธี:
    Grid Search — ลองทุก combo ของ KP × KI × KD
    แต่ละ trial = 1 grip + release (~12-15 วิ)
    27 combos ≈ 7 นาที

  ใช้:
    python PID_Tuner.py --port COM18 --material foam
    python PID_Tuner.py --port COM18 --max-trials 9   # random subset

  Output:
    data_logs/tune_<ts>_raw.csv      → trajectory ดิบทุก trial (schema เดียวกับ App.py)
    data_logs/tune_<ts>_summary.csv  → metrics ต่อ trial + cost
    + ตาราง ranking พิมพ์ออก stdout
============================================================
"""

import argparse
import csv
import itertools
import os
import time
from collections import deque
from datetime import datetime
from typing import Any, Dict, List

import numpy as np

from App import (
    GRIP_PWM, GRIP_DURATION,
    RELEASE_PWM, RELEASE_TARGET, RELEASE_TIMEOUT,
    TARGET_FORCE, PID_ALPHA,
    SerialPort, parse_sensor,
)
from ModelInclude import run_one_grip


# =====================================================
#  Search Space (แก้ตรงนี้)
#  ค่ากึ่งกลาง = ค่าที่ App.py ใช้อยู่ (KP=30, KI=12.5, KD=7)
# =====================================================
KP_GRID = [20.0]      # -33% / 0 / +33%
KI_GRID = [12.0,  15.5, 18.0]      # -36% / 0 / +44%
KD_GRID = [20.0,  18.0, 15.0, 12.5,11.0,10.0, 9.0,8.0,7.0,6.0,5.0,4.0]      # -43% / 0 / +43%


# =====================================================
#  Cost weights (ปรับน้ำหนักความสำคัญของแต่ละ metric)
#  cost = W_TIME * time_to_target
#       + W_OVERSHOOT * overshoot
#       + W_SS_ERROR  * |target - mean(force_last_2s)|
#       + W_OSCILLATION * std(force_last_2s)
# =====================================================
W_TIME        = 1.0   # วินาที — น้ำหนักหลัก (เป้าหมาย: เร็วที่สุด)
W_OVERSHOOT   = 2.0   # 1N overshoot = 2s ของเวลาช้า → ลงโทษหนัก กันบีบเสียวัตถุ
W_SS_ERROR    = 1.0   # 1N SSE = 1s
W_OSCILLATION = 0.5   # 1N std = 0.5s

# =====================================================
#  Constants
# =====================================================
TIME_TO_TARGET_RATIO = 0.95   # ถือว่าถึงเป้าหมายเมื่อ force >= 95% ของ TARGET_FORCE
SETTLING_WINDOW_S    = 2.0    # ช่วงท้าย (วินาที) ที่ใช้วัด SSE/oscillation
PREFILL_TIME_S       = 3.0    # เวลาเก็บข้อมูล idle ก่อนเริ่ม trial
INTER_TRIAL_PAUSE_S  = 1.5    # พักหลัง release ก่อน trial ถัดไป


class CapturingWriter:
    """ห่อ csv.writer + เก็บ rows ของ trial ปัจจุบันไว้ใน list"""
    def __init__(self, real_writer):
        self.w = real_writer
        self.buf: List[List[Any]] = []

    def writerow(self, row):
        self.w.writerow(row)
        self.buf.append(list(row))

    def reset(self):
        self.buf.clear()


def evaluate_trial(rows: List[List[Any]], target_force: float) -> Dict[str, Any]:
    """
    คำนวณ metrics จาก rows ของ 1 trial

    CSV column indices (จาก App.py):
       1: t_ms
      12: is_press
      13: pred_force_n
    """
    if not rows:
        return _empty_metrics()

    arr_t  = np.array([float(r[1])  for r in rows])     # ms
    arr_ip = np.array([int(r[12])   for r in rows])
    arr_F  = np.array([float(r[13]) for r in rows])

    # หาจุดสัมผัสครั้งแรก (is_press 0 → 1)
    contact = np.where(arr_ip == 1)[0]
    if len(contact) == 0:
        # ไม่เคยสัมผัสวัตถุ → trial เสีย
        m = _empty_metrics()
        m['max_force_n'] = float(arr_F.max())
        return m

    contact_i = contact[0]
    t0_ms     = arr_t[contact_i]

    # ── Time-to-target (วัดจาก contact) ────────────────────────────────
    threshold = TIME_TO_TARGET_RATIO * target_force
    above     = np.where(arr_F >= threshold)[0]
    above_pc  = above[above >= contact_i]   # หลัง contact เท่านั้น

    if len(above_pc) == 0:
        time_to_target_s = float('inf')
        reached = False
    else:
        time_to_target_s = (arr_t[above_pc[0]] - t0_ms) / 1000.0
        reached = True

    # ── Overshoot ──────────────────────────────────────────────────────
    overshoot_n = max(0.0, float(arr_F.max()) - target_force)

    # ── Steady-state metrics: ช่วง SETTLING_WINDOW_S สุดท้าย ───────────
    last_t_ms   = arr_t[-1]
    settle_mask = arr_t >= (last_t_ms - SETTLING_WINDOW_S * 1000.0)
    if settle_mask.any():
        ss_mean       = float(np.mean(arr_F[settle_mask]))
        ss_error_n    = abs(target_force - ss_mean)
        oscillation_n = float(np.std(arr_F[settle_mask]))
    else:
        ss_error_n    = abs(target_force - float(arr_F[-1]))
        oscillation_n = 0.0

    # ── Cost (รวมน้ำหนัก) ──────────────────────────────────────────────
    cost = (W_TIME        * time_to_target_s
          + W_OVERSHOOT   * overshoot_n
          + W_SS_ERROR    * ss_error_n
          + W_OSCILLATION * oscillation_n)

    return {
        'time_to_target_s': time_to_target_s,
        'overshoot_n':      overshoot_n,
        'ss_error_n':       ss_error_n,
        'oscillation_n':    oscillation_n,
        'max_force_n':      float(arr_F.max()),
        'reached_target':   reached,
        'cost':             cost,
    }


def _empty_metrics():
    return {
        'time_to_target_s': float('inf'),
        'overshoot_n':      0.0,
        'ss_error_n':       float('inf'),
        'oscillation_n':    0.0,
        'max_force_n':      0.0,
        'reached_target':   False,
        'cost':             float('inf'),
    }


def prefill_buffer_for(seconds: float, ser: SerialPort) -> deque:
    """เก็บข้อมูล idle (PWM=0) เป็นเวลาที่กำหนด เพื่อ seed buffer"""
    buf = deque(maxlen=60)
    end = time.time() + seconds
    while time.time() < end:
        ser.write("PWM:0")
        line = ser.readline()
        if line:
            d = parse_sensor(line)
            if d:
                r_kohm = d['res'] / 1000.0
                if r_kohm <= 0 or r_kohm > 800:
                    r_kohm = 800.0
                buf.append([1.0 / (r_kohm + 1e-6), 0])
        time.sleep(0.005)
    return buf


def main():
    p = argparse.ArgumentParser(description="PID Hyper-Parameter Tuner")
    p.add_argument('--port', default='COM18')
    p.add_argument('--material', default='')
    p.add_argument('--tag', default='tune')
    p.add_argument('--log-dir', default='data_logs')
    p.add_argument('--max-trials', type=int, default=0,
                   help="0 = รัน full grid; ถ้า >0 จะ random sample N combos")
    p.add_argument('--seed', type=int, default=42,
                   help="random seed สำหรับ --max-trials")
    args = p.parse_args()

    # สร้าง trial list
    full_combos = list(itertools.product(KP_GRID, KI_GRID, KD_GRID))
    if args.max_trials and args.max_trials < len(full_combos):
        rng = np.random.default_rng(seed=args.seed)
        idx = rng.choice(len(full_combos), size=args.max_trials, replace=False)
        combos = [full_combos[i] for i in sorted(idx)]
    else:
        combos = full_combos

    # ── Banner ────────────────────────────────────────────────────────
    print()
    print("=" * 60)
    print("  PID Hyper-Parameter Tuner")
    print("=" * 60)
    print(f"  Port           : {args.port}")
    print(f"  Material       : {args.material or '(none)'}")
    print(f"  TARGET_FORCE   : {TARGET_FORCE} N")
    print(f"  PID_ALPHA      : {PID_ALPHA} (LPF — fixed)")
    print(f"  Search space   : {len(KP_GRID)}×{len(KI_GRID)}×{len(KD_GRID)} "
          f"= {len(full_combos)} combos")
    print(f"  Trials to run  : {len(combos)}")
    print(f"  Est. time      : ~{len(combos)*16/60:.1f} min")
    print(f"  Cost weights   : time={W_TIME}  OS={W_OVERSHOOT}  "
          f"SSE={W_SS_ERROR}  OSC={W_OSCILLATION}")
    print("=" * 60)

    # ── Open serial ───────────────────────────────────────────────────
    ser = SerialPort(args.port)
    if not ser.open():
        return

    # ── Open CSVs ─────────────────────────────────────────────────────
    os.makedirs(args.log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{args.tag}" if args.tag else ""
    raw_path     = os.path.join(args.log_dir, f"tune_{ts}{suffix}_raw.csv")
    summary_path = os.path.join(args.log_dir, f"tune_{ts}{suffix}_summary.csv")

    raw_file   = open(raw_path, 'w', newline='')
    raw_writer = csv.writer(raw_file)
    raw_writer.writerow([
        "loop_index", "t_ms", "adc0", "pos_deg",
        "adc1", "resistance", "pwm", "material", "tag",
        "train_baseline_g", "sensor_baseline_g", "shifted_cond",
        "is_press", "pred_force_n", "pid_pwm_out",
    ])
    capturer = CapturingWriter(raw_writer)
    print(f"  Raw CSV     : {raw_path}")
    print(f"  Summary CSV : {summary_path}")

    # ── Sensor sanity check ──────────────────────────────────────────
    print("\n  Checking sensor data...", end="", flush=True)
    ser.write("PWM:0")
    time.sleep(0.2)
    ser.drain(0.3)
    got = False
    for _ in range(30):
        ser.write("PWM:0")
        line = ser.readline()
        if line:
            d = parse_sensor(line)
            if d:
                print(f" OK (POS={d['pos']:.1f} R={d['res']:.0f})")
                got = True
                break
        time.sleep(0.05)
    if not got:
        print(" WARNING: no data!")

    # ── Run trials ────────────────────────────────────────────────────
    results: List[Dict[str, Any]] = []
    base_config = {
        'GRIP_PWM':        GRIP_PWM,
        'GRIP_DURATION':   GRIP_DURATION,
        'RELEASE_PWM':     RELEASE_PWM,
        'RELEASE_TARGET':  RELEASE_TARGET,
        'RELEASE_TIMEOUT': RELEASE_TIMEOUT,
        'TARGET_FORCE':    TARGET_FORCE,
        'PID_ALPHA':       PID_ALPHA,
    }

    try:
        for trial_idx, (kp, ki, kd) in enumerate(combos, start=1):
            cfg = {**base_config, 'PID_KP': kp, 'PID_KI': ki, 'PID_KD': kd}

            print(f"\n{'='*60}")
            print(f"  TRIAL {trial_idx}/{len(combos)}  "
                  f"KP={kp:>5.1f}  KI={ki:>5.1f}  KD={kd:>5.1f}")
            print(f"{'='*60}")

            # Pre-fill buffer ด้วยข้อมูล idle สด ๆ
            prefill = prefill_buffer_for(PREFILL_TIME_S, ser)

            # รัน grip
            capturer.reset()
            n_pkts = run_one_grip(
                ser, trial_idx, capturer,
                args.material, args.tag,
                parse_sensor, cfg, prefill,
            )
            raw_file.flush()

            # คำนวณ metrics
            m = evaluate_trial(capturer.buf, TARGET_FORCE)
            row = {
                'trial': trial_idx, 'KP': kp, 'KI': ki, 'KD': kd,
                'pkts': n_pkts, **m,
            }
            results.append(row)

            # สรุปบรรทัดเดียว
            ttt = m['time_to_target_s']
            ttt_s  = f"{ttt:.2f}s" if ttt != float('inf') else "FAIL"
            cost_s = f"{m['cost']:.3f}" if m['cost'] != float('inf') else "inf"
            reach  = "✅" if m['reached_target'] else "❌"
            print(f"\n  → {reach} TtT={ttt_s}  OS={m['overshoot_n']:.2f}N  "
                  f"SSE={m['ss_error_n']:.2f}N  OSC={m['oscillation_n']:.3f}  "
                  f"COST={cost_s}")

            # Cool-down
            print(f"  (cool-down {INTER_TRIAL_PAUSE_S}s)")
            time.sleep(INTER_TRIAL_PAUSE_S)

    except KeyboardInterrupt:
        print("\n\n  ⚠️  Interrupted — saving partial results")

    # ── Write summary CSV ─────────────────────────────────────────────
    fieldnames = ['trial', 'KP', 'KI', 'KD', 'pkts',
                  'time_to_target_s', 'overshoot_n', 'ss_error_n',
                  'oscillation_n', 'max_force_n', 'reached_target', 'cost']
    with open(summary_path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, '') for k in fieldnames})

    # ── Ranking ───────────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  RESULTS — sorted by cost (lower = better)")
    print("=" * 70)
    print(f"  {'rank':<5}{'KP':>6}{'KI':>7}{'KD':>6}   "
          f"{'TtT(s)':>7}{'OS(N)':>7}{'SSE(N)':>7}{'OSC':>6}   {'COST':>8}")
    print("  " + "-" * 66)

    ranked = sorted(results, key=lambda r: r['cost'])
    for rank, r in enumerate(ranked, start=1):
        ttt    = r['time_to_target_s']
        ttt_s  = f"{ttt:7.2f}" if ttt != float('inf') else "    inf"
        cost   = r['cost']
        cost_s = f"{cost:8.3f}" if cost != float('inf') else "     inf"
        print(f"  {rank:<5}{r['KP']:>6.1f}{r['KI']:>7.1f}{r['KD']:>6.1f}   "
              f"{ttt_s}{r['overshoot_n']:>7.2f}{r['ss_error_n']:>7.2f}"
              f"{r['oscillation_n']:>6.3f}   {cost_s}")

    if ranked and ranked[0]['cost'] != float('inf'):
        best = ranked[0]
        print()
        print("=" * 70)
        print(f"  🏆 BEST: KP={best['KP']}  KI={best['KI']}  KD={best['KD']}")
        print(f"          time-to-target = {best['time_to_target_s']:.2f} s")
        print(f"          overshoot      = {best['overshoot_n']:.2f} N")
        print(f"          SSE            = {best['ss_error_n']:.2f} N")
        print(f"          oscillation    = {best['oscillation_n']:.3f} N std")
        print()
        print(f"  → คัดลอกไปแก้ใน App.py:")
        print(f"      PID_KP = {best['KP']}")
        print(f"      PID_KI = {best['KI']}")
        print(f"      PID_KD = {best['KD']}")
        print("=" * 70)
    else:
        print("\n  ⚠️  ไม่มี trial ใดถึง target — ตรวจ hardware/material/baseline")

    # Cleanup
    ser.close()
    raw_file.close()
    print(f"\n  Raw CSV     : {raw_path}")
    print(f"  Summary CSV : {summary_path}")


if __name__ == "__main__":
    main()