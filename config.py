from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ServoLimit:
    min_deg: int
    max_deg: int


@dataclass(frozen=True, slots=True)
class Pose:
    base_deg: int
    lift_deg: int
    rotate_deg: int
    claw_deg: int


@dataclass(frozen=True, slots=True)
class FeatureFlags:
    enable_gesture: bool
    enable_voice: bool
    enable_vision: bool
    enable_control_panel: bool
    enable_ai: bool
    enable_depth: bool
    simulate_serial: bool
    accessibility_ui: bool


@dataclass(frozen=True, slots=True)
class SerialConfig:
    port: str
    baud: int
    timeout_s: float
    min_command_interval_s: float
    connect_delay_s: float
    startup_sync_timeout_s: float
    ack_timeout_s: float


@dataclass(frozen=True, slots=True)
class LLMProviderConfig:
    base_url: str
    model: str
    api_key_env: str


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    serial: SerialConfig
    features: FeatureFlags
    servo_limits: dict[str, ServoLimit]
    home_pose: Pose
    survey_pose: Pose
    pickup_ready_pose: Pose
    drop_ready_pose: Pose
    inspect_pose: Pose
    default_speed_pct: int
    movement_steps: dict[str, int]
    lift_up_increases: bool
    gesture_buffer_len: int
    gesture_stable_requirement: int
    gesture_confirm_frames: int
    gesture_hud_smooth_len: int
    gesture_hud_min_votes: int
    gesture_action_cooldown_s: float
    thumbs_up_hold_time: float
    show_camera_windows: bool
    gesture_show_preview: bool
    web_camera_preview: bool
    enable_startup_sweep: bool
    vision_model_path: Path
    vision_confidence: float
    vision_center_tolerance: float
    vision_cooldown_s: float
    tf_luna_state_max_age_s: float
    vosk_model_path: Path
    confirmation_timeout_s: float
    llm_provider: str
    llm_configs: dict[str, LLMProviderConfig]
    calibration_path: Path
    memory_db_path: Path
    camera_device_index: int
    camera_prefer_name: str
    auto_simulate_on_serial_fail: bool
    voice_active_listen_s: float
    voice_require_lip_activity: bool
    pose_settle_s: float
    upright_stage_settle_s: float
    depth_model_id: str
    motor_level: str
    simple_gesture_mode: bool
    voice_short_phrases: bool
    quit_gesture: str


