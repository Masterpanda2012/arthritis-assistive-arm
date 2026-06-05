from __future__ import annotations

import argparse
import asyncio
import logging
from dataclasses import replace
from pathlib import Path

from ai.user_profile import MotorLevel, UserProfileStore, apply_profile_to_config, preset_for_level
from config import RuntimeConfig, load_config
from orchestrator import AdaptiveRobotArmApp


def build_runtime_config(args: argparse.Namespace) -> tuple[RuntimeConfig, object]:
    config = load_config()
    store = UserProfileStore(config.memory_db_path)

    level_name = getattr(args, "motor_level", None) or config.motor_level
    try:
        level = MotorLevel(level_name)
    except ValueError:
        level = MotorLevel.MODERATE

    profile = store.load(default_level=level)
    profile = replace(
        preset_for_level(level),
        display_name=profile.display_name,
        notes=profile.notes,
        preferred_input=profile.preferred_input,
    )
    if getattr(args, "accessibility", False):
        profile = replace(profile, accessibility_ui=True)
    if getattr(args, "no_accessibility", False):
        profile = replace(profile, accessibility_ui=False)

    store.save(profile)

    gesture_only = bool(getattr(args, "gesture_only", False))
    features = replace(
        config.features,
        enable_gesture=(profile.enable_gesture_input and not args.disable_gesture),
        enable_voice=(profile.enable_voice_input and not gesture_only and not args.disable_voice),
        enable_vision=(not gesture_only and not args.disable_vision),
        enable_control_panel=(profile.enable_manual_input and not gesture_only and not args.disable_panel),
        enable_ai=False if gesture_only else config.features.enable_ai,
        enable_depth=False if gesture_only else (not args.disable_depth),
        simulate_serial=args.simulation,
        accessibility_ui=profile.accessibility_ui,
    )
    serial_cfg = replace(config.serial, port=args.serial_port or config.serial.port)
    vision_model = Path(args.vision_model).expanduser() if args.vision_model else config.vision_model_path
    cam_idx = args.camera_index if args.camera_index is not None else config.camera_device_index
    cam_name = args.camera_name if args.camera_name else config.camera_prefer_name
    gesture_preview = config.gesture_show_preview and not bool(getattr(args, "no_gesture_preview", False))
    web_mode = bool(getattr(args, "web", False))
    show_cam = config.show_camera_windows and not bool(getattr(args, "no_camera_preview", False))
    if web_mode:
        # Web console replaces the legacy Tk "CREATE-TKS" desktop panel.
        features = replace(features, enable_control_panel=False)
        if not bool(getattr(args, "no_camera_preview", False)):
            show_cam = False
    auto_sim = config.auto_simulate_on_serial_fail
    if bool(getattr(args, "no_auto_simulate", False)):
        auto_sim = False

    base_config = replace(
        config,
        features=features,
        serial=serial_cfg,
        vision_model_path=vision_model,
        camera_device_index=cam_idx,
        camera_prefer_name=cam_name,
        gesture_show_preview=gesture_preview,
        show_camera_windows=show_cam,
        web_camera_preview=web_mode,
        auto_simulate_on_serial_fail=auto_sim,
        motor_level=level.value,
        quit_gesture=profile.quit_gesture if profile.quit_gesture in {"thumbs_up", "peace_hold"} else config.quit_gesture,
    )
    tuned = apply_profile_to_config(base_config, profile)
    return tuned, profile


async def _open_browser_when_ready(url: str, delay_s: float = 1.4) -> None:
    import webbrowser

    await asyncio.sleep(delay_s)
    try:
        webbrowser.open(url, new=1, autoraise=True)
    except OSError:
        logging.getLogger(__name__).info("Open your browser manually: %s", url)


