"""Suggest fallback input modalities when one channel is struggling."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from ai.user_profile import PreferredInput, UserProfile


@dataclass
class InputChannelStats:
    last_success_at: float = 0.0
    last_attempt_at: float = 0.0
    failures: int = 0
    successes: int = 0


@dataclass
class InputFallbackAdvisor:
    """Tracks per-modality health and recommends easier inputs for arthritis users."""

    profile: UserProfile
    channels: dict[str, InputChannelStats] = field(default_factory=dict)
    last_suggestion_at: float = 0.0
    last_suggestion: str = ""

    def __post_init__(self) -> None:
        for name in ("voice", "gesture", "panel", "vision"):
            self.channels.setdefault(name, InputChannelStats())

    def record_attempt(self, channel: str) -> None:
        stats = self.channels.setdefault(channel, InputChannelStats())
        stats.last_attempt_at = time.monotonic()

    def record_success(self, channel: str) -> None:
        stats = self.channels.setdefault(channel, InputChannelStats())
        now = time.monotonic()
        stats.last_success_at = now
        stats.last_attempt_at = now
        stats.successes += 1
        stats.failures = max(0, stats.failures - 1)

    def record_failure(self, channel: str, *, reason: str = "") -> None:
        stats = self.channels.setdefault(channel, InputChannelStats())
        stats.last_attempt_at = time.monotonic()
        stats.failures += 1

    def preferred_channels(self) -> list[str]:
        pref = self.profile.preferred_input
        if pref is PreferredInput.VOICE:
            return ["voice", "panel", "gesture", "vision"]
        if pref is PreferredInput.GESTURE:
            return ["gesture", "voice", "panel", "vision"]
        if pref is PreferredInput.PANEL:
            return ["panel", "voice", "gesture", "vision"]
        if self.profile.motor_level.value == "severe":
            return ["panel", "voice", "vision", "gesture"]
        if self.profile.motor_level.value == "early":
            return ["voice", "gesture", "vision", "panel"]
        return ["voice", "panel", "gesture", "vision"]

    def suggest_fallback(self, *, now: float | None = None) -> str | None:
        """Return a user-facing hint when the active channel looks unhealthy."""
        now = now if now is not None else time.monotonic()
        if now - self.last_suggestion_at < 12.0 and self.last_suggestion:
            return None

        order = self.preferred_channels()
        unhealthy: list[str] = []
        for channel in order:
            stats = self.channels[channel]
            if stats.failures >= 3 and stats.successes == 0:
                unhealthy.append(channel)
            elif stats.last_attempt_at and not stats.last_success_at:
                if now - stats.last_attempt_at > 20.0:
                    unhealthy.append(channel)
            elif stats.last_attempt_at > stats.last_success_at:
                if now - stats.last_attempt_at > 25.0:
                    unhealthy.append(channel)

        if not unhealthy:
            return None

        blocked = unhealthy[0]
        for alt in order:
            if alt == blocked:
                continue
            alt_stats = self.channels[alt]
            if alt_stats.failures < 3:
                messages = {
                    ("voice", "panel"): "Voice seems difficult — try the large buttons on the control panel.",
                    ("voice", "gesture"): "Having trouble speaking clearly? Try a simple hand gesture.",
                    ("gesture", "voice"): "Gestures look shaky — try saying what you need in plain English.",
                    ("gesture", "panel"): "Hand tremors detected — the control panel may be easier right now.",
                    ("panel", "voice"): "You can also say commands like “get my pills” or “stop”.",
                    ("vision", "voice"): "I couldn't see the object — try telling me what to fetch.",
                }
                msg = messages.get((blocked, alt), f"Try using {alt} instead of {blocked}.")
                self.last_suggestion_at = now
                self.last_suggestion = msg
                return msg
        return None

    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "preferred": [c for c in self.preferred_channels()],
            "channels": {
                name: {
                    "successes": s.successes,
                    "failures": s.failures,
                    "idle_s": round(now - s.last_attempt_at, 1) if s.last_attempt_at else -1.0,
                }
                for name, s in self.channels.items()
            },
            "last_suggestion": self.last_suggestion,
        }
