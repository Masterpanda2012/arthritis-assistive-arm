import unittest

from config import load_config
from models import ActionRequest, ArmState
from motion.planner import MotionPlanner


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.planner = MotionPlanner(self.config)
        self.state = ArmState(90, 105, 90, 95, -1, False, "")

    def test_open_claw_uses_claw_max(self) -> None:
        result = self.planner.plan(ActionRequest(source="test", intent="open_claw"), self.state)
        self.assertEqual(result.kind, "POSES")
        self.assertEqual(result.commands[0].claw_deg, self.config.servo_limits["claw"].max_deg)

    def test_lift_up_clamps_to_safe_limits(self) -> None:
        high_state = ArmState(90, self.config.servo_limits["lift"].min_deg, 90, 95, -1, False, "")
        result = self.planner.plan(ActionRequest(source="test", intent="lift_up"), high_state)
        self.assertGreaterEqual(result.commands[0].lift_deg, self.config.servo_limits["lift"].min_deg)

    def test_home_returns_home_plan(self) -> None:
        result = self.planner.plan(ActionRequest(source="test", intent="home"), self.state)
        self.assertEqual(result.kind, "HOME")

    def test_pick_object_fallback_sequence(self) -> None:
        result = self.planner.plan(ActionRequest(source="test", intent="pick_object", payload={"label": "cup"}), self.state)
        self.assertEqual(result.kind, "POSES")
        self.assertGreaterEqual(len(result.commands), 4)

    def test_system_sweep_generates_absolute_base_pose(self) -> None:
        result = self.planner.plan(ActionRequest(source="system", intent="system_sweep", payload={"base_deg": 150}), self.state)
        self.assertEqual(result.kind, "POSES")
        self.assertEqual(result.commands[-1].base_deg, 150)

    def test_base_rotation_stages_upright_lift_first(self) -> None:
        lowered_state = ArmState(90, 120, 90, 95, -1, False, "")
        result = self.planner.plan(ActionRequest(source="test", intent="base_right"), lowered_state)
        self.assertEqual(result.kind, "POSES")
        self.assertEqual(len(result.commands), 2)
        self.assertEqual(result.commands[0].lift_deg, 90)
        self.assertEqual(result.commands[0].base_deg, 90)
        self.assertEqual(result.commands[1].lift_deg, 90)
        self.assertEqual(result.commands[1].base_deg, 102)

    def test_precision_step_and_speed_payloads_adjust_manual_jog(self) -> None:
        result = self.planner.plan(
            ActionRequest(
                source="panel",
                intent="base_right",
                payload={"step_deg": 4, "speed_pct": 20},
            ),
            self.state,
        )
        self.assertEqual(result.kind, "POSES")
        self.assertEqual(result.commands[-1].base_deg, 94)
        self.assertEqual(result.commands[-1].speed_pct, 20)

    def test_system_sweep_stages_upright_before_base_rotation(self) -> None:
        lowered_state = ArmState(90, 135, 90, 95, -1, False, "")
        result = self.planner.plan(
            ActionRequest(source="system", intent="system_sweep", payload={"base_deg": 150}),
            lowered_state,
        )
        self.assertEqual(result.kind, "POSES")
        self.assertEqual(len(result.commands), 2)
        self.assertEqual(result.commands[0].lift_deg, 90)
        self.assertEqual(result.commands[1].lift_deg, 90)
        self.assertEqual(result.commands[1].base_deg, 150)

    def test_survey_pose_is_available_for_startup_mapping(self) -> None:
        result = self.planner.plan(
            ActionRequest(source="system", intent="preset_pose", payload={"name": "survey"}),
            self.state,
        )
        self.assertEqual(result.kind, "POSES")
        self.assertEqual(result.commands[0].lift_deg, self.config.survey_pose.lift_deg)
        self.assertEqual(result.commands[0].lift_deg, 90)
