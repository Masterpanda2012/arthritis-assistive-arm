#!/usr/bin/env python3
"""Entry point for running the full stack with emphasis on OpenCV + webcam + gestures.

Examples:
  ./Webcam-tracking.py --gesture-only --verbose
  ./Webcam-tracking.py --gesture-only --camera-index 1

Uses ``inputs/camera.py`` (cv2.VideoCapture) for the shared feed and ``inputs/gesture.py``
(MediaPipe Hands) for the same gesture mappings as the main orchestrator.
"""
from main import main


if __name__ == "__main__":
    raise SystemExit(main())
