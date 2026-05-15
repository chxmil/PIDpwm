"""
Robot Arm Control — Flask + Socket.IO backend

  PCA9685 : 6 standard servos  (channels 0, 1, 2, 13, 14, 15)
  ESP32-S3: hybrid AI-PID joint (UART on /dev/ttyAMA10)
  Dashboard: /dashboard  — live PID graph, gauges, controls
"""

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO
from adafruit_servokit import ServoKit
import board
import busio
import threading
import time
import os
import json
import random
import pygame
import math
from uart_controller import UARTController

os.environ["SDL_VIDEODRIVER"] = "dummy"

# ── Flask + Socket.IO ─────────────────────────────────────────────
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ── PCA9685 setup ─────────────────────────────────────────────────
print("Initializing I2C & Servos...")
try:
    i2c_bus = busio.I2C(board.SCL, board.SDA)
    kit = ServoKit(channels=16, i2c=i2c_bus)
except Exception as e:
    print(f"Error connecting to PCA9685: {e}")

servos = {
    0:  {'name': 'Base',      'current': 90},
    1:  {'name': 'Shoulder',  'current': 90},
    2:  {'name': 'Elbow',     'current': 90},
    13: {'name': 'Wrist Ver', 'current': 90},
    14: {'name': 'Wrist Rot', 'current': 90},
    15: {'name': 'Gripper',   'current': 90},
}

# ── Global state ──────────────────────────────────────────────────
SYSTEM_STATE        = "STOP"
POSITIONS_FILE      = "positions.json"
saved_positions     = {}
MANUAL_ACTIVE       = False
ALLOW_MANUAL_ADJUST = False
CONFIRMATION_RECEIVED = False
INTERRUPT_FLAG      = False

# ── Material → bin map (Update_2026-05-15 Arm-CNN Material Sorting) ───────────
# Used only by the new arm_goto / arm_grip_gate / arm_place endpoints.
# The legacy task_pick_random_place + joystick path are unaffected.
# Gap D: overridable from positions.json keys "material_bin" / "reject_bin"
# (falls back to these defaults if absent).
MATERIAL_BIN = {'Hard': '4', 'Medium': '5', 'Soft': '6'}
REJECT_BIN   = '7'   # unclassifiable objects (classifier returned None)

# Gap B: when True, the legacy random pick-place task (joystick btn 0/1/2 and
# /api run_task) is locked out so it cannot collide with a sort cycle.
SORT_MODE = False

# Web-console state (read by the Material Sorting Console UI via /api status).
GATE_PROMPT = ""   # non-empty while a sort gate is waiting for confirmation
LAST_SORT   = {}   # {'material':.., 'bin':.., 'ts':..} of the last placed object

# Poses AppSort.py needs present in positions.json before a real run.
REQUIRED_SORT_POSES = ['start', 'pregrip',
                       MATERIAL_BIN['Hard'], MATERIAL_BIN['Medium'],
                       MATERIAL_BIN['Soft'], REJECT_BIN]

if os.path.exists(POSITIONS_FILE):
    try:
        with open(POSITIONS_FILE, 'r') as f:
            saved_positions = json.load(f)
    except Exception:
        pass

# Gap D: pull bin map overrides out of positions.json if the researcher set
# them there (keeps bin layout out of source). Pose entries are dicts of
# channel→angle; the two config keys are a dict / a string respectively.
_mb = saved_positions.get('material_bin')
if isinstance(_mb, dict) and _mb:
    MATERIAL_BIN = {str(k): str(v) for k, v in _mb.items()}
_rb = saved_positions.get('reject_bin')
if isinstance(_rb, (str, int)):
    REJECT_BIN = str(_rb)
REQUIRED_SORT_POSES = ['start', 'pregrip',
                       *MATERIAL_BIN.values(), REJECT_BIN]

# Gap C: warn at boot if any required sort pose is missing, instead of
# failing mid-cycle after a grip has already happened.
_missing = [p for p in REQUIRED_SORT_POSES if p not in saved_positions]
if _missing:
    print(f"[Sort] ⚠️  positions.json missing required poses: {_missing} "
          f"— arm_goto/arm_place will 400 for these until saved.")
else:
    print(f"[Sort] All required sort poses present: {REQUIRED_SORT_POSES}")
print(f"[Sort] MATERIAL_BIN={MATERIAL_BIN}  REJECT_BIN={REJECT_BIN!r}")

