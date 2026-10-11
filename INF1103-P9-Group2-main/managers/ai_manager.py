"""
ai_manager.py

Core AI engine. Every symptom report passes through get_ai_triage_result()
before logic_manager ever sees it — this module has ZERO domain/business
logic of its own, it only builds the prompt, calls the API, and validates
the shape of what comes back.

Expected output schema (this is the contract io_manager and logic_manager
both rely on — if you change field names here, update both of them too):

    {
        "status": "ok" | "error",

        # Only present when status == "ok":
        "summary": str,                  # plain-English, shown to patient
        "urgency": "urgent" | "non-urgent",
        "severity": "low" | "medium" | "high",
        "recommended_tier": "doctor" | "nurse",
        "specialist": str,                # e.g. "oncology", "general_practice"
        "flagged_symptoms": [str, ...],
        "history_linked": bool,           # True if tied to a past condition
        "confidence": float,              # 0.0 - 1.0

        # Only present when status == "error":
        "error_reason": str,
    }

Setup:
    pip install google-genai python-dotenv
    Create a .env file in the project root (see .env.example) with:
        GEMINI_API_KEY=your-key-here
    Never commit .env — it's already in .gitignore.
"""

import json
import logging
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()  # reads .env into os.environ if present; harmless if the
                # file doesn't exist (e.g. in Docker, where the key is
                # injected via --env-file instead)

# Diagnostics go to a log file, never to the terminal: the spec reserves all
# terminal output for io_manager, and patients shouldn't see raw API errors mid-session.
LOG_FILE = os.path.join("data", "ai_manager.log")
logger = logging.getLogger("ai_manager")
logger.setLevel(logging.INFO)
logger.propagate = False  # keep records off the terminal / root logger
try:
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    _handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
except OSError:
    logger.addHandler(logging.NullHandler())  # logging must never crash the app

# The Gemini SDK writes its own warnings through Python logging (for example
# about automatic function calling, which this project doesn't use). With no
# handler set they would print in the terminal, so send them to the same log
# file as ai_manager's own messages. Both spellings of the SDK's logger name
# are covered.
for _sdk_name in ("google_genai", "google.genai"):
    _sdk_logger = logging.getLogger(_sdk_name)
    _sdk_logger.propagate = False
    for _h in logger.handlers:
        _sdk_logger.addHandler(_h)

MODEL_NAME = "gemini-3.1-flash-lite"  # free-tier eligible, for testing while billing/quota is sorted out
MAX_RETRIES = 1  # one retry on a malformed/invalid response, per spec
RETRY_DELAY_SECONDS = 3  # pause before retrying after an API-level failure (e.g. 503/429) —
                          # gives a transient outage or rate-limit spike a chance to clear

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

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "urgency": {"type": "string", "enum": ["urgent", "non-urgent"]},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "recommended_tier": {"type": "string", "enum": ["doctor", "nurse"]},
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

# Single source of truth: the allowed values live in _RESPONSE_SCHEMA above.
VALID_URGENCY = set(_RESPONSE_SCHEMA["properties"]["urgency"]["enum"])
VALID_SEVERITY = set(_RESPONSE_SCHEMA["properties"]["severity"]["enum"])
VALID_TIER = set(_RESPONSE_SCHEMA["properties"]["recommended_tier"]["enum"])
_NO_CLIENT_ERROR = {"status": "error", "error_reason": "AI client not configured (missing GEMINI_API_KEY)."}


_FOLLOW_UP_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "questions": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(type=types.Type.STRING),
        ),
    },
    required=["questions"],
)


# ---------------------------------------------------------------------------
# Client (created once, reused across calls)
# ---------------------------------------------------------------------------

