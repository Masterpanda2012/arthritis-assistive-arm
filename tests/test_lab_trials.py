import json
import tempfile
import unittest
from pathlib import Path

from ai.lab_trials import LabTrialLogger, RECOMMENDED_TRIALS_PER_CONDITION
from models import ActionRequest, ArmCommand, ArmState, PlannerResult, VisionTarget


class LabTrialLoggerTests(unittest.TestCase):
    def test_finish_records_arthritis_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trials.jsonl"
            logger = LabTrialLogger(path, motor_level="moderate")
            state = ArmState(90, 100, 90, 100, -1, False, "")
            logger.start(
                mode="dual_perception",
                target="water bottle",
                state=state,
                tremor_simulated=False,
                motor_level="moderate",
            )
            target = VisionTarget(
                label="bottle",
                confidence=0.9,
                image_x=0.5,
                image_y=0.5,
                range_mm=200,
                timestamp=0.0,
                has_3d=True,
            )
            action = ActionRequest(
                source="vision",
                intent="pick_object",
                payload={"target": target, "adl_id": "water"},
            )
            plan = PlannerResult(kind="POSES", commands=())
            logger.record_command(action, plan, True, state)
            record = logger.finish(
                success=True,
                state=state,
                final_distance_cm=2.0,
                alignment_error_mm=15.0,
            )
            assert record is not None
            self.assertTrue(record["used_3d_vision"])
            self.assertEqual(record["motor_level"], "moderate")
            self.assertEqual(record["alignment_error_mm"], 15.0)
            rows = path.read_text().strip().splitlines()
            self.assertEqual(len(rows), 1)
            parsed = json.loads(rows[0])
            self.assertEqual(parsed["mode"], "dual_perception")

    def test_recommended_sample_size(self) -> None:
        self.assertGreaterEqual(RECOMMENDED_TRIALS_PER_CONDITION, 20)


if __name__ == "__main__":
    unittest.main()
