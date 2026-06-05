"""Monocular depth estimation (Depth Anything V2) for zero-alignment targeting."""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)

_DEPTH_MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"
# Smaller inference size keeps CPU laptops responsive; output is upsampled
# back to the source frame so YOLO boxes still align.
_INFER_MAX_SIDE = 384


class DepthEstimator:
    """Lazy-loaded Depth Anything V2 Small — best speed/quality for CPU laptops."""

    def __init__(self, model_id: str = _DEPTH_MODEL_ID, *, enabled: bool = True) -> None:
        self.model_id = model_id
        self.enabled = enabled
        self._processor = None
        self._model = None
        self._device = "cpu"
        self._ready = False
        self._last_error = ""
        self._Image = None
        self.last_inference_ms: float = 0.0
        self.last_map_median_mm: float = -1.0
        self.inference_count: int = 0

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def last_error(self) -> str:
        return self._last_error

    def load(self) -> bool:
        if not self.enabled:
            return False
        if self._ready:
            return True
        try:
            import torch
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
            from PIL import Image
        except ImportError as exc:
            self._last_error = f"missing dependency: {exc}"
            LOGGER.warning("Depth estimation disabled (%s).", self._last_error)
            return False

        try:
            self._processor = AutoImageProcessor.from_pretrained(self.model_id)
            self._model = AutoModelForDepthEstimation.from_pretrained(self.model_id)
            self._device = "cuda" if torch.cuda.is_available() else "cpu"
            self._model.to(self._device)
            self._model.train(False)
            self._ready = True
            self._Image = Image
            LOGGER.info("Depth estimator ready (%s on %s).", self.model_id, self._device)
            return True
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Depth model load failed (%s). Vision will use LiDAR-only ranging.", exc)
            return False

    def _resize_for_infer(self, rgb: np.ndarray) -> tuple[np.ndarray, float, float]:
        """Return (resized_rgb, scale_x, scale_y) mapping infer → original pixels."""
        h, w = rgb.shape[:2]
        if max(h, w) <= _INFER_MAX_SIDE:
            return rgb, 1.0, 1.0
        if w >= h:
            nw = _INFER_MAX_SIDE
            nh = max(1, int(round(h * _INFER_MAX_SIDE / w)))
        else:
            nh = _INFER_MAX_SIDE
            nw = max(1, int(round(w * _INFER_MAX_SIDE / h)))
        try:
            import cv2

            small = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_AREA)
        except ImportError:
            from PIL import Image

            pil = Image.fromarray(rgb).resize((nw, nh), Image.Resampling.BILINEAR)
            small = np.asarray(pil)
        return small, w / float(nw), h / float(nh)

    def estimate_depth_mm(self, frame: Any, *, max_depth_mm: float = 1200.0) -> np.ndarray | None:
        """Return H×W depth map in millimeters (relative scale; fuse with LiDAR in vision)."""
        if not self.load():
            return None
        t0 = time.perf_counter()
        try:
            import torch

            rgb = frame
            if hasattr(frame, "shape") and len(frame.shape) == 3 and frame.shape[2] == 3:
                rgb = frame[:, :, ::-1]
            rgb = np.ascontiguousarray(rgb)
            orig_h, orig_w = rgb.shape[:2]
            small, sx, sy = self._resize_for_infer(rgb)
            pil = self._Image.fromarray(small)
            inputs = self._processor(images=pil, return_tensors="pt")
            inputs = {k: v.to(self._device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = self._model(**inputs)
                predicted = outputs.predicted_depth
            depth = torch.nn.functional.interpolate(
                predicted.unsqueeze(1),
                size=pil.size[::-1],
                mode="bicubic",
                align_corners=False,
            ).squeeze()
            depth_np = depth.cpu().numpy().astype(np.float32)
            d_min, d_max = float(depth_np.min()), float(depth_np.max())
            span = max(1e-6, d_max - d_min)
            normalized = (depth_np - d_min) / span
            inverted = 1.0 - normalized
            depth_mm = (120.0 + inverted * (max_depth_mm - 120.0)).astype(np.float32)

            if sx != 1.0 or sy != 1.0:
                try:
                    import cv2

                    depth_mm = cv2.resize(
                        depth_mm,
                        (orig_w, orig_h),
                        interpolation=cv2.INTER_LINEAR,
                    )
                except ImportError:
                    from PIL import Image

                    depth_mm = np.asarray(
                        Image.fromarray(depth_mm).resize((orig_w, orig_h), Image.Resampling.BILINEAR),
                        dtype=np.float32,
                    )

            valid = depth_mm[(depth_mm > 50) & (depth_mm < 5000)]
            self.last_map_median_mm = float(np.median(valid)) if valid.size else -1.0
            self.inference_count += 1
            self.last_inference_ms = (time.perf_counter() - t0) * 1000.0
            return depth_mm
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Depth inference failed: %s", exc)
            return None

    def status_summary(self) -> str:
        if not self.enabled:
            return "depth: off"
        if self._ready:
            tag = self.model_id.split("/")[-1]
            if self.last_map_median_mm > 0:
                return f"depth: {tag} (~{int(self.last_map_median_mm)}mm)"
            return f"depth: {tag}"
        if self._last_error:
            return f"depth: unavailable ({self._last_error[:40]})"
        return "depth: not loaded"

    def telemetry(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ready": self._ready,
            "summary": self.status_summary(),
            "last_error": self._last_error[:120] if self._last_error else "",
            "last_inference_ms": round(self.last_inference_ms, 1),
            "last_median_mm": int(self.last_map_median_mm) if self.last_map_median_mm > 0 else None,
            "inference_count": self.inference_count,
        }


def fuse_depth_with_lidar(
    depth_mm: float,
    lidar_mm: int,
    *,
    min_depth_mm: float = 80.0,
    max_depth_mm: float = 1500.0,
) -> float:
    """Scale monocular depth toward a live LiDAR reading when both are valid."""
    if depth_mm <= 0 or lidar_mm <= 0:
        return depth_mm
    fused = depth_mm * (float(lidar_mm) / max(depth_mm, 1.0))
    return float(max(min_depth_mm, min(max_depth_mm, fused)))
