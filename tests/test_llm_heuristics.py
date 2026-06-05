import unittest

from ai.llm_agent import LLMIntentAgent
from config import load_config
from ai.memory_store import MemoryStore
from ai.environment import EnvironmentMap


class LLMHeuristicTests(unittest.TestCase):
    def setUp(self) -> None:
        config = load_config()
        self.agent = LLMIntentAgent(
            config,
            MemoryStore(config.memory_db_path),
            EnvironmentMap(),
        )

    def test_get_my_pills_maps_to_pick(self) -> None:
        action = self.agent._heuristic_action("get my pills", source="web")
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.intent, "pick_object")
        self.assertEqual(action.payload.get("label"), "bottle")

    def test_bring_the_remote_maps_to_pick(self) -> None:
        action = self.agent._heuristic_action("bring the remote", source="web")
        self.assertIsNotNone(action)
        assert action is not None
        self.assertEqual(action.intent, "pick_object")
        self.assertEqual(action.payload.get("label"), "remote")
