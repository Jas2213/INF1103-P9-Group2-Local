"""
ai_manager.py - Phase 2 (OOP refactor)

Everything that talks to the AI. Every record goes through here.

Classes
    AIProvider (abstract, the interface)  "send a prompt, get JSON text back"
        GeminiProvider                    the real Gemini call; owns the API key and timeout
        MockAIProvider                    canned replies for tests; no network needed
    ResponseSchemas                       the JSON schemas sent with each request
    PromptBuilder                         builds the four prompt texts
    ResponseParser                        JSON text -> AIResult / list of questions
    AIManager                             what main.py calls; owns the retry loop

Relationships (for the UML)
    GeminiProvider, MockAIProvider --|> AIProvider     inheritance / interface (LSP:
                                                       either one can be passed in)
    AIManager o-- AIProvider                           aggregation: the provider is made
                                                       outside and passed in, and can be swapped
    AIManager *-- PromptBuilder, ResponseParser        composition: AIManager creates and owns them
"""

import json
import logging
import os
import time
from abc import ABC, abstractmethod
from datetime import datetime

from dotenv import load_dotenv

from models import (
    AIConnectionError,
    AIResponseError,
    AIResult,
    Severity,
    Tier,
    TriageError,
    Urgency,
)

load_dotenv()  # harmless if there is no .env (Docker passes the key with --env-file)

# Absolute path so the log never lands in the wrong folder (the stray
# models/data/manager.log came from a relative "data/..." path).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(_PROJECT_ROOT, "data", "ai_manager.log")


def _build_logger():
    """File-only logger: terminal output belongs to io_manager."""
    logger = logging.getLogger("ai_manager")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        try:
            os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
            handler = logging.FileHandler(LOG_FILE, encoding="utf-8", delay=True)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        except OSError:
            logger.addHandler(logging.NullHandler())
    # The Gemini SDK logs its own warnings; keep them out of the terminal too.
    for sdk_name in ("google_genai", "google.genai"):
        sdk_logger = logging.getLogger(sdk_name)
        sdk_logger.propagate = False
        for handler in logger.handlers:
            if handler not in sdk_logger.handlers:
                sdk_logger.addHandler(handler)
    return logger


# ===========================================================================
# Response schemas (built from the enums, so the allowed values have ONE source)
# ===========================================================================

class ResponseSchemas:
    """Plain-dict JSON schemas. Only GeminiProvider knows how to send them."""

    TRIAGE = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "urgency": {"type": "string", "enum": list(Urgency.values())},
            "severity": {"type": "string", "enum": list(Severity.values())},
            "recommended_tier": {"type": "string", "enum": list(Tier.values())},
            "specialist": {"type": "string"},
            "flagged_symptoms": {"type": "array", "items": {"type": "string"}},
            "history_linked": {"type": "boolean"},
            "confidence": {"type": "number"},
        },
        "required": [
            "summary", "urgency", "severity", "recommended_tier",
            "specialist", "flagged_symptoms", "history_linked", "confidence",
        ],
    }

    FOLLOW_UP = {
        "type": "object",
        "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
        "required": ["questions"],
    }

    # TEAM DECISION: Phase 1's override schema also asked the AI for "proposed_slot",
    # but logic_manager always takes the slot from the staff override, never from
    # the AI. The override call can therefore reuse TRIAGE. If you keep the AI slot,
    # add it back here and to ResponseParser.
    OVERRIDE = TRIAGE

    
# ===========================================================================
# AIProvider: the interface, and its two implementations
# ===========================================================================

class AIProvider(ABC):
    """Anything that can answer a prompt with JSON text."""

    @abstractmethod
    def generate_json(self, prompt, schema):
        """
        Send the prompt and return the raw JSON text of the reply.
        Raise AIConnectionError if the service can't be reached (missing key,
        network failure, timeout, 429/503). Raise AIResponseError for an empty reply.
        """

    @abstractmethod
    def ping(self):
        """Return None if the service answers; raise AIConnectionError if it doesn't."""

    
