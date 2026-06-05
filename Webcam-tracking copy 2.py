#!/usr/bin/env python3
import time
import cv2
import serial
import numpy as np
from collections import deque, Counter
from math import hypot
import mediapipe as mp

# ------------------------------
# Config
# ------------------------------
CAPTURE_INTERVAL = 0.08
GESTURE_BUFFER_LEN = 6
GESTURE_STABLE_REQUIREMENT = 4
THUMBS_UP_HOLD_TIME = 1.0

# ------------------------------
# Config for aurdino serial
# ------------------------------

MEGA_PORT = "/dev/cu.usbmodem1401"  
BAUD = 115200
SERIAL_TIMEOUT = 0.2
MIN_SERIAL_INTERVAL = 0.06

# ------------------------------
# MediaPipe Setup
# ------------------------------
mp_hands = mp.solutions.hands
hands = mp_hands.Hands(
    static_image_mode=False,
    max_num_hands=1,
    min_detection_confidence=0.7,
    min_tracking_confidence=0.7
)
mp_drawing = mp.solutions.drawing_utils

# ------------------------------
# Utility functions
# ------------------------------
def build_packet(values):
    checksum = sum(values) % 256
    inside = ",".join(str(v) for v in values)
    return f"<{inside}*{checksum}>"

def print_gesture_servo(gesture, vals):
    print(f"[{gesture}]: S1 {vals[0]}  S2 {vals[1]}  S3 {vals[2]}  S4 {vals[3]}")

# ------------------------------
# Gesture helpers
# ------------------------------
def count_fingers(lm):
    tips = [8,12,16,20]
    pips = [6,10,14,18]
    return sum(1 for tip, pip in zip(tips, pips) if lm[tip].y < lm[pip].y - 0.05)

def thumb_over_palm(lm):
    palm = [(lm[i].x, lm[i].y) for i in [0,1,5,9,13,17]]
    tx, ty = lm[4].x, lm[4].y
    inside = False
    j = len(palm) - 1
    for i in range(len(palm)):
        xi, yi = palm[i]
        xj, yj = palm[j]
        if ((yi > ty) != (yj > ty)) and (tx < (xj - xi)*(ty - yi)/(yj - yi + 1e-6) + xi):
            inside = not inside
        j = i
    return inside

def wrist_tilt(lm):
    if lm[0].x - lm[9].x > 0.05: return "tilt_right"
    if lm[9].x - lm[0].x > 0.05: return "tilt_left"
    return None

def is_fist(lm):
    # Count how many fingers are folded
    folded_count = 0
    fingers = [(8,5), (12,9), (16,13), (20,17)]  # Tip, MCP
    for tip, mcp in fingers:
        if hypot(lm[tip].x - lm[mcp].x, lm[tip].y - lm[mcp].y) < 0.08:  # increased tolerance
            folded_count += 1
    # Thumb near palm
    thumb_tucked = abs(lm[4].x - lm[2].x) < 0.1 and abs(lm[4].y - lm[2].y) < 0.1
    return folded_count >= 3 and thumb_tucked  # fist if 3 or 4 fingers folded


def is_thumbs_up(lm):
    thumb_vec = np.array([lm[4].x - lm[1].x, lm[4].y - lm[1].y])
    ang = np.degrees(np.arctan2(-thumb_vec[1], thumb_vec[0]))
    vertical = 60 < ang < 120
    straight = hypot(lm[4].x - lm[2].x, lm[4].y - lm[2].y) > 0.12
    folded = all(hypot(lm[tip].x - lm[mcp].x, lm[tip].y - lm[mcp].y) < 0.05
                 for tip,mcp in zip([8,12,16,20],[5,9,13,17]))
    return vertical and straight and folded


class SafeSerialWriter:
    def __init__(self, ser):
        self.ser = ser
        self.last = 0

    def write(self, pkt):
        now = time.time()
        if now - self.last < MIN_SERIAL_INTERVAL:
            return
        try:
            self.ser.write(pkt.encode())
            self.last = now
            print("[SEND]", pkt)
        except:
            pass

