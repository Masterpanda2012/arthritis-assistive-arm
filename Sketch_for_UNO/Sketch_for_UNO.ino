#include <Servo.h>

// Arduino Mega pin plan:
// D3  -> base servo
// D5  -> lift servo (ANNIMOS DS3218, 180 degree conservative profile)
// D6  -> rotate servo
// D9  -> claw servo
// Serial1 (D19 RX / D18 TX) -> TF-Luna LiDAR at 115200 baud
//
// IMPORTANT: Power the DS3218 from an external 5V/6V supply and tie the
// external ground to the Mega ground. Do not power the DS3218 from the
// Mega's onboard 5V rail.

struct ArmPose {
  int baseDeg;
  int liftDeg;
  int rotateDeg;
  int clawDeg;
};

Servo servoBase;
Servo servoLift;
Servo servoRotate;
Servo servoClaw;

const int PIN_BASE = 3;
const int PIN_LIFT = 5;
const int PIN_ROTATE = 6;
const int PIN_CLAW = 9;

const bool LIMIT_SWITCHES_ENABLED = false;
const int PIN_LIMIT_TOP = A0;
const int PIN_LIMIT_BOTTOM = A1;

// Conservative software limits. Adjust upward only after confirming the
// specific servo can travel further without buzzing against hard stops.
const int BASE_MIN = 10;
const int BASE_MAX = 250;
const int LIFT_MIN = 15;
const int LIFT_MAX = 225;
const int ROTATE_MIN = 10;
const int ROTATE_MAX = 170;
const int CLAW_MIN = 15;
const int CLAW_MAX = 165;

const int BASE_PULSE_MIN_US = 700;
const int BASE_PULSE_MAX_US = 2300;
const int LIFT_PULSE_MIN_US = 500;
const int LIFT_PULSE_MAX_US = 2700;
const int ROTATE_PULSE_MIN_US = 650;
const int ROTATE_PULSE_MAX_US = 2350;
const int CLAW_PULSE_MIN_US = 650;
const int CLAW_PULSE_MAX_US = 2350;

const ArmPose HOME_POSE = {90, 225, 90, 100};
const unsigned long STATE_INTERVAL_MS = 250;
const uint8_t TF_LUNA_FRAME_SIZE = 9;
const unsigned long LIDAR_TIMEOUT_MS = 1500;

String serialBuffer = "";
ArmPose currentPose = HOME_POSE;
int latestRangeMm = -1;
bool estopActive = false;
String lastError = "";
unsigned long lastStateMs = 0;
unsigned long lastLidarOkMs = 0;
bool lidarOnline = false;

int clampInt(int value, int minValue, int maxValue) {
  if (value < minValue) return minValue;
  if (value > maxValue) return maxValue;
  return value;
}

uint8_t checksumForPayload(const String &payload) {
  uint16_t sum = 0;
  for (unsigned int i = 0; i < payload.length(); i++) {
    sum += static_cast<uint8_t>(payload[i]);
  }
  return static_cast<uint8_t>(sum % 256);
}

String buildPacket(const String &payload) {
  return "<" + payload + "*" + String(checksumForPayload(payload)) + ">";
}

void sendAck(const char *command) {
  Serial.println(buildPacket(String("ACK,") + command));
}

void sendError(const String &code) {
  lastError = code;
  Serial.println(buildPacket(String("ERR,") + code));
}

void sendDebug(const String &topic, const String &detail) {
  Serial.println(buildPacket(String("DBG,") + topic + "," + detail));
}

void sendState() {
  String payload =
      String("STATE,") +
      String(currentPose.baseDeg) + "," +
      String(currentPose.liftDeg) + "," +
      String(currentPose.rotateDeg) + "," +
      String(currentPose.clawDeg) + "," +
      String(latestRangeMm) + "," +
      String(estopActive ? 1 : 0);
  Serial.println(buildPacket(payload));
}

bool verifyPacket(const String &packet, String &payloadOut) {
  if (!packet.startsWith("<") || !packet.endsWith(">")) {
    return false;
  }

  String body = packet.substring(1, packet.length() - 1);
  int star = body.lastIndexOf('*');
  if (star < 0) {
    return false;
  }

  String payload = body.substring(0, star);
  String checksumText = body.substring(star + 1);
  int receivedChecksum = checksumText.toInt();
  if (receivedChecksum != checksumForPayload(payload)) {
    return false;
  }

  payloadOut = payload;
  return true;
}

