"""Cheap face-presence + mouth-motion detector used as voice VAD.

This module deliberately does **not** try to lip-read words. Its only
job is to give the voice pipeline a reliable "is the user on camera and
moving their mouth right now?" signal, which the voice loop uses to:

- suppress grammar results that probably came from ambient audio (no
  face in frame, or face visible but mouth hasn't moved recently), and
- extend the wake-word active-listen window while the user is still
  talking so their teach commands don't get cut off mid-sentence.

We only use OpenCV's bundled Haar cascade for face detection and a
frame-to-frame diff over the mouth ROI — no extra model downloads, no
MediaPipe face landmarker. Accuracy is coarse but that is all the
voice layer actually needs.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


LOGGER = logging.getLogger(__name__)


@dataclass
class LipActivity:
    """Shared VAD-ish state updated by :func:`lip_activity_loop`."""

    face_seen_at: float = 0.0
    mouth_active_at: float = 0.0
    mouth_level: float = 0.0
    face_in_frame: bool = False

    def has_face(self, window: float = 2.0) -> bool:
        return (time.monotonic() - self.face_seen_at) < window

    def is_speaking(self, window: float = 1.2) -> bool:
        return (time.monotonic() - self.mouth_active_at) < window


async def lip_activity_loop(
    frame_queue: asyncio.Queue[Any],
    stop_event: asyncio.Event,
    state: LipActivity,
    *,
    sample_interval_s: float = 0.1,
    mouth_activity_threshold: float = 1.8,
) -> None:
    """Read frames from ``frame_queue`` and update ``state`` in place."""
    if cv2 is None or np is None:
        LOGGER.info("Lip activity disabled (OpenCV/numpy missing).")
        return
    try:
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            LOGGER.info("Lip activity disabled (missing face cascade at %s).", cascade_path)
            return
    except Exception as exc:  # pragma: no cover
        LOGGER.info("Lip activity disabled (%s).", exc)
        return

    prev_mouth: Any = None
    last_sample = 0.0
    LOGGER.info("Lip-activity VAD started (Haar face + mouth-ROI frame diff).")

    try:
        while not stop_event.is_set():
            try:
                frame = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            now = time.monotonic()
            if now - last_sample < sample_interval_s:
                continue
            last_sample = now

            try:
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                # Downscale before face detection: Haar is O(pixels) and
                # we only need a rough bounding box for the mouth ROI.
                small = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
                faces = face_cascade.detectMultiScale(
                    small, scaleFactor=1.2, minNeighbors=4, minSize=(60, 60),
                )
            except Exception as exc:
                LOGGER.debug("Face detect error: %s", exc)
                continue

            if len(faces) == 0:
                state.face_in_frame = False
                prev_mouth = None
                continue

            fx, fy, fw, fh = max(faces, key=lambda r: r[2] * r[3])
            fx, fy, fw, fh = fx * 2, fy * 2, fw * 2, fh * 2  # undo 0.5× resize
            state.face_seen_at = now
            state.face_in_frame = True

            # Mouth ROI: centre 70 % of the lower 40 % of the face box.
            mx = max(0, int(fx + fw * 0.15))
            my = max(0, int(fy + fh * 0.60))
            mw = int(fw * 0.70)
            mh = int(fh * 0.35)
            mouth = gray[my : my + mh, mx : mx + mw]
            if mouth.size == 0:
                continue
            try:
                mouth_small = cv2.resize(mouth, (32, 16))
            except Exception:
                continue

            if prev_mouth is not None and prev_mouth.shape == mouth_small.shape:
                diff = cv2.absdiff(prev_mouth, mouth_small)
                level = float(np.mean(diff))
                # EMA smoothing: a single flicker (compression/lighting)
                # shouldn't spike the signal.
                state.mouth_level = state.mouth_level * 0.6 + level * 0.4
                if state.mouth_level >= mouth_activity_threshold:
                    state.mouth_active_at = now
            prev_mouth = mouth_small
    finally:
        LOGGER.info("Lip-activity VAD stopped.")
