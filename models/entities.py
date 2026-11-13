# ===========================================================================
# PHASE 2 DIRECTION (read before changing any manager)
# ---------------------------------------------------------------------------
# GROUND RULES
#  1. The four managers import shared classes only with `from models import ...`
#     and never import each other. models imports no manager.
#  2. main.py, staff.py and generate_slots.py NEVER import models and never call
#     a method on a model object. They call manager functions only, and may
#     pass what one manager returns to another manager. No business rules, no
#     stage/role/decision strings and no dict editing in these three files.
#  3. Objects (not dicts) travel between managers. Only data_manager converts
#     to/from dicts (to_dict / from_dict).
#  4. Saved JSON shapes and stored string values must not change: existing
#     files in data/ must keep loading.
#  5. Do not edit models/ to suit one manager. A class only one manager uses
#     lives in that manager's file.
#  6. Keep "never mutate the input": use with_...(), send_to_patient(), close().
#  7. Do not touch the test scripts until the Phase 2 manager work is done.
# ===========================================================================

# WHO USES EACH CLASS IN PHASE 2
#  Intake
#     io_manager   : keep the prompt_symptom_* input functions; build an Intake
#                    from the answers and call intake.validate().
#     ai_manager   : build_prompt, generate_follow_up_questions and
#                    reassess_ai_triage_result take an Intake (use to_dict() for
#                    prompt JSON). Use intake.with_follow_up(...) after the questions.
#     logic_manager: needs_follow_up_questions reads intake.red_flags_present.
#  AIResult
#     ai_manager   : every public function returns an AIResult. Parse Gemini JSON
#                    with AIResult.from_dict() (it validates, so delete
#                    REQUIRED_FIELDS and _validate_response). Keep the response
#                    schema for the Gemini call only. On failure return
#                    AIResult.failure(reason).
#     logic_manager: evaluate_triage_logic consumes an AIResult; replace
#                    apply_red_flag_escalation with ai_result.with_risk_values().
#     io_manager   : summary and staff displays read .ok, .summary,
#                    .flagged_symptoms, .urgency, .severity.
#  StaffOverride
#     io_manager   : after the staff member picks a field and value, build a
#                    StaffOverride (it validates and cleans the value).
#     logic_manager: delete validate_staff_override(s) (the constructor does it).
#                    KEEP validate_manual_slot: "must be in the future" needs
#                    "now" and stays here.
#     ai_manager   : build_override_prompt takes StaffOverride objects; use
#                    override.to_pair() for the [field, verdict] lines.
#  TriageOutcome
#     logic_manager: the only creator. Replace _make_outcome with
#                    TriageOutcome(...) and dict copies with outcome.with_changes(...).
#                    next_action text is still built here.
#     io_manager   : display_final_outcome and display_staff_case read its properties.
#  CaseRecord (abstract)
#     io_manager   : list mixed PendingCase / HistoryRecord objects with one loop
#                    calling summary_line() (polymorphism).
#  PendingCase
#     logic_manager: the review step calls pending.send_to_patient(...) with the
#                    reviewer's staff id (a plain string passed in by main.py).
#     data_manager : save / load / list pending cases; list_pending_cases(stage)
#                    uses PendingCase.stage.
#  HistoryRecord
#     ai_manager   : the prompt history reads record.intake.description and
#                    record.ai_result.summary from patient.recent_history(5).
#     logic_manager: get_upcoming_appointments reads HistoryRecord.outcome
#                    (status SCHEDULED, proposed_slot).
#     data_manager : history lives inside the Patient file.

import copy                                                                      # [1]
from abc import abstractmethod                                                   # [3]
from datetime import datetime                                                    # [2]
 
from models.base import Serializable                                             # [1]
from models.constants import Severity, Tier, Urgency                             # [1]
from models.constants import Decision, Department, OverrideField, Status         # [2]
from models.constants import PatientDecision, Stage                              # [3]
from models.errors import AIResponseError, DataCorruptError, ValidationError     # [1]
 
 

