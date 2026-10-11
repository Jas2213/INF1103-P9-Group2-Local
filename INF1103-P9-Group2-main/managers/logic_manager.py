"""
logic_manager.py

Business logic engine for clinical triage. Applies deterministic rules
(DevOps proposal, Section 4), handles API-failure fallbacks, and applies
hospital-staff overrides. Staff always win: any field a staff member has
overridden is preserved for appointment routing. Emergency decisions never
receive an appointment slot.

Functions called by main.py:
    evaluate_triage_logic(ai_result, staff_overrides=None, red_flags_present=False)
    apply_staff_override(outcome, staff_overrides)
    schedule_appointment(outcome, slot_time)

staff_overrides is a list of [field, verdict] pairs, where field is one of
"department", "urgency", "severity" or "proposed_slot".

Outcome schema (dict):
    decision, next_action, specialist, proposed_slot,
    flag_for_manual_review, nurse_override, override_reason,
    slot_set_by_staff, status, assigned_practitioner_type,
    override_applied, routing_timestamp
"""

from datetime import datetime, timedelta

_GP_NAMES = ("general_practice", "general practice", "gp")

# Staff must select these exact categories; the AI must not interpret free text.
STAFF_OVERRIDE_OPTIONS = {
    "severity": ("low", "medium", "high"),
    "urgency": ("urgent", "non-urgent"),
}
VALID_OVERRIDE_FIELDS = ("department", "urgency", "severity", "proposed_slot")


def validate_staff_override(field, verdict):
    """Return a cleaned verdict, or raise ValueError for invalid staff input."""
    if not isinstance(field, str) or field not in VALID_OVERRIDE_FIELDS:
        raise ValueError("Unknown staff override field.")
    if not isinstance(verdict, str) or not verdict.strip():
        raise ValueError(f"{field} must be non-empty text.")

    verdict = verdict.strip()
    options = STAFF_OVERRIDE_OPTIONS.get(field)
    if options is not None:
        verdict = verdict.lower()
        if verdict not in options:
            raise ValueError(f"{field} must be one of: {', '.join(options)}.")
    if field == "proposed_slot":
        verdict = validate_manual_slot(verdict)
    return verdict


def validate_staff_overrides(staff_overrides):
    """Validate every [field, verdict] pair without modifying the caller's list."""
    if staff_overrides is None:
        return []
    if not isinstance(staff_overrides, (list, tuple)):
        raise ValueError("Staff overrides must be a list of [field, verdict] pairs.")
    validated = []
    for pair in staff_overrides:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError("Each staff override must contain a field and a verdict.")
        field, verdict = pair
        validated.append([field, validate_staff_override(field, verdict)])
    return validated


def _enforce_risk_values(ai_result, validated_overrides):
    """Copy the AI result and write in already-validated staff risk values; latest wins."""
    updated = dict(ai_result)
    for field, verdict in validated_overrides:
        if field in STAFF_OVERRIDE_OPTIONS:
            updated[field] = verdict
    return updated


def apply_staff_risk_values(ai_result, staff_overrides):
    """Public entry point: validate the overrides, then enforce them on a copy of the AI result."""
    return _enforce_risk_values(ai_result, validate_staff_overrides(staff_overrides))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _overridden_fields(staff_overrides):
    """Return the set of field names staff have overridden."""
    return {pair[0] for pair in (staff_overrides or []) if len(pair) >= 1}


def _calculate_next_slot(days_ahead=3):
    """Generate a future appointment slot string, fixed at 09:00."""
    slot_date = datetime.now() + timedelta(days=days_ahead)
    return slot_date.strftime("%Y-%m-%d 09:00")


def _readable_department(specialist):
    return str(specialist).replace("_", " ")


