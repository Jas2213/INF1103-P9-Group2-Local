"""
main.py

Entry point and orchestrator for the patient triage CLI. This is the ONLY
module that talks to more than one manager. Pipeline (one direction only):

    io_manager -> ai_manager -> logic_manager -> data_manager

Managers never import each other; every hand-off goes through here.
No direct terminal I/O in this file: all user-facing I/O goes through
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

If the AI is down, nothing is saved: the patient is told the system has
issues and to visit a GP (or A&E if urgent). There is no manual fallback.

Patient part 1
    1. ai_manager   : quick availability check; if the AI is down, stop here
                      before the patient types anything
    2. io_manager   : NRIC + new/existing issue
    3. data_manager : load history if a record exists
    4. io_manager   : collect symptoms -> ai_manager (with history)
    5. io_manager   : show AI summary, patient confirms (no -> back to 4)
    6. data_manager : save pending case; part 1 ends

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
    Collect symptoms, generate AI follow-up questions,
    reassess using the patient's answers, and confirm
    the final summary with the patient.
    """

    while True:
        description = io_manager.prompt_symptom_description()
        duration = io_manager.prompt_symptom_duration()
        self_rated_severity = io_manager.prompt_self_rated_severity()
        red_flags_present = io_manager.prompt_red_flags_present()

        intake = {
            "description": description,
            "duration": duration,
            "self_rated_severity": self_rated_severity,
            "red_flags_present": red_flags_present,
        }

        io_manager.display_message(
            "\nAnalysing your symptoms, please wait..."
        )

        # Step 1: Initial AI assessment
        try:
            ai_result = ai_manager.get_ai_triage_result(
                intake, existing_record
            )
        except Exception:
            ai_result = {
                "status": "error",
                "error_reason": "AI triage could not be completed.",
            }

        if not isinstance(ai_result, dict):
            ai_result = {
                "status": "error",
                "error_reason": "AI returned an unusable response.",
            }

        if ai_result.get("status") != "ok":
            io_manager.display_ai_down_patient()
            return intake, ai_result

        # Step 2: Only ask follow-up questions if the initial
        # information does not already indicate urgent escalation.
        follow_up_questions = []
        follow_up_answers = []

        is_urgent = (
            red_flags_present
            or ai_result.get("urgency") == "urgent"
            or ai_result.get("severity") == "high"
        )

        if not is_urgent:
            try:
                question_result = (
                    ai_manager.generate_follow_up_questions(
                        intake, ai_result
                    )
                )
            except Exception:
                question_result = {"status": "error", "questions": []}

            if (
                isinstance(question_result, dict)
                and question_result.get("status") == "ok"
            ):
                follow_up_questions = question_result.get(
                    "questions", []
                )

                if follow_up_questions:
                    follow_up_answers = (
                        io_manager.prompt_follow_up_answers(
                            follow_up_questions
                        )
                    )

                    # Step 3: Reassess using the new answers
                    try:
                        updated_result = (
                            ai_manager.reassess_ai_triage_result(
                                intake,
                                ai_result,
                                follow_up_questions,
                                follow_up_answers,
                                existing_record,
                            )
                        )
                    except Exception:
                        updated_result = {
                            "status": "error",
                            "error_reason": (
                                "AI reassessment could not be completed."
                            ),
                        }

                    if (
                        not isinstance(updated_result, dict)
                        or updated_result.get("status") != "ok"
                    ):
                        io_manager.display_ai_down_patient()
                        return intake, updated_result

                    ai_result = updated_result

        # Step 4: Keep the follow-up information with the intake.
        intake["follow_up_questions"] = follow_up_questions
        intake["follow_up_answers"] = follow_up_answers

        # Step 5: Ask the patient to confirm the final AI summary.
        if io_manager.display_ai_summary_and_confirm(ai_result):
            return intake, ai_result

        io_manager.display_message(
            "No problem, let's describe your symptoms again."
        )


