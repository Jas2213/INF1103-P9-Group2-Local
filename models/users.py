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

# Patient
#     data_manager : load_patient returns a Patient (or None if missing or corrupt);
#       save_patient takes a Patient; load_all_patients returns Patient objects.
#       Replace the dict logic in save_pending_case / load_pending_case /
#       close_pending_case with patient.with_pending_case(...) and
#       patient.close_pending_case(decision, outcome), then save the Patient.
#     ai_manager   : the prompt history comes from patient.recent_history(5).
#     logic_manager: get_upcoming_appointments takes a Patient (or None).
#     io_manager   : the greeting uses len(patient.history); the menu comes from
#                    patient.menu_options().
# StaffMember
#     data_manager : load_staff, save_staff, list_staff return and take
#       StaffMember objects. Add a function that takes a name and a role,
#       builds the StaffMember with next_staff_id(), saves it, and returns the
#       staff id. staff.py calls that function and nothing in models.
#     staff.py     : asks io_manager for the name and role (plain strings), then
#       calls data_manager. It never builds a dict or a StaffMember itself.
#     io_manager   : the greeting reads .name and .role; the staff menu comes
#       from staff.menu_options().
#     logic_manager: records the reviewer's staff_id on the case as reviewed_by.
# Both classes are used through the User type wherever a menu or an id is
# needed. No role-specific branching there.

from datetime import datetime                                    # [2]
 
from models.base import Serializable, User                       # [1]
from models.constants import Role                                # [1]
from models.entities import HistoryRecord, PendingCase           # [1]
from models.errors import DataCorruptError, ValidationError      # [1]
 
 

# Patient user
# ===========================================================================
 
#  Everything known about the patient from finished requests history, oldest first and even open request
class Patient(User, Serializable):

    # Data manager: loads and saves one file per patient
    # AI manager: recent_history() goes into the prompt
    # Logic manager: Reads history to find upcoming appointments
    # IO manager: Greeting shows the number of time visited and menu comes from menu_options()
    # Data manager: also calls with_pending_case and close_pending_case when a request is saved or closed
 
    def __init__(self, patient_id, history=None, pending_case=None):
        super().__init__(patient_id)
        self._history = list(history or [])
        self._pending_case = pending_case
 
    @property
    def role(self):
        return Role.PATIENT
 
    def menu_options(self):
        return ["submit", "respond", "appointments", "logout"]
 
    @property
    def history(self):
        """Finished requests, oldest first (a copy; the patient is not changed)."""
        return list(self._history)
 
    @property
    def pending_case(self):
        """The open request, or None."""
        return self._pending_case
 
    def recent_history(self, count=5):
        """The last `count` finished requests (what the AI prompt uses)."""
        return self._history[-count:] if count > 0 else []
 
    def with_pending_case(self, case):
        """Return a copy holding this open request (replacing any earlier one)."""
        return Patient(self.user_id, self._history, case)
 
    def close_pending_case(self, patient_decision, outcome=None):
        """
        The patient has responded to the proposal: return a copy where the open
        request is closed into history with the decision and no request is open.
        Raises ValidationError if there is no open request.
        """
        if self._pending_case is None:
            raise ValidationError("This patient has no open request.")
        record = self._pending_case.close(patient_decision, outcome)
        return Patient(self.user_id, self._history + [record], None)
 
    def to_dict(self):
        data = {
            "patient_id": self.user_id,
            "history": [record.to_dict() for record in self._history],
        }
        if self._pending_case is not None:
            data["pending_case"] = self._pending_case.to_dict()
        return data
 
    @classmethod
    def from_dict(cls, data):
        """
        Rebuild a patient from a saved file's contents. A file that is not a dict,
        or has no history list, is treated as corrupt (same rule as Phase 1).
        """
        if not isinstance(data, dict) or not isinstance(data.get("history"), list):
            raise DataCorruptError("Patient file needs an id and a history list.")
        patient_id = data.get("patient_id")
        if not isinstance(patient_id, str) or not patient_id.strip():
            raise DataCorruptError("Patient file has no patient id.")
        pending = data.get("pending_case")
        return cls(
            patient_id,
            history=[HistoryRecord.from_dict(entry) for entry in data["history"]],
            pending_case=PendingCase.from_dict(pending) if pending else None,
        )



#    StaffMember
# ===========================================================================
 
#  It is a hospital staff account, also inheriting from User and Serializable
class StaffMember(User, Serializable):
   
    # Data manager: Createing, saveing and loading staff files
    # IO manager: Greeting, staff menu and role prompt
    # Logic manager: Records the reviewer's staff_id on the case as reviewed_by
    
 
    _STAFF_ROLES = (Role.NURSE, Role.DOCTOR)
 
    def __init__(self, staff_id, name, role, created_at=None):
        super().__init__(staff_id)
        if not isinstance(name, str) or not name.strip():
            raise ValidationError("A staff member needs a name.")
        try:
            staff_role = Role(role)
        except ValueError:
            staff_role = None
        if staff_role not in self._STAFF_ROLES:
            raise ValidationError("A staff role must be one of: nurse, doctor.")
        self._name = name.strip()
        self._role = staff_role
        self._created_at = created_at or datetime.now().isoformat(timespec="seconds")
 
    @property
    def role(self):
        return self._role
 
    def menu_options(self):
        return ["review", "search", "logout"]
 
    @property
    def staff_id(self):
        return self.user_id
 
    @property
    def name(self):
        return self._name
 
    @property
    def created_at(self):
        return self._created_at
 
    def to_dict(self):
        return {
            "staff_id": self.user_id,
            "name": self._name,
            "role": self._role.value,
            "created_at": self._created_at,
        }
 
    @classmethod
    def from_dict(cls, data):
        """Rebuild a staff member from a saved file. A file with no staff_id is corrupt."""
        if not isinstance(data, dict) or "staff_id" not in data:
            raise DataCorruptError("Staff file has no staff_id.")
        try:
            return cls(data["staff_id"], data.get("name"), data.get("role"), data.get("created_at"))
        except ValidationError as exc:
            raise DataCorruptError(f"Staff file is unusable: {exc}") from None