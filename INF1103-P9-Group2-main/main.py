"""
main.py

Entry point and orchestrator for the patient triage CLI. This is the ONLY
module that talks to more than one manager. Pipeline (one direction only):

    io_manager -> ai_manager -> logic_manager -> data_manager

Managers never import each other; every hand-off goes through here.
No print() or input() in this file: all user-facing I/O goes through
io_manager.

The first prompt asks for an NRIC or a Staff ID. An NRIC logs in as a
patient (only patient options are shown); a Staff ID logs in as hospital
staff (only the staff option is shown, and the ID must exist in
data/staff, created with staff.py).

The system is split into three separate runs. A request travels between
them through a "pending_case" saved in the patient's JSON file (data_manager):

    Patient part 1  ->  stage "awaiting_staff"
    Hospital staff  ->  stage "awaiting_patient"
    Patient part 2  ->  closed into the patient's history

If the AI is down, symptoms are saved for staff manual review. Staff can
record a manual decision without making another AI call.

Patient part 1
    1. io_manager   : NRIC + new/existing issue
    2. data_manager : load history if a record exists
    3. io_manager   : collect symptoms -> ai_manager (with history)
    4. io_manager   : show AI summary, patient confirms (no -> back to 3)
    5. data_manager : save pending case; part 1 ends

Hospital staff
    1. data_manager : list requests awaiting staff
       logic_manager: department / urgency / severity / proposed slot
    2. io_manager   : accept, or override
    3. io_manager   : pick the field to override + type the verdict
    4. ai_manager   : regenerate the result using the staff verdict(s)
    5. io_manager   : staff confirm (no -> back to 3); then request goes
                      back to the patient (stage "awaiting_patient")

Patient part 2
    1. io_manager   : patient sees the proposed slot, accepts or rejects
    2. accept -> logic_manager books the slot; reject -> warning + re-confirm,
       and if still rejected the request is dropped. Either way the decision
       is saved to the patient's history (data_manager).
"""

from datetime import datetime

import managers.ai_manager as ai_manager
import managers.data_manager as data_manager
import managers.io_manager as io_manager
import managers.logic_manager as logic_manager

STAGE_AWAITING_STAFF = "awaiting_staff"
STAGE_AWAITING_PATIENT = "awaiting_patient"


# ===========================================================================
# Patient part 1
# ===========================================================================

def collect_symptoms_and_triage(existing_record):
    """
    Collect symptoms, run AI triage, and have the patient confirm the
    summary. If they say it's inaccurate, re-collect the description.

    On AI failure, retain the original symptoms for manual review.

    Returns:
        (symptom_description, ai_result), including error results.
    """
    while True:
        symptom_description = io_manager.prompt_symptom_description()

        io_manager.display_message("\nAnalysing your symptoms, please wait...")
        try:
            ai_result = ai_manager.get_ai_triage_result(symptom_description, existing_record)
        except Exception:
            # Client setup failures can escape the AI manager. Avoid logging secrets.
            ai_result = {"status": "error", "error_reason": "AI triage could not be completed."}
        if not isinstance(ai_result, dict):
            ai_result = {"status": "error", "error_reason": "AI returned an unusable response."}

        if ai_result.get("status") != "ok":
            io_manager.display_ai_down_patient()
            return symptom_description, ai_result

        if io_manager.display_ai_summary_and_confirm(ai_result):
            return symptom_description, ai_result

        io_manager.display_message("No problem, let's describe your symptoms again.")


def run_patient_submission(nric):
    issue_type = io_manager.prompt_issue_type()

    existing_record = data_manager.load_patient(nric)
    io_manager.display_patient_greeting(existing_record)

    if data_manager.load_pending_case(nric) is not None:
        io_manager.display_message(
            "You already have an open request. Please wait for it to be "
            "reviewed, or use 'respond' to view a proposal."
        )
        return

    symptom_description, ai_result = collect_symptoms_and_triage(existing_record)
    needs_manual_review = ai_result.get("status") != "ok"

    case = {
        "stage": STAGE_AWAITING_STAFF,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "symptom_description": symptom_description,
        "issue_type": issue_type,
        "ai_result": ai_result,
        "outcome": logic_manager.evaluate_triage_logic(ai_result, existing_record)
        if needs_manual_review else None,
        "staff_overrides": [],
    }

    if data_manager.save_pending_case(nric, case):
        if needs_manual_review:
            io_manager.display_message(
                "\nYour symptom report has been saved for manual review by hospital staff. "
                "Please check back later for their response."
            )
            return
        io_manager.display_message(
            "\nYour request has been sent to our hospital staff. "
            "Please check back later to view the proposed appointment."
        )
    else:
        io_manager.display_error("We couldn't save your request. Please try again.")


