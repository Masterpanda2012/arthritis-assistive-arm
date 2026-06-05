# Assistive Robot Arm — Arthritis & Fine Motor Support

An affordable, multi-modal desktop robot arm for people with **arthritis, tremors, and declining fine motor control**. Inspired by helping grandparents stay independent at the table — reach medication, a remote, or a drink **without** steady hands or a $30,000 clinical arm.

## What it does

- **Voice** — plain English (“get my pills”, “bring the remote”) via offline Vosk + free LLM (Groq / Gemini / Ollama)
- **Gestures** — MediaPipe hand shapes; **simple mode** for severe limitations (yes / no / stop / home)
- **Vision** — YOLOv8s object detection + **Depth Anything V2** for 3D reach (no manual alignment)
- **Control panel** — large **daily task** buttons for early, moderate, and severe profiles
- **Safety** — confirmation before picks, slow profile-based speeds, emergency stop

## Quick start

```bash
cd "/path/to/Arthritis-focused create"
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Set ROBOT_ARM_PORT and GROQ_API_KEY (optional but recommended)

python main.py --motor-level moderate
```

### Web control console

Run the robot with a **premium browser dashboard** — saved profile (SQLite), live arm meters, daily tasks, and toggles for **voice**, **gesture**, or **manual/web** control:

```bash
python main.py --motor-level moderate --web --no-auto-simulate
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787). Profile changes apply immediately to the running app.

**Quit gesture:** Hold **peace sign ✌️** to quit. **Thumbs up** confirms yes.

### Teach custom gestures (web)

Use **Gesture studio** in the web console: describe a gesture by typing or speaking, show it to the **live camera preview**, confirm, and it is saved to your personal catalogue (duplicate poses are rejected).

If the preview stays blank, grant **Camera** access to Terminal or Cursor (macOS: System Settings → Privacy & Security → Camera) and try `python main.py --list-cameras` then `--camera-index 1` if Continuity Camera hijacks index 0.

### Motor profiles

| Level | Who it's for | Behavior |
|-------|----------------|----------|
| `early` | Stiff hands, mild tremor | Faster motion, full gestures |
| `moderate` | Typical arthritis (default) | Balanced speed, ADL shortcuts |
| `severe` | Weak grip / unreliable gesture | Slowest speed, simple gestures, panel-first |

```bash
python main.py --motor-level severe --accessibility
```

## Hardware

- Arduino Mega + ELEGOO-style servo arm (base, lift, rotate, claw)
- Webcam for vision/gestures
- Optional Benewake TF-Luna on Serial1 for fine range before grasp
- Target build cost: under **$75 CAD** (see project BOM docs)

## Models used

| Task | Model |
|------|--------|
| Object detection | `yolov8s.pt` (Ultralytics) |
| Depth | `depth-anything/Depth-Anything-V2-Small-hf` |
| Hand gestures | MediaPipe Hand Landmarker |
| Speech | Vosk small EN |
| Intent | Groq `llama-3.3-70b-versatile` (or Gemini / Ollama) |

## Calibration

Default `calibration/camera_to_arm.json` is created on first vision run. Refine with a calibration sweep (see `depth_perception_research/`) for your desk layout.

## Tests

```bash
python -m unittest discover -s tests -q
python scripts/summarize_trials.py
```

## Documentation

| Doc | Purpose |
|-----|---------|
| [docs/BOM.md](docs/BOM.md) | Bill of materials under $75 CAD |
| [docs/USER_TESTING_KIT.md](docs/USER_TESTING_KIT.md) | Consent, protocol, survey |
| [docs/RECRUITMENT_PLAN.md](docs/RECRUITMENT_PLAN.md) | Coquitlam recruitment plan |
| [docs/REGULATORY_ROADMAP.md](docs/REGULATORY_ROADMAP.md) | IEC 62304 / Health Canada outline |

## One-command setup

```bash
chmod +x setup.sh
./setup.sh
```

## Calibration

```bash
python scripts/calibrate_camera.py --reset
python scripts/calibrate_camera.py --base-center 120 --lift-deg-per-mm-z 0.06
```

## Research & impact

Lab trials log to `logs/lab_trials.jsonl` via the control panel **Experiment** tab. See `depth_perception_research/adaptive_robot_arm_lab_report_v4.md` for the dual-perception accessibility thesis.

---

*Built to restore dignity — the robot carries precision; the user carries intent.*