def init_arm():
    for ch, data in servos.items():
        try:
            kit.servo[ch].set_pulse_width_range(500, 2500)
            kit.servo[ch].angle = data['current']
        except Exception:
            pass

init_arm()

# ── CPU temperature ───────────────────────────────────────────────
def get_cpu_temp():
    try:
        with open('/sys/class/thermal/thermal_zone0/temp') as f:
            return round(int(f.read()) / 1000, 1)
    except Exception:
        return None

# ── ESP32-S3 UART controller ──────────────────────────────────────
uart = UARTController(port='/dev/ttyAMA10')
_uart_ok = uart.connect()
if _uart_ok:
    print("[UART] Connected to ESP32-S3 on /dev/ttyAMA10")
else:
    print("[UART] WARNING: Could not open /dev/ttyAMA10 — ESP32 features disabled")

# ── Dashboard state-push thread (20 Hz) ───────────────────────────
def _state_push_loop():
    while True:
        try:
            state = uart.get_state()
            state['cpu_temp'] = get_cpu_temp()
            socketio.emit('state_update', state)
        except Exception as exc:
            print(f"[state-push] {exc}")
        time.sleep(0.05)

threading.Thread(target=_state_push_loop, name='state-push', daemon=True).start()

# ─────────────────────────────────────────────────────────────────
# PCA9685 servo helpers  (unchanged)
# ─────────────────────────────────────────────────────────────────
def move_servo_to(channel, angle):
    try:
        angle = max(0, min(180, angle))
        kit.servo[channel].angle = angle
        servos[channel]['current'] = angle
    except Exception:
        pass

def move_single_channel_smooth(channel, target, duration=1.0):
    global SYSTEM_STATE, MANUAL_ACTIVE, INTERRUPT_FLAG
    start = servos[channel]['current']
    diff  = target - start
    if abs(diff) < 0.5:
        return
    update_rate = 0.01
    start_time  = time.time()
    while True:
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            return
        if SYSTEM_STATE == "STOP":
            return
        elapsed  = time.time() - start_time
        if elapsed >= duration:
            move_servo_to(channel, target)
            break
        ease = (1 - math.cos((elapsed / duration) * math.pi)) / 2
        move_servo_to(channel, start + diff * ease)
        time.sleep(update_rate)

def move_to_pose_sequential(target_pose, reverse=False):
    global SYSTEM_STATE, INTERRUPT_FLAG
    sequence = [0, 14, 13, 2, 1]
    if reverse:
        sequence = sequence[::-1]
    for ch in sequence:
        if INTERRUPT_FLAG or SYSTEM_STATE in ("INTERRUPT", "STOP"):
            return
        target_angle = target_pose.get(str(ch)) or target_pose.get(ch)
        if target_angle is not None:
            move_single_channel_smooth(ch, target_angle, duration=0.8)
            if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
                return
            time.sleep(0.1)

def move_to_home_force():
    global saved_positions
    if 'home' not in saved_positions:
        return
    for ch in [1, 2, 13, 14, 0]:
        angle = saved_positions['home'].get(str(ch)) or saved_positions['home'].get(ch)
        if angle is not None:
            move_servo_to(ch, angle)
            time.sleep(0.3)

def safe_home():
    """Gap A: return the arm to a known safe pose after an interrupt/abort in
    the new sort endpoints (they otherwise freeze the arm mid-pose, possibly
    still over an object). Tries 'safe' → 'home' → 'start' in that order.
    Force-drives servos (no SYSTEM_STATE gating) so it works even after STOP."""
    for key in ('safe', 'home', 'start'):
        if key in saved_positions:
            for ch in [1, 2, 13, 14, 0]:
                angle = (saved_positions[key].get(str(ch))
                         or saved_positions[key].get(ch))
                if angle is not None:
                    move_servo_to(ch, angle)
                    time.sleep(0.3)
            print(f"[Sort] safe_home → '{key}'")
            return
    print("[Sort] ⚠️  safe_home: no 'safe'/'home'/'start' pose saved")


def wait_for_permission(step_name):
    global ALLOW_MANUAL_ADJUST, CONFIRMATION_RECEIVED, SYSTEM_STATE, INTERRUPT_FLAG
    ALLOW_MANUAL_ADJUST   = True
    CONFIRMATION_RECEIVED = False
    while not CONFIRMATION_RECEIVED:
        if INTERRUPT_FLAG or SYSTEM_STATE in ("INTERRUPT", "STOP"):
            ALLOW_MANUAL_ADJUST = False
            return
        time.sleep(0.1)
    ALLOW_MANUAL_ADJUST = False
    time.sleep(0.5)


