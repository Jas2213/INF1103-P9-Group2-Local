"""
io_manager.py - ALL terminal input/output lives in this class.
No other manager may call print() or input().

Depends on the shared BaseManager only for: get_manager_name() and the
log_info / log_warning / log_error methods. Nothing else is inherited.
"""

import re

from managers.temp.base_manager import BaseManager


class IOManager(BaseManager):

    def get_manager_name(self):
        return "io_manager"

    # Singapore NRIC/FIN: leading letter (S/T citizens/PRs, F/G/M foreigners),
    # 7 digits, trailing checksum letter. Format check only.
    NRIC_PATTERN = re.compile(r"^[STFGM]\d{7}[A-Z]$", re.IGNORECASE)
    # Staff IDs ("STF0001") can never match the NRIC pattern, so one input
    # box can tell patients and staff apart.
    STAFF_ID_PATTERN = re.compile(r"^STF\d{4}$", re.IGNORECASE)
    PATIENT_MENU_OPTIONS = ["submit", "respond", "appointments", "logout"]
    STAFF_MENU_OPTIONS = ["review", "search", "logout"]
    ADMIN_MENU_OPTIONS = ["create", "list", "exit"]
    # Fields staff may override: department -> specialist, the rest map 1:1.
    OVERRIDE_FIELDS = ["department", "urgency", "severity", "proposed_slot"]

    # ------------------------------------------------------------------
    # Format validators (never raise, safe on raw input)
    # ------------------------------------------------------------------
    def validate_nric_format(self, nric):
        """Plausible NRIC/FIN, e.g. "S1234567A"."""
        return isinstance(nric, str) and bool(self.NRIC_PATTERN.match(nric.strip()))

    def validate_staff_id_format(self, staff_id):
        """Looks like a staff ID, e.g. "STF0001"."""
        return isinstance(staff_id, str) and bool(self.STAFF_ID_PATTERN.match(staff_id.strip()))

    # ------------------------------------------------------------------
    # Generic validated-input helpers
    # ------------------------------------------------------------------
    def prompt_nonempty_text(self, prompt_text):
        """Ask until the user enters something that isn't blank."""
        while True:
            value = input(prompt_text).strip()
            if value:
                return value
            print("This can't be empty — please try again.")

    def prompt_yes_no(self, prompt_text):
        """y/yes/n/no (case-insensitive). True for yes."""
        while True:
            raw = input(f"{prompt_text} (yes/no): ").strip().lower()
            if raw in ("y", "yes"):
                return True
            if raw in ("n", "no"):
                return False
            print("Please answer 'yes' or 'no'.")

    def prompt_choice(self, prompt_text, valid_options):
        """Ask until the user picks one of valid_options; returns it in original casing."""
        lowered_map = {opt.lower(): opt for opt in valid_options}
        options_display = "/".join(valid_options)
        while True:
            raw = input(f"{prompt_text} ({options_display}): ").strip().lower()
            if raw in lowered_map:
                return lowered_map[raw]
            print(f"Please enter one of: {options_display}")

    def prompt_press_enter_to_continue(self, message="Press Enter to return to the menu..."):
        input(f"\n{message}")

    # ------------------------------------------------------------------
    # Plain status / error display
    # ------------------------------------------------------------------
    def display_message(self, message):
        print(message)

    def display_error(self, message):
        print(f"\n[!] {message}")

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    def prompt_login_id(self):
        """
        One prompt for NRIC or Staff ID; the format decides the role.
        Returns ("patient", NRIC), ("staff", STAFF_ID) or ("exit", None).
        """
        while True:
            raw = input("\nEnter your NRIC or Staff ID (or 'exit' to quit): ").strip()
            if raw.lower() == "exit":
                return "exit", None
            if self.validate_nric_format(raw):
                return "patient", raw.upper()
            if self.validate_staff_id_format(raw):
                return "staff", raw.upper()
            print("That doesn't look like an NRIC (e.g. S1234567A) or a Staff ID (e.g. STF0001).")

    def _prompt_valid_nric(self, prompt_text):
        """Ask until a valid NRIC is entered; returns it uppercased."""
        while True:
            raw = input(prompt_text).strip()
            if self.validate_nric_format(raw):
                return raw.upper()
            print(f"'{raw}' doesn't look like a valid NRIC. Format is 1 letter + 7 digits + 1 letter.")

    def display_patient_greeting(self, existing_record):
        """Greeting for a returning or new patient (main.py does the lookup)."""
        if existing_record is not None:
            visit_count = len(existing_record.get("history", []))
            print(f"\nWelcome back! We have {visit_count} previous visit(s) on file.")
        else:
            print("\nWe don't have a record for you yet — you'll be registered as a new patient.")

    def display_staff_greeting(self, staff_record):
        print(f"\nWelcome, {staff_record.get('name', 'staff member')} ({staff_record.get('role', 'staff')}).")

    # ------------------------------------------------------------------
    # Menus
    # ------------------------------------------------------------------
    def prompt_patient_menu(self):
        print("\n=== Patient Menu ===")
        print("")
        print("  submit       - Submit a new problem")
        print("")
        print("  respond      - View and respond to a proposed appointment")
        print("")
        print("  appointments - View my upcoming appointments")
        print("")
        print("  logout       - Log out")
        print("")
        print("====================")
        return self.prompt_choice("Select an option", self.PATIENT_MENU_OPTIONS)

    def prompt_staff_menu(self):
        print("\n=== Hospital Staff Menu ===")
        print("")
        print("  review - Review waiting patient requests")
        print("")
        print("  search - Search for a patient")
        print("")
        print("  logout - Log out")
        print("")
        print("===========================")
        return self.prompt_choice("Select an option", self.STAFF_MENU_OPTIONS)

    def prompt_admin_menu(self):
        print("\n=== Staff Account Admin ===")
        print("")
        print("  create - Create a new staff account")
        print("")
        print("  list   - List existing staff accounts")
        print("")
        print("  exit   - Quit")
        print("")
        print("\n=========================")
        return self.prompt_choice("Select an option", self.ADMIN_MENU_OPTIONS)

    # ------------------------------------------------------------------
    # Staff account administration
    # ------------------------------------------------------------------
    def prompt_staff_name(self):
        return self.prompt_nonempty_text("Staff full name: ")

    def prompt_staff_role(self):
        return self.prompt_choice("Role", ["nurse", "doctor"])

    def display_staff_list(self, staff_records):
        if not staff_records:
            print("\nNo staff accounts yet.")
            return
        print("\nStaff accounts:")
        for record in staff_records:
            print(f"  {record.get('staff_id')} | {record.get('name')} | {record.get('role')}")

        # ------------------------------------------------------------------
    # Patient intake
    # ------------------------------------------------------------------
    def prompt_symptom_description(self):
        return self.prompt_nonempty_text("Please describe your symptoms: ")

    def prompt_symptom_duration(self):
        return self.prompt_nonempty_text(
            "How long have you had these symptoms? (e.g. 3 hours, 5 days, or 2 weeks): "
        )

    def prompt_self_rated_severity(self):
        return int(self.prompt_choice("Rate the severity (1-10)", [str(i) for i in range(1, 11)]))

    def prompt_red_flags_present(self):
        return self.prompt_yes_no("Any chest pain, or difficulty breathing")

    def prompt_issue_type(self):
        """Patient-stated: is this a "new" problem or an "existing" condition?"""
        return self.prompt_choice("\nIs this report for a", ["new", "existing"])

    def prompt_follow_up_answers(self, questions):
        """Show AI follow-up questions; return the answers in the same order."""
        answers = []
        if not questions:
            return answers
        self.display_message(
            "\nWe have a few additional questions to better understand your symptoms."
        )
        for index, question in enumerate(questions, start=1):
            self.display_message(f"\nQuestion {index}: {question}")
            answers.append(self.prompt_nonempty_text("Your answer: "))
        return answers

        # ------------------------------------------------------------------
    # Patient-side: AI summary + outcome
    # ------------------------------------------------------------------
    def display_ai_summary_and_confirm(self, ai_result):
        """
        Show the AI summary and ask the patient to confirm it. Returns True if
        confirmed. Also True (nothing to confirm) on an API failure.
        """
        if not isinstance(ai_result, dict) or ai_result.get("status") != "ok":
            self.display_ai_down_patient()
            return True
        summary = ai_result.get("summary", "(no summary available)")
        print(f"\nHere's what we understood from your symptoms:\n  \"{summary}\"")
        return self.prompt_yes_no("Does this accurately reflect your symptoms?")

    def display_final_outcome(self, outcome):
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

    def prompt_patient_accept_proposal(self):
        return self.prompt_yes_no("Do you accept this proposed appointment?")

    def prompt_patient_confirm_reject(self):
        print("\nWarning: if you reject, your request will be dropped and you may lose this proposed slot.")
        return self.prompt_yes_no("Are you sure you want to reject this proposal?")

    def display_appointments(self, appointments, heading="Your upcoming appointments"):
        """Show the list from LogicManager.get_upcoming_appointments()."""
        print(f"\n--- {heading} ---")
        if not appointments:
            print("  No upcoming appointments.")
            return
        for i, appt in enumerate(appointments, start=1):
            print(f"  {i}. {appt['slot']} | {appt['department']} (with a {appt['practitioner']})")
            print("")
            print(f"     Reason: {appt['reason']}")

    # ------------------------------------------------------------------
    # AI outage messages (no manual fallback by design)
    # ------------------------------------------------------------------
    def display_ai_down_patient(self):
        print("\n[!] Our triage system is currently unavailable, so your request was NOT submitted.")
        print("")
        print("    Please visit your GP. If this is an emergency, go to A&E immediately.")

    def display_ai_down_staff(self):
        print("\n[!] The AI triage system is currently down.")
        print("")
        print("    Please contact IT support. No changes were made to this request.")

    # ------------------------------------------------------------------
    # Hospital staff
    # ------------------------------------------------------------------
    def prompt_select_case(self, pending_cases):
        """List waiting requests ([patient_id, case] pairs) and return the chosen one."""
        print("\nRequests awaiting staff review:")
        for i, (patient_id, case) in enumerate(pending_cases, start=1):
            summary = (case.get("ai_result") or {}).get("summary", "(AI summary unavailable)")
            print(f"  {i}. {patient_id} | submitted {case.get('submitted_at', '?')} | {summary}")

        total = len(pending_cases)
        while True:
            raw = input(f"Select a request (1-{total}): ").strip()
            if raw.isdigit() and 1 <= int(raw) <= total:
                return pending_cases[int(raw) - 1]
            print(f"Please enter a number between 1 and {total}.")

    def display_staff_case(self, ai_result, outcome, case=None):
        """Show the AI suggestion, follow-up Q&A and the logic proposal to staff."""
        print("\n--- Request for Staff Review ---")

        if isinstance(ai_result, dict) and ai_result.get("status") == "ok":
            print(f"AI summary: {ai_result.get('summary', '(none)')}")
            flagged = ai_result.get("flagged_symptoms") or []
            print(f"Flagged symptoms: {', '.join(flagged) if flagged else '(none)'}")
        else:
            print("AI summary: unavailable (API failure)")

        case = case or {}
        questions = case.get("follow_up_questions") or []
        answers = case.get("follow_up_answers") or []

        print("\n--- Patient Follow-up Questions ---")
        if questions:
            for index, question in enumerate(questions):
                answer = answers[index] if index < len(answers) else "(no answer recorded)"
                print(f"Q{index + 1}: {question}")
                print(f"A{index + 1}: {answer}")
        else:
            print("No follow-up questions recorded.")

        print(f"Department:    {outcome.get('specialist') or 'N/A'}")
        print("")
        print(f"Urgency:       {(ai_result or {}).get('urgency', 'n/a')}")
        print("")
        print(f"Severity:      {(ai_result or {}).get('severity', 'n/a')}")
        print("")
        print(f"Proposed slot: {outcome.get('proposed_slot') or 'N/A'}")
        print("")
        print(f"Decision:      {outcome.get('decision')}")
        print("")
        print(f"Next action:   {outcome.get('next_action')}")
        print("")
        if outcome.get("nurse_override"):
            print(f"(Staff override applied: {outcome.get('override_reason')})")

    def prompt_staff_accept(self):
        return self.prompt_yes_no("Accept the proposed option as is?")

    def prompt_override_field(self):
        return self.prompt_choice("Which field do you want to override", self.OVERRIDE_FIELDS)

    def prompt_override_verdict(self, field, options=None):
        """Pick from `options` (urgency/severity) or enter free text (department/slot)."""
        if options:
            return self.prompt_choice(f"Enter your verdict for '{field}'", list(options))
        return self.prompt_nonempty_text(f"Enter your verdict for '{field}': ")

    def prompt_staff_confirm_correct(self):
        return self.prompt_yes_no("Is everything correct now?")

    def prompt_search_nric(self):
        """Staff NRIC prompt. Uppercased NRIC, or None if the user types 'back'."""
        while True:
            raw = input(
                "\nEnter the patient's NRIC (e.g. S1234567A), or 'back' to return to the menu: "
            ).strip()
            if raw.lower() == "back":
                return None
            if self.validate_nric_format(raw):
                return raw.upper()
            print(f"'{raw}' doesn't look like a valid NRIC. Format is 1 letter + 7 digits + 1 letter.")