"""
============================================================
  Phase 1 v2: Gripper Data Collector (Fixed)
============================================================
  CSV columns:
    loop_index, t_ms, adc0, pos_deg, adc1, resistance, pwm,
    material, tag

  t_ms = 0 ตอนส่ง PWM ไป ESP32 ทุก loop reset เป็น 0
============================================================
"""

import serial
import serial.tools.list_ports
import threading
import time
import csv
import os
import argparse
import signal
from datetime import datetime
from collections import deque
from typing import Optional
from ModelInclude import run_one_grip # import ฟังก์ชันมา

# =====================================================
#  ตัวแปรกำหนดเอง — แก้ตรงนี้
# =====================================================

GRIP_PWM = -180         # PWM บีบ (ส่งค่านี้ทีเดียวเลย)
GRIP_DURATION = 8.0    # เวลาบีบ (วินาที) — Report 2: เพิ่มเวลาให้ integral สะสมพอ
RELEASE_PWM = 200     # PWM ตอนคลาย
RELEASE_TARGET = 106.0 # ปล่อยจนองศากลับถึงค่านี้ (home ~151.5 deg, เผื่อ margin 3.5 deg)
RELEASE_TIMEOUT = 5.0 # timeout คลาย (วินาที) — เพิ่มเป็น 12s ป้องกัน timeout หลัง grip นาน
# App.py
TARGET_FORCE = 2.5  # แรงเป้าหมาย (N)


# =====================================================
#  PID Hyper-Parameter Tuning
#  เป้าหมาย: ทำเวลาให้ไวที่สุดในการไปถึง TARGET_FORCE
# -----------------------------------------------------
#  KP สูง  -> ตอบสนองเร็ว แต่เกิด overshoot ง่าย
#  KI สูง  -> ลด steady-state error เร็ว แต่ทำให้ระบบสั่น
#  KD สูง  -> ดูดซับการสั่น (damping) ป้องกันการเหวี่ยง
# -----------------------------------------------------
#  ค่าเก่า : KP=35.0, KI=1.5,  KD=1     -> ช้า, error ค้าง
#  ค่าใหม่ : KP=30.0, KI=12.5, KD=7     -> เร็วขึ้น + damped
#                                          (ต้องคู่กับ LPF)
# =====================================================
PID_KP = 50.0
PID_KI = 22.0    # Report 2: 13 -> 22, สะสม integral แรงขึ้นเพื่อดัน steady-state PWM ลึกกว่าเดิม
PID_KD = 5.0
# PID_KP = 25.0
# PID_KI = 13.0
# PID_KD = 20.0
# -----------------------------------------------------
#  Low-Pass Filter (Alpha Filter) บนเอาต์พุต PWM
#  - ช่วยกรองสัญญาณ PWM ที่กระตุกจาก KI/KD สูง
#  - alpha = 0.1  -> สมูทมาก แต่ตอบสนองช้า
#  - alpha = 0.3  -> สมดุล (ค่าเริ่มต้น)
#  - alpha = 0.9  -> เร็วแต่กระด้าง (เกือบไม่กรอง)
# =====================================================
PID_ALPHA = 0.4   # Report 2: 0.6 -> 0.4, อย่าให้ LPF bleed approach pressure เร็วเกินไป

# =====================================================
#  Sensor Gain (Cross-Hardware Compatibility)
#  - 1.0  -> original training sensor (default)
#  - 0.08 -> new/replacement sensor (per UPDATE.md)
#  Applied as: shifted_cond = (delta * SENSOR_GAIN) + TRAIN_BASELINE_G
# =====================================================
SENSOR_GAIN = 1.0

# ใน main() เพิ่มลงใน config dict

# =====================================================