bool isIntegerToken(const String &token) {
  if (token.length() == 0) {
    return false;
  }
  for (unsigned int i = 0; i < token.length(); i++) {
    char c = token[i];
    if (i == 0 && c == '-') continue;
    if (c < '0' || c > '9') return false;
  }
  return true;
}

bool parseCsvInts(const String &csv, int *values, int expectedCount) {
  int start = 0;
  for (int index = 0; index < expectedCount; index++) {
    int comma = csv.indexOf(',', start);
    String token;
    if (comma < 0) {
      token = csv.substring(start);
      if (index != expectedCount - 1) return false;
    } else {
      token = csv.substring(start, comma);
    }

    token.trim();
    if (!isIntegerToken(token)) {
      return false;
    }
    values[index] = token.toInt();

    if (comma < 0) {
      start = csv.length();
    } else {
      start = comma + 1;
    }
  }

  return start >= csv.length();
}

ArmPose sanitizedPose(const ArmPose &pose) {
  ArmPose safePose;
  safePose.baseDeg = clampInt(pose.baseDeg, BASE_MIN, BASE_MAX);
  safePose.liftDeg = clampInt(pose.liftDeg, LIFT_MIN, LIFT_MAX);
  safePose.rotateDeg = clampInt(pose.rotateDeg, ROTATE_MIN, ROTATE_MAX);
  safePose.clawDeg = clampInt(pose.clawDeg, CLAW_MIN, CLAW_MAX);
  return safePose;
}

bool poseWasClamped(const ArmPose &requestedPose, const ArmPose &safePose) {
  return requestedPose.baseDeg != safePose.baseDeg ||
         requestedPose.liftDeg != safePose.liftDeg ||
         requestedPose.rotateDeg != safePose.rotateDeg ||
         requestedPose.clawDeg != safePose.clawDeg;
}

int angleToPulse(int angle, int angleMin, int angleMax, int pulseAtMinAngle, int pulseAtMaxAngle) {
  long spanAngle = angleMax - angleMin;
  if (spanAngle <= 0) {
    return pulseAtMinAngle;
  }
  return static_cast<int>(map(angle, angleMin, angleMax, pulseAtMinAngle, pulseAtMaxAngle));
}

void attachServoSafe(Servo &servo, int pin, int pulseA, int pulseB) {
  int pulseMin = pulseA < pulseB ? pulseA : pulseB;
  int pulseMax = pulseA < pulseB ? pulseB : pulseA;
  servo.attach(pin, pulseMin, pulseMax);
}

void ensureServosAttached() {
  if (!servoBase.attached()) {
    attachServoSafe(servoBase, PIN_BASE, BASE_PULSE_MIN_US, BASE_PULSE_MAX_US);
  }
  if (!servoLift.attached()) {
    attachServoSafe(servoLift, PIN_LIFT, LIFT_PULSE_MIN_US, LIFT_PULSE_MAX_US);
  }
  if (!servoRotate.attached()) {
    attachServoSafe(servoRotate, PIN_ROTATE, ROTATE_PULSE_MIN_US, ROTATE_PULSE_MAX_US);
  }
  if (!servoClaw.attached()) {
    attachServoSafe(servoClaw, PIN_CLAW, CLAW_PULSE_MIN_US, CLAW_PULSE_MAX_US);
  }
}

void detachServos() {
  if (servoBase.attached()) servoBase.detach();
  if (servoLift.attached()) servoLift.detach();
  if (servoRotate.attached()) servoRotate.detach();
  if (servoClaw.attached()) servoClaw.detach();
}

void applyPose(const ArmPose &pose) {
  ArmPose safePose = sanitizedPose(pose);
  ensureServosAttached();
  servoBase.writeMicroseconds(angleToPulse(safePose.baseDeg, BASE_MIN, BASE_MAX, BASE_PULSE_MIN_US, BASE_PULSE_MAX_US));
  servoLift.writeMicroseconds(angleToPulse(safePose.liftDeg, LIFT_MIN, LIFT_MAX, LIFT_PULSE_MIN_US, LIFT_PULSE_MAX_US));
  servoRotate.writeMicroseconds(angleToPulse(safePose.rotateDeg, ROTATE_MIN, ROTATE_MAX, ROTATE_PULSE_MIN_US, ROTATE_PULSE_MAX_US));
  servoClaw.writeMicroseconds(angleToPulse(safePose.clawDeg, CLAW_MIN, CLAW_MAX, CLAW_PULSE_MIN_US, CLAW_PULSE_MAX_US));
  currentPose = safePose;
  lastError = poseWasClamped(pose, safePose) ? "CLAMPED" : "";
}

