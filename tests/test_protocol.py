import unittest

from motion.protocol import build_packet, parse_packet


class ProtocolTests(unittest.TestCase):
    def test_build_and_parse_pose_packet(self) -> None:
        packet = build_packet("POSE", 90, 105, 90, 95, 35)
        parsed = parse_packet(packet)
        self.assertEqual(parsed.command, "POSE")
        self.assertEqual(parsed.fields, ("90", "105", "90", "95", "35"))

    def test_rejects_bad_checksum(self) -> None:
        with self.assertRaises(ValueError):
            parse_packet("<PING*999>")

    def test_rejects_missing_wrappers(self) -> None:
        with self.assertRaises(ValueError):
            parse_packet("PING*46")
