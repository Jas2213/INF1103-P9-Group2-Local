"""
python -m unittest tests.test_io_manager -v

io_manager talks to a person through input() and print(), so each test:
  - replaces input() with a scripted list of answers (as if a person typed them)
  - captures everything print() would have shown
If a function asks for more input than the test supplied, the test fails
instead of hanging. No API, files or terminal interaction are needed.
"""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from managers import io_manager as iom


class IOManagerTestCase(unittest.TestCase):
    """Base class: feeds scripted answers and captures printed output."""

    def feed(self, function, *args, inputs=()):
        """Call function(*args) with input() scripted from `inputs`.

        Returns (result, printed_output). Also records self.prompts (every
        prompt text shown) and self.unused (scripted answers never asked for).
        """
        answers = iter(inputs)
        self.prompts = []

        def fake_input(prompt=""):
            self.prompts.append(prompt)
            try:
                return next(answers)
            except StopIteration:
                raise AssertionError(
                    f"{function.__name__} asked for more input than the test supplied"
                ) from None

        buffer = io.StringIO()
        with patch("builtins.input", side_effect=fake_input), redirect_stdout(buffer):
            result = function(*args)
        self.unused = list(answers)
        return result, buffer.getvalue()

    def show(self, function, *args):
        """Call a display function that must not ask for input; return its output."""
        _, output = self.feed(function, *args)
        self.assertEqual(self.prompts, [], "display function unexpectedly asked for input")
        return output


# ===========================================================================
# Part 1: ID validators (pure checks)
# ===========================================================================

class TestIdValidators(IOManagerTestCase):

    def test_valid_nrics_are_accepted(self):
        valid = ("S1234567A", "T1234567B", "F1234567C", "G1234567D", "M1234567E",
                 "s1234567a", "  S1234567A  ")
        for nric in valid:
            with self.subTest(nric=nric):
                self.assertTrue(iom.validate_nric_format(nric))

    def test_invalid_nrics_are_rejected(self):
        invalid = ("A1234567B", "S123456A", "S12345678A", "S1234567", "1234567A",
                   "S1234567AB", "", "   ", None, 12345678)
        for nric in invalid:
            with self.subTest(nric=nric):
                self.assertFalse(iom.validate_nric_format(nric))

    def test_valid_staff_ids_are_accepted(self):
        for staff_id in ("STF0001", "stf0001", " STF0001 ", "STF9999"):
            with self.subTest(staff_id=staff_id):
                self.assertTrue(iom.validate_staff_id_format(staff_id))

    def test_invalid_staff_ids_are_rejected(self):
        invalid = ("STF001", "STF00001", "STF000A", "STF 0001", "S1234567A", "", None, 1)
        for staff_id in invalid:
            with self.subTest(staff_id=staff_id):
                self.assertFalse(iom.validate_staff_id_format(staff_id))

    def test_nric_and_staff_id_formats_never_overlap(self):
        # This is what lets one login box tell patients and staff apart.
        for nric in ("S1234567A", "T1234567B", "F1234567C"):
            with self.subTest(nric=nric):
                self.assertFalse(iom.validate_staff_id_format(nric))
        for staff_id in ("STF0001", "STF0002"):
            with self.subTest(staff_id=staff_id):
                self.assertFalse(iom.validate_nric_format(staff_id))


# ===========================================================================
# Part 2: generic re-prompt loops
# ===========================================================================