def sort_gate(pose_key, step_name):
    """Gap E: confirmation gate for the sort endpoints WITH manual correction.

    The arm pose can drift from the saved pose; the operator must be able to
    jog and correct it every time, then persist the correction. Manual jogging
    is already continuous (joystick_thread runs regardless of state); this gate
    additionally:
      • joystick btn 8 → RE-SAVE the (corrected) current pose to
        saved_positions[pose_key] and positions.json (so the fix sticks),
      • joystick btn 9 → confirm and proceed,
      • interrupt/stop  → abort.
    Returns True if confirmed, False if interrupted. `wait_for_permission`
    (used by the legacy task) is intentionally left unchanged."""
    global ALLOW_MANUAL_ADJUST, CONFIRMATION_RECEIVED, SYSTEM_STATE, INTERRUPT_FLAG, GATE_PROMPT
    ALLOW_MANUAL_ADJUST   = True
    CONFIRMATION_RECEIVED = False
    GATE_PROMPT           = step_name      # surfaced to the web console
    print(f"[Sort] GATE '{step_name}': jog to correct drift if needed — "
          f"btn 8 = save '{pose_key}', btn 9 = confirm (or web Confirm button)")
    try:
        joy = pygame.joystick.Joystick(0)
    except Exception:
        joy = None
    while not CONFIRMATION_RECEIVED:
        if INTERRUPT_FLAG or SYSTEM_STATE in ("INTERRUPT", "STOP"):
            ALLOW_MANUAL_ADJUST = False
            GATE_PROMPT = ""
            return False
        if joy is not None:
            try:
                pygame.event.pump()
                if joy.get_button(8):          # save corrected pose
                    pose = {str(ch): servos[ch]['current']
                            for ch in [0, 1, 2, 13, 14]}
                    saved_positions[pose_key] = pose
                    try:
                        with open(POSITIONS_FILE, 'w') as f:
                            json.dump(saved_positions, f)
                        print(f"[Sort] re-saved '{pose_key}' = {pose}")
                    except Exception as e:
                        print(f"[Sort] ⚠️  save '{pose_key}' failed: {e}")
                    time.sleep(0.5)            # debounce
            except Exception:
                pass
        time.sleep(0.1)
    ALLOW_MANUAL_ADJUST = False
    GATE_PROMPT = ""
    time.sleep(0.5)
    return True


def task_pick_random_place(source_id):
    global SYSTEM_STATE, CONFIRMATION_RECEIVED, saved_positions, ALLOW_MANUAL_ADJUST, INTERRUPT_FLAG
    INTERRUPT_FLAG = False
    SYSTEM_STATE   = "RUNNING"
    try:
        move_to_pose_sequential(saved_positions['home'])
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        move_single_channel_smooth(15, 0, 0.5)
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        wait_for_permission("Step 1: Arrived Home")
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        move_to_pose_sequential(saved_positions[str(source_id)])
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        ALLOW_MANUAL_ADJUST   = True
        CONFIRMATION_RECEIVED = False
        while not CONFIRMATION_RECEIVED:
            if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
                raise Exception("User Interrupt")
            pygame.event.pump()
            joy = pygame.joystick.Joystick(0)
            if joy.get_button(8):
                current_pose = {str(ch): servos[ch]['current'] for ch in [0, 1, 2, 13, 14]}
                saved_positions[str(source_id)] = current_pose
                with open(POSITIONS_FILE, 'w') as f:
                    json.dump(saved_positions, f)
                time.sleep(0.5)
            time.sleep(0.1)
        ALLOW_MANUAL_ADJUST = False
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        move_single_channel_smooth(15, 180, 0.5)
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        time.sleep(0.5)
        move_to_pose_sequential(saved_positions['home'], reverse=True)
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        dest_id = random.choice(['4', '5', '6'])
        move_to_pose_sequential(saved_positions[dest_id])
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        wait_for_permission(f"Step 5: Ready to drop at {dest_id}")
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        move_single_channel_smooth(15, 0, 0.5)
        if INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT":
            raise Exception("User Interrupt")
        time.sleep(0.5)
        move_to_pose_sequential(saved_positions['home'], reverse=True)
    except Exception:
        pass
    move_to_home_force()
    SYSTEM_STATE          = "STOP"
    ALLOW_MANUAL_ADJUST   = False
    INTERRUPT_FLAG        = False

