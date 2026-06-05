#!/usr/bin/env bash
# One-command setup for the arthritis-focused assistive arm project.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Creating virtual environment (if needed)"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Installing Python dependencies"
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "==> Environment file"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "    Created .env — set ROBOT_ARM_PORT and GROQ_API_KEY"
fi

echo "==> Directories"
mkdir -p logs calibration models

echo "==> Default calibration"
if [[ ! -f calibration/camera_to_arm.json ]]; then
  python scripts/calibrate_camera.py --reset
fi

echo "==> Download Vosk model (if missing)"
VOSK_DIR="models/vosk-model-small-en-us-0.15"
if [[ ! -d "$VOSK_DIR" ]]; then
  echo "    Download vosk-model-small-en-us-0.15 from https://alphacephei.com/vosk/models"
  echo "    and extract into models/"
fi

echo "==> YOLO weights (first run downloads yolov8s.pt automatically)"
python -c "from ultralytics import YOLO; YOLO('yolov8s.pt')" 2>/dev/null || true

echo ""
echo "Setup complete. Run:"
echo "  source .venv/bin/activate"
echo "  python main.py --motor-level moderate --no-auto-simulate"
echo ""
echo "Summarize trials:"
echo "  python scripts/summarize_trials.py"