class TestGenericPrompts(IOManagerTestCase):

    def test_nonempty_text_rejects_blank_then_returns_stripped_text(self):
        result, output = self.feed(iom.prompt_nonempty_text, "Name: ",
                                   inputs=["", "   ", "  cough  "])
        self.assertEqual(result, "cough")
        self.assertEqual(len(self.prompts), 3)
        self.assertEqual(output.count("can't be empty"), 2)
        self.assertEqual(self.prompts[0], "Name: ")

    def test_yes_no_accepts_all_yes_forms(self):
        for answer in ("y", "Y", "yes", "YES", " yes "):
            with self.subTest(answer=answer):
                result, _ = self.feed(iom.prompt_yes_no, "Sure?", inputs=[answer])
                self.assertIs(result, True)

    def test_yes_no_accepts_all_no_forms(self):
        for answer in ("n", "N", "no", "No", " no "):
            with self.subTest(answer=answer):
                result, _ = self.feed(iom.prompt_yes_no, "Sure?", inputs=[answer])
                self.assertIs(result, False)

    def test_yes_no_rejects_other_answers_then_accepts(self):
        result, output = self.feed(iom.prompt_yes_no, "Sure?", inputs=["maybe", "", "y"])
        self.assertIs(result, True)
        self.assertEqual(output.count("Please answer 'yes' or 'no'."), 2)
        self.assertIn("(yes/no)", self.prompts[0])

    def test_choice_is_case_insensitive_and_keeps_original_casing(self):
        result, _ = self.feed(iom.prompt_choice, "Pick", ["Nurse", "Doctor"], inputs=["nurse"])
        self.assertEqual(result, "Nurse")

    def test_choice_rejects_unknown_option_then_accepts(self):
        result, output = self.feed(iom.prompt_choice, "Pick", ["Nurse", "Doctor"],
                                   inputs=["porter", "DOCTOR"])
        self.assertEqual(result, "Doctor")
        self.assertIn("Please enter one of: Nurse/Doctor", output)
        self.assertEqual(self.unused, [])


# ===========================================================================
# Part 3: patient input prompts
# ===========================================================================

class TestPatientPrompts(IOManagerTestCase):

    def test_symptom_description_and_duration_reject_blank(self):
        result, _ = self.feed(iom.prompt_symptom_description, inputs=["", "sore throat"])
        self.assertEqual(result, "sore throat")
        result, _ = self.feed(iom.prompt_symptom_duration, inputs=["  ", "3 days"])
        self.assertEqual(result, "3 days")

    def test_self_rated_severity_returns_an_int(self):
        for answer, expected in (("1", 1), ("7", 7), ("10", 10)):
            with self.subTest(answer=answer):
                result, _ = self.feed(iom.prompt_self_rated_severity, inputs=[answer])
                self.assertEqual(result, expected)
                self.assertIsInstance(result, int)

    def test_self_rated_severity_rejects_out_of_range_and_non_numbers(self):
        result, output = self.feed(iom.prompt_self_rated_severity,
                                   inputs=["0", "11", "abc", "5.5", "", "5"])
        self.assertEqual(result, 5)
        self.assertEqual(len(self.prompts), 6)
        self.assertEqual(output.count("Please enter one of"), 5)

    def test_red_flags_prompt_returns_a_bool(self):
        result, _ = self.feed(iom.prompt_red_flags_present, inputs=["yes"])
        self.assertIs(result, True)
        self.assertIn("chest pain", self.prompts[0])
        result, _ = self.feed(iom.prompt_red_flags_present, inputs=["no"])
        self.assertIs(result, False)

    def test_issue_type_accepts_new_or_existing_in_any_case(self):
        result, _ = self.feed(iom.prompt_issue_type, inputs=["NEW"])
        self.assertEqual(result, "new")
        result, _ = self.feed(iom.prompt_issue_type, inputs=["existing"])
        self.assertEqual(result, "existing")

    def test_issue_type_rejects_other_answers(self):
        result, output = self.feed(iom.prompt_issue_type, inputs=["old", "new"])
        self.assertEqual(result, "new")
        self.assertIn("Please enter one of: new/existing", output)

    def test_follow_up_answers_with_no_questions_asks_nothing(self):
        result, output = self.feed(iom.prompt_follow_up_answers, [])
        self.assertEqual(result, [])
        self.assertEqual(self.prompts, [])
        self.assertEqual(output, "")

    def test_follow_up_answers_come_back_in_order(self):
        result, output = self.feed(iom.prompt_follow_up_answers, ["Any fever?", "Allergies?"],
                                   inputs=["No", "Two days"])
        self.assertEqual(result, ["No", "Two days"])
        self.assertIn("Question 1: Any fever?", output)
        self.assertIn("Question 2: Allergies?", output)

    def test_follow_up_answers_reject_blank_answers(self):
        result, output = self.feed(iom.prompt_follow_up_answers, ["Any fever?"],
                                   inputs=["", "Yes"])
        self.assertEqual(result, ["Yes"])
        self.assertIn("can't be empty", output)

    def test_valid_nric_prompt_returns_uppercase(self):
        result, _ = self.feed(iom._prompt_valid_nric, "NRIC: ", inputs=[" s1234567a "])
        self.assertEqual(result, "S1234567A")

    def test_valid_nric_prompt_reprompts_on_bad_format(self):
        result, output = self.feed(iom._prompt_valid_nric, "NRIC: ", inputs=["123", "S1234567A"])
        self.assertEqual(result, "S1234567A")
        self.assertIn("'123' doesn't look like a valid NRIC", output)

    def test_login_recognises_patients(self):
        for typed, expected in (("S1234567A", "S1234567A"), ("s1234567a", "S1234567A")):
            with self.subTest(typed=typed):
                result, _ = self.feed(iom.prompt_login_id, inputs=[typed])
                self.assertEqual(result, ("patient", expected))

    def test_login_recognises_staff(self):
        for typed, expected in (("STF0001", "STF0001"), ("stf0001", "STF0001")):
            with self.subTest(typed=typed):
                result, _ = self.feed(iom.prompt_login_id, inputs=[typed])
                self.assertEqual(result, ("staff", expected))

    def test_login_exit_in_any_case(self):
        for typed in ("exit", "EXIT", " exit "):
            with self.subTest(typed=typed):
                result, _ = self.feed(iom.prompt_login_id, inputs=[typed])
                self.assertEqual(result, ("exit", None))

    def test_login_rejects_junk_then_accepts(self):
        result, output = self.feed(iom.prompt_login_id, inputs=["hello", "", "S1234567A"])
        self.assertEqual(result, ("patient", "S1234567A"))
        self.assertEqual(output.count("doesn't look like an NRIC"), 2)


