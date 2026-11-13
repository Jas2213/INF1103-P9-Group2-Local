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

# CLASSES EACH MANAGER MUST CREATE IN PHASE 2 (the OOP evidence)
#  ai_manager
#     AIProvider (abstract) with GeminiProvider and MockAIProvider. The manager
#       depends on AIProvider only, so tests need no live API. The connection
#       TIMEOUT belongs inside GeminiProvider.
#     A class that builds the prompts (triage, follow-up, reassess, override),
#       and validation through AIResult.from_dict().
#  data_manager
#     Repository (abstract) with JSON implementations for patients, staff and
#       appointment slots; SlotStatus (enum) and AppointmentSlot (implements
#       Serializable); a slot calendar that HOLDS AppointmentSlots which can
#       exist on their own (aggregation).
#     generate_slots.py calls one data_manager function to build the calendar;
#       it writes no slot dicts itself.
#  logic_manager
#     RoutingRule (abstract) with one rule class per rule (red-flag escalation,
#       keyword specialist, high risk, non-urgent). The evaluator loops over
#       the rules, so a new rule needs no edit to existing code (Open/Closed).
#       Keep the "high risk AND history_linked AND specialist" rule exactly as
#       in Phase 1.
#  io_manager
#     Group the prompt and display functions into classes with one job each
#       (for example input prompts, patient display, staff display).
#  main.py / staff.py / generate_slots.py: after Phase 2 these contain no rules,
#     only calls to manager classes and functions.
#
# WHERE EACH OOP CONCEPT SHOULD BE VISIBLE (for the UML and the report)
#  Abstract class / interface : User, Serializable, AIProvider, Repository,
#                               RoutingRule, CaseRecord
#  Inheritance                : Patient / StaffMember -> User; PendingCase /
#                               HistoryRecord -> CaseRecord; errors -> TriageError
#  Polymorphism               : menu_options(), summary_line(), the rule loop,
#                               swapping Gemini / Mock providers
#  Composition                : Patient owns its cases; a case owns its Intake,
#                               AIResult and TriageOutcome
#  Aggregation                : the slot calendar and its slots
#  Association                : a case refers to its reviewer by staff id
#  Encapsulation              : protected attributes with read-only properties
#  SOLID                      : SRP (each manager one job), OCP (rules,
#                               repositories, errors), LSP (providers, users)

from models.constants import (
    Decision,
    Department,
    OverrideField,
    PatientDecision,
    Role,
    Severity,
    Stage,
    Status,
    Tier,
    Urgency,
)
from models.errors import (
    AIConnectionError,
    AIResponseError,
    DataCorruptError,
    TriageError,
    ValidationError,
)
from models.base import Serializable, User
from models.entities import (
    AIResult,
    CaseRecord,
    HistoryRecord,
    Intake,
    PendingCase,
    StaffOverride,
    TriageOutcome,
)
from models.users import Patient, StaffMember
 
__all__ = [
    # constants.py
    "Decision", "Department", "OverrideField", "PatientDecision", "Role",
    "Severity", "Stage", "Status", "Tier", "Urgency",
    # errors.py
    "AIConnectionError", "AIResponseError", "DataCorruptError",
    "TriageError", "ValidationError",
    # base.py
    "Serializable", "User",
    # entities.py
    "AIResult", "CaseRecord", "HistoryRecord", "Intake", "PendingCase",
    "StaffOverride", "TriageOutcome",
    # users.py
    "Patient", "StaffMember",
]