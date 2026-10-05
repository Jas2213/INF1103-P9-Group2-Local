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
import os
import time
from datetime import datetime

from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()  # reads .env into os.environ if present; harmless if the
                # file doesn't exist (e.g. in Docker, where the key is
                # injected via --env-file instead)

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

VALID_URGENCY = {"urgent", "non-urgent"}
VALID_SEVERITY = {"low", "medium", "high"}
VALID_TIER = {"doctor", "nurse"}

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
        return None
    return genai.Client(api_key=api_key)


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def build_prompt(symptom_description, patient_history=None):
    """
    Build the prompt sent to the AI from the patient's raw symptom text
    plus (optionally) their prior visit history, so the model can check
    for a link between the new complaint and a past condition.

    Args:
        symptom_description: str, the patient's free-text description.
        patient_history: the patient's stored record dict from
                          data_manager.load_patient(), or None if new.

    Returns:
        str prompt ready to send to the API.
    """
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
\"\"\"{symptom_description}\"\"\"

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
        print(f"[ai_manager] API call failed: {exc}")
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

def get_ai_triage_result(symptom_description, patient_history=None):
    """
    Main entry point called by main.py. Builds the prompt, calls the API,
    validates the response, retries once on a malformed/invalid result,
    and always returns a well-formed dict — never raises, never crashes
    the app even if the API is down or returns garbage.

    Args:
        symptom_description: str, the patient's free-text report.
        patient_history: the patient's record dict from data_manager, or
                          None for a new patient.

    Returns:
        dict matching the schema documented at the top of this file.
    """
    client = _get_client()
    if client is None:
        return {"status": "error", "error_reason": "AI client not configured (missing GEMINI_API_KEY)."}

    prompt = build_prompt(symptom_description, patient_history)

    for attempt in range(MAX_RETRIES + 1):
        is_last_attempt = attempt == MAX_RETRIES
        raw_text = _call_api(client, prompt)

        if raw_text is None:
            # API-level failure (network error, 429, 503, etc.) — pause
            # before retrying so a transient spike has a chance to clear,
            # instead of immediately hammering the API again.
            if not is_last_attempt:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            print(f"[ai_manager] Attempt {attempt + 1}: response was not valid JSON.")
            continue

        if _validate_response(parsed):
            parsed["status"] = "ok"
            return parsed

        print(f"[ai_manager] Attempt {attempt + 1}: response failed schema validation.")

    # Every attempt failed — log and return a safe fallback so the rest
    # of the pipeline (io_manager, logic_manager) can handle it gracefully
    # instead of crashing.
    return {"status": "error", "error_reason": "AI did not return a valid structured response after retry."}


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
        return {"status": "error", "error_reason": "AI client not configured (missing GEMINI_API_KEY)."}

    prompt = build_override_prompt(symptom_description, original_ai_result, staff_overrides)

    for attempt in range(MAX_RETRIES + 1):
        is_last_attempt = attempt == MAX_RETRIES
        raw_text = _call_api(client, prompt, _OVERRIDE_SCHEMA)

        if raw_text is None:
            if not is_last_attempt:
                time.sleep(RETRY_DELAY_SECONDS)
            continue

        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, TypeError):
            print(f"[ai_manager] Override attempt {attempt + 1}: response was not valid JSON.")
            continue

        if _validate_response(parsed) and _valid_slot(parsed.get("proposed_slot", "")):
            slot = parsed.get("proposed_slot", "").strip()
            if slot:
                parsed["proposed_slot"] = slot
            else:
                parsed.pop("proposed_slot", None)
            parsed["status"] = "ok"
            return parsed

        print(f"[ai_manager] Override attempt {attempt + 1}: response failed validation.")

    return {"status": "error", "error_reason": "AI did not return a valid structured response for the override."}