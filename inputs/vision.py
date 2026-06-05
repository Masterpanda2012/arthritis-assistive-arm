from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from config import RuntimeConfig
from inputs.depth import DepthEstimator, fuse_depth_with_lidar
from models import ActionRequest, ArmState, VisionTarget
from motion.calibration import (
    ArmCalibration,
    bbox_center_depth_mm,
    load_calibration,
    pixel_depth_to_camera_mm,
)

try:
    from ultralytics import YOLO
except ImportError:  # pragma: no cover
    YOLO = None


LOGGER = logging.getLogger(__name__)

# Shared status for telemetry / web console (updated by vision_loop).
_pipeline_status: dict[str, Any] = {
    "yolo_stride": 2,
    "frames": 0,
    "detections": 0,
    "last_labels": {},
    "depth": {"enabled": False, "ready": False, "summary": "depth: —"},
}


def vision_pipeline_status() -> dict[str, Any]:
    return dict(_pipeline_status)


async def vision_loop(
    config: RuntimeConfig,
    action_queue: asyncio.Queue[ActionRequest],
    stop_event: asyncio.Event,
    state_provider: Callable[[], tuple[ArmState, float]],
    frame_queue: asyncio.Queue[Any],
    *,
    environment_cb: Callable[[VisionTarget, ArmState, ArmCalibration | None], None] | None = None,
) -> None:
    if YOLO is None:
        LOGGER.warning("Vision input disabled because ultralytics is not installed.")
        return

    try:
        model = YOLO(str(config.vision_model_path))
    except Exception as exc:
        LOGGER.warning("Unable to load YOLO model %s (%s). Vision input disabled.", config.vision_model_path, exc)
        return

    depth_estimator = DepthEstimator(
        model_id=config.depth_model_id,
        enabled=config.features.enable_depth,
    )
    calibration = load_calibration(config.calibration_path)
    if calibration is None and config.calibration_path.exists() is False:
        from motion.calibration import default_calibration, save_calibration

        calibration = default_calibration()
        try:
            save_calibration(config.calibration_path, calibration)
            LOGGER.info("Wrote default camera-to-arm calibration to %s (run calibration sweep to refine).", config.calibration_path)
        except OSError as exc:
            LOGGER.warning("Could not write default calibration: %s", exc)

    yolo_stride = max(1, int(getattr(config, "vision_frame_stride", 2)))
    depth_stride = max(2, int(getattr(config, "vision_depth_stride", 3)))
    imgsz = int(getattr(config, "vision_imgsz", 480))

    last_emit = 0.0
    last_summary_log = 0.0
    depth_counter = 0
    last_depth_map = None
    smoothed_coords: dict[str, tuple[float, tuple[float, float, float]]] = {}
    confirm_label: str | None = None
    confirm_streak = 0
    frame_idx = 0
    cached_results = None

    _pipeline_status["yolo_stride"] = yolo_stride
    _pipeline_status["depth"]["enabled"] = config.features.enable_depth

    LOGGER.info(
        "Vision input started with YOLO %s (stride=%d, imgsz=%d); %s.",
        config.vision_model_path,
        yolo_stride,
        imgsz,
        depth_estimator.status_summary(),
    )

    while not stop_event.is_set():
        try:
            frame = await asyncio.wait_for(frame_queue.get(), timeout=1.0)
        except asyncio.TimeoutError:
            continue

        frame_idx += 1
        _pipeline_status["frames"] = frame_idx

        state, state_age = state_provider()
        have_lidar = state.range_mm > 0 and state_age <= config.tf_luna_state_max_age_s
        reported_range = state.range_mm if have_lidar else -1

        depth_map = last_depth_map
        if config.features.enable_depth:
            depth_counter += 1
            run_depth = (depth_counter % depth_stride == 0) or (last_depth_map is None and not have_lidar)
            if have_lidar and depth_counter % (depth_stride * 2) != 0:
                run_depth = False
            if run_depth:
                depth_map = await asyncio.to_thread(
                    depth_estimator.estimate_depth_mm,
                    frame,
                )
                if depth_map is not None:
                    last_depth_map = depth_map
            _pipeline_status["depth"] = depth_estimator.telemetry()

        run_yolo = (frame_idx % yolo_stride == 1) or cached_results is None
        if run_yolo:
            try:
                cached_results = await asyncio.to_thread(
                    model.predict,
                    source=frame,
                    conf=config.vision_confidence,
                    verbose=False,
                    imgsz=imgsz,
                )
            except Exception as exc:
                LOGGER.warning("YOLO prediction failed: %s", exc)
                await asyncio.sleep(0.05)
                continue

        target, all_labels = _extract_target_with_depth(
            cached_results,
            depth_map=depth_map,
            calibration=calibration,
            range_mm=reported_range,
            center_tolerance=config.vision_center_tolerance,
            fuse_lidar=have_lidar,
        )

        now = time.monotonic()
        _pipeline_status["last_labels"] = dict(all_labels)
        if all_labels:
            _pipeline_status["detections"] = _pipeline_status.get("detections", 0) + 1

        if all_labels and now - last_summary_log > 2.0:
            depth_note = ""
            if target is not None and target.has_3d:
                depth_note = f" @ {int(target.depth_mm)}mm"
            elif have_lidar:
                depth_note = f" (LiDAR {reported_range}mm)"
            LOGGER.info(
                "Vision detections: %s%s",
                ", ".join(f"{lbl}×{cnt}" for lbl, cnt in sorted(all_labels.items())),
                depth_note,
            )
            last_summary_log = now

        for lbl in list(smoothed_coords.keys()):
            t_last, _ = smoothed_coords[lbl]
            if now - t_last > 4.0:
                del smoothed_coords[lbl]

        if target is not None:
            if target.has_3d:
                alpha = 0.38
                lbl = target.label
                if lbl in smoothed_coords:
                    _, (prev_x, prev_y, prev_z) = smoothed_coords[lbl]
                    smoothed_x = prev_x + alpha * (target.camera_x_mm - prev_x)
                    smoothed_y = prev_y + alpha * (target.camera_y_mm - prev_y)
                    smoothed_z = prev_z + alpha * (target.camera_z_mm - prev_z)
                else:
                    smoothed_x, smoothed_y, smoothed_z = (
                        target.camera_x_mm,
                        target.camera_y_mm,
                        target.camera_z_mm,
                    )

                smoothed_coords[lbl] = (now, (smoothed_x, smoothed_y, smoothed_z))

                from dataclasses import replace

                target = replace(
                    target,
                    camera_x_mm=smoothed_x,
                    camera_y_mm=smoothed_y,
                    camera_z_mm=smoothed_z,
                )

            if environment_cb is not None:
                try:
                    environment_cb(target, state, calibration)
                except Exception as exc:
                    LOGGER.debug("Environment callback failed: %s", exc)

        if target is None:
            confirm_label = None
            confirm_streak = 0
            continue

        if target.label == confirm_label:
            confirm_streak += 1
        else:
            confirm_label = target.label
            confirm_streak = 1
        if confirm_streak < 2:
            continue

        if now - last_emit < config.vision_cooldown_s:
            continue

        last_emit = now
        can_pick = have_lidar or target.has_3d
        if not can_pick:
            LOGGER.info(
                "Vision saw '%s' (conf=%.2f) but no depth/LiDAR; surfacing detection only.",
                target.label,
                target.confidence,
            )
            await action_queue.put(
                ActionRequest(
                    source="vision",
                    intent="vision_target",
                    payload={"target": target, "label": target.label},
                )
            )
            continue

        await action_queue.put(
            ActionRequest(
                source="vision",
                intent="pick_object",
                payload={"target": target, "label": target.label},
                requires_confirmation=True,
            )
        )


