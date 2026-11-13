"""
ai_manager.py - everything that talks to the Gemini API.

Result schema (the contract IOManager and LogicManager rely on):

    {
        "status": "ok" | "error",

        # Only when status == "ok":
        "summary": str, "urgency": "urgent" | "non-urgent",
        "severity": "low" | "medium" | "high",
        "recommended_tier": "doctor" | "nurse",
        "specialist": str, "flagged_symptoms": [str, ...],
        "history_linked": bool, "confidence": float (0.0 - 1.0),

        # Only when status == "error":
        "error_reason": str,
    }

Setup:
    pip install google-genai python-dotenv
    .env in the project root containing GEMINI_API_KEY=your-key-here
"""

import json
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types

from managers.temp.base_manager import BaseManager

load_dotenv()  # harmless if no .env exists (e.g. Docker injects the key)


class AIManager(BaseManager):

    MODEL_NAME = "gemini-3.1-flash-lite"  # free-tier eligible, for testing
    MAX_RETRIES = 1                       # one retry on a malformed/invalid response
    API_TIMEOUT_SECONDS = 15
    RETRY_DELAY_SECONDS = 3               # pause before retrying after an API-level failure

    REQUIRED_FIELDS = {
        "summary": str,
        "urgency": str,
        "severity": str,
        "recommended_tier": str,
        "specialist": str,
        "flagged_symptoms": list,
        "history_linked": bool,
        "confidence": (int, float),
    }

    # Allowed values (kept here so this manager needs nothing from BaseManager
    # except logging).
    URGENCY_VALUES = ("urgent", "non-urgent")
    SEVERITY_VALUES = ("low", "medium", "high")
    TIER_VALUES = ("doctor", "nurse")
    SLOT_FORMAT = "%Y-%m-%d %H:%M"

    RESPONSE_SCHEMA = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "urgency": {"type": "string", "enum": list(URGENCY_VALUES)},
            "severity": {"type": "string", "enum": list(SEVERITY_VALUES)},
            "recommended_tier": {"type": "string", "enum": list(TIER_VALUES)},
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

    FOLLOW_UP_SCHEMA = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "questions": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(type=types.Type.STRING),
            ),
        },
        required=["questions"],
    )

    # Same as the triage schema, plus proposed_slot ("" when staff didn't override it).
    OVERRIDE_SCHEMA = {
        **RESPONSE_SCHEMA,
        "properties": {**RESPONSE_SCHEMA["properties"], "proposed_slot": {"type": "string"}},
        "required": RESPONSE_SCHEMA["required"] + ["proposed_slot"],
    }

    NO_CLIENT_ERROR = {
        "status": "error",
        "error_reason": "AI client not configured (missing GEMINI_API_KEY).",
    }

    def get_manager_name(self):
        return "ai_manager"

    def __init__(self, model_name=None):
        super().__init__()
        self.model_name = model_name or self.MODEL_NAME
        self._client = None
        self._route_sdk_logs_to_file()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def _route_sdk_logs_to_file(self):
        """The Gemini SDK logs warnings of its own; keep them out of the terminal."""
        for sdk_name in ("google_genai", "google.genai"):
            sdk_logger = logging.getLogger(sdk_name)
            sdk_logger.propagate = False
            for handler in self.logger.handlers:
                if handler not in sdk_logger.handlers:
                    sdk_logger.addHandler(handler)

    def _get_client(self):
        """Create (once) and return the Gemini client, or None if no API key is set."""
        if self._client is not None:
            return self._client
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            self.log_error("GEMINI_API_KEY is not set; AI triage unavailable.")
            return None
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(self.API_TIMEOUT_SECONDS * 1000)),
        )
        return self._client

        # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------
    def build_prompt(self, intake, patient_history=None):
        """Prompt from the patient's intake answers plus up to 5 prior visits."""
        description = intake["description"]
        duration = intake.get("duration", "unknown")
        self_rated = intake.get("self_rated_severity", "unknown")
        red_flags = "YES" if intake.get("red_flags_present") else "no"

        history_block = "The patient has no prior visits on file."
        if patient_history and patient_history.get("history"):
            past_entries = []
            for entry in patient_history["history"][-5:]:
                desc = entry.get("symptom_description", "(no description)")
                ai_result = entry.get("ai_result") or {}
                past_summary = ai_result.get("summary", "(no summary)")
                past_entries.append(f"- {entry.get('timestamp', 'unknown date')}: {desc} -> {past_summary}")
            history_block = "Prior visits on file:\n" + "\n".join(past_entries)

        return f"""You are a clinical intake triage assistant. You do NOT diagnose.
Your job is to summarise the patient's report, flag concerning symptoms,
check for a link to their prior history, and suggest an urgency/routing
tier for a nurse to review. A human always makes the final decision.

{history_block}

New symptom report from the patient:
\"\"\"{description}\"\"\"

Patient's answers to the intake questions:
- How long: {duration}
- Self-rated severity (1-10): {self_rated}
- Reports chest pain or difficulty breathing: {red_flags}

Use these answers together with the description when judging urgency and severity.

Respond with a single JSON object matching this exact shape:
{{
  "summary": "<one or two sentence plain-English summary>",
  "urgency": "urgent" | "non-urgent",
  "severity": "low" | "medium" | "high",
  "recommended_tier": "doctor" | "nurse",
  "specialist": "<e.g. general_practice, oncology, cardiology>",
  "flagged_symptoms": ["<symptom or red flag>", ...],
  "history_linked": true | false,
  "confidence": <number between 0.0 and 1.0>
}}

Only output the JSON object. No other text."""

    # ------------------------------------------------------------------
    # API call + validation
    # ------------------------------------------------------------------
    def _call_api(self, client, prompt, schema=None):
        """One API call. Returns raw response text, or None on any failure."""
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=schema or self.RESPONSE_SCHEMA,
                    temperature=0.2,  # low: consistent, conservative triage
                ),
            )
            return response.text
        except Exception as exc:  # noqa: BLE001 - any API failure must be caught
            self.log_warning(f"API call failed: {exc}")
            return None

    def _validate_response(self, data):
        """Every required field present, right type, enum fields holding allowed values."""
        if not isinstance(data, dict):
            return False
        for field, expected_type in self.REQUIRED_FIELDS.items():
            if field not in data or not isinstance(data[field], expected_type):
                return False
        if not all(isinstance(s, str) for s in data["flagged_symptoms"]):
            return False
        if data["urgency"] not in self.URGENCY_VALUES:
            return False
        if data["severity"] not in self.SEVERITY_VALUES:
            return False
        if data["recommended_tier"] not in self.TIER_VALUES:
            return False
        if isinstance(data["confidence"], bool) or not (0.0 <= float(data["confidence"]) <= 1.0):
            return False
        return True

    def _request_validated(self, client, prompt, schema, is_valid, label):
        """call -> parse -> validate loop with retries. Parsed dict, or None. Never raises."""
        for attempt in range(self.MAX_RETRIES + 1):
            is_last_attempt = attempt == self.MAX_RETRIES
            raw_text = self._call_api(client, prompt, schema)

            if raw_text is None:
                if not is_last_attempt:
                    time.sleep(self.RETRY_DELAY_SECONDS)  # let a transient spike clear
                continue

            try:
                parsed = json.loads(raw_text)
            except (json.JSONDecodeError, TypeError):
                self.log_warning(f"{label} attempt {attempt + 1}: response was not valid JSON.")
                continue

            if is_valid(parsed):
                return parsed
            self.log_warning(f"{label} attempt {attempt + 1}: response failed validation.")
        return None

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------
    def is_ai_available(self):
        """Pre-check: key configured AND API answers a tiny request. Never raises."""
        try:
            client = self._get_client()
            if client is None:
                return False
            client.models.generate_content(
                model=self.model_name, contents="Reply with the single word OK.",
            )
            return True
        except Exception as exc:  # noqa: BLE001
            self.log_warning(f"AI availability check failed: {exc}")
            return False

    def get_ai_triage_result(self, intake, patient_history=None):
        """Main triage call. Always returns a well-formed dict; never raises."""
        client = self._get_client()
        if client is None:
            return dict(self.NO_CLIENT_ERROR)

        prompt = self.build_prompt(intake, patient_history)
        parsed = self._request_validated(
            client, prompt, self.RESPONSE_SCHEMA, self._validate_response, "Triage",
        )
        if parsed is None:
            return {"status": "error",
                    "error_reason": "AI did not return a valid structured response after retry."}
        parsed["status"] = "ok"
        return parsed

    # ---- follow-up questions -----------------------------------------
    def _validate_follow_up_response(self, data):
        """A dict with at most 3 non-empty string questions."""
        if not isinstance(data, dict):
            return False
        questions = data.get("questions")
        if not isinstance(questions, list) or len(questions) > 3:
            return False
        return all(isinstance(q, str) and q.strip() for q in questions)

    def generate_follow_up_questions(self, intake, ai_result=None):
        """Up to 3 symptom-specific follow-up questions, or an error status."""
        client = self._get_client()
        if client is None:
            return {"status": "error", "questions": []}

        prompt = f"""
You are assisting with a patient triage application.

Your task is to identify what additional information may be
needed to understand the patient's symptoms for staff review.

Patient's initial information:
{json.dumps(intake, ensure_ascii=False)}

Initial AI assessment:
{json.dumps(ai_result or {}, ensure_ascii=False)}

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
        parsed = self._request_validated(
            client, prompt, self.FOLLOW_UP_SCHEMA,
            self._validate_follow_up_response, "Follow-up questions",
        )
        if parsed is None:
            return {"status": "error", "questions": []}
        return {"status": "ok", "questions": parsed["questions"]}

    # ---- reassessment -------------------------------------------------
    def reassess_ai_triage_result(self, intake, initial_ai_result, follow_up_questions,
                                  follow_up_answers, patient_history=None):
        """Reassess the symptoms using the patient's follow-up answers."""
        client = self._get_client()
        if client is None:
            return dict(self.NO_CLIENT_ERROR)

        prompt = self.build_prompt(intake, patient_history)
        follow_up_information = {
            "initial_ai_assessment": initial_ai_result,
            "follow_up_questions_and_answers": [
                {"question": q, "answer": a}
                for q, a in zip(follow_up_questions, follow_up_answers)
            ],
        }
        prompt += f"""

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
        parsed = self._request_validated(
            client, prompt, self.RESPONSE_SCHEMA,
            self._validate_response, "Triage reassessment",
        )
        if parsed is None:
            return {"status": "error",
                    "error_reason": "AI reassessment could not be completed after retry."}
        parsed["status"] = "ok"
        return parsed

    # ---- staff override -----------------------------------------------
    def build_override_prompt(self, symptom_description, original_ai_result, staff_overrides):
        """
        Prompt asking the AI to regenerate its output after staff overrides.

        staff_overrides: list of [field, verdict] pairs, field one of
        department/urgency/severity/proposed_slot.
        """
        original = {k: v for k, v in original_ai_result.items() if k != "status"}
        override_lines = "\n".join(f"- {field}: {verdict}" for field, verdict in staff_overrides)
        today = datetime.now().strftime("%Y-%m-%d")

        return f"""You are a clinical intake triage assistant. You do NOT diagnose.
