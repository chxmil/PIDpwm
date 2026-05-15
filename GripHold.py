"""
GripHold — hold-capable grip for the Arm-CNN material-sorting pipeline
=====================================================================
Per Update_2026-05-15_Arm-CNN Material Sorting Integration §5 (Option B).

`App.py` / `ModelInclude.py` are frozen. This is a NEW sibling module that
reproduces the CLAUDE.md §5–§7 force-control contract but splits the single
`run_one_grip()` call into THREE externally-driven phases so an object can be
carried by the haptic gripper while the arm moves:

    grip()     — Stage 1-4: approach + PID until force settles at TARGET_FORCE.
                 Returns the trial dict (same shape as run_one_grip, no probe).
    hold()     — background thread keeps the PID loop maintaining TARGET_FORCE
                 (and feeds the ESP32 watchdog) while the arm transports.
    release()  — stops the hold thread, runs Stage 5 release + home.

Contract reproduced verbatim from ModelInclude.run_one_grip():
  • conductance shift + SENSOR_GAIN          (CLAUDE.md §5)
  • dynamic latching is_press @ 0.93×baseline (CLAUDE.md §5)
  • inference at TARGET_HZ = 1.8 Hz           (CLAUDE.md §10)
  • PID sign rule  current_pwm = clip(-pid_output, -255, 0)
  • integral accumulates only after contact, clamped ±100
  • LPF on PWM (PID_ALPHA) + grip floor (-120 while force < 0.95×setpoint)

The model/scalers are imported from ModelInclude so inference is byte-identical
to the running system and TensorFlow is loaded only once.
"""

import threading
import time
import warnings
from collections import deque

import numpy as np

# Reuse the exact model + scalers the live system uses (single TF load).
from ModelInclude import model, scaler_X, scaler_y

TRAIN_BASELINE_G = 0.004369          # CLAUDE.md §10 — anchors distribution shift
TARGET_HZ        = 1.8               # CLAUDE.md §10 — must match training rate
INTERVAL         = 1.0 / TARGET_HZ   # ~0.556 s


