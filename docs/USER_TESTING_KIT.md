# User Testing Kit — Arthritis & Motor Limitation Pilots

Use this kit when placing the arm in front of seniors, arthritis patients, or caregivers. Adapt language for minors if testing with family members.

## 1. Session overview (read to participant)

> “This is a **research robot arm** that tries to reach everyday objects when your hands aren’t steady. You can use **voice**, **big buttons**, or **simple gestures**. Nothing will move until you say **yes** to confirm. You can say **stop** anytime.”

**Duration:** 30–45 minutes including rest breaks.

## 2. Consent checklist (facilitator)

- [ ] Purpose explained (research, not medical treatment)
- [ ] Right to stop at any time demonstrated (e-stop button + “stop”)
- [ ] No video/audio recording without separate permission
- [ ] Caregiver present if participant requests
- [ ] Trip hazards cleared around arm sweep area

**Optional one-line consent (non-IRB pilot):**  
“I understand this is a student research device, not a medical product, and I agree to try it today.”

## 3. Pre-session setup

```bash
python main.py --motor-level moderate --no-auto-simulate --accessibility
```

Place on table:

- Water bottle
- TV remote (or similar)
- Medication bottle (or pill bottle substitute)

Lighting: bright, even — avoid backlighting the webcam.

## 4. Trial protocol (per input method)

Run **5–10 reaches minimum** per method; aim for **20+** before science-fair statistics.

| Block | Mode (panel Experiment tab) | Task |
|-------|------------------------------|------|
| A | `manual` | Operator steers arm to target without vision assist |
| B | `dual_perception` | “Get my water” / ADL button — vision + depth |
| C | `voice` | Same target, voice only |
| D | `gesture` | Simple gestures (severe profile if needed) |
| E | `panel_adl` | Large Medication / Remote buttons only |

**Tremor simulation (operator as proxy):**  
Check **Tremor simulated** on manual trials; facilitator deliberately shakes during joystick-equivalent corrections.

**Record each trial:**

1. Start Trial → pick mode + target  
2. Perform task  
3. Mark Success or Failure  
4. Enter **final distance (cm)** from target center  
5. Optional **alignment error (mm)** if measured  

## 5. Observation checklist (facilitator notes)

| Question | Yes / No / Notes |
|----------|------------------|
| Completed task without caregiver physical help? | |
| Which input felt easiest? | |
| Understood confirmation prompt? | |
| Used emergency stop? Why? | |
| Reported pain or fatigue? | |
| Would use weekly at home? | |

## 6. Post-session survey (participant)

1. **Easiest control:** Voice / Buttons / Gestures / Vision automatic  
2. **Confidence (1–5):** I felt safe using the arm  
3. **Effort (1–5):** Physical effort required (5 = exhausting)  
4. **Open comment:** What would make this useful for you or your family?

## 7. Data export

Trials append to `logs/lab_trials.jsonl`. After the session:

```bash
python scripts/summarize_trials.py --log logs/lab_trials.jsonl --out logs/session_YYYY-MM-DD.md
```

## 8. Incident response

- Say **“stop”** or hit **EMERGENCY STOP**
- Unplug USB power if motion runaway
- Log incident in trial note field
