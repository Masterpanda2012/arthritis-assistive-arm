import asyncio
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from config import load_config
from models import ArmCommand
from motion.protocol import build_packet
from motion.serial_bridge import SerialBridge


class SerialBridgeSimulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_simulation_pose_updates_state(self) -> None:
        config = load_config()
        config = replace(config, features=replace(config.features, simulate_serial=True))
        state_queue: asyncio.Queue = asyncio.Queue()
        bridge = SerialBridge(config, state_queue)

        await bridge.start()
        await bridge.send_pose(ArmCommand(90, 110, 85, 100, 30, "test"))
        state = await state_queue.get()
        if state.base_deg == config.home_pose.base_deg and state.lift_deg == config.home_pose.lift_deg:
            state = await state_queue.get()

        self.assertEqual(state.lift_deg, 110)
        self.assertFalse(state.estop)
        await bridge.close()

    async def test_live_bridge_waits_for_state_and_ping_ack(self) -> None:
        config = load_config()
        config = replace(
            config,
            serial=replace(
                config.serial,
                connect_delay_s=0.0,
                startup_sync_timeout_s=0.2,
                timeout_s=0.01,
                ack_timeout_s=0.2,
            ),
        )
        state_queue: asyncio.Queue = asyncio.Queue()
        fake_serial = _FakeSerialPort(
            [
                build_packet(
                    "STATE",
                    config.home_pose.base_deg,
                    config.home_pose.lift_deg,
                    config.home_pose.rotate_deg,
                    config.home_pose.claw_deg,
                    -1,
                    0,
                ),
            ]
        )

        with patch("motion.serial_bridge.serial", SimpleNamespace(Serial=lambda *args, **kwargs: fake_serial)):
            bridge = SerialBridge(config, state_queue)
            await bridge.start()

        self.assertEqual(bridge.mode, "live")
        self.assertIn(build_packet("PING"), fake_serial.writes)
        monitor = bridge.monitor_snapshot()
        self.assertTrue(any(event["channel"] == "SYS" for event in monitor))
        self.assertTrue(any(event["channel"] == "ACK" and event["text"] == "PING" for event in monitor))
        await bridge.close()

    async def test_live_bridge_accepts_state_triggered_by_ping(self) -> None:
        config = load_config()
        config = replace(
            config,
            serial=replace(
                config.serial,
                connect_delay_s=0.0,
                startup_sync_timeout_s=0.2,
                timeout_s=0.01,
                ack_timeout_s=0.2,
            ),
        )
        state_queue: asyncio.Queue = asyncio.Queue()
        fake_serial = _FakeSerialPort(
            [],
            ping_state_packet=build_packet(
                "STATE",
                config.home_pose.base_deg,
                config.home_pose.lift_deg,
                config.home_pose.rotate_deg,
                config.home_pose.claw_deg,
                420,
                0,
            ),
        )

        with patch("motion.serial_bridge.serial", SimpleNamespace(Serial=lambda *args, **kwargs: fake_serial)):
            bridge = SerialBridge(config, state_queue)
            await bridge.start()

        self.assertEqual(bridge.mode, "live")
        state = await asyncio.wait_for(state_queue.get(), timeout=0.1)
        self.assertEqual(state.range_mm, 420)
        await bridge.close()

    async def test_missing_handshake_falls_back_to_simulation(self) -> None:
        config = load_config()
        config = replace(
            config,
            serial=replace(
                config.serial,
                connect_delay_s=0.0,
                startup_sync_timeout_s=0.1,
                timeout_s=0.01,
                ack_timeout_s=0.1,
            ),
        )
        state_queue: asyncio.Queue = asyncio.Queue()
        fake_serial = _FakeSerialPort([])

        with patch("motion.serial_bridge.serial", SimpleNamespace(Serial=lambda *args, **kwargs: fake_serial)):
            bridge = SerialBridge(config, state_queue)
            await bridge.start()

        self.assertTrue(bridge.simulate)
        self.assertEqual(bridge.mode, "simulation")
        self.assertTrue(any(event["channel"] == "SIM" for event in bridge.monitor_snapshot()))
        await bridge.close()


class _FakeSerialPort:
    def __init__(self, responses: list[str], ping_state_packet: str | None = None) -> None:
        self._responses = [response.encode("ascii") for response in responses]
        self.writes: list[str] = []
        self.closed = False
        self._ping_state_packet = ping_state_packet

    def write(self, data: bytes) -> int:
        decoded = data.decode("ascii")
        self.writes.append(decoded)
        if decoded == build_packet("PING"):
            self._responses.append(build_packet("ACK", "PING").encode("ascii"))
            if self._ping_state_packet is not None:
                self._responses.append(self._ping_state_packet.encode("ascii"))
        return len(data)

    def read_until(self, separator: bytes = b">") -> bytes:
        if self._responses:
            return self._responses.pop(0)
        return b""

    def close(self) -> None:
        self.closed = True

    def reset_input_buffer(self) -> None:
        return None

    def reset_output_buffer(self) -> None:
        return None
