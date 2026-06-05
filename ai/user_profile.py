"""Per-user motor profiles for arthritis and tremor support.

Profiles tune speed, confirmation windows, gesture tolerance, and default
input preferences so the same codebase serves early through severe motor loss.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from config import RuntimeConfig


class MotorLevel(str, Enum):
    EARLY = "early"
    MODERATE = "moderate"
    SEVERE = "severe"


class PreferredInput(str, Enum):
    VOICE = "voice"
    GESTURE = "gesture"
    PANEL = "panel"
    AUTO = "auto"


@dataclass(slots=True)
class UserProfile:
    motor_level: MotorLevel = MotorLevel.MODERATE
    preferred_input: PreferredInput = PreferredInput.AUTO
    display_name: str = "User"
    default_speed_pct: int = 30
    confirmation_timeout_s: float = 18.0
    gesture_stable_requirement: int = 5
    gesture_confirm_frames: int = 2
    gesture_hud_smooth_len: int = 7
    gesture_hud_min_votes: int = 3
    gesture_action_cooldown_s: float = 0.55
    simple_gesture_mode: bool = False
    accessibility_ui: bool = True
    voice_short_phrases: bool = False
    movement_step_scale: float = 1.0
    require_confirmation_all_picks: bool = True
    caregiver_mode: bool = False
    notes: str = ""
    enable_voice_input: bool = True
    enable_gesture_input: bool = True
    enable_manual_input: bool = True
    quit_gesture: str = "peace_hold"  # peace_hold only (peace sign hold to quit)
    rest_reminder_minutes: int = 45
    fatigue_slowdown: bool = True
    gentle_reach: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["motor_level"] = self.motor_level.value
        data["preferred_input"] = self.preferred_input.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UserProfile:
        level = MotorLevel(str(data.get("motor_level", MotorLevel.MODERATE.value)))
        pref = PreferredInput(str(data.get("preferred_input", PreferredInput.AUTO.value)))
        return cls(
            motor_level=level,
            preferred_input=pref,
            display_name=str(data.get("display_name", "User")),
            default_speed_pct=int(data.get("default_speed_pct", 30)),
            confirmation_timeout_s=float(data.get("confirmation_timeout_s", 18.0)),
            gesture_stable_requirement=int(data.get("gesture_stable_requirement", 5)),
            gesture_confirm_frames=int(data.get("gesture_confirm_frames", 2)),
            gesture_hud_smooth_len=int(data.get("gesture_hud_smooth_len", 7)),
            gesture_hud_min_votes=int(data.get("gesture_hud_min_votes", 3)),
            gesture_action_cooldown_s=float(data.get("gesture_action_cooldown_s", 0.55)),
            simple_gesture_mode=bool(data.get("simple_gesture_mode", False)),
            accessibility_ui=bool(data.get("accessibility_ui", True)),
            voice_short_phrases=bool(data.get("voice_short_phrases", False)),
            movement_step_scale=float(data.get("movement_step_scale", 1.0)),
            require_confirmation_all_picks=bool(data.get("require_confirmation_all_picks", True)),
            caregiver_mode=bool(data.get("caregiver_mode", False)),
            notes=str(data.get("notes", "")),
            enable_voice_input=bool(data.get("enable_voice_input", True)),
            enable_gesture_input=bool(data.get("enable_gesture_input", True)),
            enable_manual_input=bool(data.get("enable_manual_input", True)),
            quit_gesture=str(data.get("quit_gesture", "peace_hold")),
            rest_reminder_minutes=int(data.get("rest_reminder_minutes", 45)),
            fatigue_slowdown=bool(data.get("fatigue_slowdown", True)),
            gentle_reach=bool(data.get("gentle_reach", True)),
        )


def preset_for_level(level: MotorLevel) -> UserProfile:
    """Factory presets tuned for arthritis / tremor severity."""
    if level is MotorLevel.EARLY:
        return UserProfile(
            motor_level=level,
            preferred_input=PreferredInput.AUTO,
            default_speed_pct=38,
            confirmation_timeout_s=14.0,
            gesture_stable_requirement=3,
            gesture_confirm_frames=2,
            gesture_hud_smooth_len=5,
            gesture_hud_min_votes=2,
            gesture_action_cooldown_s=0.4,
            simple_gesture_mode=False,
            accessibility_ui=True,
            voice_short_phrases=False,
            movement_step_scale=1.0,
        )
    if level is MotorLevel.SEVERE:
        return UserProfile(
            motor_level=level,
            preferred_input=PreferredInput.PANEL,
            default_speed_pct=22,
            confirmation_timeout_s=28.0,
            gesture_stable_requirement=8,
            gesture_confirm_frames=3,
            gesture_hud_smooth_len=9,
            gesture_hud_min_votes=4,
            gesture_action_cooldown_s=0.75,
            simple_gesture_mode=True,
            accessibility_ui=True,
            voice_short_phrases=True,
            movement_step_scale=0.75,
        )
    return UserProfile(
        motor_level=MotorLevel.MODERATE,
        preferred_input=PreferredInput.AUTO,
        default_speed_pct=30,
        confirmation_timeout_s=18.0,
        gesture_stable_requirement=5,
        gesture_confirm_frames=2,
        gesture_hud_smooth_len=7,
        gesture_hud_min_votes=3,
        gesture_action_cooldown_s=0.55,
        simple_gesture_mode=False,
        accessibility_ui=True,
        voice_short_phrases=True,
        movement_step_scale=0.9,
    )


class UserProfileStore:
    """Persist and load the active user profile (SQLite)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profile (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    profile_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def load(self, *, default_level: MotorLevel = MotorLevel.MODERATE) -> UserProfile:
        with self._connect() as conn:
            row = conn.execute("SELECT profile_json FROM user_profile WHERE id = 1").fetchone()
        if row is None:
            return preset_for_level(default_level)
        try:
            return UserProfile.from_dict(json.loads(row["profile_json"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            return preset_for_level(default_level)

    def save(self, profile: UserProfile) -> None:
        payload = json.dumps(profile.to_dict())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO user_profile (id, profile_json, updated_at)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET profile_json = excluded.profile_json, updated_at = excluded.updated_at
                """,
                (payload, time.time()),
            )


def apply_profile_to_config(config: RuntimeConfig, profile: UserProfile) -> RuntimeConfig:
    """Return a new RuntimeConfig with profile-tuned motion and gesture settings."""
    from dataclasses import replace

    scale = max(0.5, min(1.5, profile.movement_step_scale))
    steps = {
        joint: max(1, int(round(config.movement_steps[joint] * scale)))
        for joint in config.movement_steps
    }
    return replace(
        config,
        default_speed_pct=profile.default_speed_pct,
        confirmation_timeout_s=profile.confirmation_timeout_s,
        gesture_stable_requirement=profile.gesture_stable_requirement,
        gesture_confirm_frames=profile.gesture_confirm_frames,
        gesture_hud_smooth_len=profile.gesture_hud_smooth_len,
        gesture_hud_min_votes=profile.gesture_hud_min_votes,
        gesture_action_cooldown_s=profile.gesture_action_cooldown_s,
        movement_steps=steps,
        quit_gesture=profile.quit_gesture if profile.quit_gesture in {"thumbs_up", "peace_hold"} else config.quit_gesture,
        simple_gesture_mode=profile.simple_gesture_mode,
        voice_short_phrases=profile.voice_short_phrases,
    )
