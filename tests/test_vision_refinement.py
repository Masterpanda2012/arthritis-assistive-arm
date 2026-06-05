import unittest
from unittest.mock import MagicMock, patch
import asyncio
import time
from dataclasses import replace

from config import load_config
from models import ArmState, VisionTarget
from inputs.vision import _extract_target_with_depth, vision_loop


class TestVisionRefinement(unittest.TestCase):
    def test_extract_target_with_depth(self) -> None:
        # Mock ultralytics YOLO prediction results
        mock_box = MagicMock()
        mock_box.conf = [0.85]
        mock_box.cls = [0]
        mock_xyxy = MagicMock()
        mock_xyxy.tolist.return_value = [100.0, 100.0, 200.0, 200.0]
        mock_box.xyxy = [mock_xyxy]

        mock_result = MagicMock()
        mock_result.boxes = [mock_box]
        mock_result.names = {0: "bottle"}
        mock_result.orig_shape = (480, 640)

        # 1. Test target extraction without depth
        target, counts = _extract_target_with_depth(
            [mock_result],
            depth_map=None,
            calibration=None,
            range_mm=-1,
            center_tolerance=0.5,
        )
        self.assertIsNotNone(target)
        self.assertEqual(target.label, "bottle")
        self.assertEqual(target.confidence, 0.85)
        self.assertFalse(target.has_3d)

    @patch("inputs.vision.YOLO")
    @patch("inputs.vision.DepthEstimator")
    async def async_test_vision_loop_logic(self, mock_depth_est_cls, mock_yolo_cls) -> None:
        # We will test the depth caching and smoothing logic within vision_loop
        config = load_config()
        config = replace(
            config,
            features=replace(config.features, enable_depth=True),
        )

        mock_model = MagicMock()
        mock_yolo_cls.return_value = mock_model

        mock_depth_estimator = MagicMock()
        mock_depth_estimator.status_summary.return_value = "depth: mock"
        # Return a simple 2D list/array for depth map
        import numpy as np
        dummy_depth = np.full((480, 640), 500.0, dtype=np.float32)
        mock_depth_estimator.estimate_depth_mm.return_value = dummy_depth
        mock_depth_est_cls.return_value = mock_depth_estimator

        action_queue = asyncio.Queue()
        stop_event = asyncio.Event()

        # State provider
        state = ArmState(90, 120, 90, 100, range_mm=520, estop=False, last_error="")
        state_provider = lambda: (state, 0.0)

        # Mock frame queue
        frame_queue = asyncio.Queue()
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        for _ in range(3):
            await frame_queue.put(frame)

        # Setup model predicts
        mock_box = MagicMock()
        mock_box.conf = [0.9]
        mock_box.cls = [0]
        mock_xyxy = MagicMock()
        mock_xyxy.tolist.return_value = [100.0, 100.0, 200.0, 200.0]
        mock_box.xyxy = [mock_xyxy]
        mock_result = MagicMock()
        mock_result.boxes = [mock_box]
        mock_result.names = {0: "bottle"}
        mock_result.orig_shape = (480, 640)
        mock_model.predict.return_value = [mock_result]

        # Run loop for a short duration
        task = asyncio.create_task(
            vision_loop(
                config,
                action_queue,
                stop_event,
                state_provider,
                frame_queue,
            )
        )

        # Wait a small bit for frame processing
        await asyncio.sleep(0.15)
        stop_event.set()
        await task

        # Check that items were queued
        self.assertGreater(action_queue.qsize(), 0)
        action = await action_queue.get()
        self.assertEqual(action.intent, "pick_object")
        target_payload = action.payload.get("target")
        self.assertIsNotNone(target_payload)
        self.assertEqual(target_payload.label, "bottle")
        self.assertTrue(target_payload.has_3d)

    def test_vision_loop_wrapper(self) -> None:
        asyncio.run(self.async_test_vision_loop_logic())


if __name__ == "__main__":
    unittest.main()
