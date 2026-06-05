"""Premium web console for profile, controls, and live robot status."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ai.gesture_interpreter import interpret_gesture_description
from ai.user_profile import MotorLevel, UserProfile, UserProfileStore, preset_for_level
from models import ActionRequest

LOGGER = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

_robot_app: Any = None
_profile_store: UserProfileStore | None = None


def set_robot_app(app: Any, db_path: Path) -> None:
    global _robot_app, _profile_store
    _robot_app = app
    _profile_store = UserProfileStore(db_path)


def create_app() -> FastAPI:
    api = FastAPI(title="Assistive Arm Console", version="1.0.0")

    if STATIC_DIR.is_dir():
        api.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @api.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @api.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "robot_connected": _robot_app is not None}

    @api.get("/api/camera/status")
    async def camera_status() -> dict:
        return _camera_payload()

    @api.get("/api/camera/mjpeg")
    async def camera_mjpeg() -> StreamingResponse:
        if _robot_app is None or getattr(_robot_app, "camera", None) is None:
            raise HTTPException(503, "Camera feed not available — start with --web and enable gestures in Profile.")

        async def stream() -> Any:
            boundary = b"--frame"
            while True:
                cam = _robot_app.camera
                if cam is None:
                    await asyncio.sleep(0.5)
                    continue
                jpeg = cam.latest_jpeg()
                if jpeg:
                    yield (
                        boundary
                        + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                        + str(len(jpeg)).encode()
                        + b"\r\n\r\n"
                        + jpeg
                        + b"\r\n"
                    )
                await asyncio.sleep(1.0 / 24.0)

        return StreamingResponse(
            stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @api.get("/api/profile")
    async def get_profile() -> dict:
        return _profile_payload(_load_profile())

    @api.put("/api/profile")
    async def put_profile(body: "ProfileUpdate") -> dict:
        profile = _merge_profile(_load_profile(), body)
        _save_profile(profile)
        return _profile_payload(profile)

    @api.get("/api/status")
    async def status() -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        payload = _live_payload()
        payload["profile"] = _profile_payload(_load_profile())
        return payload

    @api.post("/api/command/say")
    async def post_say(body: SayBody) -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        text = body.text.strip()
        if not text:
            raise HTTPException(400, "text required")
        profile = _load_profile()
        if not profile.enable_voice_input and not profile.enable_manual_input:
            raise HTTPException(403, "Voice and web commands disabled in profile")
        action = ActionRequest(
            source="web",
            intent="spoken_text",
            payload={"text": text},
            requires_confirmation=False,
        )
        await _robot_app.action_queue.put(action)
        return {"queued": True, "text": text}

    @api.post("/api/command")
    async def post_command(body: "CommandBody") -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        intent = body.intent.strip()
        if not intent:
            raise HTTPException(400, "intent required")
        requires = body.requires_confirmation
        if requires is None:
            requires = intent in {"pick_object", "place_object"}
        action = ActionRequest(
            source="web",
            intent=intent,
            payload=dict(body.payload or {}),
            requires_confirmation=bool(requires),
        )
        if not _robot_app.is_source_enabled("web"):
            raise HTTPException(403, "Manual/web control disabled in profile")
        await _robot_app.action_queue.put(action)
        return {"queued": True, "intent": intent}

    @api.get("/api/gestures")
    async def list_gestures() -> dict:
        return _gestures_payload()

    @api.post("/api/gestures/interpret")
    async def interpret_gesture(body: "GestureDescribeBody") -> dict:
        try:
            result = interpret_gesture_description(body.description)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return result

    @api.post("/api/gestures/capture/start")
    async def capture_start(body: "GestureCaptureStart") -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        if not _robot_app.is_source_enabled("gesture"):
            raise HTTPException(403, "Gesture input disabled in profile")
        _robot_app.start_gesture_capture({
            "display_name": body.display_name,
            "description": body.description,
            "intent": body.intent,
            "payload": body.payload or {},
        })
        return {"capturing": True, "message": "Show your gesture to the camera now."}

    @api.post("/api/gestures/capture/stop")
    async def capture_stop() -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        result = _robot_app.stop_gesture_capture()
        if result["sample_count"] < 4:
            raise HTTPException(400, "Not enough samples — hold the pose steady for 2–3 seconds.")
        return result

    @api.post("/api/gestures/confirm")
    async def confirm_gesture(body: "GestureConfirmBody") -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        catalog = _robot_app.custom_gestures
        ok, msg, similar = catalog.validate_new(
            display_name=body.display_name,
            template=body.template,
            intent=body.intent,
        )
        if not ok:
            detail = {"message": msg}
            if similar is not None:
                detail["similar_to"] = similar.to_dict()
            raise HTTPException(409, detail)
        try:
            item = catalog.add(
                display_name=body.display_name,
                description=body.description,
                intent=body.intent,
                payload=body.payload,
                template=body.template,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        _robot_app.gesture_bindings.set(item.gesture_id, item.intent, item.payload)
        _robot_app.activity.add(
            source="web",
            kind="gesture",
            text=f"learned gesture “{item.display_name}” → {item.intent}",
            accent="system",
        )
        return {"saved": True, "gesture": item.to_dict()}

    @api.delete("/api/gestures/{gesture_id}")
    async def delete_gesture(gesture_id: str) -> dict:
        if _robot_app is None:
            raise HTTPException(503, "Robot runtime not started")
        removed = _robot_app.custom_gestures.remove(gesture_id)
        if not removed:
            raise HTTPException(404, "Gesture not found")
        _robot_app.gesture_bindings.clear(gesture_id)
        return {"deleted": True}

    @api.websocket("/ws/live")
    async def live_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            while True:
                await websocket.send_json(_live_payload())
                await asyncio.sleep(0.4)
        except WebSocketDisconnect:
            return

    return api


class ProfileUpdate(BaseModel):
    display_name: str | None = None
    motor_level: str | None = None
    preferred_input: str | None = None
    enable_voice_input: bool | None = None
    enable_gesture_input: bool | None = None
    enable_manual_input: bool | None = None
    quit_gesture: str | None = None
    default_speed_pct: int | None = None
    notes: str | None = None
    rest_reminder_minutes: int | None = None
    fatigue_slowdown: bool | None = None
    gentle_reach: bool | None = None
    caregiver_mode: bool | None = None


class GestureDescribeBody(BaseModel):
    description: str


class GestureCaptureStart(BaseModel):
    display_name: str
    description: str = ""
    intent: str
    payload: dict = Field(default_factory=dict)


class GestureConfirmBody(BaseModel):
    display_name: str
    description: str = ""
    intent: str
    payload: dict = Field(default_factory=dict)
    template: list[float]


class CommandBody(BaseModel):
    intent: str
    payload: dict = Field(default_factory=dict)
    requires_confirmation: bool | None = None


class SayBody(BaseModel):
    text: str


def _camera_payload() -> dict:
    if _robot_app is None or getattr(_robot_app, "camera", None) is None:
        return {
            "available": False,
            "active": False,
            "message": "Camera not started — enable Gestures in Profile and run main.py --web.",
        }
    cam = _robot_app.camera
    import time

    now = time.monotonic()
    age = (now - cam.last_frame_at) if cam.last_frame_at else None
    active = age is not None and age < 3.0
    msg = "Live"
    if not active:
        if age is None:
            msg = "Waiting for first frame — grant Camera access to Terminal/Cursor in System Settings."
        else:
            msg = f"No frames for {age:.0f}s — check USB webcam or try --camera-index 1"
    return {
        "available": True,
        "active": active,
        "name": cam.active_name or "",
        "index": cam.active_index,
        "age_s": round(age, 1) if age is not None else None,
        "message": msg,
        "stream_url": "/api/camera/mjpeg",
    }


def _live_payload() -> dict:
    """Single snapshot for WebSocket and status API."""
    if _robot_app is None:
        return {"type": "error", "message": "robot offline"}
    state, age = _robot_app.current_state_snapshot()
    tel = _robot_app.telemetry_snapshot()
    profile = _load_profile()
    serial = tel.get("health", {}).get("serial", {})
    smart = tel.get("smart", {})

    env_objs = []
    if getattr(_robot_app, "environment", None) is not None:
        import time
        now = time.time()
        for label, obj in _robot_app.environment._objects.items():
            if now - obj.timestamp <= _robot_app.environment.expiration_s:
                env_objs.append({
                    "label": obj.label,
                    "confidence": float(obj.confidence),
                    "base_deg": int(obj.base_deg),
                    "distance_mm": int(obj.distance_mm),
                    "age_s": round(now - obj.timestamp, 1),
                })

    return {
        "type": "status",
        "camera": _camera_payload(),
        "serial": serial,
        "arm": {
            "base": state.base_deg,
            "lift": state.lift_deg,
            "rotate": state.rotate_deg,
            "claw": state.claw_deg,
            "range_mm": state.range_mm,
            "estop": state.estop,
            "age_s": age,
        },
        "environment": env_objs,
        "activity": tel.get("activity", [])[-12:],
        "pending": tel.get("pending"),
        "voice": _robot_app.voice_log.snapshot(),
        "input_fallback": tel.get("input_fallback", {}),
        "gesture_diversity": tel.get("gesture_diversity", {}),
        "smart": smart,
        "gesture_capture": tel.get("gesture_capture", {}),
        "profile_summary": {
            "display_name": profile.display_name,
            "motor_level": profile.motor_level.value,
            "enable_voice_input": profile.enable_voice_input,
            "enable_gesture_input": profile.enable_gesture_input,
            "enable_manual_input": profile.enable_manual_input,
            "rest_reminder_due": smart.get("rest_reminder_due", False),
        },
    }


def _load_profile() -> UserProfile:
    if _robot_app is not None and getattr(_robot_app, "user_profile", None) is not None:
        return _robot_app.user_profile
    if _profile_store is not None:
        return _profile_store.load()
    return preset_for_level(MotorLevel.MODERATE)


def _save_profile(profile: UserProfile) -> None:
    if _profile_store is not None:
        _profile_store.save(profile)
    if _robot_app is not None and hasattr(_robot_app, "apply_user_profile"):
        _robot_app.apply_user_profile(profile, persist=False)


def _merge_profile(current: UserProfile, body: ProfileUpdate) -> UserProfile:
    from dataclasses import replace

    if body.motor_level:
        try:
            level = MotorLevel(body.motor_level)
            base = preset_for_level(level)
            current = replace(
                base,
                display_name=current.display_name,
                notes=current.notes,
                preferred_input=current.preferred_input,
                enable_voice_input=current.enable_voice_input,
                enable_gesture_input=current.enable_gesture_input,
                enable_manual_input=current.enable_manual_input,
                quit_gesture=current.quit_gesture,
            )
        except ValueError:
            pass

    updates: dict[str, Any] = {}
    for field in (
        "display_name", "preferred_input", "enable_voice_input",
        "enable_gesture_input", "enable_manual_input", "quit_gesture",
        "default_speed_pct", "notes", "rest_reminder_minutes",
        "fatigue_slowdown", "gentle_reach", "caregiver_mode",
    ):
        val = getattr(body, field, None)
        if val is not None:
            updates[field] = val
    if updates:
        current = replace(current, **updates)
    current = replace(current, quit_gesture="peace_hold")
    return current


def _gestures_payload() -> dict:
    builtin = {
        "one": "Lift up",
        "two": "Lift down",
        "three": "Rotate left",
        "four": "Inspect pose",
        "thumbs_up": "Confirm yes",
        "thumbs_down": "Go home",
        "fist": "Close claw",
        "open": "Open claw",
        "spread": "Open claw (wide spread)",
        "spider": "Rotate right",
        "high_five": "Inspect pose",
        "pinch": "Rotate right",
        "rock": "Emergency stop",
        "stop_palm": "Emergency stop",
        "point": "Drop-ready pose",
        "call_me": "Pickup-ready pose",
        "peace": "Hold to quit (system)",
    }
    custom: list[dict] = []
    if _robot_app is not None:
        custom = [g.to_dict() for g in _robot_app.custom_gestures.list_all()]
    return {"builtin": builtin, "custom": custom, "quit": "Hold peace sign to quit"}


def _profile_payload(profile: UserProfile) -> dict:
    data = profile.to_dict()
    data["quit_gesture"] = "peace_hold"
    gestures = _gestures_payload()
    data["gesture_guide"] = {**gestures["builtin"], "quit": gestures["quit"]}
    data["custom_gestures"] = gestures["custom"]
    return data


async def run_uvicorn(host: str, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(create_app(), host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()