def _extract_target_with_depth(
    results,
    *,
    depth_map,
    calibration: ArmCalibration | None,
    range_mm: int,
    center_tolerance: float,
    fuse_lidar: bool = False,
) -> tuple[VisionTarget | None, dict[str, int]]:
    counts: dict[str, int] = {}
    if not results:
        return None, counts

    result = results[0]
    boxes = getattr(result, "boxes", None)
    names = getattr(result, "names", {})
    if boxes is None or len(boxes) == 0:
        return None, counts

    frame_width = float(result.orig_shape[1])
    frame_height = float(result.orig_shape[0])

    best_target: VisionTarget | None = None
    best_score = -1.0
    for box in boxes:
        conf = float(box.conf[0])
        cls = int(box.cls[0])
        label = str(names.get(cls, f"class_{cls}"))
        counts[label] = counts.get(label, 0) + 1
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        center_x = ((x1 + x2) / 2.0) / frame_width
        center_y = ((y1 + y2) / 2.0) / frame_height
        u_px = (x1 + x2) / 2.0
        v_px = (y1 + y2) / 2.0

        depth_mm = -1.0
        cam_x = cam_y = cam_z = 0.0
        has_3d = False
        if depth_map is not None and calibration is not None:
            depth_mm = bbox_center_depth_mm(
                depth_map,
                x1,
                y1,
                x2,
                y2,
                frame_width=int(frame_width),
                frame_height=int(frame_height),
            )
            if depth_mm > 0 and fuse_lidar and range_mm > 0:
                depth_mm = fuse_depth_with_lidar(depth_mm, range_mm)
            if depth_mm > 0:
                intr = calibration.intrinsics
                if intr.cx <= 2.0:
                    cx_px = intr.cx * frame_width
                    cy_px = intr.cy * frame_height
                    focal = intr.focal_length_px * max(frame_width, frame_height) / 640.0
                else:
                    cx_px = intr.cx
                    cy_px = intr.cy
                    focal = intr.focal_length_px
                from motion.calibration import CameraIntrinsics

                pix_intr = CameraIntrinsics(focal_length_px=focal, cx=cx_px, cy=cy_px)
                cam_x, cam_y, cam_z = pixel_depth_to_camera_mm(
                    u_px=u_px,
                    v_px=v_px,
                    depth_mm=depth_mm,
                    intrinsics=pix_intr,
                )
                has_3d = cam_z > 50.0

        effective_range = int(range_mm if range_mm > 0 else depth_mm)
        center_penalty = abs(center_x - 0.5) + abs(center_y - 0.5) * 0.25
        if center_penalty > center_tolerance and not has_3d:
            continue
        adl_boost = 0.08 if label in {"bottle", "cup", "cell phone", "remote", "book"} else 0.0
        score = conf - center_penalty * 0.32 + (0.18 if has_3d else 0.0) + adl_boost
        if score <= best_score:
            continue
        best_score = score
        best_target = VisionTarget(
            label=label,
            confidence=conf,
            image_x=center_x,
            image_y=center_y,
            range_mm=effective_range,
            timestamp=time.time(),
            depth_mm=depth_mm,
            camera_x_mm=cam_x,
            camera_y_mm=cam_y,
            camera_z_mm=cam_z,
            has_3d=has_3d,
        )

    return best_target, counts
