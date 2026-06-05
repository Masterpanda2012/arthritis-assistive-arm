"""Personal gesture catalogue — landmark templates taught from the web studio."""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from ai.gesture_bindings import _DEFAULT_BINDINGS, canonical_gesture, canonical_intent
from ai.memory_store import MemoryStore


def _dist(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def landmarks_to_vector(landmarks: Sequence[Any]) -> list[float]:
    """Normalize 21 hand landmarks to a rotation-invariant 63-float vector."""
    wrist = landmarks[0]
    scale = max(_dist((wrist.x, wrist.y, wrist.z), (landmarks[9].x, landmarks[9].y, landmarks[9].z)), 1e-4)
    out: list[float] = []
    for lm in landmarks:
        out.extend([(lm.x - wrist.x) / scale, (lm.y - wrist.y) / scale, (lm.z - wrist.z) / scale])
    return out


def average_vectors(vectors: Sequence[Sequence[float]]) -> list[float]:
    if not vectors:
        return []
    dim = len(vectors[0])
    acc = [0.0] * dim
    for vec in vectors:
        for i, v in enumerate(vec):
            acc[i] += float(v)
    n = float(len(vectors))
    return [v / n for v in acc]


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug[:48] or f"custom_{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class CustomGesture:
    gesture_id: str
    display_name: str
    description: str
    intent: str
    payload: dict[str, Any]
    template: list[float]
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gesture_id": self.gesture_id,
            "display_name": self.display_name,
            "description": self.description,
            "intent": self.intent,
            "payload": dict(self.payload),
            "template": list(self.template),
            "created_at": self.created_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> CustomGesture:
        payload = json.loads(row["payload_json"] or "{}")
        template = json.loads(row["template_json"] or "[]")
        if not isinstance(payload, dict):
            payload = {}
        if not isinstance(template, list):
            template = []
        return cls(
            gesture_id=str(row["gesture_id"]),
            display_name=str(row["display_name"]),
            description=str(row["description"]),
            intent=str(row["intent"]),
            payload=payload,
            template=[float(x) for x in template],
            created_at=float(row["created_at"]),
        )


class CustomGestureCatalog:
    """Load, match, and persist user-taught landmark templates."""

    SIMILARITY_DUPLICATE = 0.90
    SIMILARITY_MATCH = 0.78

    def __init__(self, memory: MemoryStore) -> None:
        self._memory = memory
        self._items: dict[str, CustomGesture] = memory.load_custom_gestures()

    def list_all(self) -> list[CustomGesture]:
        return sorted(self._items.values(), key=lambda g: g.display_name.lower())

    def get(self, gesture_id: str) -> CustomGesture | None:
        return self._items.get(gesture_id)

    def find_similar(self, template: Sequence[float], *, exclude_id: str | None = None) -> tuple[CustomGesture | None, float]:
        best: CustomGesture | None = None
        best_score = 0.0
        for item in self._items.values():
            if exclude_id and item.gesture_id == exclude_id:
                continue
            score = cosine_similarity(template, item.template)
            if score > best_score:
                best_score = score
                best = item
        return best, best_score

    def match(self, landmarks: Sequence[Any]) -> CustomGesture | None:
        vector = landmarks_to_vector(landmarks)
        item, score = self.find_similar(vector)
        if item is not None and score >= self.SIMILARITY_MATCH:
            return item
        return None

    def validate_new(
        self,
        *,
        display_name: str,
        template: Sequence[float],
        intent: str,
    ) -> tuple[bool, str, CustomGesture | None]:
        canonical = canonical_gesture(display_name)
        if canonical in {"peace", "thumbs_up"}:
            return False, f"'{display_name}' is reserved for system control.", None
        if canonical and canonical in _DEFAULT_BINDINGS:
            return False, f"This looks like the built-in '{canonical}' gesture — use that instead.", None

        gesture_id = slugify(display_name)
        if gesture_id in self._items:
            return False, f"You already taught '{display_name}'.", self._items[gesture_id]

        similar, score = self.find_similar(template)
        if similar is not None and score >= self.SIMILARITY_DUPLICATE:
            return (
                False,
                f"Too similar to your gesture '{similar.display_name}' ({score:.0%} match). Try a clearer pose.",
                similar,
            )

        if intent not in {
            "open_claw", "close_claw", "lift_up", "lift_down",
            "base_left", "base_right", "rotate_left", "rotate_right",
            "home", "emergency_stop", "preset_pose", "confirm_yes", "confirm_no",
        }:
            return False, f"Intent '{intent}' is not supported for custom gestures.", None

        return True, "ok", None

    def add(
        self,
        *,
        display_name: str,
        description: str,
        intent: str,
        payload: dict | None,
        template: Sequence[float],
    ) -> CustomGesture:
        ok, msg, _ = self.validate_new(display_name=display_name, template=template, intent=intent)
        if not ok:
            raise ValueError(msg)
        item = CustomGesture(
            gesture_id=slugify(display_name),
            display_name=display_name.strip(),
            description=description.strip(),
            intent=intent,
            payload=dict(payload or {}),
            template=list(template),
        )
        self._items[item.gesture_id] = item
        self._memory.save_custom_gesture(item)
        return item

    def remove(self, gesture_id: str) -> bool:
        if gesture_id not in self._items:
            return False
        del self._items[gesture_id]
        self._memory.delete_custom_gesture(gesture_id)
        return True


def interpret_gesture_description(text: str) -> dict[str, Any]:
    """Map a natural-language gesture description to intent + suggested label."""
    raw = " ".join(text.strip().split())
    if not raw:
        raise ValueError("Describe the gesture in a few words.")

    intent_hit = canonical_intent(raw)
    if intent_hit is None:
        lowered = raw.lower()
        for phrase, binding in _DESCRIPTION_HINTS:
            if phrase in lowered:
                intent_hit = binding
                break

    if intent_hit is None:
        raise ValueError(
            "Could not infer an arm action. Try phrases like “lift up”, “open claw”, or “go home”."
        )

    intent, payload = intent_hit
    words = re.findall(r"[a-zA-Z]+", raw.lower())
    stop = {
        "when", "i", "do", "the", "a", "an", "my", "make", "arm", "robot",
        "gesture", "hand", "sign", "move", "please", "want", "to", "and", "or",
    }
    meaningful = [w for w in words if w not in stop][:4]
    display_name = " ".join(meaningful).title() if meaningful else "Custom Gesture"
    canonical = canonical_gesture(raw)
    suggested_label = canonical or slugify(display_name)

    return {
        "display_name": display_name,
        "suggested_label": suggested_label,
        "intent": intent,
        "payload": dict(payload),
        "description": raw,
        "builtin_match": canonical if canonical in _DEFAULT_BINDINGS else None,
    }


_DESCRIPTION_HINTS: list[tuple[str, tuple[str, dict]]] = [
    ("lift up", ("lift_up", {})),
    ("raise", ("lift_up", {})),
    ("lift down", ("lift_down", {})),
    ("lower", ("lift_down", {})),
    ("turn left", ("base_left", {})),
    ("turn right", ("base_right", {})),
    ("rotate left", ("rotate_left", {})),
    ("rotate right", ("rotate_right", {})),
    ("open claw", ("open_claw", {})),
    ("open hand", ("open_claw", {})),
    ("close claw", ("close_claw", {})),
    ("fist", ("close_claw", {})),
    ("grab", ("close_claw", {})),
    ("home", ("home", {})),
    ("stop", ("emergency_stop", {})),
    ("yes", ("confirm_yes", {})),
    ("no", ("confirm_no", {})),
    ("thumbs up", ("confirm_yes", {})),
    ("peace", ("confirm_yes", {})),
    ("pickup", ("preset_pose", {"name": "pickup_ready"})),
    ("drop", ("preset_pose", {"name": "drop_ready"})),
    ("inspect", ("preset_pose", {"name": "inspect"})),
]
