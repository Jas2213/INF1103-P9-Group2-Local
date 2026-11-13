import json
import tempfile
import unittest
from unittest.mock import MagicMock

from managers.temp.ai_manager import AIManager
from managers.temp.data_manager import DataManager
from managers.temp.io_manager import IOManager
from managers.temp.logic_manager import LogicManager


def ai_reply(**overrides):
    reply = {
        "summary": "mild cough", "urgency": "non-urgent", "severity": "low",
        "recommended_tier": "nurse", "specialist": "general_practice",
        "flagged_symptoms": ["cough"], "history_linked": False, "confidence": 0.8,
    }
    reply.update(overrides)
    return json.dumps(reply)


class TestAllManagers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.io = IOManager()
        self.ai = AIManager()
        self.logic = LogicManager()
        self.data = DataManager(base_dir=self.tmp.name)
        self.ai._client = MagicMock()  # no live API

    def _triage(self, text):
        self.ai._client.models.generate_content.return_value.text = text
        return self.ai.get_ai_triage_result({"description": "cough"})

    def test_non_urgent_pipeline(self):
        # IO
        self.assertTrue(self.io.validate_nric_format("S1234567A"))
        # AI
        result = self._triage(ai_reply())
        self.assertEqual(result["status"], "ok")
        # Logic
        outcome = self.logic.evaluate_triage_logic(result)
        self.assertEqual(outcome["decision"], LogicManager.DECISION_NON_URGENT)
        self.assertIsNotNone(outcome["proposed_slot"])
        # Data: pending case -> history
        self.data.save_pending_case("S1234567A", {"stage": "review", "ai_result": result})
        self.assertEqual(len(self.data.list_pending_cases("review")), 1)
        self.data.close_pending_case("S1234567A", {"symptom_description": "cough", "outcome": outcome})
        self.assertEqual(len(self.data.load_patient("S1234567A")["history"]), 1)

    def test_high_severity_goes_to_emergency_without_slot(self):
        result = self._triage(ai_reply(urgency="urgent", severity="high"))
        outcome = self.logic.evaluate_triage_logic(result)
        self.assertEqual(outcome["decision"], LogicManager.DECISION_URGENT)
        self.assertIsNone(outcome["proposed_slot"])

    def test_malformed_ai_reply_goes_to_manual_review(self):
        result = self._triage("not json")
        self.assertEqual(result["status"], "error")
        outcome = self.logic.evaluate_triage_logic(result)
        self.assertEqual(outcome["decision"], LogicManager.DECISION_MANUAL_REVIEW)


if __name__ == "__main__":
    unittest.main()