def _get_client():
    """
    Create the Gemini client. Returns None if no API key is configured,
    so callers can fail gracefully instead of crashing on import.
    """
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        logger.error("GEMINI_API_KEY is not set; AI triage unavailable.")
        return None
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_prompt(intake, patient_history=None):
    """
    Build the prompt sent to the AI from the patient's raw symptom text
    plus (optionally) their prior visit history, so the model can check
    for a link between the new complaint and a past condition.

    Args:
        intake: dict with description, duration, self_rated_severity
                and red_flags_present.
        patient_history: the patient's stored record dict from
                          data_manager.load_patient(), or None if new.

    Returns:
        str prompt ready to send to the API.
    """

    description = intake["description"]
    duration = intake.get("duration", "unknown")
    self_rated = intake.get("self_rated_severity", "unknown")
    red_flags = "YES" if intake.get("red_flags_present") else "no"

    history_block = "The patient has no prior visits on file."
    if patient_history and patient_history.get("history"):
        past_entries = []
        for entry in patient_history["history"][-5:]:  # last 5 visits is plenty of context
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


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def _call_api(client, prompt, schema=_RESPONSE_SCHEMA):
    """
    Call the Gemini API once. Returns the raw response text, or None on
    any failure (network error, API error, empty response). Never raises.
    """
    try:
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.2,  # low temperature: we want consistent, conservative triage
            ),
        )
        return response.text
    except Exception as exc:  # noqa: BLE001 — any API failure must be caught, never crash the app
        logger.warning("API call failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Response validation
# ---------------------------------------------------------------------------

def _validate_response(data):
    """
    Check that the parsed JSON has every required field with the right
    type and that enum-like fields hold an allowed value.

    Returns:
        True if valid, False otherwise.
    """
    if not isinstance(data, dict):
        return False

    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data or not isinstance(data[field], expected_type):
            return False

    if data["urgency"] not in VALID_URGENCY:
        return False
    if data["severity"] not in VALID_SEVERITY:
        return False
    if data["recommended_tier"] not in VALID_TIER:
        return False
    if not (0.0 <= float(data["confidence"]) <= 1.0):
        return False

    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def _request_validated(client, prompt, schema, is_valid, label):
    """
    Shared call -> parse -> validate loop with one retry. Returns the parsed
    dict, or None if every attempt failed. Never raises.
    """
    for attempt in range(MAX_RETRIES + 1):
        is_last_attempt = attempt == MAX_RETRIES
        raw_text = _call_api(client, prompt, schema)

        if raw_text is None:
            # API-level failure (network error, 429, 503, etc.) - pause
            # before retrying so a transient spike has a chance to clear.
            if not is_last_attempt:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            logger.warning("%s attempt %d: response was not valid JSON.", label, attempt + 1)
            continue

        if is_valid(parsed):
            return parsed

        logger.warning("%s attempt %d: response failed validation.", label, attempt + 1)

    return None


def is_ai_available():
    """
    Quick pre-check, run before a patient is asked to describe anything:
    True only if the API key is configured AND the API answers a tiny
    request. One attempt, no retry. Never raises.
    """
    try:
        client = _get_client()
        if client is None:
            return False
        client.models.generate_content(
            model=MODEL_NAME, contents="Reply with the single word OK.",
        )
        return True
    except Exception as exc:  # noqa: BLE001 - any failure means "not available"
        logger.warning("AI availability check failed: %s", exc)
        return False


def get_ai_triage_result(intake, patient_history=None):
    """
    Main entry point called by main.py. Builds the prompt, calls the API,
    validates the response, retries once on a malformed/invalid result,
    and always returns a well-formed dict - never raises, never crashes
    the app even if the API is down or returns garbage.

    Args:
        intake: dict with description, duration, self_rated_severity
                and red_flags_present.
        patient_history: the patient's record dict from data_manager, or
                          None for a new patient.

    Returns:
        dict matching the schema documented at the top of this file.
    """
    client = _get_client()
    if client is None:
        return dict(_NO_CLIENT_ERROR)

    prompt = build_prompt(intake, patient_history)
    parsed = _request_validated(client, prompt, _RESPONSE_SCHEMA, _validate_response, "Triage")
    if parsed is None:
        return {"status": "error", "error_reason": "AI did not return a valid structured response after retry."}

    parsed["status"] = "ok"
    return parsed



def generate_follow_up_questions(intake, ai_result=None):
    """
    Generate symptom-specific follow-up questions using Gemini.

    Args:
        intake: The patient's initial symptoms and answers.
        ai_result: The initial AI triage assessment, if available.

    Returns:
        A dict containing up to 3 questions, or an error status.
    """
    client = _get_client()

    if client is None:
        return {
            "status": "error",
            "questions": [],
        }

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

    parsed = _request_validated(
        client,
        prompt,
        _FOLLOW_UP_SCHEMA,
        _validate_follow_up_response,
        "Follow-up questions",
    )

    if parsed is None:
        return {
            "status": "error",
            "questions": [],
        }

    return {
        "status": "ok",
        "questions": parsed["questions"],
    }


def _validate_follow_up_response(data):
    """Check that Gemini returned a valid list of follow-up questions."""
    if not isinstance(data, dict):
        return False

    questions = data.get("questions")

    if not isinstance(questions, list) or len(questions) > 3:
        return False

    if not all(
        isinstance(question, str) and question.strip()
        for question in questions
    ):
        return False

    return True



def reassess_ai_triage_result(
    intake,
    initial_ai_result,
    follow_up_questions,
    follow_up_answers,
    patient_history=None,
):
    """
    Reassess the patient's symptoms using their follow-up answers.

    Returns an updated triage result using the existing response schema.
    """
    client = _get_client()

    if client is None:
        return dict(_NO_CLIENT_ERROR)

    # Start with the original symptom information and patient history.
    prompt = build_prompt(intake, patient_history)

    # Add the initial assessment and the patient's follow-up answers.
    follow_up_information = {
        "initial_ai_assessment": initial_ai_result,
        "follow_up_questions_and_answers": [
            {
                "question": question,
                "answer": answer,
            }
            for question, answer in zip(
                follow_up_questions,
                follow_up_answers,
            )
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

    parsed = _request_validated(
        client,
        prompt,
        _RESPONSE_SCHEMA,
        _validate_response,
        "Triage reassessment",
    )

    if parsed is None:
        return {
            "status": "error",
            "error_reason": (
                "AI reassessment could not be completed "
                "after retry."
            ),
        }

    parsed["status"] = "ok"
    return parsed


# ---------------------------------------------------------------------------
# Staff override: AI incorporates the staff member's verdicts
# ---------------------------------------------------------------------------

# Same shape as the triage schema, plus a proposed_slot string
# ("" when staff did not override the slot).
_OVERRIDE_SCHEMA = {
    **_RESPONSE_SCHEMA,
    "properties": {**_RESPONSE_SCHEMA["properties"], "proposed_slot": {"type": "string"}},
    "required": _RESPONSE_SCHEMA["required"] + ["proposed_slot"],
}


def build_override_prompt(symptom_description, original_ai_result, staff_overrides):
    """
    Build the prompt that asks the AI to regenerate its output after
    applying hospital staff overrides.

    Args:
        symptom_description: the patient's original text.
        original_ai_result: the AI result before any override.
        staff_overrides: list of [field, verdict] pairs, e.g.
                         [["urgency", "treat as urgent"], ["proposed_slot", "next week"]]
                         field is one of department/urgency/severity/proposed_slot.
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


def _valid_slot(slot):
    """True for an empty slot (no override) or a YYYY-MM-DD HH:MM string."""
    if not isinstance(slot, str):
        return False
    if slot.strip() == "":
        return True
    try:
        datetime.strptime(slot.strip(), "%Y-%m-%d %H:%M")
        return True
    except ValueError:
        return False


def _validate_override_response(data):
    """Standard triage validation plus a well-formed proposed_slot."""
    return _validate_response(data) and _valid_slot(data.get("proposed_slot", ""))


def get_ai_override_result(symptom_description, original_ai_result, staff_overrides):
    """
    Regenerate the AI result after hospital staff overrides. Same safety
    guarantees as get_ai_triage_result: never raises, retries once on a
    malformed response, returns {"status": "error", ...} on failure.

    Returns:
        dict in the standard schema, plus "proposed_slot" (str) only if
        staff overrode the slot.
    """
    client = _get_client()
    if client is None:
        return dict(_NO_CLIENT_ERROR)

    prompt = build_override_prompt(symptom_description, original_ai_result, staff_overrides)
    parsed = _request_validated(client, prompt, _OVERRIDE_SCHEMA, _validate_override_response, "Override")
    if parsed is None:
        return {"status": "error", "error_reason": "AI did not return a valid structured response for the override."}

    slot = parsed.get("proposed_slot", "").strip()
    if slot:
        parsed["proposed_slot"] = slot
    else:
        parsed.pop("proposed_slot", None)
    parsed["status"] = "ok"
    return parsed