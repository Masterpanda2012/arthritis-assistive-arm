from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

LOGGER = logging.getLogger(__name__)

@dataclass
class MapObject:
    label: str
    confidence: float
    base_deg: int
    distance_mm: int
    timestamp: float


class EnvironmentMap:
    def __init__(self, expiration_s: float = 60.0):
        self.expiration_s = expiration_s
        self._objects: dict[str, MapObject] = {}

    def update_object(self, label: str, base_deg: int, distance_mm: int, confidence: float) -> None:
        """Register or update an object tracked via vision + lidar + servo base angle."""
        self._objects[label] = MapObject(
            label=label,
            confidence=confidence,
            base_deg=base_deg,
            distance_mm=distance_mm,
            timestamp=time.time()
        )
        LOGGER.info("Mapped %s at base_deg=%d, dist=%dmm", label, base_deg, distance_mm)

    def get_object(self, label: str) -> Optional[MapObject]:
        """Fetch an object's location if it was mapped recently."""
        obj = self._objects.get(label)
        if obj is None:
            return None
            
        if time.time() - obj.timestamp > self.expiration_s:
            del self._objects[label]
            return None
            
        return obj

    def clear(self) -> None:
        self._objects.clear()

    @property
    def known_labels(self) -> list[str]:
        now = time.time()
        return [label for label, obj in self._objects.items() if now - obj.timestamp <= self.expiration_s]