class GeminiProvider(AIProvider):
    """The real Gemini API. The only class in the project that imports the Google SDK."""

    MODEL_NAME = "gemini-3.1-flash-lite"
    TIMEOUT_SECONDS = 15
    TEMPERATURE = 0.2  # low: consistent, conservative triage

    def __init__(self, api_key=None, model_name=None):
        self.__api_key = api_key or os.environ.get("GEMINI_API_KEY")  # private: never printed
        self._model_name = model_name or self.MODEL_NAME
        self._client = None
        self._types = None

    def _get_client(self):
        if self._client is None:
            if not self.__api_key:
                raise AIConnectionError("GEMINI_API_KEY is not set.")
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:
                raise AIConnectionError(f"google-genai is not installed: {exc}") from exc
            self._types = types
            self._client = genai.Client(
                api_key=self.__api_key,
                http_options=types.HttpOptions(timeout=int(self.TIMEOUT_SECONDS * 1000)),
            )
        return self._client

    def generate_json(self, prompt, schema):
        client = self._get_client()
        try:
            response = client.models.generate_content(
                model=self._model_name,
                contents=prompt,
                config=self._types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema,
                    temperature=self.TEMPERATURE,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - any SDK/network failure
            raise AIConnectionError(f"Gemini call failed: {exc}") from exc
        if not response.text:
            raise AIResponseError("Gemini returned an empty reply.")
        return response.text

    def ping(self):
        client = self._get_client()
        try:
            client.models.generate_content(
                model=self._model_name, contents="Reply with the single word OK.",
            )
        except Exception as exc:  # noqa: BLE001
            raise AIConnectionError(f"Gemini availability check failed: {exc}") from exc


class MockAIProvider(AIProvider):
    """
    Test double. Give it the replies to hand out, in order. Each reply can be a
    dict (sent back as JSON), a str (sent back as is, e.g. broken JSON) or an
    exception instance (raised). Tests can check .prompts to see what was sent.
    """

    def __init__(self, replies=None, available=True):
        self._replies = list(replies or [])
        self._available = available
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        if not self._replies:
            raise AIConnectionError("MockAIProvider has no replies left.")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, str) else json.dumps(reply)

    def ping(self):
        if not self._available:
            raise AIConnectionError("MockAIProvider is set to unavailable.")


# ===========================================================================
# PromptBuilder: one method per prompt (texts ported from Phase 1)
# ===========================================================================

