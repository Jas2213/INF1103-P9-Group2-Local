"""
Phase 2 tests for ai_manager. Run from the project root:

    python -m unittest tests.test_ai_manager -v

No live API is ever called: AIManager is tested with MockAIProvider, and
GeminiProvider is tested against a fake Google SDK. Retry pauses are recorded
instead of slept, so the run is instant.
"""

import json
import os
import sys
import types
import unittest
from unittest.mock import patch

from managers.ai_manager import (
    AIManager,
    AIProvider,
    GeminiProvider,
    MockAIProvider,
    PromptBuilder,
    ResponseParser,
)
from models import (
    AIConnectionError,
    AIResponseError,
    AIResult,
    HistoryRecord,
    Intake,
    Patient,
    Severity,
    StaffOverride,
    Tier,
    Urgency,
)

GOOD_REPLY = {
    "summary": "Mild cough for two days.",
    "urgency": "non-urgent",
    "severity": "low",
    "recommended_tier": "nurse",
    "specialist": "general_practice",
    "flagged_symptoms": ["cough"],
    "history_linked": False,
    "confidence": 0.9,
}


def reply(**changes):
    data = dict(GOOD_REPLY)
    data.update(changes)
    return data


class CountingProvider(MockAIProvider):
    """MockAIProvider that also counts ping() calls."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pings = 0

    def ping(self):
        self.pings += 1
        super().ping()


class AIManagerTestCase(unittest.TestCase):
    """Base class: records retry pauses, gives a ready Intake, offers mock helpers."""

    def setUp(self):
        self.sleeps = []
        patcher = patch("managers.ai_manager.time.sleep", side_effect=self.sleeps.append)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.intake = Intake("persistent cough", duration="2 days", self_rated_severity=3,
                             red_flags_present=False)

    @staticmethod
    def make_manager(*replies, available=True):
        provider = MockAIProvider(list(replies), available=available)
        return AIManager(provider), provider

    def install_fake_genai(self, *outcomes):
        """
        Replace the Google SDK with a fake for this test. Outcomes are what the API
        produces in order: a str is returned as the reply text, an exception is raised.
        Returns a dict recording the Client arguments and the number of calls.
        """
        record = {"client_kwargs": None, "calls": 0}
        queue = list(outcomes)

        class FakeModels:
            def generate_content(self, **kwargs):
                record["calls"] += 1
                outcome = queue.pop(0)
                if isinstance(outcome, Exception):
                    raise outcome
                return types.SimpleNamespace(text=outcome)

        class FakeClient:
            def __init__(self, **kwargs):
                record["client_kwargs"] = kwargs
                self.models = FakeModels()

        genai = types.ModuleType("google.genai")
        genai.Client = FakeClient
        genai.types = types.SimpleNamespace(
            HttpOptions=lambda timeout: types.SimpleNamespace(timeout=timeout),
            GenerateContentConfig=lambda **kwargs: kwargs,
        )
        google = types.ModuleType("google")
        google.genai = genai
        patcher = patch.dict(sys.modules, {"google": google, "google.genai": genai})
        patcher.start()
        self.addCleanup(patcher.stop)
        return record


# ===========================================================================
# Part 1: response validation (replaces _validate_response tests)
# ===========================================================================

class TestResponseValidation(AIManagerTestCase):

    def test_valid_response_passes(self):
        self.assertTrue(ResponseParser().triage(json.dumps(GOOD_REPLY)).ok)

    def test_extra_fields_are_allowed(self):
        self.assertTrue(ResponseParser().triage(json.dumps(reply(note="x", proposed_slot=""))).ok)

    def test_each_missing_required_field_fails(self):
        for field in GOOD_REPLY:
            with self.subTest(field=field):
                data = reply()
                del data[field]
                with self.assertRaises(AIResponseError):
                    ResponseParser().triage(json.dumps(data))

    def test_wrong_field_types_fail(self):
        wrong_types = {
            "summary": 123, "urgency": 5, "severity": None, "recommended_tier": [],
            "specialist": 1, "flagged_symptoms": "chest pain", "history_linked": "yes",
            "confidence": "high",
        }
        for field, bad_value in wrong_types.items():
            with self.subTest(field=field):
                with self.assertRaises(AIResponseError):
                    ResponseParser().triage(json.dumps(reply(**{field: bad_value})))

    def test_unknown_enum_values_fail(self):
        bad_values = {"urgency": "soon", "severity": "critical", "recommended_tier": "surgeon"}
        for field, bad_value in bad_values.items():
            with self.subTest(field=field):
                with self.assertRaises(AIResponseError):
                    ResponseParser().triage(json.dumps(reply(**{field: bad_value})))

    def test_confidence_inside_range_passes(self):
        for value in (0.0, 1.0, 0, 1, 0.5):
            with self.subTest(confidence=value):
                self.assertTrue(ResponseParser().triage(json.dumps(reply(confidence=value))).ok)

    def test_confidence_outside_range_or_boolean_fails(self):
        # Booleans are a regression check: in Python, True counts as the number 1.
        for value in (-0.1, 1.5, 2, True, False):
            with self.subTest(confidence=value):
                with self.assertRaises(AIResponseError):
                    ResponseParser().triage(json.dumps(reply(confidence=value)))

    def test_non_object_replies_fail(self):
        for raw in ("null", "[]", '"text"', "5", "not json", ""):
            with self.subTest(raw=raw):
                with self.assertRaises(AIResponseError):
                    ResponseParser().triage(raw)


class TestFollowUpValidation(AIManagerTestCase):

    def test_empty_list_is_valid(self):
        self.assertEqual(ResponseParser().follow_up(json.dumps({"questions": []})), [])

    def test_up_to_three_questions_are_valid(self):
        raw = json.dumps({"questions": ["A?", "B?", "C?"]})
        self.assertEqual(ResponseParser().follow_up(raw), ["A?", "B?", "C?"])

    def test_four_questions_are_invalid(self):
        with self.assertRaises(AIResponseError):
            ResponseParser().follow_up(json.dumps({"questions": ["A?", "B?", "C?", "D?"]}))

    def test_blank_or_non_string_questions_are_invalid(self):
        for bad in (["   "], [""], [1], ["A?", None]):
            with self.subTest(questions=bad):
                with self.assertRaises(AIResponseError):
                    ResponseParser().follow_up(json.dumps({"questions": bad}))

    def test_missing_key_or_wrong_shape_is_invalid(self):
        for bad in ({}, {"questions": "A?"}, [], None, "text"):
            with self.subTest(value=bad):
                with self.assertRaises(AIResponseError):
                    ResponseParser().follow_up(json.dumps(bad))


# ===========================================================================
# Part 2: retry logic (replaces _request_validated tests)
# ===========================================================================

class TestRetry(AIManagerTestCase):

    def test_valid_first_reply_is_used_without_retry(self):
        ai, provider = self.make_manager(GOOD_REPLY)
        self.assertTrue(ai.triage(self.intake).ok)
        self.assertEqual(len(provider.prompts), 1)
        self.assertEqual(self.sleeps, [])

    def test_connection_failure_then_valid_reply_recovers(self):
        ai, provider = self.make_manager(AIConnectionError("503"), GOOD_REPLY)
        self.assertTrue(ai.triage(self.intake).ok)
        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(self.sleeps, [AIManager.RETRY_DELAY_SECONDS])

    def test_invalid_json_then_valid_reply_recovers_without_pause(self):
        ai, provider = self.make_manager("this is not json", GOOD_REPLY)
        self.assertTrue(ai.triage(self.intake).ok)
        self.assertEqual(len(provider.prompts), 2)
        self.assertEqual(self.sleeps, [])  # the pause is only for connection failures

    def test_invalid_schema_then_valid_reply_recovers(self):
        ai, provider = self.make_manager(reply(severity="extreme"), GOOD_REPLY)
        self.assertTrue(ai.triage(self.intake).ok)
        self.assertEqual(len(provider.prompts), 2)

    def test_failing_validation_every_time_gives_failure(self):
        ai, provider = self.make_manager(reply(urgency="soon"), reply(urgency="soon"))
        result = ai.triage(self.intake)
        self.assertFalse(result.ok)
        self.assertTrue(result.error_reason)
        self.assertEqual(len(provider.prompts), AIManager.MAX_RETRIES + 1)

    def test_valid_json_of_wrong_shape_gives_failure(self):
        ai, _ = self.make_manager("[]", "[]")
        self.assertFalse(ai.triage(self.intake).ok)

    def test_connection_failing_every_time_pauses_only_once(self):
        ai, provider = self.make_manager(AIConnectionError("down"), AIConnectionError("down"))
        result = ai.triage(self.intake)
        self.assertFalse(result.ok)
        self.assertIn("down", result.error_reason)
        self.assertEqual(len(provider.prompts), AIManager.MAX_RETRIES + 1)
        self.assertEqual(self.sleeps, [AIManager.RETRY_DELAY_SECONDS])  # not after the last try

    def test_failed_attempts_are_logged_as_warnings(self):
        ai, _ = self.make_manager("not json", "not json")
        with self.assertLogs("ai_manager", level="WARNING") as captured:
            ai.triage(self.intake)
        self.assertTrue(any("not valid JSON" in line for line in captured.output))


# ===========================================================================
# Part 3: public methods
# ===========================================================================

class TestTriage(AIManagerTestCase):

    def test_good_response_returns_ai_result_with_fields(self):
        ai, _ = self.make_manager(GOOD_REPLY)
        result = ai.triage(self.intake)
        self.assertIsInstance(result, AIResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.summary, "Mild cough for two days.")
        self.assertEqual(result.urgency, Urgency.NON_URGENT)
        self.assertEqual(result.severity, Severity.LOW)
        self.assertEqual(result.recommended_tier, Tier.NURSE)

    def test_garbage_text_gives_failure_instead_of_crashing(self):
        ai, _ = self.make_manager("I am not JSON at all", "still not JSON")
        result = ai.triage(self.intake)
        self.assertFalse(result.ok)
        self.assertTrue(result.error_reason)

    def test_history_is_passed_into_the_prompt(self):
        old = HistoryRecord(Intake("old knee pain"), ai_result=AIResult.from_dict(GOOD_REPLY),
                            timestamp="2030-01-01T10:00:00")
        ai, provider = self.make_manager(GOOD_REPLY)
        ai.triage(self.intake, Patient("S1234567A", history=[old]))
        self.assertIn("old knee pain", provider.prompts[0])

    def test_is_usable(self):
        ai, _ = self.make_manager(GOOD_REPLY)
        self.assertTrue(ai.is_usable(ai.triage(self.intake)))
        self.assertFalse(ai.is_usable(AIResult.failure("down")))
        self.assertFalse(ai.is_usable(None))


class TestFollowUpQuestions(AIManagerTestCase):

    def setUp(self):
        super().setUp()
        self.initial = AIResult.from_dict(GOOD_REPLY)

    def test_good_response_returns_questions(self):
        ai, _ = self.make_manager({"questions": ["Any fever?", "Any allergies?"]})
        self.assertEqual(ai.follow_up_questions(self.intake, self.initial),
                         ["Any fever?", "Any allergies?"])

    def test_empty_list_is_a_valid_answer(self):
        ai, _ = self.make_manager({"questions": []})
        self.assertEqual(ai.follow_up_questions(self.intake, self.initial), [])

    def test_connection_failure_gives_no_questions(self):
        ai, _ = self.make_manager(AIConnectionError("down"), AIConnectionError("down"))
        self.assertEqual(ai.follow_up_questions(self.intake, self.initial), [])

    def test_too_many_questions_gives_no_questions(self):
        four = {"questions": ["1?", "2?", "3?", "4?"]}
        ai, _ = self.make_manager(four, four)
        self.assertEqual(ai.follow_up_questions(self.intake, self.initial), [])


class TestReassessment(AIManagerTestCase):

    def setUp(self):
        super().setUp()
        self.initial = AIResult.from_dict(GOOD_REPLY)
        self.answered = self.intake.with_follow_up(["Any fever?"], ["Yes, since Monday"])

    def test_good_response_returns_updated_result(self):
        ai, _ = self.make_manager(reply(severity="medium"))
        result = ai.reassess(self.answered, self.initial)
        self.assertTrue(result.ok)
        self.assertEqual(result.severity, Severity.MEDIUM)

    def test_prompt_contains_the_follow_up_questions_and_answers(self):
        ai, provider = self.make_manager(GOOD_REPLY)
        ai.reassess(self.answered, self.initial)
        self.assertIn("Any fever?", provider.prompts[0])
        self.assertIn("Yes, since Monday", provider.prompts[0])

    def test_connection_failure_gives_failure(self):
        ai, _ = self.make_manager(AIConnectionError("down"), AIConnectionError("down"))
        result = ai.reassess(self.answered, self.initial)
        self.assertFalse(result.ok)
        self.assertTrue(result.error_reason)


class TestStaffOverride(AIManagerTestCase):

    def setUp(self):
        super().setUp()
        self.original = AIResult.from_dict(GOOD_REPLY)

    def test_good_response_applies_the_verdict(self):
        ai, provider = self.make_manager(reply(urgency="urgent"))
        result = ai.apply_staff_overrides(self.intake, self.original,
                                          [StaffOverride("urgency", "urgent")])
        self.assertTrue(result.ok)
        self.assertEqual(result.urgency, Urgency.URGENT)
        self.assertIn("- urgency: urgent", provider.prompts[0])

    def test_ai_proposed_slot_in_reply_is_ignored(self):
        """The slot always comes from staff; a slot the AI adds must not break parsing."""
        ai, _ = self.make_manager(reply(proposed_slot="next week"))
        result = ai.apply_staff_overrides(self.intake, self.original,
                                          [StaffOverride("severity", "high")])
        self.assertTrue(result.ok)

    def test_connection_failure_gives_failure(self):
        ai, _ = self.make_manager(AIConnectionError("down"), AIConnectionError("down"))
        result = ai.apply_staff_overrides(self.intake, self.original,
                                          [StaffOverride("urgency", "urgent")])
        self.assertFalse(result.ok)


# ===========================================================================
# Part 4: providers, availability, API key and timeout
# ===========================================================================

class TestProviders(AIManagerTestCase):

    def test_providers_implement_the_interface(self):
        self.assertIsInstance(MockAIProvider(), AIProvider)
        self.assertIsInstance(GeminiProvider(api_key="x"), AIProvider)
        with self.assertRaises(TypeError):
            AIProvider()  # abstract: cannot be created

    def test_available_when_provider_answers(self):
        self.assertTrue(self.make_manager(available=True)[0].is_available())

    def test_unavailable_check_is_not_retried(self):
        provider = CountingProvider(available=False)
        self.assertFalse(AIManager(provider).is_available())
        self.assertEqual(provider.pings, 1)


class TestGeminiProvider(AIManagerTestCase):

    def test_no_api_key_is_a_connection_error_without_calling_the_api(self):
        record = self.install_fake_genai('{"ok": true}')
        with patch.dict(os.environ):
            os.environ.pop("GEMINI_API_KEY", None)
            with self.assertRaises(AIConnectionError):
                GeminiProvider().generate_json("prompt", {})
            self.assertFalse(AIManager(GeminiProvider()).is_available())
        self.assertEqual(record["calls"], 0)

    def test_key_is_read_from_the_environment(self):
        record = self.install_fake_genai("OK")
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            GeminiProvider().ping()
        self.assertEqual(record["client_kwargs"]["api_key"], "test-key")

    def test_client_receives_the_timeout_in_milliseconds(self):
        record = self.install_fake_genai("OK")
        with patch.object(GeminiProvider, "TIMEOUT_SECONDS", 2.5):
            GeminiProvider(api_key="k").ping()
        self.assertEqual(record["client_kwargs"]["http_options"].timeout, 2500)

    def test_timeout_setting_is_positive(self):
        self.assertGreater(GeminiProvider.TIMEOUT_SECONDS, 0)

    def test_timeout_becomes_connection_error(self):
        self.install_fake_genai(TimeoutError("timed out"))
        with self.assertRaises(AIConnectionError):
            GeminiProvider(api_key="k").generate_json("prompt", {})

    def test_empty_reply_is_a_response_error(self):
        self.install_fake_genai("")
        with self.assertRaises(AIResponseError):
            GeminiProvider(api_key="k").generate_json("prompt", {})

    def test_triage_fails_when_every_attempt_times_out(self):
        record = self.install_fake_genai(TimeoutError("t1"), TimeoutError("t2"))
        result = AIManager(GeminiProvider(api_key="k")).triage(self.intake)
        self.assertFalse(result.ok)
        self.assertTrue(result.error_reason)
        self.assertEqual(record["calls"], AIManager.MAX_RETRIES + 1)
        self.assertEqual(self.sleeps, [AIManager.RETRY_DELAY_SECONDS])

    def test_triage_recovers_when_the_retry_succeeds_after_a_timeout(self):
        record = self.install_fake_genai(TimeoutError("t1"), json.dumps(GOOD_REPLY))
        self.assertTrue(AIManager(GeminiProvider(api_key="k")).triage(self.intake).ok)
        self.assertEqual(record["calls"], 2)

    def test_follow_up_reassess_and_override_survive_timeouts(self):
        self.install_fake_genai(*[TimeoutError("t")] * 6)
        ai = AIManager(GeminiProvider(api_key="k"))
        original = AIResult.from_dict(GOOD_REPLY)
        self.assertEqual(ai.follow_up_questions(self.intake, original), [])
        self.assertFalse(ai.reassess(self.intake, original).ok)
        self.assertFalse(ai.apply_staff_overrides(
            self.intake, original, [StaffOverride("urgency", "urgent")]).ok)

    def test_availability_is_false_on_timeout_and_not_retried(self):
        record = self.install_fake_genai(TimeoutError("timed out"))
        self.assertFalse(AIManager(GeminiProvider(api_key="k")).is_available())
        self.assertEqual(record["calls"], 1)


# ===========================================================================
# Part 5: prompt building
# ===========================================================================

class TestPromptBuilding(AIManagerTestCase):

    @staticmethod
    def patient_with_visits(count):
        records = [
            HistoryRecord(Intake(f"visit{i}"), ai_result=AIResult.from_dict(GOOD_REPLY),
                          timestamp=f"2030-01-{i + 1:02d}T10:00:00")
            for i in range(count)
        ]
        return Patient("S1234567A", history=records)

    def test_intake_answers_appear_in_prompt(self):
        prompt = PromptBuilder().triage(self.intake)
        self.assertIn("persistent cough", prompt)
        self.assertIn("2 days", prompt)
        self.assertIn("(1-10): 3", prompt)

    def test_red_flags_show_yes_or_no(self):
        flagged = Intake("chest pain", duration="1 hour", self_rated_severity=8,
                         red_flags_present=True)
        self.assertIn("difficulty breathing: YES", PromptBuilder().triage(flagged))
        self.assertIn("difficulty breathing: no", PromptBuilder().triage(self.intake))

    def test_missing_optional_answers_show_unknown(self):
        prompt = PromptBuilder().triage(Intake("sore throat"))
        self.assertIn("How long: unknown", prompt)
        self.assertIn("Self-rated severity (1-10): unknown", prompt)

    def test_new_patient_has_no_prior_visits(self):
        self.assertIn("no prior visits", PromptBuilder().triage(self.intake, None))
        self.assertIn("no prior visits", PromptBuilder().triage(self.intake, Patient("S1234567A")))

    def test_only_the_last_five_visits_are_included(self):
        prompt = PromptBuilder().triage(self.intake, self.patient_with_visits(7))
        for kept in ("visit2", "visit3", "visit4", "visit5", "visit6"):
            self.assertIn(kept, prompt)
        for dropped in ("visit0", "visit1"):
            self.assertNotIn(dropped, prompt)

    def test_override_prompt_lists_each_staff_verdict(self):
        overrides = [StaffOverride("urgency", "urgent"),
                     StaffOverride("proposed_slot", "2030-01-10 09:00")]
        prompt = PromptBuilder().override(self.intake, AIResult.from_dict(GOOD_REPLY), overrides)
        self.assertIn("- urgency: urgent", prompt)
        self.assertIn("- proposed_slot: 2030-01-10 09:00", prompt)
        self.assertIn("persistent cough", prompt)

    def test_override_prompt_leaves_out_the_status_field(self):
        prompt = PromptBuilder().override(self.intake, AIResult.from_dict(GOOD_REPLY),
                                          [StaffOverride("severity", "high")])
        self.assertNotIn('"status"', prompt)


if __name__ == "__main__":
    unittest.main()