# Intake and AIResult entities with validation and copy-on-change
# ===========================================================================
 
#  Turns a raw string into one of the allowed choices, or raise an error and list the allowed choices
def _to_enum(enum_class, value, error_class, label):

    # Used by: AIResult to raises AIResponseError, StaffOverride and TriageOutcome to raise ValidationError and PendingCase for HistoryRecord

    try:
        return enum_class(value)
    except ValueError:
        allowed = ", ".join(enum_class.values())
        raise error_class(f"{label} must be one of: {allowed} (got {value!r}).") from None
 

#  Makes a copy of an object with one or two fields changed
def _replace(obj, **changes):
 
    # Used by: every entity's with_...() / send_to_patient() method.

    clone = copy.copy(obj)
    for name, value in changes.items():
        if not hasattr(clone, f"_{name}"):
            raise ValidationError(f"Unknown field: {name}")
        setattr(clone, f"_{name}", value)
    return clone
 

#  Essentially what the patient report is their reason for wanting to arrange an appointment. e.g, (symptoms, duration, 1-10 rating)
class Intake(Serializable):
 
    # IO manager: builds it from the patient's answers and runs validate()
    # AI manager: prompts additional inputs
    # Logic manager: reads red_flags_present and CaseRecord

 
    def __init__(self, description, duration="unknown", self_rated_severity=None,
                 red_flags_present=False, follow_up_questions=None, follow_up_answers=None):
        self._description = str(description).strip()
        self._duration = str(duration).strip()
        self._self_rated_severity = self_rated_severity
        self._red_flags_present = red_flags_present
        self._follow_up_questions = list(follow_up_questions or [])
        self._follow_up_answers = list(follow_up_answers or [])
 
    @property
    def description(self):
        return self._description
 
    @property
    def duration(self):
        return self._duration
 
    @property
    def self_rated_severity(self):
        return self._self_rated_severity
 
    @property
    def red_flags_present(self):
        return self._red_flags_present
 
    @property
    def follow_up_questions(self):
        return list(self._follow_up_questions)
 
    @property
    def follow_up_answers(self):
        return list(self._follow_up_answers)
 
    def validate(self):
        """Raise ValidationError if this intake is not complete and sensible."""
        if not self._description:
            raise ValidationError("Please describe your symptoms.")
        if not self._duration:
            raise ValidationError("Please say how long you have had the symptoms.")
        rating = self._self_rated_severity
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 10:
            raise ValidationError("The severity rating must be a whole number from 1 to 10.")
        if not isinstance(self._red_flags_present, bool):
            raise ValidationError("The red flag answer must be yes or no.")
 
    def with_follow_up(self, questions, answers):
        """Return a copy that also holds the follow-up questions and answers."""
        return _replace(self, follow_up_questions=list(questions), follow_up_answers=list(answers))
 
    def to_dict(self):
        return {
            "description": self._description,
            "duration": self._duration,
            "self_rated_severity": self._self_rated_severity,
            "red_flags_present": self._red_flags_present,
            "follow_up_questions": list(self._follow_up_questions),
            "follow_up_answers": list(self._follow_up_answers),
        }
 
    @classmethod
    def from_dict(cls, data):

        # Accepts both the intake keys (description, duration) and the patient files (symptom_description, symptom_duration).

        if not isinstance(data, dict):
            raise DataCorruptError("Intake data must be a dict.")
        description = data.get("description", data.get("symptom_description"))
        if not isinstance(description, str) or not description.strip():
            raise DataCorruptError("Stored intake has no symptom description.")
        return cls(
            description,
            duration=data.get("duration", data.get("symptom_duration", "unknown")) or "unknown",
            self_rated_severity=data.get("self_rated_severity"),
            red_flags_present=bool(data.get("red_flags_present", False)),
            follow_up_questions=data.get("follow_up_questions"),
            follow_up_answers=data.get("follow_up_answers"),
        )

 
