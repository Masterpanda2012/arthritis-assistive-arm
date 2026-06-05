import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from config import load_config
from models import ActionRequest, ArmCommand, PlannerResult
from orchestrator import AdaptiveRobotArmApp


class OrchestratorExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_hardware_execution_is_recorded_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config()
            config = replace(
                config,
                memory_db_path=Path(tmpdir) / "memory.db",
                features=replace(
                    config.features,
                    enable_gesture=False,
                    enable_voice=False,
                    enable_vision=False,
                    enable_control_panel=False,
                ),
            )
            app = AdaptiveRobotArmApp(config)
            app.serial_bridge._mode = "disconnected"

            async def _fail_home() -> bool:
                return False

            app.serial_bridge.send_home = _fail_home  # type: ignore[method-assign]
            action = ActionRequest(source="voice", intent="home")
            plan = PlannerResult(kind="HOME")

            executed = await app._execute_plan(plan)
            app.record_plan_execution(action, plan, executed)

            self.assertFalse(executed)
            with app.memory_store._connect() as conn:
                row = conn.execute(
                    "SELECT status, outcome_json FROM command_log ORDER BY id DESC LIMIT 1"
                ).fetchone()

            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(row["status"], "failed")
            self.assertIn("disconnected", row["outcome_json"])

    async def test_telemetry_exposes_serial_monitor_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config()
            config = replace(
                config,
                memory_db_path=Path(tmpdir) / "memory.db",
                features=replace(
                    config.features,
                    enable_gesture=False,
                    enable_voice=False,
                    enable_vision=False,
                    enable_control_panel=False,
                ),
            )
            app = AdaptiveRobotArmApp(config)
            app.serial_bridge._record_monitor("TX", "<PING*46>")

            telemetry = app.telemetry_snapshot()

            self.assertIn("serial_monitor", telemetry)
            self.assertEqual(telemetry["serial_monitor"][-1]["channel"], "TX")

    async def test_execute_plan_waits_after_upright_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config()
            config = replace(
                config,
                memory_db_path=Path(tmpdir) / "memory.db",
                features=replace(
                    config.features,
                    enable_gesture=False,
                    enable_voice=False,
                    enable_vision=False,
                    enable_control_panel=False,
                ),
                pose_settle_s=0.2,
                upright_stage_settle_s=1.1,
            )
            app = AdaptiveRobotArmApp(config)
            app.serial_bridge.send_pose = AsyncMock(return_value=True)  # type: ignore[method-assign]
            plan = PlannerResult(
                kind="POSES",
                commands=(
                    ArmCommand(90, 90, 90, 100, 35, "panel:upright"),
                    ArmCommand(102, 90, 90, 100, 35, "panel"),
                ),
            )

            with patch("orchestrator.asyncio.sleep", new=AsyncMock()) as sleep_mock:
                executed = await app._execute_plan(plan)

            self.assertTrue(executed)
            self.assertEqual(app.serial_bridge.send_pose.await_count, 2)
            sleep_mock.assert_awaited_once_with(1.1)

    async def test_trial_actions_log_duration_corrections_and_distance(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            config = load_config()
            config = replace(
                config,
                memory_db_path=Path(tmpdir) / "memory.db",
                features=replace(
                    config.features,
                    enable_gesture=False,
                    enable_voice=False,
                    enable_vision=False,
                    enable_control_panel=False,
                ),
            )
            app = AdaptiveRobotArmApp(config)

            await app._resolve_action(
                ActionRequest(
                    source="panel",
                    intent="trial_start",
                    payload={"mode": "manual", "target": "cup"},
                )
            )
            plan = PlannerResult(kind="POSES", commands=(ArmCommand(94, 90, 90, 100, 20, "panel"),))
            app.lab_trials.record_command(
                ActionRequest(source="panel", intent="base_right", payload={"step_deg": 4}),
                plan,
                True,
                app.latest_state,
            )
            await app._resolve_action(
                ActionRequest(
                    source="panel",
                    intent="trial_success",
                    payload={"final_distance_cm": "2.5", "note": "clean reach"},
                )
            )

            rows = app.lab_trials.path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(rows), 1)
            record = json.loads(rows[0])
            self.assertEqual(record["mode"], "manual")
            self.assertEqual(record["target"], "cup")
            self.assertTrue(record["success"])
            self.assertEqual(record["corrections"], 1)
            self.assertEqual(record["commands"], 1)
            self.assertEqual(record["final_distance_cm"], 2.5)


class SourceEnablementTests(unittest.TestCase):
    def test_web_voice_allowed_when_only_voice_enabled(self) -> None:
        from ai.user_profile import UserProfile, MotorLevel

        config = load_config()
        app = AdaptiveRobotArmApp(config)
        app.user_profile = UserProfile(
            motor_level=MotorLevel.MODERATE,
            enable_voice_input=True,
            enable_manual_input=False,
        )
        self.assertTrue(app.is_source_enabled("web"))
        self.assertFalse(app.is_source_enabled("panel"))

    def test_web_blocked_when_voice_and_manual_off(self) -> None:
        from ai.user_profile import UserProfile, MotorLevel

        config = load_config()
        app = AdaptiveRobotArmApp(config)
        app.user_profile = UserProfile(
            motor_level=MotorLevel.MODERATE,
            enable_voice_input=False,
            enable_manual_input=False,
        )
        self.assertFalse(app.is_source_enabled("web"))
