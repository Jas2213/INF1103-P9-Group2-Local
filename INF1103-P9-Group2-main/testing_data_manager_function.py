"""
demo_data_manager.py

A throwaway interactive CLI for manually poking at data_manager.py the way
the real app eventually will — WITHOUT needing io_manager or ai_manager
built yet. This is a testing/dev tool only, not part of the final
submission (it's fine to delete once main.py exists and does this for real).

Run it with:  python demo_data_manager.py

Since ai_manager and logic_manager aren't built yet, this script lets you
fake their output by picking from a short menu of presets, so you can see
realistic-looking records get saved and queried.
"""

import managers.data_manager as data_manager


def print_main_menu():
    print("\n=== data_manager manual test harness ===")
    print("1. Add a new history record for a patient")
    print("2. View a patient's full record")
    print("3. Find patients by condition (filter)")
    print("4. Simulate a corrupt file and confirm it doesn't crash")
    print("5. Exit")


def action_add_record():
    nric = input("Enter patient NRIC (e.g. S1234567A): ").strip()
    # NRIC format validation lives in io_manager now, not here — this demo
    # is testing data_manager in isolation, so it accepts any string as-is.

    symptom = input("Enter symptom description: ").strip()

    print("\nSince ai_manager isn't built yet, fake its output by answering these:")
    print("(In the real app, the AI decides these from the symptom text — here, you do.)")

    simulate_failure = input("Simulate an AI API failure instead? (y/N): ").strip().lower()

    if simulate_failure == "y":
        ai_result = {"status": "api_failure"}
        outcome = {
            "decision": "manual_review",
            "specialist": None,
            "flag_for_manual_review": True,
            "nurse_override": False,
            "next_action": "Escalate to nursing staff for manual review",
        }
    else:
        urgency = input("Urgency (urgent / non-urgent): ").strip().lower() or "non-urgent"
        severity = input("Severity (low / medium / high): ").strip().lower() or "low"
        specialist = input("Recommended specialist (blank for none): ").strip() or None

        ai_result = {
            "status": "ok",
            "urgency": urgency,
            "severity": severity,
            "possible_conditions": [],
            "recommended_specialist": specialist,
            "confidence": "medium",
            "summary": f"({urgency}, {severity} severity) — {symptom}",
        }

        # Simple stand-in rule so outcome reacts to what you picked, just
        # like logic_manager eventually will (real rules live there, not here).
        if urgency == "urgent" and severity == "high":
            decision = "route_to_specialist"
            next_action = "Book urgent appointment within 24 hours"
        elif urgency == "urgent":
            decision = "route_to_specialist" if specialist else "manual_review"
            next_action = "Book priority appointment"
        else:
            decision = "schedule_non_urgent"
            next_action = "Propose next available general clinic slot"

        outcome = {
            "decision": decision,
            "specialist": specialist,
            "flag_for_manual_review": False,
            "nurse_override": False,
            "next_action": next_action,
        }

    record = {
        "symptom_description": symptom,
        "ai_result": ai_result,
        "outcome": outcome,
    }

    success = data_manager.append_history_record(nric, record)
    if success:
        print(f"Saved. {nric} now has a new history entry.")
    else:
        print(f"FAILED to save record for {nric}.")


def action_view_record():
    nric = input("Enter patient NRIC to view: ").strip()
    patient = data_manager.load_patient(nric)

    if patient is None:
        print(f"No record found for '{nric}' (or file was corrupt and got quarantined).")
        return

    print(f"\nPatient: {patient['patient_id']}")
    print(f"Total history entries: {len(patient['history'])}")
    for i, entry in enumerate(patient["history"], start=1):
        print(f"\n  Entry {i} ({entry.get('timestamp')})")
        print(f"    Symptom: {entry.get('symptom_description')}")
        print(f"    AI result: {entry.get('ai_result')}")
        print(f"    Outcome: {entry.get('outcome')}")


def action_find_patients():
    print("\nLeave any field blank to skip that filter.")
    urgency = input("Filter by urgency (urgent / non-urgent): ").strip() or None
    severity = input("Filter by severity (low / medium / high): ").strip() or None
    specialist = input("Filter by specialist (e.g. cardiology): ").strip() or None

    results = data_manager.find_patients_by_condition(
        urgency=urgency, severity=severity, specialist=specialist
    )

    if not results:
        print("No matching patients found.")
        return

    print(f"\nFound {len(results)} matching patient(s):")
    for patient in results:
        latest = patient["history"][-1]
        print(f"  - {patient['patient_id']}: {latest.get('symptom_description')}"
              f" (decision: {latest.get('outcome', {}).get('decision')})")


def action_simulate_corrupt_file():
    import os

    nric = input("Enter an NRIC to corrupt (e.g. S9999999Z): ").strip()
    data_manager.ensure_data_folder()
    filepath = data_manager._get_patient_filepath(nric)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("{ this is deliberately broken json ][")

    print(f"Wrote a corrupt file to {filepath}. Now attempting to load it...")
    result = data_manager.load_patient(nric)

    if result is None:
        print("load_patient() correctly returned None instead of crashing.")
        if os.path.exists(filepath + ".corrupt"):
            print(f"Corrupt file was quarantined to {filepath}.corrupt as expected.")
    else:
        print("UNEXPECTED: load_patient() returned data for a corrupt file.")


def main():
    data_manager.ensure_data_folder()
    print(f"Using data folder: {data_manager.DATA_DIR}")

    actions = {
        "1": action_add_record,
        "2": action_view_record,
        "3": action_find_patients,
        "4": action_simulate_corrupt_file,
    }

    while True:
        print_main_menu()
        choice = input("Choose an option: ").strip()

        if choice == "5":
            print("Bye!")
            break

        action = actions.get(choice)
        if action:
            action()
        else:
            print("Invalid choice, try again.")


if __name__ == "__main__":
    main()