#  holds the AI’s answer, in ok or error. An ok result with a missing field or a made-up value raises AIResponseError
class AIResult(Serializable):
 
    # AI manager: creates and validates response
    # Logic manager: evaluates gathered response
    # IO manager: shows summary and flagged symptoms to patient and staff
 
    OK = "ok"
    ERROR = "error"
 
    def __init__(self, status, summary=None, urgency=None, severity=None,
                 recommended_tier=None, specialist=None, flagged_symptoms=None,
                 history_linked=False, confidence=None, error_reason=None):
        if status not in (self.OK, self.ERROR):
            raise AIResponseError(f"AI result status must be 'ok' or 'error' (got {status!r}).")
        self._status = status
        self._error_reason = error_reason
        self._summary = None
        self._urgency = None
        self._severity = None
        self._recommended_tier = None
        self._specialist = None
        self._flagged_symptoms = []
        self._history_linked = False
        self._confidence = None
        if status == self.OK:
            self._set_ok_fields(summary, urgency, severity, recommended_tier,
                                specialist, flagged_symptoms, history_linked, confidence)
 
    def _set_ok_fields(self, summary, urgency, severity, tier, specialist,
                       flagged_symptoms, history_linked, confidence):
        """Check and store the fields of an "ok" result; raise AIResponseError if any is bad."""
        if not isinstance(summary, str) or not summary.strip():
            raise AIResponseError("AI result needs a text summary.")
        if not isinstance(specialist, str) or not specialist.strip():
            raise AIResponseError("AI result needs a specialist.")
        if not isinstance(flagged_symptoms, list) or not all(isinstance(s, str) for s in flagged_symptoms):
            raise AIResponseError("flagged_symptoms must be a list of text items.")
        if not isinstance(history_linked, bool):
            raise AIResponseError("history_linked must be true or false.")
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            raise AIResponseError("confidence must be a number between 0.0 and 1.0.")
        self._summary = summary
        self._urgency = _to_enum(Urgency, urgency, AIResponseError, "urgency")
        self._severity = _to_enum(Severity, severity, AIResponseError, "severity")
        self._recommended_tier = _to_enum(Tier, tier, AIResponseError, "recommended_tier")
        self._specialist = specialist
        self._flagged_symptoms = list(flagged_symptoms)
        self._history_linked = history_linked
        self._confidence = float(confidence)
 
    @classmethod
    def failure(cls, reason):
        """An error-state result (API down, bad response, ...)."""
        return cls(cls.ERROR, error_reason=reason)
 
    @property
    def status(self):
        return self._status
 
    @property
    def ok(self):
        """True if this is a usable result."""
        return self._status == self.OK
 
    @property
    def error_reason(self):
        return self._error_reason
 
    @property
    def summary(self):
        return self._summary
 
    @property
    def urgency(self):
        return self._urgency
 
    @property
    def severity(self):
        return self._severity
 
    @property
    def recommended_tier(self):
        return self._recommended_tier
 
    @property
    def specialist(self):
        return self._specialist
 
    @property
    def flagged_symptoms(self):
        return list(self._flagged_symptoms)
 
    @property
    def history_linked(self):
        return self._history_linked
 
    @property
    def confidence(self):
        return self._confidence
 
    def with_risk_values(self, urgency=None, severity=None):
        """
        Return a copy with urgency and/or severity replaced (red-flag escalation,
        staff verdicts). A failed result is returned unchanged as a copy.
        """
        if not self.ok:
            return copy.copy(self)
        changes = {}
        if urgency is not None:
            changes["urgency"] = _to_enum(Urgency, urgency, ValidationError, "urgency")
        if severity is not None:
            changes["severity"] = _to_enum(Severity, severity, ValidationError, "severity")
        return _replace(self, **changes)
 
    def to_dict(self):
        if not self.ok:
            return {"status": self.ERROR, "error_reason": self._error_reason}
        return {
            "status": self.OK,
            "summary": self._summary,
            "urgency": self._urgency.value,
            "severity": self._severity.value,
            "recommended_tier": self._recommended_tier.value,
            "specialist": self._specialist,
            "flagged_symptoms": list(self._flagged_symptoms),
            "history_linked": self._history_linked,
            "confidence": self._confidence,
        }
 
    @classmethod
    def from_dict(cls, data):
        """
        Build from Gemini's parsed JSON (which has no "status" - treated as ok) or
        from a stored result. Raises AIResponseError if anything is unusable.
        """
        if not isinstance(data, dict):
            raise AIResponseError("AI result must be a JSON object.")
        status = data.get("status", cls.OK)
        if status == cls.ERROR:
            return cls.failure(data.get("error_reason", "Unknown AI error."))
        return cls(
            status,
            summary=data.get("summary"),
            urgency=data.get("urgency"),
            severity=data.get("severity"),
            recommended_tier=data.get("recommended_tier"),
            specialist=data.get("specialist"),
            flagged_symptoms=data.get("flagged_symptoms"),
            history_linked=data.get("history_linked"),
            confidence=data.get("confidence"),
        )


