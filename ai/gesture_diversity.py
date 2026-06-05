"""Ensure major arm movement families rotate before repeating the same motion."""

from __future__ import annotations

from dataclasses import dataclass, field

# Core degrees of freedom the assistive arm exposes via gestures.
_MOVEMENT_FAMILIES: dict[str, frozenset[str]] = {
    "lift": frozenset({"lift_up", "lift_down"}),
    "base": frozenset({"base_left", "base_right"}),
    "rotate": frozenset({"rotate_left", "rotate_right"}),
    "claw": frozenset({"open_claw", "close_claw"}),
}

_EXEMPT_INTENTS: frozenset[str] = frozenset({
    "confirm_yes",
    "confirm_no",
    "emergency_stop",
    "home",
    "shutdown",
    "pick_object",
    "place_object",
    "preset_pose",
    "teach_gesture",
    "teach_phrase",
    "reset_gestures",
})


def movement_family(intent: str) -> str | None:
    for family, intents in _MOVEMENT_FAMILIES.items():
        if intent in intents:
            return family
    return None


@dataclass
class GestureDiversityTracker:
    """Block repeating a movement family until all four have been used."""

    covered: set[str] = field(default_factory=set)
    last_family: str | None = None
    blocked_count: int = 0

    def should_block(self, intent: str) -> bool:
        if intent in _EXEMPT_INTENTS:
            return False
        family = movement_family(intent)
        if family is None:
            return False
        if family in self.covered and len(self.covered) < len(_MOVEMENT_FAMILIES):
            return True
        return False

    def record(self, intent: str) -> None:
        if intent in _EXEMPT_INTENTS:
            return
        family = movement_family(intent)
        if family is None:
            return
        self.last_family = family
        self.covered.add(family)
        if len(self.covered) >= len(_MOVEMENT_FAMILIES):
            self.covered.clear()
            self.covered.add(family)

    def snapshot(self) -> dict:
        total = len(_MOVEMENT_FAMILIES)
        return {
            "families_total": total,
            "families_covered": len(self.covered),
            "covered": sorted(self.covered),
            "remaining": sorted(set(_MOVEMENT_FAMILIES) - self.covered),
            "blocked_count": self.blocked_count,
            "hint": self._hint(),
        }

    def _hint(self) -> str:
        remaining = set(_MOVEMENT_FAMILIES) - self.covered
        if not remaining:
            return "All major movements ready — any gesture may repeat."
        labels = {
            "lift": "lift up or down",
            "base": "turn base left or right",
            "rotate": "rotate wrist",
            "claw": "open or close claw",
        }
        parts = [labels.get(r, r) for r in sorted(remaining)]
        return "Try " + ", ".join(parts) + " before repeating the last motion."