def move_servo_manual(channel, speed):
    global MANUAL_ACTIVE
    if abs(speed) < 0.1:
        return
    MANUAL_ACTIVE = True
    move_servo_to(channel, servos[channel]['current'] + speed * 2)

def joystick_thread():
    global SYSTEM_STATE, CONFIRMATION_RECEIVED, ALLOW_MANUAL_ADJUST, MANUAL_ACTIVE, INTERRUPT_FLAG
    try:
        pygame.init()
        pygame.joystick.init()
        pygame.display.init()
        pygame.display.set_mode((1, 1))
        joy = pygame.joystick.Joystick(0)
        joy.init()
    except Exception as e:
        print(f"Joystick init failed: {e}")
        return
    btn14_prev = False
    while True:
        pygame.event.pump()
        try:
            btn14 = joy.get_button(14)
            if btn14 and not btn14_prev:
                INTERRUPT_FLAG = True
                SYSTEM_STATE   = "INTERRUPT"
                time.sleep(0.3)
            btn14_prev = btn14
            move_servo_manual(0, joy.get_axis(0))
            move_servo_manual(1, -joy.get_axis(1))
            move_servo_manual(2, -joy.get_axis(3))
            if joy.get_numbuttons() > 7:
                if joy.get_button(6): move_servo_manual(15, -1.0)
                if joy.get_button(7): move_servo_manual(15,  1.0)
            hat = joy.get_hat(0)
            if hat[0] != 0: move_servo_manual(14,  hat[0])
            if hat[1] != 0: move_servo_manual(13, -hat[1])
            if SYSTEM_STATE == "STOP" and not SORT_MODE:   # Gap B: locked during sort
                if joy.get_button(0):
                    threading.Thread(target=task_pick_random_place, args=('1',), daemon=True).start()
                    time.sleep(0.3)
                elif joy.get_button(1):
                    threading.Thread(target=task_pick_random_place, args=('2',), daemon=True).start()
                    time.sleep(0.3)
                elif joy.get_button(2):
                    threading.Thread(target=task_pick_random_place, args=('3',), daemon=True).start()
                    time.sleep(0.3)
            if joy.get_button(9):
                if not CONFIRMATION_RECEIVED:
                    CONFIRMATION_RECEIVED = True
                    time.sleep(0.3)
            time.sleep(0.02)
        except Exception as e:
            print(f"Joystick thread error: {e}")
            time.sleep(1)

threading.Thread(target=joystick_thread, daemon=True).start()

# ─────────────────────────────────────────────────────────────────
# Existing routes  (unchanged)
# ─────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html', servos=servos)