# StaffOverride and TriageOutcome
# ===========================================================================
 
def _now_iso():
    # Current local time as "YYYY-MM-DDTHH:MM:SS", the format Phase 1 stored.
    return datetime.now().isoformat(timespec="seconds")
 

#  Its a change of decision made by the staff to override the verdict of the AI or logic manager. and the constructor cleans the input e.g.,(spaces, upper/lower case, "general practice" -> "general_practice")
class StaffOverride(Serializable):
 
    # IO manager: builds one per verdict
    # Logic manager: applies the verdict
    # AI manager: override prompt lists 

 
    _CHOICES = {
        OverrideField.SEVERITY: Severity,
        OverrideField.URGENCY: Urgency,
        OverrideField.DEPARTMENT: Department,
    }
 
    def __init__(self, field, verdict):
        self._field = _to_enum(OverrideField, str(field).strip().lower(), ValidationError, "Override field")
        if not isinstance(verdict, str) or not verdict.strip():
            raise ValidationError(f"{self._field.value} must be non-empty text.")
        verdict = verdict.strip()
        choices = self._CHOICES.get(self._field)
        if choices is not None:
            verdict = verdict.lower()
            if self._field == OverrideField.DEPARTMENT:
                verdict = verdict.replace(" ", "_")
            verdict = _to_enum(choices, verdict, ValidationError, self._field.value).value
        else:
            self._check_slot_format(verdict)
        self._verdict = verdict
 
    @staticmethod
    def _check_slot_format(text):
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValidationError("Enter a valid appointment date/time as YYYY-MM-DD HH:MM.") from None
        if parsed.strftime("%Y-%m-%d %H:%M") != text:
            raise ValidationError("Use the exact format YYYY-MM-DD HH:MM.")
 
    @property
    def field(self):
        return self._field
 
    @property
    def verdict(self):
        return self._verdict
 
    def to_pair(self):
        """[field, verdict] - the shape Phase 1 stored in staff_overrides."""
        return [self._field.value, self._verdict]
 
    def to_dict(self):
        return {"field": self._field.value, "verdict": self._verdict}
 
    @classmethod
    def from_dict(cls, data):
        """Accepts a dict with "field" and "verdict", or a Phase 1 [field, verdict] pair."""
        if isinstance(data, dict):
            return cls(data.get("field"), data.get("verdict"))
        if isinstance(data, (list, tuple)) and len(data) == 2:
            return cls(data[0], data[1])
        raise ValidationError("A staff override needs a field and a verdict.")
 

