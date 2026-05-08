import os
import time
import warnings
import joblib
import numpy as np
from collections import deque

os.environ.setdefault('TF_ENABLE_ONEDNN_OPTS', '0')

from tensorflow.keras.models import load_model

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_MODEL_DIR = os.path.join(_BASE_DIR, 'Model')

model     = None
scaler_X  = None
scaler_y  = None

try:
    model    = load_model(os.path.join(_MODEL_DIR, 'my_cnn_lstm_model.keras'))
    scaler_X = joblib.load(os.path.join(_MODEL_DIR, 'scaler_X.pkl'))
    scaler_y = joblib.load(os.path.join(_MODEL_DIR, 'scaler_y.pkl'))
    print("✅ Model and Scalers loaded.")
    print(f"   scaler_X: {type(scaler_X).__name__}")
    print(f"   scaler_y: {type(scaler_y).__name__}  "
          f"range=[{scaler_y.data_min_[0]:.2f}, {scaler_y.data_max_[0]:.2f}] N")

    _test_seq    = np.array([[0.004, 0]] * 30 + [[c, 1] for c in np.linspace(0.006, 0.020, 30)])
    _test_scaled = scaler_X.transform(_test_seq).reshape(1, 60, 2)
    _test_pred   = np.array(model(_test_scaled, training=False))
    _test_force  = scaler_y.inverse_transform(_test_pred.reshape(1, -1))[0][0]
    print(f"   Sanity check (synthetic grip): {_test_force:.2f} N  "
          f"{'✅ OK' if _test_force > 5 else '❌ LOW — model may need retraining'}")
except Exception as e:
    print(f"❌ Error loading files: {e}")
    print("   Inference will be skipped.")