bool parsePoseCommand(const String &args, ArmPose &poseOut, int &speedPctOut) {
  int values[5];
  if (!parseCsvInts(args, values, 5)) {
    return false;
  }

  poseOut.baseDeg = values[0];
  poseOut.liftDeg = values[1];
  poseOut.rotateDeg = values[2];
  poseOut.clawDeg = values[3];
  speedPctOut = clampInt(values[4], 5, 100);
  return true;
}

void handlePayload(const String &payload) {
  int comma = payload.indexOf(',');
  String command = comma < 0 ? payload : payload.substring(0, comma);
  String args = comma < 0 ? "" : payload.substring(comma + 1);

  if (command == "PING") {
    sendDebug("RX", "PING");
    sendAck("PING");
    sendState();
    return;
  }

  if (command == "STOP") {
    sendDebug("RX", "STOP");
    estopActive = true;
    detachServos();
    sendAck("STOP");
    sendState();
    return;
  }

  if (command == "INIT") {
    sendDebug("RX", "INIT");
    estopActive = false;
    applyPose(HOME_POSE);
    sendAck("INIT");
    if (lastError.length() > 0) {
      sendError(lastError);
    }
    sendState();
    return;
  }

  if (command == "HOME") {
    sendDebug("RX", "HOME");
    estopActive = false;
    applyPose(HOME_POSE);
    sendAck("HOME");
    if (lastError.length() > 0) {
      sendError(lastError);
    }
    sendState();
    return;
  }

  if (command == "POSE") {
    ArmPose requestedPose;
    int speedPct = 0;
    if (!parsePoseCommand(args, requestedPose, speedPct)) {
      sendError("POSE_PARSE");
      return;
    }

    sendDebug("RX", String("POSE,") + args);
    estopActive = false;
    applyPose(requestedPose);
    sendAck("POSE");
    if (lastError.length() > 0) {
      sendError(lastError);
    }
    sendState();
    return;
  }

  sendError("UNKNOWN_CMD");
  sendDebug("RXERR", command);
}

void readHostSerial() {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '<') {
      serialBuffer = "";
    }
    serialBuffer += c;

    if (c != '>') {
      continue;
    }

    String payload;
    if (!verifyPacket(serialBuffer, payload)) {
      sendError("CHECKSUM");
      sendDebug("RXERR", "CHECKSUM");
      serialBuffer = "";
      continue;
    }

    handlePayload(payload);
    serialBuffer = "";
  }
}

void readTfLuna() {
  static uint8_t frame[TF_LUNA_FRAME_SIZE];
  static uint8_t index = 0;

  while (Serial1.available()) {
    uint8_t value = static_cast<uint8_t>(Serial1.read());

    if (index == 0 && value != 0x59) {
      continue;
    }
    if (index == 1 && value != 0x59) {
      index = 0;
      continue;
    }

    frame[index++] = value;
    if (index < TF_LUNA_FRAME_SIZE) {
      continue;
    }

    uint8_t checksum = 0;
    for (uint8_t i = 0; i < TF_LUNA_FRAME_SIZE - 1; i++) {
      checksum += frame[i];
    }

    if (checksum == frame[TF_LUNA_FRAME_SIZE - 1]) {
      int distanceCm = frame[2] + frame[3] * 256;
      latestRangeMm = distanceCm * 10;
      lastLidarOkMs = millis();
      if (!lidarOnline) {
        lidarOnline = true;
        sendDebug("LIDAR", "ONLINE");
      }
    }
    index = 0;
  }
}

void configureLimitSwitches() {
  if (!LIMIT_SWITCHES_ENABLED) {
    return;
  }
  pinMode(PIN_LIMIT_TOP, INPUT_PULLUP);
  pinMode(PIN_LIMIT_BOTTOM, INPUT_PULLUP);
}

void setup() {
  Serial.begin(115200);
  Serial1.begin(115200);

  configureLimitSwitches();
  sendDebug("BOOT", "READY");
  sendState();
}

void loop() {
  readHostSerial();
  readTfLuna();

  unsigned long now = millis();
  if (lidarOnline && now - lastLidarOkMs > LIDAR_TIMEOUT_MS) {
    lidarOnline = false;
    latestRangeMm = -1;
    sendDebug("LIDAR", "TIMEOUT");
  }
  if (now - lastStateMs >= STATE_INTERVAL_MS) {
    sendState();
    lastStateMs = now;
  }
}
