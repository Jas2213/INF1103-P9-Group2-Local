"""
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
from managers.temp.base_manager import BaseManager


class LogicManager(BaseManager):
    """Handle the business rules and triage decisions."""

    # General constants
    GP_NAMES = ("general_practice", "general practice", "gp")

    # Departments staff may override to
    DEPARTMENT_OPTIONS = (
        "general_practice",
        "cardiology",
        "neurology",
        "dermatology",
        "oncology"
    )

    # Valid staff override options
    STAFF_OVERRIDE_OPTIONS = {
            "severity": ("low", "medium", "high"),
            "urgency": ("urgent", "non-urgent"),
            "department": DEPARTMENT_OPTIONS
    }

    RISK_FIELDS = ("severity", 'urgency')
    VALID_OVERRIDE_FIELDS = (
        "department",
        "urgency",
        "severity",
        "proposed_slot",
    )

    # Decision values
    DECISION_URGENT = "urgent_priority"
    DECISION_SPECIALIST = "route_to_specialist"
    DECISION_NON_URGENT = "schedule_non_urgent"
    DECISION_MANUAL_REVIEW = "manual_review"

    BOOKABLE_DECISIONS = (
        DECISION_SPECIALIST,
        DECISION_NON_URGENT,
    )

    NO_SLOT_DECISIONS = (
        DECISION_URGENT,
        DECISION_MANUAL_REVIEW,
    )

    # Status values
    STATUS_TRIAGED = "TRIAGED"
    STATUS_OVERRIDDEN = "GUARDRAIL_OVERRIDDEN"
    STATUS_SCHEDULED = "SCHEDULED"

    def __init__(self):
        super().__init__()

    def get_manager_name(self):
        """Return the manager name for the shared logger."""
        return "Logic Manager"

    # ---------------------------------------------------------------------------
    # Small helpers
    # ---------------------------------------------------------------------------

    def _now_iso(self):
        """Return the current date and time in ISO format."""
        return datetime.now().isoformat(timespec="seconds")

    def _normalize_department(self, value):
        """Convert a department name into a standard format."""
        return str(value).lower().replace(" ", "_")

    def _overridden_fields(self, staff_overrides):
        """Return the fields that have been overridden by staff."""
        return {pair[0] for pair in (staff_overrides or [])}

    def _calculate_next_slot(self, days_ahead=3):
        """Calculate a proposed appointment slot."""
        return (
            (datetime.now() + timedelta(days=days_ahead))
            .replace(hour=9, minute=0, second=0, microsecond=0)
            .strftime("%Y-%m-%d %H:%M")
        )

    def _readable_department(self, specialist):
        """Convert a department name into a readable format"""
        return str(specialist).replace("_", " ")


    def _build_next_action(self, decision, specialist, slot):
        """Build a patient-facing message based on the triage decision."""

        department = self._readable_department(specialist)

        if decision == self.DECISION_URGENT:
            return "Please proceed immediately to the nearest Emergency Department."
        
        if decision == self.DECISION_SPECIALIST:
            department = self._readable_department(specialist)
            return f"Your case has been routed to the {department} department. Proposed appointment: {slot}."

        if decision == self.DECISION_MANUAL_REVIEW:
            return "Your case has been placed on the manual review queue."

        return f"A routine appointment with {department} has been proposed for {slot}."

    
    def _make_outcome(self, decision, next_action, **fields):
        """Create a standard triage outcome dictionary."""

        outcome = {
            "decision": decision,
            "next_action": next_action,
            "specialist": None,
            "proposed_slot": None,
            "flag_for_manual_review": False,
            "nurse_override": False,
            "override_reason": None,
            "slot_set_by_staff": False,
            "status": self.STATUS_TRIAGED,
            "assigned_practitioner_type": "nurse",
            "override_applied": False,
            "routing_timestamp": self._now_iso(),
        }

        # Check that only recognised fields are added
        unknown_fields = set(fields) - set(outcome)

        if unknown_fields:
            raise TypeError(
                f"Unexpected outcome fields: {', '.join(sorted(unknown_fields))}"
            )

        outcome.update(fields)

        return outcome

    # ---------------------------------------------------------------------------
    # Staff override validation
    # ---------------------------------------------------------------------------

    def validate_manual_slot(self, slot_time):
        """Require an exact future local date/time for a manually entered slot."""

        if not isinstance(slot_time, str):
            raise ValueError(
                "Enter an appointment date/time as YYYY-MM-DD HH:MM."
            )

        slot_time = slot_time.strip()

        try:
            parsed_slot = datetime.strptime(
                slot_time, "%Y-%m-%d %H:%M"
            )
        except ValueError:
            raise ValueError(
                "Enter a valid appointment date/time as YYYY-MM-DD HH:MM."
            ) from None

        if parsed_slot.strftime("%Y-%m-%d %H:%M") != slot_time:
            raise ValueError(
                "Use the exact format YYYY-MM-DD HH:MM."
            )

        if parsed_slot <= datetime.now():
            raise ValueError(
                "Appointment date/time must be in the future."
            )

        return slot_time

    def validate_staff_override(self, field, verdict):
        """Return a cleaned verdict, or raise ValueError for invalid staff input."""

        if not isinstance(field, str) or field not in self.VALID_OVERRIDE_FIELDS:
            raise ValueError("Unknown staff override field.")

        if not isinstance(verdict, str) or not verdict.strip():
            raise ValueError(f"{field} must be non-empty text.")

        verdict = verdict.strip()
        options = self.STAFF_OVERRIDE_OPTIONS.get(field)

        if options is not None:
            if field == "department":
                verdict = self._normalize_department(verdict)
            else:
                verdict = verdict.lower()

            if verdict not in options:
                raise ValueError(
                    f"{field} must be one of: {', '.join(options)}."
                )

        if field == "proposed_slot":
            verdict = self.validate_manual_slot(verdict)

        return verdict

    def validate_staff_overrides(self, staff_overrides):
        """Validate a list of staff overrides."""

        if staff_overrides is None:
            return []

        if not isinstance(staff_overrides, (list, tuple)):
            raise ValueError("Staff overrides must be a list of [field, verdict] pairs.")

        cleaned = []

        for pair in staff_overrides:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(
                    "Each staff override must contain a field and a verdict."
                )

            field, verdict = pair

            cleaned_verdict = self.validate_staff_override(field, verdict)

            cleaned.append([field, cleaned_verdict])

        return cleaned

    def _enforce_risk_values(self, ai_result, validated_overrides):
        """Copy the AI result and write in already-validated staff risk values; latest wins."""
        updated = dict(ai_result)

        for field, verdict in validated_overrides:
            if field in self.RISK_FIELDS:
                updated[field] = verdict

        return updated

    def apply_staff_risk_values(self, ai_result, staff_overrides):
        """Public entry point: validate the overrides, then enforce them on a copy of the AI result."""
        
        return self._enforce_risk_values(ai_result, self.validate_staff_overrides(staff_overrides))
    

    # ---------------------------------------------------------------------------
    # Checks that run on the AI result before the main evaluation
    # ---------------------------------------------------------------------------

    def needs_follow_up_questions(self, ai_result, red_flags_present):
        """
        Decide whether follow-up questions should be asked.

        Follow-up questions are only worth asking for non-urgent cases. When
        the patient reports red flags, or the AI rates the case urgent or
        high severity, escalate straight away instead of delaying with questions.

        Returns:
            True if follow-up questions should be asked, otherwise False.
        """

        if red_flags_present:
            return False

        if not isinstance(ai_result, dict):
            return False

        return not (
            ai_result.get("urgency") == "urgent"
            or ai_result.get("severity") == "high"
        )

    def apply_red_flag_escalation(self, ai_result, red_flags_present):
        """Copy of ai_result escalated to urgent/high when the patient reported red flags."""
        if not isinstance(ai_result, dict) or not red_flags_present or ai_result.get("status") != "ok":
                return ai_result
        updated = dict(ai_result)
        updated["urgency"] = "urgent"
        updated["severity"] = "high"
        return updated

    # ---------------------------------------------------------------------------
    # Core evaluation
    # ---------------------------------------------------------------------------
        

    def _apply_keyword_rules(self, symptoms_str, urgency, specialist, tier):
        """Keyword specialist rules. Returns the (specialist, tier) to use."""

        if "chest pain" in symptoms_str or "shortness of breath" in symptoms_str:
            return "cardiology", "doctor"
        
        if "headache" in symptoms_str and ("vision" in symptoms_str or "numbness" in symptoms_str):
            return "neurology", "doctor"
        
        if "rash" in symptoms_str and urgency == "urgent":
            return "dermatology", tier
        
        return specialist, tier
    

    def _is_high_risk(self, urgency, severity, overridden):
        """
        High risk means urgency == "urgent" or severity == "high". If staff
        overrode urgency and/or severity, only the overridden field(s) count.
        """
        if "urgency" in overridden or "severity" in overridden:
            return (
                ("urgency" in overridden and urgency == "urgent")
                or ("severity" in overridden and severity == "high")
            )
        return urgency == "urgent" or severity == "high"


    def _choose_decision(self, high_risk, history_linked, has_specialist):
        if high_risk and history_linked and has_specialist:
            return self.DECISION_SPECIALIST      # direct to specialist rule
        
        if high_risk:
            return self.DECISION_URGENT          # urgent case rule
        
        if has_specialist:
            return self.DECISION_SPECIALIST
        
        return self.DECISION_NON_URGENT          # non-urgent rule


    def _choose_slot(self, decision, urgency, staff_values):
        """Return (slot, slot_set_by_staff). Emergencies never get a slot."""

        if decision == self.DECISION_URGENT:
            return None, False
        
        if "proposed_slot" in staff_values:
            return staff_values["proposed_slot"], True
        
        if urgency == "urgent":
            return self._calculate_next_slot(days_ahead=2), False
        
        return self._calculate_next_slot(days_ahead=5), False
    

    def evaluate_triage_logic(self, ai_result, staff_overrides=None, red_flags_present=False):
        """
        Apply the business rules to an ai_manager result.

        Rules (Section 4 of the proposal):
        - Direct to specialist: high risk AND history_linked -> specialist.
        - Urgent case: high risk but no matching history -> urgent care / A&E. (no booking needed)
        - Non-urgent: low/medium risk -> next available slot in the right
            department.
        "High risk" means urgency == "urgent" or severity == "high".

        Severity "high" always goes straight to A&E (no slot). Red flags
        escalate the AI result to urgent/high before staff overrides apply.

        Staff overrides (list of [field, verdict]):
        - "department": use the staff value and skip keyword specialist rules.
        - "urgency" / "severity": the doctor-tier guardrail is skipped, and
            high risk is judged only from the field(s) staff overrode. Only a
            staff "severity" override can lower a high-severity case; a staff
            "urgency" override cannot.
        - "proposed_slot": use the validated staff value for appointment decisions.
            Emergency decisions always have no slot.
        """
        staff_overrides = self.validate_staff_overrides(staff_overrides)

        # API failure (or no usable AI result): manual review, no slot.
        if not isinstance(ai_result, dict) or ai_result.get("status") != "ok":
            return self._make_outcome(
                self.DECISION_MANUAL_REVIEW,
                "Your symptom report could not be processed automatically. A clinical staff member will review your case shortly.",
                flag_for_manual_review=True,
            )

        # Escalate the AI assessment when the patient reports red flags.
        ai_result = self.apply_red_flag_escalation(ai_result, red_flags_present)

        # Apply validated staff decisions after the automatic escalation.
        ai_result = self._enforce_risk_values(ai_result, staff_overrides)

        overridden = self._overridden_fields(staff_overrides)
        staff_values = dict(staff_overrides)  # Latest value for each field wins.

        severity = str(ai_result.get("severity", "low")).lower()
        urgency = str(ai_result.get("urgency", "non-urgent")).lower()
        tier = str(ai_result.get("recommended_tier", "nurse")).lower()
        specialist = str(ai_result.get("specialist", "general_practice")).lower().strip()
        if "department" in staff_values:
            specialist = self._normalize_department(staff_values["department"])
        history_linked = bool(ai_result.get("history_linked", False))

        flagged = ai_result.get("flagged_symptoms", [])
        symptoms_str = " ".join(flagged).lower() + " " + str(ai_result.get("summary", "")).lower()

        # Keyword specialist rules (skipped when staff chose the department)
        if "department" not in overridden:
            specialist, tier = self._apply_keyword_rules(symptoms_str, urgency, specialist, tier)

        # Is this high risk? Staff verdicts on urgency/severity take over.
        staff_set_risk = "urgency" in overridden or "severity" in overridden
        high_risk = self._is_high_risk(urgency, severity, overridden)

        # High severity goes directly to A&E, with no appointment slot.
        if severity == "high":
            return self._make_outcome(
                self.DECISION_URGENT,
                self._build_next_action(self.DECISION_URGENT, specialist, None),
                specialist=specialist,
                flag_for_manual_review=True,
                assigned_practitioner_type="doctor",
            )

        # Guardrail: high-risk cases need a doctor (skipped if staff set the risk)
        override_applied = False
        if high_risk and not staff_set_risk and tier != "doctor":
            tier = "doctor"
            override_applied = True

        has_specialist = specialist not in self.GP_NAMES
        decision = self._choose_decision(high_risk, history_linked, has_specialist)
        slot, slot_set_by_staff = self._choose_slot(decision, urgency, staff_values)

        return self._make_outcome(
            decision,
            self._build_next_action(decision, specialist, slot),
            specialist=specialist,
            proposed_slot=slot,
            flag_for_manual_review=urgency == "urgent" or decision == self.DECISION_URGENT,
            slot_set_by_staff=slot_set_by_staff,
            status=self.STATUS_OVERRIDDEN if override_applied else self.STATUS_TRIAGED,
            assigned_practitioner_type=tier,
            override_applied=override_applied,
        )

    # ---------------------------------------------------------------------------
    # Staff override
    # ---------------------------------------------------------------------------

    def apply_staff_override(self, outcome, staff_overrides):
        """
        Mark an outcome as overridden by hospital staff.

        Args:
            outcome: dict from evaluate_triage_logic.
            staff_overrides: list of [field, verdict] pairs (validated here).

        Returns:
            a NEW outcome dict (the input is not modified).
        """
        staff_overrides = self.validate_staff_overrides(staff_overrides)
        updated = dict(outcome)
        updated["nurse_override"] = True
        updated["override_reason"] = "; ".join(
            f"{field}: {verdict}" for field, verdict in staff_overrides
        )
        updated["status"] = self.STATUS_OVERRIDDEN

        staff_values = dict(staff_overrides)
        if updated.get("decision") in self.NO_SLOT_DECISIONS:
            updated["proposed_slot"] = None
            updated["slot_set_by_staff"] = False
        else:
            if "department" in staff_values:
                updated["specialist"] = self._normalize_department(staff_values["department"])
            if "proposed_slot" in staff_values:
                updated["proposed_slot"] = staff_values["proposed_slot"]
                updated["slot_set_by_staff"] = True
        updated["next_action"] = self._build_next_action(
            updated.get("decision"), updated.get("specialist"), updated.get("proposed_slot"),
        )

        return updated

    # ---------------------------------------------------------------------------
    # Booking
    # ---------------------------------------------------------------------------

    def schedule_appointment(self, outcome, slot_time):
        """
        Book the appointment (called only after the patient accepts).
        Returns {"success": True, "outcome": <new dict>} or
                {"success": False, "error": str}. The input is not modified.
        """
        if not isinstance(outcome, dict) or outcome.get("status") not in (self.STATUS_TRIAGED, self.STATUS_OVERRIDDEN):
            return {"success": False, "error": "Patient not ready for scheduling."}
        if outcome.get("decision") not in self.BOOKABLE_DECISIONS:
            return {"success": False, "error": "This decision is not ready for an appointment."}
        if not slot_time:
            return {"success": False, "error": "No appointment slot to book."}
        try:
            slot_time = self.validate_manual_slot(slot_time)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        booked = dict(outcome)
        booked["status"] = self.STATUS_SCHEDULED
        booked["proposed_slot"] = slot_time

        return {"success": True, "outcome": booked}

    def finalize_proposed_slot(self, outcome, slot_key):
        """Replace the outcome's placeholder slot with the real available
        slot main.py found, and rebuild next_action so the patient-facing
        text matches. Returns a NEW outcome dict."""
        if outcome.get("decision") not in self.BOOKABLE_DECISIONS:
            raise ValueError("This decision cannot receive an appointment slot.")
        slot_key = self.validate_manual_slot(slot_key)
        updated = dict(outcome)
        updated["proposed_slot"] = slot_key
        updated["next_action"] = self._build_next_action(
            updated.get("decision"), updated.get("specialist"), slot_key,
        )
        return updated

    def mark_no_slots_available(self, outcome):
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

    def get_upcoming_appointments(self, patient_record, now=None):
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
            if not isinstance(outcome, dict) or outcome.get("status") != self.STATUS_SCHEDULED:
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
                "department": self._readable_department(outcome.get("specialist") or "general practice"),
                "practitioner": outcome.get("assigned_practitioner_type", "nurse"),
                "reason": entry.get("symptom_description", "(not provided)"),
                "booked_on": entry.get("timestamp", "?"),
            })

        upcoming.sort(key=lambda appt: appt["slot"])  # "YYYY-MM-DD HH:MM" sorts correctly as text
        return upcoming