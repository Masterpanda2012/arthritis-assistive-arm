"""MediaPipe-Tasks hand-gesture input.

Older versions of MediaPipe exposed ``mp.solutions.hands.Hands`` but the
current ``mediapipe >= 0.10.15`` wheels (which are the ones that work
with numpy 2.x on Apple Silicon) only ship the new *Tasks* API. The
classic ``solutions`` tree is gone, which is why gesture input was
silently failing before — ``mp_hands = mp.solutions.hands`` raised
``AttributeError`` inside the background task and the whole gesture
loop died without ever logging "Gesture input started".

This module now uses ``mediapipe.tasks.python.vision.HandLandmarker``
under the hood, with a compatibility shim that re-exposes the same
21-landmark layout (``landmarks[i].x/y/z``) so our legacy helpers
(``_is_thumbs_up``, ``_count_fingers``, etc.) keep working unchanged.

The model file (``hand_landmarker.task``) is expected at
``models/hand_landmarker.task`` next to the project root. It's a ~8 MB
file published by Google; the app will auto-download it on first run
if it's missing.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import urllib.request
from collections import Counter, deque
from math import hypot
from pathlib import Path
from typing import Any, Callable, Sequence

from ai.gesture_bindings import GestureBindings
from ai.custom_gestures import CustomGestureCatalog, landmarks_to_vector
from ai.gesture_diversity import GestureDiversityTracker
from config import RuntimeConfig
from inputs.highgui import poll_key
from models import ActionRequest

try:
    import cv2
except ImportError:  # pragma: no cover
    cv2 = None

try:
    import mediapipe as mp
    from mediapipe.tasks.python import vision as _mp_vision
    _MP_TASKS_OK = True
except Exception:  # pragma: no cover - optional dep / version skew
    mp = None
    _mp_vision = None
    _MP_TASKS_OK = False

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None


LOGGER = logging.getLogger(__name__)

# BGR: red primary label (OpenCV uses BGR).
_GESTURE_TEXT_COLOR = (0, 0, 255)
_GESTURE_OUTLINE_COLOR = (0, 0, 0)

_HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/hand_landmarker/"
    "hand_landmarker/float16/1/hand_landmarker.task"
)
_HAND_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "hand_landmarker.task"


# MediaPipe hand connection tuples (21-landmark topology), duplicated
# here so we no longer depend on ``mp.solutions.hands.HAND_CONNECTIONS``.
_HAND_CONNECTIONS: tuple[tuple[int, int], ...] = (
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
)


def _ensure_hand_model(path: Path = _HAND_MODEL_PATH) -> Path | None:
    """Make sure the HandLandmarker task file exists locally, downloading it once if needed."""
    if path.exists() and path.stat().st_size > 1000:
        return path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        LOGGER.info("Downloading MediaPipe hand_landmarker.task (~8 MB) to %s …", path)
        urllib.request.urlretrieve(_HAND_MODEL_URL, str(path))
        return path
    except Exception as exc:
        LOGGER.warning(
            "Could not download MediaPipe hand model from %s (%s). "
            "Gesture input will be disabled.", _HAND_MODEL_URL, exc,
        )
        return None


def _hud_vote_smoothed(samples: deque[str | None], min_votes: int) -> str:
    valid = [x for x in samples if x is not None and x != "—"]
    if not valid:
        return "—"
    best, n = Counter(valid).most_common(1)[0]
    need = max(1, min(min_votes, (len(valid) + 1) // 2))
    if n >= need:
        return best
    return "—"


def _draw_gesture_label(frame: Any, text: str, y: int = 32) -> None:
    if cv2 is None:
        return
    for dx, dy in ((2, 2), (1, 1)):
        cv2.putText(
            frame, text, (10 + dx, y + dy), cv2.FONT_HERSHEY_SIMPLEX,
            0.85, _GESTURE_OUTLINE_COLOR, 4, cv2.LINE_AA,
        )
    cv2.putText(
        frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
        0.85, _GESTURE_TEXT_COLOR, 2, cv2.LINE_AA,
    )


def _draw_landmarks(frame: Any, landmarks: Sequence[Any]) -> None:
    """Minimal replacement for ``mp.solutions.drawing_utils.draw_landmarks``."""
    if cv2 is None:
        return
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in _HAND_CONNECTIONS:
        if 0 <= a < len(pts) and 0 <= b < len(pts):
            cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2, cv2.LINE_AA)
    for x, y in pts:
        cv2.circle(frame, (x, y), 4, (0, 0, 255), -1, cv2.LINE_AA)


def _make_landmarker() -> Any | None:
    """Initialise a MediaPipe Tasks HandLandmarker in VIDEO mode."""
    if not _MP_TASKS_OK or mp is None or _mp_vision is None:
        LOGGER.warning(
            "MediaPipe Tasks API is unavailable (mediapipe=%r). Gesture input disabled.",
            getattr(mp, "__version__", None),
        )
        return None

    model_path = _ensure_hand_model()
    if model_path is None:
        return None

    try:
        options = _mp_vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
            running_mode=_mp_vision.RunningMode.VIDEO,
            num_hands=1,
            min_hand_detection_confidence=0.42,
            min_tracking_confidence=0.45,
            min_hand_presence_confidence=0.42,
        )
        return _mp_vision.HandLandmarker.create_from_options(options)
    except Exception as exc:
        LOGGER.warning("Failed to initialize HandLandmarker (%s). Gesture input disabled.", exc)
        return None


async def gesture_loop(
    config: RuntimeConfig,
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    frame_queue: asyncio.Queue[Any],
    *,
    hud_queue: asyncio.Queue[str] | None = None,
    bindings: GestureBindings | None = None,
    enabled_fn: Callable[[], bool] | None = None,
    custom_catalog: CustomGestureCatalog | None = None,
    diversity: GestureDiversityTracker | None = None,
    capture_fn: Callable[[list[float]], None] | None = None,
) -> None:
    if cv2 is None or not _MP_TASKS_OK or np is None:
        missing = []
        if cv2 is None:
            missing.append("opencv-python")
        if not _MP_TASKS_OK:
            missing.append("mediapipe (tasks API)")
        if np is None:
            missing.append("numpy")
        LOGGER.warning("Gesture input disabled; missing/broken: %s.", ", ".join(missing) or "unknown")
        return

    landmarker = await asyncio.to_thread(_make_landmarker)
    if landmarker is None:
        return

    process_lock = asyncio.Lock()

    gesture_buffer: deque[str] = deque(maxlen=config.gesture_buffer_len)
    custom_buffer: deque[str] = deque(maxlen=5)
    last_sent: str | None = None
    thumbs_start: float | None = None
    last_action_at = 0.0

    hud_samples: deque[str | None] = deque(maxlen=config.gesture_hud_smooth_len)
    no_hand_streak = 0

    feeds_hud = hud_queue is not None
    show_local_window = bool(config.gesture_show_preview and not feeds_hud)
    if feeds_hud:
        LOGGER.info("Gesture HUD lines feed the Webcam preview (mirror + red label).")
    elif show_local_window:
        LOGGER.info("Gesture input will render to OpenCV window 'Gesture Control'.")
    else:
        LOGGER.info("Gesture input started (headless; no OpenCV window).")

    def _push_hud(line: str) -> None:
        if hud_queue is None:
            return
        try:
            while True:
                hud_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            hud_queue.put_nowait(line)
        except Exception as exc:  # pragma: no cover
            LOGGER.debug("Gesture HUD queue update failed: %s", exc)

    loop_started_at = time.monotonic()
    frames_seen = 0
    hands_seen = 0

    try:
        while not stop_event.is_set():
            if enabled_fn is not None and not enabled_fn():
                thumbs_start = None
                gesture_buffer.clear()
                await asyncio.sleep(0.15)
                continue
            try:
                frame = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            frame = cv2.flip(frame, 1)
            frames_seen += 1

            try:
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
                ts_ms = int((time.monotonic() - loop_started_at) * 1000)
                async with process_lock:
                    result = await asyncio.to_thread(landmarker.detect_for_video, mp_image, ts_ms)
            except Exception as exc:  # pragma: no cover
                LOGGER.debug("HandLandmarker detect failed: %s", exc)
                result = None

            label = None
            countdown_text = ""
            hud_token: str | None = None

            draw_overlays = feeds_hud or config.gesture_show_preview

            hand_landmarks_list = getattr(result, "hand_landmarks", None) if result is not None else None
            if hand_landmarks_list:
                no_hand_streak = 0
                hands_seen += 1
                landmarks = hand_landmarks_list[0]
                if draw_overlays and (show_local_window or (config.gesture_show_preview and not feeds_hud)):
                    _draw_landmarks(frame, landmarks)

                quit_mode = getattr(config, "quit_gesture", "peace_hold") or "peace_hold"
                quit_hold = max(1.5, float(config.thumbs_up_hold_time))
                quit_candidate = _detect_quit_gesture(landmarks, mode=quit_mode)

                if capture_fn is not None:
                    try:
                        capture_fn(landmarks_to_vector(landmarks))
                    except Exception as exc:
                        LOGGER.debug("Gesture capture sample failed: %s", exc)

                if quit_candidate:
                    if thumbs_start is None:
                        thumbs_start = time.monotonic()
                    elapsed = time.monotonic() - thumbs_start
                    if elapsed >= quit_hold:
                        label = "shutdown"
                        hud_token = "QUIT"
                    else:
                        countdown_text = f"Hold to quit {quit_hold - elapsed:.1f}s"
                        hud_token = "quit (hold)"
                    # Do not classify motion gestures while counting down to quit.
                    gesture_buffer.clear()
                else:
                    thumbs_start = None
                    custom_hit = custom_catalog.match(landmarks) if custom_catalog is not None else None
                    if custom_hit is not None:
                        custom_buffer.append(custom_hit.gesture_id)
                        need = max(2, config.gesture_stable_requirement - 1)
                        votes = sum(1 for g in custom_buffer if g == custom_hit.gesture_id)
                        if votes >= need:
                            gesture_buffer.append(f"custom:{custom_hit.gesture_id}")
                            hud_token = custom_hit.display_name
                        else:
                            hud_token = f"{custom_hit.display_name}…"
                    elif _is_one_gesture(landmarks):
                        gesture_buffer.append("one")
                        hud_token = "one"
                    else:
                        raw_observed = _gesture_label_baseline(landmarks)
                        if raw_observed is not None:
                            gesture_buffer.append(raw_observed)
                        hud_token = raw_observed
            else:
                no_hand_streak += 1
                thumbs_start = None
                hud_token = None
                if no_hand_streak >= 3:
                    gesture_buffer.clear()
                    custom_buffer.clear()
                    hud_samples.clear()

            hud_samples.append(hud_token)

            smoothed = _hud_vote_smoothed(hud_samples, config.gesture_hud_min_votes)
            hud_line = f"Gesture: {smoothed}"

            if label == "shutdown":
                # Thumbs-up held → quit the whole program. This is the
                # only gesture the user cannot rebind so there is always
                # a guaranteed escape hatch from any re-learned mapping.
                await action_queue.put(ActionRequest(source="gesture", intent="shutdown"))
                LOGGER.info("Gesture: thumbs-up held → shutdown")
                gesture_buffer.clear()
                last_sent = "shutdown"
                last_action_at = time.monotonic()
                return
            elif gesture_buffer:
                now = time.monotonic()
                if now - last_action_at >= config.gesture_action_cooldown_s:
                    chosen, votes = Counter(gesture_buffer).most_common(1)[0]
                    if votes >= config.gesture_stable_requirement and chosen != last_sent:
                        action = _action_for_label(
                            chosen,
                            bindings=bindings,
                            simple_mode=config.simple_gesture_mode,
                            custom_catalog=custom_catalog,
                        )
                        if action is not None:
                            if diversity is not None and diversity.should_block(action.intent):
                                diversity.blocked_count += 1
                                hud_token = "cycle moves"
                                LOGGER.debug(
                                    "Gesture diversity blocked %s (%s)",
                                    action.intent,
                                    diversity._hint(),
                                )
                            else:
                                LOGGER.info("Gesture: %s (votes=%d) → %s", chosen, votes, action.intent)
                                await action_queue.put(action)
                                if diversity is not None:
                                    diversity.record(action.intent)
                                last_sent = chosen
                                last_action_at = now
                                gesture_buffer.clear()

            paint_frame = show_local_window or (config.gesture_show_preview and not feeds_hud)
            if paint_frame:
                _draw_gesture_label(frame, hud_line, y=32)
                if countdown_text:
                    for dx, dy in ((2, 2), (1, 1)):
                        cv2.putText(
                            frame, countdown_text, (10 + dx, 67 + dy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4, cv2.LINE_AA,
                        )
                    cv2.putText(
                        frame, countdown_text, (10, 67),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 0), 2, cv2.LINE_AA,
                    )

            if feeds_hud:
                preview_hud = hud_line
                if countdown_text:
                    preview_hud = f"{hud_line}  |  {countdown_text}"
                _push_hud(preview_hud)

            if show_local_window:
                try:
                    cv2.imshow("Gesture Control", frame)
                except Exception as exc:  # pragma: no cover
                    LOGGER.warning("Gesture preview imshow failed (%s).", exc)
                key = await poll_key(1)
                if key in (27, ord("q")):
                    await action_queue.put(ActionRequest(source="gesture", intent="shutdown"))
                    return

            # Periodic diag so users can see whether hands are being detected at all.
            if frames_seen % 150 == 0:
                LOGGER.debug(
                    "Gesture heartbeat: frames=%d hands=%d last_token=%s",
                    frames_seen, hands_seen, hud_token,
                )

            await asyncio.sleep(0)
    finally:
        try:
            landmarker.close()
        except Exception:
            pass
        if show_local_window and cv2 is not None:
            try:
                cv2.destroyWindow("Gesture Control")
            except Exception:
                pass


def _action_for_label(
    label: str,
    *,
    bindings: GestureBindings | None = None,
    simple_mode: bool = False,
    custom_catalog: CustomGestureCatalog | None = None,
) -> ActionRequest | None:
    """Resolve a gesture label to an ActionRequest using user bindings first."""
    if label.startswith("custom:") and custom_catalog is not None:
        gesture_id = label.split(":", 1)[1]
        item = custom_catalog.get(gesture_id)
        if item is None:
            return None
        return ActionRequest(
            source="gesture",
            intent=item.intent,
            payload=dict(item.payload),
            requires_confirmation=item.intent in {"pick_object", "place_object"},
        )
    if simple_mode:
        simple = {
            "open": ("confirm_yes", {}),
            "fist": ("confirm_no", {}),
            "rock": ("emergency_stop", {}),
            "four": ("home", {}),
        }
        hit = simple.get(label)
        if hit is None:
            return None
        intent, payload = hit
        return ActionRequest(source="gesture", intent=intent, payload=dict(payload))
    if bindings is not None:
        return bindings.action_for(label, source="gesture")
    # Fallback defaults if no binding store was provided (e.g. legacy
    # callers or unit tests).
    fallback = {
        "open": ("open_claw", {}),
        "fist": ("close_claw", {}),
        "tilt_left": ("base_left", {}),
        "tilt_right": ("base_right", {}),
        "one": ("lift_up", {}),
        "two": ("lift_down", {}),
        "three": ("rotate_left", {}),
        "four": ("preset_pose", {"name": "inspect"}),
        "pinch": ("rotate_right", {}),
        "rock": ("base_left", {}),
        "call_me": ("preset_pose", {"name": "pickup_ready"}),
        "point": ("preset_pose", {"name": "drop_ready"}),
        "thumbs_up": ("confirm_yes", {}),
        "thumbs_down": ("lift_down", {}),
        "stop_palm": ("emergency_stop", {}),
        "high_five": ("preset_pose", {"name": "survey"}),
        "spread": ("preset_pose", {"name": "survey"}),
        "spider": ("base_right", {}),
        "ok_circle": ("confirm_yes", {}),
        "palm_down": ("home", {}),
    }
    hit = fallback.get(label)
    if hit is None:
        return None
    intent, payload = hit
    return ActionRequest(source="gesture", intent=intent, payload=dict(payload))


def _gesture_label_baseline(landmarks) -> str | None:
    if _is_fist(landmarks):
        return "fist"

    states = _finger_states(landmarks)  # (index, middle, ring, pinky)
    thumb_out = _thumb_extended(landmarks)
    thumb_in = _thumb_over_palm(landmarks)

    if _is_one_gesture(landmarks):
        return "one"

    if _is_ok_circle(landmarks, states):
        return "ok_circle"
    if _is_thumbs_up_strict(landmarks):
        return "thumbs_up"
    if _is_thumbs_down(landmarks, states):
        return "thumbs_down"
    if _is_palm_down(landmarks, states, thumb_out):
        return "palm_down"
    if _is_spider(landmarks, states):
        return "spider"
    if _is_high_five(landmarks, states, thumb_out):
        return "high_five"
    if _is_stop_palm(landmarks, states, thumb_out):
        return "stop_palm"
    if _is_spread(landmarks, states, thumb_out):
        return "spread"
    if _is_pinch(landmarks, states):
        return "pinch"
    if _is_point_l(landmarks, states, thumb_out):
        return "point"
    if states == (True, False, False, True):
        return "rock"
    if states == (False, False, False, True) and thumb_out:
        return "call_me"

    fingers = sum(states)
    tilt = _wrist_tilt(landmarks, states, thumb_out)
    if fingers >= 4 and not thumb_in:
        return tilt or "open"
    if fingers == 4:
        return "four"
    if fingers == 3:
        return "three"
    if fingers == 2:
        return "two"
    return None


def _detect_quit_gesture(landmarks, *, mode: str) -> bool:
    """Return True when the user is holding the configured quit pose."""
    states = _finger_states(landmarks)
    if mode == "peace_hold" or mode == "peace":
        return _is_peace(landmarks, states)
    # Legacy thumbs_up quit — discouraged; peace sign is the default quit.
    if _is_one_gesture(landmarks):
        return False
    return _is_thumbs_up_strict(landmarks)


def _is_one_gesture(landmarks) -> bool:
    """Exactly one finger up (index), thumb tucked — not thumbs-up."""
    states = _finger_states(landmarks)
    if states != (True, False, False, False):
        return False
    if _thumb_extended(landmarks):
        return False
    scale = _hand_scale(landmarks)
    return _finger_extended(landmarks, 8, 6, 5, scale)


def _is_peace(landmarks, states: tuple[bool, bool, bool, bool]) -> bool:
    """V-sign: index + middle extended, ring + pinky folded."""
    if states != (True, True, False, False):
        return False
    if _thumb_extended(landmarks):
        return False
    scale = _hand_scale(landmarks)
    spread = _dist(landmarks[8], landmarks[12])
    return spread > 0.22 * scale


def _is_thumbs_down(landmarks, states: tuple[bool, bool, bool, bool]) -> bool:
    """Thumb pointing down, other fingers curled."""
    if any(states):
        return False
    scale = _hand_scale(landmarks)
    thumb_tip_y = landmarks[4].y
    thumb_down = (
        thumb_tip_y > landmarks[3].y + 0.08 * scale
        and thumb_tip_y > landmarks[2].y + 0.08 * scale
    )
    folded = sum(
        1 for tip, pip, mcp in zip(_FINGER_TIPS, _FINGER_PIPS, _FINGER_MCPS)
        if _finger_folded(landmarks, tip, pip, mcp, scale)
    )
    return thumb_down and folded >= 3 and _thumb_extended(landmarks)


def _is_stop_palm(landmarks, states: tuple[bool, bool, bool, bool], thumb_out: bool) -> bool:
    """Open palm held toward camera — all fingers extended."""
    if not all(states) or not thumb_out:
        return False
    angle = abs(_palm_angle_deg(landmarks))
    return angle < 25.0


def _is_spider(landmarks, states: tuple[bool, bool, bool, bool]) -> bool:
    """Index, middle, ring extended; pinky folded — like a spider walk."""
    if states != (True, True, True, False):
        return False
    if _thumb_extended(landmarks):
        return False
    scale = _hand_scale(landmarks)
    return _finger_extended(landmarks, 16, 14, 13, scale)


def _is_high_five(landmarks, states: tuple[bool, bool, bool, bool], thumb_out: bool) -> bool:
    """All digits extended, palm facing camera."""
    if not all(states) or not thumb_out:
        return False
    angle = abs(_palm_angle_deg(landmarks))
    return 15.0 <= angle <= 40.0


def _is_spread(landmarks, states: tuple[bool, bool, bool, bool], thumb_out: bool) -> bool:
    """Wide open hand — fingers spread farther than a flat open palm."""
    if sum(states) < 4 or not thumb_out:
        return False
    scale = _hand_scale(landmarks)
    gaps = (
        _dist(landmarks[8], landmarks[12]),
        _dist(landmarks[12], landmarks[16]),
        _dist(landmarks[16], landmarks[20]),
    )
    return min(gaps) > 0.28 * scale


# --- Finger-state helpers ---------------------------------------------
#
# We used to check "tip above PIP on the y-axis" which meant every rule
# silently broke the moment the user tilted their hand. We now measure
# extension by *distance from the wrist*: a straight finger has its tip
# farther from the wrist than its PIP/MCP joints; a curled finger tucks
# the tip in closer than the MCP. That comparison is rotation-invariant
# and fixes the "tilt is mis-read as four / three" problem the operator
# hit in practice.


_FINGER_TIPS = (8, 12, 16, 20)
_FINGER_PIPS = (6, 10, 14, 18)
_FINGER_MCPS = (5, 9, 13, 17)


def _dist(a, b) -> float:
    return hypot(a.x - b.x, a.y - b.y)


def _hand_scale(landmarks) -> float:
    """Rough "palm size" for adaptive thresholds."""
    return max(_dist(landmarks[0], landmarks[9]), 1e-3)


def _finger_extended(landmarks, tip: int, pip: int, mcp: int, scale: float) -> bool:
    """True if the finger is held up (straight), regardless of hand tilt."""
    tip_d = _dist(landmarks[0], landmarks[tip])
    pip_d = _dist(landmarks[0], landmarks[pip])
    mcp_d = _dist(landmarks[0], landmarks[mcp])
    margin = 0.15 * scale
    return tip_d > pip_d + margin and tip_d > mcp_d + margin


def _finger_folded(landmarks, tip: int, pip: int, mcp: int, scale: float) -> bool:
    """True if the finger is curled in toward the palm — tip no farther
    from the wrist than the MCP joint."""
    tip_d = _dist(landmarks[0], landmarks[tip])
    mcp_d = _dist(landmarks[0], landmarks[mcp])
    return tip_d <= mcp_d + 0.05 * scale


def _count_fingers(landmarks) -> int:
    scale = _hand_scale(landmarks)
    return sum(
        1 for tip, pip, mcp in zip(_FINGER_TIPS, _FINGER_PIPS, _FINGER_MCPS)
        if _finger_extended(landmarks, tip, pip, mcp, scale)
    )


def _finger_states(landmarks) -> tuple[bool, bool, bool, bool]:
    """Per-finger extended flag — (index, middle, ring, pinky).

    Needed for gesture shapes like peace, rock, call-me and point where
    *which* fingers are up matters, not just the total count.
    """
    scale = _hand_scale(landmarks)
    return tuple(  # type: ignore[return-value]
        _finger_extended(landmarks, tip, pip, mcp, scale)
        for tip, pip, mcp in zip(_FINGER_TIPS, _FINGER_PIPS, _FINGER_MCPS)
    )


def _thumb_extended(landmarks) -> bool:
    """True if the thumb sticks clearly out from the side of the palm.

    Distance from the index MCP (5) to the thumb tip (4) is compared to
    the hand scale so the check is orientation- *and* size-invariant.
    The tip also has to be farther from the wrist than the thumb's own
    MCP (2), which filters tucked thumbs curling across the palm.
    """
    scale = _hand_scale(landmarks)
    reach = _dist(landmarks[4], landmarks[5])
    tip_out = _dist(landmarks[0], landmarks[4]) > _dist(landmarks[0], landmarks[2]) + 0.1 * scale
    return reach > 0.6 * scale and tip_out and not _thumb_over_palm(landmarks)


def _thumb_over_palm(landmarks) -> bool:
    """Thumb tip tucked across the palm (closed-fist indicator)."""
    palm = [(landmarks[i].x, landmarks[i].y) for i in [0, 1, 5, 9, 13, 17]]
    tx, ty = landmarks[4].x, landmarks[4].y
    inside = False
    j = len(palm) - 1
    for i in range(len(palm)):
        xi, yi = palm[i]
        xj, yj = palm[j]
        crosses = (yi > ty) != (yj > ty)
        if crosses and tx < (xj - xi) * (ty - yi) / (yj - yi + 1e-6) + xi:
            inside = not inside
        j = i
    return inside


def _palm_angle_deg(landmarks) -> float:
    """Angle of the wrist→middle-MCP axis relative to image-up (degrees).

    0° means the fingers point straight up; positive values mean the
    hand has rotated toward image-right (the user's left in the mirror
    preview), negative toward image-left.
    """
    dx = landmarks[9].x - landmarks[0].x
    dy = landmarks[9].y - landmarks[0].y
    return math.degrees(math.atan2(dx, -dy))


# Minimum palm-axis rotation before we call a gesture a tilt. Anything
# below this is still a plain "open" — this larger threshold is the
# main reason tilts stop getting confused with other gestures.
_TILT_ANGLE_DEG = 35.0


def _wrist_tilt(landmarks, states, thumb_out: bool) -> str | None:
    """Return ``"tilt_left"`` / ``"tilt_right"`` for a *clearly open palm*
    that has been rotated well away from vertical.

    We now require every finger (and the thumb) to be extended before
    we emit a tilt label. A half-closed hand held at an angle used to
    read as "tilt" and collide with "three"/"four", which is the
    confusion the operator hit in testing.
    """
    if not all(states) or not thumb_out:
        return None
    angle = _palm_angle_deg(landmarks)
    if angle > _TILT_ANGLE_DEG:
        return "tilt_right"
    if angle < -_TILT_ANGLE_DEG:
        return "tilt_left"
    return None


def _finger_direction(landmarks, mcp: int, tip: int) -> tuple[float, float]:
    return (landmarks[tip].x - landmarks[mcp].x, landmarks[tip].y - landmarks[mcp].y)


def _angle_between_deg(v1: tuple[float, float], v2: tuple[float, float]) -> float:
    dot = v1[0] * v2[0] + v1[1] * v2[1]
    n1 = hypot(*v1)
    n2 = hypot(*v2)
    if n1 < 1e-6 or n2 < 1e-6:
        return 0.0
    cos_theta = max(-1.0, min(1.0, dot / (n1 * n2)))
    return math.degrees(math.acos(cos_theta))


# Maximum thumb-tip ↔ index-tip distance (in "hand scale" units) for a
# pinch. Comfortably tighter than a casual "two" pose so it stays
# visually distinct, and well inside the noise floor of MediaPipe's
# landmark jitter at typical webcam distances.
_PINCH_DIST_SCALE = 0.45


def _is_ok_circle(landmarks, states: tuple[bool, bool, bool, bool]) -> bool:
    """OK sign — thumb and index form a ring; other fingers folded.

    Easier than a strict thumbs-up for stiff hands; distinct from pinch
    because the index stays extended and the gap is wider.
    """
    if states[1] or states[2] or states[3]:
        return False
    if not states[0]:
        return False
    if _thumb_over_palm(landmarks):
        return False
    scale = _hand_scale(landmarks)
    gap = _dist(landmarks[4], landmarks[8])
    if gap < 0.22 * scale or gap > 0.62 * scale:
        return False
    return _finger_extended(landmarks, 8, 6, 5, scale)


def _is_palm_down(landmarks, states: tuple[bool, bool, bool, bool], thumb_out: bool) -> bool:
    """Flat palm facing downward — maps to a calm 'rest / home' pose."""
    if not all(states) or not thumb_out:
        return False
    scale = _hand_scale(landmarks)
    # Fingertips below the wrist in image space → palm facing down.
    tips_below = sum(
        1 for tip in _FINGER_TIPS
        if landmarks[tip].y > landmarks[0].y + 0.06 * scale
    )
    return tips_below >= 3 and landmarks[9].y > landmarks[0].y + 0.04 * scale


def _is_pinch(landmarks, states) -> bool:
    """Thumb tip touching (or nearly touching) the index tip, with
    middle, ring and pinky clearly curled. This replaces the old peace
    sign — pinch is much easier to detect reliably and never collides
    with "two" / "three" under tilt.
    """
    # Middle/ring/pinky must be folded down; the index *can* be
    # extended (classic open pinch) or partially folded (closed pinch)
    # so we only enforce the three curled ones.
    if states[1] or states[2] or states[3]:
        return False
    scale = _hand_scale(landmarks)
    # Thumb has to be away from the palm (not tucked across it) so a
    # fist doesn't masquerade as a pinch when landmarks wobble.
    if _thumb_over_palm(landmarks):
        return False
    tip_gap = _dist(landmarks[4], landmarks[8])
    if tip_gap > _PINCH_DIST_SCALE * scale:
        return False
    # Require the thumb + index tips to meet meaningfully forward of
    # the wrist — otherwise a closed fist with the thumb resting on
    # the side of the index tip can still satisfy the distance test.
    wrist_to_tips = min(
        _dist(landmarks[0], landmarks[4]),
        _dist(landmarks[0], landmarks[8]),
    )
    return wrist_to_tips > 0.85 * scale


def _is_point_l(landmarks, states, thumb_out: bool) -> bool:
    """L-shape / finger-gun: thumb out **and** index extended, every
    other finger folded. This is unmistakable next to "one" (thumb
    tucked) and doesn't collide with "call_me" (thumb + pinky)."""
    if not thumb_out:
        return False
    if states != (True, False, False, False):
        return False
    # Sanity-check the angle between thumb and index so a fist with the
    # thumb poking sideways doesn't register as point.
    v_thumb = (landmarks[4].x - landmarks[2].x, landmarks[4].y - landmarks[2].y)
    v_index = _finger_direction(landmarks, 5, 8)
    spread = _angle_between_deg(v_thumb, v_index)
    return spread >= 35.0


