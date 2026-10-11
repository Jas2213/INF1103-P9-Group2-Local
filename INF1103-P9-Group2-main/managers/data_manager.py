"""
{
    "patient_id": "S1234567A",
    "history": [
        {
            "timestamp": "2026-09-24T14:32:00",
            "symptom_description": "...",
            "ai_result": { ... },   # AI Manager output, see team schema
            "outcome": { ... }      # Logic Manager output, see team schema
        },
        ...
    ]
}
"""

import json
import os
from datetime import datetime

DATA_DIR = os.path.join("data", "patients")


# ---------------------------------------------------------------------------
# Folder setup
# ---------------------------------------------------------------------------

def ensure_data_folder():
    """Create the data/patients folder if it doesn't already exist."""
    os.makedirs(DATA_DIR, exist_ok=True)


def _get_patient_filepath(patient_id):
    """Return the JSON file path for a given patient id (NRIC)."""
    safe_id = str(patient_id).strip().upper()
    return os.path.join(DATA_DIR, f"{safe_id}.json")


def _write_json_atomic(filepath, data):
    """
    Write data as JSON to a temp file, then replace the target - avoids
    leaving a half-written/corrupt file if the process dies mid-write.
    Returns True on success, False on failure (never raises).
    """
    try:
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, filepath)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Core load / save
# ---------------------------------------------------------------------------

def load_patient(patient_id):
    """
    Load a patient's record by id.

    Returns:
        dict  -> the patient record if found and valid
        None  -> if the file does not exist, OR if the file is corrupt
                 (never raises — corrupt/missing data must not crash the app)
    """
    ensure_data_folder()
    filepath = _get_patient_filepath(patient_id)

    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        # Corrupt file: quarantine it so it doesn't repeatedly fail,
        # then treat the patient as having no existing record.
        _quarantine_corrupt_file(filepath)
        return None

    if not _is_valid_patient_shape(data):
        _quarantine_corrupt_file(filepath)
        return None

    return data


def save_patient(patient_id, data):
    """
    Write/overwrite a patient's full record to disk.

    Args:
        patient_id: the patient's id (used for the filename)
        data: the full patient dict, e.g. {"patient_id": ..., "history": [...]}

    Returns:
        True on success, False on failure (never raises).
    """
    ensure_data_folder()
    filepath = _get_patient_filepath(patient_id)

    return _write_json_atomic(filepath, data)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _is_valid_patient_shape(data):
    """Minimal structural check so a malformed-but-parseable JSON file
    (e.g. a list, or a dict missing 'history') doesn't get used downstream."""
    return isinstance(data, dict) and isinstance(data.get("history"), list)


def _quarantine_corrupt_file(filepath):
    """Rename a corrupt file out of the way instead of silently deleting
    it, so it can be inspected later, and so load_patient can safely
    return None for that patient going forward."""
    try:
        if os.path.exists(filepath):
            os.replace(filepath, filepath + ".corrupt")
    except OSError:
        pass  # if even this fails, we still don't crash the app

# ---------------------------------------------------------------------------
# Pending case (a request that is still moving between patient and staff)
#
# Stored under a top-level "pending_case" key in the patient's JSON file:
#   {"patient_id": ..., "history": [...], "pending_case": {...} or absent}
# When the request is finished it is moved into "history" (append-only) and
# "pending_case" is removed.
# ---------------------------------------------------------------------------

def save_pending_case(patient_id, case):
    """Attach/overwrite the patient's open request. Creates the file if needed.
    Returns True on success, False on failure."""
    patient = load_patient(patient_id)
    if patient is None:
        patient = {"patient_id": str(patient_id), "history": []}
    patient["pending_case"] = case
    return save_patient(patient_id, patient)


def load_pending_case(patient_id):
    """Return the patient's open request dict, or None if there isn't one."""
    patient = load_patient(patient_id)
    if patient is None:
        return None
    return patient.get("pending_case")


def list_pending_cases(stage=None):
    """
    List open requests across all patients.

    Args:
        stage: if given, only cases whose "stage" matches
               (e.g. "awaiting_staff", "awaiting_patient").

    Returns:
        list of [patient_id, case] pairs.
    """
    ensure_data_folder()
    found = []
    for filename in sorted(os.listdir(DATA_DIR)):
        if not filename.endswith(".json"):
            continue
        patient = load_patient(filename[: -len(".json")])
        if patient is None:
            continue
        case = patient.get("pending_case")
        if not case:
            continue
        if stage is not None and case.get("stage") != stage:
            continue
        found.append([patient.get("patient_id", filename[: -len(".json")]), case])
    return found


def close_pending_case(patient_id, final_record):
    """
    Move a finished request into the patient's history (append-only) and
    clear pending_case. final_record should include the patient's decision.
    Returns True on success, False on failure.
    """
    patient = load_patient(patient_id)
    if patient is None:
        return False
    final_record = dict(final_record)
    final_record.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))
    patient["history"].append(final_record)
    patient.pop("pending_case", None)
    return save_patient(patient_id, patient)


