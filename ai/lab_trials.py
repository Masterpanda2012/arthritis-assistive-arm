"""Structured lab trials for arthritis accessibility evaluation."""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import ActionRequest, ArmState, PlannerResult

# Recommended minimum trials per condition for science-fair / publication-style claims.
RECOMMENDED_TRIALS_PER_CONDITION = 20

TRIAL_MODES = (
    "manual",
    "adaptive",
    "dual_perception",
    "voice",
    "gesture",
    "vision",
    "panel",
    "panel_adl",
)

TRIAL_TARGETS = (
    "water bottle",
    "tv remote",
    "medication bottle",
    "cup",
    "taped target",
    "custom",
)


@dataclass
class LabTrialLogger:
    """Records trials with timing, corrections, and arthritis-specific metadata."""

    path: Path
    motor_level: str = "moderate"
    active: dict | None = None
    recent: deque = field(default_factory=lambda: deque(maxlen=24))
    _seq: int = 0

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def start(
        self,
        *,
        mode: str,
        target: str,
        state: ArmState,
        motor_level: str | None = None,
        input_method: str = "",
        object_type: str = "",
        tremor_simulated: bool = False,
        note: str = "",
    ) -> dict:
        if self.active is not None:
            self.finish(success=False, state=state, note="auto-closed by new trial")
        self._seq += 1
        now = time.time()
        mode_norm = (mode or "unspecified").strip().lower()
        target_norm = (target or "target").strip()
        self.active = {
            "trial_id": self._seq,
            "mode": mode_norm,
            "target": target_norm,
            "motor_level": (motor_level or self.motor_level).strip().lower(),
            "input_method": (input_method or mode_norm).strip().lower(),
            "object_type": (object_type or target_norm).strip().lower(),
            "tremor_simulated": bool(tremor_simulated),
            "used_3d_vision": False,
            "alignment_error_mm": None,
            "started_at": self._iso(now),
            "started_ts": now,
            "started_mono": time.monotonic(),
            "start_state": self._state_dict(state),
            "commands": 0,
            "corrections": 0,
            "confirmations": 0,
            "events": [],
            "notes": [],
        }
        if note.strip():
            self.active["notes"].append(note.strip())
        self._event(
            "trial_start",
            mode=self.active["mode"],
            target=self.active["target"],
            motor_level=self.active["motor_level"],
            tremor_simulated=self.active["tremor_simulated"],
        )
        return dict(self.active)

    def record_command(
        self,
        action: ActionRequest,
        plan: PlannerResult,
        executed: bool,
        state: ArmState,
    ) -> None:
        if self.active is None:
            return
        self.active["commands"] += 1
        if action.requires_confirmation or action.intent in {"confirm_yes", "confirm_no"}:
            self.active["confirmations"] += 1
        if action.intent in {
            "base_left", "base_right", "lift_up", "lift_down",
            "rotate_left", "rotate_right",
        }:
            self.active["corrections"] += 1
        payload = action.payload or {}
        target = payload.get("target")
        if getattr(target, "has_3d", False):
            self.active["used_3d_vision"] = True
        if payload.get("adl_id"):
            self.active["object_type"] = str(payload.get("adl_id"))
        self._event(
            "command",
            source=action.source,
            intent=action.intent,
            payload=_json_safe(payload),
            plan_kind=plan.kind,
            executed=executed,
            state=self._state_dict(state),
        )

    def add_correction(self, note: str = "") -> None:
        if self.active is None:
            return
        self.active["corrections"] += 1
        self._event("correction", note=note)

    def add_note(self, note: str) -> None:
        if self.active is None or not note.strip():
            return
        self.active["notes"].append(note.strip())
        self._event("note", text=note.strip())

    def finish(
        self,
        *,
        success: bool,
        state: ArmState,
        final_distance_cm: float | None = None,
        alignment_error_mm: float | None = None,
        note: str = "",
    ) -> dict | None:
        if self.active is None:
            return None

        now_mono = time.monotonic()
        now = time.time()
        record = dict(self.active)
        record.pop("started_mono", None)
        record["ended_at"] = self._iso(now)
        record["duration_s"] = round(now_mono - float(self.active["started_mono"]), 2)
        record["success"] = bool(success)
        record["end_state"] = self._state_dict(state)
        if final_distance_cm is not None:
            record["final_distance_cm"] = round(float(final_distance_cm), 2)
        if alignment_error_mm is not None:
            record["alignment_error_mm"] = round(float(alignment_error_mm), 2)
        if note.strip():
            record["notes"] = [*record.get("notes", []), note.strip()]
        self._write(record)
        self.recent.append(record)
        self.active = None
        return record

    def snapshot(self) -> dict:
        active = None
        if self.active is not None:
            active = {
                "trial_id": self.active["trial_id"],
                "mode": self.active["mode"],
                "target": self.active["target"],
                "motor_level": self.active["motor_level"],
                "tremor_simulated": self.active["tremor_simulated"],
                "elapsed_s": round(time.monotonic() - float(self.active["started_mono"]), 1),
                "commands": self.active["commands"],
                "corrections": self.active["corrections"],
                "confirmations": self.active["confirmations"],
                "used_3d_vision": self.active["used_3d_vision"],
                "notes": list(self.active["notes"])[-3:],
            }
        return {
            "active": active,
            "recommended_per_condition": RECOMMENDED_TRIALS_PER_CONDITION,
            "recent": [
                {
                    "trial_id": row["trial_id"],
                    "mode": row["mode"],
                    "target": row["target"],
                    "motor_level": row.get("motor_level"),
                    "duration_s": row["duration_s"],
                    "success": row["success"],
                    "corrections": row["corrections"],
                    "commands": row["commands"],
                    "tremor_simulated": row.get("tremor_simulated"),
                    "used_3d_vision": row.get("used_3d_vision"),
                    "final_distance_cm": row.get("final_distance_cm"),
                    "alignment_error_mm": row.get("alignment_error_mm"),
                }
                for row in list(self.recent)[-10:]
            ],
            "path": str(self.path),
        }

    def _event(self, kind: str, **payload: Any) -> None:
        if self.active is None:
            return
        self.active["events"].append({
            "t_s": round(time.monotonic() - float(self.active["started_mono"]), 2),
            "kind": kind,
            **_json_safe_dict(payload),
        })

    def _write(self, record: dict) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")

    @staticmethod
    def _iso(ts: float) -> str:
        return datetime.fromtimestamp(ts, timezone.utc).isoformat()

    @staticmethod
    def _state_dict(state: ArmState) -> dict:
        return {
            "base_deg": state.base_deg,
            "lift_deg": state.lift_deg,
            "rotate_deg": state.rotate_deg,
            "claw_deg": state.claw_deg,
            "range_mm": state.range_mm,
            "estop": state.estop,
            "last_error": state.last_error,
        }


def _json_safe(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def _json_safe_dict(payload: dict) -> dict:
    return {k: _json_safe(v) for k, v in payload.items()}
