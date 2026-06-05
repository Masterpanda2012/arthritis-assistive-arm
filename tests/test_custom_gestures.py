"""Tests for gesture movement diversity and custom gesture catalogue."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai.custom_gestures import CustomGestureCatalog, interpret_gesture_description, landmarks_to_vector
from ai.gesture_diversity import GestureDiversityTracker
from ai.memory_store import MemoryStore
from types import SimpleNamespace


def _fake_landmarks() -> list[SimpleNamespace]:
    pts = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
    pts[0] = SimpleNamespace(x=0.5, y=0.7, z=0.0)
    pts[8] = SimpleNamespace(x=0.4, y=0.2, z=0.0)
    return pts


class GestureDiversityTests(unittest.TestCase):
    def test_blocks_repeat_until_all_families_used(self) -> None:
        tracker = GestureDiversityTracker()
        self.assertFalse(tracker.should_block("lift_up"))
        tracker.record("lift_up")
        self.assertTrue(tracker.should_block("lift_down"))
        tracker.record("base_left")
        tracker.record("rotate_left")
        tracker.record("open_claw")
        self.assertFalse(tracker.should_block("lift_up"))


class CustomGestureTests(unittest.TestCase):
    def test_interpret_and_save(self) -> None:
        parsed = interpret_gesture_description("when I fist, close the claw")
        self.assertEqual(parsed["intent"], "close_claw")

        with tempfile.TemporaryDirectory() as tmp:
            store = MemoryStore(Path(tmp) / "mem.db")
            catalog = CustomGestureCatalog(store)
            vec = landmarks_to_vector(_fake_landmarks())
            item = catalog.add(
                display_name="My Fist",
                description="personal fist",
                intent="close_claw",
                payload={},
                template=vec,
            )
            self.assertEqual(item.intent, "close_claw")
            ok, msg, _ = catalog.validate_new(
                display_name="Copy Fist",
                template=vec,
                intent="close_claw",
            )
            self.assertFalse(ok)
            self.assertIn("similar", msg.lower())


if __name__ == "__main__":
    unittest.main()
