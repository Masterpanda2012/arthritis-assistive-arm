from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from config import RuntimeConfig
from inputs.highgui import poll_key

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

from models import ActionRequest

LOGGER = logging.getLogger(__name__)

_PREVIEW_WINDOW = "Webcam Feed"

# BGR red + black outline (match gesture overlay).
_HUD_COLOR = (0, 0, 255)
_HUD_OUTLINE = (0, 0, 0)


def _drain_latest_hud(hud_queue: asyncio.Queue[str] | None, fallback: str) -> str:
    if hud_queue is None:
        return fallback
    latest = fallback
    try:
        while True:
            latest = hud_queue.get_nowait()
    except asyncio.QueueEmpty:
        pass
    return latest


def _draw_hud_line(frame: Any, line: str) -> None:
    if cv2 is None:
        return
    y = 32
    for dx, dy in ((2, 2), (1, 1)):
        cv2.putText(
            frame,
            line,
            (10 + dx, y + dy),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.85,
            _HUD_OUTLINE,
            4,
            cv2.LINE_AA,
        )
    cv2.putText(
        frame,
        line,
        (10, y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.85,
        _HUD_COLOR,
        2,
        cv2.LINE_AA,
    )


async def camera_preview_loop(
    config: RuntimeConfig,
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    raw_frame_queue: asyncio.Queue[Any],
    *,
    hud_queue: asyncio.Queue[str] | None = None,
    status_line_fn: Callable[[], str] | None = None,
) -> None:
    """Smooth mirror preview from raw frames; optional red ``Gesture: …`` line from ``hud_queue``."""
    if not config.show_camera_windows:
        return
    if cv2 is None:
        LOGGER.warning("Camera preview skipped: OpenCV is not installed.")
        return

    try:
        # WINDOW_AUTOSIZE keeps 1:1 pixels — resizing a NORMAL window stretches
        # the image and looks blurry on Retina displays.
        cv2.namedWindow(_PREVIEW_WINDOW, cv2.WINDOW_AUTOSIZE)
    except Exception as exc:  # pragma: no cover
        LOGGER.warning("Could not open camera preview window (%s).", exc)
        return

    LOGGER.info("Camera preview window '%s' ready (q or Esc to quit).", _PREVIEW_WINDOW)
    shown = 0
    default_hud = "Gesture: —"
    current_hud = default_hud
    try:
        while not stop_event.is_set():
            try:
                raw = await asyncio.wait_for(raw_frame_queue.get(), timeout=2.0)
            except asyncio.TimeoutError:
                LOGGER.debug("Camera preview: no raw frame yet (camera starting or permission denied).")
                continue

            if raw is None or not hasattr(raw, "size") or raw.size == 0:
                continue

            display = cv2.flip(raw, 1)
            if status_line_fn is not None:
                try:
                    status_full = status_line_fn()
                except Exception as exc:
                    status_full = f"status: {exc}"
                # Split long strips so the footer wraps into two lines
                # rather than getting chopped off screen.
                max_chars = max(60, min(140, int(display.shape[1] / 10)))
                lines: list[str] = []
                remaining = status_full
                while remaining:
                    if len(remaining) <= max_chars:
                        lines.append(remaining)
                        break
                    # Prefer wrapping on the " | " separator if possible.
                    slice_at = remaining.rfind("|", 0, max_chars)
                    if slice_at < int(max_chars * 0.4):
                        slice_at = max_chars
                    lines.append(remaining[:slice_at].rstrip())
                    remaining = remaining[slice_at:].lstrip(" |")
                    if len(lines) >= 2:
                        # Drop anything that wouldn't fit in 2 lines.
                        if len(remaining) > max_chars:
                            remaining = remaining[: max_chars - 1] + "…"
                        lines.append(remaining)
                        break
                base_y = display.shape[0] - 12
                for idx, line in enumerate(reversed(lines)):
                    y0 = base_y - idx * 22
                    for dx, dy in ((2, 2), (1, 1)):
                        cv2.putText(
                            display,
                            line,
                            (10 + dx, y0 + dy),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            (0, 0, 0),
                            3,
                            cv2.LINE_AA,
                        )
                    cv2.putText(
                        display,
                        line,
                        (10, y0),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.55,
                        (0, 255, 200),
                        1,
                        cv2.LINE_AA,
                    )
            if hud_queue is not None:
                current_hud = _drain_latest_hud(hud_queue, current_hud)
                main_hud, sep, rest = current_hud.partition("  |  ")
                _draw_hud_line(display, main_hud if sep else current_hud)
                if sep:
                    sub = rest
                    for dx, dy in ((2, 2), (1, 1)):
                        cv2.putText(
                            display,
                            sub,
                            (10 + dx, 67 + dy),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.75,
                            _HUD_OUTLINE,
                            4,
                            cv2.LINE_AA,
                        )
                    cv2.putText(
                        display,
                        sub,
                        (10, 67),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.75,
                        (0, 255, 0),
                        2,
                        cv2.LINE_AA,
                    )

            try:
                cv2.imshow(_PREVIEW_WINDOW, display)
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("Camera preview imshow failed (%s).", exc)
            if shown == 0:
                LOGGER.info(
                    "Camera preview showing frames (shape=%s).",
                    getattr(display, "shape", "?"),
                )
                shown += 1

            key = await poll_key(1)
            if key in (27, ord("q")):
                await action_queue.put(ActionRequest(source="camera_preview", intent="shutdown"))
                return
            await asyncio.sleep(0)
    finally:
        try:
            cv2.destroyWindow(_PREVIEW_WINDOW)
        except Exception:
            pass