def _build_next_action(decision, specialist, slot):
    """
    Build the patient-facing next_action text so it always agrees with the
    decision, department and slot actually being proposed.
    """
    department = _readable_department(specialist)

    if decision == "urgent_priority":
        return "Please proceed immediately to the nearest Emergency Department."
    if decision == "route_to_specialist":
        return f"Your case has been routed to the {department} department. Proposed appointment: {slot}."
    if decision == "manual_review":
        return "Your case has been placed on the manual review queue."
    return f"A routine appointment with {department} has been proposed for {slot}."


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_triage_logic(ai_result, staff_overrides=None, red_flags_present=False):
    """
    Apply the business rules to an ai_manager result.

    Rules (Section 4 of the proposal):
      - Direct to specialist: high risk AND history_linked -> specialist.
      - Urgent case: high risk but no matching history -> urgent care / A&E. (no booking needed)
      - Non-urgent: low/medium risk -> next available slot in the right
        department.
    "High risk" means urgency == "urgent" or severity == "high".

    Staff overrides (list of [field, verdict]):
      - "department": use the staff value and skip keyword specialist rules.
      - "urgency" / "severity": the high-risk guardrail is skipped, and
        high risk is judged only from the field(s) staff overrode.
      - "proposed_slot": use the validated staff value for appointment decisions.
        Emergency decisions always have no slot.
    """
    staff_overrides = validate_staff_overrides(staff_overrides)

    # API failure (or no usable AI result): manual review, no slot.
    if not isinstance(ai_result, dict) or ai_result.get("status") != "ok":
        return {
            "decision": "manual_review",
            "next_action": "Your symptom report could not be processed automatically. A clinical staff member will review your case shortly.",
            "specialist": None,
            "proposed_slot": None,
            "flag_for_manual_review": True,
            "nurse_override": False,
            "override_reason": None,
            "slot_set_by_staff": False,
            "status": "TRIAGED",
            "assigned_practitioner_type": "nurse",
            "override_applied": False,
            "routing_timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    # Escalate the AI assessment when the patient reports red flags.
    ai_result = apply_red_flag_escalation(ai_result, red_flags_present)

    # Apply validated staff decisions after the automatic escalation.
    ai_result = _enforce_risk_values(ai_result, staff_overrides)  # already validated above

    overridden = _overridden_fields(staff_overrides)
    staff_values = dict(staff_overrides)  # Latest value for each field wins.

    severity = str(ai_result.get("severity", "low")).lower()
    urgency = str(ai_result.get("urgency", "non-urgent")).lower()
    tier = str(ai_result.get("recommended_tier", "nurse")).lower()
    specialist = str(ai_result.get("specialist", "general_practice")).lower().strip()
    if "department" in staff_values:
        specialist = staff_values["department"].lower().replace(" ", "_")
    history_linked = bool(ai_result.get("history_linked", False))


    flagged = ai_result.get("flagged_symptoms", [])
    symptoms_str = " ".join(flagged).lower() + " " + str(ai_result.get("summary", "")).lower()


    # Keyword specialist rules (skipped when staff chose the department)
    if "department" not in overridden:
        if "chest pain" in symptoms_str or "shortness of breath" in symptoms_str:
            specialist = "cardiology"
            tier = "doctor"
        elif "headache" in symptoms_str and ("vision" in symptoms_str or "numbness" in symptoms_str):
            specialist = "neurology"
            tier = "doctor"
        elif "rash" in symptoms_str and urgency == "urgent":
            specialist = "dermatology"

    # Is this high risk? Staff verdicts on urgency/severity take over.
    staff_set_risk = "urgency" in overridden or "severity" in overridden
    if staff_set_risk:
        high_risk = (
            ("urgency" in overridden and urgency == "urgent")
            or ("severity" in overridden and severity == "high")
        )
    else:
        high_risk = urgency == "urgent" or severity == "high"

    # High severity cases go directly to A&E
    if severity == "high":
        return {
            "decision": "urgent_priority",
            "next_action": "Please proceed immediately to the nearest Emergency Department.",
            "specialist": specialist,
            "proposed_slot": None,
            "flag_for_manual_review": True,
            "nurse_override": False,
            "override_reason": None,
            "slot_set_by_staff": False,
            "status": "TRIAGED",
            "assigned_practitioner_type": "doctor",
            "override_applied": False,
            "routing_timestamp": datetime.now().isoformat(timespec="seconds"),
        }

    # Guardrail: high-risk cases need a doctor (skipped if staff set the risk)
    override_applied = False
    if high_risk and not staff_set_risk and tier != "doctor":
        tier = "doctor"
        override_applied = True

    status = "GUARDRAIL_OVERRIDDEN" if override_applied else "TRIAGED"
    has_specialist = specialist not in _GP_NAMES

    # Decision
    if high_risk and history_linked and has_specialist:
        decision = "route_to_specialist"        # direct to specialist rule
    elif high_risk:
        decision = "urgent_priority"            # urgent case rule
    elif has_specialist:
        decision = "route_to_specialist"
    else:
        decision = "schedule_non_urgent"        # non-urgent rule

    # Emergency outcomes never propose appointments, even with a staff slot.
    slot_set_by_staff = False
    if decision == "urgent_priority":
        slot = None
    elif "proposed_slot" in staff_values:
        slot = staff_values["proposed_slot"]
        slot_set_by_staff = True
    elif urgency == "urgent":
        slot = _calculate_next_slot(days_ahead=2)
    else:
        slot = _calculate_next_slot(days_ahead=5)

    return {
        "decision": decision,
        "next_action": _build_next_action(decision, specialist, slot),
        "specialist": specialist,
        "proposed_slot": slot,
        "flag_for_manual_review": urgency == "urgent" or decision == "urgent_priority",
        "nurse_override": False,
        "override_reason": None,
        "slot_set_by_staff": slot_set_by_staff,
        "status": status,
        "assigned_practitioner_type": tier,
        "override_applied": override_applied,
        "routing_timestamp": datetime.now().isoformat(timespec="seconds"),
    }

def apply_red_flag_escalation(ai_result, red_flags_present):
    """Copy of ai_result escalated to urgent/high when the patient reported red flags."""
    if not isinstance(ai_result, dict) or not red_flags_present or ai_result.get("status") != "ok":
        return ai_result
    updated = dict(ai_result)
    updated["urgency"] = "urgent"
    updated["severity"] = "high"
    return updated


# ---------------------------------------------------------------------------
# Staff override
# ---------------------------------------------------------------------------

def apply_staff_override(outcome, staff_overrides):
    """
    Mark an outcome as overridden by hospital staff.

    Args:
        outcome: dict from evaluate_triage_logic.
        staff_overrides: list of [field, verdict] pairs (validated here).

    Returns:
        a NEW outcome dict (the input is not modified).
    """
    staff_overrides = validate_staff_overrides(staff_overrides)
    updated = dict(outcome)
    updated["nurse_override"] = True
    updated["override_reason"] = "; ".join(
        f"{field}: {verdict}" for field, verdict in staff_overrides
    )
    updated["status"] = "GUARDRAIL_OVERRIDDEN"

    staff_values = dict(staff_overrides)
    if updated.get("decision") in ("urgent_priority", "manual_review"):
        updated["proposed_slot"] = None
        updated["slot_set_by_staff"] = False
    else:
        if "department" in staff_values:
            updated["specialist"] = staff_values["department"].lower().replace(" ", "_")
        if "proposed_slot" in staff_values:
            updated["proposed_slot"] = staff_values["proposed_slot"]
            updated["slot_set_by_staff"] = True
    updated["next_action"] = _build_next_action(
        updated.get("decision"), updated.get("specialist"), updated.get("proposed_slot"),
    )

    return updated


# ---------------------------------------------------------------------------
# Booking
# ---------------------------------------------------------------------------

def validate_manual_slot(slot_time):
    """Require an exact future local date/time for a manually entered slot."""
    if not isinstance(slot_time, str):
        raise ValueError("Enter an appointment date/time as YYYY-MM-DD HH:MM.")
    slot_time = slot_time.strip()
    try:
        parsed = datetime.strptime(slot_time, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Enter a valid appointment date/time as YYYY-MM-DD HH:MM.") from None
    if parsed.strftime("%Y-%m-%d %H:%M") != slot_time:
        raise ValueError("Use the exact format YYYY-MM-DD HH:MM.")
    if parsed <= datetime.now():
        raise ValueError("Appointment date/time must be in the future.")
    return slot_time


def schedule_appointment(outcome, slot_time):
    """
    Book the appointment (called only after the patient accepts).
    Returns {"success": True, "outcome": <new dict>} or
            {"success": False, "error": str}. The input is not modified.
    """
    if not isinstance(outcome, dict) or outcome.get("status") not in ("TRIAGED", "GUARDRAIL_OVERRIDDEN"):
        return {"success": False, "error": "Patient not ready for scheduling."}
    if outcome.get("decision") not in ("route_to_specialist", "schedule_non_urgent"):
        return {"success": False, "error": "This decision is not ready for an appointment."}
    if not slot_time:
        return {"success": False, "error": "No appointment slot to book."}
    try:
        slot_time = validate_manual_slot(slot_time)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    booked = dict(outcome)
    booked["status"] = "SCHEDULED"
    booked["proposed_slot"] = slot_time

    return {"success": True, "outcome": booked}


def finalize_proposed_slot(outcome, slot_key):
    """Replace the outcome's placeholder slot with the real available
    slot main.py found, and rebuild next_action so the patient-facing
    text matches. Returns a NEW outcome dict."""
    if outcome.get("decision") not in ("route_to_specialist", "schedule_non_urgent"):
        raise ValueError("This decision cannot receive an appointment slot.")
    slot_key = validate_manual_slot(slot_key)
    updated = dict(outcome)
    updated["proposed_slot"] = slot_key
    updated["next_action"] = _build_next_action(
        updated.get("decision"), updated.get("specialist"), slot_key,
    )
    return updated


def mark_no_slots_available(outcome):
    """No calendar slot was available. Flag for manual scheduling instead
    of proposing nothing. Returns a NEW outcome dict."""
    updated = dict(outcome)
    updated["proposed_slot"] = None
    updated["flag_for_manual_review"] = True
    updated["next_action"] = (
        "No appointment slots are currently available. Your case has "
        "been flagged for manual scheduling by staff."
    )
    return updated
# ---------------------------------------------------------------------------
# Search for upcoming appointments
# ---------------------------------------------------------------------------

def get_upcoming_appointments(patient_record, now=None): 
    """
    Return a patient's booked appointments that haven't happened yet,
    soonest first. Only history entries whose outcome status is
    "SCHEDULED" count; malformed or past slots are skipped.

    Args:
        patient_record: dict from data_manager.load_patient(), or None.
        now: datetime override (for testing); defaults to datetime.now().

    Returns:
        list of dicts: slot, department, practitioner, reason, booked_on.
    """
    now = now or datetime.now()
    upcoming = []
    if not isinstance(patient_record, dict):
        return upcoming

    for entry in patient_record.get("history", []):
        outcome = entry.get("outcome") if isinstance(entry, dict) else None
        if not isinstance(outcome, dict) or outcome.get("status") != "SCHEDULED":
            continue
        slot = str(outcome.get("proposed_slot") or "").strip()
        try:
            slot_dt = datetime.strptime(slot, "%Y-%m-%d %H:%M")
        except ValueError:
            continue
        if slot_dt < now:
            continue
        upcoming.append({
            "slot": slot,
            "department": _readable_department(outcome.get("specialist") or "general practice"),
            "practitioner": outcome.get("assigned_practitioner_type", "nurse"),
            "reason": entry.get("symptom_description", "(not provided)"),
            "booked_on": entry.get("timestamp", "?"),
        })

    upcoming.sort(key=lambda appt: appt["slot"])  # "YYYY-MM-DD HH:MM" sorts correctly as text
    return upcoming