def run_patient_submission(nric):
    if data_manager.load_pending_case(nric) is not None:
        io_manager.display_message(
            "\nYou already have an open request. Please wait for it to be "
            "reviewed, or use 'respond' to view a proposal."
        )
        return

    # Check the AI can respond BEFORE asking the patient anything.
    io_manager.display_message("\nChecking that the triage system is available...")
    if not ai_manager.is_ai_available():
        io_manager.display_ai_down_patient()
        return

    issue_type = io_manager.prompt_issue_type()

    existing_record = data_manager.load_patient(nric)
    io_manager.display_patient_greeting(existing_record)

    intake, ai_result = collect_symptoms_and_triage(existing_record)
    if ai_result.get("status") != "ok":
        return  # outage message already shown; nothing is saved

    case = {
        "stage": STAGE_AWAITING_STAFF,
        "submitted_at": datetime.now().isoformat(timespec="seconds"),
        "symptom_description": intake["description"],
        "symptom_duration": intake.get("duration"),
        "self_rated_severity": intake.get("self_rated_severity"),
        "red_flags_present": intake.get("red_flags_present", False),
        "issue_type": issue_type,
        "ai_result": ai_result,
        "outcome": None,
        "staff_overrides": [],
        "follow_up_questions": intake.get("follow_up_questions", []),
        "follow_up_answers": intake.get("follow_up_answers", []),
    }

    if data_manager.save_pending_case(nric, case):
        io_manager.display_message(
            "\nYour request has been sent to our hospital staff. "
            "Please check back later to view the proposed appointment."
        )
    else:
        io_manager.display_error("We couldn't save your request. Please try again.")



def resolve_and_reserve_slot(nric, outcome, previous_slot=None):
    """
    Turn the outcome's placeholder proposed_slot into a real available
    calendar slot and reserve it for this patient immediately, so nobody
    else can be offered it while this patient decides. If previous_slot
    is given (e.g. re-running after a staff override), it is released first.
    """
    if previous_slot:
        data_manager.release_slot(previous_slot)

    if not outcome.get("proposed_slot") or outcome.get("slot_set_by_staff"):
        return outcome  # nothing to book, or staff chose an exact slot

    earliest_dt = datetime.strptime(outcome["proposed_slot"], "%Y-%m-%d %H:%M")
    actual_slot = data_manager.find_next_available_slot(earliest_dt)
    if actual_slot is None:
        return logic_manager.mark_no_slots_available(outcome)

    if not data_manager.book_slot(actual_slot, nric):
        # Rare race: taken between search and book. Retry once.
        actual_slot = data_manager.find_next_available_slot(earliest_dt)
        if actual_slot is None or not data_manager.book_slot(actual_slot, nric):
            return logic_manager.mark_no_slots_available(outcome)

    return logic_manager.finalize_proposed_slot(outcome, actual_slot)


def prompt_and_book_staff_slot(nric):
    """Ask staff for an exact date/time, re-prompting until it is a valid
    future slot that exists in the calendar and isn't already taken.
    Books it for this patient and returns the slot string."""
    while True:
        raw = io_manager.prompt_nonempty_text("Appointment date/time (YYYY-MM-DD HH:MM): ")
        try:
            slot = logic_manager.validate_manual_slot(raw)
        except ValueError as exc:
            io_manager.display_error(str(exc))
            continue
        if not data_manager.is_slot_available(slot):
            io_manager.display_error("That slot is already booked or isn't in the calendar. Please choose another.")
            continue
        if not data_manager.book_slot(slot, nric):
            io_manager.display_error("Couldn't reserve that slot - it may have just been taken. Please try another.")
            continue
        return slot
# ===========================================================================
# Hospital staff
# ===========================================================================