# ---------------------------------------------------------------------------
# Staff accounts (data/staff/<STAFF_ID>.json)
#
# {"staff_id": "STF0001", "name": "...", "role": "nurse", "created_at": "..."}
# ---------------------------------------------------------------------------

STAFF_DIR = os.path.join("data", "staff")


def ensure_staff_folder():
    """Create the data/staff folder if it doesn't already exist."""
    os.makedirs(STAFF_DIR, exist_ok=True)


def _get_staff_filepath(staff_id):
    return os.path.join(STAFF_DIR, f"{str(staff_id).strip().upper()}.json")


def load_staff(staff_id):
    """
    Load a staff record by id.
    Returns the record dict, or None if missing or corrupt (never raises).
    """
    ensure_staff_folder()
    filepath = _get_staff_filepath(staff_id)

    if not os.path.exists(filepath):
        return None

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _quarantine_corrupt_file(filepath)
        return None

    if not isinstance(data, dict) or "staff_id" not in data:
        _quarantine_corrupt_file(filepath)
        return None

    return data


def save_staff(staff_record):
    """Write a staff record to disk. Returns True on success, False on failure."""
    ensure_staff_folder()
    filepath = _get_staff_filepath(staff_record["staff_id"])

    return _write_json_atomic(filepath, staff_record)


def list_staff():
    """Return a list of all valid staff records, sorted by staff id."""
    ensure_staff_folder()
    records = []
    for filename in sorted(os.listdir(STAFF_DIR)):
        if not filename.endswith(".json"):
            continue
        record = load_staff(filename[: -len(".json")])
        if record is not None:
            records.append(record)
    return records


def next_staff_id():
    """Return the next unused staff id, e.g. "STF0001", "STF0002", ..."""
    ensure_staff_folder()
    highest = 0
    for filename in os.listdir(STAFF_DIR):
        name = filename[: -len(".json")] if filename.endswith(".json") else ""
        if name.startswith("STF") and name[3:].isdigit():
            highest = max(highest, int(name[3:]))
    return f"STF{highest + 1:04d}"

# ---------------------------------------------------------------------------
# Appointment slot calendar (data/appointment_slots.json)
#
# {"YYYY-MM-DD HH:MM": {"status": "available" | "booked", "patient_id": str|None}, ...}
# Seeded by generate_slots.py. Booking is check-and-set on the whole file,
# which is safe for this CLI's single-process use (no concurrent writers).
# ---------------------------------------------------------------------------

SLOTS_FILE = os.path.join("data", "appointment_slots.json")


def load_slots():
    """Load the full slot calendar. Returns {} if the file is missing or
    corrupt (never raises)."""
    ensure_data_folder()
    if not os.path.exists(SLOTS_FILE):
        return {}
    try:
        with open(SLOTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        _quarantine_corrupt_file(SLOTS_FILE)
        return {}
    if not isinstance(data, dict):
        _quarantine_corrupt_file(SLOTS_FILE)
        return {}
    return data


def save_slots(slots):
    """Write the full slot calendar to disk. True on success, False on failure."""
    ensure_data_folder()
    return _write_json_atomic(SLOTS_FILE, slots)


def _parse_slot_key(key):
    """Parse a slot key, or return None if it isn't a valid "YYYY-MM-DD HH:MM"."""
    try:
        return datetime.strptime(key, "%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return None


def is_slot_available(slot_key):
    """True if slot_key exists in the calendar and is currently available."""
    info = load_slots().get(slot_key)
    return info is not None and info.get("status") == "available"


def find_next_available_slot(earliest_dt):
    """Earliest available slot ("YYYY-MM-DD HH:MM") at or after
    earliest_dt, or None if nothing is available."""
    slots = load_slots()
    best_key, best_dt = None, None
    for key, info in slots.items():
        if not isinstance(info, dict) or info.get("status") != "available":
            continue
        slot_dt = _parse_slot_key(key)
        if slot_dt is None or slot_dt < earliest_dt:
            continue  # malformed key in a damaged calendar file: skip it, don't crash
        if best_dt is None or slot_dt < best_dt:
            best_key, best_dt = key, slot_dt
    return best_key


def book_slot(slot_key, patient_id):
    """
    Reserve a slot for exactly one patient. Only succeeds if the slot
    exists and is currently available - this is what guarantees no two
    patients ever share a date/time. Returns True if booked, False if
    the slot doesn't exist or is already taken (never raises).
    """
    slots = load_slots()
    info = slots.get(slot_key)
    if info is None or info.get("status") != "available":
        return False
    slots[slot_key] = {"status": "booked", "patient_id": str(patient_id)}
    return save_slots(slots)


def release_slot(slot_key):
    """Free a previously booked slot (patient rejected, or staff changed
    it). Returns True on success, False if the slot doesn't exist."""
    if not slot_key:
        return False
    slots = load_slots()
    if slot_key not in slots:
        return False
    slots[slot_key] = {"status": "available", "patient_id": None}
    return save_slots(slots)