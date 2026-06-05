import unittest

from inputs.voice import parse_direct_command


class VoiceParsingTests(unittest.TestCase):
    def test_open_claw_command(self) -> None:
        action = parse_direct_command("open claw")
        self.assertIsNotNone(action)
        self.assertEqual(action.intent, "open_claw")

    def test_lower_arm_command(self) -> None:
        action = parse_direct_command("lower arm")
        self.assertIsNotNone(action)
        self.assertEqual(action.intent, "lift_down")

    def test_unknown_command_falls_back_to_spoken_text(self) -> None:
        action = parse_direct_command("pick up the cup")
        self.assertIsNotNone(action)
        self.assertEqual(action.intent, "spoken_text")
        self.assertEqual(action.payload["text"], "pick up the cup")

    def test_confirmation_commands_are_supported(self) -> None:
        yes_action = parse_direct_command("yes")
        no_action = parse_direct_command("no")
        self.assertIsNotNone(yes_action)
        self.assertIsNotNone(no_action)
        self.assertEqual(yes_action.intent, "confirm_yes")
        self.assertEqual(no_action.intent, "confirm_no")

    def test_polite_direct_command_still_parses_without_llm(self) -> None:
        action = parse_direct_command("please open the claw")
        self.assertIsNotNone(action)
        self.assertEqual(action.intent, "open_claw")

    def test_home_variants_are_matched_directly(self) -> None:
        go_back = parse_direct_command("go back")
        home_position = parse_direct_command("home position")
        self.assertIsNotNone(go_back)
        self.assertIsNotNone(home_position)
        self.assertEqual(go_back.intent, "home")
        self.assertEqual(home_position.intent, "home")

    def test_hand_and_arm_phrases_normalize_cleanly(self) -> None:
        open_hand = parse_direct_command("please open the hand")
        raise_arm = parse_direct_command("raise arm")
        stop_moving = parse_direct_command("stop moving")
        self.assertIsNotNone(open_hand)
        self.assertIsNotNone(raise_arm)
        self.assertIsNotNone(stop_moving)
        self.assertEqual(open_hand.intent, "open_claw")
        self.assertEqual(raise_arm.intent, "lift_up")
        self.assertEqual(stop_moving.intent, "emergency_stop")