def _is_fist(landmarks) -> bool:
    """All four non-thumb fingers curled into the palm.

    We used to accept ``folded >= 3`` here, but that meant a "one"
    gesture (index up, other three down) satisfied the fist test — and
    because ``_gesture_label_baseline`` checks ``_is_fist`` first, the
    "one" rule never ran. Require *all four* to be folded and none
    clearly extended so a single finger held up still reads as "one".
    """
    scale = _hand_scale(landmarks)
    folded = 0
    extended = 0
    for tip, pip, mcp in zip(_FINGER_TIPS, _FINGER_PIPS, _FINGER_MCPS):
        if _finger_folded(landmarks, tip, pip, mcp, scale):
            folded += 1
        if _finger_extended(landmarks, tip, pip, mcp, scale):
            extended += 1
    return folded >= 4 and extended == 0


def _is_thumbs_up_strict(landmarks) -> bool:
    """Strict thumbs-up: vertical thumb, all four fingers clearly folded."""
    if _is_one_gesture(landmarks):
        return False
    if np is None:
        return False

    scale = _hand_scale(landmarks)
    states = _finger_states(landmarks)
    if any(states):
        return False

    thumb_tip_y = landmarks[4].y
    thumb_up_margin = 0.12 * scale
    thumb_up = (
        thumb_tip_y < landmarks[3].y - thumb_up_margin
        and thumb_tip_y < landmarks[2].y - thumb_up_margin
        and thumb_tip_y < landmarks[8].y - thumb_up_margin
        and thumb_tip_y < landmarks[5].y - thumb_up_margin
    )

    thumb_vec_x = landmarks[4].x - landmarks[2].x
    thumb_vec_y = landmarks[4].y - landmarks[2].y
    vertical = abs(thumb_vec_y) > abs(thumb_vec_x) * 0.85

    folded_count = sum(
        1 for tip, pip, mcp in zip(_FINGER_TIPS, _FINGER_PIPS, _FINGER_MCPS)
        if _finger_folded(landmarks, tip, pip, mcp, scale)
    )
    return thumb_up and vertical and folded_count >= 4


def _is_thumbs_up(landmarks) -> bool:
    """Backward-compatible alias for strict thumbs-up detection."""
    return _is_thumbs_up_strict(landmarks)