def parse_sensor(line: str) -> Optional[dict]:
    """Parse: D:<t_ms>,<adc0>,<pos>,<adc1>,<resistance>,<pwm>"""
    try:
        if not line or not line.startswith("D:"):
            return None
        p = line[2:].split(',')
        if len(p) != 6:
            return None
        return {
            'esp_ms': int(p[0]),
            'adc0':   int(p[1]),
            'pos':    float(p[2]),
            'adc1':   int(p[3]),
            'res':    float(p[4]),
            'pwm':    int(p[5]),
        }
    except (ValueError, IndexError):
        return None


class SerialPort:
    """Serial ง่ายๆ — ไม่มี thread ไม่มี heartbeat"""

    def __init__(self, port):
        self.port = port
        self.ser = None

    def open(self):
        try:
            self.ser = serial.Serial(self.port, 115200, timeout=0.1)
            time.sleep(2.0)
            # Drain
            while self.ser.in_waiting:
                self.ser.readline()
            print(f"  Serial opened: {self.port}")
            return True
        except serial.SerialException as e:
            print(f"  [ERROR] {e}")
            for p in serial.tools.list_ports.comports():
                print(f"    {p.device} - {p.description}")
            return False

    def write(self, cmd: str):
        try:
            self.ser.write(f"{cmd}\n".encode())
            self.ser.flush()
        except serial.SerialException:
            pass

    def readline(self) -> Optional[str]:
        try:
            if self.ser.in_waiting:
                return self.ser.readline().decode('utf-8', errors='ignore').strip()
        except serial.SerialException:
            pass
        return None

    def drain(self, duration=0.3):
        """อ่านทิ้งให้ buffer ว่าง"""
        end = time.time() + duration
        while time.time() < end:
            try:
                if self.ser.in_waiting:
                    self.ser.readline()
                else:
                    time.sleep(0.01)
            except serial.SerialException:
                break

    def close(self):
        if self.ser:
            self.write("STOP")
            time.sleep(0.1)
            self.ser.close()


