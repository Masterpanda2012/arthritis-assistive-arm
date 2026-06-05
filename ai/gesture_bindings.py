"""User-teachable gesture → action bindings.

Defaults live in code so a fresh install is immediately useful, but any
``teach <gesture> as <action>`` command from the user persists to the
same SQLite memory database and silently overrides the default. This is
what makes the robot actually *learn* from the operator: after they
rewire a few gestures, the new mapping is remembered across restarts.
"""

from __future__ import annotations

import logging
from typing import Iterable

from ai.memory_store import MemoryStore
from models import ActionRequest


LOGGER = logging.getLogger(__name__)


# Gestures reserved for system control (quit / safety).
_RESERVED: set[str] = {"peace", "thumbs_up"}


# Human-friendly aliases → canonical gesture label. Used by the teach
# parser so users can say "teach peace sign as rotate right".
_GESTURE_ALIASES: dict[str, str] = {
    "fist": "fist",
    "closed fist": "fist",
    "open": "open",
    "open hand": "open",
    "palm": "open",
    "flat hand": "open",
    "tilt left": "tilt_left",
    "left tilt": "tilt_left",
    "tilt right": "tilt_right",
    "right tilt": "tilt_right",
    "one": "one",
    "one finger": "one",
    "two": "two",
    "two fingers": "two",
    "three": "three",
    "three fingers": "three",
    "four": "four",
    "four fingers": "four",
    "pinch": "pinch",
    "pinching": "pinch",
    "zoom": "pinch",
    "ok": "pinch",
    "okay": "pinch",
    "ok sign": "pinch",
    "rock": "rock",
    "rock sign": "rock",
    "metal": "rock",
    "horns": "rock",
    "devil horns": "rock",
    "call me": "call_me",
    "call": "call_me",
    "shaka": "call_me",
    "hang loose": "call_me",
    "point": "point",
    "pointer": "point",
    "pointing": "point",
    "index": "point",
    "l": "point",
    "l shape": "point",
    "l sign": "point",
    "finger gun": "point",
    "gun": "point",
    "pistol": "point",
    "peace": "peace",
    "peace sign": "peace",
    "v sign": "peace",
    "victory": "peace",
    "thumbs down": "thumbs_down",
    "thumbsdown": "thumbs_down",
    "thumbs up": "thumbs_up",
    "thumbsup": "thumbs_up",
    "like": "thumbs_up",
    "stop palm": "stop_palm",
    "high five": "high_five",
    "spread": "spread",
    "spread fingers": "spread",
    "spider": "spider",
    "three spread": "spider",
    "ok circle": "ok_circle",
    "ok": "ok_circle",
    "circle": "ok_circle",
    "got it": "ok_circle",
    "palm down": "palm_down",
    "flat palm": "palm_down",
    "rest hand": "palm_down",
    "calm down": "palm_down",
}


_DEFAULT_BINDINGS: dict[str, tuple[str, dict]] = {
    "fist": ("close_claw", {}),
    "open": ("open_claw", {}),
    "tilt_left": ("base_left", {}),
    "tilt_right": ("base_right", {}),
    "one": ("lift_up", {}),
    "two": ("lift_down", {}),
    "three": ("rotate_left", {}),
    "four": ("preset_pose", {"name": "inspect"}),
    # New gestures with richer output coverage.
    "pinch": ("rotate_right", {}),
    "rock": ("emergency_stop", {}),
    "call_me": ("preset_pose", {"name": "pickup_ready"}),
    "point": ("preset_pose", {"name": "drop_ready"}),
    "thumbs_up": ("confirm_yes", {}),
    "thumbs_down": ("home", {}),
    "stop_palm": ("emergency_stop", {}),
    "high_five": ("preset_pose", {"name": "inspect"}),
    "spread": ("open_claw", {}),
    "spider": ("rotate_right", {}),
    # Arthritis-friendly alternates — less thumb strain than thumbs-up / peace.
    "ok_circle": ("confirm_yes", {}),
    "palm_down": ("home", {}),
}


# Valid action intents that users can bind a gesture to. Keeping the
# list explicit prevents typos like "rotate_rite" from silently
# persisting into the database.
_VALID_INTENTS: set[str] = {
    "open_claw", "close_claw",
    "lift_up", "lift_down",
    "base_left", "base_right",
    "rotate_left", "rotate_right",
    "home", "emergency_stop",
    "preset_pose",
    "confirm_yes", "confirm_no",
}


_INTENT_ALIASES: dict[str, tuple[str, dict]] = {
    "open claw": ("open_claw", {}),
    "open gripper": ("open_claw", {}),
    "release": ("open_claw", {}),
    "close claw": ("close_claw", {}),
    "close gripper": ("close_claw", {}),
    "grip": ("close_claw", {}),
    "grab": ("close_claw", {}),
    "lift up": ("lift_up", {}),
    "arm up": ("lift_up", {}),
    "raise": ("lift_up", {}),
    "lift down": ("lift_down", {}),
    "arm down": ("lift_down", {}),
    "lower": ("lift_down", {}),
    "base left": ("base_left", {}),
    "turn left": ("base_left", {}),
    "base right": ("base_right", {}),
    "turn right": ("base_right", {}),
    "rotate left": ("rotate_left", {}),
    "spin left": ("rotate_left", {}),
    "twist left": ("rotate_left", {}),
    "rotate right": ("rotate_right", {}),
    "spin right": ("rotate_right", {}),
    "twist right": ("rotate_right", {}),
    "home": ("home", {}),
    "go home": ("home", {}),
    "reset": ("home", {}),
    "stop": ("emergency_stop", {}),
    "emergency stop": ("emergency_stop", {}),
    "e stop": ("emergency_stop", {}),
    "freeze": ("emergency_stop", {}),
    "halt": ("emergency_stop", {}),
    "inspect": ("preset_pose", {"name": "inspect"}),
    "look": ("preset_pose", {"name": "inspect"}),
    "pickup": ("preset_pose", {"name": "pickup_ready"}),
    "pickup ready": ("preset_pose", {"name": "pickup_ready"}),
    "pick ready": ("preset_pose", {"name": "pickup_ready"}),
    "drop": ("preset_pose", {"name": "drop_ready"}),
    "drop ready": ("preset_pose", {"name": "drop_ready"}),
    "place": ("preset_pose", {"name": "drop_ready"}),
}


