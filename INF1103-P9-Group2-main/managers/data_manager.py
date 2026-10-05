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

    try:
        # Write to a temp file first, then replace — avoids leaving a
        # half-written/corrupt JSON file if the process dies mid-write.
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, filepath)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# History append
# ---------------------------------------------------------------------------

def append_history_record(patient_id, record):
    """
    Add a new record to a patient's history rather than overwriting it.
    Creates the patient file if it doesn't already exist.

    Args:
        patient_id: the patient's id
        record: dict, e.g. {"symptom_description": ..., "ai_result": ...,
                             "outcome": ...}. A "timestamp" key is added
                             automatically if not already present.

    Returns:
        True on success, False on failure.
    """
    existing = load_patient(patient_id)

    if existing is None:
        existing = {"patient_id": str(patient_id), "history": []}

    record = dict(record)  # don't mutate caller's dict
    record.setdefault("timestamp", datetime.now().isoformat(timespec="seconds"))

    existing["history"].append(record)
    return save_patient(patient_id, existing)


# ---------------------------------------------------------------------------
# Query / filter
# ---------------------------------------------------------------------------

def find_patients_by_condition(urgency=None, severity=None, specialist=None):
    """
    Filter/query function across all stored patients (spec requirement:
    "at least one filter or query function").

    Looks at each patient's MOST RECENT history entry and matches against
    any of the given filters (all provided filters must match — AND logic).
    Any filter left as None is ignored.

    Args:
        urgency: e.g. "urgent" / "non-urgent" (matches ai_result.urgency)
        severity: e.g. "low" / "medium" / "high" (matches ai_result.severity)
        specialist: e.g. "cardiology" (matches outcome.specialist)

    Returns:
        list of patient record dicts (full records, not just the id) whose
        latest history entry matches all given filters.
    """
    ensure_data_folder()
    matches = []

    for filename in os.listdir(DATA_DIR):
        if not filename.endswith(".json"):
            continue
        patient_id = filename[: -len(".json")]
        patient = load_patient(patient_id)

        if patient is None or not patient.get("history"):
            continue

        latest = patient["history"][-1]
        ai_result = latest.get("ai_result") or {}
        outcome = latest.get("outcome") or {}

        if urgency is not None and ai_result.get("urgency") != urgency:
            continue
        if severity is not None and ai_result.get("severity") != severity:
            continue
        if specialist is not None and outcome.get("specialist") != specialist:
            continue

        matches.append(patient)

    return matches


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

    try:
        tmp_path = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(staff_record, f, indent=2)
        os.replace(tmp_path, filepath)
        return True
    except OSError:
        return False


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