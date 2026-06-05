"""Camera-to-arm coordinate transforms and calibration persistence."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CameraIntrinsics:
    focal_length_px: float
    cx: float
    cy: float


@dataclass(frozen=True, slots=True)
class ArmCalibration:
    """Maps camera-frame millimeters to rough joint targets."""

    version: int
    intrinsics: CameraIntrinsics
    base_center_deg: int
    base_deg_per_mm_x: float
    lift_center_deg: int
    lift_deg_per_mm_z: float
    rotate_center_deg: int
    rotate_deg_per_pixel_y: float
    depth_scale: float = 1.0
    min_depth_mm: float = 120.0
    max_depth_mm: float = 800.0

    def camera_mm_to_joints(self, x_mm: float, y_mm: float, z_mm: float) -> dict[str, int]:
        z_mm = max(self.min_depth_mm, min(self.max_depth_mm, z_mm * self.depth_scale))
        base = int(round(self.base_center_deg + x_mm * self.base_deg_per_mm_x))
        lift = int(round(self.lift_center_deg - (z_mm - 300.0) * self.lift_deg_per_mm_z))
        rotate = int(round(self.rotate_center_deg + y_mm * 0.04))
        return {"base_deg": base, "lift_deg": lift, "rotate_deg": rotate}

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "intrinsics": {
                "focal_length_px": self.intrinsics.focal_length_px,
                "cx": self.intrinsics.cx,
                "cy": self.intrinsics.cy,
            },
            "base_center_deg": self.base_center_deg,
            "base_deg_per_mm_x": self.base_deg_per_mm_x,
            "lift_center_deg": self.lift_center_deg,
            "lift_deg_per_mm_z": self.lift_deg_per_mm_z,
            "rotate_center_deg": self.rotate_center_deg,
            "rotate_deg_per_pixel_y": self.rotate_deg_per_pixel_y,
            "depth_scale": self.depth_scale,
            "min_depth_mm": self.min_depth_mm,
            "max_depth_mm": self.max_depth_mm,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArmCalibration:
        intr = data.get("intrinsics") or {}
        return cls(
            version=int(data.get("version", 1)),
            intrinsics=CameraIntrinsics(
                focal_length_px=float(intr.get("focal_length_px", 640.0)),
                cx=float(intr.get("cx", 0.5)),
                cy=float(intr.get("cy", 0.5)),
            ),
            base_center_deg=int(data.get("base_center_deg", 120)),
            base_deg_per_mm_x=float(data.get("base_deg_per_mm_x", 0.08)),
            lift_center_deg=int(data.get("lift_center_deg", 120)),
            lift_deg_per_mm_z=float(data.get("lift_deg_per_mm_z", 0.06)),
            rotate_center_deg=int(data.get("rotate_center_deg", 90)),
            rotate_deg_per_pixel_y=float(data.get("rotate_deg_per_pixel_y", 35.0)),
            depth_scale=float(data.get("depth_scale", 1.0)),
            min_depth_mm=float(data.get("min_depth_mm", 120.0)),
            max_depth_mm=float(data.get("max_depth_mm", 800.0)),
        )


def default_calibration(*, frame_width: int = 640, frame_height: int = 480) -> ArmCalibration:
    return ArmCalibration(
        version=1,
        intrinsics=CameraIntrinsics(
            focal_length_px=max(frame_width, frame_height) * 0.92,
            cx=frame_width / 2.0,
            cy=frame_height / 2.0,
        ),
        base_center_deg=120,
        base_deg_per_mm_x=0.085,
        lift_center_deg=120,
        lift_deg_per_mm_z=0.055,
        rotate_center_deg=92,
        rotate_deg_per_pixel_y=30.0,
    )


def load_calibration(path: Path) -> ArmCalibration | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cal = ArmCalibration.from_dict(data)
        LOGGER.info("Loaded arm calibration from %s", path)
        return cal
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        LOGGER.warning("Could not load calibration %s (%s)", path, exc)
        return None


def save_calibration(path: Path, calibration: ArmCalibration) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration.to_dict(), indent=2), encoding="utf-8")
    LOGGER.info("Saved arm calibration to %s", path)


def pixel_depth_to_camera_mm(
    *,
    u_px: float,
    v_px: float,
    depth_mm: float,
    intrinsics: CameraIntrinsics,
) -> tuple[float, float, float]:
    """Convert pixel + depth to camera-frame coordinates (mm)."""
    if depth_mm <= 0:
        return 0.0, 0.0, 0.0
    z = depth_mm
    x = (u_px - intrinsics.cx) * z / max(1.0, intrinsics.focal_length_px)
    y = (v_px - intrinsics.cy) * z / max(1.0, intrinsics.focal_length_px)
    return x, y, z


def bbox_center_depth_mm(
    depth_map,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    frame_width: int,
    frame_height: int,
) -> float:
    """Median depth (mm) inside a bounding box from a depth map (H×W, mm)."""
    import numpy as np

    if depth_map is None:
        return -1.0
    arr = np.asarray(depth_map, dtype=np.float32)
    if arr.ndim != 2:
        return -1.0
    h, w = arr.shape
    ix1 = max(0, min(w - 1, int(x1 * w / max(1, frame_width))))
    ix2 = max(0, min(w, int(x2 * w / max(1, frame_width))))
    iy1 = max(0, min(h - 1, int(y1 * h / max(1, frame_height))))
    iy2 = max(0, min(h, int(y2 * h / max(1, frame_height))))
    if ix2 <= ix1 or iy2 <= iy1:
        return -1.0
    patch = arr[iy1:iy2, ix1:ix2]
    valid = patch[(patch > 50) & (patch < 5000)]
    if valid.size == 0:
        return -1.0
    return float(np.median(valid))
