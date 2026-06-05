"""Quit gesture must not fire while the user is showing a single index finger."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from inputs.gesture import _detect_quit_gesture, _is_one_gesture, _is_thumbs_up_strict


def _lm(x: float, y: float, z: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=z)


def _one_finger_landmarks() -> list[SimpleNamespace]:
    """Rough pose: index extended, other fingers folded, thumb tucked."""
    pts = [SimpleNamespace(x=0.5, y=0.5, z=0.0) for _ in range(21)]
    # Wrist / palm
    pts[0] = _lm(0.5, 0.7)
    pts[5] = _lm(0.45, 0.55)
    # Index extended upward
    pts[6] = _lm(0.42, 0.48)
    pts[7] = _lm(0.40, 0.38)
    pts[8] = _lm(0.38, 0.22)
    # Middle/ring/pinky folded
    for tip, pip, mcp, y in (
        (12, 10, 9, 0.52),
        (16, 14, 13, 0.54),
        (20, 18, 17, 0.56),
    ):
        pts[mcp] = _lm(0.5, y)
        pts[pip] = _lm(0.5, y + 0.02)
        pts[tip] = _lm(0.5, y + 0.06)
    # Thumb tucked near index base
    pts[1] = _lm(0.48, 0.58)
    pts[2] = _lm(0.46, 0.56)
    pts[3] = _lm(0.44, 0.54)
    pts[4] = _lm(0.43, 0.56)
    return pts


class GestureQuitTests(unittest.TestCase):
    def test_one_is_not_thumbs_up_quit(self) -> None:
        lm = _one_finger_landmarks()
        self.assertTrue(_is_one_gesture(lm))
        self.assertFalse(_is_thumbs_up_strict(lm))
        self.assertFalse(_detect_quit_gesture(lm, mode="thumbs_up"))

    def test_peace_hold_mode_uses_peace_not_one(self) -> None:
        lm = _one_finger_landmarks()
        self.assertFalse(_detect_quit_gesture(lm, mode="peace_hold"))


if __name__ == "__main__":
    unittest.main()
