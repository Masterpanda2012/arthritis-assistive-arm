import tempfile
import unittest
from pathlib import Path

from ai.user_profile import MotorLevel, UserProfileStore, preset_for_level, apply_profile_to_config
from config import load_config


class UserProfileTests(unittest.TestCase):
    def test_severe_preset_is_slower(self) -> None:
        severe = preset_for_level(MotorLevel.SEVERE)
        early = preset_for_level(MotorLevel.EARLY)
        self.assertLess(severe.default_speed_pct, early.default_speed_pct)
        self.assertTrue(severe.simple_gesture_mode)

    def test_profile_persists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.db"
            store = UserProfileStore(path)
            profile = preset_for_level(MotorLevel.MODERATE)
            store.save(profile)
            loaded = store.load()
            self.assertEqual(loaded.motor_level, MotorLevel.MODERATE)

    def test_apply_profile_tunes_config(self) -> None:
        config = load_config()
        profile = preset_for_level(MotorLevel.SEVERE)
        tuned = apply_profile_to_config(config, profile)
        self.assertEqual(tuned.default_speed_pct, profile.default_speed_pct)
        self.assertGreaterEqual(tuned.gesture_stable_requirement, 8)


if __name__ == "__main__":
    unittest.main()