#  Holds the twelve fields of the logic manager’s decision. The rules that choose them stay in logic_manager.
class TriageOutcome(Serializable):
 
    # Logic manager: Creates and updates it
    # IO manager: Displays the next step, department and slot

 
    def __init__(self, decision, next_action, specialist=None, proposed_slot=None,
                 flag_for_manual_review=False, nurse_override=False, override_reason=None,
                 slot_set_by_staff=False, status=Status.TRIAGED,
                 assigned_practitioner_type=Tier.NURSE, override_applied=False,
                 routing_timestamp=None):
        self._decision = _to_enum(Decision, decision, ValidationError, "decision")
        self._next_action = next_action
        self._specialist = specialist
        self._proposed_slot = proposed_slot
        self._flag_for_manual_review = flag_for_manual_review
        self._nurse_override = nurse_override
        self._override_reason = override_reason
        self._slot_set_by_staff = slot_set_by_staff
        self._status = _to_enum(Status, status, ValidationError, "status")
        self._assigned_practitioner_type = _to_enum(
            Tier, assigned_practitioner_type, ValidationError, "assigned_practitioner_type")
        self._override_applied = override_applied
        self._routing_timestamp = routing_timestamp or _now_iso()
 
    @property
    def decision(self):
        return self._decision
 
    @property
    def next_action(self):
        return self._next_action
 
    @property
    def specialist(self):
        return self._specialist
 
    @property
    def proposed_slot(self):
        return self._proposed_slot
 
    @property
    def flag_for_manual_review(self):
        return self._flag_for_manual_review
 
    @property
    def nurse_override(self):
        return self._nurse_override
 
    @property
    def override_reason(self):
        return self._override_reason
 
    @property
    def slot_set_by_staff(self):
        return self._slot_set_by_staff
 
    @property
    def status(self):
        return self._status
 
    @property
    def assigned_practitioner_type(self):
        return self._assigned_practitioner_type
 
    @property
    def override_applied(self):
        return self._override_applied
 
    @property
    def routing_timestamp(self):
        return self._routing_timestamp
 
    def with_changes(self, **changes):

        # Return a copy with the named fields changed, e.g. outcome.with_changes(proposed_slot="2030-01-20 10:30", status=Status.SCHEDULED).

        for name, enum_class in (("decision", Decision), ("status", Status),
                                 ("assigned_practitioner_type", Tier)):
            if name in changes:
                changes[name] = _to_enum(enum_class, changes[name], ValidationError, name)
        return _replace(self, **changes)
 
    def to_dict(self):
        return {
            "decision": self._decision.value,
            "next_action": self._next_action,
            "specialist": self._specialist,
            "proposed_slot": self._proposed_slot,
            "flag_for_manual_review": self._flag_for_manual_review,
            "nurse_override": self._nurse_override,
            "override_reason": self._override_reason,
            "slot_set_by_staff": self._slot_set_by_staff,
            "status": self._status.value,
            "assigned_practitioner_type": self._assigned_practitioner_type.value,
            "override_applied": self._override_applied,
            "routing_timestamp": self._routing_timestamp,
        }
 
    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict) or "decision" not in data:
            raise DataCorruptError("Stored outcome has no decision.")
        return cls(
            data["decision"],
            data.get("next_action"),
            specialist=data.get("specialist"),
            proposed_slot=data.get("proposed_slot"),
            flag_for_manual_review=data.get("flag_for_manual_review", False),
            nurse_override=data.get("nurse_override", False),
            override_reason=data.get("override_reason"),
            slot_set_by_staff=data.get("slot_set_by_staff", False),
            status=data.get("status", Status.TRIAGED),
            assigned_practitioner_type=data.get("assigned_practitioner_type", Tier.NURSE),
            override_applied=data.get("override_applied", False),
            routing_timestamp=data.get("routing_timestamp"),
        )



# CaseRecord, PendingCase and HistoryRecord
# ===========================================================================
 
