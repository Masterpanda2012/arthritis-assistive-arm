import unittest

from motion.lidar_filter import LidarFilter


class LidarFilterTests(unittest.TestCase):
    def test_rejects_zero_and_out_of_range(self) -> None:
        filt = LidarFilter()
        self.assertEqual(filt.ingest(0), -1)
        self.assertEqual(filt.ingest(50), -1)
        self.assertEqual(filt.ingest(9000), -1)

    def test_median_smooths_spikes(self) -> None:
        filt = LidarFilter(window=5)
        for mm in (400, 410, 2000, 405, 415):
            filt.ingest(mm)
        self.assertGreater(filt.last_good, 390)
        self.assertLess(filt.last_good, 430)

    def test_status_reports_valid_after_samples(self) -> None:
        filt = LidarFilter()
        filt.ingest(350)
        st = filt.status()
        self.assertTrue(st["valid"])
        self.assertEqual(st["last_good_mm"], 350)


if __name__ == "__main__":
    unittest.main()
