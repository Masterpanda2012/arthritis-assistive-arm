"""Profile-aware cheat sheet for /api/help and the Help tab."""

from __future__ import annotations

from typing import Any

from ai.adl_tasks import ADL_TASKS
from ai.gesture_bindings import _DEFAULT_BINDINGS
from ai.user_profile import MotorLevel, UserProfile


def _gesture_rows() -> list[list[str]]:
    intent_labels = {
        "open_claw": "Open claw",
        "close_claw": "Close claw",
        "lift_up": "Lift up",
        "lift_down": "Lift down",
        "base_left": "Turn base left",
        "base_right": "Turn base right",
        "rotate_left": "Rotate wrist left",
        "rotate_right": "Rotate wrist right",
        "home": "Go home",
        "emergency_stop": "Emergency stop",
        "confirm_yes": "Confirm yes",
        "confirm_no": "Cancel / no",
        "preset_pose": "Preset pose",
    }
    rows: list[list[str]] = []
    for gesture, (intent, payload) in sorted(_DEFAULT_BINDINGS.items()):
        label = intent_labels.get(intent, intent.replace("_", " "))
        if intent == "preset_pose":
            label = f"Pose: {payload.get('name', 'preset')}"
        rows.append([gesture.replace("_", " "), label, "Hand sign"])
    return rows


def _simple_gesture_rows() -> list[list[str]]:
    """Reduced set for severe / simple-gesture profiles."""
    keep = {
        "open": "Open claw",
        "fist": "Close claw",
        "thumbs_up": "Confirm yes",
        "ok_circle": "Confirm yes (OK sign)",
        "palm_down": "Go home",
        "stop_palm": "Emergency stop",
        "one": "Lift up",
        "two": "Lift down",
    }
    return [[g.replace("_", " "), action, "Hand sign"] for g, action in keep.items()]


def _voice_rows(short_phrases: bool) -> list[list[str]]:
    rows: list[list[str]] = []
    limit = 2 if short_phrases else 4
    for task in ADL_TASKS:
        for phrase in task.voice_phrases[:limit]:
            rows.append([f"“{phrase}”", task.label, "Pick (confirm)"])
        if len(task.voice_phrases) > limit and rows:
            rows[-1][0] += " …"
    rows.extend([
        ["“open the claw”", "Open claw", "Immediate"],
        ["“go home”", "Home", "Immediate"],
        ["“yes” / “no”", "Confirm / cancel", "When prompted"],
        ["“stop”", "Emergency stop", "Always"],
    ])
    return rows