class PromptBuilder:
    """Builds prompt text from model objects. Knows nothing about the API."""

    HISTORY_LIMIT = 5

    def triage(self, intake, patient=None):
        """First assessment. `patient` is a Patient or None for a new patient."""
        red_flags = "YES" if intake.red_flags_present else "no"
        rating = intake.self_rated_severity if intake.self_rated_severity is not None else "unknown"
        return f"""You are a clinical intake triage assistant. You do NOT diagnose.
Your job is to summarise the patient's report, flag concerning symptoms,
check for a link to their prior history, and suggest an urgency/routing
tier for a nurse to review. A human always makes the final decision.

{self._history_block(patient)}

New symptom report from the patient:
\"\"\"{intake.description}\"\"\"

Patient's answers to the intake questions:
- How long: {intake.duration}
- Self-rated severity (1-10): {rating}
- Reports chest pain or difficulty breathing: {red_flags}

Use these answers together with the description when judging urgency and severity.

Respond with a single JSON object matching this exact shape:
{{
  "summary": "<one or two sentence plain-English summary>",
  "urgency": {" | ".join(f'"{v}"' for v in Urgency.values())},
  "severity": {" | ".join(f'"{v}"' for v in Severity.values())},
  "recommended_tier": {" | ".join(f'"{v}"' for v in Tier.values())},
  "specialist": "<e.g. general_practice, oncology, cardiology>",
  "flagged_symptoms": ["<symptom or red flag>", ...],
  "history_linked": true | false,
  "confidence": <number between 0.0 and 1.0>
}}

Only output the JSON object. No other text."""

    def follow_up(self, intake, ai_result):
        """Ask for up to 3 extra questions."""
        return f"""
You are assisting with a patient triage application.

Your task is to identify what additional information may be
needed to understand the patient's symptoms for staff review.

Patient's initial information:
{json.dumps(intake.to_dict(), ensure_ascii=False)}

Initial AI assessment:
{json.dumps(ai_result.to_dict(), ensure_ascii=False)}

Instructions:
1. Generate up to 3 relevant follow-up questions.
2. Base the questions on this patient's specific symptoms
   and answers, not a fixed list of questions.
3. Do not repeat questions that the patient has already answered.
4. Ask only questions that could provide useful additional
   information for clinical staff.
5. Use clear, simple language that patients can understand.
6. If no further information is needed, return an empty list.
7. Do not diagnose the patient or make treatment decisions.
8. Do not delay urgent escalation when warning signs are present.
9. Return only JSON matching the required schema.

Return an object with a "questions" field containing a list
of strings.
"""

    def reassess(self, intake, initial_result, patient=None):
        """Second assessment. `intake` already holds the follow-up Q&A (intake.with_follow_up)."""
        follow_up_information = {
            "initial_ai_assessment": initial_result.to_dict(),
            "follow_up_questions_and_answers": [
                {"question": q, "answer": a}
                for q, a in zip(intake.follow_up_questions, intake.follow_up_answers)
            ],
        }
        return self.triage(intake, patient) + f"""

Additional information collected from the patient:
{json.dumps(follow_up_information, ensure_ascii=False)}

Reassess the patient using BOTH the original symptom information
and the additional answers.

Instructions:
1. Consider all the information together.
2. Update the summary, severity, urgency and recommendations
   according to the available information.
3. Do not automatically retain the initial assessment if the
   new information changes the assessment.
4. Do not diagnose the patient.
5. Return a valid response matching the required triage schema.
6. This assessment is advisory and must be reviewed by staff.
"""

    def override(self, intake, original_result, overrides):
        """Regenerate after staff verdicts. `overrides` is a list of StaffOverride."""
        original = {k: v for k, v in original_result.to_dict().items() if k != "status"}
        override_lines = "\n".join(
            f"- {field}: {verdict}" for field, verdict in (o.to_pair() for o in overrides)
        )
        today = datetime.now().strftime("%Y-%m-%d")
        return f"""You are a clinical intake triage assistant. You do NOT diagnose.
A hospital staff member has reviewed your earlier assessment and given
verdicts for some fields. Staff verdicts are authoritative: apply them
exactly, and update every other field so the whole result stays consistent.

Today's date is {today}.

Patient's original report:
\"\"\"{intake.description}\"\"\"

Your earlier assessment:
{json.dumps(original)}

Staff overrides (field: verdict):
{override_lines}

Field meanings: "department" is the "specialist" field; "urgency" is
"urgent" or "non-urgent"; "severity" is "low", "medium" or "high";
"proposed_slot" is the appointment date/time (already booked by staff).

Respond with a single JSON object with the same fields as before.

Only output the JSON object. No other text."""

    def _history_block(self, patient):
        records = patient.recent_history(self.HISTORY_LIMIT) if patient is not None else []
        if not records:
            return "The patient has no prior visits on file."
        lines = []
        for record in records:
            result = record.ai_result
            summary = result.summary if result is not None and result.ok else "(no summary)"
            lines.append(f"- {record.timestamp}: {record.intake.description} -> {summary}")
        return "Prior visits on file:\n" + "\n".join(lines)


# ===========================================================================
# ResponseParser: raw JSON text -> objects (raises AIResponseError)
# ===========================================================================

