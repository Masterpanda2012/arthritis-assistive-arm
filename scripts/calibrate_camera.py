#!/usr/bin/env python3
"""Interactive camera-to-arm calibration helper.

Places calibration targets at known desk positions and records joint
angles so depth + YOLO targeting can steer base/lift/rotate automatically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from motion.calibration import ArmCalibration, default_calibration, load_calibration, save_calibration


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or tune camera_to_arm.json")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("calibration/camera_to_arm.json"),
    )
    parser.add_argument("--base-center", type=int, help="Home base angle (degrees)")
    parser.add_argument("--lift-center", type=int, help="Default lift when object is mid-range")
    parser.add_argument("--base-deg-per-mm-x", type=float, help="Base sensitivity (deg/mm horizontal)")
    parser.add_argument("--lift-deg-per-mm-z", type=float, help="Lift sensitivity (deg/mm depth)")
    parser.add_argument("--reset", action="store_true", help="Write factory defaults")
    args = parser.parse_args()

    if args.reset:
        cal = default_calibration()
        save_calibration(args.out, cal)
        print(f"Reset calibration → {args.out}")
        return 0

    cal = load_calibration(args.out) or default_calibration()
    data = cal.to_dict()
    if args.base_center is not None:
        data["base_center_deg"] = args.base_center
    if args.lift_center is not None:
        data["lift_center_deg"] = args.lift_center
    if args.base_deg_per_mm_x is not None:
        data["base_deg_per_mm_x"] = args.base_deg_per_mm_x
    if args.lift_deg_per_mm_z is not None:
        data["lift_deg_per_mm_z"] = args.lift_deg_per_mm_z

    updated = ArmCalibration.from_dict(data)
    save_calibration(args.out, updated)
    print(json.dumps(updated.to_dict(), indent=2))
    print(f"\nSaved {args.out}")
    print(
        "\nTip: Run a trial at each desk corner, note base/lift when the claw "
        "points at the object, then adjust --base-center and --lift-deg-per-mm-z."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
