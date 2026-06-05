from __future__ import annotations

import logging
from pathlib import Path

from config import Pose, RuntimeConfig
from ai.environment import EnvironmentMap
from models import ActionRequest, ArmCommand, ArmState, PlannerResult, VisionTarget


LOGGER = logging.getLogger(__name__)


class MotionPlanner:
    def __init__(self, config: RuntimeConfig, environment: EnvironmentMap | None = None) -> None:
        self.config = config
        self.environment = environment
        from motion.calibration import load_calibration

        self.calibration = load_calibration(config.calibration_path)
        self.cartesian_available = self.calibration is not None

    def plan(self, action: ActionRequest, current_state: ArmState | None) -> PlannerResult:
        intent = action.intent

        if intent == "home":
            return PlannerResult(kind="HOME")
        if intent == "emergency_stop":
            return PlannerResult(kind="STOP")
        if intent == "system_sweep":
            base_deg = action.payload.get("base_deg")
            if base_deg is None:
                return PlannerResult(kind="NONE")
            state = current_state or self._state_from_pose(self.config.home_pose)
            return PlannerResult(
                kind="POSES",
                commands=self._plan_base_rotation(state, target_base=int(base_deg), origin=action.source),
            )

        state = current_state or self._state_from_pose(self.config.home_pose)

        if intent == "open_claw":
            command = self._command_from_state(
                state,
                claw=self.config.servo_limits["claw"].max_deg,
                speed_pct=self._speed_from_action(action),
                origin=action.source,
            )
            return PlannerResult(kind="POSES", commands=(command,))

        if intent == "close_claw":
            command = self._command_from_state(
                state,
                claw=self.config.servo_limits["claw"].min_deg,
                speed_pct=self._speed_from_action(action),
                origin=action.source,
            )
            return PlannerResult(kind="POSES", commands=(command,))

        if intent == "base_left":
            return PlannerResult(
                kind="POSES",
                commands=self._plan_base_rotation(
                    state,
                    target_base=state.base_deg - self._step_from_action(action, "base"),
                    speed_pct=self._speed_from_action(action),
                    origin=action.source,
                ),
            )

        if intent == "base_right":
            return PlannerResult(
                kind="POSES",
                commands=self._plan_base_rotation(
                    state,
                    target_base=state.base_deg + self._step_from_action(action, "base"),
                    speed_pct=self._speed_from_action(action),
                    origin=action.source,
                ),
            )

        if intent == "lift_up":
            return PlannerResult(
                kind="POSES",
                commands=(self._offset_command(
                    state,
                    lift=self._lift_delta(action, up=True),
                    speed_pct=self._speed_from_action(action),
                    origin=action.source,
                ),),
            )

        if intent == "lift_down":
            return PlannerResult(
                kind="POSES",
                commands=(self._offset_command(
                    state,
                    lift=self._lift_delta(action, up=False),
                    speed_pct=self._speed_from_action(action),
                    origin=action.source,
                ),),
            )

        if intent == "rotate_left":
            return PlannerResult(
                kind="POSES",
                commands=(self._offset_command(
                    state,
                    rotate=-self._step_from_action(action, "rotate"),
                    speed_pct=self._speed_from_action(action),
                    origin=action.source,
                ),),
            )

        if intent == "rotate_right":
            return PlannerResult(
                kind="POSES",
                commands=(self._offset_command(
                    state,
                    rotate=self._step_from_action(action, "rotate"),
                    speed_pct=self._speed_from_action(action),
                    origin=action.source,
                ),),
            )

        if intent == "preset_pose":
            pose_name = str(action.payload.get("name", "home"))
            pose = self._pose_by_name(pose_name)
            if pose is None:
                LOGGER.warning("Unknown preset pose '%s'", pose_name)
                return PlannerResult(kind="NONE")
            return PlannerResult(
                kind="POSES",
                commands=(self._command_from_pose(
                    pose,
                    origin=f"{action.source}:{pose_name}",
                    speed_pct=self._speed_from_action(action),
                ),),
            )

        if intent in {"pick_object", "vision_target"}:
            return PlannerResult(kind="POSES", commands=self._plan_pick_sequence(action, state))

        if intent == "place_object":
            return PlannerResult(kind="POSES", commands=self._plan_place_sequence(action, state))

        LOGGER.info("Ignoring unsupported intent '%s'", intent)
        return PlannerResult(kind="NONE")

    def _plan_pick_sequence(self, action: ActionRequest, state: ArmState) -> tuple[ArmCommand, ...]:
        label = action.payload.get("label")
        target = action.payload.get("target")
        speed_pct = self._speed_from_action(action)
        if isinstance(target, VisionTarget) and label is None:
            label = target.label

        base_angle_target = None
        lift_angle_target = None
        rotate_angle_target = None
        if label and self.environment:
            obj = self.environment.get_object(label)
            if obj:
                base_angle_target = obj.base_deg
                LOGGER.info("Planner mapping '%s' vector to base_deg=%d", label, base_angle_target)

        if isinstance(target, VisionTarget) and target.has_3d and self.calibration is not None:
            joints = self.calibration.camera_mm_to_joints(
                target.camera_x_mm,
                target.camera_y_mm,
                target.camera_z_mm,
            )
            base_angle_target = joints["base_deg"]
            lift_angle_target = joints["lift_deg"]
            rotate_angle_target = joints["rotate_deg"]
            LOGGER.info(
                "3D target '%s' → base=%d lift=%d rotate=%d (cam mm: %.0f, %.0f, %.0f)",
                label or "object",
                base_angle_target,
                lift_angle_target,
                rotate_angle_target,
                target.camera_x_mm,
                target.camera_y_mm,
                target.camera_z_mm,
            )
        elif isinstance(target, VisionTarget) and target.image_x and self.calibration is not None:
            # 2D-only fallback: steer base from horizontal offset
            offset = (target.image_x - 0.5) * 200.0
            base_angle_target = int(round(self.calibration.base_center_deg + offset * self.calibration.base_deg_per_mm_x))

        approach = self._command_from_pose(
            self.config.pickup_ready_pose,
            origin=f"{action.source}:pickup_ready",
            speed_pct=speed_pct,
        )
        if base_angle_target is not None:
            approach.base_deg = self._clamp("base", base_angle_target)
        if lift_angle_target is not None:
            approach.lift_deg = self._clamp("lift", lift_angle_target)
        if rotate_angle_target is not None:
            approach.rotate_deg = self._clamp("rotate", rotate_angle_target)

        open_claw = self._command_from_state(
            self._state_from_command(approach),
            claw=self.config.servo_limits["claw"].max_deg,
            speed_pct=speed_pct,
            origin=f"{action.source}:open",
        )
        lower = self._offset_command(
            self._state_from_command(open_claw),
            lift=self._lift_delta(action, up=False),
            speed_pct=speed_pct,
            origin=f"{action.source}:lower",
        )
        grip = self._command_from_state(
            self._state_from_command(lower),
            claw=self.config.servo_limits["claw"].min_deg,
            speed_pct=speed_pct,
            origin=f"{action.source}:grip",
        )
        retract = self._command_from_pose(self.config.home_pose, origin=f"{action.source}:retract", speed_pct=speed_pct)
        if label:
            LOGGER.info("Planning pick sequence for '%s'", label)
        return (approach, open_claw, lower, grip, retract)

    def _plan_place_sequence(self, action: ActionRequest, state: ArmState) -> tuple[ArmCommand, ...]:
        speed_pct = self._speed_from_action(action)
        place = self._command_from_pose(self.config.drop_ready_pose, origin=f"{action.source}:place_ready", speed_pct=speed_pct)
        lower = self._offset_command(
            self._state_from_command(place),
            lift=self._lift_delta(action, up=False),
            speed_pct=speed_pct,
            origin=f"{action.source}:place_lower",
        )
        release = self._command_from_state(
            self._state_from_command(lower),
            claw=self.config.servo_limits["claw"].max_deg,
            speed_pct=speed_pct,
            origin=f"{action.source}:release",
        )
        retract = self._command_from_pose(self.config.home_pose, origin=f"{action.source}:retract", speed_pct=speed_pct)
        return (place, lower, release, retract)

    def _plan_base_rotation(
        self,
        state: ArmState,
        *,
        target_base: int,
        origin: str,
        speed_pct: int | None = None,
    ) -> tuple[ArmCommand, ...]:
        commands: list[ArmCommand] = []
        vertical_lift = self._clamp("lift", self.config.survey_pose.lift_deg)

        staged_state = state
        if state.lift_deg != vertical_lift:
            upright = self._command_from_state(
                state,
                lift=vertical_lift,
                speed_pct=speed_pct,
                origin=f"{origin}:upright",
            )
            commands.append(upright)
            staged_state = self._state_from_command(upright)

        commands.append(
            self._command_from_state(
                staged_state,
                base=target_base,
                speed_pct=speed_pct,
                origin=origin,
            )
        )
        return tuple(commands)

    def _command_from_pose(self, pose: Pose, origin: str, speed_pct: int | None = None) -> ArmCommand:
        return ArmCommand(
            base_deg=self._clamp("base", pose.base_deg),
            lift_deg=self._clamp("lift", pose.lift_deg),
            rotate_deg=self._clamp("rotate", pose.rotate_deg),
            claw_deg=self._clamp("claw", pose.claw_deg),
            speed_pct=self._speed(speed_pct),
            origin=origin,
        )

    def _command_from_state(
        self,
        state: ArmState,
        *,
        base: int | None = None,
        lift: int | None = None,
        rotate: int | None = None,
        claw: int | None = None,
        speed_pct: int | None = None,
        origin: str,
    ) -> ArmCommand:
        return ArmCommand(
            base_deg=self._clamp("base", base if base is not None else state.base_deg),
            lift_deg=self._clamp("lift", lift if lift is not None else state.lift_deg),
            rotate_deg=self._clamp("rotate", rotate if rotate is not None else state.rotate_deg),
            claw_deg=self._clamp("claw", claw if claw is not None else state.claw_deg),
            speed_pct=self._speed(speed_pct),
            origin=origin,
        )

    def _offset_command(
        self,
        state: ArmState,
        *,
        base: int = 0,
        lift: int = 0,
        rotate: int = 0,
        claw: int = 0,
        speed_pct: int | None = None,
        origin: str,
    ) -> ArmCommand:
        return self._command_from_state(
            state,
            base=state.base_deg + base,
            lift=state.lift_deg + lift,
            rotate=state.rotate_deg + rotate,
            claw=state.claw_deg + claw,
            speed_pct=speed_pct,
            origin=origin,
        )

    def _pose_by_name(self, name: str) -> Pose | None:
        poses = {
            "home": self.config.home_pose,
            "survey": self.config.survey_pose,
            "pickup_ready": self.config.pickup_ready_pose,
            "drop_ready": self.config.drop_ready_pose,
            "inspect": self.config.inspect_pose,
        }
        return poses.get(name)

    def _lift_delta(self, action: ActionRequest, *, up: bool) -> int:
        step = self._step_from_action(action, "lift")
        if self.config.lift_up_increases:
            return step if up else -step
        return -step if up else step

    def _step_from_action(self, action: ActionRequest, joint: str) -> int:
        default = int(self.config.movement_steps[joint])
        raw = action.payload.get("step_deg")
        if raw is None:
            raw = action.payload.get(f"{joint}_step_deg")
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return max(1, min(45, value))

    def _speed_from_action(self, action: ActionRequest) -> int | None:
        raw = action.payload.get("speed_pct")
        if raw is None:
            return None
        try:
            return self._speed(int(raw))
        except (TypeError, ValueError):
            return None

    def _speed(self, value: int | None) -> int:
        speed = value if value is not None else self.config.default_speed_pct
        return max(5, min(100, int(speed)))

    def _clamp(self, joint: str, value: int) -> int:
        limits = self.config.servo_limits[joint]
        return max(limits.min_deg, min(limits.max_deg, int(value)))

    def _state_from_pose(self, pose: Pose) -> ArmState:
        return ArmState(
            base_deg=pose.base_deg,
            lift_deg=pose.lift_deg,
            rotate_deg=pose.rotate_deg,
            claw_deg=pose.claw_deg,
            range_mm=-1,
            estop=False,
            last_error="",
        )

    def _state_from_command(self, command: ArmCommand) -> ArmState:
        return ArmState(
            base_deg=command.base_deg,
            lift_deg=command.lift_deg,
            rotate_deg=command.rotate_deg,
            claw_deg=command.claw_deg,
            range_mm=-1,
            estop=False,
            last_error="",
        )