@app.route('/api', methods=['POST'])
def api_handler():
    global SYSTEM_STATE, saved_positions, INTERRUPT_FLAG, SORT_MODE
    global CONFIRMATION_RECEIVED, GATE_PROMPT, LAST_SORT
    data = request.get_json()
    cmd  = data.get('cmd')

    if cmd == 'save_pos':
        pos_name     = data.get('name')
        current_pose = {ch: servos[ch]['current'] for ch in [0, 1, 2, 13, 14]}
        saved_positions[pos_name] = current_pose
        with open(POSITIONS_FILE, 'w') as f:
            json.dump(saved_positions, f)
        return jsonify({'status': 'saved', 'name': pos_name})

    elif cmd == 'run_task':
        source = data.get('source')
        if SORT_MODE:                                  # Gap B: locked during sort
            return jsonify({'status': 'locked',
                            'reason': 'sort mode active'})
        if SYSTEM_STATE == "RUNNING":
            return jsonify({'status': 'busy'})
        threading.Thread(target=task_pick_random_place, args=(source,), daemon=True).start()
        return jsonify({'status': 'started', 'source': source})

    elif cmd == 'stop':
        INTERRUPT_FLAG = True
        SYSTEM_STATE   = "INTERRUPT"
        return jsonify({'status': 'stopped'})

    # ── Arm-CNN material-sorting endpoints (Update_2026-05-15) ───────────────
    # Additive. Reuse move_to_pose_sequential + wait_for_permission as-is.
    elif cmd == 'status':
        return jsonify({'state': SYSTEM_STATE,
                        'confirmed': CONFIRMATION_RECEIVED,
                        'sort_mode': SORT_MODE,
                        'gate': GATE_PROMPT,          # '' when no gate waiting
                        'awaiting': bool(GATE_PROMPT) and not CONFIRMATION_RECEIVED,
                        'last_sort': LAST_SORT})

    elif cmd == 'confirm':
        # Web-console equivalent of joystick btn 9. Only acts while a gate is
        # actually waiting, so a stray click can't pre-confirm a future gate.
        if ALLOW_MANUAL_ADJUST and not CONFIRMATION_RECEIVED:
            CONFIRMATION_RECEIVED = True
            return jsonify({'status': 'confirmed', 'gate': GATE_PROMPT})
        return jsonify({'status': 'no_gate'})

    elif cmd == 'arm_goto':
        target = str(data.get('target', ''))
        if target not in saved_positions:
            return jsonify({'status': 'error',
                            'reason': f'no saved pose {target!r}'}), 400
        INTERRUPT_FLAG = False
        SYSTEM_STATE   = "RUNNING"
        move_to_pose_sequential(saved_positions[target])
        interrupted = INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT"
        SYSTEM_STATE = "STOP"
        if interrupted:
            safe_home()                                # Gap A
        return jsonify({'status': 'interrupt' if interrupted else 'at',
                        'target': target})

    elif cmd == 'arm_grip_gate':
        # Move to the pre-grip pose, then BLOCK on operator confirmation
        # (joystick btn 9) before the PC is allowed to grip.
        target = str(data.get('target', 'pregrip'))
        if target not in saved_positions:
            return jsonify({'status': 'error',
                            'reason': f'no saved pose {target!r}'}), 400
        INTERRUPT_FLAG = False
        SYSTEM_STATE   = "RUNNING"
        move_to_pose_sequential(saved_positions[target])
        confirmed = False
        if not (INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT"):
            # Gap E: jog-to-correct + persist (btn 8) + confirm (btn 9)
            confirmed = sort_gate(target, "Ready to grip")
        interrupted = (not confirmed) or INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT"
        SYSTEM_STATE = "STOP"
        if interrupted:
            safe_home()                                # Gap A
        return jsonify({'status': 'interrupt' if interrupted else 'confirmed',
                        'target': target})

    elif cmd == 'arm_place':
        # Map material → bin, carry the held object there, then BLOCK on
        # operator confirmation before the PC releases.
        material = str(data.get('material', ''))
        bin_id   = MATERIAL_BIN.get(material, REJECT_BIN)
        if bin_id not in saved_positions:
            return jsonify({'status': 'error',
                            'reason': f'no bin pose {bin_id!r}',
                            'material': material}), 400
        INTERRUPT_FLAG = False
        SYSTEM_STATE   = "RUNNING"
        move_to_pose_sequential(saved_positions[bin_id])
        confirmed = False
        if not (INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT"):
            # Gap E: correct the bin drop pose + persist before releasing
            confirmed = sort_gate(bin_id, f"Ready to drop at bin {bin_id}")
        interrupted = (not confirmed) or INTERRUPT_FLAG or SYSTEM_STATE == "INTERRUPT"
        SYSTEM_STATE = "STOP"
        if interrupted:
            safe_home()                                # Gap A
        else:
            LAST_SORT = {'material': material, 'bin': bin_id,
                         'ts': time.strftime('%H:%M:%S')}
        return jsonify({'status': 'interrupt' if interrupted else 'at_bin',
                        'bin': bin_id, 'material': material})

    elif cmd == 'sort_mode':
        # Gap B: AppSort enables this for the whole session so the legacy
        # random task can't be triggered between cycles. Idempotent.
        SORT_MODE = bool(data.get('on', True))
        return jsonify({'status': 'ok', 'sort_mode': SORT_MODE})

    return jsonify({'status': 'unknown'})

# ─────────────────────────────────────────────────────────────────
# ESP32-S3 routes  (new)
# ─────────────────────────────────────────────────────────────────
@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/api/esp32/target', methods=['POST'])
def esp32_target():
    data  = request.get_json()
    angle = float(data.get('angle', 90))
    ok    = uart.set_target_angle(angle)
    return jsonify({'status': 'ok' if ok else 'error', 'angle': angle})

@app.route('/api/esp32/pid', methods=['POST'])
def esp32_pid():
    data = request.get_json()
    ok   = uart.set_pid_gains(
        float(data.get('kp', 1.0)),
        float(data.get('ki', 0.1)),
        float(data.get('kd', 0.05)),
    )
    return jsonify({'status': 'ok' if ok else 'error'})

@app.route('/api/esp32/state')
def esp32_state():
    state = uart.get_state()
    state['cpu_temp'] = get_cpu_temp()
    return jsonify(state)

# ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5001,
                 debug=True, use_reloader=False,
                 allow_unsafe_werkzeug=True)