A hospital staff member has reviewed your earlier assessment and given
verdicts for some fields. Staff verdicts are authoritative: apply them
exactly, and update every other field so the whole result stays consistent.

Today's date is {today}.

Patient's original report:
\"\"\"{symptom_description}\"\"\"

Your earlier assessment:
{json.dumps(original)}

Staff overrides (field: verdict):
{override_lines}

Field meanings: "department" is the "specialist" field; "urgency" is
"urgent" or "non-urgent"; "severity" is "low", "medium" or "high";
"proposed_slot" is the appointment date/time.

Respond with a single JSON object with the same fields as before, plus:
  "proposed_slot": "YYYY-MM-DD HH:MM" (24-hour clock, a future date that
  follows the staff verdict), or "" if staff did NOT override proposed_slot.

Only output the JSON object. No other text."""

    def _parse_slot(self, text):
        """Datetime for "YYYY-MM-DD HH:MM", or None if invalid."""
        try:
            return datetime.strptime(text, self.SLOT_FORMAT)
        except (ValueError, TypeError):
            return None

    def _valid_slot(self, slot):
        """True for an empty slot (no override) or a YYYY-MM-DD HH:MM string."""
        if not isinstance(slot, str):
            return False
        return slot.strip() == "" or self._parse_slot(slot.strip()) is not None

    def _validate_override_response(self, data):
        """Standard triage validation plus a well-formed proposed_slot."""
        return self._validate_response(data) and self._valid_slot(data.get("proposed_slot", ""))

    def get_ai_override_result(self, symptom_description, original_ai_result, staff_overrides):
        """Regenerate the AI result after staff overrides. Never raises."""
        client = self._get_client()
        if client is None:
            return dict(self.NO_CLIENT_ERROR)

        prompt = self.build_override_prompt(symptom_description, original_ai_result, staff_overrides)
        parsed = self._request_validated(
            client, prompt, self.OVERRIDE_SCHEMA, self._validate_override_response, "Override",
        )
        if parsed is None:
            return {"status": "error",
                    "error_reason": "AI did not return a valid structured response for the override."}

        slot = parsed.get("proposed_slot", "").strip()
        if slot:
            parsed["proposed_slot"] = slot
        else:
            parsed.pop("proposed_slot", None)
        parsed["status"] = "ok"
        return parsed