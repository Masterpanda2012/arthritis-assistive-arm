import unittest

from inputs.depth import fuse_depth_with_lidar


class DepthFusionTests(unittest.TestCase):
    def test_scales_toward_lidar(self) -> None:
        fused = fuse_depth_with_lidar(400.0, 600)
        self.assertGreater(fused, 500.0)
        self.assertLess(fused, 700.0)

    def test_invalid_inputs_passthrough(self) -> None:
        self.assertEqual(fuse_depth_with_lidar(-1.0, 500), -1.0)
        self.assertEqual(fuse_depth_with_lidar(400.0, 0), 400.0)


if __name__ == "__main__":
    unittest.main()
