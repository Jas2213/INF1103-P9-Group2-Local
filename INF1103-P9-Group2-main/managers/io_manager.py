import re

# Singapore NRIC/FIN format: leading letter (S/T for citizens/PRs,
# F/G/M for foreigners), 7 digits, trailing checksum letter.
# Format check only (not the real checksum algorithm) — enough to
# satisfy "reject and re-prompt on bad data."
_NRIC_PATTERN = re.compile(r"^[STFGM]\d{7}[A-Z]$", re.IGNORECASE)


def validate_nric_format(nric):
    """
    Check whether a string is a plausible NRIC/FIN, e.g. "S1234567A".
    Never raises, so it's safe to call directly on raw user input.
    """
    if not isinstance(nric, str):
        return False
    return bool(_NRIC_PATTERN.match(nric.strip()))


# Staff IDs look like "STF0001". Deliberately cannot match the NRIC pattern
# above, so one input box can tell patients and staff apart.
_STAFF_ID_PATTERN = re.compile(r"^STF\d{4}$", re.IGNORECASE)


def validate_staff_id_format(staff_id):
    """Check whether a string looks like a staff ID, e.g. "STF0001". Never raises."""
    if not isinstance(staff_id, str):
        return False
    return bool(_STAFF_ID_PATTERN.match(staff_id.strip()))


# ---------------------------------------------------------------------------
# Generic validated-input helpers (reused by the more specific prompts below)
# ---------------------------------------------------------------------------

def prompt_nonempty_text(prompt_text):
    """Keep asking until the user enters something that isn't blank/whitespace."""
    while True:
        value = input(prompt_text).strip()
        if value:
            return value
        print("This can't be empty — please try again.")


def prompt_yes_no(prompt_text):
    """
    Keep asking until the user answers yes or no. Accepts y/yes/n/no,
    case-insensitive. Returns True for yes, False for no.
    """
    while True:
        raw = input(f"{prompt_text} (yes/no): ").strip().lower()
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("Please answer 'yes' or 'no'.")


def prompt_choice(prompt_text, valid_options):
    """
    Keep asking until the user picks one of valid_options (case-insensitive
    match). Returns the matched option in its original casing from
    valid_options.
    """
    lowered_map = {opt.lower(): opt for opt in valid_options}
    options_display = "/".join(valid_options)
    while True:
        raw = input(f"{prompt_text} ({options_display}): ").strip().lower()
        if raw in lowered_map:
            return lowered_map[raw]
        print(f"Please enter one of: {options_display}")


# ---------------------------------------------------------------------------
# Patient identity (collect patient ID + new/existing patient)
# ---------------------------------------------------------------------------

def prompt_patient_nric():
    """
    Collect and validate the patient's NRIC. Rejects and re-prompts on
    bad format. Does NOT check whether the patient already exists —
    that requires a storage lookup, which is data_manager's job. main.py
    should call data_manager.load_patient(nric) with the value this
    returns, then pass the result to display_patient_greeting() below.

    Returns:
        The validated, uppercased NRIC string.
    """
    while True:
        raw = input("Please enter your NRIC (e.g. S1234567A): ").strip()

        if not raw:
            print("This can't be empty — please try again.")
            continue

        if not validate_nric_format(raw):
            print(f"'{raw}' doesn't look like a valid NRIC. Format is 1 letter + 7 digits + 1 letter.")
            continue

        return raw.upper()


def display_patient_greeting(existing_record):
    """
    Show the appropriate greeting based on whether a patient record
    already exists. Called by main.py after it has looked the patient up
    via data_manager — this function only displays, it doesn't look
    anything up itself.

    Args:
        existing_record: the patient dict from data_manager.load_patient(),
                          or None if the patient is new.
    """
    if existing_record is not None:
        visit_count = len(existing_record.get("history", []))
        print(f"Welcome back! We have {visit_count} previous visit(s) on file.")
    else:
        print("We don't have a record for you yet — you'll be registered as a new patient.")


# ---------------------------------------------------------------------------
# Symptom description (free text)
# ---------------------------------------------------------------------------

def prompt_symptom_description():
    """Collect a free-text symptom description. Rejects empty input."""
    return prompt_nonempty_text("Please describe your symptoms: ")