# ===========================================================================
# Part 4: menus and staff account prompts
# ===========================================================================

class TestMenus(IOManagerTestCase):

    def test_patient_menu_returns_each_option(self):
        for option in iom.PATIENT_MENU_OPTIONS:
            with self.subTest(option=option):
                result, output = self.feed(iom.prompt_patient_menu, inputs=[option.upper()])
                self.assertEqual(result, option)
                self.assertIn("Patient Menu", output)

    def test_staff_menu_returns_each_option(self):
        for option in iom.STAFF_MENU_OPTIONS:
            with self.subTest(option=option):
                result, output = self.feed(iom.prompt_staff_menu, inputs=[option])
                self.assertEqual(result, option)
                self.assertIn("Hospital Staff Menu", output)

    def test_admin_menu_returns_each_option(self):
        for option in iom.ADMIN_MENU_OPTIONS:
            with self.subTest(option=option):
                result, output = self.feed(iom.prompt_admin_menu, inputs=[option])
                self.assertEqual(result, option)
                self.assertIn("Staff Account Admin", output)

    def test_menus_reject_unknown_options(self):
        result, _ = self.feed(iom.prompt_patient_menu, inputs=["review", "logout"])
        self.assertEqual(result, "logout")
        result, _ = self.feed(iom.prompt_staff_menu, inputs=["submit", "search"])
        self.assertEqual(result, "search")
        result, _ = self.feed(iom.prompt_admin_menu, inputs=["delete", "list"])
        self.assertEqual(result, "list")

    def test_staff_name_rejects_blank(self):
        result, _ = self.feed(iom.prompt_staff_name, inputs=["", "Alice Tan"])
        self.assertEqual(result, "Alice Tan")

    def test_staff_role_is_nurse_or_doctor(self):
        result, _ = self.feed(iom.prompt_staff_role, inputs=["DOCTOR"])
        self.assertEqual(result, "doctor")
        result, _ = self.feed(iom.prompt_staff_role, inputs=["porter", "nurse"])
        self.assertEqual(result, "nurse")


# ===========================================================================
# Part 5: staff review prompts
# ===========================================================================