# ===========================================================================
# Hospital staff
# ===========================================================================

def run_manual_staff_review(nric, case, history, staff_record):
    """Review without AI; deferred cases stay queued."""
    io_manager.display_manual_review_case(case, history)
    while True:
        decision = io_manager.prompt_manual_review_decision()
        if decision == "manual_review":
            io_manager.display_message("Request remains in the staff review queue.")
            return
        reason = io_manager.prompt_nonempty_text("Enter your review notes / reason: ")
        specialist = None
        slot = None
        if decision in ("route_to_specialist", "schedule_non_urgent"):
            specialist = io_manager.prompt_override_verdict("department")
            while True:
                slot = io_manager.prompt_nonempty_text("Appointment date/time (YYYY-MM-DD HH:MM): ")
                try:
                    slot = logic_manager.validate_manual_slot(slot)
                    break
                except ValueError as exc:
                    io_manager.display_error(str(exc))
        outcome = logic_manager.build_manual_review_outcome(decision, reason, specialist, slot)
        io_manager.display_staff_case(case.get("ai_result"), outcome)
        if io_manager.prompt_staff_confirm_correct():
            break
    reviewed = dict(case)
    reviewed.update({
        "outcome": outcome,
        "review_source": "staff_manual",
        "reviewed_by": staff_record["staff_id"],
        "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        "stage": STAGE_AWAITING_PATIENT,
    })
    if data_manager.save_pending_case(nric, reviewed):
        io_manager.display_message("Manual review saved. The response is available to the patient.")
    else:
        io_manager.display_error("Couldn't save the manual review. The request is still awaiting staff.")


def run_staff_review(staff_record):
    pending = data_manager.list_pending_cases(STAGE_AWAITING_STAFF)
    if not pending:
        io_manager.display_message("\nNo requests are waiting for review.")
        return

    # Step 1: pick a request; logic_manager works out the proposal
    nric, case = io_manager.prompt_select_case(pending)
    history = data_manager.load_patient(nric)

    original_ai = case.get("ai_result")
    if not isinstance(original_ai, dict) or original_ai.get("status") != "ok":
        run_manual_staff_review(nric, case, history, staff_record)
        return
    ai_result = original_ai
    outcome = logic_manager.evaluate_triage_logic(ai_result, history)
    staff_overrides = []  # list of [field, verdict]

    io_manager.display_staff_case(ai_result, outcome)

    # Step 2: accept or override
    if not io_manager.prompt_staff_accept():
        while True:
            # Step 3: which field + verdict
            field = io_manager.prompt_override_field()
            options = logic_manager.STAFF_OVERRIDE_OPTIONS.get(field)
            verdict = io_manager.prompt_override_verdict(field, options)
            try:
                verdict = logic_manager.validate_staff_override(field, verdict)
            except ValueError as exc:
                io_manager.display_error(str(exc))
                continue
            staff_overrides.append([field, verdict])

            # Step 4: AI incorporates ALL verdicts so far, from the original result
            new_ai = ai_manager.get_ai_override_result(
                case["symptom_description"], original_ai, staff_overrides
            )
            if new_ai.get("status") != "ok":
                # AI down: stop here. The request stays in the queue untouched.
                io_manager.display_ai_down_staff()
                return

            ai_result = logic_manager.apply_staff_risk_values(new_ai, staff_overrides)
            outcome = logic_manager.evaluate_triage_logic(ai_result, history, staff_overrides)
            outcome = logic_manager.apply_staff_override(outcome, ai_result, staff_overrides)
            io_manager.display_staff_case(ai_result, outcome)

            # Step 5: staff confirm, otherwise back to step 3
            if io_manager.prompt_staff_confirm_correct():
                break

    # Send back to the patient
    case["ai_result"] = ai_result
    case["outcome"] = outcome
    case["staff_overrides"] = staff_overrides
    if staff_overrides:
        case["original_ai_result"] = original_ai
    case["reviewed_by"] = staff_record["staff_id"]
    case["stage"] = STAGE_AWAITING_PATIENT

    if data_manager.save_pending_case(nric, case):
        io_manager.display_message(f"\nReview complete. The proposal has been sent back to patient {nric}.")
    else:
        io_manager.display_error("Couldn't save the reviewed request. Please try again.")


