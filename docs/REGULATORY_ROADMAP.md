# Regulatory Roadmap (Non-Certified — Planning Document)

This project is an **engineering prototype**, not an approved medical device. This document outlines a credible path toward home or care-environment deployment in Canada.

## Current status

| Aspect | Status |
|--------|--------|
| Health Canada licensing | Not applied |
| IEC 62304 software lifecycle | Partially aligned (this repo) |
| Risk management (ISO 14971) | Informal only |
| Clinical validation | Lab + planned user pilots |

## Intended classification (research hypothesis)

- **Health Canada:** Likely **Class II** medical device if marketed as assistive ADL robotics for persons with disability (confirm with regulatory consultant).
- **FDA (US reference):** Similar assistive manipulators often Class II 510(k) pathway if commercialized.

*This is not legal advice — engage a regulatory consultant before any sale or clinical claim.*

## IEC 62304 software lifecycle mapping

| IEC 62304 activity | This repository |
|--------------------|-----------------|
| Software development planning | `README.md`, phase docs |
| Requirements | ADL tasks (`ai/adl_tasks.py`), user profiles |
| Architectural design | `orchestrator.py`, `motion/planner.py` |
| Detailed design | Module docstrings, framework v4 txt |
| Unit verification | `tests/` |
| Integration testing | Lab trials JSONL + `scripts/summarize_trials.py` |
| Risk control | Confirmation gate, e-stop, speed limits |
| Configuration management | Git + `.env.example` |
| Problem resolution | GitHub issues / lab notes |

### Recommended next documentation artifacts

1. **Software Requirements Specification (SRS)** — link each ADL task to hazard controls.
2. **Software Architecture Document** — diagram orchestrator + inputs + Mega firmware.
3. **Traceability matrix** — requirement ID → test → trial evidence.
4. **Cybersecurity file** — API keys, serial port exposure, offline mode.

## Risk controls already in software

- Confirmation before pick/place
- Emergency stop (voice, gesture, panel)
- Motor profile speed caps (`ai/user_profile.py`)
- Session command logging (`ai/memory_store.py`, `logs/lab_trials.jsonl`)
- Simulation mode when hardware absent

## Phased path to deployment

### Phase A — Prototype (current)

- Science fair / research demos
- No medical claims on packaging
- Informed consent for pilots (`docs/USER_TESTING_KIT.md`)

### Phase B — Pilot study (6–12 months)

- 20+ trials/condition (`RECOMMENDED_TRIALS_PER_CONDITION`)
- Seniors/arthritis user sessions (`docs/RECRUITMENT_PLAN.md`)
- Formal hazard analysis (ISO 14971)

### Phase C — Pre-submission (12–24 months)

- IEC 62304 Class B or C software safety class (consultant decision)
- EMC / electrical safety for Mega + LiDAR assembly
- Labeling: intended use, contraindications, caregiver supervision

### Phase D — Submission (24+ months)

- Health Canada MDL application (Class II)
- Post-market surveillance plan
- Manufacturing QC for printed parts + wiring

## Claims to avoid until licensed

- “Treats arthritis” or “rehabilitation device”
- “FDA approved” / “Health Canada approved”
- Unsupervised use without caregiver training

## Acceptable claims today

- “Research prototype to reduce fine-motor demand for tabletop reach tasks”
- “Multi-modal control for ADL-style object fetch experiments”