#  CaseRecord is the abstract parent. It holds what open and finished requests share, so nothing is repeated
class CaseRecord(Serializable):

    # Each subclass says how it describes itself on one line and how it is saved, so a list of mixed records can be shown with one loop (polymorphism).
 
    # Used by: PendingCase and HistoryRecord (inherit it), 
    # IO manager: lists records through summary_line
    # Data manager: saves them via to_dict

 
    def __init__(self, intake, issue_type="new", ai_result=None, outcome=None,
                 staff_overrides=None, reviewed_by=None, reviewed_at=None):
        self._intake = intake
        self._issue_type = issue_type
        self._ai_result = ai_result
        self._outcome = outcome
        self._staff_overrides = list(staff_overrides or [])
        self._reviewed_by = reviewed_by
        self._reviewed_at = reviewed_at
 
    @property
    def intake(self):
        return self._intake
 
    @property
    def issue_type(self):
        return self._issue_type
 
    @property
    def ai_result(self):
        return self._ai_result
 
    @property
    def outcome(self):
        return self._outcome
 
    @property
    def staff_overrides(self):
        return list(self._staff_overrides)
 
    @property
    def reviewed_by(self):
        return self._reviewed_by
 
    @property
    def reviewed_at(self):
        return self._reviewed_at
 
    @abstractmethod
    def summary_line(self):
        """One-line description of this record for lists shown to staff or patients."""
 
    def _shared_dict(self):
        """The stored fields both subclasses have in common (Phase 1 key names)."""
        intake = self._intake
        return {
            "symptom_description": intake.description,
            "symptom_duration": intake.duration,
            "self_rated_severity": intake.self_rated_severity,
            "red_flags_present": intake.red_flags_present,
            "follow_up_questions": intake.follow_up_questions,
            "follow_up_answers": intake.follow_up_answers,
            "issue_type": self._issue_type,
            "ai_result": self._ai_result.to_dict() if self._ai_result else None,
            "outcome": self._outcome.to_dict() if self._outcome else None,
            "staff_overrides": [override.to_pair() for override in self._staff_overrides],
            "reviewed_by": self._reviewed_by,
            "reviewed_at": self._reviewed_at,
        }
 
    @staticmethod
    def _shared_kwargs(data):
        """Rebuild the shared constructor arguments from a stored dict."""
        if not isinstance(data, dict):
            raise DataCorruptError("Stored case must be a dict.")
        ai_data = data.get("ai_result")
        outcome_data = data.get("outcome")
        return {
            "intake": Intake.from_dict(data),
            "issue_type": data.get("issue_type", "new"),
            "ai_result": AIResult.from_dict(ai_data) if isinstance(ai_data, dict) else None,
            "outcome": TriageOutcome.from_dict(outcome_data) if isinstance(outcome_data, dict) else None,
            "staff_overrides": [StaffOverride.from_dict(pair) for pair in data.get("staff_overrides") or []],
            "reviewed_by": data.get("reviewed_by"),
            "reviewed_at": data.get("reviewed_at"),
        }
 


