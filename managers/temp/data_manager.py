"""
Data Manager for the patient triage application.

Handles persistent JSON storage for patient records, staff accounts,
pending cases, and appointment slots.

"""

import json
import os
from datetime import datetime

from managers.temp.base_manager import BaseManager


class DataManager(BaseManager):
    """Manage the application's persistent JSON data."""

    
    def __init__(self, base_dir="data"):
        super().__init__()
        self._data_dir = os.path.join(base_dir, "patients")
        self._staff_dir = os.path.join(base_dir, "staff")
        self._slots_file = os.path.join(base_dir, "appointment_slots.json")

    def get_manager_name(self):
        """Return the name used by the shared BaseManager logger."""
        return "Data Manager"

    # -----------------------------------------
    # Folder setup and internal helper methods
    # -----------------------------------------

    def ensure_data_folder(self):
        """Create the data/patients folder if it doesn't already exist."""
        os.makedirs(self._data_dir, exist_ok=True)

    def _get_patient_filepath(self, patient_id):
        """Return the JSON file path for a given patient id (NRIC)."""
        safe_id = str(patient_id).strip().upper()
        return os.path.join(self._data_dir, f"{safe_id}.json")

    def _write_json_atomic(self, filepath, data):
        """
        Write JSON to a temporary file before replacing the target file.
        Returns True on success and False on failure.
        """
        try:
            tmp_path = filepath + ".tmp"

            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=2)

            os.replace(tmp_path, filepath)
            return True

        except OSError as error:
            self.log_error(f"Could not write JSON file: {error}")
            return False

    def _is_valid_patient_shape(self, data):
        """Check that a patient record has the minimum required structure."""
        return isinstance(data, dict) and isinstance(data.get("history"), list)

    def _quarantine_corrupt_file(self, filepath):
        """Move a corrupt file aside instead of deleting it."""
        try:
            if os.path.exists(filepath):
                os.replace(filepath, filepath + ".corrupt")
                self.log_warning(f"Moved corrupt data file: {filepath}")

        except OSError as error:
            self.log_error(f"Could not quarantine corrupt file: {error}")

    # -----------------------------------------
    # Patient record management
    # -----------------------------------------

    def load_patient(self, patient_id):
        """
        Load a patient's record by id.

        Returns the patient record if valid, or None if missing or corrupt.
        """
        self.ensure_data_folder()
        filepath = self._get_patient_filepath(patient_id)

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as file:
                data = json.load(file)

        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            self._quarantine_corrupt_file(filepath)
            return None

        if not self._is_valid_patient_shape(data):
            self._quarantine_corrupt_file(filepath)
            return None

        return data

    def save_patient(self, patient_id, data):
        """
        Save or overwrite a patient's full record.
        Returns True on success and False on failure.
        """
        self.ensure_data_folder()
        filepath = self._get_patient_filepath(patient_id)

        return self._write_json_atomic(filepath, data)

    # -----------------------------------------
    # Pending case management
    # -----------------------------------------

    def save_pending_case(self, patient_id, case):
        """Save or update a patient's pending case."""
        patient = self.load_patient(patient_id)

        if patient is None:
            patient = {
                "patient_id": str(patient_id),
                "history": []
            }

        patient["pending_case"] = case

        return self.save_patient(patient_id, patient)

    def load_pending_case(self, patient_id):
        """Load a patient's pending case."""
        patient = self.load_patient(patient_id)

        if patient is None:
            return None

        return patient.get("pending_case")

    def load_all_patients(self):
        """Load all valid patient records."""
        self.ensure_data_folder()

        patients = []

        for filename in sorted(os.listdir(self._data_dir)):
            if not filename.endswith(".json"):
                continue

            patient_id = filename[:-5]
            patient = self.load_patient(patient_id)

            if patient is not None:
                patients.append(patient)

        return patients

    def list_pending_cases(self, stage=None):
        """List all pending cases, optionally filtered by stage."""
        self.ensure_data_folder()

        found = []

        for filename in sorted(os.listdir(self._data_dir)):
            if not filename.endswith(".json"):
                continue

            patient_id = filename[:-5]
            patient = self.load_patient(patient_id)

            if patient is None:
                continue

            case = patient.get("pending_case")

            if not isinstance(case, dict) or not case:
                continue

            if stage is not None and case.get("stage") != stage:
                continue

            found.append([
                patient.get("patient_id", patient_id),
                case
            ])

        return found

    def close_pending_case(self, patient_id, final_record):
        """Move a completed case into the patient's history."""
        patient = self.load_patient(patient_id)

        if patient is None:
            return False

        final_record = dict(final_record)

        final_record.setdefault(
            "timestamp",
            datetime.now().isoformat(timespec="seconds")
        )

        patient["history"].append(final_record)
        patient.pop("pending_case", None)

        return self.save_patient(patient_id, patient)

    # -----------------------------------------
    # Staff account management
    # -----------------------------------------

    def ensure_staff_folder(self):
        """Create the data/staff folder if it doesn't exist."""
        os.makedirs(self._staff_dir, exist_ok=True)

    def _get_staff_filepath(self, staff_id):
        """Return the JSON file path for a given staff ID."""
        safe_id = str(staff_id).strip().upper()
        return os.path.join(self._staff_dir, f"{safe_id}.json")

    def load_staff(self, staff_id):
        """Load a staff record using the staff ID."""
        self.ensure_staff_folder()
        filepath = self._get_staff_filepath(staff_id)

        if not os.path.exists(filepath):
            return None

        try:
            with open(filepath, "r", encoding="utf-8") as file:
                data = json.load(file)

        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            self._quarantine_corrupt_file(filepath)
            return None

        if not isinstance(data, dict) or "staff_id" not in data:
            self._quarantine_corrupt_file(filepath)
            return None

        return data

    def save_staff(self, staff_record):
        """Save or update a staff record"""
        self.ensure_staff_folder()

        if not isinstance(staff_record, dict) or not staff_record.get("staff_id"):
            return False

        staff_record = dict(staff_record)

        staff_record.setdefault(
            "created_at",
            datetime.now().isoformat(timespec="seconds")
        )

        filepath = self._get_staff_filepath(staff_record["staff_id"])

        return self._write_json_atomic(filepath, staff_record)

    def list_staff(self):
        """Load and return all valid staff records."""
        self.ensure_staff_folder()

        staff_records = []

        for filename in sorted(os.listdir(self._staff_dir)):
            if not filename.endswith(".json"):
                continue

            staff_id = filename[:-5]
            staff = self.load_staff(staff_id)

            if staff is not None:
                staff_records.append(staff)

        return staff_records

    def next_staff_id(self):
        """Generate the next available staff ID."""
        self.ensure_staff_folder()

        highest = 0

        for filename in os.listdir(self._staff_dir):
            if not filename.endswith(".json"):
                continue

            staff_id = filename[:-5]

            if staff_id.startswith("STF") and staff_id[3:].isdigit():
                number = int(staff_id[3:])
                highest = max(highest, number)

        return f"STF{highest + 1:04d}"

    # -----------------------------------------
    # Appointment slot management
    # -----------------------------------------

    def load_slots(self):
        """Load all appointment slots from the JSON file."""
        self.ensure_data_folder()

        if not os.path.exists(self._slots_file):
            return {}

        try:
            with open(self._slots_file, "r", encoding="utf-8") as file:
                data = json.load(file)

        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            self._quarantine_corrupt_file(self._slots_file)
            return {}

        if not isinstance(data, dict):
            self._quarantine_corrupt_file(self._slots_file)
            return {}

        return data

    def save_slots(self, slots):
        """Save appointment slots to the JSON file."""
        self.ensure_data_folder()

        if not isinstance(slots, dict):
            return False

        return self._write_json_atomic(self._slots_file, slots)

    def _parse_slot_key(self, key):
        """Convert a slot key into a datetime object."""
        try:
            return datetime.strptime(key, "%Y-%m-%d %H:%M")

        except (ValueError, TypeError):
            return None

    def is_slot_available(self, slot_key):
        """Check whether a particular appointment slot is available."""
        info = self.load_slots().get(slot_key)

        return isinstance(info, dict) and info.get("status") == "available"

    def find_next_available_slot(self, earliest_dt):
        """Find the earliest available slot from a given datetime."""
        slots = self.load_slots()
        best_key = None
        best_dt = None

        for key, info in slots.items():
            if not isinstance(info, dict):
                continue

            if info.get("status") != "available":
                continue

            slot_dt = self._parse_slot_key(key)

            if slot_dt is None or slot_dt < earliest_dt:
                continue

            if best_dt is None or slot_dt < best_dt:
                best_dt = slot_dt
                best_key = key

        return best_key

    def book_slot(self, slot_key, patient_id):
        """Book an available appointment slot for a patient."""
        slots = self.load_slots()
        info = slots.get(slot_key)

        if info is None or not isinstance(info, dict):
            return False

        if info.get("status") != "available":
            return False

        slots[slot_key] = {
            "status": "booked",
            "patient_id": str(patient_id)
        }

        return self.save_slots(slots)

    def release_slot(self, slot_key):
        """Make a previously booked appointment slot available again."""
        if not slot_key:
            return False

        slots = self.load_slots()

        if slot_key not in slots:
            return False

        slots[slot_key] = {
            "status": "available",
            "patient_id": None
        }

        return self.save_slots(slots)