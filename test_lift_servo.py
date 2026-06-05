"""
test_lift_servo.py – Isolated lift-servo test.

Connects to the Arduino, sends INIT, then sweeps the lift servo through
a sequence of angles so you can visually confirm direction and range.
No base sweep, no LiDAR, no OpenCV – just the lift.

Usage:
    python test_lift_servo.py              # default sequence
    python test_lift_servo.py 180 140 100 180   # custom angles
"""

import asyncio
import sys
import time
from dataclasses import replace

from config import load_config
from motion.serial_bridge import SerialBridge


HOLD_TIME = 2.5   # seconds to hold each position so you can observe


async def run_lift_test(custom_angles: list[int] | None = None):
    config = replace(load_config(), auto_simulate_on_serial_fail=False)
    state_queue: asyncio.Queue = asyncio.Queue()

    # ── Connect ──────────────────────────────────────────────────────
    print("Connecting to Arduino...")
    bridge = SerialBridge(config, state_queue)
    await bridge.start()

    print(f"Bridge mode: {bridge.mode} on {bridge.port_path or config.serial.port}")
    if bridge.mode != "live":
        print("❌  Live serial not available – check USB cable & board power.")
        await bridge.close()
        return

    print("Sending INIT...")
    if not await bridge.send_init():
        print("❌  INIT failed – Arduino may still be booting.")
        await bridge.close()
        return
    print("✔  INIT OK")

    from models import ArmCommand

    home_lift = config.home_pose.lift_deg  # 225

    # ── Let servos settle (matches hardware_test.py pattern) ─────────
    print("Standing up straight first...")
    await bridge.send_pose(
        ArmCommand(
            base_deg=90,
            lift_deg=home_lift,
            rotate_deg=90,
            claw_deg=102,
            speed_pct=50,
            origin="test",
        )
    )
    await asyncio.sleep(2.0)

    # ── Build angle sequence ─────────────────────────────────────────
    if custom_angles:
        angles = custom_angles
    else:
        # Step 1-2: big DOWN then back UP to prove servo is alive
        # Step 3-5: probe UPWARD to find "fully straight"
        angles = [
            120,                 # BIG down — confirm movement
            home_lift,           # 180 — back to current home
            200,                 # probe: straighter?
            215,                 # probe: more?
            225,                 # firmware max — should be fully straight now
            home_lift,           # end at 180 for comparison
        ]

    # ── Helper ───────────────────────────────────────────────────────
    async def drain_state(label: str):
        await asyncio.sleep(HOLD_TIME)
        latest = None
        while not state_queue.empty():
            try:
                latest = state_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if latest:
            print(f"    ↳ Arduino echo: lift={latest.lift_deg}°  base={latest.base_deg}°")
        else:
            print(f"    ↳ (no state packet received)")

    # ── Run test ─────────────────────────────────────────────────────
    print(f"══════════════════════════════════════")
    print(f"   LIFT SERVO TEST   (home = {home_lift}°)")
    print(f"══════════════════════════════════════")
    print(f"   Sequence: {' → '.join(str(a) + '°' for a in angles)}")
    print(f"   Hold time per step: {HOLD_TIME}s")
    print(f"   Base/rotate/claw stay fixed.\n")

    for i, angle in enumerate(angles, 1):
        tag = f"[{i}/{len(angles)}]"
        delta = angle - home_lift
        direction = "UP" if delta > 0 else ("DOWN" if delta < 0 else "HOME")
        print(f"  {tag}  lift_deg={angle}°   ({direction}, Δ{delta:+d}°)")

        await bridge.send_pose(
            ArmCommand(
                base_deg=90,
                lift_deg=angle,
                rotate_deg=90,
                claw_deg=102,
                speed_pct=30,
                origin="lift_test",
            )
        )
        await drain_state(f"step {i}")

    # ── Cleanup ──────────────────────────────────────────────────────
    print("\n══ LIFT TEST COMPLETE ══")
    print("Detaching servos...")
    await bridge.send_stop()
    await bridge.close()
    print("Done.")


if __name__ == "__main__":
    custom = None
    if len(sys.argv) > 1:
        try:
            custom = [int(a) for a in sys.argv[1:]]
        except ValueError:
            print(f"Usage: python {sys.argv[0]} [angle1 angle2 ...]")
            sys.exit(1)
    asyncio.run(run_lift_test(custom))
