import unittest

from ai.user_profile import MotorLevel, UserProfile
from web.help_content import build_help_payload


class HelpContentTests(unittest.TestCase):
    def test_includes_gestures_when_enabled(self) -> None:
        profile = UserProfile(enable_gesture_input=True, enable_voice_input=False)
        payload = build_help_payload(profile)
        ids = [s.get("id") for s in payload["sections"]]
        self.assertIn("gestures", ids)
        self.assertIn("voice_off", ids)
        self.assertNotIn("voice", ids)

    def test_simple_mode_for_severe(self) -> None:
        profile = UserProfile(motor_level=MotorLevel.SEVERE, enable_gesture_input=True)
        payload = build_help_payload(profile)
        gesture = next(s for s in payload["sections"] if s.get("id") == "gestures")
        self.assertLess(len(gesture["rows"]), 12)

    def test_custom_gestures_section(self) -> None:
        profile = UserProfile(enable_gesture_input=True)
        custom = [{"display_name": "My wave", "intent": "open_claw", "id": "g1"}]
        payload = build_help_payload(profile, custom_gestures=custom)
        ids = [s.get("id") for s in payload["sections"]]
        self.assertIn("custom_gestures", ids)

    def test_intro_notes_simulation(self) -> None:
        payload = build_help_payload(UserProfile(), serial_mode="simulation")
        joined = " ".join(payload["intro"]["notes"])
        self.assertIn("simulation", joined.lower())


if __name__ == "__main__":
    unittest.main()