class TestStaffReviewPrompts(IOManagerTestCase):

    def pending_cases(self):
        return [
            ["S1111111A", {"submitted_at": "2030-01-01T10:00:00",
                           "ai_result": {"summary": "Cough"}}],
            ["S2222222B", {}],
        ]

    def test_select_case_returns_the_chosen_pair(self):
        cases = self.pending_cases()
        result, _ = self.feed(iom.prompt_select_case, cases, inputs=["2"])
        self.assertEqual(result, cases[1])

    def test_select_case_lists_every_request(self):
        _, output = self.feed(iom.prompt_select_case, self.pending_cases(), inputs=["1"])
        self.assertIn("S1111111A | submitted 2030-01-01T10:00:00 | Cough", output)
        self.assertIn("S2222222B | submitted ? | (AI summary unavailable)", output)

    def test_select_case_rejects_bad_numbers_and_text(self):
        cases = self.pending_cases()
        result, output = self.feed(iom.prompt_select_case, cases,
                                   inputs=["0", "3", "abc", "", "1"])
        self.assertEqual(result, cases[0])
        self.assertEqual(output.count("Please enter a number between 1 and 2."), 4)

    def test_override_field_must_be_a_known_field(self):
        result, _ = self.feed(iom.prompt_override_field, inputs=["colour", "SEVERITY"])
        self.assertEqual(result, "severity")

    def test_override_verdict_with_options_is_restricted(self):
        result, output = self.feed(iom.prompt_override_verdict, "severity",
                                   ("low", "medium", "high"), inputs=["critical", "HIGH"])
        self.assertEqual(result, "high")
        self.assertIn("Please enter one of: low/medium/high", output)

    def test_override_verdict_without_options_is_free_text(self):
        result, _ = self.feed(iom.prompt_override_verdict, "department",
                              inputs=["", " neurology "])
        self.assertEqual(result, "neurology")

    def test_staff_accept_and_confirm_return_bools(self):
        result, _ = self.feed(iom.prompt_staff_accept, inputs=["yes"])
        self.assertIs(result, True)
        result, _ = self.feed(iom.prompt_staff_accept, inputs=["no"])
        self.assertIs(result, False)
        result, _ = self.feed(iom.prompt_staff_confirm_correct, inputs=["no"])
        self.assertIs(result, False)

    def test_patient_accept_proposal_returns_a_bool(self):
        result, _ = self.feed(iom.prompt_patient_accept_proposal, inputs=["y"])
        self.assertIs(result, True)
        result, _ = self.feed(iom.prompt_patient_accept_proposal, inputs=["n"])
        self.assertIs(result, False)

    def test_reject_confirmation_warns_before_asking(self):
        result, output = self.feed(iom.prompt_patient_confirm_reject, inputs=["yes"])
        self.assertIs(result, True)
        self.assertIn("Warning", output)
        self.assertIn("dropped", output)

    def test_search_nric_back_returns_none(self):
        for typed in ("back", "BACK", " back "):
            with self.subTest(typed=typed):
                result, _ = self.feed(iom.prompt_search_nric, inputs=[typed])
                self.assertIsNone(result)

    def test_search_nric_reprompts_then_returns_uppercase(self):
        result, output = self.feed(iom.prompt_search_nric, inputs=["bad", "s1234567a"])
        self.assertEqual(result, "S1234567A")
        self.assertIn("'bad' doesn't look like a valid NRIC", output)


# ===========================================================================
# Part 6: patient-facing displays
# ===========================================================================