def prompt_issue_type():
    """
    Ask whether today's report is for a new problem or an existing/ongoing
    condition (per the DevOps proposal's process flow). This is a
    patient-stated flag, independent of whether a record already exists
    in storage.

    Returns:
        "new" or "existing".
    """
    return prompt_choice("Is this report for a", ["new", "existing"])


# ---------------------------------------------------------------------------
# Patient-side: show AI summary, get confirmation
# ---------------------------------------------------------------------------

def display_ai_summary_and_confirm(ai_result):
    """
    Show the AI's plain-English summary back to the patient and ask them
    to confirm it's accurate (yes/no). This gives the patient a chance to
    catch a misunderstanding before it's acted on.

    Args:
        ai_result: dict from ai_manager. If status isn't "ok" (API
                   failure), tells the patient rather than showing a
                   summary that doesn't exist.

    Returns:
        True if the patient confirmed the summary is accurate, False if not.
        Always True (no confirmation needed) if there was an API failure,
        since there's nothing for the patient to confirm.
    """
    if not isinstance(ai_result, dict) or ai_result.get("status") != "ok":
        display_ai_down_patient()
        return True

    summary = ai_result.get("summary", "(no summary available)")
    print(f"\nHere's what we understood from your symptoms:\n  \"{summary}\"")
    return prompt_yes_no("Does this accurately reflect your symptoms?")


# ---------------------------------------------------------------------------
# Nurse-side: show AI suggestion + outcome, accept or override
# ---------------------------------------------------------------------------

# Decision options a nurse can pick when overriding — kept as plain strings
# (not imported from logic_manager) so this module doesn't depend on that
# module's internals. These values must match the "decision" values
# logic_manager produces, per the team's agreed schema.
NURSE_DECISION_OPTIONS = [
    "route_to_specialist",
    "urgent_priority",
    "schedule_non_urgent",
    "manual_review",
]


def display_nurse_review(ai_result, outcome):
    """
    Show the AI's assessment and the system's proposed outcome to a
    nurse, then ask whether to accept it or override it.

    Args:
        ai_result: dict from ai_manager.
        outcome: dict from logic_manager (the proposed decision).

    Returns:
        If accepted: (False, None, None, None)
            i.e. (is_override, new_decision, reason, specialist)
        If overridden: (True, new_decision, reason, specialist)
            where new_decision is one of NURSE_DECISION_OPTIONS, reason is
            free text, and specialist is a string or None.
    """
    print("\n--- Nurse Review ---")
    if isinstance(ai_result, dict) and ai_result.get("status") == "ok":
        print(f"AI summary: {ai_result.get('summary', '(none)')}")
        print(f"AI urgency/severity: {ai_result.get('urgency')} / {ai_result.get('severity')}")
    else:
        print("AI summary: unavailable (API failure)")

    print(f"System-proposed decision: {outcome.get('decision')}")
    print(f"Next action: {outcome.get('next_action')}")

    accept = prompt_yes_no("Accept this decision?")
    if accept:
        return False, None, None, None

    new_decision = prompt_choice("Enter the overriding decision", NURSE_DECISION_OPTIONS)
    reason = prompt_nonempty_text("Reason for override: ")

    specialist = None
    if new_decision == "route_to_specialist":
        specialist = prompt_nonempty_text("Specialist to route to: ")

    return True, new_decision, reason, specialist


# ---------------------------------------------------------------------------
# Display final outcome to patient
# ---------------------------------------------------------------------------

def display_final_outcome(outcome):
    """Show the final decision to the patient in plain language."""
    print("\n--- Your Outcome ---")

    if outcome.get("nurse_override"):
        print("(This case was reviewed and confirmed by a nurse.)")

    print(f"Next step: {outcome.get('next_action', 'Please wait for further instructions.')}")

    specialist = outcome.get("specialist")
    if specialist:
        print(f"Specialist: {specialist}")

    proposed_slot = outcome.get("proposed_slot")
    if proposed_slot:
        print(f"Proposed appointment date: {proposed_slot}")

    if outcome.get("flag_for_manual_review"):
        print("Your case has been flagged for manual review by our staff.")


# ---------------------------------------------------------------------------
# Generic status/error display (so main.py never needs print() itself —
# all print() calls in the system must live in io_manager, per spec)
# ---------------------------------------------------------------------------

