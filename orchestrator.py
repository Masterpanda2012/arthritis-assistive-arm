from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai.memory_store import MemoryStore
from ai.environment import EnvironmentMap
from ai.gesture_bindings import GestureBindings
from ai.llm_agent import LLMIntentAgent
from ai.input_fallback import InputFallbackAdvisor
from ai.adl_tasks import get_adl_task, match_adl_phrase
from ai.lab_trials import LabTrialLogger
from ai.user_profile import UserProfileStore
from ai.custom_gestures import CustomGestureCatalog, average_vectors
from ai.gesture_diversity import GestureDiversityTracker
from config import RuntimeConfig
from models import ActionRequest, ArmState, PlannerResult
from motion.planner import MotionPlanner
from motion.serial_bridge import SerialBridge


LOGGER = logging.getLogger(__name__)


@dataclass
class ActivityLog:
    """Bounded ring buffer of every input/output event the system sees.

    Drives the unified "Activity" feed in the control panel so the user
    can tell — at a glance — what just happened, regardless of whether
    the input came from voice, gesture, vision, the panel, or the arm
    itself. Keeping it in the orchestrator (rather than scattered logs)
    is the easiest way to keep every source feeling like part of the
    same application.
    """

    maxlen: int = 60
    events: deque = field(default_factory=lambda: deque(maxlen=60))
    _seq: int = 0

    def __post_init__(self) -> None:
        # Re-bind the deque with the caller-specified maxlen.
        self.events = deque(maxlen=self.maxlen)

    def add(self, source: str, kind: str, text: str, *, accent: str = "") -> None:
        self._seq += 1
        self.events.append({
            "id": self._seq,
            "ts": time.monotonic(),
            "source": source,
            "kind": kind,
            "text": text,
            "accent": accent,
        })

    def snapshot(self, limit: int = 20) -> list[dict]:
        items = list(self.events)[-limit:]
        # Attach wall-clock "ago" string the UI can render directly.
        now = time.monotonic()
        out = []
        for ev in items:
            age = now - ev["ts"]
            out.append({
                "id": ev["id"],
                "source": ev["source"],
                "kind": ev["kind"],
                "text": ev["text"],
                "accent": ev["accent"],
                "age_s": round(age, 1),
            })
        return out


@dataclass
class VoiceLog:
    """Shared transcript state so the control panel can show the user
    exactly what the computer thought it heard (partial & final), what
    intent was resolved, and any typed-text commands."""

    partial: str = ""
    heard: str = ""
    source: str = ""           # "voice" or "typed"
    intent: str = ""
    payload: dict = field(default_factory=dict)
    status: str = ""           # free-form, e.g. "resolved", "ignored", "ambiguous"
    updated_at: float = 0.0

    def set_partial(self, text: str) -> None:
        self.partial = text
        self.updated_at = time.monotonic()

    def set_heard(self, text: str, *, source: str) -> None:
        self.heard = text
        self.source = source
        self.partial = ""
        self.updated_at = time.monotonic()

    def set_intent(self, intent: str, payload: dict | None = None, *, status: str = "resolved") -> None:
        self.intent = intent
        self.payload = dict(payload or {})
        self.status = status
        self.updated_at = time.monotonic()

    def snapshot(self) -> dict:
        return {
            "partial": self.partial,
            "heard": self.heard,
            "source": self.source,
            "intent": self.intent,
            "payload": self.payload,
            "status": self.status,
            "age": round(time.monotonic() - self.updated_at, 2) if self.updated_at else -1.0,
        }


@dataclass
class SessionTracker:
    """Lightweight runtime counters for health, throughput, and latency."""

    started_at: float = field(default_factory=time.monotonic)
    source_counts: Counter = field(default_factory=Counter)
    intent_counts: Counter = field(default_factory=Counter)
    status_counts: Counter = field(default_factory=Counter)
    recent_latency_ms: deque = field(default_factory=lambda: deque(maxlen=50))
    queue_high_water: int = 0
    total_latency_ms: float = 0.0
    latency_count: int = 0
    last_action_at: float = 0.0
    last_source: str = ""
    last_intent: str = ""
    last_status: str = ""
    last_latency_ms: float = 0.0

    def observe_inbound(self, action: ActionRequest, *, queue_depth: int) -> None:
        source = action.source or "system"
        self.source_counts[source] += 1
        self.intent_counts[action.intent] += 1
        self.queue_high_water = max(self.queue_high_water, int(queue_depth))
        self.last_action_at = time.monotonic()
        self.last_source = source
        self.last_intent = action.intent

    def observe_status(
        self,
        status: str,
        *,
        action: ActionRequest | None = None,
        latency_ms: float | None = None,
    ) -> None:
        self.status_counts[status] += 1
        self.last_status = status
        if action is not None:
            self.last_source = action.source or "system"
            self.last_intent = action.intent
        if latency_ms is not None:
            value = max(0.0, float(latency_ms))
            self.last_latency_ms = round(value, 1)
            self.total_latency_ms += value
            self.latency_count += 1
            self.recent_latency_ms.append(value)

    def snapshot(self, *, now: float, queue_depth: int, state_queue_depth: int) -> dict[str, Any]:
        recent = list(self.recent_latency_ms)
        avg_latency = self.total_latency_ms / self.latency_count if self.latency_count else 0.0
        recent_avg = sum(recent) / len(recent) if recent else 0.0
        return {
            "uptime_s": round(now - self.started_at, 1),
            "queue_depth": int(queue_depth),
            "state_queue_depth": int(state_queue_depth),
            "queue_high_water": self.queue_high_water,
            "sources": dict(self.source_counts),
            "intents": dict(self.intent_counts.most_common(12)),
            "statuses": dict(self.status_counts),
            "latency_ms": {
                "last": self.last_latency_ms,
                "avg": round(avg_latency, 1),
                "recent_avg": round(recent_avg, 1),
                "samples": self.latency_count,
            },
            "last_action": {
                "source": self.last_source,
                "intent": self.last_intent,
                "status": self.last_status,
                "age_s": round(now - self.last_action_at, 1) if self.last_action_at else -1.0,
            },
        }