class GripHold:
    """Stateful, three-phase grip. One instance per gripper serial port."""

    def __init__(self, ser, parse_sensor, config):
        self.ser         = ser
        self.parse       = parse_sensor
        self.cfg         = config

        self.SETPOINT  = config.get('TARGET_FORCE', 5.0)
        self.KP        = config.get('PID_KP', 15.0)
        self.KI        = config.get('PID_KI', 2.0)
        self.KD        = config.get('PID_KD', 0.5)
        self.ALPHA     = config.get('PID_ALPHA', 0.3)
        self.INITIAL   = config.get('GRIP_PWM', -180)
        self.GAIN      = config.get('SENSOR_GAIN', 1.0)
        # Hold-phase safety: auto-release if the arm never comes back.
        self.HOLD_TIMEOUT = float(config.get('HOLD_TIMEOUT', 30.0))
        # Stage-4 exit: N consecutive inference ticks at >= 0.95×setpoint = "settled".
        self.SETTLE_TICKS = int(config.get('HOLD_SETTLE_TICKS', 3))

        # PID state — local to a grip cycle, reset in grip(); never global.
        self._ei = 0.0
        self._le = 0.0
        self._lf = 0.0
        self._smoothed = float(self.INITIAL)
        self._cur_pwm  = int(self.INITIAL)
        self._is_press = 0
        self._sensor_baseline = None
        self._threshold_res_k = None
        self.baseline_res_k   = None

        # Hold thread machinery
        self._hold_stop  = threading.Event()
        self._hold_thread = None
        self._lock = threading.Lock()   # guards serial writes between grip/hold

    # ── internal: one sensor→feature step ────────────────────────────────────
    def _features(self, d):
        res_k = d['res'] / 1000.0
        if res_k <= 0 or res_k > 800:        # CLAUDE.md §10 resistance clamp
            res_k = 800.0
        raw_cond     = 1.0 / (res_k + 1e-6)
        shifted_cond = ((raw_cond - self._sensor_baseline) * self.GAIN) + TRAIN_BASELINE_G
        return res_k, shifted_cond

    def _infer(self, data_buffer):
        buf_list = list(data_buffer)
        n_pad    = 60 - len(buf_list)
        padded   = np.array([[TRAIN_BASELINE_G, 0.0]] * n_pad + buf_list,
                            dtype=np.float32)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            scaled = scaler_X.transform(padded).reshape(1, 60, 2)
        pred = model(scaled, training=False)
        return max(0.0, float(
            scaler_y.inverse_transform([[float(np.array(pred).flat[0])]])[0][0]))

    def _pid_step(self, force, dt):
        """Identical math to run_one_grip Stage 4. Returns PWM to send."""
        error      = self.SETPOINT - force
        derivative = (error - self._le) / dt if dt > 0 else 0.0
        if self._is_press:
            self._ei += error * dt
            self._ei  = float(np.clip(self._ei, -100.0, 100.0))
        pid_output = (self.KP * error) + (self.KI * self._ei) + (self.KD * derivative)
        if self._is_press:
            target_pwm = int(np.clip(-pid_output, -255, 0))   # sign rule — DO NOT remove negation
            if force < self.SETPOINT * 0.95:                  # grip floor (Report 2)
                target_pwm = min(target_pwm, -120)
            self._smoothed = (target_pwm * self.ALPHA) + (self._smoothed * (1.0 - self.ALPHA))
            cur = int(self._smoothed)
        else:
            cur = self.INITIAL
            self._smoothed = float(self.INITIAL)
        self._le = error
        return cur

    # ── PHASE 1+2: grip until force settles ──────────────────────────────────
    def grip(self, loop_idx, writer, material, tag, prefill_buffer=None):
        cfg = self.cfg
        self._ei = 0.0
        self._le = 0.0
        self._lf = 0.0
        self._smoothed = float(self.INITIAL)
        self._is_press = 0

        # STAGE 1 — buffer seed
        data_buffer = deque(list(prefill_buffer) if prefill_buffer else [], maxlen=60)

        # STAGE 2 — baseline calibration (PWM=0, 30 samples, 5 s timeout)
        self.ser.write("PWM:0")
        time.sleep(0.2)
        self.ser.drain(0.3)
        res_samples = []
        t_cal = time.time()
        while len(res_samples) < 30:
            if time.time() - t_cal > 5.0:
                break
            line = self.ser.readline()
            if not line:
                time.sleep(0.001); continue
            d = self.parse(line)
            if not d:
                continue
            rk = d['res'] / 1000.0
            if rk <= 0 or rk > 800:
                rk = 800.0
            res_samples.append(rk)
        self.baseline_res_k = float(np.mean(res_samples)) if res_samples else 250.0
        self._sensor_baseline = 1.0 / (self.baseline_res_k + 1e-6)
        self._threshold_res_k = self.baseline_res_k * 0.93
        print(f"[GripHold] baseline={self.baseline_res_k:.2f} kOhm "
              f"threshold={self._threshold_res_k:.2f} kOhm setpoint={self.SETPOINT} N")

        # STAGE 3 — approach
        self._cur_pwm = self.INITIAL
        self.ser.write(f"PWM:{self._cur_pwm}")
        grip_start = time.perf_counter()
        wall_start = time.time()
        last_infer = grip_start
        detected   = False
        pkt_count  = 0
        max_force  = 0.0
        settled    = 0
        records    = []

        # STAGE 4 — inference + PID until force settles (or GRIP_DURATION fallback)
        while True:
            if time.perf_counter() - grip_start >= cfg['GRIP_DURATION']:
                print("[GripHold] GRIP_DURATION reached before settle — entering hold anyway")
                break

            line = self.ser.readline()
            if not line:
                time.sleep(0.001); continue
            d = self.parse(line)
            if not d:
                continue

            pkt_count += 1
            now  = time.perf_counter()
            t_ms = int((time.time() - wall_start) * 1000)
            res_k, shifted_cond = self._features(d)

            if not detected and res_k < self._threshold_res_k:
                detected = True
                self._is_press = 1
                print(f"\n[GripHold][CONTACT] t={t_ms} ms R={res_k:.2f} kOhm — PID engaged")

            data_buffer.append([shifted_cond, float(self._is_press)])
            records.append({
                "t_ms":         t_ms,
                "pos":          d['pos'],
                "res":          d['res'],
                "is_press":     int(self._is_press),
                "pred_force_n": float(self._lf),
                "shifted_cond": shifted_cond,
            })
            if writer:
                writer.writerow([
                    loop_idx, t_ms, d['adc0'], d['pos'], d['adc1'], d['res'],
                    d['pwm'], material, tag, TRAIN_BASELINE_G, self._sensor_baseline,
                    shifted_cond, self._is_press, f"{self._lf:.4f}", self._cur_pwm,
                ])

            if model is not None and (now - last_infer) >= INTERVAL:
                dt = now - last_infer
                last_infer = now
                force = self._infer(data_buffer)
                self._lf = force
                max_force = max(max_force, force)
                self._cur_pwm = self._pid_step(force, dt)
                self.ser.write(f"PWM:{self._cur_pwm}")
                print(f"\r[GripHold {TARGET_HZ}Hz] F={force:.2f}/{self.SETPOINT:.1f}N "
                      f"PWM={self._cur_pwm:+4d} {'[HOLDING]' if self._is_press else '[APPROACH]'}",
                      end="", flush=True)

                if detected and force >= self.SETPOINT * 0.95:
                    settled += 1
                    if settled >= self.SETTLE_TICKS:
                        print(f"\n[GripHold] force settled ({settled} ticks ≥ "
                              f"{self.SETPOINT*0.95:.2f} N) — gripping & holding")
                        break
                else:
                    settled = 0

        if not detected:
            print(f"\n[GripHold] ⚠️ contact NEVER detected "
                  f"(R never < {self._threshold_res_k:.2f} kOhm)")

        # Start the hold thread so the object stays gripped during transport.
        self._start_hold(data_buffer)

        return {
            "pkt_count":        pkt_count,
            "trial_records":    records,
            "baseline_res_k":   self.baseline_res_k,
            "max_force":        max_force,
            "contact_detected": detected,
        }

    # ── PHASE: hold (background) ──────────────────────────────────────────────
    def _start_hold(self, data_buffer):
        self._hold_stop.clear()
        self._hold_thread = threading.Thread(
            target=self._hold_loop, args=(data_buffer,),
            name="griphold-hold", daemon=True)
        self._hold_thread.start()

    def _hold_loop(self, data_buffer):
        """Keep the PID maintaining force + feed the ESP32 watchdog until
        release() is called or HOLD_TIMEOUT expires (safety auto-release)."""
        t0         = time.perf_counter()
        last_infer = t0
        last_send  = t0
        while not self._hold_stop.is_set():
            now = time.perf_counter()
            if now - t0 >= self.HOLD_TIMEOUT:
                print(f"\n[GripHold] ⚠️ HOLD_TIMEOUT ({self.HOLD_TIMEOUT}s) — auto-releasing")
                break

            line = self.ser.readline()
            if line:
                d = self.parse(line)
                if d:
                    _, shifted_cond = self._features(d)
                    data_buffer.append([shifted_cond, float(self._is_press)])

            if (now - last_infer) >= INTERVAL and model is not None:
                dt = now - last_infer
                last_infer = now
                force = self._infer(data_buffer)
                self._lf = force
                self._cur_pwm = self._pid_step(force, dt)

            # Resend PWM at ~20 Hz so the ESP32 watchdog never drops the grip
            # during a long arm transport (run_one_grip only sends per tick;
            # transport can outlast a tick, so we refresh here).
            if now - last_send >= 0.05:
                with self._lock:
                    self.ser.write(f"PWM:{self._cur_pwm}")
                last_send = now
            time.sleep(0.002)

        # Loop exit (release() or timeout) → perform the physical release.
        self._do_release()

    # ── PHASE 3: release ─────────────────────────────────────────────────────
    def release(self):
        """Stop holding and run Stage 5 release + home. Idempotent."""
        if self._hold_thread and self._hold_thread.is_alive():
            self._hold_stop.set()
            self._hold_thread.join(timeout=self.HOLD_TIMEOUT + 5.0)
        else:
            self._do_release()

    def _do_release(self):
        cfg = self.cfg
        with self._lock:
            self.ser.write("PWM:0")
            release_pwm     = cfg.get('RELEASE_PWM', 170)
            release_target  = cfg.get('RELEASE_TARGET', 97.0)
            release_timeout = cfg.get('RELEASE_TIMEOUT', 5.0)
            print(f"\n[GripHold][STAGE 5] RELEASE PWM={release_pwm} target≤{release_target}°")
            self.ser.write(f"PWM:{release_pwm}")
            t_rel = time.time()
            last_send = t_rel
            while True:
                now_t = time.time()
                if now_t - t_rel >= release_timeout:
                    print(f"\n[GripHold] release timeout ({release_timeout}s)")
                    break
                if now_t - last_send >= 0.05:
                    self.ser.write(f"PWM:{release_pwm}")
                    last_send = now_t
                line = self.ser.readline()
                if line:
                    d = self.parse(line)
                    if d and d['pos'] <= release_target:
                        print(f"\n[GripHold] returned to {d['pos']:.1f}°")
                        break
                time.sleep(0.005)
            self.ser.write("PWM:0")
            self.ser.drain(0.5)
            self.ser.write("PWM:0")
