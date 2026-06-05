import unittest

from ai.adl_tasks import match_adl_phrase, adl_to_action_request, yolo_label_matches_adl


class ADLTasksTests(unittest.TestCase):
    def test_match_medication_phrase(self) -> None:
        task = match_adl_phrase("please get my pills")
        self.assertIsNotNone(task)
        assert task is not None
        self.assertEqual(task.id, "medication")

    def test_adl_pick_payload(self) -> None:
        task = match_adl_phrase("get the remote")
        assert task is not None
        shaped = adl_to_action_request(task, source="voice")
        self.assertEqual(shaped["intent"], "pick_object")
        self.assertTrue(shaped["requires_confirmation"])
        self.assertEqual(shaped["payload"]["adl_id"], "remote")

    def test_yolo_label_synonym(self) -> None:
        self.assertTrue(yolo_label_matches_adl("bottle", "medication"))


if __name__ == "__main__":
    unittest.main()