# ------------------------------
# Fixed packet sets for each gesture
# ------------------------------
PACKETS = {
    "one":        [10, 20, 30, 40],
    "two":        [50, 60, 70, 80],
    "three":      [15, 35, 55, 75],
    "four":       [90, 90, 90, 90],
    "open":       [3, 164, 75, 128],
    "fist":       [143, 87, 12, 179],
    "tilt_left":  [102, 49, 139, 6],
    "tilt_right": [177, 9, 58, 131]
}

# ------------------------------
# Main program
# ------------------------------
def main():

        # Serial
    try:
        ser = serial.Serial(MEGA_PORT, BAUD, timeout=SERIAL_TIMEOUT)
        time.sleep(2)
    except Exception as e:
        print("Serial error:", e)
        return

    writer = SafeSerialWriter(ser)

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open webcam")
        return

    gesture_buffer = deque(maxlen=GESTURE_BUFFER_LEN)
    last_gesture_sent = None
    thumbs_start = None

    cv2.namedWindow("Webcam Feed", cv2.WINDOW_NORMAL)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = hands.process(rgb)

            label = None
            countdown_text = ""

            if result.multi_hand_landmarks:
                lm = result.multi_hand_landmarks[0].landmark
                mp_drawing.draw_landmarks(frame, result.multi_hand_landmarks[0], mp_hands.HAND_CONNECTIONS)

                # -------------------------------
                # Gesture Detection
                # -------------------------------
                if is_thumbs_up(lm):
                    if thumbs_start is None:
                        thumbs_start = time.time()
                    elapsed = time.time() - thumbs_start
                    if elapsed >= THUMBS_UP_HOLD_TIME:
                        print("[EXIT] Thumbs up")
                        break
                    countdown_text = f"Hold: {THUMBS_UP_HOLD_TIME - elapsed:.1f}s"
                else:
                    thumbs_start = None

                    # 1. Fist
                    if is_fist(lm):
                        label = "fist"
                    else:
                        fingers = count_fingers(lm)
                        thumb_in = thumb_over_palm(lm)
                        tilt = wrist_tilt(lm)

                        # 2. Open & tilt detection
                        if fingers >= 4 and not thumb_in:
                            if tilt:
                                label = tilt
                            else:
                                label = "open"
                        # 3. Four fingers
                        elif fingers == 4:
                            label = "four"
                        elif fingers == 3:
                            label = "three"
                        elif fingers == 2:
                            label = "two"
                        elif fingers == 1:
                            label = "one"

                    if label:
                        gesture_buffer.append(label)
            # -------------------------------
            # Stable gesture detection
            # -------------------------------
            if gesture_buffer:
                chosen, votes = Counter(gesture_buffer).most_common(1)[0]
                if votes >= GESTURE_STABLE_REQUIREMENT:
                    if chosen != last_gesture_sent:
                        if chosen in PACKETS:
                            vals = PACKETS[chosen]
                            pkt = build_packet(vals)
                            print(f"[SEND] {pkt}")
                            if (chosen == "one"):
                                writer.write("1")
                            elif (chosen == "two"):
                                writer.write("2")
                            elif (chosen == "three"):
                                writer.write("3")
                            elif (chosen == "four"):
                                writer.write("4")
                            else:
                                writer.write("0")
                            print_gesture_servo(chosen, vals)
                            last_gesture_sent = chosen

            # Display current gesture
            if gesture_buffer:
                cv2.putText(frame, gesture_buffer[-1], (10,30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
            if countdown_text:
                cv2.putText(frame, countdown_text, (10,70),
                            cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 2)

            cv2.imshow("Webcam Feed", frame)
            if cv2.waitKey(1) & 0xFF in [27, ord('q')]:
                break

    finally:
        cap.release()
        ser.close()
        cv2.destroyAllWindows()
        print("Exiting.")

if __name__ == "__main__":
    main()
