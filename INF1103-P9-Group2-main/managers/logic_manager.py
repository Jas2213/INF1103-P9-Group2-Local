"""
logic_manager.py

Business logic engine for clinical triage. Applies deterministic rules
(DevOps proposal, Section 4), handles API-failure fallbacks, and applies
hospital-staff overrides. Staff always win: any field a staff member has
overridden is never changed back by the automatic rules.

Functions called by main.py:
    evaluate_triage_logic(ai_result, patient_history=None, staff_overrides=None)
    apply_staff_override(outcome, ai_result, staff_overrides)
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

# Valid states for the booking workflow
VALID_STATES = [
    "INITIATED",
    "TRIAGED",
    "GUARDRAIL_OVERRIDDEN",
    "SCHEDULED",
    "COMPLETED",
]

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


def apply_staff_risk_values(ai_result, staff_overrides):
    """Copy the AI result and enforce validated staff risk values; latest wins."""
    validated = validate_staff_overrides(staff_overrides)
    updated = dict(ai_result)
    for field, verdict in validated:
        if field in STAFF_OVERRIDE_OPTIONS:
            updated[field] = verdict
    return updated


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


def _build_next_action(decision, specialist, slot, slot_set_by_staff):
    """
    Build the patient-facing next_action text so it always agrees with the
    decision, department and slot actually being proposed.
    """
    department = _readable_department(specialist)

    if decision == "urgent_priority":
        if slot_set_by_staff:
            return f"Your case is marked urgent. Proposed appointment: {slot}."
        return "Please proceed immediately to the nearest Emergency Department."
    if decision == "route_to_specialist":
        return f"Your case has been routed to the {department} department. Proposed appointment: {slot}."
    if decision == "manual_review":
        return "Your case has been placed on the manual review queue."
    return f"A routine appointment with {department} has been proposed for {slot}."


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_triage_logic(ai_result, patient_history=None, staff_overrides=None):
    """
    Apply the business rules to an ai_manager result.

    Rules (Section 4 of the proposal):
      - Direct to specialist: high risk AND history_linked -> specialist.
      - Urgent case: high risk but no matching history -> urgent care / A&E. (no booking needed)
      - Non-urgent: low/medium risk -> next available slot in the right
        department.
    "High risk" means urgency == "urgent" or severity == "high".

    Staff overrides (list of [field, verdict]):
      - "department": the keyword specialist rules are skipped.
      - "urgency" / "severity": the high-risk guardrail is skipped, and
        high risk is judged only from the field(s) staff overrode.
      - "proposed_slot": the slot is taken from ai_result["proposed_slot"]
        instead of being calculated.
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

    ai_result = apply_staff_risk_values(ai_result, staff_overrides)
    overridden = _overridden_fields(staff_overrides)

    severity = str(ai_result.get("severity", "low")).lower()
    urgency = str(ai_result.get("urgency", "non-urgent")).lower()
    tier = str(ai_result.get("recommended_tier", "nurse")).lower()
    specialist = str(ai_result.get("specialist", "general_practice")).lower().strip()
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

    # Slot: staff's slot is used exactly as given
    slot_set_by_staff = "proposed_slot" in overridden and bool(ai_result.get("proposed_slot"))
    if slot_set_by_staff:
        slot = ai_result["proposed_slot"]
    elif decision == "urgent_priority" or urgency == "urgent":
        slot = _calculate_next_slot(days_ahead=2)
    else:
        slot = _calculate_next_slot(days_ahead=5)

    return {
        "decision": decision,
        "next_action": _build_next_action(decision, specialist, slot, slot_set_by_staff),
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


# ---------------------------------------------------------------------------
# Staff override
# ---------------------------------------------------------------------------

def apply_staff_override(outcome, ai_result, staff_overrides):
    """
    Mark an outcome as overridden by hospital staff.

    Args:
        outcome: dict from evaluate_triage_logic.
        ai_result: the AI result regenerated after the staff input; may
                   contain "proposed_slot" ("YYYY-MM-DD HH:MM").
        staff_overrides: list of [field, verdict] pairs.

    Returns:
        a NEW outcome dict (the input is not modified).
    """
    staff_overrides = validate_staff_overrides(staff_overrides)
    updated = dict(outcome)
    updated["nurse_override"] = True
    updated["override_reason"] = "; ".join(
        f"{pair[0]}: {pair[1]}" for pair in (staff_overrides or []) if len(pair) >= 2
    )
    updated["status"] = "GUARDRAIL_OVERRIDDEN"

    if "proposed_slot" in _overridden_fields(staff_overrides) and ai_result.get("proposed_slot"):
        updated["proposed_slot"] = ai_result["proposed_slot"]
        updated["slot_set_by_staff"] = True
        updated["next_action"] = _build_next_action(
            updated.get("decision"), updated.get("specialist"),
            updated["proposed_slot"], True,
        )

    return updated


def apply_nurse_override(current_outcome, override_decision, reason, specialist=None):
    """
    Legacy decision-based override (not used by the current main.py flow,
    which uses apply_staff_override). Returns a new outcome dict.
    """
    updated = dict(current_outcome)

    updated["decision"] = override_decision
    updated["nurse_override"] = True
    updated["override_reason"] = reason
    updated["status"] = "GUARDRAIL_OVERRIDDEN"

    if override_decision == "route_to_specialist":
        updated["specialist"] = specialist
        updated["assigned_practitioner_type"] = "doctor"
        updated["next_action"] = f"Nurse override: Case re-routed to {specialist}."
        updated["proposed_slot"] = _calculate_next_slot(1)
        updated["flag_for_manual_review"] = False
    elif override_decision == "urgent_priority":
        updated["assigned_practitioner_type"] = "doctor"
        updated["next_action"] = "Nurse override: Escalated to urgent priority."
        updated["flag_for_manual_review"] = True
    elif override_decision == "schedule_non_urgent":
        updated["next_action"] = "Nurse override: Scheduled for routine follow-up."
        updated["proposed_slot"] = _calculate_next_slot(3)
        updated["flag_for_manual_review"] = False
    elif override_decision == "manual_review":
        updated["next_action"] = "Nurse override: Placed on manual review queue."
        updated["flag_for_manual_review"] = True

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


def build_manual_review_outcome(decision, reason, specialist=None, slot_time=None):
    """Build a staff-authored outcome without a fabricated AI assessment."""
    if decision not in ("urgent_priority", "route_to_specialist", "schedule_non_urgent"):
        raise ValueError("Choose an urgent, specialist or routine decision.")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("Manual review requires a reason.")
    if decision == "urgent_priority":
        specialist, slot_time = None, None
    else:
        specialist = validate_staff_override("department", specialist).lower().replace(" ", "_")
        slot_time = validate_manual_slot(slot_time)
    base = evaluate_triage_logic({"status": "error"})
    base.update({
        "decision": decision,
        "specialist": specialist,
        "proposed_slot": slot_time,
        "next_action": _build_next_action(decision, specialist, slot_time, bool(slot_time)),
        "flag_for_manual_review": decision == "urgent_priority",
        "nurse_override": True,
        "override_reason": reason.strip(),
        "slot_set_by_staff": bool(slot_time),
        "status": "GUARDRAIL_OVERRIDDEN",
        "assigned_practitioner_type": "doctor" if decision != "schedule_non_urgent" else "nurse",
    })
    return base


def schedule_appointment(outcome, slot_time):
    """
    Book the appointment (called only after the patient accepts).
    Returns {"success": True, "outcome": <new dict>} or
            {"success": False, "error": str}. The input is not modified.
    """
    if outcome.get("status") not in ("TRIAGED", "GUARDRAIL_OVERRIDDEN"):
        return {"success": False, "error": "Patient not ready for scheduling."}
    if not slot_time:
        return {"success": False, "error": "No appointment slot to book."}

    booked = dict(outcome)
    booked["status"] = "SCHEDULED"
    booked["proposed_slot"] = slot_time

    return {"success": True, "outcome": booked}
