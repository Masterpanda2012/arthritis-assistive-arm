from __future__ import annotations

import asyncio
import glob
import logging
import time
from collections import defaultdict, deque
from typing import DefaultDict

from config import RuntimeConfig
from models import ArmCommand, ArmState
from motion.protocol import build_packet, parse_packet

try:
    import serial
    from serial import SerialException
except ImportError:  # pragma: no cover
    serial = None
    SerialException = Exception


LOGGER = logging.getLogger(__name__)


class SerialBridge:
    def __init__(self, config: RuntimeConfig, state_queue: asyncio.Queue[ArmState]) -> None:
        self.config = config
        self.state_queue = state_queue
        self._serial = None
        self._reader_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()
        self._last_send = 0.0
        self._last_error = ""
        self._closed = False
        self._simulate = config.features.simulate_serial or serial is None
        self._mode = "simulation" if self._simulate else "disconnected"
        self._port_path = ""
        self._ack_waiters: DefaultDict[str, list[asyncio.Future[bool]]] = defaultdict(list)
        self._state_seen_event: asyncio.Event | None = None
        self._monitor_events: deque[dict] = deque(maxlen=160)
        self._monitor_seq = 0
        self._last_state_monitor_at = 0.0
        self._sim_state = ArmState(
            base_deg=config.home_pose.base_deg,
            lift_deg=config.home_pose.lift_deg,
            rotate_deg=config.home_pose.rotate_deg,
            claw_deg=config.home_pose.claw_deg,
            range_mm=-1,
            estop=False,
            last_error="",
        )

    @property
    def simulate(self) -> bool:
        return self._simulate

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def port_path(self) -> str:
        return self._port_path

    def monitor_snapshot(self, limit: int = 60) -> list[dict]:
        now = time.monotonic()
        items = list(self._monitor_events)[-limit:]
        return [
            {
                **event,
                "age_s": round(now - float(event["ts"]), 1),
            }
            for event in items
        ]

    def _record_monitor(self, channel: str, text: str, *, level: str = "info") -> None:
        self._monitor_seq += 1
        self._monitor_events.append(
            {
                "id": self._monitor_seq,
                "ts": time.monotonic(),
                "channel": channel,
                "text": text,
                "level": level,
            }
        )

    def _candidate_ports(self) -> list[str]:
        candidates: list[str] = []
        configured = self.config.serial.port.strip()
        if configured:
            candidates.append(configured)
        for pattern in ("/dev/cu.usbmodem*", "/dev/cu.usbserial*", "/dev/tty.usbmodem*", "/dev/tty.usbserial*", "/dev/ttyACM*", "/dev/ttyUSB*"):
            for path in sorted(glob.glob(pattern)):
                if path not in candidates:
                    candidates.append(path)
        return candidates

    async def start(self) -> None:
        self._closed = False
        if self._simulate:
            self._mode = "simulation"
            reason = "explicit --simulation flag" if self.config.features.simulate_serial else "pyserial is unavailable"
            LOGGER.warning("Serial bridge running in simulation mode (%s).", reason)
            self._record_monitor("SIM", f"simulation mode enabled ({reason})", level="warn")
            await self._push_state(self._sim_state)
            return

        candidates = self._candidate_ports()
        if not candidates:
            LOGGER.error(
                "No serial candidates found. Connect the Arduino and check the configured port %s.",
                self.config.serial.port,
            )
            self._mode = "disconnected"
            return

        LOGGER.info("Serial candidates: %s", ", ".join(candidates))
        last_error: Exception | None = None
        for port in candidates:
            try:
                LOGGER.info("Opening serial port %s at %d baud...", port, self.config.serial.baud)
                self._serial = serial.Serial(
                    port,
                    self.config.serial.baud,
                    timeout=self.config.serial.timeout_s,
                )
                self._port_path = port
                await asyncio.sleep(self.config.serial.connect_delay_s)
                await self._reset_buffers()
                self._state_seen_event = asyncio.Event()
                self._reader_task = asyncio.create_task(self._reader_loop(), name="serial-reader")
                if not await self.ping(retries=3):
                    raise RuntimeError("firmware did not ACK PING after connect")
                if not await self._await_initial_state():
                    raise RuntimeError("firmware did not emit a STATE packet after connect")
                self._mode = "live"
                self._record_monitor("SYS", f"connected on {port}", level="ok")
                LOGGER.info("Serial bridge connected on %s with live firmware handshake.", port)
                return
            except Exception as exc:
                last_error = exc
                LOGGER.warning("Unable to open serial port %s (%s).", port, exc)
                if self._serial is not None:
                    try:
                        await asyncio.to_thread(self._serial.close)
                    except Exception:
                        pass
                    self._serial = None
                if self._reader_task is not None:
                    self._reader_task.cancel()
                    await asyncio.gather(self._reader_task, return_exceptions=True)
                    self._reader_task = None
                self._state_seen_event = None

        self._mode = "disconnected"
        LOGGER.error(
            "Serial bridge is disconnected. Tried: %s. Last error: %s",
            ", ".join(candidates),
            last_error,
        )
        if getattr(self.config, "auto_simulate_on_serial_fail", True):
            self._simulate = True
            self._mode = "simulation"
            self._record_monitor(
                "SIM",
                f"live handshake failed on {self.config.serial.port}; using simulation",
                level="warn",
            )
            LOGGER.warning(
                "No live Arduino handshake succeeded; running in SIMULATION mode so panel/voice/gesture still "
                "update a virtual arm state only. Plug in the Arduino, flash the packet firmware, and restart, "
                "or set ROBOT_ARM_PORT. "
                "Use --no-auto-simulate or ROBOT_ARM_AUTO_SIMULATE=0 to require real hardware."
            )
            await self._push_state(self._sim_state)

    async def close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        if self._serial is not None:
            await asyncio.to_thread(self._serial.close)
            self._serial = None
        self._state_seen_event = None
        if self._mode == "live":
            self._mode = "disconnected"

    async def send_pose(self, command: ArmCommand) -> bool:
        packet = build_packet(
            "POSE",
            command.base_deg,
            command.lift_deg,
            command.rotate_deg,
            command.claw_deg,
            command.speed_pct,
        )
        ok = await self._send_packet(packet, expect_ack="POSE")
        if self._simulate and ok:
            self._sim_state = ArmState(
                base_deg=command.base_deg,
                lift_deg=command.lift_deg,
                rotate_deg=command.rotate_deg,
                claw_deg=command.claw_deg,
                range_mm=self._sim_state.range_mm,
                estop=False,
                last_error="",
            )
            await self._push_state(self._sim_state)
        return ok

    async def send_init(self) -> bool:
        ok = await self._send_packet(build_packet("INIT"), expect_ack="INIT")
        if self._simulate and ok:
            self._sim_state = ArmState(
                base_deg=self.config.home_pose.base_deg,
                lift_deg=self.config.home_pose.lift_deg,
                rotate_deg=self.config.home_pose.rotate_deg,
                claw_deg=self.config.home_pose.claw_deg,
                range_mm=self._sim_state.range_mm,
                estop=False,
                last_error="",
            )
            await self._push_state(self._sim_state)
        return ok

    async def send_home(self) -> bool:
        ok = await self._send_packet(build_packet("HOME"), expect_ack="HOME")
        if self._simulate and ok:
            self._sim_state = ArmState(
                base_deg=self.config.home_pose.base_deg,
                lift_deg=self.config.home_pose.lift_deg,
                rotate_deg=self.config.home_pose.rotate_deg,
                claw_deg=self.config.home_pose.claw_deg,
                range_mm=self._sim_state.range_mm,
                estop=False,
                last_error="",
            )
            await self._push_state(self._sim_state)
        return ok

    async def send_stop(self) -> bool:
        ok = await self._send_packet(build_packet("STOP"), expect_ack="STOP")
        if self._simulate and ok:
            self._sim_state = ArmState(
                base_deg=self._sim_state.base_deg,
                lift_deg=self._sim_state.lift_deg,
                rotate_deg=self._sim_state.rotate_deg,
                claw_deg=self._sim_state.claw_deg,
                range_mm=self._sim_state.range_mm,
                estop=True,
                last_error="",
            )
            await self._push_state(self._sim_state)
        return ok

    async def ping(self, retries: int = 2) -> bool:
        for _ in range(retries + 1):
            if await self._send_packet(build_packet("PING"), expect_ack="PING"):
                return True
        return False

    async def _send_packet(self, packet: str, expect_ack: str | None = None) -> bool:
        max_attempts = 2 if expect_ack and not self._simulate else 1
        for attempt in range(1, max_attempts + 1):
            async with self._write_lock:
                now = time.monotonic()
                delta = now - self._last_send
                if delta < self.config.serial.min_command_interval_s:
                    await asyncio.sleep(self.config.serial.min_command_interval_s - delta)

                if self._simulate:
                    LOGGER.info("[SIM SEND] %s", packet)
                    self._record_monitor("SIM-TX", packet, level="warn")
                    self._last_send = time.monotonic()
                    if expect_ack:
                        await self._resolve_ack(expect_ack)
                        self._record_monitor("SIM-RX", f"ACK {expect_ack}", level="warn")
                    return True

                if self._serial is None:
                    LOGGER.warning("[SERIAL %s] dropping packet %s", self._mode.upper(), packet)
                    self._record_monitor("DROP", packet, level="bad")
                    return False

                future: asyncio.Future[bool] | None = None
                if expect_ack:
                    future = asyncio.get_running_loop().create_future()
                    self._ack_waiters[expect_ack].append(future)

                try:
                    await asyncio.to_thread(self._serial.write, packet.encode("ascii"))
                    if hasattr(self._serial, "flush"):
                        await asyncio.to_thread(self._serial.flush)
                    self._last_send = time.monotonic()
                    LOGGER.info("[SEND] %s", packet)
                    self._record_monitor("TX", packet)
                except Exception as exc:
                    if future is not None:
                        future.cancel()
                        self._discard_ack_waiter(expect_ack, future)
                    LOGGER.error("Serial write failed: %s", exc)
                    self._record_monitor("ERR", f"write failed: {exc}", level="bad")
                    self._mode = "disconnected"
                    return False

            if future is None:
                return True

            try:
                return await asyncio.wait_for(future, timeout=self.config.serial.ack_timeout_s)
            except asyncio.TimeoutError:
                future.cancel()
                self._discard_ack_waiter(expect_ack, future)
                if attempt < max_attempts:
                    LOGGER.warning(
                        "Timed out waiting for ACK %s after sending %s; retrying (%d/%d).",
                        expect_ack,
                        packet,
                        attempt + 1,
                        max_attempts,
                    )
                    self._record_monitor(
                        "RETRY",
                        f"ACK {expect_ack} retry {attempt + 1} for {packet}",
                        level="warn",
                    )
                    continue
                LOGGER.warning("Timed out waiting for ACK %s after sending %s", expect_ack, packet)
                self._record_monitor("TIMEOUT", f"ACK {expect_ack} after {packet}", level="bad")
                return False
        return False

    async def _resolve_ack(self, command: str) -> None:
        waiters = self._ack_waiters.pop(command, [])
        for future in waiters:
            if not future.done():
                future.set_result(True)

    def _discard_ack_waiter(self, command: str | None, future: asyncio.Future[bool]) -> None:
        if not command:
            return
        waiters = self._ack_waiters.get(command)
        if not waiters:
            return
        try:
            waiters.remove(future)
        except ValueError:
            return
        if not waiters:
            self._ack_waiters.pop(command, None)

    async def _await_initial_state(self) -> bool:
        if self._state_seen_event is None:
            return False
        if self._state_seen_event.is_set():
            return True
        try:
            await asyncio.wait_for(
                self._state_seen_event.wait(),
                timeout=self.config.serial.startup_sync_timeout_s,
            )
            return True
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Timed out waiting %.2fs for an initial STATE packet from %s.",
                self.config.serial.startup_sync_timeout_s,
                self._port_path or self.config.serial.port,
            )
            return False

    async def _reader_loop(self) -> None:
        while not self._closed and self._serial is not None:
            try:
                packet = await asyncio.to_thread(self._read_packet_blocking)
            except Exception as exc:
                LOGGER.warning("Serial read failed: %s", exc)
                self._record_monitor("ERR", f"read failed: {exc}", level="bad")
                broken_serial = self._serial
                self._serial = None
                self._mode = "disconnected"
                if broken_serial is not None:
                    try:
                        await asyncio.to_thread(broken_serial.close)
                    except Exception:
                        pass
                break

            if not packet:
                await asyncio.sleep(0.02)
                continue

            try:
                parsed = parse_packet(packet)
            except ValueError as exc:
                LOGGER.warning("Ignoring malformed packet %r (%s)", packet, exc)
                continue

            if parsed.command == "ACK" and parsed.fields:
                LOGGER.info("[ACK] %s", parsed.fields[0])
                self._record_monitor("ACK", str(parsed.fields[0]), level="ok")
                await self._resolve_ack(parsed.fields[0])
                continue

            if parsed.command == "STATE" and len(parsed.fields) >= 6:
                try:
                    state = ArmState(
                        base_deg=int(parsed.fields[0]),
                        lift_deg=int(parsed.fields[1]),
                        rotate_deg=int(parsed.fields[2]),
                        claw_deg=int(parsed.fields[3]),
                        range_mm=int(parsed.fields[4]),
                        estop=bool(int(parsed.fields[5])),
                        last_error=self._last_error,
                    )
                except ValueError:
                    LOGGER.warning("Ignoring STATE packet with non-numeric fields: %s", parsed.fields)
                    continue
                now = time.monotonic()
                if now - self._last_state_monitor_at >= 0.9:
                    self._record_monitor(
                        "STATE",
                        (
                            f"b={state.base_deg} l={state.lift_deg} r={state.rotate_deg} "
                            f"c={state.claw_deg} range={state.range_mm} estop={int(state.estop)}"
                        ),
                    )
                    self._last_state_monitor_at = now
                if self._state_seen_event is not None:
                    self._state_seen_event.set()
                await self._push_state(state)
                continue

            if parsed.command == "ERR" and parsed.fields:
                self._last_error = parsed.fields[0]
                detail = ",".join(parsed.fields)
                self._record_monitor("ERR", detail, level="bad")
                LOGGER.error("Mega reported error: %s", self._last_error)
                continue

            if parsed.command == "DBG" and parsed.fields:
                topic = str(parsed.fields[0])
                detail = ",".join(str(field) for field in parsed.fields[1:]) if len(parsed.fields) > 1 else ""
                text = f"{topic} {detail}".strip()
                level = "info"
                if topic == "RXERR":
                    level = "bad"
                elif topic == "LIDAR":
                    level = "ok" if detail == "ONLINE" else "warn"
                elif topic == "BOOT":
                    level = "ok"
                self._record_monitor("MCU", text, level=level)

    def _read_packet_blocking(self) -> str:
        assert self._serial is not None
        raw = self._serial.read_until(b">")
        if not raw:
            return ""

        text = raw.decode("ascii", errors="ignore")
        start = text.find("<")
        end = text.rfind(">")
        if start == -1 or end == -1 or end <= start:
            return ""
        return text[start : end + 1]

    async def _push_state(self, state: ArmState) -> None:
        self._sim_state = state
        await self.state_queue.put(state)

    async def _reset_buffers(self) -> None:
        if self._serial is None:
            return
        try:
            if hasattr(self._serial, "reset_input_buffer"):
                await asyncio.to_thread(self._serial.reset_input_buffer)
            if hasattr(self._serial, "reset_output_buffer"):
                await asyncio.to_thread(self._serial.reset_output_buffer)
        except Exception as exc:
            LOGGER.debug("Serial buffer reset skipped: %s", exc)
