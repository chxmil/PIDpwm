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

    # ── STAGE 2.5: Probe phase (optional — Phase B 1D-CNN material classifier) ─
    # Slow constant-velocity press BEFORE PID engages. Captures the dynamic
    # force-deformation signature uncontaminated by PID overshoot.
    # Plan: Update_2026-05-13_1D-CNN Phase B Plan.md §1.1 / §1.2.
    probe_records = []
    if config.get("PROBE_ENABLED", False):
        PROBE_PWM_START = int(config.get("PROBE_PWM_START", -80))
        PROBE_PWM_END   = int(config.get("PROBE_PWM_END",  -150))
        PROBE_DURATION  = float(config.get("PROBE_DURATION", 2.0))   # seconds, ramp duration
        PROBE_BIN_S     = 0.050                                       # 50 ms → 20 Hz
        PROBE_LEN       = 40                                          # samples
        PROBE_TIMEOUT_S = PROBE_DURATION + 2.0                        # bail if no contact

        print(f"\n[STAGE 2.5] PROBE — PWM {PROBE_PWM_START} → {PROBE_PWM_END} over {PROBE_DURATION:.1f}s "
              f"(target 40 samples @ 20 Hz from first contact)")

        probe_start    = time.perf_counter()
        last_pwm_send  = probe_start
        ser.write(f"PWM:{PROBE_PWM_START}")

        contact_seen     = False
        pos_at_contact   = None
        prev_cond        = None
        prev_dpos        = None
        bin_packets      = []   # accumulate raw packets for current 50 ms bin
        bin_start        = None

        while True:
            elapsed = time.perf_counter() - probe_start

            # Linear PWM ramp; hold end value if elapsed > ramp duration
            frac      = min(elapsed / PROBE_DURATION, 1.0)
            target_pwm_probe = int(round(PROBE_PWM_START + frac * (PROBE_PWM_END - PROBE_PWM_START)))

            if time.perf_counter() - last_pwm_send >= 0.05:
                ser.write(f"PWM:{target_pwm_probe}")
                last_pwm_send = time.perf_counter()

            if elapsed >= PROBE_TIMEOUT_S:
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
            raw_cond     = 1.0 / (res_k + 1e-6)
            shifted_cond = ((raw_cond - current_sensor_baseline) * SENSOR_GAIN) + TRAIN_BASELINE_G

            # Contact detection — same threshold as PID's is_press latch
            if not contact_seen and res_k < threshold_res_k:
                contact_seen   = True
                pos_at_contact = d['pos']
                bin_start      = time.perf_counter()
                print(f"\n  [PROBE-CONTACT] t={int(elapsed*1000)} ms  R={res_k:.2f} kOhm")

            if not contact_seen:
                continue

            bin_packets.append({
                "shifted_cond": shifted_cond,
                "pos_deg":      d['pos'],
                "res_k":        res_k,
            })

            # Emit one feature row per 50 ms bin
            if time.perf_counter() - bin_start >= PROBE_BIN_S:
                if bin_packets:
                    mean_cond = float(np.mean([p["shifted_cond"] for p in bin_packets]))
                    mean_pos  = float(np.mean([p["pos_deg"]      for p in bin_packets]))
                    mean_res  = float(np.mean([p["res_k"]        for p in bin_packets]))
                    delta_pos = mean_pos - pos_at_contact
                    d_cond_dt = (mean_cond - prev_cond) / PROBE_BIN_S if prev_cond is not None else 0.0
                    d_dpos_dt = (delta_pos - prev_dpos) / PROBE_BIN_S if prev_dpos is not None else 0.0
                    res_norm  = float(np.clip(mean_res / max(baseline_res_k, 1e-6), 0.0, 1.5))
                    probe_records.append({
                        "t_ms":        int((time.perf_counter() - probe_start) * 1000),
                        "shifted_cond": mean_cond,
                        "delta_pos":    delta_pos,
                        "d_cond_dt":    d_cond_dt,
                        "d_dpos_dt":    d_dpos_dt,
                        "res_norm":     res_norm,
                    })
                    prev_cond = mean_cond
                    prev_dpos = delta_pos
                bin_packets = []
                bin_start   = time.perf_counter()

            if len(probe_records) >= PROBE_LEN:
                break

        print(f"  Probe captured {len(probe_records)}/{PROBE_LEN} samples  "
              f"(contact={'YES' if contact_seen else 'NO'})")

        # Bridge to Stage 3 — drop PWM briefly so the approach starts from a known state
        ser.write("PWM:0")
        time.sleep(0.05)

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
    records    = []   # per-packet trace for post-grip material classification

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

        records.append({
            "t_ms":         t_ms,
            "pos":          d['pos'],
            "res":          d['res'],
            "is_press":     int(is_press),
            "pred_force_n": float(last_force),
            "shifted_cond": shifted_cond,
        })

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

    return {
        "pkt_count":        pkt_count,
        "trial_records":    records,
        "probe_records":    probe_records,
        "baseline_res_k":   baseline_res_k,
        "max_force":        max_force,
        "contact_detected": detected,
    }