def main():
    p = argparse.ArgumentParser(description="Phase 1 v2: Gripper Loop Collector")
    p.add_argument('--port', default='COM18')
    p.add_argument('--material', default='')
    p.add_argument('--tag', default='')
    p.add_argument('--log-dir', default='data_logs')
    args = p.parse_args()

    config = {
    'GRIP_PWM': GRIP_PWM,
    'GRIP_DURATION': GRIP_DURATION,
    'RELEASE_PWM': RELEASE_PWM,
    'RELEASE_TARGET': RELEASE_TARGET,
    'RELEASE_TIMEOUT': RELEASE_TIMEOUT,
    'TARGET_FORCE': TARGET_FORCE,
    'PID_KP': PID_KP,
    'PID_KI': PID_KI,
    'PID_KD': PID_KD,
    'PID_ALPHA': PID_ALPHA,
    'SENSOR_GAIN': SENSOR_GAIN,
    }

    print()
    print("=" * 55)
    print("  Phase 1 v2: Gripper Loop Data Collector")
    print("=" * 55)
    print(f"  Port:     {args.port}")
    print(f"  Material: {args.material or '(none)'}")
    print(f"  PWM:      {GRIP_PWM}")
    print(f"  Duration: {GRIP_DURATION}s")
    print(f"  Release:  PWM={RELEASE_PWM} until {RELEASE_TARGET} deg")
    print("=" * 55)

    ser = SerialPort(args.port)
    if not ser.open():
        return

    # CSV
    os.makedirs(args.log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = f"_{args.tag}" if args.tag else ""
    csv_path = os.path.join(args.log_dir, f"phase1_{ts}{suffix}.csv")

    csv_file = open(csv_path, 'w', newline='')
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow([
        "loop_index", "t_ms", "adc0", "pos_deg",
        "adc1", "resistance", "pwm", "material", "tag",
        "train_baseline_g", "sensor_baseline_g", "shifted_cond", "is_press", "pred_force_n", "pid_pwm_out"
    ])
    print(f"  CSV: {csv_path}")

    # Verify sensor data
    print("  Checking sensor data...", end="", flush=True)
    ser.write("PWM:0")  # ensure stopped
    time.sleep(0.2)
    ser.drain(0.3)
    got = False
    for _ in range(30):
        ser.write("PWM:0")  # acts as heartbeat too
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

    print()
    print("Commands:")
    print("  1          -> run 1 grip loop")
    print("  a          -> AUTO continuous loop (type 'q' to stop)")
    print("  mat <n> -> set material label")
    print("  q          -> quit")
    print()

    # Input thread
    input_q = deque()
    running = True

    def _sig(s, f):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, _sig)

    def _input():
        while running:
            try:
                input_q.append(input().strip())
            except EOFError:
                break
    threading.Thread(target=_input, daemon=True).start()

    loop_idx = 0
    material = args.material
    total_pkts = 0

    # Rolling pre-fill buffer at packet rate (~100 Hz) to match data_buffer
    # in run_one_grip() — 33 s of context per Issue 1 / Option C.
    prefill_buffer = deque(maxlen=3333)   # 60 * (100/1.8) ≈ 3333

    while running:
        # Drain ALL available packets (not just one) so prefill captures
        # the full 100 Hz stream during idle periods.
        while True:
            line = ser.readline()
            if not line:
                break
            d = parse_sensor(line)
            if not d:
                continue
            r_kohm = d['res'] / 1000.0
            if r_kohm <= 0 or r_kohm > 800:
                r_kohm = 800.0
            conductance = 1.0 / (r_kohm + 1e-6)
            prefill_buffer.append([conductance, 0])

        ser.write("PWM:0")    # heartbeat (PWM:0 = stopped + keeps watchdog happy)

        while input_q:
            cmd = input_q.popleft()

            if cmd == '1':
                loop_idx += 1
                print(f"\n{'='*40}")
                print(f"  LOOP {loop_idx}")
                print(f"{'='*40}")

                n = run_one_grip(ser, loop_idx, csv_writer, material, args.tag, parse_sensor, config, prefill_buffer)
                total_pkts += n
                csv_file.flush()
                # Clear buffer so next grip pre-fills with fresh post-release data (new baseline drift)
                prefill_buffer.clear()

                print(f"  LOOP {loop_idx} DONE ({n} pkts)")
                print(f"{'='*40}")
                print(f"\n  พิมพ์ '1' เพื่อรอบถัดไป\n")

            elif cmd in ('a', 'auto'):
                print("  AUTO LOOP started — type 'q' to stop\n")
                while running:
                    # Check for stop command before each loop
                    if input_q and input_q[0] in ('q', 'quit', 'exit', 's', 'stop'):
                        stop_cmd = input_q.popleft()
                        if stop_cmd in ('q', 'quit', 'exit'):
                            running = False
                        print("  AUTO LOOP stopped.")
                        break

                    loop_idx += 1
                    print(f"\n{'='*40}")
                    print(f"  AUTO LOOP {loop_idx}")
                    print(f"{'='*40}")

                    n = run_one_grip(ser, loop_idx, csv_writer, material, args.tag, parse_sensor, config, prefill_buffer)
                    total_pkts += n
                    csv_file.flush()
                    prefill_buffer.clear()

                    print(f"  AUTO LOOP {loop_idx} DONE ({n} pkts)")
                    print(f"{'='*40}")

                    # Brief pause between loops so user can stop
                    print("  (Next loop in 1.5s — type 'q' then Enter to stop)")
                    for _ in range(15):
                        time.sleep(0.1)
                        if input_q and input_q[0] in ('q', 'quit', 'exit', 's', 'stop'):
                            break

            elif cmd.startswith('mat '):
                material = cmd[4:].strip()
                print(f"  Material -> {material}")

            elif cmd in ('q', 'quit', 'exit'):
                running = False

            else:
                print(f"  Unknown: {cmd}")

        time.sleep(0.05)

    # Cleanup
    ser.close()
    csv_file.close()
    print(f"\n  Done: {loop_idx} loops, {total_pkts} packets")
    print(f"  CSV: {csv_path}")


if __name__ == "__main__":
    main()