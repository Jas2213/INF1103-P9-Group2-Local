
import managers.io_manager as io_manager


# ---------------------------------------------------------------------------
# Helpers to build fake ai_result / outcome dicts for testing
# ---------------------------------------------------------------------------

def build_fake_ai_result():
    print("\nBuild a fake ai_result to test with:")
    ok = io_manager.prompt_yes_no("Should status be 'ok'? (no = simulate API failure)")

    if not ok:
        return {"status": "api_failure"}

    summary = io_manager.prompt_nonempty_text("AI summary text: ")
    urgency = io_manager.prompt_choice("Urgency", ["urgent", "non-urgent"])
    severity = io_manager.prompt_choice("Severity", ["low", "medium", "high"])
    has_specialist = io_manager.prompt_yes_no("Recommend a specialist?")
    specialist = io_manager.prompt_nonempty_text("Specialist name: ") if has_specialist else None

    return {
        "status": "ok",
        "urgency": urgency,
        "severity": severity,
        "recommended_specialist": specialist,
        "confidence": "medium",
        "summary": summary,
    }


def build_fake_outcome():
    print("\nBuild a fake outcome (logic_manager's output) to test with:")
    decision = io_manager.prompt_choice(
        "Decision", ["route_to_specialist", "urgent_priority", "schedule_non_urgent", "manual_review"]
    )
    next_action = io_manager.prompt_nonempty_text("Next action text: ")
    has_specialist = io_manager.prompt_yes_no("Include a specialist?")
    specialist = io_manager.prompt_nonempty_text("Specialist name: ") if has_specialist else None
    has_slot = io_manager.prompt_yes_no("Include a proposed slot date?")
    proposed_slot = io_manager.prompt_nonempty_text("Proposed slot (e.g. 2026-09-28): ") if has_slot else None
    flagged = io_manager.prompt_yes_no("Flag for manual review?")

    return {
        "decision": decision,
        "next_action": next_action,
        "specialist": specialist,
        "proposed_slot": proposed_slot,
        "flag_for_manual_review": flagged,
        "nurse_override": False,
    }


# ---------------------------------------------------------------------------
# Menu actions — each calls one io_manager function and prints what it returned
# ---------------------------------------------------------------------------

def action_patient_identity():
    print("\n>>> Calling io_manager.prompt_patient_nric()")
    nric = io_manager.prompt_patient_nric()
    print(f"\nRETURNED: nric={nric!r}")

    # In the real app, main.py would now call data_manager.load_patient(nric)
    # and pass the result to display_patient_greeting(). Since this demo
    # tests io_manager in isolation (no data_manager import), fake that
    # lookup result here instead.
    is_existing = io_manager.prompt_yes_no("Simulate: does this patient already have a record?")
    fake_record = {"patient_id": nric, "history": [{}, {}]} if is_existing else None

    print("\n>>> Calling io_manager.display_patient_greeting(fake_record)")
    io_manager.display_patient_greeting(fake_record)


def action_symptom_description():
    print("\n>>> Calling io_manager.prompt_symptom_description()")
    result = io_manager.prompt_symptom_description()
    print(f"\nRETURNED: {result!r}")


def action_ai_summary_confirm():
    ai_result = build_fake_ai_result()
    print("\n>>> Calling io_manager.display_ai_summary_and_confirm(ai_result)")
    result = io_manager.display_ai_summary_and_confirm(ai_result)
    print(f"\nRETURNED: {result!r}")


def action_nurse_review():
    ai_result = build_fake_ai_result()
    outcome = build_fake_outcome()
    print("\n>>> Calling io_manager.display_nurse_review(ai_result, outcome)")
    is_override, new_decision, reason, specialist = io_manager.display_nurse_review(ai_result, outcome)
    print(f"\nRETURNED: is_override={is_override!r}, new_decision={new_decision!r}, "
          f"reason={reason!r}, specialist={specialist!r}")


def action_final_outcome():
    outcome = build_fake_outcome()
    print("\n>>> Calling io_manager.display_final_outcome(outcome)")
    io_manager.display_final_outcome(outcome)
    print("\n(this function has no return value — just prints)")


def action_generic_helpers():
    print("\nWhich generic helper?")
    print("  1. prompt_nonempty_text")
    print("  2. prompt_yes_no")
    print("  3. prompt_choice")
    choice = input("Choice: ").strip()

    if choice == "1":
        result = io_manager.prompt_nonempty_text("Enter some text: ")
        print(f"\nRETURNED: {result!r}")
    elif choice == "2":
        result = io_manager.prompt_yes_no("Confirm something?")
        print(f"\nRETURNED: {result!r}")
    elif choice == "3":
        result = io_manager.prompt_choice("Pick one", ["apple", "banana", "cherry"])
        print(f"\nRETURNED: {result!r}")
    else:
        print("Invalid choice.")


# ---------------------------------------------------------------------------
# Main menu
# ---------------------------------------------------------------------------

def print_main_menu():
    print("\n=== io_manager manual test harness ===")
    print("1. prompt_patient_nric() + display_patient_greeting()")
    print("2. prompt_symptom_description()")
    print("3. display_ai_summary_and_confirm()")
    print("4. display_nurse_review()")
    print("5. display_final_outcome()")
    print("6. Test a generic helper (prompt_nonempty_text / prompt_yes_no / prompt_choice)")
    print("7. Exit")


def main():
    actions = {
        "1": action_patient_identity,
        "2": action_symptom_description,
        "3": action_ai_summary_confirm,
        "4": action_nurse_review,
        "5": action_final_outcome,
        "6": action_generic_helpers,
    }

    while True:
        print_main_menu()
        choice = input("Choose an option: ").strip()

        if choice == "7":
            print("Bye!")
            break

        action = actions.get(choice)
        if action:
            action()
        else:
            print("Invalid choice, try again.")


if __name__ == "__main__":
    main()