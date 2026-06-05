# Bill of Materials — Assistive Arm (Arthritis Focus)

**Target:** Complete functional build under **$75 CAD** (excluding laptop, 3D printer filament, and optional upgrades).

Prices are approximate retail (Canada, 2026) and assume you already own an **ELEGOO Mega Starter Kit** (~$45–60 CAD bundled).

## Already in ELEGOO Mega kit ($0 incremental)

| Item | Use |
|------|-----|
| Arduino Mega 2560 | Main controller |
| 28BYJ-48 stepper + ULN2003 | Vertical lift |
| Servos (×4 typical) | Base, lift joint, wrist, claw |
| Breadboard + jumper wires | Wiring |
| 100µF capacitors | Servo noise decoupling |
| 9V adapter | Bench power (upgrade to 5V/2A USB pack if servos stall) |

## Required purchases

| Item | Est. CAD | Source notes |
|------|----------|----------------|
| TF-Luna LiDAR (MakerFocus / Benewake) | ~$30 | UART to Mega Serial1 — fine range before grasp |
| USB webcam (720p+, fixed focus) | ~$0–15 | Often already on laptop; external cam improves desk view |
| 3D-printed arm structure + mounts | ~$5–15 | PLA/PETG; STL self-printed |
| **Subtotal (beyond kit)** | **~$35–45** | |

## Software ($0)

| Component | Cost |
|-----------|------|
| Python stack (Vosk, YOLO, Depth Anything V2, MediaPipe) | $0 |
| Groq / Gemini API tier | $0 with free keys |
| Ollama (offline LLM) | $0 optional |

## Optional upgrades (not required for thesis)

| Item | Est. CAD | When to buy |
|------|----------|-------------|
| FIFINE K669B USB mic | ~$35 | If built-in mic fails Vosk tests |
| NEMA 17 + A4988 | ~$29 | If 28BYJ-48 lift stalls under load |
| Intel RealSense D435 | ~$300+ | Skip — Depth Anything V2 uses normal webcam |

## Total cost scenarios

| Scenario | ~CAD |
|----------|------|
| Kit + TF-Luna + printed parts | **$55–65** |
| Kit + LiDAR + printed parts + external webcam | **$65–75** |
| Clinical Kinova JACO (comparison) | **$40,000–65,000 USD** |

## Verification checklist

- [ ] All four servos respond via control panel
- [ ] TF-Luna reports `range_mm` in diagnostics tab
- [ ] Voice command “get my pills” triggers confirmation
- [ ] Depth pipeline logs `used_3d_vision` in trial JSONL
- [ ] `scripts/summarize_trials.py` runs on `logs/lab_trials.jsonl`

## 3D-printed parts (document your STL names)

| Part | Qty | Notes |
|------|-----|-------|
| Base turntable mount | 1 | Servo horn interface |
| Upper arm link | 1–2 | Keep total arm mass low for 28BYJ-48 |
| Claw fingers | 2 | Line with YOLO “bottle” / “remote” test objects |
| Camera bracket | 1 | Fixed angle toward workspace |

Export STL paths in your repo or science-fair appendix when files are finalized.