async def async_main(args: argparse.Namespace) -> int:
    config, profile = build_runtime_config(args)
    app = AdaptiveRobotArmApp(config, user_profile=profile)

    if getattr(args, "web", False):
        from web.server import run_uvicorn, set_robot_app

        set_robot_app(app, config.memory_db_path)
        host = args.web_host
        port = args.web_port
        url = f"http://{host}:{port}/"
        logging.getLogger(__name__).info(
            "Web console at %s — use the browser UI (Tk desktop panel is disabled in --web mode)",
            url,
        )
        if not getattr(args, "no_open_browser", False):
            asyncio.create_task(_open_browser_when_ready(url))
        await asyncio.gather(
            app.run(duration=args.duration),
            run_uvicorn(host=host, port=port),
        )
    else:
        if config.features.enable_control_panel:
            logging.getLogger(__name__).warning(
                "Legacy Tk desktop panel is opening. For the modern web UI, restart with: "
                "python main.py --web"
            )
        await app.run(duration=args.duration)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Assistive robot arm for arthritis — voice, gesture, vision, and gentle motion",
    )
    parser.add_argument("--serial-port", help="Override the Arduino serial port path.")
    parser.add_argument("--vision-model", help="Path to a local YOLO model file (default: yolov8s.pt).")
    parser.add_argument("--simulation", action="store_true", help="Force serial simulation mode.")
    parser.add_argument("--disable-gesture", action="store_true", help="Disable the MediaPipe gesture input.")
    parser.add_argument("--disable-voice", action="store_true", help="Disable the voice or text command input.")
    parser.add_argument("--disable-vision", action="store_true", help="Disable YOLO + depth vision targeting.")
    parser.add_argument("--disable-depth", action="store_true", help="Disable Depth Anything V2 (LiDAR-only ranging).")
    parser.add_argument("--disable-panel", action="store_true", help="Disable the fallback click control panel.")
    parser.add_argument(
        "--gesture-only",
        action="store_true",
        help="OpenCV webcam + MediaPipe gestures + robot serial only.",
    )
    parser.add_argument(
        "--no-gesture-preview",
        action="store_true",
        help="Process gestures without cv2.imshow (headless).",
    )
    parser.add_argument(
        "--no-camera-preview",
        action="store_true",
        help="Do not open the raw webcam preview window.",
    )
    parser.add_argument("--duration", type=float, help="Optional runtime limit in seconds for smoke tests.")
    parser.add_argument(
        "--camera-index",
        type=int,
        default=None,
        help="Webcam index (default: ROBOT_ARM_CAMERA_INDEX or 0).",
    )
    parser.add_argument(
        "--camera-name",
        default="",
        help="Prefer a camera whose name contains this substring (macOS).",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="Print connected cameras and exit.",
    )
    parser.add_argument(
        "--motor-level",
        choices=["early", "moderate", "severe"],
        default=None,
        help="Motor profile: early, moderate, or severe arthritis limitations.",
    )
    parser.add_argument(
        "--accessibility",
        action="store_true",
        help="Enable large-button accessibility UI with daily-task shortcuts.",
    )
    parser.add_argument(
        "--no-accessibility",
        action="store_true",
        help="Use compact technical control panel layout.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose debug logging.")
    parser.add_argument(
        "--no-auto-simulate",
        action="store_true",
        help="Do not fall back to simulated serial when USB is missing.",
    )
    parser.add_argument(
        "--web",
        action="store_true",
        help="Serve the premium web control console (profile + manual controls).",
    )
    parser.add_argument(
        "--web-port",
        type=int,
        default=8787,
        help="Port for the web console when --web is set (default: 8787).",
    )
    parser.add_argument(
        "--no-open-browser",
        action="store_true",
        help="Do not auto-open the browser when --web is set.",
    )
    parser.add_argument(
        "--web-host",
        default="127.0.0.1",
        help="Bind address for the web console (default: 127.0.0.1).",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_env_file() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
    except ImportError:
        return
    load_dotenv(Path(__file__).resolve().parent / ".env")


def main() -> int:
    _load_env_file()
    args = parse_args()
    if getattr(args, "list_cameras", False):
        from inputs.camera import pretty_camera_listing

        print(pretty_camera_listing())
        return 0
    configure_logging(args.verbose)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Interrupted by user.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