def canonical_gesture(name: str) -> str | None:
    """Resolve a human phrase to a canonical gesture label, or None."""
    key = " ".join(name.strip().lower().split())
    if not key:
        return None
    if key in _GESTURE_ALIASES:
        return _GESTURE_ALIASES[key]
    snake = key.replace(" ", "_")
    if snake in _DEFAULT_BINDINGS or snake in _GESTURE_ALIASES.values():
        return snake
    return None


def canonical_intent(text: str) -> tuple[str, dict] | None:
    """Resolve a human phrase to ``(intent, payload)`` or None.

    Accepts both raw intent names (``rotate_right``) and free-form
    phrases (``spin right``, ``go home``, ``inspect pose``).
    """
    key = " ".join(text.strip().lower().split())
    if not key:
        return None
    # Exact intent id.
    snake = key.replace(" ", "_")
    if snake in _VALID_INTENTS and snake != "preset_pose":
        return (snake, {})
    if key in _INTENT_ALIASES:
        intent, payload = _INTENT_ALIASES[key]
        return (intent, dict(payload))
    # "pose X" / "preset X".
    for prefix in ("pose ", "preset ", "preset pose "):
        if key.startswith(prefix):
            name = key[len(prefix):].strip().replace(" ", "_")
            if name in {"home", "inspect", "pickup_ready", "drop_ready"}:
                if name == "home":
                    return ("home", {})
                return ("preset_pose", {"name": name})
    return None


class GestureBindings:
    """Combined defaults + persistent user overrides for gesture actions."""

    def __init__(self, memory_store: MemoryStore) -> None:
        self._memory = memory_store
        try:
            self._user: dict[str, tuple[str, dict]] = memory_store.load_gesture_bindings()
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Could not load learned gesture bindings: %s", exc)
            self._user = {}

    @property
    def known_gestures(self) -> set[str]:
        return set(_DEFAULT_BINDINGS.keys()) | set(self._user.keys())

    def is_reserved(self, gesture: str) -> bool:
        return gesture in _RESERVED

    def binding_for(self, gesture: str) -> tuple[str, dict] | None:
        if gesture in _RESERVED:
            return None
        if gesture in self._user:
            return self._user[gesture]
        return _DEFAULT_BINDINGS.get(gesture)

    def action_for(self, gesture: str, *, source: str = "gesture") -> ActionRequest | None:
        binding = self.binding_for(gesture)
        if binding is None:
            return None
        intent, payload = binding
        requires_confirm = intent in {"pick_object", "place_object"}
        return ActionRequest(
            source=source,
            intent=intent,
            payload=dict(payload),
            requires_confirmation=requires_confirm,
        )

    def set(self, gesture: str, intent: str, payload: dict | None = None) -> tuple[str, dict]:
        if gesture in _RESERVED:
            raise ValueError(f"gesture '{gesture}' is reserved and cannot be rebound")
        if gesture not in _DEFAULT_BINDINGS:
            # Still allow binding an unknown label (useful for future gestures),
            # but log so the user isn't confused when it never fires.
            LOGGER.info("Binding unknown gesture label '%s'; add a detector to use it.", gesture)
        if intent not in _VALID_INTENTS:
            raise ValueError(f"intent '{intent}' is not supported")
        payload = dict(payload or {})
        if intent == "preset_pose":
            name = str(payload.get("name", "")).strip().lower()
            if name not in {"home", "inspect", "pickup_ready", "drop_ready"}:
                raise ValueError(f"unknown preset pose '{name}'")
            payload["name"] = name
        self._user[gesture] = (intent, payload)
        self._memory.save_gesture_binding(gesture, intent, payload)
        return (intent, payload)

    def clear(self, gesture: str | None = None) -> None:
        if gesture is None:
            self._user.clear()
            self._memory.clear_gesture_bindings()
            return
        self._user.pop(gesture, None)
        self._memory.delete_gesture_binding(gesture)

    def describe(self, include_defaults: bool = True) -> list[tuple[str, str, dict, bool]]:
        """Return ``[(gesture, intent, payload, is_user_taught)]`` rows."""
        rows: list[tuple[str, str, dict, bool]] = []
        names: Iterable[str] = (
            sorted(set(_DEFAULT_BINDINGS) | set(self._user))
            if include_defaults
            else sorted(self._user)
        )
        for g in names:
            if g in self._user:
                intent, payload = self._user[g]
                rows.append((g, intent, dict(payload), True))
            elif include_defaults and g in _DEFAULT_BINDINGS:
                intent, payload = _DEFAULT_BINDINGS[g]
                rows.append((g, intent, dict(payload), False))
        return rows