class ResponseParser:
    MAX_QUESTIONS = 3

    def triage(self, raw_text):
        """Parse a triage or override reply. AIResult.from_dict does the validation."""
        return AIResult.from_dict(self._load(raw_text))

    def follow_up(self, raw_text):
        """Parse the follow-up reply into a list of at most 3 non-empty questions."""
        data = self._load(raw_text)
        questions = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(questions, list) or len(questions) > self.MAX_QUESTIONS:
            raise AIResponseError("Follow-up reply needs a list of at most 3 questions.")
        if not all(isinstance(q, str) and q.strip() for q in questions):
            raise AIResponseError("Every follow-up question must be non-empty text.")
        return [q.strip() for q in questions]

    @staticmethod
    def _load(raw_text):
        try:
            return json.loads(raw_text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise AIResponseError(f"AI reply was not valid JSON: {exc}") from None

# ===========================================================================
# AIManager: what main.py calls
# ===========================================================================

class AIManager:
    """
    main.py builds it once:  ai = AIManager(GeminiProvider())
    tests build it with:     ai = AIManager(MockAIProvider([...]))
    """

    MAX_RETRIES = 1          # one retry, per spec
    RETRY_DELAY_SECONDS = 3  # pause before retrying a connection failure

    def __init__(self, provider, logger=None):
        self._provider = provider            # aggregation (passed in)
        self._prompts = PromptBuilder()      # composition (owned)
        self._parser = ResponseParser()      # composition (owned)
        self._logger = logger or _build_logger()

    # ---- checks main.py can branch on without looking inside a model ----

    def is_available(self):
        """Quick check before the patient types anything. Never raises."""
        try:
            self._provider.ping()
            return True
        except TriageError as err:
            self._logger.warning("AI availability check failed: %s", err)
            return False

    def is_usable(self, ai_result):
        """True if the result can be used (so main.py never reads ai_result.ok itself)."""
        return ai_result is not None and ai_result.ok

    # ---- the four AI steps ----

    def triage(self, intake, patient=None):
        prompt = self._prompts.triage(intake, patient)
        return self._result_or_failure(prompt, ResponseSchemas.TRIAGE, "Triage")

    def follow_up_questions(self, intake, ai_result):
        """Up to 3 questions; [] if none are needed or the AI fails (Phase 1 behaviour)."""
        prompt = self._prompts.follow_up(intake, ai_result)
        try:
            return self._ask(prompt, ResponseSchemas.FOLLOW_UP, self._parser.follow_up, "Follow-up")
        except TriageError:
            return []

    def reassess(self, intake, initial_result, patient=None):
        prompt = self._prompts.reassess(intake, initial_result, patient)
        return self._result_or_failure(prompt, ResponseSchemas.TRIAGE, "Reassessment")

    def apply_staff_overrides(self, intake, original_result, overrides):
        """Regenerate the result with ALL staff verdicts so far, from the ORIGINAL result."""
        prompt = self._prompts.override(intake, original_result, overrides)
        return self._result_or_failure(prompt, ResponseSchemas.OVERRIDE, "Override")

    # ---- retry loop ----

    def _ask(self, prompt, schema, parse, label):
        """Call -> parse, retrying once if the error is retryable. Raises the last TriageError."""
        last_error = None
        for attempt in range(1, self.MAX_RETRIES + 2):
            try:
                return parse(self._provider.generate_json(prompt, schema))
            except TriageError as err:
                last_error = err
                self._logger.warning("%s attempt %d failed: %s", label, attempt, err)
                if not err.retryable or attempt > self.MAX_RETRIES:
                    break
                if isinstance(err, AIConnectionError):
                    time.sleep(self.RETRY_DELAY_SECONDS)
        raise last_error

    def _result_or_failure(self, prompt, schema, label):
        try:
            return self._ask(prompt, schema, self._parser.triage, label)
        except TriageError as err:
            return AIResult.failure(str(err))