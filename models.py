from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ActionRequest:
    source: str
    intent: str
    payload: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False


@dataclass(slots=True)
class ArmCommand:
    base_deg: int
    lift_deg: int
    rotate_deg: int
    claw_deg: int
    speed_pct: int
    origin: str


@dataclass(slots=True)
class ArmState:
    base_deg: int
    lift_deg: int
    rotate_deg: int
    claw_deg: int
    range_mm: int
    estop: bool
    last_error: str


@dataclass(slots=True)
class VisionTarget:
    label: str
    confidence: float
    image_x: float
    image_y: float
    range_mm: int
    timestamp: float
    depth_mm: float = -1.0
    camera_x_mm: float = 0.0
    camera_y_mm: float = 0.0
    camera_z_mm: float = 0.0
    has_3d: bool = False


@dataclass(slots=True)
class PlannerResult:
    kind: str
    commands: tuple[ArmCommand, ...] = ()