#  A request in progress. For instance, send_to_patient() is the staff step, and it keeps the AI result from before any override
class PendingCase(CaseRecord):
 
    # Logic manager: Calls send_to_patient when a staff finish a review
    # Data manager: Saves, loads and lists it
    # IO manager: lists waiting requests via summary_line

    def __init__(self, intake, stage=Stage.AWAITING_STAFF, submitted_at=None,
                 original_ai_result=None, **shared):
        super().__init__(intake, **shared)
        self._stage = _to_enum(Stage, stage, DataCorruptError, "stage")
        self._submitted_at = submitted_at or _now_iso()
        self._original_ai_result = original_ai_result
 
    @property
    def stage(self):
        return self._stage
 
    @property
    def submitted_at(self):
        return self._submitted_at
 
    @property
    def original_ai_result(self):
        # The AI result before any staff override (None if staff accepted as is)
        return self._original_ai_result
 
    def send_to_patient(self, ai_result, outcome, staff_overrides, reviewed_by, reviewed_at=None):
        """
        Staff review finished: return a copy now awaiting the patient. If staff
        overrode anything, the AI result from before the override is kept.
        """
        overrides = list(staff_overrides)
        original = (self._original_ai_result or self._ai_result) if overrides else self._original_ai_result
        return _replace(
            self, stage=Stage.AWAITING_PATIENT, ai_result=ai_result, outcome=outcome,
            staff_overrides=overrides, reviewed_by=reviewed_by,
            reviewed_at=reviewed_at or _now_iso(), original_ai_result=original,
        )
 
    def close(self, patient_decision, outcome=None):
    
        # The patient has responded: return the finished HistoryRecord. Pass the updated outcome (e.g. the booked one) if it changed;
        # A rejected proposal is marked as dropped.

        decision = _to_enum(PatientDecision, patient_decision, ValidationError, "patient_decision")
        return HistoryRecord(
            self._intake,
            patient_decision=decision,
            request_dropped=decision == PatientDecision.REJECTED,
            issue_type=self._issue_type,
            ai_result=self._ai_result,
            outcome=outcome or self._outcome,
            staff_overrides=self._staff_overrides,
            reviewed_by=self._reviewed_by,
            reviewed_at=self._reviewed_at,
        )
 
    def summary_line(self):
        if self._ai_result is not None and self._ai_result.ok:
            summary = self._ai_result.summary
        else:
            summary = "(AI summary unavailable)"
        return f"submitted {self._submitted_at} | {summary}"
 
    def to_dict(self):
        data = self._shared_dict()
        data["stage"] = self._stage.value
        data["submitted_at"] = self._submitted_at
        if self._original_ai_result is not None:
            data["original_ai_result"] = self._original_ai_result.to_dict()
        return data
 
    @classmethod
    def from_dict(cls, data):
        kwargs = cls._shared_kwargs(data)
        original = data.get("original_ai_result")
        return cls(
            kwargs.pop("intake"),
            stage=data.get("stage", Stage.AWAITING_STAFF),
            submitted_at=data.get("submitted_at"),
            original_ai_result=AIResult.from_dict(original) if isinstance(original, dict) else None,
            **kwargs,
        )
 

#  It is a finished request with the patient’s decision, a dropped flag and a timestamp
class HistoryRecord(CaseRecord):

    # Data manager: stores it in the patient's history
    # AI manager: recent visits is loaded into the prompt
    # Logic manager: upcoming appointments are found from the outcomes
    # IO manager: past visits via summary_line
    
 
    def __init__(self, intake, patient_decision=None, request_dropped=False,
                 timestamp=None, **shared):
        super().__init__(intake, **shared)
        self._patient_decision = (
            None if patient_decision is None
            else _to_enum(PatientDecision, patient_decision, DataCorruptError, "patient_decision")
        )
        self._request_dropped = bool(request_dropped)
        self._timestamp = timestamp or _now_iso()
 
    @property
    def patient_decision(self):
        return self._patient_decision
 
    @property
    def request_dropped(self):
        return self._request_dropped
 
    @property
    def timestamp(self):
        return self._timestamp
 
    def summary_line(self):
        decision = self._patient_decision.value if self._patient_decision else "n/a"
        return f"{self._timestamp} | {self._intake.description} -> {decision}"
 
    def to_dict(self):
        data = self._shared_dict()
        data["timestamp"] = self._timestamp
        data["patient_decision"] = self._patient_decision.value if self._patient_decision else None
        data["request_dropped"] = self._request_dropped
        return data
 
    @classmethod
    def from_dict(cls, data):
        kwargs = cls._shared_kwargs(data)
        return cls(
            kwargs.pop("intake"),
            patient_decision=data.get("patient_decision"),
            request_dropped=data.get("request_dropped", False),
            timestamp=data.get("timestamp"),
            **kwargs,
        )