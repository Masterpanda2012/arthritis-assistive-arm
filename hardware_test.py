import asyncio
import time
from dataclasses import replace
from pathlib import Path

from config import load_config
from motion.serial_bridge import SerialBridge

async def run_test():
    config = replace(load_config(), auto_simulate_on_serial_fail=False)
    print("Testing Serial Connection...")
    state_queue = asyncio.Queue()
    bridge = SerialBridge(config, state_queue)
    await bridge.start()

    print(f"Bridge mode: {bridge.mode} on {bridge.port_path or config.serial.port}")
    if bridge.mode != "live":
        print("Live serial handshake failed. Check the USB port, board power, and flashed firmware before retrying.")
        visible_candidates = [path for path in bridge._candidate_ports() if Path(path).exists()]
        if visible_candidates:
            print("Visible serial devices:", ", ".join(visible_candidates))
        else:
            print("No matching USB serial devices are currently visible to the host.")
            print(f"Configured port was: {config.serial.port}")
        await bridge.close()
        return
    
    print("Sending INIT packet...")
    init_ok = await bridge.send_init()
    if not init_ok:
        print("Failed to send INIT. Arduino might not be connected or is still booting.")
        await bridge.close()
        return
    print("INIT successful!")
    
    from models import ArmCommand

    print("Standing up straight first...")
    await bridge.send_pose(
        ArmCommand(
            base_deg=90,
            lift_deg=config.home_pose.lift_deg,
            rotate_deg=90,
            claw_deg=102,
            speed_pct=50,
            origin="test",
        )
    )
    await asyncio.sleep(1.5)

    # ── Simple lift servo test ──────────────────────────────────────
    # 3 big, obvious movements. We read the Arduino state back so we
    # can confirm the command actually arrived.

    async def drain_state(label: str):
        """Read latest state from queue to confirm Arduino received the pose."""
        await asyncio.sleep(2.0)  # let servo move + Arduino report
        latest = None
        while not state_queue.empty():
            try:
                latest = state_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if latest:
            print(f"    Arduino reports: lift={latest.lift_deg}° base={latest.base_deg}° (after {label})")
        else:
            print(f"    No state received from Arduino (after {label})")

    home_lift = config.home_pose.lift_deg  # 225

    test_angles = [
        ("HOME (upright)", home_lift,      "Arm should stand straight"),
        ("DOWN 40°",       home_lift - 40, "Arm tilts forward"),
        ("BACK TO HOME",   home_lift,      "Arm returns upright"),
    ]

    print(f"\n══ LIFT SERVO TEST (home={home_lift}°) ══")
    print("Watch the arm carefully at each step.\n")
    for label, angle, hint in test_angles:
        print(f"  → Sending lift_deg={angle}°  ({label}: {hint})")
        await bridge.send_pose(
            ArmCommand(base_deg=90, lift_deg=angle, rotate_deg=90,
                       claw_deg=102, speed_pct=30, origin="test")
        )
        await drain_state(label)
    print("\n══ LIFT TEST DONE ══\n")
    lift_up = home_lift  # keep arm upright during sweep

    # ── LiDAR + base sweep (arm stays lifted) ───────────────────────
    print("Sweeping base with arm raised for 15 seconds...")

    try:
        import cv2
        import numpy as np
        import math
        cv2.namedWindow("LiDAR Map")
        map_img = np.zeros((600, 600, 3), dtype=np.uint8)
        # Draw robot in center
        cv2.circle(map_img, (300, 300), 10, (255, 255, 255), -1)
    except ImportError:
        cv2 = None
        print("OpenCV not installed, skipping visual map.")

    end_time = time.time() + 15.0
    sweep_angle = 90
    sweep_dir = 10
    last_sweep = time.time()

    while time.time() < end_time:
        # Sweep base back and forth every 0.5s to prove movement
        if time.time() - last_sweep > 0.5:
            sweep_angle += sweep_dir
            if sweep_angle >= 150:
                sweep_dir = -10
            elif sweep_angle <= 30:
                sweep_dir = 10

            await bridge.send_pose(
                ArmCommand(
                    base_deg=sweep_angle,
                    lift_deg=lift_up,
                    rotate_deg=90,
                    claw_deg=102,
                    speed_pct=50,
                    origin="test",
                )
            )
            last_sweep = time.time()
            
        try:
            state = state_queue.get_nowait()
            print(f"Lidar Distance: {state.range_mm} mm at Base: {state.base_deg} deg")
            
            if cv2 is not None and state.range_mm >= 0:
                # Map angle to 2D space
                angle_rad = math.radians(state.base_deg)
                
                if state.range_mm == 0:
                    # Draw a red dot near the center to indicate blind spot / 0 reading
                    dist_px = 15
                    color = (0, 0, 255) # Red for blind spot
                else:
                    # Scale distance: 1 mm = 0.2 pixels (max ~1500mm fits in 300px)
                    dist_px = int(state.range_mm * 0.2)
                    color = (0, 255, 0) # Green for valid distance
                    
                x = 300 + int(math.cos(angle_rad) * dist_px)
                y = 300 - int(math.sin(angle_rad) * dist_px)
                
                # Draw the point
                if 0 <= x < 600 and 0 <= y < 600:
                    cv2.circle(map_img, (x, y), 3, color, -1)
                
                cv2.imshow("LiDAR Map", map_img)
                cv2.waitKey(10)
                
        except asyncio.QueueEmpty:
            if cv2 is not None:
                cv2.waitKey(50)
            else:
                await asyncio.sleep(0.1)
            continue
        
    print("Test Complete. Shutting down and detaching servos...")
    if cv2 is not None:
        cv2.destroyAllWindows()
    await bridge.send_stop()
    await bridge.close()

if __name__ == "__main__":
    asyncio.run(run_test())
