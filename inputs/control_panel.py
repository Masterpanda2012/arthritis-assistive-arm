"""Control panel bridge.

The Tk GUI runs in a subprocess (``inputs/_control_panel_gui.py``) so it
owns the macOS main thread — something Cocoa/Tkinter requires. This
module is the async bridge that:

* spawns the GUI,
* streams serial/AI status + live arm state into it over stdin, and
* forwards button clicks into the orchestrator's action queue.

Using a subprocess also means OpenCV's webcam windows and this UI don't
fight for the HighGUI event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from models import ActionRequest, ArmState


LOGGER = logging.getLogger(__name__)


StatusFn = Callable[[], list[str]]
StateFn = Callable[[], tuple[ArmState, float]]
VoiceFn = Callable[[], dict]
TelemetryFn = Callable[[], dict]


_GUI_SCRIPT = Path(__file__).resolve().parent / "_control_panel_gui.py"


async def control_panel_loop(
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    *,
    status_fn: StatusFn | None = None,
    state_fn: StateFn | None = None,
    voice_fn: VoiceFn | None = None,
    telemetry_fn: TelemetryFn | None = None,
    accessibility_ui: bool = True,
) -> None:
    if not _GUI_SCRIPT.exists():
        LOGGER.warning("Control panel GUI script not found at %s; panel disabled.", _GUI_SCRIPT)
        return

    python_exe = sys.executable or "python3"
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["ROBOT_ARM_ACCESSIBILITY"] = "1" if accessibility_ui else "0"

    try:
        process = await asyncio.create_subprocess_exec(
            python_exe,
            str(_GUI_SCRIPT),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except Exception as exc:
        LOGGER.warning("Could not start control panel subprocess (%s); panel disabled.", exc)
        return

    LOGGER.info("Control panel (Tk) launched as PID %s.", process.pid)

    stderr_task = asyncio.create_task(_log_stderr(process), name="control-panel-stderr")
    sender_task = asyncio.create_task(
        _status_sender(process, stop_event, status_fn, state_fn, voice_fn, telemetry_fn),
        name="control-panel-status",
    )

    try:
        assert process.stdout is not None
        while not stop_event.is_set():
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=0.5)
            except asyncio.TimeoutError:
                if process.returncode is not None:
                    break
                continue
            if not line:
                break
            try:
                message = json.loads(line.decode("utf-8", errors="ignore").strip() or "{}")
            except json.JSONDecodeError:
                continue

            mtype = message.get("type")
            if mtype == "action":
                intent = str(message.get("intent", "")).strip()
                if not intent:
                    continue
                payload = message.get("payload") or {}
                requires_confirmation = intent in {"pick_object", "place_object"}
                if isinstance(payload, dict) and payload.get("adl_id"):
                    requires_confirmation = True
                action = ActionRequest(
                    source="panel",
                    intent=intent,
                    payload=dict(payload) if isinstance(payload, dict) else {},
                    requires_confirmation=requires_confirmation,
                )
                LOGGER.info("Manual control queued command: %s %s", action.intent, action.payload or {})
                await action_queue.put(action)
            elif mtype == "shutdown":
                LOGGER.info("Control panel window closed by user.")
                await action_queue.put(ActionRequest(source="panel", intent="shutdown"))
                return
    finally:
        sender_task.cancel()
        await asyncio.gather(sender_task, return_exceptions=True)
        if process.returncode is None:
            try:
                if process.stdin is not None and not process.stdin.is_closing():
                    process.stdin.write((json.dumps({"type": "quit"}) + "\n").encode("utf-8"))
                    await process.stdin.drain()
                    process.stdin.close()
            except Exception:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=1.5)
            except asyncio.TimeoutError:
                try:
                    process.terminate()
                except Exception:
                    pass
        stderr_task.cancel()
        await asyncio.gather(stderr_task, return_exceptions=True)


async def _status_sender(
    process: asyncio.subprocess.Process,
    stop_event: asyncio.Event,
    status_fn: StatusFn | None,
    state_fn: StateFn | None,
    voice_fn: VoiceFn | None,
    telemetry_fn: TelemetryFn | None,
) -> None:
    if process.stdin is None:
        return
    last_status: list[str] = []
    last_state_sent = 0.0
    last_voice_snapshot: dict = {}
    last_activity_id: int = 0
    last_serial_event_id: int = 0
    last_telemetry_sent = 0.0
    try:
        while not stop_event.is_set() and process.returncode is None:
            now = time.monotonic()
            try:
                if status_fn is not None:
                    lines = list(status_fn())
                    if lines != last_status:
                        await _write_line(process.stdin, {"type": "status", "lines": lines})
                        last_status = lines
                if state_fn is not None and now - last_state_sent > 0.25:
                    state, age = state_fn()
                    await _write_line(
                        process.stdin,
                        {
                            "type": "state",
                            "base": state.base_deg,
                            "lift": state.lift_deg,
                            "rotate": state.rotate_deg,
                            "claw": state.claw_deg,
                            "range_mm": state.range_mm,
                            "estop": bool(state.estop),
                            "age": round(float(age), 2),
                        },
                    )
                    last_state_sent = now
                if voice_fn is not None:
                    snapshot = voice_fn()
                    # Drop the freshness "age" from the comparison so we
                    # don't spam the GUI pipe with "age changed from 1.2
                    # to 1.4" updates.
                    comparable = {k: v for k, v in snapshot.items() if k != "age"}
                    if comparable != last_voice_snapshot:
                        await _write_line(
                            process.stdin,
                            {"type": "voice", **snapshot},
                        )
                        last_voice_snapshot = comparable
                if telemetry_fn is not None and now - last_telemetry_sent > 0.3:
                    tel = telemetry_fn()
                    events = tel.get("activity") or []
                    newest = max((int(e.get("id", 0)) for e in events), default=0)
                    serial_events = tel.get("serial_monitor") or []
                    newest_serial = max((int(e.get("id", 0)) for e in serial_events), default=0)
                    # Always send health + pending (cheap), but only the
                    # tail of the activity log if something changed.
                    activity_changed = newest != last_activity_id
                    serial_changed = newest_serial != last_serial_event_id
                    await _write_line(
                        process.stdin,
                        {
                            "type": "telemetry",
                            "activity": events if activity_changed else [],
                            "activity_changed": activity_changed,
                            "serial_monitor": serial_events if serial_changed else [],
                            "serial_changed": serial_changed,
                            "pending": tel.get("pending"),
                            "health": tel.get("health") or {},
                        },
                    )
                    last_activity_id = newest
                    last_serial_event_id = newest_serial
                    last_telemetry_sent = now
            except Exception as exc:
                LOGGER.debug("control-panel status send failed: %s", exc)
                return
            await asyncio.sleep(0.15)
    except asyncio.CancelledError:
        return


async def _write_line(stream: asyncio.StreamWriter, message: dict) -> None:
    data = (json.dumps(message) + "\n").encode("utf-8")
    stream.write(data)
    try:
        await stream.drain()
    except (ConnectionResetError, BrokenPipeError):
        return


async def _log_stderr(process: asyncio.subprocess.Process) -> None:
    if process.stderr is None:
        return
    try:
        async for raw in process.stderr:
            text = raw.decode("utf-8", errors="ignore").rstrip()
            if text:
                LOGGER.warning("[control-panel] %s", text)
    except Exception:
        return
