import unittest

from inputs.depth import fuse_depth_with_lidar


class DepthFusionTests(unittest.TestCase):
    def test_lidar_direct_in_vision_extract(self) -> None:
        from unittest.mock import MagicMock

        from inputs.vision import _extract_target_with_depth
        from motion.calibration import default_calibration

        mock_box = MagicMock()
        mock_box.conf = [0.9]
        mock_box.cls = [0]
        mock_xyxy = MagicMock()
        mock_xyxy.tolist.return_value = [280.0, 200.0, 360.0, 280.0]
        mock_box.xyxy = [mock_xyxy]
        mock_result = MagicMock()
        mock_result.boxes = [mock_box]
        mock_result.names = {0: "bottle"}
        mock_result.orig_shape = (480, 640)
        import numpy as np

        depth_map = np.full((480, 640), 900.0, dtype=np.float32)
        target, _ = _extract_target_with_depth(
            [mock_result],
            depth_map=depth_map,
            calibration=default_calibration(),
            range_mm=450,
            center_tolerance=0.5,
            fuse_lidar=True,
        )
        self.assertIsNotNone(target)
        self.assertTrue(target.has_3d)
        self.assertAlmostEqual(target.camera_z_mm, 450.0, delta=80.0)

    def test_scales_toward_lidar(self) -> None:
        fused = fuse_depth_with_lidar(400.0, 600)
        self.assertGreater(fused, 500.0)
        self.assertLess(fused, 700.0)

    def test_invalid_inputs_passthrough(self) -> None:
        self.assertEqual(fuse_depth_with_lidar(-1.0, 500), -1.0)
        self.assertEqual(fuse_depth_with_lidar(400.0, 0), 400.0)


if __name__ == "__main__":
    unittest.main()
