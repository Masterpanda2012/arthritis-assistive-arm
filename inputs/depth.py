"""Monocular depth estimation (Depth Anything V2) for zero-alignment targeting."""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)

_DEPTH_MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"


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

    def estimate_depth_mm(self, frame: Any, *, max_depth_mm: float = 1200.0) -> np.ndarray | None:
        """Return H×W depth map in millimeters (relative scale calibrated heuristically)."""
        if not self.load():
            return None
        try:
            import torch

            rgb = frame
            if hasattr(frame, "shape") and len(frame.shape) == 3 and frame.shape[2] == 3:
                rgb = frame[:, :, ::-1]
            pil = self._Image.fromarray(np.ascontiguousarray(rgb))
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
            return depth_mm
        except Exception as exc:
            self._last_error = str(exc)
            LOGGER.warning("Depth inference failed: %s", exc)
            return None

    def status_summary(self) -> str:
        if not self.enabled:
            return "depth: off"
        if self._ready:
            return f"depth: {self.model_id.split('/')[-1]}"
        if self._last_error:
            return f"depth: unavailable ({self._last_error[:40]})"
        return "depth: not loaded"