# ===========================================================================
# Patient part 2
# ===========================================================================

def run_patient_response(nric):
    case = data_manager.load_pending_case(nric)

    if case is None:
        io_manager.display_message("We have no open request for you.")
        return
    if case.get("stage") != STAGE_AWAITING_PATIENT:
        io_manager.display_message(
            "Your request is still being reviewed by our staff. Please check back later."
        )
        return

    outcome = case["outcome"]
    io_manager.display_final_outcome(outcome)
    slot = outcome.get("proposed_slot")

    if not slot:
        # Nothing to accept (e.g. manual review): just record that it was seen.
        decision, dropped = "acknowledged", False
    else:
        while True:
            if io_manager.prompt_patient_accept_proposal():
                result = logic_manager.schedule_appointment(outcome, slot)
                if not result.get("success"):
                    io_manager.display_error(result.get("error", "Could not schedule appointment."))
                    return
                outcome = result["outcome"]
                decision, dropped = "accepted", False
                io_manager.display_message(f"\nYour appointment is confirmed for {slot}.")
                break

            if io_manager.prompt_patient_confirm_reject():
                decision, dropped = "rejected", True
                io_manager.display_message(
                    "\nYour request has been dropped. You are welcome to submit a new one."
                )
                break
            # Patient changed their mind about rejecting: ask again.

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "symptom_description": case["symptom_description"],
        "issue_type": case["issue_type"],
        "ai_result": case["ai_result"],
        "outcome": outcome,
        "staff_overrides": case.get("staff_overrides", []),
        "reviewed_by": case.get("reviewed_by"),
        "review_source": case.get("review_source", "ai_assisted"),
        "reviewed_at": case.get("reviewed_at"),
        "patient_decision": decision,
        "request_dropped": dropped,
    }
    if not data_manager.close_pending_case(nric, record):
        io_manager.display_error("We couldn't save your response. Please inform a staff member.")


# ===========================================================================
# Sessions and entry point
# ===========================================================================

def run_safely(action, *args):
    """Run one action; a failure in it must not crash the whole app."""
    try:
        action(*args)
    except (KeyboardInterrupt, EOFError):
        io_manager.display_message("\nCancelled.")
    except Exception as exc:  # noqa: BLE001
        io_manager.display_error(f"Unexpected error: {exc}")


def run_patient_session(nric):
    """Patient-only options, until the patient logs out."""
    while True:
        choice = io_manager.prompt_patient_menu()
        if choice == "submit":
            run_safely(run_patient_submission, nric)
        elif choice == "respond":
            run_safely(run_patient_response, nric)
        else:
            return


def run_staff_session(staff_record):
    """Hospital-staff-only options, until the staff member logs out."""
    while True:
        choice = io_manager.prompt_staff_menu()
        if choice == "review":
            run_safely(run_staff_review, staff_record)
        else:
            return


def main():
    data_manager.ensure_data_folder()
    data_manager.ensure_staff_folder()
    io_manager.display_message("=== Patient Triage System ===")

    while True:
        role, identifier = io_manager.prompt_login_id()

        if role == "exit":
            break

        if role == "patient":
            run_patient_session(identifier)
        else:
            staff_record = data_manager.load_staff(identifier)
            if staff_record is None:
                io_manager.display_error(
                    "Staff ID not found. Please ask an administrator to create your account."
                )
                continue
            io_manager.display_staff_greeting(staff_record)
            run_staff_session(staff_record)

    io_manager.display_message("Goodbye.")


if __name__ == "__main__":
    main()