class AdaptiveRobotArmApp:
    def __init__(self, config: RuntimeConfig, user_profile: Any | None = None) -> None:
        self.config = config
        self.user_profile = user_profile
        self.action_queue: asyncio.Queue[ActionRequest] = asyncio.Queue()
        self.state_queue: asyncio.Queue[ArmState] = asyncio.Queue()
        self.stop_event = asyncio.Event()
        self.serial_bridge = SerialBridge(config, self.state_queue)
        self.environment = EnvironmentMap(expiration_s=300.0)
        self.planner = MotionPlanner(config, self.environment)
        self.memory_store = MemoryStore(config.memory_db_path)
        self.gesture_bindings = GestureBindings(self.memory_store)
        # Lip-activity state is shared with the voice loop as a cheap
        # VAD so ambient audio stops being mistaken for robot commands.
        from inputs.lip_activity import LipActivity
        self.lip_activity = LipActivity()
        self.llm_agent = LLMIntentAgent(
            config, self.memory_store, self.environment, self.gesture_bindings, user_profile=user_profile
        )
        self.input_fallback = (
            InputFallbackAdvisor(user_profile) if user_profile is not None else None
        )
        self.pending_confirmation_message: str = ""
        self.is_sweeping = False
        self.camera = None
        needs_camera = (
            config.features.enable_gesture
            or config.features.enable_vision
            or config.web_camera_preview
        )
        if needs_camera:
            from inputs.camera import SharedCamera

            self.camera = SharedCamera(
                device_index=config.camera_device_index,
                prefer_name_substr=config.camera_prefer_name or None,
            )
        self.pending_confirmation: ActionRequest | None = None
        self.pending_confirmation_at = 0.0
        self.voice_log = VoiceLog()
        self.activity = ActivityLog(maxlen=60)
        log_root = (
            config.memory_db_path.parent.parent
            if config.memory_db_path.parent.name == "ai"
            else config.memory_db_path.parent
        )
        self.lab_trials = LabTrialLogger(
            log_root / "logs" / "lab_trials.jsonl",
            motor_level=config.motor_level,
        )
        # Freshness heartbeats so the panel can show traffic-light health.
        self.last_camera_frame_at: float = 0.0
        self.last_voice_heard_at: float = 0.0
        self.last_vision_detect_at: float = 0.0
        self.last_gesture_at: float = 0.0
        # Most recent user-facing command from any source (voice, gesture,
        # panel, vision…). Surfaced as a CMD slot on the camera preview's
        # bottom strip so manual panel clicks are visibly acknowledged on
        # the same glance-line as voice/gesture events.
        self.last_action_at: float = 0.0
        self.last_action_label: str = ""
        self.last_action_source: str = ""
        self.latest_state = ArmState(
            base_deg=config.home_pose.base_deg,
            lift_deg=config.home_pose.lift_deg,
            rotate_deg=config.home_pose.rotate_deg,
            claw_deg=config.home_pose.claw_deg,
            range_mm=-1,
            estop=False,
            last_error="",
        )
        self.latest_state_at = time.monotonic()
        self._profile_store = UserProfileStore(config.memory_db_path)
        self.custom_gestures = CustomGestureCatalog(self.memory_store)
        self.gesture_diversity = GestureDiversityTracker()
        self.session_started_at = time.monotonic()
        self.session_command_count = 0
        self.session_tracker = SessionTracker(started_at=self.session_started_at)
        self._gesture_capture_active = False
        self._gesture_capture_samples: list[list[float]] = []
        self._gesture_capture_meta: dict[str, Any] = {}

    def _gesture_capture_sample(self, vector: list[float]) -> None:
        if not self._gesture_capture_active:
            return
        if len(self._gesture_capture_samples) >= 36:
            return
        self._gesture_capture_samples.append(vector)

    def start_gesture_capture(self, meta: dict[str, Any] | None = None) -> None:
        self._gesture_capture_active = True
        self._gesture_capture_samples = []
        self._gesture_capture_meta = dict(meta or {})

    def stop_gesture_capture(self) -> dict[str, Any]:
        self._gesture_capture_active = False
        samples = list(self._gesture_capture_samples)
        self._gesture_capture_samples = []
        template = average_vectors(samples)
        return {
            "sample_count": len(samples),
            "template": template,
            "meta": dict(self._gesture_capture_meta),
        }

    def gesture_capture_status(self) -> dict[str, Any]:
        return {
            "active": self._gesture_capture_active,
            "sample_count": len(self._gesture_capture_samples),
            "meta": dict(self._gesture_capture_meta),
        }

    def is_source_enabled(self, source: str) -> bool:
        profile = self.user_profile
        if profile is None:
            return True
        src = (source or "").strip().lower()
        if src in {"system", "vision"}:
            return True
        if src == "voice":
            return bool(profile.enable_voice_input)
        if src == "gesture":
            return bool(profile.enable_gesture_input)
        if src in {"panel", "typed", "web"}:
            return bool(profile.enable_manual_input)
        return True

    def apply_user_profile(self, profile: Any, *, persist: bool = True) -> None:
        from dataclasses import replace

        from ai.user_profile import UserProfile, apply_profile_to_config

        if not isinstance(profile, UserProfile):
            raise TypeError("profile must be UserProfile")
        self.user_profile = profile
        tuned = apply_profile_to_config(self.config, profile)
        self.config = replace(
            tuned,
            features=replace(
                tuned.features,
                enable_gesture=profile.enable_gesture_input,
                enable_voice=profile.enable_voice_input,
                enable_control_panel=profile.enable_manual_input,
            ),
        )
        self.lab_trials.motor_level = profile.motor_level.value
        if persist:
            self._profile_store.save(profile)
        self.activity.add(
            source="system",
            kind="profile",
            text=f"profile updated · {profile.display_name} · {profile.motor_level.value}",
            accent="system",
        )
        LOGGER.info(
            "Runtime features: gesture=%s voice=%s vision=%s depth=%s panel=%s ai=%s motor=%s accessibility=%s",
            self.config.features.enable_gesture,
            self.config.features.enable_voice,
            self.config.features.enable_vision,
            self.config.features.enable_depth,
            self.config.features.enable_control_panel,
            self.config.features.enable_ai,
            self.config.motor_level,
            self.config.features.accessibility_ui,
        )

    async def run(self, duration: float | None = None) -> None:
        await self.serial_bridge.start()
        LOGGER.info(
            "Serial status: mode=%s port=%s",
            self.serial_bridge.mode,
            self.serial_bridge.port_path or self.config.serial.port,
        )
        initial_init_ok = await self.serial_bridge.send_init()
        if not initial_init_ok:
            LOGGER.error(
                "Initial INIT command failed; robot may not be under live control. serial_mode=%s port=%s",
                self.serial_bridge.mode,
                self.serial_bridge.port_path or self.config.serial.port,
            )
            self.activity.add(
                source="system",
                kind="serial",
                text="initial INIT failed — check Arduino link before trusting motion",
                accent="warning",
            )

        tasks = [
            asyncio.create_task(self._state_consumer(), name="state-consumer"),
            asyncio.create_task(self._action_consumer(), name="action-consumer"),
        ]
        if self.camera is not None:
            tasks.append(asyncio.create_task(self.camera.run(self.stop_event), name="shared-camera"))
        tasks.extend(self._start_input_tasks())
        if self.input_fallback is not None:
            tasks.append(asyncio.create_task(self._fallback_advisor_loop(), name="input-fallback"))
        if self.config.enable_startup_sweep:
            tasks.append(asyncio.create_task(self._startup_sweep(), name="startup-sweep"))

        # Surface task failures (previously these died silently and users
        # wondered why gesture/voice "stopped working").
        for task in tasks:
            task.add_done_callback(self._log_task_result)

        try:
            if duration is None:
                await self.stop_event.wait()
            else:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), timeout=duration)
                except asyncio.TimeoutError:
                    LOGGER.info("Run duration reached; shutting down cleanly.")
        finally:
            self.stop_event.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.serial_bridge.close()

    def _start_input_tasks(self) -> list[asyncio.Task[None]]:
        tasks: list[asyncio.Task[None]] = []
        # Short HUD line for the Webcam preview (smooth raw mirror + red label); not full-frame overlays.
        gesture_hud_queue: asyncio.Queue | None = None
        if (
            self.camera is not None
            and self.config.features.enable_gesture
            and (self.config.show_camera_windows or self.config.web_camera_preview)
        ):
            gesture_hud_queue = asyncio.Queue(maxsize=1)

        if self.config.features.enable_gesture:
            from inputs.gesture import gesture_loop

            if self.camera is None:
                LOGGER.warning("Gesture input requested without a camera feed.")
            else:
                tasks.append(
                    asyncio.create_task(
                        gesture_loop(
                            self.config,
                            self.action_queue,
                            self.stop_event,
                            self.camera.subscribe("gesture"),
                            hud_queue=gesture_hud_queue,
                            bindings=self.gesture_bindings,
                            enabled_fn=lambda: self.is_source_enabled("gesture"),
                            custom_catalog=self.custom_gestures,
                            diversity=self.gesture_diversity,
                            capture_fn=self._gesture_capture_sample,
                        ),
                        name="gesture-input",
                    )
                )
        if self.config.features.enable_voice:
            from inputs.voice import voice_loop

            # If we have a camera feed, spin up the cheap lip-activity
            # VAD so voice can reject ambient audio when the user is
            # plainly not talking. Without a camera we fall back to the
            # old always-on behaviour.
            lip = self.lip_activity if self.camera is not None else None
            if self.camera is not None:
                from inputs.lip_activity import lip_activity_loop

                tasks.append(
                    asyncio.create_task(
                        lip_activity_loop(
                            self.camera.subscribe("lip"),
                            self.stop_event,
                            self.lip_activity,
                        ),
                        name="lip-activity",
                    )
                )

            tasks.append(
                asyncio.create_task(
                    voice_loop(
                        self.config,
                        self.action_queue,
                        self.stop_event,
                        voice_log=self.voice_log,
                        lip_activity=lip,
                        enabled_fn=lambda: self.is_source_enabled("voice"),
                    ),
                    name="voice-input",
                )
            )
        if self.config.features.enable_control_panel:
            from inputs.control_panel import control_panel_loop

            def _panel_status_lines() -> list[str]:
                port = self.serial_bridge.port_path or self.config.serial.port
                serial_line = f"Serial: {self.serial_bridge.mode.upper()}  ({port})"
                profile_line = ""
                if self.user_profile is not None:
                    profile_line = f"Profile: {self.user_profile.motor_level.value} arthritis assist"
                lines = [serial_line, self.llm_agent.status_summary()]
                if profile_line:
                    lines.append(profile_line)
                return lines

            tasks.append(
                asyncio.create_task(
                    control_panel_loop(
                        self.action_queue,
                        self.stop_event,
                        status_fn=_panel_status_lines,
                        state_fn=self.current_state_snapshot,
                        voice_fn=self.voice_log.snapshot,
                        telemetry_fn=self.telemetry_snapshot,
                        accessibility_ui=self.config.features.accessibility_ui,
                    ),
                    name="control-panel",
                )
            )
        if self.camera is not None and self.config.show_camera_windows:
            from inputs.camera_preview import camera_preview_loop

            # Dedicated raw queue keeps the window alive even when MediaPipe blocks gesture.
            preview_raw_queue = self.camera.subscribe("preview")

            def _preview_status() -> str:
                """Bottom HUD line: packs every channel's health into a
                single glance-able strip so the operator doesn't need
                to flip back to the control panel."""
                port = self.serial_bridge.port_path or self.config.serial.port
                mode = self.serial_bridge.mode.upper()
                ai = self.llm_agent.status_summary().replace("AI: ", "")
                now = time.monotonic()

                def _age(ts: float) -> str:
                    if not ts:
                        return "—"
                    d = now - ts
                    if d < 1.0:
                        return "now"
                    if d < 60.0:
                        return f"{int(d)}s"
                    return f"{int(d // 60)}m"

                heard = (self.voice_log.heard or self.voice_log.partial or "").strip()
                if len(heard) > 40:
                    heard = heard[:37] + "…"
                heard_txt = heard if heard else "…"
                # Live VAD indicator from lip activity so operator sees
                # the voice pipeline actually "seeing" them speak.
                lip_txt = "—"
                if self.lip_activity.has_face(window=2.5):
                    lip_txt = "SPEAKING" if self.lip_activity.is_speaking(1.2) else "quiet"
                active_txt = "LISTENING" if (
                    self.voice_log.heard.startswith("🎤")
                    or self.voice_log.status == "listening"
                ) else ""
                cmd_label = (self.last_action_label or "…").strip()
                if len(cmd_label) > 28:
                    cmd_label = cmd_label[:25] + "…"
                cmd_src = self.last_action_source or "—"
                mic_line = f"MIC {_age(self.last_voice_heard_at)} · “{heard_txt}” · LIPS {lip_txt}"
                if active_txt:
                    mic_line = f"{active_txt} · " + mic_line
                pieces = [
                    f"SERIAL {mode}",
                    f"AI {ai[:24]}",
                    f"CMD {_age(self.last_action_at)} · {cmd_label} ({cmd_src})",
                    mic_line,
                    f"VIS {_age(self.last_vision_detect_at)}",
                    f"GEST {_age(self.last_gesture_at)}",
                ]
                pending = self.pending_confirmation
                if pending is not None:
                    label = self._friendly_intent(pending.intent, pending.payload)
                    pieces.insert(0, f"⚠ CONFIRM? {label}")
                return "  |  ".join(pieces)

            tasks.append(
                asyncio.create_task(
                    camera_preview_loop(
                        self.config,
                        self.action_queue,
                        self.stop_event,
                        preview_raw_queue,
                        hud_queue=gesture_hud_queue,
                        status_line_fn=_preview_status,
                    ),
                    name="camera-preview",
                )
            )
        if self.config.features.enable_vision:
            from inputs.vision import vision_loop

            if self.camera is None:
                LOGGER.warning("Vision input requested without a camera feed.")
            else:
                tasks.append(
                    asyncio.create_task(
                        vision_loop(
                            self.config,
                            self.action_queue,
                            self.stop_event,
                            self.current_state_snapshot,
                            self.camera.subscribe("vision"),
                            environment_cb=self._vision_environment_update,
                        ),
                        name="vision-input",
                    )
                )
        return tasks

    def current_state_snapshot(self) -> tuple[ArmState, float]:
        return self.latest_state, time.monotonic() - self.latest_state_at

    def telemetry_snapshot(self, *, include_heavy: bool = True) -> dict:
        """Bundle everything the control panel needs in one call.

        Keeping this on the orchestrator side means the panel does not
        have to juggle four separate callbacks that can drift out of
        sync, and gives us a single place to add new panels later
        (e.g. vision detections, environment map).
        """
        now = time.monotonic()
        pending = self.pending_confirmation
        timeout = self.config.confirmation_timeout_s
        pending_payload: dict | None = None
        if pending is not None:
            age = now - self.pending_confirmation_at
            pending_payload = {
                "intent": pending.intent,
                "payload": pending.payload,
                "source": pending.source,
                "age_s": round(age, 1),
                "timeout_s": float(timeout),
                "remaining_s": round(max(0.0, timeout - age), 1),
                "message": self.pending_confirmation_message,
            }
        payload = {
            "activity": self.activity.snapshot(limit=20),
            "pending": pending_payload,
            "profile": {
                "motor_level": self.config.motor_level,
                "accessibility_ui": self.config.features.accessibility_ui,
                "speed_pct": self.config.default_speed_pct,
            },
            "input_fallback": self.input_fallback.snapshot() if self.input_fallback else {},
            "gesture_diversity": self.gesture_diversity.snapshot(),
            "smart": self._smart_snapshot(now),
            "gesture_capture": self.gesture_capture_status(),
            "metrics": self.session_tracker.snapshot(
                now=now,
                queue_depth=self.action_queue.qsize(),
                state_queue_depth=self.state_queue.qsize(),
            ),
            "health": {
                "serial": {
                    "mode": self.serial_bridge.mode,
                    "port": self.serial_bridge.port_path or self.config.serial.port,
                },
                "ai": self.llm_agent.status_summary(),
                "camera": {
                    "active_index": getattr(self.camera, "active_index", None) if self.camera else None,
                    "active_name": getattr(self.camera, "active_name", "") if self.camera else "",
                    "age_s": round(now - getattr(self.camera, "last_frame_at", 0.0), 1) if self.camera and getattr(self.camera, "last_frame_at", 0.0) else -1.0,
                },
                "voice_age_s": round(now - self.last_voice_heard_at, 1) if self.last_voice_heard_at else -1.0,
                "vision_age_s": round(now - self.last_vision_detect_at, 1) if self.last_vision_detect_at else -1.0,
                "gesture_age_s": round(now - self.last_gesture_at, 1) if self.last_gesture_at else -1.0,
                "vision_pipeline": self._vision_pipeline_health(),
                "features": {
                    "gesture": self.config.features.enable_gesture,
                    "voice": self.config.features.enable_voice,
                    "vision": self.config.features.enable_vision,
                    "ai": self.config.features.enable_ai,
                },
            },
        }
        if include_heavy:
            payload["serial_monitor"] = self.serial_bridge.monitor_snapshot(limit=80)
            payload["lab"] = self.lab_trials.snapshot()
            payload["custom_gesture_count"] = len(self.custom_gestures.list_all())
        return payload

    def _vision_environment_update(
        self,
        target: Any,
        state: Any,
        calibration: Any | None,
    ) -> None:
        """Keep the workspace map warm from depth + LiDAR even before a pick."""
        if target is None:
            return
        range_mm = int(getattr(target, "range_mm", -1) or -1)
        if range_mm <= 0:
            depth_mm = float(getattr(target, "depth_mm", -1.0) or -1.0)
            if depth_mm > 0:
                range_mm = int(depth_mm)
        if range_mm <= 0:
            return

        base_deg = int(getattr(state, "base_deg", self.latest_state.base_deg))
        if getattr(target, "has_3d", False):
            if calibration is None:
                from motion.calibration import load_calibration

                calibration = load_calibration(self.config.calibration_path)
            if calibration is not None:
                joints = calibration.camera_mm_to_joints(
                    float(getattr(target, "camera_x_mm", 0.0)),
                    float(getattr(target, "camera_y_mm", 0.0)),
                    float(getattr(target, "camera_z_mm", range_mm)),
                )
                base_deg = int(joints.get("base_deg", base_deg))

        self.environment.update_object(
            label=str(getattr(target, "label", "object")),
            base_deg=base_deg,
            distance_mm=range_mm,
            confidence=float(getattr(target, "confidence", 0.0)),
        )

    def _vision_pipeline_health(self) -> dict[str, Any]:
        if not self.config.features.enable_vision:
            return {"active": False, "summary": "vision off"}
        try:
            from inputs.vision import vision_pipeline_status

            status = vision_pipeline_status()
            depth = status.get("depth") or {}
            summary = depth.get("summary", "vision running")
            return {
                "active": True,
                "summary": summary,
                "depth_ready": bool(depth.get("ready")),
                "last_median_mm": depth.get("last_median_mm"),
                "last_inference_ms": depth.get("last_inference_ms"),
                "labels": status.get("last_labels") or {},
            }
        except Exception:
            return {"active": True, "summary": "vision running"}

    def _smart_snapshot(self, now: float) -> dict[str, Any]:
        profile = self.user_profile
        session_min = max(0.0, (now - self.session_started_at) / 60.0)
        fatigue = False
        if profile is not None and getattr(profile, "fatigue_slowdown", True):
            fatigue = self.session_command_count >= 18 and session_min < 20.0
        rest_due = False
        rest_min = int(getattr(profile, "rest_reminder_minutes", 45) or 0) if profile else 0
        if rest_min > 0 and session_min >= rest_min:
            rest_due = True
        return {
            "session_minutes": round(session_min, 1),
            "command_count": self.session_command_count,
            "fatigue_slowdown": fatigue,
            "rest_reminder_due": rest_due,
            "rest_reminder_minutes": rest_min,
            "caregiver_mode": bool(getattr(profile, "caregiver_mode", False)) if profile else False,
            "gentle_reach": bool(getattr(profile, "gentle_reach", True)) if profile else True,
        }

    def _log_task_result(self, task: "asyncio.Task[Any]") -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            LOGGER.error(
                "Background task %r crashed: %s", task.get_name(), exc, exc_info=exc
            )

    async def _state_consumer(self) -> None:
        while not self.stop_event.is_set():
            state = await self.state_queue.get()
            self.latest_state = state
            self.latest_state_at = time.monotonic()
            LOGGER.info(
                "STATE base=%s lift=%s rotate=%s claw=%s range_mm=%s estop=%s",
                state.base_deg,
                state.lift_deg,
                state.rotate_deg,
                state.claw_deg,
                state.range_mm,
                state.estop,
            )

    async def _action_consumer(self) -> None:
        while not self.stop_event.is_set():
            action = await self.action_queue.get()
            action_started_at = time.monotonic()
            self.session_tracker.observe_inbound(
                action,
                queue_depth=self.action_queue.qsize() + 1,
            )
            if not self.is_source_enabled(action.source or "system"):
                LOGGER.debug("Ignored %s (input channel disabled in profile).", action.source)
                self.session_tracker.observe_status("disabled", action=action)
                continue
            LOGGER.info("INPUT %s", self._format_action(action))
            # Freshness tracking — used by the panel's traffic-lights.
            now = action_started_at
            if action.source == "voice":
                self.last_voice_heard_at = now
            elif action.source == "gesture":
                self.last_gesture_at = now
            elif action.source == "vision":
                self.last_vision_detect_at = now
            # "Last command" beacon for the camera-preview HUD. We skip
            # spoken_text (the raw ASR line — handled separately below)
            # and the yes/no confirmations so the CMD slot tracks the
            # real commanded motion rather than meta-interactions.
            if action.intent not in {
                "spoken_text", "vision_target", "shutdown",
                "confirm_yes", "confirm_no",
            }:
                self.last_action_at = now
                self.last_action_label = self._friendly_intent(action.intent, action.payload)
                self.last_action_source = action.source or "system"
                # Manual panel clicks should mirror into the voice log
                # too so the "Heard / Intent" row at the bottom of the
                # control-panel card updates alongside real voice input.
                if action.source == "panel":
                    self.voice_log.set_heard(self.last_action_label, source="typed")
                    self.voice_log.set_intent(action.intent, action.payload, status="matched")
            # Log the inbound event to the unified activity feed.
            self._record_activity_in(action)
            if self.input_fallback is not None and action.source in {"voice", "gesture", "panel", "vision"}:
                self.input_fallback.record_attempt(action.source)
            resolved_action = await self._resolve_action(action)
            if resolved_action is None:
                if self.input_fallback is not None and action.intent == "spoken_text":
                    self.input_fallback.record_failure(action.source or "voice")
                status = "pending" if self.pending_confirmation is action else "ignored"
                self.session_tracker.observe_status(status, action=action)
                LOGGER.info("ACTION ignored or deferred for %s.", action.intent)
                continue
            LOGGER.info("ACTION resolved -> %s", self._format_action(resolved_action))
            if self.input_fallback is not None:
                self.input_fallback.record_success(resolved_action.source)
            plan = self.planner.plan(resolved_action, self.latest_state)
            LOGGER.info("PLAN %s", self._describe_plan(plan))
            executed = await self._execute_plan(plan)
            if plan.kind != "NONE":
                self.record_plan_execution(resolved_action, plan, executed)
                self.lab_trials.record_command(resolved_action, plan, executed, self.latest_state)
                self._record_activity_out(resolved_action, plan, executed)
                self.session_tracker.observe_status(
                    "executed" if executed else "failed",
                    action=resolved_action,
                    latency_ms=(time.monotonic() - action_started_at) * 1000.0,
                )
            else:
                self.session_tracker.observe_status(
                    "no_plan",
                    action=resolved_action,
                    latency_ms=(time.monotonic() - action_started_at) * 1000.0,
                )

    def _record_activity_in(self, action: ActionRequest) -> None:
        source = action.source or "system"
        if action.intent == "vision_target":
            target = action.payload.get("target")
            label = getattr(target, "label", "object")
            conf = getattr(target, "confidence", 0.0)
            depth_mm = int(getattr(target, "depth_mm", -1) or -1)
            range_mm = int(getattr(target, "range_mm", -1) or -1)
            dist = range_mm if range_mm > 0 else depth_mm
            extra = f" · {dist}mm" if dist > 0 else ""
            if getattr(target, "has_3d", False):
                extra += " · 3D"
            self.activity.add(
                source="vision",
                kind="detected",
                text=f"{label} ({conf:.0%}){extra}",
                accent="vision",
            )
            return
        if action.intent == "spoken_text":
            text = str(action.payload.get("text", "")).strip()
            if text:
                self.activity.add(
                    source=source if source in {"voice", "panel"} else "voice",
                    kind="heard" if source == "voice" else "typed",
                    text=text,
                    accent=source,
                )
            return
        if action.intent in {"confirm_yes", "confirm_no"}:
            self.activity.add(
                source=source,
                kind="confirm",
                text="yes" if action.intent == "confirm_yes" else "no",
                accent=source,
            )
            return
        if action.intent == "shutdown":
            return
        label = self._friendly_intent(action.intent, action.payload)
        self.activity.add(source=source, kind="command", text=label, accent=source)

    def _record_activity_out(self, resolved: ActionRequest, plan: PlannerResult, executed: bool) -> None:
        # Plans that actually moved the arm are the "output" side of the
        # loop — useful when the resolved intent differs from what was
        # asked for (e.g. spoken_text → pick_object).
        if plan.kind in {"NONE"}:
            return
        label = self._friendly_intent(resolved.intent, resolved.payload)
        status = "executed" if executed else "failed"
        summary = f"{status} · {plan.kind.lower()} · {label}"
        self.activity.add(
            source="arm",
            kind=status,
            text=summary,
            accent="arm" if executed else "warning",
        )

    @staticmethod
    def _friendly_intent(intent: str, payload: dict | None) -> str:
        payload = payload or {}
        pretty = {
            "open_claw": "open claw",
            "close_claw": "close claw",
            "lift_up": "lift ↑",
            "lift_down": "lift ↓",
            "base_left": "base ←",
            "base_right": "base →",
            "rotate_left": "rotate ↺",
            "rotate_right": "rotate ↻",
            "home": "home",
            "emergency_stop": "EMERGENCY STOP",
            "shutdown": "quit program",
            "reset_gestures": "reset learned gestures",
        }.get(intent)
        if pretty:
            return pretty
        if intent == "preset_pose":
            return f"pose · {payload.get('name', '?')}"
        if intent in {"pick_object", "place_object"}:
            verb = "pick" if intent == "pick_object" else "place"
            adl_id = payload.get("adl_id")
            if adl_id:
                task = get_adl_task(str(adl_id))
                if task:
                    return f"{verb} · {task.label}"
            return f"{verb} · {payload.get('label', 'object')}"
        if intent == "teach_gesture":
            g = payload.get("gesture", "?")
            ti = payload.get("target_intent", "?")
            return f"teach {g} → {ti}"
        if intent == "teach_phrase":
            p = payload.get("phrase", "?")
            ti = payload.get("target_intent", "?")
            return f"teach “{p}” → {ti}"
        return intent

    def _apply_teach_gesture(self, action: ActionRequest) -> None:
        gesture = str(action.payload.get("gesture", "")).strip()
        target_intent = str(action.payload.get("target_intent", "")).strip()
        target_payload = action.payload.get("target_payload") or {}
        if not gesture or not target_intent:
            LOGGER.info("Teach gesture ignored (missing fields): %s", action.payload)
            self.voice_log.set_intent("teach_gesture", status="invalid")
            return
        try:
            self.gesture_bindings.set(gesture, target_intent, target_payload)
        except ValueError as exc:
            LOGGER.info("Teach gesture rejected: %s", exc)
            self.activity.add(
                source=action.source or "system",
                kind="learned",
                text=f"teach failed · {gesture} → {target_intent} ({exc})",
                accent="warning",
            )
            self.voice_log.set_intent("teach_gesture", status=f"rejected: {exc}")
            return
        nice = self._friendly_intent(target_intent, target_payload)
        self.activity.add(
            source=action.source or "system",
            kind="learned",
            text=f"bound gesture {gesture} → {nice}",
            accent="ai",
        )
        self.voice_log.set_intent(
            "teach_gesture",
            {"gesture": gesture, "target_intent": target_intent, "target_payload": target_payload},
            status="learned",
        )
        LOGGER.info("Learned gesture binding %s → %s %s", gesture, target_intent, target_payload)

    def _apply_teach_phrase(self, action: ActionRequest) -> None:
        phrase = str(action.payload.get("phrase", "")).strip().lower()
        target_intent = str(action.payload.get("target_intent", "")).strip()
        target_payload = action.payload.get("target_payload") or {}
        if not phrase or not target_intent:
            LOGGER.info("Teach phrase ignored (missing fields): %s", action.payload)
            self.voice_log.set_intent("teach_phrase", status="invalid")
            return
        try:
            self.llm_agent.learn_phrase(phrase, target_intent, target_payload)
        except ValueError as exc:
            LOGGER.info("Teach phrase rejected: %s", exc)
            self.activity.add(
                source=action.source or "system",
                kind="learned",
                text=f"teach failed · “{phrase}” → {target_intent} ({exc})",
                accent="warning",
            )
            self.voice_log.set_intent("teach_phrase", status=f"rejected: {exc}")
            return
        nice = self._friendly_intent(target_intent, target_payload)
        self.activity.add(
            source=action.source or "system",
            kind="learned",
            text=f"bound phrase “{phrase}” → {nice}",
            accent="ai",
        )
        self.voice_log.set_intent(
            "teach_phrase",
            {"phrase": phrase, "target_intent": target_intent, "target_payload": target_payload},
            status="learned",
        )
        LOGGER.info("Learned phrase binding %r → %s %s", phrase, target_intent, target_payload)

    async def _resolve_action(self, action: ActionRequest) -> ActionRequest | None:
        if action.intent == "shutdown":
            self.stop_event.set()
            return None

        if action.intent.startswith("trial_"):
            self._apply_trial_action(action)
            return None

        # --- Learning / teaching intents (no motion) -------------------
        # These are how the user rewires the robot's behaviour at
        # runtime: "teach peace as rotate right" binds the peace-sign
        # gesture to rotate_right, "when I say zap do close claw" binds
        # a custom phrase, and "reset gestures" wipes user overrides.
        if action.intent == "teach_gesture":
            self._apply_teach_gesture(action)
            return None
        if action.intent == "teach_phrase":
            self._apply_teach_phrase(action)
            return None
        if action.intent == "reset_gestures":
            self.gesture_bindings.clear()
            self.activity.add(
                source=action.source or "system",
                kind="learned",
                text="reset all gesture mappings to defaults",
                accent="ai",
            )
            self.voice_log.set_intent("reset_gestures", status="ok")
            return None

        if action.intent == "vision_target":
            target = action.payload.get("target")
            if target is not None:
                self._vision_environment_update(
                    target,
                    self.latest_state,
                    None,
                )
            if self.is_sweeping:
                return None

        if self.pending_confirmation is not None and time.monotonic() - self.pending_confirmation_at > self.config.confirmation_timeout_s:
            LOGGER.info("Pending confirmation timed out for %s.", self.pending_confirmation.intent)
            self.memory_store.record_action(self.pending_confirmation, status="timed_out")
            self.pending_confirmation = None

        if action.intent == "confirm_yes":
            if self.pending_confirmation is None:
                LOGGER.info("Received confirmation without a pending action.")
                return None
            confirmed = self.pending_confirmation
            self.pending_confirmation = None
            self.memory_store.record_action(confirmed, status="confirmed")
            LOGGER.info("Confirmed action: %s", confirmed.intent)
            return confirmed

        if action.intent == "confirm_no":
            if self.pending_confirmation is not None:
                LOGGER.info("Cancelled pending action: %s", self.pending_confirmation.intent)
                self.memory_store.record_action(self.pending_confirmation, status="cancelled")
            self.pending_confirmation = None
            return None

        if action.intent == "spoken_text":
            text = str(action.payload.get("text", "")).strip()
            if not text:
                return None
            # Make sure the control panel sees typed-text commands too.
            if action.source == "panel":
                self.voice_log.set_heard(text, source="typed")
            resolved = await self.llm_agent.interpret_text(text, source=action.source)
            if resolved is None:
                adl = match_adl_phrase(text)
                if adl is not None:
                    from ai.adl_tasks import adl_to_action_request

                    shaped = adl_to_action_request(adl, source=action.source)
                    resolved = ActionRequest(
                        source=shaped["source"],
                        intent=shaped["intent"],
                        payload=shaped["payload"],
                        requires_confirmation=shaped["requires_confirmation"],
                    )
            if resolved is None:
                LOGGER.info("No planner action produced for spoken text: %s", text)
                self.voice_log.set_intent("", status="no match")
                return None
            LOGGER.info("LLM/heuristic resolved '%s' to %s.", text, resolved.intent)
            self.voice_log.set_intent(resolved.intent, resolved.payload, status="resolved")
            action = resolved
        elif action.intent not in {"vision_target", "shutdown", "confirm_yes", "confirm_no"}:
            # Any direct-match voice command should show up in the panel too.
            if action.source == "voice":
                self.voice_log.set_intent(action.intent, action.payload, status="matched")

        if action.requires_confirmation:
            self.pending_confirmation = action
            self.pending_confirmation_at = time.monotonic()
            self.pending_confirmation_message = self._confirmation_message_for(action)
            self.memory_store.record_action(action, status="awaiting_confirmation")
            LOGGER.info(
                "Awaiting confirmation for %s. %s",
                action.intent,
                self.pending_confirmation_message or "Say yes to continue, no to cancel.",
            )
            self.activity.add(
                source="system",
                kind="pending",
                text=self.pending_confirmation_message or f"awaiting yes/no → {self._friendly_intent(action.intent, action.payload)}",
                accent="warning",
            )
            return None

        return action

    def _confirmation_message_for(self, action: ActionRequest) -> str:
        adl_id = action.payload.get("adl_id")
        if adl_id:
            task = get_adl_task(str(adl_id))
            if task and task.confirmation_message:
                return task.confirmation_message
        if action.intent == "pick_object":
            label = action.payload.get("label", "the object")
            return f"I'll gently reach for {label}. Say yes to continue, or no to cancel."
        if action.intent == "place_object":
            return "I'll place the object down carefully. Say yes to continue, or no to cancel."
        return ""

    async def _fallback_advisor_loop(self) -> None:
        while not self.stop_event.is_set():
            await asyncio.sleep(15.0)
            if self.input_fallback is None:
                continue
            hint = self.input_fallback.suggest_fallback()
            if hint:
                self.activity.add(source="system", kind="hint", text=hint, accent="info")
                LOGGER.info("Input fallback hint: %s", hint)

    def _apply_trial_action(self, action: ActionRequest) -> None:
        payload = action.payload or {}
        if action.intent == "trial_start":
            mode = str(payload.get("mode") or "adaptive").strip()
            target = str(payload.get("target") or "target").strip()
            tremor = str(payload.get("tremor_simulated", "")).strip().lower() in {
                "1", "true", "yes", "on",
            }
            self.lab_trials.start(
                mode=mode,
                target=target,
                state=self.latest_state,
                motor_level=str(payload.get("motor_level") or self.config.motor_level),
                input_method=str(payload.get("input_method") or mode),
                object_type=str(payload.get("object_type") or target),
                tremor_simulated=tremor,
                note=str(payload.get("note") or ""),
            )
            self.activity.add(
                source="system",
                kind="trial",
                text=f"started {mode} trial · target={target}",
                accent="system",
            )
            return
        if action.intent == "trial_correction":
            note = str(payload.get("note") or "").strip()
            self.lab_trials.add_correction(note)
            self.activity.add(source="system", kind="trial", text="marked correction", accent="system")
            return
        if action.intent == "trial_note":
            note = str(payload.get("note") or "").strip()
            self.lab_trials.add_note(note)
            if note:
                self.activity.add(source="system", kind="trial", text=f"note · {note}", accent="system")
            return
        if action.intent in {"trial_success", "trial_failure"}:
            final_distance = payload.get("final_distance_cm")
            alignment_mm = payload.get("alignment_error_mm")
            distance_cm: float | None
            align_mm: float | None
            try:
                distance_cm = float(final_distance) if final_distance not in {None, ""} else None
            except (TypeError, ValueError):
                distance_cm = None
            try:
                align_mm = float(alignment_mm) if alignment_mm not in {None, ""} else None
            except (TypeError, ValueError):
                align_mm = None
            note = str(payload.get("note") or "").strip()
            success = action.intent == "trial_success"
            record = self.lab_trials.finish(
                success=success,
                state=self.latest_state,
                final_distance_cm=distance_cm,
                alignment_error_mm=align_mm,
                note=note,
            )
            if record is None:
                self.activity.add(
                    source="system",
                    kind="trial",
                    text="no active trial to finish",
                    accent="warning",
                )
                return
            outcome = "success" if success else "failure"
            self.activity.add(
                source="system",
                kind="trial",
                text=(
                    f"{outcome} · {record['duration_s']}s · "
                    f"{record['corrections']} corrections · log saved"
                ),
                accent="system",
            )

    async def _execute_plan(self, plan: PlannerResult) -> bool:
        if plan.kind == "NONE":
            return False

        if plan.kind == "STOP":
            self._drain_actions()
            ok = await self.serial_bridge.send_stop()
            LOGGER.info("EXECUTE STOP -> %s", "ok" if ok else "failed")
            return ok

        if plan.kind == "HOME":
            ok = await self.serial_bridge.send_home()
            LOGGER.info("EXECUTE HOME -> %s", "ok" if ok else "failed")
            return ok

        if plan.kind == "POSES":
            for index, command in enumerate(plan.commands):
                if self.stop_event.is_set():
                    return False
                LOGGER.info(
                    "EXECUTE POSE origin=%s base=%d lift=%d rotate=%d claw=%d speed=%d",
                    command.origin,
                    command.base_deg,
                    command.lift_deg,
                    command.rotate_deg,
                    command.claw_deg,
                    command.speed_pct,
                )
                ok = await self.serial_bridge.send_pose(command)
                LOGGER.info("EXECUTE RESULT %s -> %s", command.origin, "ok" if ok else "failed")
                if not ok:
                    return False
                if index < len(plan.commands) - 1:
                    settle_s = self._settle_delay_for_command(command)
                    if settle_s > 0:
                        LOGGER.info("EXECUTE WAIT %.2fs after %s", settle_s, command.origin)
                        await asyncio.sleep(settle_s)
            return True

        return False

    def _settle_delay_for_command(self, command) -> float:
        if command.origin.endswith(":upright"):
            return self.config.upright_stage_settle_s
        return self.config.pose_settle_s

    def record_plan_execution(self, action: ActionRequest, plan: PlannerResult, executed: bool) -> None:
        if executed and plan.kind not in {"NONE"}:
            self.session_command_count += 1
        if executed and plan.kind == "POSES":
            self.memory_store.record_execution(action, plan.commands)
        elif executed and plan.kind in {"STOP", "HOME"}:
            self.memory_store.record_action(action, status="executed", outcome={"kind": plan.kind})
        else:
            self.memory_store.record_action(
                action,
                status="failed",
                outcome={
                    "kind": plan.kind,
                    "serial_mode": self.serial_bridge.mode,
                    "port": self.serial_bridge.port_path or self.config.serial.port,
                },
            )

    def _drain_actions(self) -> None:
        while True:
            try:
                self.action_queue.get_nowait()
            except asyncio.QueueEmpty:
                return

    async def _startup_sweep(self) -> None:
        self.is_sweeping = True
        self.activity.add(
            source="system",
            kind="mapping",
            text="moving to upright survey pose for startup sweep",
            accent="system",
        )
        await self.action_queue.put(
            ActionRequest(source="system", intent="preset_pose", payload={"name": "survey"})
        )
        LOGGER.info("Beginning startup LiDAR & Vision mapping sweep from upright survey pose...")
        await asyncio.sleep(1.25)

        base_limits = self.config.servo_limits["base"]
        step_deg = 20
        for angle in range(base_limits.min_deg, base_limits.max_deg + 1, step_deg):
            if self.stop_event.is_set():
                break
            action = ActionRequest(source="system", intent="system_sweep", payload={"base_deg": angle})
            await self.action_queue.put(action)
            await asyncio.sleep(2.0)

        await self.action_queue.put(ActionRequest(source="system", intent="home"))
        await asyncio.sleep(1.5)
        self.is_sweeping = False
        self.activity.add(
            source="system",
            kind="mapping",
            text="startup sweep complete",
            accent="system",
        )
        LOGGER.info("Startup sweep complete. Environment mapped: %s", self.environment.known_labels)

    def _format_action(self, action: ActionRequest) -> str:
        payload = f" payload={action.payload}" if action.payload else ""
        confirm = " confirm=yes" if action.requires_confirmation else ""
        return f"source={action.source} intent={action.intent}{payload}{confirm}"

    def _describe_plan(self, plan: PlannerResult) -> str:
        if plan.kind != "POSES":
            return f"kind={plan.kind}"
        poses = [
            f"{cmd.origin}[b={cmd.base_deg} l={cmd.lift_deg} r={cmd.rotate_deg} c={cmd.claw_deg} s={cmd.speed_pct}]"
            for cmd in plan.commands
        ]
        return f"kind=POSES count={len(plan.commands)} {'; '.join(poses)}"
