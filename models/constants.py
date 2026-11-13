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

# WHO USES EACH ENUM IN PHASE 2 (delete the old string constants they replace)
#  Urgency, Severity, Tier
#     ai_manager   : build the Gemini response schema choices from .values();
#                    delete VALID_URGENCY / VALID_SEVERITY / VALID_TIER.
#     logic_manager: high-risk rule, severity "high" -> A&E, doctor-tier guardrail.
#     io_manager   : offer .values() as the choices when staff override a field.
#  Department
#     logic_manager: replace DEPARTMENT_OPTIONS, _GP_NAMES and keyword routing results.
#     io_manager   : offer Department.values() in the staff override prompt.
#     ai_manager   : the override prompt may list Department.values().
#  OverrideField
#     io_manager   : replace OVERRIDE_FIELDS in prompt_override_field.
#     logic_manager: replace VALID_OVERRIDE_FIELDS / RISK_FIELDS.
#  Decision, Status
#     logic_manager: replace DECISION_* and STATUS_*. The only manager that SETS
#                    them. io_manager only displays them. Values must not change.
#  Stage
#     data_manager : list_pending_cases(stage) and any "cases awaiting staff /
#                    patient" helpers use Stage members.
#     logic_manager: the review step moves a case on via PendingCase.send_to_patient().
#     io_manager   : waiting messages. main.py never names a stage.
#  PatientDecision
#     logic_manager: owns the accept / reject / acknowledge handling.
#     data_manager : closing a request passes the decision to
#                    Patient.close_pending_case(). main.py never writes
#                    "accepted" / "rejected" strings.
#  Role
#     io_manager   : the staff-role prompt offers Role.NURSE and Role.DOCTOR only.
#     data_manager : creates the staff account with the chosen role and stores it.
#     SlotStatus is NOT in this file: create it inside data_manager.



from enum import StrEnum
 
 # gives the full list of allowed values, and tell me whether this text is an allowed value
class _Choice(StrEnum):
 
    @classmethod
    def values(cls):
        # All allowed values as a tuple of plain strings
        return tuple(member.value for member in cls)
 
    @classmethod
    def is_valid(cls, value):
        # returns True or False depending whether a string is an allowed value. It never raises an error
        return value in cls.values()
 


# AI result fields used by AI, Logic, and IO Manager
# ---------------------------------------------------------------------------

 # two choices for how quickly a case needs attention (Urgent or Non-Urgent). 
class Urgency(_Choice):
    # AI manager: validates Gemini output and builds the response
    # Logic manager: high-risk rule, staff override validation
    # IO manager: staff override menu

    URGENT = "urgent"
    NON_URGENT = "non-urgent"

 
#  Measures if the symptom is low, medium, or high severity. If high, send straight to A&E don't waste time.
class Severity(_Choice):
    # AI manager: validation, response schema
    # Logic manager: high severity goes straight to A&E; override validation
    # IO manager: staff override menu.

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

 
#  Determines if the staff is a doctor or a nurse that is handling the case
class Tier(_Choice):
    # AI manager: validation, response schema 
    # logic_manager: guardrail that forces high-risk cases to a doctor.

    DOCTOR = "doctor"
    NURSE = "nurse"
 

#  Determines which department each case is routed to. e.g., Cardiology, or nerulogy.
class Department(_Choice):
    # Logic manager: override validation, keyword routing
    # IO manager: staff override prompt 
    # AI manager: override prompt

    GENERAL_PRACTICE = "general_practice"
    CARDIOLOGY = "cardiology"
    NEUROLOGY = "neurology"
    DERMATOLOGY = "dermatology"
    ONCOLOGY = "oncology"



# Staff overrides fields used by AI, Logic, and IO Manager
# ---------------------------------------------------------------------------
 
#  Fields hospital staff is allowed to override decisions from different case. (department, urgency, severity and appointment slot)
class OverrideField(_Choice):
    # IO manager: to collect which field to override 
    # Logic manager: validates and applies the override 
    # AI manager: override prompt from logic.

    DEPARTMENT = "department"
    URGENCY = "urgency"
    SEVERITY = "severity"
    PROPOSED_SLOT = "proposed_slot"
 

 

# Outcome of triage. Called by IO, logic, and Data Manager
# ---------------------------------------------------------------------------

#  logic manager decides urgent priority, route to a specialist, schedule a routine appointment, or send to manual review.
class Decision(_Choice):
    # Logic Manager: sets decision based on business rules 
    # IO manager: displays decision
    # Data Manager: stored inside history records

    URGENT_PRIORITY = "urgent_priority"
    ROUTE_TO_SPECIALIST = "route_to_specialist"
    SCHEDULE_NON_URGENT = "schedule_non_urgent"
    MANUAL_REVIEW = "manual_review"
 

#  Progress of the case if its at triaged, overridden by staff, or scheduled stage
class Status(_Choice):

    # logic manager: To sets or checks before booking and when used to list upcoming appointments
    # Data manager: store the status so that other managers can check what status is it at now

    TRIAGED = "TRIAGED"
    GUARDRAIL_OVERRIDDEN = "GUARDRAIL_OVERRIDDEN"
    SCHEDULED = "SCHEDULED"




# Workflow between patient and staff. Used by Data, and IO manager
# ---------------------------------------------------------------------------
 
#  When the request is open, shows the status of pending staff response, or patient to respond
class Stage(_Choice):

    # Data manager list_pending_cases filters by stage
    # IO manager waiting message when pending someone else to follow up

    AWAITING_STAFF = "awaiting_staff"
    AWAITING_PATIENT = "awaiting_patient"
 

#  Display if the patient accepts, rejects or acknowledge and pending decision
class PatientDecision(_Choice):

    # Data manager: stored record in the patient's case history record

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ACKNOWLEDGED = "acknowledged"
 
 


# Users. Used by IO Manager
# ---------------------------------------------------------------------------

#  Check which user type is logged in currently
class Role(_Choice):

    # IO manager: login prompt and menus

    PATIENT = "patient"
    NURSE = "nurse"
    DOCTOR = "doctor"