def display_message(message):
    """Print a plain informational message (session banners, etc.)."""
    print(message)


def display_error(message):
    """Print an error/warning message in a visually distinct way."""
    print(f"\n[!] {message}")

# ---------------------------------------------------------------------------
# Login and role-specific menus
# ---------------------------------------------------------------------------

PATIENT_MENU_OPTIONS = ["submit", "respond", "logout"]
STAFF_MENU_OPTIONS = ["review", "logout"]
ADMIN_MENU_OPTIONS = ["create", "list", "exit"]


def prompt_login_id():
    """
    Single entry prompt: the user types an NRIC or a Staff ID and the
    format decides which role they get. Re-prompts on anything else.

    Returns:
        ("patient", NRIC), ("staff", STAFF_ID), or ("exit", None).
    """
    while True:
        raw = input("\nEnter your NRIC or Staff ID (or 'exit' to quit): ").strip()
        if raw.lower() == "exit":
            return "exit", None
        if validate_nric_format(raw):
            return "patient", raw.upper()
        if validate_staff_id_format(raw):
            return "staff", raw.upper()
        print("That doesn't look like an NRIC (e.g. S1234567A) or a Staff ID (e.g. STF0001).")


def prompt_patient_menu():
    """Options a patient can see. Returns one of PATIENT_MENU_OPTIONS."""
    print("\n=== Patient Menu ===")
    print("  submit  - Submit a new problem")
    print("  respond - View and respond to a proposed appointment")
    print("  logout  - Log out")
    return prompt_choice("Select an option", PATIENT_MENU_OPTIONS)


def prompt_staff_menu():
    """Options hospital staff can see. Returns one of STAFF_MENU_OPTIONS."""
    print("\n=== Hospital Staff Menu ===")
    print("  review - Review waiting patient requests")
    print("  logout - Log out")
    return prompt_choice("Select an option", STAFF_MENU_OPTIONS)


def display_staff_greeting(staff_record):
    """Greet a logged-in staff member."""
    print(f"\nWelcome, {staff_record.get('name', 'staff member')} ({staff_record.get('role', 'staff')}).")


# ---------------------------------------------------------------------------
# Staff account administration (used by staff.py)
# ---------------------------------------------------------------------------

def prompt_admin_menu():
    """Options for the staff-account tool. Returns one of ADMIN_MENU_OPTIONS."""
    print("\n=== Staff Account Admin ===")
    print("  create - Create a new staff account")
    print("  list   - List existing staff accounts")
    print("  exit   - Quit")
    return prompt_choice("Select an option", ADMIN_MENU_OPTIONS)


def prompt_staff_name():
    return prompt_nonempty_text("Staff full name: ")


def prompt_staff_role():
    return prompt_choice("Role", ["nurse", "doctor"])


def display_staff_list(staff_records):
    """Show all staff accounts."""
    if not staff_records:
        print("\nNo staff accounts yet.")
        return
    print("\nStaff accounts:")
    for record in staff_records:
        print(f"  {record.get('staff_id')} | {record.get('name')} | {record.get('role')}")


# ---------------------------------------------------------------------------
# Hospital staff
# ---------------------------------------------------------------------------

# Fields staff may override. These map onto the AI/logic output:
#   department -> specialist, urgency -> urgency, severity -> severity,
#   proposed_slot -> outcome proposed_slot
OVERRIDE_FIELDS = ["department", "urgency", "severity", "proposed_slot"]


def prompt_select_case(pending_cases):
    """
    List waiting requests and let staff pick one.

    Args:
        pending_cases: list of [patient_id, case] pairs (non-empty).

    Returns:
        the chosen [patient_id, case] pair.
    """
    print("\nRequests awaiting staff review:")
    for i, (patient_id, case) in enumerate(pending_cases, start=1):
        result = case.get("ai_result")
        if isinstance(result, dict) and result.get("status") == "ok":
            summary = result.get("summary", "(AI summary unavailable)")
        else:
            summary = "[MANUAL REVIEW] " + case.get("symptom_description", "(no report)")
        print(f"  {i}. {patient_id} | submitted {case.get('submitted_at', '?')} | {summary}")

    total = len(pending_cases)
    while True:
        raw = input(f"Select a request (1-{total}): ").strip()
        if raw.isdigit() and 1 <= int(raw) <= total:
            return pending_cases[int(raw) - 1]
        print(f"Please enter a number between 1 and {total}.")

