from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from models import ActionRequest, ArmCommand


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    source TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    requires_confirmation INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    outcome_json TEXT NOT NULL
                )
                """
            )
            # User-taught gesture → action overrides. Populated by the
            # "teach" command flow so the arm adapts to each operator's
            # preferences and those preferences survive restarts.
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS gesture_bindings (
                    gesture TEXT PRIMARY KEY,
                    intent TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            # User-taught phrase → action overrides ("when I say X, do Y").
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS phrase_bindings (
                    phrase TEXT PRIMARY KEY,
                    intent TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS custom_gestures (
                    gesture_id TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    template_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )

    def record_action(self, action: ActionRequest, *, status: str, outcome: dict | None = None) -> None:
        payload_json = json.dumps(action.payload, default=str, sort_keys=True)
        outcome_json = json.dumps(outcome or {}, default=str, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO command_log (
                    timestamp, source, intent, payload_json,
                    requires_confirmation, status, outcome_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    time.time(),
                    action.source,
                    action.intent,
                    payload_json,
                    1 if action.requires_confirmation else 0,
                    status,
                    outcome_json,
                ),
            )

    def record_execution(self, action: ActionRequest, commands: tuple[ArmCommand, ...]) -> None:
        serialized = [
            {
                "base_deg": cmd.base_deg,
                "lift_deg": cmd.lift_deg,
                "rotate_deg": cmd.rotate_deg,
                "claw_deg": cmd.claw_deg,
                "speed_pct": cmd.speed_pct,
                "origin": cmd.origin,
            }
            for cmd in commands
        ]
        self.record_action(action, status="executed", outcome={"commands": serialized})

    def frequent_labels(self, limit: int = 5) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT json_extract(payload_json, '$.label') AS label, COUNT(*) AS count
                FROM command_log
                WHERE status = 'executed'
                  AND json_extract(payload_json, '$.label') IS NOT NULL
                GROUP BY label
                ORDER BY count DESC, label ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["label"]) for row in rows if row["label"]]

    # ------------------------------------------------------------------
    # Learned bindings (gestures & phrases the user taught us)
    # ------------------------------------------------------------------

    def load_gesture_bindings(self) -> dict[str, tuple[str, dict]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT gesture, intent, payload_json FROM gesture_bindings"
            ).fetchall()
        out: dict[str, tuple[str, dict]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
            out[str(row["gesture"])] = (str(row["intent"]), payload)
        return out

    def save_gesture_binding(self, gesture: str, intent: str, payload: dict | None) -> None:
        payload_json = json.dumps(payload or {}, default=str, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO gesture_bindings (gesture, intent, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(gesture) DO UPDATE SET
                    intent = excluded.intent,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (gesture, intent, payload_json, time.time()),
            )

    def delete_gesture_binding(self, gesture: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM gesture_bindings WHERE gesture = ?", (gesture,))

    def clear_gesture_bindings(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM gesture_bindings")

    def load_phrase_bindings(self) -> dict[str, tuple[str, dict]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT phrase, intent, payload_json FROM phrase_bindings"
            ).fetchall()
        out: dict[str, tuple[str, dict]] = {}
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}")
                if not isinstance(payload, dict):
                    payload = {}
            except Exception:
                payload = {}
            out[str(row["phrase"])] = (str(row["intent"]), payload)
        return out

    def save_phrase_binding(self, phrase: str, intent: str, payload: dict | None) -> None:
        payload_json = json.dumps(payload or {}, default=str, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO phrase_bindings (phrase, intent, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phrase) DO UPDATE SET
                    intent = excluded.intent,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (phrase, intent, payload_json, time.time()),
            )

    def clear_phrase_bindings(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM phrase_bindings")

    def load_custom_gestures(self) -> dict[str, Any]:
        from ai.custom_gestures import CustomGesture

        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM custom_gestures ORDER BY display_name ASC"
            ).fetchall()
        out: dict[str, CustomGesture] = {}
        for row in rows:
            item = CustomGesture.from_row(row)
            out[item.gesture_id] = item
        return out

    def save_custom_gesture(self, item: Any) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO custom_gestures (
                    gesture_id, display_name, description, intent,
                    payload_json, template_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gesture_id) DO UPDATE SET
                    display_name = excluded.display_name,
                    description = excluded.description,
                    intent = excluded.intent,
                    payload_json = excluded.payload_json,
                    template_json = excluded.template_json,
                    created_at = excluded.created_at
                """,
                (
                    item.gesture_id,
                    item.display_name,
                    item.description,
                    item.intent,
                    json.dumps(item.payload, default=str, sort_keys=True),
                    json.dumps(item.template),
                    item.created_at,
                ),
            )

    def delete_custom_gesture(self, gesture_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM custom_gestures WHERE gesture_id = ?", (gesture_id,))

    def recent_summary(self, limit: int = 5) -> str:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT intent, source, timestamp
                FROM command_log
                WHERE status = 'executed'
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        if not rows:
            return "No prior successful commands."
        parts = [f"{row['intent']} from {row['source']}" for row in rows]
        return "Recent successful commands: " + "; ".join(parts)