def run_one_grip(ser, loop_idx, writer, material, tag, parse_sensor, config, prefill_buffer=None):
    """
    One complete grip trial:
      STAGE 1 — seed rolling buffer from prefill
      STAGE 2 — baseline calibration (PWM=0, 30 samples)
      STAGE 3 — start approach with INITIAL_PWM, then hand off to PID
      STAGE 4 — main loop: sensor → buffer → inference @ 1.8 Hz → PID → PWM
      STAGE 5 — release and home
    """

    # ── PID state (local per grip, never leaks between loops) ────────────────
    error_integral = 0.0
    last_error     = 0.0
    last_force     = 0.0   # numeric; seeds derivative on first tick

    SETPOINT_FORCE = config.get('TARGET_FORCE', 5.0)
    KP             = config.get('PID_KP', 15.0)
    KI             = config.get('PID_KI', 2.0)
    KD             = config.get('PID_KD', 0.5)
    ALPHA          = config.get('PID_ALPHA', 0.3)   # LPF coefficient: lower=smoother, higher=faster
    INITIAL_PWM    = config.get('GRIP_PWM', -180)   # approach PWM before contact
    SENSOR_GAIN    = config.get('SENSOR_GAIN', 1.0) # 1.0=original sensor, 0.08=new sensor

    TRAIN_BASELINE_G = 0.004369
    TARGET_HZ        = 1.8                          # must match training sampling rate
    INTERVAL         = 1.0 / TARGET_HZ              # ~0.556 s between inferences

    # ── STAGE 1: Buffer init ──────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"[STAGE 1] Seeding buffer from prefill")
    data_buffer = deque(list(prefill_buffer) if prefill_buffer else [], maxlen=60)
    print(f"  Buffer seeded: {len(data_buffer)}/60 rows")

    # ── STAGE 2: Baseline calibration (before grip, with timeout) ────────────
    print("[STAGE 2] Calibrating baseline (30 samples, PWM=0)...")
    ser.write("PWM:0")
    time.sleep(0.2)
    ser.drain(0.3)

    res_samples = []
    t_cal = time.time()
    while len(res_samples) < 30:
        if time.time() - t_cal > 5.0:
            print("  ⚠️  Calibration timeout — using collected samples")
            break
        line = ser.readline()
        if not line:
            time.sleep(0.001)
            continue
        d = parse_sensor(line)
        if not d:
            continue
        res_k = d['res'] / 1000.0
        if res_k <= 0 or res_k > 800:
            res_k = 800.0
        res_samples.append(res_k)
        print(f"  Sample {len(res_samples):2d}/30: {res_k:.2f} kOhm")

    if res_samples:
        baseline_res_k = float(np.mean(res_samples))
    else:
        baseline_res_k = 250.0
        print("  ⚠️  No calibration samples — using 250 kOhm default")

    current_sensor_baseline = 1.0 / (baseline_res_k + 1e-6)
    threshold_res_k         = baseline_res_k * 0.93   # Report 2: 0.97 -> 0.93, ให้ approach กดลึกขึ้นก่อน PID เข้ามา
    print(f"  Baseline  : {baseline_res_k:.2f} kOhm  (G={current_sensor_baseline:.5f})")
    print(f"  Threshold : {threshold_res_k:.2f} kOhm  (93% of baseline)")
    print(f"  Train G   : {TRAIN_BASELINE_G:.5f}")

    # ── STAGE 3: Begin approach ───────────────────────────────────────────────
    # Apply INITIAL_PWM so gripper starts closing.
    # PID takes full authority once contact is detected (is_press → 1).
    current_pwm = INITIAL_PWM
    print(f"\n[STAGE 3] APPROACH — PWM={current_pwm}  "
          f"(PID will engage on contact, setpoint={SETPOINT_FORCE} N @ {TARGET_HZ} Hz)")
    ser.write(f"PWM:{current_pwm}")

    grip_start = time.perf_counter()   # monotonic clock for interval math
    wall_start = time.time()           # wall clock for t_ms CSV column

    is_press   = 0
    detected   = False
    pkt_count  = 0
    last_infer = grip_start
    max_force  = 0.0

    # ── Low-Pass Filter state ────────────────────────────────────────────────
    # smoothed_pwm = (target_pwm * ALPHA) + (smoothed_pwm * (1 - ALPHA))
    # ทำหน้าที่กรองความกระตุกของเอาต์พุต PID (KI, KD สูง) ก่อนส่งให้มอเตอร์
    smoothed_pwm = float(INITIAL_PWM)

    # ── STAGE 4: Main inference + PID loop ───────────────────────────────────
    while True:
        # Check duration FIRST so we exit even if no packets arrive (e.g. ESP32 unresponsive)
        if time.perf_counter() - grip_start >= config['GRIP_DURATION']:
            break

        line = ser.readline()
        if not line:
            time.sleep(0.001)
            continue

        d = parse_sensor(line)
        if not d:
            continue

        pkt_count += 1
        now  = time.perf_counter()
        t_ms = int((time.time() - wall_start) * 1000)

        # ── Sensor processing ────────────────────────────────────────────────
        res_k = d['res'] / 1000.0
        if res_k <= 0 or res_k > 800:
            res_k = 800.0
        raw_cond     = 1.0 / (res_k + 1e-6)
        shifted_cond = ((raw_cond - current_sensor_baseline) * SENSOR_GAIN) + TRAIN_BASELINE_G

        # ── Dynamic Is_Press detection ───────────────────────────────────────
        if not detected and res_k < threshold_res_k:
            detected = True
            is_press = 1
            print(f"\n  [CONTACT] t={t_ms} ms  R={res_k:.2f} kOhm — PID engaged")

        data_buffer.append([shifted_cond, float(is_press)])

        # ── CSV: every packet; current_pwm = what was last sent ──────────────
        if writer:
            writer.writerow([
                loop_idx, t_ms, d['adc0'], d['pos'],
                d['adc1'], d['res'], d['pwm'], material, tag,
                TRAIN_BASELINE_G, current_sensor_baseline,
                shifted_cond, is_press,
                f"{last_force:.4f}",
                current_pwm,
            ])

        # ── Inference + PID @ 1.8 Hz ─────────────────────────────────────────
        if model is not None and (now - last_infer) >= INTERVAL:
            dt         = now - last_infer      # actual elapsed since last inference
            last_infer = now

            # --- Inference ---------------------------------------------------
            buf_list = list(data_buffer)
            n_pad    = 60 - len(buf_list)
            padded   = np.array(
                [[TRAIN_BASELINE_G, 0.0]] * n_pad + buf_list, dtype=np.float32
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                scaled = scaler_X.transform(padded).reshape(1, 60, 2)

            pred       = model(scaled, training=False)
            force      = max(0.0, float(
                scaler_y.inverse_transform([[float(np.array(pred).flat[0])]])[0][0]
            ))
            last_force = force
            if force > max_force:
                max_force = force

            # --- PID ---------------------------------------------------------
            error      = SETPOINT_FORCE - force
            derivative = (error - last_error) / dt if dt > 0 else 0.0

            # Only accumulate integral after contact to prevent wind-up
            # during the free approach phase
            if is_press:
                error_integral += error * dt
                error_integral  = float(np.clip(error_integral, -100.0, 100.0))

            pid_output = (KP * error) + (KI * error_integral) + (KD * derivative)
            # pid_output is positive when force < setpoint (need to grip harder).
            # Grip direction is negative PWM, so we negate before clamping:
            #   error > 0  →  pid_output > 0  →  -pid_output < 0  (tighten)
            #   error < 0  →  pid_output < 0  →  -pid_output > 0  → clipped to 0 (loosen)
            if is_press:
                target_pwm   = int(np.clip(-pid_output, -255, 0))
                # ── Grip Floor (Report 2) ────────────────────────────────────
                # ขณะที่ force < 95% ของ setpoint อย่าให้ PWM อ่อนกว่า -120
                # บังคับให้ gripper ดันต่อเนื่องจนกว่าจะถึงเป้า แล้ว PID ค่อยปรับละเอียด
                if force < SETPOINT_FORCE * 0.95:
                    target_pwm = min(target_pwm, -120)
                # ── Low-Pass Filter (Alpha Filter) ───────────────────────────
                # smoothed = target*α + previous*(1-α)
                # ป้องกัน PWM กระตุก/แกว่ง จากค่า KI=12.5, KD=7 ที่ค่อนข้างสูง
                smoothed_pwm = (target_pwm * ALPHA) + (smoothed_pwm * (1.0 - ALPHA))
                current_pwm  = int(smoothed_pwm)
            else:
                current_pwm  = INITIAL_PWM
                smoothed_pwm = float(INITIAL_PWM)   # reset filter ก่อนสัมผัสวัตถุ

            ser.write(f"PWM:{current_pwm}")
            last_error = error

            # --- Status line -------------------------------------------------
            print(
                f"\r[PID {TARGET_HZ}Hz]  "
                f"Force={force:.2f}/{SETPOINT_FORCE:.1f}N  "
                f"Err={error:+.2f}  I={error_integral:+.2f}  D={derivative:+.3f}  "
                f"PWM={current_pwm:+4d}  "
                f"{'[CONTACT]' if is_press else '[APPROACH]'}",
                end="", flush=True
            )

        # ── Duration check ───────────────────────────────────────────────────
        if now - grip_start >= config['GRIP_DURATION']:
            break

    # ── STAGE 5: Release ──────────────────────────────────────────────────────
    ser.write("PWM:0")
    print(f"\n[GRIP DONE] {pkt_count} pkts | "
          f"Contact={'YES' if detected else 'NEVER DETECTED ⚠️'} | "
          f"MaxForce={max_force:.2f} N")
    if not detected:
        print(f"  ⚠️  Resistance never fell below {threshold_res_k:.2f} kOhm")
        print("      Check: sensor connection and baseline calibration")
    elif max_force < SETPOINT_FORCE:
        print(f"  ⚠️  Force never reached setpoint: {max_force:.2f} N < {SETPOINT_FORCE:.1f} N")
        print("      Check: KP/KI gains, GRIP_DURATION, or GRIP_PWM approach strength")

    release_pwm     = config.get('RELEASE_PWM', 170)
    release_target  = config.get('RELEASE_TARGET', 97.0)
    release_timeout = config.get('RELEASE_TIMEOUT', 5.0)

    print(f"[STAGE 5] RELEASE — PWM={release_pwm}  target≤{release_target}°")
    ser.write(f"PWM:{release_pwm}")
    t_rel         = time.time()
    last_pwm_send = t_rel

    while True:
        now_t = time.time()
        if now_t - t_rel >= release_timeout:
            print(f"\n  Timeout ({release_timeout}s)")
            break
        if now_t - last_pwm_send >= 0.05:
            ser.write(f"PWM:{release_pwm}")
            last_pwm_send = now_t

        line = ser.readline()
        if line:
            d = parse_sensor(line)
            if d:
                print(
                    f"\r  POS={d['pos']:.1f}°  R={d['res']/1000:.2f} kOhm  ",
                    end="", flush=True
                )
                if d['pos'] <= release_target:
                    print(f"\n  ✅ Returned to {d['pos']:.1f}°")
                    break
        time.sleep(0.005)

    ser.write("PWM:0")
    print("  [SETTLE] 0.5s")
    ser.drain(0.5)
    ser.write("PWM:0")
    return pkt_count