def display_manual_review_case(case, history):
    """Display the original patient report when AI is unavailable."""
    print("\n--- Manual Review: AI unavailable ---")
    print(f"Reported issue: {case.get('issue_type', 'not specified')}")
    print(
        f"Patient's symptoms: "
        f"{case.get('symptom_description', '(not provided)')}"
    )

    entries = (history or {}).get("history", [])
    print("Previous reports:")

    if not entries:
        print("  No previous reports on file.")

    for entry in entries:
        if isinstance(entry, dict):
            print(
                f"  {entry.get('timestamp', '?')}: "
                f"{entry.get('symptom_description', '(not provided)')}"
            )


def prompt_manual_review_decision():
    """Ask staff to choose an action or leave the case queued."""
    print("manual_review: leave this request queued for further review")
    print("urgent_priority: send emergency instructions, without an appointment")
    print("route_to_specialist: propose a specialist appointment")
    print("schedule_non_urgent: propose a routine appointment")

    return prompt_choice(
        "Select manual decision",
        (
            "manual_review",
            "urgent_priority",
            "route_to_specialist",
            "schedule_non_urgent",
        ),
    )

def display_staff_case(ai_result, outcome):
    """Show the AI suggestion plus the logic_manager proposal to staff."""
    print("\n--- Request for Staff Review ---")
    if isinstance(ai_result, dict) and ai_result.get("status") == "ok":
        print(f"AI summary: {ai_result.get('summary', '(none)')}")
        flagged = ai_result.get("flagged_symptoms") or []
        print(f"Flagged symptoms: {', '.join(flagged) if flagged else '(none)'}")
    else:
        print("AI summary: unavailable (API failure)")

    print(f"Department:    {outcome.get('specialist') or 'N/A'}")
    print(f"Urgency:       {(ai_result or {}).get('urgency', 'n/a')}")
    print(f"Severity:      {(ai_result or {}).get('severity', 'n/a')}")
    print(f"Proposed slot: {outcome.get('proposed_slot') or 'N/A'}")
    print(f"Decision:      {outcome.get('decision')}")
    print(f"Next action:   {outcome.get('next_action')}")
    if outcome.get("nurse_override"):
        print(f"(Staff override applied: {outcome.get('override_reason')})")


def prompt_staff_accept():
    """True if staff accept the proposed option as-is."""
    return prompt_yes_no("Accept the proposed option as is?")


def prompt_override_field():
    """Ask which field staff want to override. Returns one of OVERRIDE_FIELDS."""
    return prompt_choice("Which field do you want to override", OVERRIDE_FIELDS)


def prompt_override_verdict(field, valid_options=None):
    """Use fixed choices when main supplies them; otherwise collect text."""
    if valid_options is not None:
        return prompt_choice(f"Select {field}", valid_options)
    if field == "proposed_slot":
        print("(e.g. a date, a date range, or 'push to next month')")
    return prompt_nonempty_text(f"Enter your verdict for '{field}': ")


def prompt_staff_confirm_correct():
    """True if staff confirm the updated proposal is correct."""
    return prompt_yes_no("Is everything correct now?")


# ---------------------------------------------------------------------------
# Patient part 2
# ---------------------------------------------------------------------------

def prompt_patient_accept_proposal():
    """True if the patient accepts the proposed timeslot."""
    return prompt_yes_no("Do you accept this proposed appointment?")


def prompt_patient_confirm_reject():
    """Warn the patient, then ask again. True if they still want to reject."""
    print("\nWarning: if you reject, your request will be dropped and you may lose this proposed slot.")
    return prompt_yes_no("Are you sure you want to reject this proposal?")


# ---------------------------------------------------------------------------
# AI outage messages
# ---------------------------------------------------------------------------

def display_ai_down_patient():
    """Explain fallback before saving; do not claim submission succeeded yet."""
    print("\n[!] AI triage is unavailable. We will try to save your report for staff review.")
    print("    If this is an emergency, go to A&E immediately; do not wait for this review.")


def display_ai_down_staff():
    """Tell staff the system is down; they should not handle it manually."""
    print("\n[!] The AI triage system is currently down.")
    print("    Please contact IT support. No changes were made to this request.")