class TestPatientDisplays(IOManagerTestCase):

    def test_greeting_for_returning_patient_shows_visit_count(self):
        output = self.show(iom.display_patient_greeting, {"history": [{}, {}]})
        self.assertIn("Welcome back", output)
        self.assertIn("2 previous visit(s)", output)

    def test_greeting_for_record_with_no_visits(self):
        output = self.show(iom.display_patient_greeting, {"history": []})
        self.assertIn("Welcome back", output)
        self.assertIn("0 previous visit(s)", output)

    def test_greeting_for_new_patient(self):
        output = self.show(iom.display_patient_greeting, None)
        self.assertIn("registered as a new patient", output)
        self.assertNotIn("Welcome back", output)

    def test_summary_confirm_returns_the_patients_answer(self):
        ai_result = {"status": "ok", "summary": "Mild cough for two days."}
        result, output = self.feed(iom.display_ai_summary_and_confirm, ai_result, inputs=["yes"])
        self.assertIs(result, True)
        self.assertIn("Mild cough for two days.", output)
        result, _ = self.feed(iom.display_ai_summary_and_confirm, ai_result, inputs=["no"])
        self.assertIs(result, False)

    def test_summary_confirm_handles_missing_summary(self):
        _, output = self.feed(iom.display_ai_summary_and_confirm, {"status": "ok"},
                              inputs=["yes"])
        self.assertIn("(no summary available)", output)

    def test_summary_confirm_on_ai_failure_shows_outage_and_asks_nothing(self):
        for bad in (None, {}, {"status": "error"}, "oops"):
            with self.subTest(ai_result=bad):
                result, output = self.feed(iom.display_ai_summary_and_confirm, bad)
                self.assertIs(result, True)
                self.assertEqual(self.prompts, [])
                self.assertIn("NOT submitted", output)

    def test_final_outcome_shows_the_main_details(self):
        outcome = {"next_action": "Routed to cardiology.", "specialist": "cardiology",
                   "proposed_slot": "2030-01-12 09:00"}
        output = self.show(iom.display_final_outcome, outcome)
        self.assertIn("Your Outcome", output)
        self.assertIn("Next step: Routed to cardiology.", output)
        self.assertIn("Specialist: cardiology", output)
        self.assertIn("Proposed appointment date: 2030-01-12 09:00", output)

    def test_final_outcome_leaves_out_missing_details(self):
        output = self.show(iom.display_final_outcome, {"next_action": "Go to A&E."})
        self.assertNotIn("Specialist:", output)
        self.assertNotIn("Proposed appointment date", output)
        self.assertNotIn("manual review", output)
        self.assertNotIn("confirmed by a nurse", output)

    def test_final_outcome_shows_flags_when_set(self):
        outcome = {"next_action": "x", "flag_for_manual_review": True, "nurse_override": True}
        output = self.show(iom.display_final_outcome, outcome)
        self.assertIn("flagged for manual review", output)
        self.assertIn("reviewed and confirmed by a nurse", output)

    def test_final_outcome_has_a_default_next_step(self):
        output = self.show(iom.display_final_outcome, {})
        self.assertIn("Please wait for further instructions.", output)

    def test_appointments_empty_list(self):
        output = self.show(iom.display_appointments, [])
        self.assertIn("Your upcoming appointments", output)
        self.assertIn("No upcoming appointments.", output)

    def test_appointments_list_shows_each_booking(self):
        appointments = [
            {"slot": "2030-01-12 09:00", "department": "cardiology",
             "practitioner": "doctor", "reason": "chest tightness"},
            {"slot": "2030-01-20 10:30", "department": "general practice",
             "practitioner": "nurse", "reason": "cough"},
        ]
        output = self.show(iom.display_appointments, appointments)
        self.assertIn("1. 2030-01-12 09:00 | cardiology (with a doctor)", output)
        self.assertIn("Reason: chest tightness", output)
        self.assertIn("2. 2030-01-20 10:30 | general practice (with a nurse)", output)
        self.assertNotIn("No upcoming appointments", output)

    def test_appointments_custom_heading(self):
        output = self.show(iom.display_appointments, [], "Upcoming appointments for S1234567A")
        self.assertIn("Upcoming appointments for S1234567A", output)


# ===========================================================================
# Part 7: staff-facing displays
# ===========================================================================