def build_help_payload(
    profile: UserProfile | None = None,
    *,
    custom_gestures: list[dict[str, Any]] | None = None,
    lidar: dict[str, Any] | None = None,
    serial_mode: str = "offline",
) -> dict[str, Any]:
    profile = profile or UserProfile()
    voice_on = profile.enable_voice_input
    gesture_on = profile.enable_gesture_input
    manual_on = profile.enable_manual_input
    simple = profile.simple_gesture_mode or profile.motor_level == MotorLevel.SEVERE
    caregiver = profile.caregiver_mode
    gentle = profile.gentle_reach

    active: list[str] = []
    if voice_on:
        active.append("Voice")
    if gesture_on:
        active.append("Gestures")
    if manual_on:
        active.append("Web buttons")
    if not active:
        active.append("None — enable inputs in Profile")

    intro = {
        "greeting": f"Controls for {profile.display_name or 'you'}",
        "motor_level": profile.motor_level.value,
        "speed_pct": profile.default_speed_pct,
        "active_inputs": active,
        "preferred": profile.preferred_input.value,
        "notes": [],
    }
    if caregiver:
        intro["notes"].append(
            "Caregiver mode is on — picks always ask for confirmation before moving."
        )
    if gentle:
        intro["notes"].append("Gentle reach is on — the arm moves slowly and confirms before grasping.")
    if profile.fatigue_slowdown:
        intro["notes"].append("Fatigue slowdown will reduce pace after many commands in one session.")
    if serial_mode != "live":
        intro["notes"].append(
            f"Arm serial is in {serial_mode} mode — connect the Arduino and use --no-auto-simulate for real motion."
        )
    if lidar and not lidar.get("valid"):
        intro["notes"].append("LiDAR has no valid reading yet — point the TF-Luna at your target before picking.")

    sections: list[dict[str, Any]] = [
        {
            "id": "safety",
            "title": "Safety (always available)",
            "description": "These override everything else.",
            "columns": ["Input", "Action", "Notes"],
            "rows": [
                ["Hold peace sign 1.5s", "Quit program", "Cannot be rebound"],
                ["Say “stop”", "Emergency stop", "Voice or web"],
                ["Web: Emergency stop", "Emergency stop", "Red button on Control tab"],
                ["Open palm toward camera", "Emergency stop", "stop_palm gesture"],
            ],
        },
    ]

    if gesture_on:
        gesture_rows = _simple_gesture_rows() if simple else _gesture_rows()
        tip = (
            "Simple mode: fewer gestures, easier to hold steady."
            if simple
            else "OK sign and thumbs up both mean YES. Palm down = home; thumbs down = lower the arm."
        )
        sections.append({
            "id": "gestures",
            "title": "Hand gestures" if simple else "Built-in hand gestures",
            "description": (
                "Hold each pose steady for about half a second."
                if not simple
                else "Hold each pose for a full second — severe profile uses a smaller set."
            ),
            "tip": tip,
            "tip_kind": "ok",
            "columns": ["Gesture", "Arm action", "Type"],
            "rows": gesture_rows,
        })
        custom = custom_gestures or []
        if custom:
            sections.append({
                "id": "custom_gestures",
                "title": "Your custom gestures",
                "description": "Saved in Gesture studio — these are personal to your profile.",
                "columns": ["Name", "Action", "Source"],
                "rows": [
                    [
                        g.get("display_name", g.get("id", "gesture")),
                        str(g.get("intent", "")).replace("_", " "),
                        "Your catalogue",
                    ]
                    for g in custom
                ],
            })
        sections.append({
            "id": "gesture_studio",
            "title": "Gesture studio workflow",
            "description": "Describe → show → save. Duplicates are rejected.",
            "cards": [
                {"title": "1. Describe", "detail": "Type or speak what your hand will do."},
                {"title": "2. Show", "detail": "Hold the pose while capture runs."},
                {"title": "3. Save", "detail": "Adds to your personal catalogue."},
            ],
        })
    else:
        sections.append({
            "id": "gestures_off",
            "title": "Gestures",
            "description": "Gesture input is off for your profile.",
            "tip": "Enable Gestures in Profile if you want hand-sign control.",
            "tip_kind": "",
        })

    if voice_on:
        sections.append({
            "id": "voice",
            "title": "Voice & quick phrases",
            "description": "Plain English — same as the microphone. Picks ask for confirmation first.",
            "columns": ["Say this", "Task", "Result"],
            "rows": _voice_rows(profile.voice_short_phrases),
        })
    else:
        sections.append({
            "id": "voice_off",
            "title": "Voice",
            "description": "Voice input is off for your profile.",
            "tip": "Enable Voice in Profile or use the web buttons on the Control tab.",
            "tip_kind": "",
        })

    if manual_on:
        sections.append({
            "id": "web_deck",
            "title": "Web control deck",
            "columns": ["Control", "Action", "Notes"],
            "rows": [
                ["Medication / Remote / Water / Phone", "Pick object", "Confirms first"],
                ["Motion grid", "Single joint move", "One step each tap"],
                ["Workspace radar", "Pick clicked object", "Needs LiDAR + vision"],
                ["Yes / No / Home", "Confirm / cancel / home", "After a prompt"],
            ],
        })

    sections.append({
        "id": "lidar",
        "title": "TF-Luna LiDAR tips",
        "description": "Ranging comes from the TF-Luna on the arm, not the webcam.",
        "tip": (
            "Point the LiDAR at the object. Valid range is roughly 20 cm–8 m. "
            "Check the Range line in the live rail while aiming."
        ),
        "columns": ["Symptom", "Likely cause", "Fix"],
        "rows": [
            ["Depth always wrong", "Camera depth used instead of LiDAR", "Face LiDAR at target; watch Range mm"],
            ["Pick moves wildly", "No valid LiDAR", "Say yes only when Range shows a sensible distance"],
            ["range_mm = -1", "LiDAR timeout or invalid", "Power cycle; verify Serial1 @ 115200 baud"],
            ["Arm does not move", "Simulation mode", "Use --no-auto-simulate; check USB to Arduino"],
        ],
    })

    level_tips = {
        MotorLevel.EARLY: "Early profile — more speed and full gesture set available.",
        MotorLevel.MODERATE: "Moderate profile — balanced speed and confirmation windows.",
        MotorLevel.SEVERE: "Severe profile — slower moves; consider simple gestures and web buttons.",
    }
    sections.append({
        "id": "profile",
        "title": "Your profile settings",
        "description": level_tips.get(profile.motor_level, ""),
        "columns": ["Setting", "Your value", "Effect"],
        "rows": [
            ["Motor level", profile.motor_level.value, "Tunes speed and gesture complexity"],
            ["Default speed", f"{profile.default_speed_pct}%", "Applied to each move"],
            ["Rest reminder", f"{profile.rest_reminder_minutes} min", "Banner nudge after long sessions"],
            ["Caregiver mode", "On" if caregiver else "Off", "Extra confirmation before picks"],
            ["Gentle reach", "On" if gentle else "Off", "Slower approach on pick sequences"],
        ],
    })

    return {
        "intro": intro,
        "sections": sections,
        "lidar": lidar or {"valid": False, "range_mm": None},
        "serial_mode": serial_mode,
    }


def build_help_sections(profile: UserProfile | None = None) -> list[dict]:
    """Backward-compatible section list."""
    return build_help_payload(profile)["sections"]
