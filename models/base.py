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

# Serializable (interface)
#     data_manager: the ONLY caller of to_dict() / from_dict(). Define an abstract
#       Repository in data_manager that saves and loads any Serializable, with
#       concrete JSON repositories for Patient, StaffMember and appointment slots.
#       A new storage type is a new repository class, not an edit to old ones
#       (Open/Closed). The slot class defined in data_manager must also implement
#       Serializable.
#     No other manager calls to_dict() / from_dict().
# User (abstract class)
#     io_manager : replace prompt_patient_menu and prompt_staff_menu with ONE
#       function that takes the logged-in user and shows user.menu_options(), with
#       no branching on role strings (polymorphism evidence for the report).
#     data_manager: build the file name from user.user_id; load_patient and
#       load_staff return User objects.
#     Never add Patient- or StaffMember-specific code in code that receives a
#       User (Liskov Substitution).
#     main.py : one session loop; it passes the user object it got from
#       data_manager to io_manager and does not look inside it.

from abc import ABC, abstractmethod
 
from models.constants import Role
from models.errors import ValidationError
 

#  Anything that uses Serializable() can turn itself into a dict for saving (to_dict) and rebuild itself from a dict.
class Serializable(ABC):
 
    # Data manager: Uses it to call to_dict() to save and from_dict() to load data
    # Implemented by: every class in entities.py and users.py.


    @abstractmethod
    def to_dict(self):
        """Return this object as a plain dict that json can write to disk."""
 
    @classmethod
    @abstractmethod
    def from_dict(cls, data):
        """Build an object of this class back from a dict read from disk."""


# User is the shared parent for everyone who logs in. It is abstract, so you can never create a plain User, only a patient or a staff member
class User(ABC):
 
    # Io manager: asks the user object for its menu instead of branching on a role string
    # Data manager: builds the JSON file name from the id 
    # Extended by: Patient and StaffMember in users.py

 
    def __init__(self, user_id):
        cleaned = str(user_id).strip().upper()
        if not cleaned:
            raise ValidationError("A user id cannot be empty.")
        self.__user_id = cleaned
 
    @property
    def user_id(self):
        # cleaned id (NRIC for patients, staff id for staff). Read-only.
        return self.__user_id
 
    @property
    @abstractmethod
    def role(self):
        ''' What Role the user has e.g., patient, nurse or doctor'''
 
    @abstractmethod
    def menu_options(self):
        ''' Returns the list of menu choices the user is allowed to see.'''
 
    def __repr__(self):
        return f"{type(self).__name__}({self.__user_id!r})"