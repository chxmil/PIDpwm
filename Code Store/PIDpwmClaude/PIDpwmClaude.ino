/*
 * ============================================================
 *  ESP32-S3 Data Collector — Phase 1 (CNN Training Data)
 *  อิงจากโค้ด Debug ที่เวิร์กแล้ว + เพิ่ม USB CDC Protocol สำหรับ PC
 * ============================================================
 *  Hardware (เหมือน Debug):
 *    - ESP32-S3 DevModule (USB Native CDC)
 *    - ADS1115 16-bit ADC (I2C: SDA=1, SCL=2)
 *      - Ch0: Potentiometer (Position)
 *      - Ch1: Voltage Divider (Pressure Sensor)
 *    - MX1508 Motor Driver (IN1=GPIO8, IN2=GPIO9)
 *
 *  Requires: ESP32 Arduino Core v3.x, Adafruit ADS1X15
 *
 *  Protocol (USB CDC):
 *    ESP32 → PC (100Hz):
 *      "D:<t_ms>,<adc0>,<pos_deg>,<adc1>,<resistance>,<pwm>\n"
 *
 *    PC → ESP32 (Commands):
 *      "PWM:<-255~255>"   → สั่ง PWM ตรง
 *      "STOP"             → หยุดมอเตอร์
 * ============================================================
 */

#include <Wire.h>
#include <Adafruit_ADS1X15.h>

// ===================== Pin / Config ========================
#define I2C_SDA        1
#define I2C_SCL        2
#define MOTOR_IN1      8
#define MOTOR_IN2      9

#define PWM_FREQ       20000
#define PWM_RESOLUTION 8      // 0-255

// Sensor (เหมือน Debug ทุกอย่าง)
#define R_FIXED        330000.0f
#define VIN            3.3f
#define POT_ADC_MIN    0
#define POT_ADC_MAX    26000
#define POT_DEG_MIN    0.0f
#define POT_DEG_MAX    180.0f

// Timing
#define SENSOR_INTERVAL_US  20000   // 20ms = 50Hz (Issue 7: was 10ms; ADS1115 at 128SPS needs more loop time)
// Watchdog removed — PC controls motor directly

// ===================== Globals =============================
Adafruit_ADS1115 ads;
bool adsReady      = false;
bool motorEnabled  = true;
int  currentPWM    = 0;

unsigned long lastSensorRead  = 0;

// ===================== Helpers =============================
float mapFloat(float x, float in_min, float in_max, float out_min, float out_max) {
  return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

void setMotor(int pwm) {
  currentPWM = constrain(pwm, -255, 255);
  if (!motorEnabled) return;

  if (currentPWM > 0) {
    ledcWrite(MOTOR_IN1, currentPWM);
    ledcWrite(MOTOR_IN2, 0);
  } else if (currentPWM < 0) {
    ledcWrite(MOTOR_IN1, 0);
    ledcWrite(MOTOR_IN2, -currentPWM);
  } else {
    ledcWrite(MOTOR_IN1, 0);
    ledcWrite(MOTOR_IN2, 0);
  }
}

void stopMotor() {
  currentPWM = 0;
  ledcWrite(MOTOR_IN1, 0);
  ledcWrite(MOTOR_IN2, 0);
}

// ===================== SETUP ===============================
void setup() {
  Serial.begin(115200);
  delay(500);

  // I2C (เหมือน Debug)
  Wire.begin(I2C_SDA, I2C_SCL);
  Wire.setClock(400000);

  // ADS1115 (เหมือน Debug)
  if (ads.begin()) {
    adsReady = true;
    ads.setGain(GAIN_ONE);
    ads.setDataRate(RATE_ADS1115_128SPS);   // Issue 7 H2: was 860SPS; 128SPS gives S/H time to acquire AIN1 through ~226k source impedance
  } else {
    Serial.println("ERR:ADS_NOT_FOUND");
  }

  // Motor PWM — v3.x API (เหมือน Debug)
  ledcAttach(MOTOR_IN1, PWM_FREQ, PWM_RESOLUTION);
  ledcAttach(MOTOR_IN2, PWM_FREQ, PWM_RESOLUTION);
  stopMotor();

  // Issue 7 boot self-test: read AIN1 once at idle, print computed R.
  // Operator can spot-check ADC chain matches multimeter without opening a CSV.
  if (adsReady) {
    delay(50);
    int16_t self_v = ads.readADC_SingleEnded(1);
    float vout = self_v * 0.000125f;
    float r_k  = (vout > 0 && vout < VIN) ? (R_FIXED * vout / (VIN - vout)) / 1000.0f : -1.0f;
    Serial.print("I:CALIB,adc1=");
    Serial.print(self_v);
    Serial.print(",Vout=");
    Serial.print(vout, 3);
    Serial.print(",R_kohm=");
    Serial.println(r_k, 1);
  }

  Serial.println("READY");
}

// ===================== LOOP ================================
void loop() {
  unsigned long now = millis();

  // -------- 1) (Watchdog removed) --------

  // -------- 2) รับคำสั่งจาก PC --------
  while (Serial.available()) {
    String cmd = Serial.readStringUntil('\n');
    cmd.trim();
    if (cmd.length() == 0) continue;

    if (cmd == "STOP") {
      stopMotor();
      Serial.println("I:STOPPED");
    }
    else if (cmd.startsWith("PWM:")) {
      if (!motorEnabled) {
        motorEnabled = true;
      }
      int val = cmd.substring(4).toInt();
      setMotor(val);
    }
  }

  // -------- 3) อ่าน Sensor + ส่งข้อมูล 100Hz --------
  unsigned long nowUs = micros();
  if ((nowUs - lastSensorRead) >= SENSOR_INTERVAL_US) {
    lastSensorRead = nowUs;

    int16_t adc0_raw = -1;
    int16_t adc1_raw = -1;
    float   position = -1.0f;
    float   resistance = -1.0f;

    if (adsReady) {
      // Ch0: Potentiometer → Position (เหมือน Debug)
      adc0_raw = ads.readADC_SingleEnded(0);
      position = mapFloat((float)adc0_raw, POT_ADC_MIN, POT_ADC_MAX,
                          POT_DEG_MIN, POT_DEG_MAX);
      position = constrain(position, POT_DEG_MIN, POT_DEG_MAX);

      delayMicroseconds(200);   // Issue 7 H2: AIN1 mux + S/H settle through high-Z source

      // Ch1: Voltage Divider → Resistance (เหมือน Debug)
      adc1_raw = ads.readADC_SingleEnded(1);
      float Vout = adc1_raw * 0.125f / 1000.0f;
      if (Vout > 0.001f && Vout < (VIN - 0.001f)) {
        resistance = R_FIXED * (Vout / (VIN - Vout));
      }
    }

    // Packet: D:<t_ms>,<adc0>,<pos>,<adc1>,<resistance>,<pwm>
    Serial.print("D:");
    Serial.print(now);
    Serial.print(',');
    Serial.print(adc0_raw);
    Serial.print(',');
    Serial.print(position, 2);
    Serial.print(',');
    Serial.print(adc1_raw);
    Serial.print(',');
    Serial.print(resistance, 1);
    Serial.print(',');
    Serial.println(currentPWM);
  }
}
