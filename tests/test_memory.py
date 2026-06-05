import tempfile
import unittest
from pathlib import Path

from ai.memory_store import MemoryStore
from models import ActionRequest, ArmCommand


class MemoryStoreTests(unittest.TestCase):
    def test_records_and_summarizes_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            store = MemoryStore(Path(tmpdir) / "memory.db")
            action = ActionRequest(source="voice", intent="pick_object", payload={"label": "cup"}, requires_confirmation=True)
            store.record_action(action, status="confirmed")
            store.record_execution(
                action,
                (
                    ArmCommand(90, 105, 90, 95, 35, "voice"),
                    ArmCommand(90, 115, 90, 45, 35, "voice"),
                ),
            )

            self.assertIn("cup", store.frequent_labels())
            self.assertIn("pick_object", store.recent_summary())