def load_config() -> RuntimeConfig:
    project_root = Path(__file__).resolve().parent
    show_windows = sys.platform == "darwin" or bool(os.environ.get("DISPLAY"))

    def _env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            return default

    try:
        camera_index = int(os.environ.get("ROBOT_ARM_CAMERA_INDEX", "0"))
    except ValueError:
        camera_index = 0
    try:
        gesture_confirm_frames = max(1, int(os.environ.get("ROBOT_ARM_GESTURE_CONFIRM_FRAMES", "2")))
    except ValueError:
        gesture_confirm_frames = 2
    try:
        gesture_hud_smooth_len = max(3, int(os.environ.get("ROBOT_ARM_GESTURE_HUD_SMOOTH_LEN", "5")))
    except ValueError:
        gesture_hud_smooth_len = 5
    try:
        gesture_hud_min_votes = max(1, int(os.environ.get("ROBOT_ARM_GESTURE_HUD_MIN_VOTES", "2")))
    except ValueError:
        gesture_hud_min_votes = 2
    try:
        gesture_action_cooldown_s = float(os.environ.get("ROBOT_ARM_GESTURE_ACTION_COOLDOWN_S", "0.45"))
    except ValueError:
        gesture_action_cooldown_s = 0.45
    # OpenCV preview for MediaPipe (separate from control panel). Default follows desktop availability.
    _gp = os.environ.get("ROBOT_ARM_GESTURE_PREVIEW")
    if _gp is None:
        gesture_preview = show_windows
    else:
        gesture_preview = _gp.strip().lower() not in {"0", "false", "no"}
    # Raw webcam window (gesture/vision preview is separate). Default follows desktop availability.
    _cp = os.environ.get("ROBOT_ARM_CAMERA_PREVIEW")
    if _cp is None:
        show_camera = show_windows
    else:
        show_camera = _cp.strip().lower() not in {"0", "false", "no"}
    _ss = os.environ.get("ROBOT_ARM_ENABLE_STARTUP_SWEEP", "")
    enable_startup_sweep = _ss.strip().lower() in {"1", "true", "yes", "on"}
    _as = os.environ.get("ROBOT_ARM_AUTO_SIMULATE", "1")
    auto_simulate_on_serial_fail = _as.strip().lower() not in {"0", "false", "no", "off"}
    try:
        voice_active_listen_s = max(2.0, float(os.environ.get("ROBOT_ARM_VOICE_ACTIVE_LISTEN_S", "6.0")))
    except ValueError:
        voice_active_listen_s = 6.0
    try:
        pose_settle_s = max(0.0, float(os.environ.get("ROBOT_ARM_POSE_SETTLE_S", "0.35")))
    except ValueError:
        pose_settle_s = 0.35
    try:
        upright_stage_settle_s = max(pose_settle_s, float(os.environ.get("ROBOT_ARM_UPRIGHT_SETTLE_S", "1.2")))
    except ValueError:
        upright_stage_settle_s = max(pose_settle_s, 1.2)
    _lip_gate = os.environ.get("ROBOT_ARM_VOICE_REQUIRE_LIP", "1")
    voice_require_lip_activity = _lip_gate.strip().lower() not in {"0", "false", "no", "off"}
    _depth = os.environ.get("ROBOT_ARM_ENABLE_DEPTH", "1")
    enable_depth = _depth.strip().lower() not in {"0", "false", "no", "off"}
    motor_level = os.environ.get("ROBOT_ARM_MOTOR_LEVEL", "moderate").strip().lower()
    if motor_level not in {"early", "moderate", "severe"}:
        motor_level = "moderate"
    _access = os.environ.get("ROBOT_ARM_ACCESSIBILITY", "1")
    accessibility_ui = _access.strip().lower() not in {"0", "false", "no", "off"}
    _simple_g = os.environ.get("ROBOT_ARM_SIMPLE_GESTURES", "")
    simple_gesture_mode = _simple_g.strip().lower() in {"1", "true", "yes", "on"}
    if motor_level == "severe" and not _simple_g:
        simple_gesture_mode = True
    voice_short_phrases = motor_level in {"moderate", "severe"}
    quit_gesture = os.environ.get("ROBOT_ARM_QUIT_GESTURE", "peace_hold").strip().lower()
    if quit_gesture not in {"thumbs_up", "peace_hold", "peace"}:
        quit_gesture = "peace_hold"
    _vsp = os.environ.get("ROBOT_ARM_VOICE_SHORT_PHRASES", "")
    if _vsp.strip().lower() in {"0", "false", "no", "off"}:
        voice_short_phrases = False
    if _vsp.strip().lower() in {"1", "true", "yes", "on"}:
        voice_short_phrases = True

    home_pose = Pose(
        _env_int("ROBOT_ARM_HOME_BASE_DEG", 90),
        _env_int("ROBOT_ARM_HOME_LIFT_DEG", 225),
        _env_int("ROBOT_ARM_HOME_ROTATE_DEG", 90),
        _env_int("ROBOT_ARM_HOME_CLAW_DEG", 100),
    )
    survey_pose = Pose(
        _env_int("ROBOT_ARM_SURVEY_BASE_DEG", home_pose.base_deg),
        _env_int("ROBOT_ARM_SURVEY_LIFT_DEG", 90),
        _env_int("ROBOT_ARM_SURVEY_ROTATE_DEG", home_pose.rotate_deg),
        _env_int("ROBOT_ARM_SURVEY_CLAW_DEG", 102),
    )

    return RuntimeConfig(
        serial=SerialConfig(
            port=os.environ.get("ROBOT_ARM_PORT", "/dev/cu.usbmodem1401"),
            baud=115200,
            timeout_s=0.2,
            min_command_interval_s=0.05,
            connect_delay_s=2.5,
            startup_sync_timeout_s=3.0,
            ack_timeout_s=0.6,
        ),
        features=FeatureFlags(
            enable_gesture=True,
            enable_voice=True,
            enable_vision=True,
            enable_control_panel=True,
            enable_ai=True,
            enable_depth=enable_depth,
            simulate_serial=False,
            accessibility_ui=accessibility_ui,
        ),
        servo_limits={
            "base": ServoLimit(_env_int("ROBOT_ARM_BASE_MIN", 10), _env_int("ROBOT_ARM_BASE_MAX", 250)),
            "lift": ServoLimit(_env_int("ROBOT_ARM_LIFT_MIN", 15), _env_int("ROBOT_ARM_LIFT_MAX", 225)),
            "rotate": ServoLimit(_env_int("ROBOT_ARM_ROTATE_MIN", 10), _env_int("ROBOT_ARM_ROTATE_MAX", 170)),
            "claw": ServoLimit(_env_int("ROBOT_ARM_CLAW_MIN", 15), _env_int("ROBOT_ARM_CLAW_MAX", 165)),
        },
        home_pose=home_pose,
        survey_pose=survey_pose,
        pickup_ready_pose=Pose(92, 120, 92, 118),
        drop_ready_pose=Pose(55, 95, 125, 115),
        inspect_pose=Pose(120, 85, 70, 100),
        default_speed_pct=35,
        movement_steps={
            "base": 12,
            "lift": 10,
            "rotate": 12,
            "claw": 18,
        },
        lift_up_increases=True,
        gesture_buffer_len=6,
        gesture_stable_requirement=4,
        gesture_confirm_frames=gesture_confirm_frames,
        gesture_hud_smooth_len=gesture_hud_smooth_len,
        gesture_hud_min_votes=gesture_hud_min_votes,
        gesture_action_cooldown_s=gesture_action_cooldown_s,
        thumbs_up_hold_time=1.0,
        show_camera_windows=show_camera,
        gesture_show_preview=gesture_preview,
        web_camera_preview=False,
        enable_startup_sweep=enable_startup_sweep,
        vision_model_path=Path(os.environ.get("ROBOT_ARM_VISION_MODEL", "yolov8s.pt")),
        vision_confidence=0.55,
        vision_center_tolerance=0.18,
        vision_cooldown_s=2.5,
        tf_luna_state_max_age_s=1.0,
        vosk_model_path=project_root / "models" / "vosk-model-small-en-us-0.15",
        confirmation_timeout_s=12.0,
        llm_provider=os.environ.get("ROBOT_ARM_LLM_PROVIDER", "groq"),
        llm_configs={
            "groq": LLMProviderConfig(
                base_url="https://api.groq.com/openai/v1",
                model="llama-3.3-70b-versatile",
                api_key_env="GROQ_API_KEY",
            ),
            "google": LLMProviderConfig(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                model="gemini-2.5-flash",
                api_key_env="GOOGLE_API_KEY",
            ),
            "ollama": LLMProviderConfig(
                base_url="http://localhost:11434/v1",
                model="llama3.2:3b",
                api_key_env="OLLAMA_API_KEY",
            ),
        },
        calibration_path=project_root / "calibration" / "camera_to_arm.json",
        memory_db_path=project_root / "ai" / "memory.db",
        camera_device_index=camera_index,
        camera_prefer_name=os.environ.get("ROBOT_ARM_CAMERA_NAME", "").strip(),
        auto_simulate_on_serial_fail=auto_simulate_on_serial_fail,
        voice_active_listen_s=voice_active_listen_s,
        voice_require_lip_activity=voice_require_lip_activity,
        pose_settle_s=pose_settle_s,
        upright_stage_settle_s=upright_stage_settle_s,
        depth_model_id=os.environ.get(
            "ROBOT_ARM_DEPTH_MODEL",
            "depth-anything/Depth-Anything-V2-Small-hf",
        ),
        motor_level=motor_level,
        simple_gesture_mode=simple_gesture_mode,
        voice_short_phrases=voice_short_phrases,
        quit_gesture=quit_gesture,
    )