def run_staff_review(staff_record):
    pending = data_manager.list_pending_cases(STAGE_AWAITING_STAFF)
    if not pending:
        io_manager.display_message("\nNo requests are waiting for review.")
        return

    # Step 1: pick a request; logic_manager works out the proposal
    nric, case = io_manager.prompt_select_case(pending)
    red_flags_present = case.get("red_flags_present", False)

    original_ai = case.get("ai_result")
    if not isinstance(original_ai, dict) or original_ai.get("status") != "ok":
        # Only reachable for stale files saved before AI-down requests stopped being stored.
        io_manager.display_error("This request has no usable AI assessment and cannot be reviewed.")
        return
    ai_result = original_ai
    outcome = logic_manager.evaluate_triage_logic(ai_result, red_flags_present=red_flags_present)
    outcome = resolve_and_reserve_slot(nric, outcome)
    reserved_slot = outcome.get("proposed_slot")
    staff_typed_slot = None
    staff_overrides = []  # list of [field, verdict]

    io_manager.display_staff_case(ai_result, outcome, case)

    # Step 2: accept or override
    if not io_manager.prompt_staff_accept():
        while True:
            # Step 3: which field + verdict
            field = io_manager.prompt_override_field()
            if field == "proposed_slot":
                # Exact, calendar-checked slot instead of free text: the AI
                # knows nothing about which slots are taken.
                staff_typed_slot = prompt_and_book_staff_slot(nric)
                verdict = staff_typed_slot
            else:
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
                # AI down: stop. The request stays in the queue untouched,
                # so give back anything reserved during this review.
                for slot in {reserved_slot, staff_typed_slot}:
                    data_manager.release_slot(slot)
                io_manager.display_ai_down_staff()
                return

            ai_result = logic_manager.apply_staff_risk_values(new_ai, staff_overrides)
            outcome = logic_manager.evaluate_triage_logic(
                ai_result, staff_overrides=staff_overrides, red_flags_present=red_flags_present
            )
            outcome = logic_manager.apply_staff_override(outcome, staff_overrides)

            if staff_typed_slot and outcome.get("slot_set_by_staff"):
                # Trust the slot we actually booked, not what the AI echoed back.
                if reserved_slot and reserved_slot != staff_typed_slot:
                    data_manager.release_slot(reserved_slot)
                outcome = logic_manager.finalize_proposed_slot(outcome, staff_typed_slot)
                reserved_slot = staff_typed_slot
            else:
                outcome = resolve_and_reserve_slot(nric, outcome, previous_slot=reserved_slot)
                reserved_slot = outcome.get("proposed_slot")

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
# Search and view patient appointments for staff and patients
# ===========================================================================

def run_staff_patient_search():
    """For Staff to look up a patient by NRIC and show their upcoming appointments."""
    nric = io_manager.prompt_search_nric()
    record = data_manager.load_patient(nric)
    if record is None:
        io_manager.display_message(f"\nNo record found for {nric}.")
        return
    appointments = logic_manager.get_upcoming_appointments(record)
    io_manager.display_appointments(appointments, f"Upcoming appointments for {nric}")

    
def run_view_my_appointments(nric):
    """For Patient to show their own upcoming appointments."""
    record = data_manager.load_patient(nric)
    appointments = logic_manager.get_upcoming_appointments(record)
    io_manager.display_appointments(appointments)

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
        # Nothing to accept (e.g. an emergency): just record that it was seen.
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
                data_manager.release_slot(slot)
                decision, dropped = "rejected", True
                io_manager.display_message(
                    "\nYour request has been dropped. You are welcome to submit a new one."
                )
                break
            # Patient changed their mind about rejecting: ask again.

    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "symptom_description": case["symptom_description"],
        "symptom_duration": case.get("symptom_duration"),
        "self_rated_severity": case.get("self_rated_severity"),
        "red_flags_present": case.get("red_flags_present", False),
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
        elif choice == "appointments":
            run_safely(run_view_my_appointments, nric)
        else:
            return


def run_staff_session(staff_record):
    """Hospital-staff-only options, until the staff member logs out."""
    while True:
        choice = io_manager.prompt_staff_menu()
        if choice == "review":
            run_safely(run_staff_review, staff_record)
        elif choice == "search":
            run_safely(run_staff_patient_search)
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