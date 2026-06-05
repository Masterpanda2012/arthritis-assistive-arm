"""Shared webcam feed with macOS-friendly enumeration + freeze recovery.

Two user-visible problems motivated this rewrite:

1. On macOS the default ``cv2.VideoCapture(0)`` often grabs the iPhone
   *Continuity Camera* if a phone is nearby, which is almost never what
   the user wants. We now enumerate cameras via ``system_profiler``
   (``SPCameraDataType``) and skip anything whose name looks like a
   phone/tablet. The user can override with ``ROBOT_ARM_CAMERA_INDEX`` or
   ``--camera-index`` to force a specific index, or ``ROBOT_ARM_CAMERA_NAME``
   to prefer a camera by name.
2. When the iPhone disconnects mid-session the old capture handle keeps
   returning ``ret=False`` forever and every downstream consumer
   (preview, gesture, vision) freezes. We now count consecutive failed
   reads and automatically release + re-open the capture on a fresh
   device.
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None


LOGGER = logging.getLogger(__name__)

# Anything whose camera name matches this is almost certainly a phone
# or tablet acting as a webcam via Continuity / AirPlay.
_PHONE_NAME_PATTERN = re.compile(
    r"\b(iphone|ipad|phone|continuity|airplay|android|pixel|galaxy)\b",
    re.IGNORECASE,
)

# Macro-ish defaults: if the camera stops producing frames for this long
# while we think it is open, assume it silently died (e.g. Continuity
# unplug) and try to re-open.
_FREEZE_SECONDS = 2.5
_READ_FAIL_BEFORE_REOPEN = 45

# We explicitly cap capture resolution / FPS to keep the pipeline
# responsive. Full-HD frames are overkill for YOLOv8-n / MediaPipe hand
# tracking and cause the whole event loop to stall because
# ``cap.read()`` on 1080p takes ~15–30 ms per call. 720p@30fps gives us
# plenty of accuracy with ~4× lower CPU and bandwidth.
_DEFAULT_CAPTURE_WIDTH = 1280
_DEFAULT_CAPTURE_HEIGHT = 720
_DEFAULT_CAPTURE_FPS = 30
# Web MJPEG: 960×540 keeps the feed sharp in the browser without overloading USB.
_WEB_STREAM_WIDTH = 960
_WEB_STREAM_HEIGHT = 540
_WEB_JPEG_QUALITY = 88
# Target publish interval (seconds). Reading the camera faster than
# this wastes CPU; the display loop can't do more than ~30 fps anyway.
_PUBLISH_INTERVAL_S = 1.0 / 30.0


@dataclass(frozen=True)
class CameraInfo:
    name: str
    is_phone: bool


def list_macos_cameras() -> list[CameraInfo]:
    """Parse ``system_profiler SPCameraDataType`` for connected camera names.

    Returns an empty list if the tool is unavailable or the platform
    is not macOS. Ordering mirrors system_profiler's output which is a
    reasonable proxy for AVFoundation's device ordering.
    """
    if sys.platform != "darwin":
        return []
    try:
        proc = subprocess.run(
            ["system_profiler", "SPCameraDataType"],
            capture_output=True,
            text=True,
            timeout=6.0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []

    cameras: list[CameraInfo] = []
    for raw_line in proc.stdout.splitlines():
        # Device headings in system_profiler are indented with four
        # spaces and end with a colon, e.g. "    FaceTime HD Camera:".
        if not raw_line.startswith("    ") or raw_line.startswith("      "):
            continue
        line = raw_line.strip()
        if not line.endswith(":"):
            continue
        name = line[:-1].strip()
        if not name or name.lower() == "camera":
            continue
        cameras.append(CameraInfo(name=name, is_phone=bool(_PHONE_NAME_PATTERN.search(name))))
    return cameras


def pretty_camera_listing() -> str:
    """Human-friendly summary for ``--list-cameras``."""
    cams = list_macos_cameras()
    if not cams:
        return (
            "No cameras reported by system_profiler (or not macOS). OpenCV will still\n"
            "try indices 0..3 at runtime. Pass --camera-index N to pick one explicitly."
        )
    lines = ["Cameras seen by macOS (order ~ AVFoundation index):"]
    for idx, cam in enumerate(cams):
        flag = "  [phone-like, skipped by default]" if cam.is_phone else ""
        lines.append(f"  {idx}: {cam.name}{flag}")
    lines.append(
        "\nTip: if your iPhone Continuity Camera is hijacking index 0, either unplug the\n"
        "phone, turn off Continuity Camera in System Settings → General → AirPlay &\n"
        "Handoff, or run with --camera-index <n> pointing at your real webcam."
    )
    return "\n".join(lines)


def _preferred_indices(
    requested_index: int,
    prefer_name_substr: str | None,
) -> list[int]:
    """Produce the order in which we will probe camera indices."""
    tried: list[int] = []

    def _add(idx: int) -> None:
        if 0 <= idx < 8 and idx not in tried:
            tried.append(idx)

    cams = list_macos_cameras()
    phone_indices = {i for i, c in enumerate(cams) if c.is_phone}

    if prefer_name_substr:
        needle = prefer_name_substr.lower()
        for i, cam in enumerate(cams):
            if needle in cam.name.lower():
                _add(i)

    # Honour the caller's explicit request first unless it's a phone.
    if requested_index not in phone_indices:
        _add(requested_index)

    # Then every non-phone camera, in order.
    for i, _ in enumerate(cams):
        if i not in phone_indices:
            _add(i)

    # Finally, phone cameras (last resort) and generic 0..3.
    for i in phone_indices:
        _add(i)
    for i in range(4):
        _add(i)

    return tried


def _open_capture(index: int) -> Any | None:
    """Try default backend first, then AVFoundation on macOS."""
    if cv2 is None:
        return None

    attempts: list[tuple[int | None, str]] = [(None, "default")]
    if sys.platform == "darwin":
        attempts.append((cv2.CAP_AVFOUNDATION, "AVFoundation"))

    for api, label in attempts:
        cap = cv2.VideoCapture(index) if api is None else cv2.VideoCapture(index, api)
        if cap is not None and cap.isOpened():
            # Small internal buffer → always get the freshest frame.
            for prop, value in (
                (cv2.CAP_PROP_BUFFERSIZE, 1),
                (cv2.CAP_PROP_FRAME_WIDTH, _DEFAULT_CAPTURE_WIDTH),
                (cv2.CAP_PROP_FRAME_HEIGHT, _DEFAULT_CAPTURE_HEIGHT),
                (cv2.CAP_PROP_FPS, _DEFAULT_CAPTURE_FPS),
            ):
                try:
                    cap.set(prop, value)
                except Exception:
                    pass
            actual_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            actual_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            actual_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
            LOGGER.info(
                "Opened webcam index %s via %s (%dx%d @ %.0f fps requested).",
                index, label, actual_w, actual_h, actual_fps,
            )
            return cap
        if cap is not None:
            cap.release()
    return None


def _warmup(capture: Any, deadline_s: float = 3.5) -> Any | None:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        ret, frame = capture.read()
        if ret and frame is not None and getattr(frame, "size", 0):
            return frame
        time.sleep(0.03)
    return None


class SharedCamera:
    """Async webcam publisher shared by preview, gesture, and vision.

    Resilient to Continuity-Camera disconnect freezes: if the current
    capture stops producing frames it is released and a fresh capture
    is opened from the preferred-index list.
    """

    def __init__(
        self,
        device_index: int = 0,
        *,
        prefer_name_substr: str | None = None,
    ) -> None:
        self.device_index = device_index
        self.prefer_name_substr = prefer_name_substr
        self._subscribers: dict[str, asyncio.Queue[Any]] = {}
        self._active_index: int | None = None
        self._active_name: str = ""
        self.last_frame_at: float = 0.0
        self._jpeg_lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._stream_size: tuple[int, int] = (_WEB_STREAM_WIDTH, _WEB_STREAM_HEIGHT)

    @property
    def active_index(self) -> int | None:
        return self._active_index

    @property
    def active_name(self) -> str:
        return self._active_name

    def subscribe(self, name: str) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1)
        self._subscribers[name] = queue
        return queue

    def latest_jpeg(self) -> bytes | None:
        with self._jpeg_lock:
            return self._latest_jpeg

    def _encode_web_jpeg(self, frame: Any) -> bytes | None:
        if cv2 is None:
            return None
        try:
            h, w = frame.shape[:2]
            tw, th = self._stream_size
            if w != tw or h != th:
                frame = cv2.resize(frame, (tw, th), interpolation=cv2.INTER_AREA)
            ok, buf = cv2.imencode(
                ".jpg",
                frame,
                [int(cv2.IMWRITE_JPEG_QUALITY), _WEB_JPEG_QUALITY],
            )
            if not ok:
                return None
            return buf.tobytes()
        except Exception:
            return None

    def _publish_frame(self, frame: Any) -> None:
        jpeg = self._encode_web_jpeg(frame)
        if jpeg is not None:
            with self._jpeg_lock:
                self._latest_jpeg = jpeg
        for queue in self._subscribers.values():
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(frame.copy())

    def _open_preferred(self) -> tuple[Any | None, int | None, str]:
        cams = list_macos_cameras()
        for index in _preferred_indices(self.device_index, self.prefer_name_substr):
            name = cams[index].name if 0 <= index < len(cams) else f"index {index}"
            is_phone = 0 <= index < len(cams) and cams[index].is_phone
            if is_phone and index != self.device_index:
                LOGGER.debug("Skipping phone-like camera at index %s (%s).", index, name)
                continue
            capture = _open_capture(index)
            if capture is None:
                continue
            frame = _warmup(capture)
            if frame is None:
                LOGGER.warning(
                    "Webcam index %s (%s) opened but produced no frames during warmup.",
                    index, name,
                )
                capture.release()
                continue
            self._publish_frame(frame)
            return capture, index, name
        return None, None, ""

    async def run(self, stop_event: asyncio.Event) -> None:
        if cv2 is None:
            LOGGER.warning("OpenCV is not installed; shared camera feed is disabled.")
            return

        capture, index, name = self._open_preferred()
        if capture is None:
            LOGGER.warning(
                "Could not open any webcam. If this is macOS, grant Terminal/Cursor camera "
                "access in System Settings → Privacy & Security → Camera, or pass "
                "--camera-index N explicitly. Run `python main.py --list-cameras` to see options."
            )
            return

        self._active_index = index
        self._active_name = name
        LOGGER.info("Shared camera feed started on index %s (%s).", index, name)

        read_failures = 0
        last_frame_at = time.monotonic()
        last_publish = 0.0

        try:
            while not stop_event.is_set():
                # ``cap.read()`` is blocking (~15 ms at 720p, ~30 ms at
                # 1080p). Running it inline on the event loop stalls
                # *every* other task — voice, gesture, panel updates —
                # and was the main source of "laggy camera" complaints.
                # Offloading to a worker thread lets asyncio stay
                # responsive while OpenCV blocks.
                ret, frame = await asyncio.to_thread(capture.read)
                now = time.monotonic()
                if not ret or frame is None or not getattr(frame, "size", 0):
                    read_failures += 1
                    stale = now - last_frame_at > _FREEZE_SECONDS
                    if read_failures >= _READ_FAIL_BEFORE_REOPEN or stale:
                        LOGGER.warning(
                            "Webcam %s (%s) appears frozen (fails=%d, stale=%.1fs). Re-opening.",
                            index, name, read_failures, now - last_frame_at,
                        )
                        try:
                            capture.release()
                        except Exception:
                            pass
                        capture, index, name = self._open_preferred()
                        if capture is None:
                            LOGGER.warning("Reopen failed; sleeping briefly before retry.")
                            await asyncio.sleep(1.0)
                            capture, index, name = self._open_preferred()
                        if capture is None:
                            LOGGER.error("No camera available after reopen attempts; stopping feed.")
                            return
                        self._active_index = index
                        self._active_name = name
                        LOGGER.info("Webcam re-opened on index %s (%s).", index, name)
                        read_failures = 0
                        last_frame_at = time.monotonic()
                        continue
                    await asyncio.sleep(0.02)
                    continue

                read_failures = 0
                last_frame_at = now
                self.last_frame_at = now
                # Cap publish rate so downstream tasks (YOLO, MediaPipe)
                # aren't starved processing stale frames back-to-back.
                if now - last_publish >= _PUBLISH_INTERVAL_S:
                    self._publish_frame(frame)
                    last_publish = now
                # Tiny yield lets other tasks run between frames.
                await asyncio.sleep(0)
        finally:
            try:
                capture.release()
            except Exception:
                pass
            LOGGER.info("Shared camera feed stopped.")