class TestStaffDisplays(IOManagerTestCase):

    def ok_ai(self, **changes):
        result = {"status": "ok", "summary": "Mild cough", "flagged_symptoms": ["cough", "fever"],
                  "urgency": "urgent", "severity": "high"}
        result.update(changes)
        return result

    def outcome(self, **changes):
        result = {"decision": "route_to_specialist", "next_action": "Routed.",
                  "specialist": "cardiology", "proposed_slot": "2030-01-12 09:00"}
        result.update(changes)
        return result

    def test_staff_case_shows_ai_summary_and_flagged_symptoms(self):
        output = self.show(iom.display_staff_case, self.ok_ai(), self.outcome())
        self.assertIn("AI summary: Mild cough", output)
        self.assertIn("Flagged symptoms: cough, fever", output)

    def test_staff_case_with_no_flagged_symptoms(self):
        output = self.show(iom.display_staff_case, self.ok_ai(flagged_symptoms=[]), self.outcome())
        self.assertIn("Flagged symptoms: (none)", output)

    def test_staff_case_shows_the_proposal_fields(self):
        output = self.show(iom.display_staff_case, self.ok_ai(), self.outcome())
        self.assertRegex(output, r"Department:\s+cardiology")
        self.assertRegex(output, r"Urgency:\s+urgent")
        self.assertRegex(output, r"Severity:\s+high")
        self.assertRegex(output, r"Proposed slot:\s+2030-01-12 09:00")
        self.assertRegex(output, r"Decision:\s+route_to_specialist")
        self.assertRegex(output, r"Next action:\s+Routed\.")

    def test_staff_case_shows_na_for_missing_values(self):
        output = self.show(iom.display_staff_case, self.ok_ai(),
                           self.outcome(specialist=None, proposed_slot=None))
        self.assertRegex(output, r"Department:\s+N/A")
        self.assertRegex(output, r"Proposed slot:\s+N/A")

    def test_staff_case_when_ai_is_unavailable(self):
        for bad in ({"status": "error"}, None):
            with self.subTest(ai_result=bad):
                output = self.show(iom.display_staff_case, bad, self.outcome())
                self.assertIn("AI summary: unavailable (API failure)", output)
                self.assertRegex(output, r"Urgency:\s+n/a")

    def test_staff_case_matches_questions_to_answers(self):
        case = {"follow_up_questions": ["Any fever?", "Allergies?"],
                "follow_up_answers": ["Yes"]}
        output = self.show(iom.display_staff_case, self.ok_ai(), self.outcome(), case)
        self.assertIn("Q1: Any fever?", output)
        self.assertIn("A1: Yes", output)
        self.assertIn("Q2: Allergies?", output)
        self.assertIn("A2: (no answer recorded)", output)

    def test_staff_case_without_follow_up_questions(self):
        for case in (None, {}, {"follow_up_questions": []}):
            with self.subTest(case=case):
                output = self.show(iom.display_staff_case, self.ok_ai(), self.outcome(), case)
                self.assertIn("No follow-up questions recorded.", output)

    def test_staff_case_shows_override_note_only_when_applied(self):
        plain = self.show(iom.display_staff_case, self.ok_ai(), self.outcome())
        self.assertNotIn("Staff override applied", plain)
        overridden = self.show(iom.display_staff_case, self.ok_ai(),
                               self.outcome(nurse_override=True, override_reason="urgency: urgent"))
        self.assertIn("(Staff override applied: urgency: urgent)", overridden)

    def test_staff_list_empty(self):
        self.assertIn("No staff accounts yet.", self.show(iom.display_staff_list, []))

    def test_staff_list_shows_each_account(self):
        records = [{"staff_id": "STF0001", "name": "Alice", "role": "doctor"},
                   {"staff_id": "STF0002", "name": "Ben", "role": "nurse"}]
        output = self.show(iom.display_staff_list, records)
        self.assertIn("STF0001 | Alice | doctor", output)
        self.assertIn("STF0002 | Ben | nurse", output)

    def test_staff_greeting_shows_name_and_role(self):
        output = self.show(iom.display_staff_greeting, {"name": "Alice", "role": "doctor"})
        self.assertIn("Welcome, Alice (doctor).", output)

    def test_staff_greeting_has_defaults_for_missing_fields(self):
        output = self.show(iom.display_staff_greeting, {})
        self.assertIn("Welcome, staff member (staff).", output)


# ===========================================================================
# Part 8: messages and outage notices
# ===========================================================================

class TestMessageDisplays(IOManagerTestCase):

    def test_display_message_prints_plain_text(self):
        self.assertEqual(self.show(iom.display_message, "hello"), "hello\n")

    def test_display_error_is_visibly_marked(self):
        self.assertEqual(self.show(iom.display_error, "Something broke"),
                         "\n[!] Something broke\n")

    def test_patient_outage_notice_says_where_to_go(self):
        output = self.show(iom.display_ai_down_patient)
        self.assertIn("NOT submitted", output)
        self.assertIn("GP", output)
        self.assertIn("A&E", output)

    def test_staff_outage_notice_points_to_it_support(self):
        output = self.show(iom.display_ai_down_staff)
        self.assertIn("IT support", output)
        self.assertIn("No changes were made", output)


if __name__ == "__main__":
    unittest.main()