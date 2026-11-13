"""Readable, case-by-case automated tests for the updated logic_manager.
Place beside main.py. Run: python test_logic_procedural.py
No live API, external dependencies, input prompts or patient-file writes.
Exit code: 0 if all checks pass; 1 on failures or unexpected errors.
A fixed clock makes dates and output repeatable. Expected values express
software requirements, not clinical validation. The test clock is restored after the run.
Requires the updated logic_manager with staff override and booking fixes.
The first six cases demonstrate routing; the remaining cases retain regression coverage.
"""
import copy
import sys
from datetime import datetime
from types import ModuleType
from managers import logic_manager as logic

FIXED_NOW = datetime(2030, 1, 10, 12, 0)

def fixed_now():
    return FIXED_NOW

def check_equal(actual, expected, label="Value"):
    print(f"  {label} | Expected: {expected!r} | Actual: {actual!r}")
    if actual != expected:
        raise AssertionError(f"Expected {expected!r}, got {actual!r}")

def check_true(value, label="Value"):
    check_equal(value, True, label)

def check_false(value, label="Value"):
    check_equal(value, False, label)

def check_none(value, label="Value"):
    print(f"  {label} | Expected: None | Actual: {value!r}")
    if value is not None:
        raise AssertionError(f"Expected None, got {value!r}")

def check_in(item, container, label="Text or collection"):
    print(f"  {label} | Expected to contain: {item!r} | Actual: {container!r}")
    if item not in container:
        raise AssertionError(f"Expected {item!r} in {container!r}")

def check_raises(expected_error, action):
    print(f"  Expected exception: {expected_error.__name__}")
    try:
        action()
    except expected_error as exc:
        print(f"  Actual exception: {type(exc).__name__}: {exc}")
        return
    raise AssertionError(f"Expected {expected_error.__name__} was not raised")


# Shared hardcoded response for the additional validation and booking tests.
SAMPLE_AI = {
    "status": "ok",
    "summary": "Mild seasonal symptoms.",
    "urgency": "non-urgent",
    "severity": "low",
    "recommended_tier": "nurse",
    "specialist": "general_practice",
    "flagged_symptoms": ["sneezing"],
    "history_linked": False,
    "confidence": 0.95,
}


def display_outcome(result):
    """Print the returned fields for a demonstration; checks still decide PASS/FAIL."""
    print("Decision:", result["decision"])
    print("Specialist:", result["specialist"])
    print("Practitioner:", result["assigned_practitioner_type"])
    print("Proposed slot:", result["proposed_slot"])
    print("Flag for review:", result["flag_for_manual_review"])
    print("Status:", result["status"])
    print("Next action:", result["next_action"])


def sample(**changes):
    response = copy.deepcopy(SAMPLE_AI)
    response.update(changes)
    return response

def test_routine_case():
    print("Expected: Routine appointment / Nurse")
    ai_result = {
        "status": "ok",
        "severity": 'low',
        "urgency": 'non-urgent',
        "recommended_tier": "nurse",
        "specialist": "general_practice",
        "history_linked": False,
        "flagged_symptoms": ['mild cough'],
        "summary": 'Mild cough',
    }
    result = logic.evaluate_triage_logic(ai_result)
    display_outcome(result)
    check_equal(result['decision'], 'schedule_non_urgent', label="result['decision']")
    check_equal(result['proposed_slot'], '2030-01-15 09:00', label="result['proposed_slot']")
    check_equal(result['status'], 'TRIAGED', label="result['status']")
    check_false(result['flag_for_manual_review'], label="result['flag_for_manual_review']")
    check_in(result['proposed_slot'], result['next_action'], label="result['proposed_slot']")

def test_high_severity_emergency_without_slot():
    print("Expected: Direct to A&E / Doctor / No appointment")
    ai_result = {
        "status": "ok",
        "severity": 'high',
        "urgency": 'urgent',
        "recommended_tier": "nurse",
        "specialist": "general_practice",
        "history_linked": False,
        "flagged_symptoms": ['chest pain'],
        "summary": 'Severe chest pain',
    }
    result = logic.evaluate_triage_logic(ai_result)
    display_outcome(result)
    check_equal(result['decision'], 'urgent_priority', label="result['decision']")
    check_equal(result['assigned_practitioner_type'], 'doctor', label="result['assigned_practitioner_type']")
    check_none(result['proposed_slot'], label="result['proposed_slot']")
    check_true(result['flag_for_manual_review'], label="result['flag_for_manual_review']")
    check_in('Emergency Department', result['next_action'], label="'Emergency Department'")

def test_urgent_case_escalates_nurse_to_doctor():
    print("Expected: Urgent priority / Doctor / No appointment")
    ai_result = {
        "status": "ok",
        "severity": 'medium',
        "urgency": 'urgent',
        "recommended_tier": "nurse",
        "specialist": "general_practice",
        "history_linked": False,
        "flagged_symptoms": ['fever'],
        "summary": 'Patient requires urgent attention',
    }
    result = logic.evaluate_triage_logic(ai_result)
    display_outcome(result)
    check_equal(result['decision'], 'urgent_priority', label="result['decision']")
    check_equal(result['assigned_practitioner_type'], 'doctor', label="result['assigned_practitioner_type']")
    check_true(result['override_applied'], label="result['override_applied']")
    check_equal(result['status'], 'GUARDRAIL_OVERRIDDEN', label="result['status']")
    check_none(result['proposed_slot'], label="result['proposed_slot']")
    check_in('Emergency Department', result['next_action'], label="'Emergency Department'")

def test_history_linked_specialist_routing():
    result = logic.evaluate_triage_logic(sample(urgency='urgent', severity='medium', history_linked=True, specialist='oncology', recommended_tier='doctor'))
    check_equal(result['decision'], 'route_to_specialist', label="result['decision']")
    check_equal(result['specialist'], 'oncology', label="result['specialist']")
    check_equal(result['proposed_slot'], '2030-01-12 09:00', label="result['proposed_slot']")
    check_false(result['override_applied'], label="result['override_applied']")

def test_keyword_routing():
    for symptoms, urgency, expected in [(['chest pain'], 'non-urgent', 'cardiology'), (['shortness of breath'], 'non-urgent', 'cardiology'), (['headache'], 'non-urgent', 'general_practice'), (['headache', 'blurred vision'], 'non-urgent', 'neurology'), (['headache', 'numbness'], 'non-urgent', 'neurology'), (['rash'], 'urgent', 'dermatology'), (['rash'], 'non-urgent', 'general_practice')]:
        result = logic.evaluate_triage_logic(sample(flagged_symptoms=symptoms, urgency=urgency))
        check_equal(result['specialist'], expected, label="result['specialist']")

def test_failed_or_unusable_ai_requires_manual_review():
    print("Expected: Manual review / No appointment")
    ai_result = {"status": "error"}
    result = logic.evaluate_triage_logic(ai_result)
    display_outcome(result)
    for response in [None, [], 'invalid', {}, {'status': 'error'}, {'status': 'api_failure'}]:
        result = logic.evaluate_triage_logic(response)
        check_equal(result['decision'], 'manual_review', label="result['decision']")
        check_true(result['flag_for_manual_review'], label="result['flag_for_manual_review']")
        check_none(result['proposed_slot'], label="result['proposed_slot']")

def test_evaluation_preserves_input():
    response = sample()
    overrides = [['severity', 'HIGH']]
    before = copy.deepcopy((response, overrides))
    logic.evaluate_triage_logic(response, staff_overrides=overrides)
    check_equal((response, overrides), before, label='(response, overrides)')

def test_staff_department_skips_keyword_routing():
    response = sample(specialist='neurology', flagged_symptoms=['chest pain'])
    result = logic.evaluate_triage_logic(response, staff_overrides=[['department', 'neurology']])
    check_equal(result['specialist'], 'neurology', label="result['specialist']")

def test_staff_risk_changes_routing():
    result = logic.evaluate_triage_logic(sample(), staff_overrides=[['severity', 'high']])
    check_equal(result['decision'], 'urgent_priority', label="result['decision']")
    check_none(result['proposed_slot'], label="result['proposed_slot']")

def test_staff_slot_and_audit_details():
    response = sample(proposed_slot='2030-01-20 14:30')
    overrides = [['proposed_slot', '2030-01-20 14:30']]
    outcome = logic.evaluate_triage_logic(response, staff_overrides=overrides)
    before = copy.deepcopy(outcome)
    result = logic.apply_staff_override(outcome, overrides)
    check_equal(result['proposed_slot'], '2030-01-20 14:30', label="result['proposed_slot']")
    check_true(result['slot_set_by_staff'], label="result['slot_set_by_staff']")
    check_true(result['nurse_override'], label="result['nurse_override']")
    check_equal(result['status'], 'GUARDRAIL_OVERRIDDEN', label="result['status']")
    check_in('proposed_slot: 2030-01-20 14:30', result['override_reason'], label="'proposed_slot: 2030-01-20 14:30'")
    check_in('2030-01-20 14:30', result['next_action'], label="'2030-01-20 14:30'")
    check_equal(outcome, before, label='outcome')


def test_staff_value_normalization():
    for field, values in [('severity', ('low', 'medium', 'high')), ('urgency', ('urgent', 'non-urgent'))]:
        for value in values:
            check_equal(logic.validate_staff_override(field, ' ' + value.upper() + ' '), value, label="logic.validate_staff_override(field, ' ' + value.upper() + ' ')")

def test_invalid_staff_values():
    for field, value in [('severity', 'critical'), ('urgency', 'soon'), ('department', ' '), ('severity', None), ('unknown', 'low')]:
        check_raises(ValueError, lambda: logic.validate_staff_override(field, value))

def test_override_list_validation():
    check_equal(logic.validate_staff_overrides(None), [], label='logic.validate_staff_overrides(None)')
    check_equal(logic.validate_staff_overrides([['severity', ' HIGH ']]), [['severity', 'high']], label="logic.validate_staff_overrides([['severity', ' HIGH ']])")
    for invalid in ['severity', {}, [['severity']], [['severity', 'high', 'extra']]]:
        check_raises(ValueError, lambda: logic.validate_staff_overrides(invalid))

def test_latest_staff_risk_wins():
    response = sample()
    result = logic.apply_staff_risk_values(response, [['severity', 'medium'], ['severity', 'high']])
    check_equal(result['severity'], 'high', label="result['severity']")
    check_equal(response['severity'], 'low', label="response['severity']")

def test_invalid_overrides_rejected_by_logic_entry_points():
    invalid = [['urgency', 'soon']]
    check_raises(ValueError, lambda: logic.evaluate_triage_logic(sample(), staff_overrides=invalid))
    check_raises(ValueError, lambda: logic.apply_staff_override({}, invalid))

def test_manual_slot_validation():
    check_equal(logic.validate_manual_slot(' 2030-01-11 09:00 '), '2030-01-11 09:00', label="logic.validate_manual_slot(' 2030-01-11 09:00 ')")
    for invalid in [None, '', 'tomorrow', '2030-02-30 09:00', '2030-1-11 09:00', '2030-01-09 09:00', '2030-01-10 12:00']:
        check_raises(ValueError, lambda: logic.validate_manual_slot(invalid))


def test_booking_ready_states_and_input_preservation():
    for state in ('TRIAGED', 'GUARDRAIL_OVERRIDDEN'):
        outcome = logic.evaluate_triage_logic(sample())
        outcome['status'] = state
        before = copy.deepcopy(outcome)
        result = logic.schedule_appointment(outcome, '2030-01-15 09:00')
        check_true(result['success'], label="result['success']")
        check_equal(result['outcome']['status'], 'SCHEDULED', label="result['outcome']['status']")
        check_equal(result['outcome']['proposed_slot'], '2030-01-15 09:00', label="result['outcome']['proposed_slot']")
        check_equal(outcome, before, label='outcome')

def test_booking_rejects_unready_states_and_missing_slots():
    for state in ('INITIATED', 'SCHEDULED', 'COMPLETED', None):
        check_false(logic.schedule_appointment({'status': state}, '2030-01-15 09:00')['success'], label="logic.schedule_appointment({'status': state}, '2030-01-15 09:00')['success']")
    for slot in (None, ''):
        check_false(logic.schedule_appointment({'status': 'TRIAGED', 'decision': 'schedule_non_urgent'}, slot)['success'], label="logic.schedule_appointment({'status': 'TRIAGED', 'decision': 'schedule_non_urgent'}, slot)['success']")


def test_red_flag_helper_escalates_without_mutation():
    original = sample()
    before = copy.deepcopy(original)
    result = logic.apply_red_flag_escalation(original, True)
    check_equal(result['severity'], 'high', label="result['severity']")
    check_equal(result['urgency'], 'urgent', label="result['urgency']")
    check_equal(original, before, label='original')
    check_equal(logic.apply_red_flag_escalation(original, False), before, label='logic.apply_red_flag_escalation(original, False)')
    error = {'status': 'error'}
    check_equal(logic.apply_red_flag_escalation(error, True), error, label='logic.apply_red_flag_escalation(error, True)')


def test_red_flags_reach_triage_decision():
    # Intentionally checks the public entry point as main.py calls it.
    # Regression: red flags must reach the actual routing decision.
    result = logic.evaluate_triage_logic(sample(), red_flags_present=True)
    check_equal(result['decision'], 'urgent_priority', label="result['decision']")
    check_none(result['proposed_slot'], label="result['proposed_slot']")
    check_equal(result['assigned_practitioner_type'], 'doctor', label="result['assigned_practitioner_type']")


def test_finalize_real_slot_updates_message_without_mutation():
    original = logic.evaluate_triage_logic(sample())
    before = copy.deepcopy(original)
    result = logic.finalize_proposed_slot(original, '2030-01-20 10:30')
    check_equal(result['proposed_slot'], '2030-01-20 10:30', label="result['proposed_slot']")
    check_in('2030-01-20 10:30', result['next_action'], label="'2030-01-20 10:30'")
    check_equal(original, before, label='original')


def test_no_available_slots_flags_manual_scheduling():
    original = logic.evaluate_triage_logic(sample())
    before = copy.deepcopy(original)
    result = logic.mark_no_slots_available(original)
    check_none(result['proposed_slot'], label="result['proposed_slot']")
    check_true(result['flag_for_manual_review'], label="result['flag_for_manual_review']")
    check_in('manual scheduling', result['next_action'], label="'manual scheduling'")
    check_equal(original, before, label='original')


def history_entry(slot, status='SCHEDULED'):
    return {
        'timestamp': '2030-01-01T12:00:00',
        'symptom_description': 'Synthetic report',
        'outcome': {'status': status, 'proposed_slot': slot,
                    'specialist': 'general_practice',
                    'assigned_practitioner_type': 'nurse'},
    }


def test_upcoming_appointments_filter_sort_and_preserve_record():
    record = {'history': [
        history_entry('2030-01-20 09:00'),
        history_entry('2030-01-11 09:00'),
        history_entry('2030-01-09 09:00'),
        history_entry('2030-01-12 09:00', 'TRIAGED'),
        history_entry('invalid'), None, {},
    ]}
    before = copy.deepcopy(record)
    results = logic.get_upcoming_appointments(record, now=FIXED_NOW)
    check_equal([item['slot'] for item in results],
                ['2030-01-11 09:00', '2030-01-20 09:00'], label="[item['slot'] for item in results]")
    check_equal(results[0]['department'], 'general practice', label="results[0]['department']")
    check_equal(results[0]['reason'], 'Synthetic report', label="results[0]['reason']")
    check_equal(results[0]['practitioner'], 'nurse', label="results[0]['practitioner']")
    check_equal(record, before, label='record')


def test_upcoming_appointments_empty_inputs_and_time_boundary():
    for value in [None, [], {}, {'history': []}]:
        check_equal(logic.get_upcoming_appointments(value), [], label='logic.get_upcoming_appointments(value)')
    record = {'history': [history_entry('2030-01-10 12:00')]}
    check_equal(len(logic.get_upcoming_appointments(record)), 1, label='len(logic.get_upcoming_appointments(record))')


def test_repeatable_results_with_fixed_input_and_time():
    first = logic.evaluate_triage_logic(sample())
    second = logic.evaluate_triage_logic(sample())
    check_equal(first, second, label='first')
    check_equal(first['routing_timestamp'], '2030-01-10T12:00:00', label="first['routing_timestamp']")


def test_staff_department_wins_over_different_ai_value():
    response = sample(specialist='general_practice', flagged_symptoms=['chest pain'])
    result = logic.evaluate_triage_logic(response, staff_overrides=[['department', 'Neurology']])
    check_equal(result['specialist'], 'neurology', label="result['specialist']")
    check_equal(result['decision'], 'route_to_specialist', label="result['decision']")
    check_in('neurology', result['next_action'], label="'neurology'")


def test_staff_slot_wins_over_different_or_missing_ai_slot():
    for response in (sample(), sample(proposed_slot='2030-01-20 14:30')):
        overrides = [['proposed_slot', '2030-01-21 14:30']]
        outcome = logic.evaluate_triage_logic(response, staff_overrides=overrides)
        result = logic.apply_staff_override(outcome, overrides)
        for value in (outcome, result):
            check_equal(value['proposed_slot'], '2030-01-21 14:30', label="value['proposed_slot']")
            check_true(value['slot_set_by_staff'], label="value['slot_set_by_staff']")
            check_in('2030-01-21 14:30', value['next_action'], label="'2030-01-21 14:30'")


def test_staff_slot_validation_at_entry_points():
    for slot in ('tomorrow', '2030-02-30 09:00', '2030-01-09 09:00'):
        overrides = [['proposed_slot', slot]]
        check_raises(ValueError, lambda: logic.validate_staff_override('proposed_slot', slot))
        check_raises(ValueError, lambda: logic.evaluate_triage_logic(sample(), staff_overrides=overrides))
        check_raises(ValueError, lambda: logic.apply_staff_override({}, overrides))


def test_emergency_never_gets_staff_slot():
    overrides = [['proposed_slot', '2030-01-21 14:30']]
    for severity in ('medium', 'high'):
        response = sample(severity=severity, urgency='urgent')
        outcome = logic.evaluate_triage_logic(response, staff_overrides=overrides)
        result = logic.apply_staff_override(outcome, overrides)
        for value in (outcome, result):
            check_equal(value['decision'], 'urgent_priority', label="value['decision']")
            check_none(value['proposed_slot'], label="value['proposed_slot']")
            check_false(value['slot_set_by_staff'], label="value['slot_set_by_staff']")
            check_in('Emergency Department', value['next_action'], label="'Emergency Department'")


def test_booking_rejects_invalid_dates():
    outcome = logic.evaluate_triage_logic(sample())
    before = copy.deepcopy(outcome)
    for slot in ('not-a-date', '2030-02-30 09:00', '2030-01-09 09:00',
                 '2030-01-10 12:00', 123):
        check_false(logic.schedule_appointment(outcome, slot)['success'], label="logic.schedule_appointment(outcome, slot)['success']")
    check_equal(outcome, before, label='outcome')


def test_booking_rejects_unresolved_review_and_emergency():
    for outcome in (logic.evaluate_triage_logic({'status': 'error'}),
                    logic.evaluate_triage_logic(sample(severity='high'))):
        for status in ('TRIAGED', 'GUARDRAIL_OVERRIDDEN'):
            outcome['status'] = status
            check_false(logic.schedule_appointment(outcome, '2030-01-21 14:30')['success'], label="logic.schedule_appointment(outcome, '2030-01-21 14:30')['success']")
    check_false(logic.schedule_appointment(None, '2030-01-21 14:30')['success'], label="logic.schedule_appointment(None, '2030-01-21 14:30')['success']")


def test_staff_risk_values_after_red_flags():
    # Preserve the existing staff-review policy: explicit risk values are applied last.
    overrides = [['severity', 'low'], ['urgency', 'non-urgent']]
    result = logic.evaluate_triage_logic(sample(), red_flags_present=True,
                                         staff_overrides=overrides)
    check_equal(result['decision'], 'schedule_non_urgent', label="result['decision']")
    # Changing urgency alone does not erase the remaining high severity.
    result = logic.evaluate_triage_logic(sample(), red_flags_present=True,
                                         staff_overrides=[['urgency', 'non-urgent']])
    check_equal(result['decision'], 'urgent_priority', label="result['decision']")
    check_none(result['proposed_slot'], label="result['proposed_slot']")


def test_finalize_slot_rejects_non_booking_decisions():
    for response in ({'status': 'error'}, sample(severity='high')):
        outcome = logic.evaluate_triage_logic(response)
        check_raises(ValueError, lambda: logic.finalize_proposed_slot(outcome, '2030-01-21 14:30'))


def test_chest_pain_routes_to_cardiology():
    print("Expected: Cardiology / Doctor")
    # Synthetic input exercises keyword routing; red_flags_present defaults to False.
    ai_result = {
        "status": "ok",
        "severity": 'low',
        "urgency": "non-urgent",
        "recommended_tier": "nurse",
        "specialist": "general_practice",
        "history_linked": False,
        "flagged_symptoms": ['chest pain'],
        "summary": 'Mild chest discomfort',
    }
    result = logic.evaluate_triage_logic(ai_result)
    display_outcome(result)
    check_equal(result["decision"], "route_to_specialist", label='result["decision"]')
    check_equal(result["specialist"], 'cardiology', label='result["specialist"]')
    check_equal(result["assigned_practitioner_type"], "doctor", label='result["assigned_practitioner_type"]')


def test_headache_and_vision_routes_to_neurology():
    print("Expected: Neurology / Doctor")
    # Synthetic input exercises keyword routing; red_flags_present defaults to False.
    ai_result = {
        "status": "ok",
        "severity": 'medium',
        "urgency": "non-urgent",
        "recommended_tier": "nurse",
        "specialist": "general_practice",
        "history_linked": False,
        "flagged_symptoms": ['headache', 'vision problems'],
        "summary": 'Headache with vision problems',
    }
    result = logic.evaluate_triage_logic(ai_result)
    display_outcome(result)
    check_equal(result["decision"], "route_to_specialist", label='result["decision"]')
    check_equal(result["specialist"], 'neurology', label='result["specialist"]')
    check_equal(result["assigned_practitioner_type"], "doctor", label='result["assigned_practitioner_type"]')

def test_follow_up_questions_asked_for_non_urgent_case():
    # Low severity and non-urgent with no red flags: ask follow-up questions.
    check_true(logic.needs_follow_up_questions(sample(), False), label="needs_follow_up_questions(non-urgent, no red flags)")


def test_follow_up_questions_skipped_for_red_flags_urgent_or_high_severity():
    # Any escalation signal skips the questions so urgent care is not delayed.
    check_false(logic.needs_follow_up_questions(sample(), True), label="needs_follow_up_questions(red flags)")
    check_false(logic.needs_follow_up_questions(sample(urgency='urgent'), False), label="needs_follow_up_questions(urgent)")
    check_false(logic.needs_follow_up_questions(sample(severity='high'), False), label="needs_follow_up_questions(high severity)")


def test_follow_up_questions_skipped_for_unusable_ai_result():
    for response in (None, [], 'invalid'):
        check_false(logic.needs_follow_up_questions(response, False), label="needs_follow_up_questions(unusable response)")


def test_department_override_is_validated_and_normalized():
    check_equal(logic.validate_staff_override('department', ' Cardiology '), 'cardiology', label="validate_staff_override('department', ' Cardiology ')")
    check_equal(logic.validate_staff_override('department', 'general practice'), 'general_practice', label="validate_staff_override('department', 'general practice')")
    for invalid in ('asdf', 'cardio', 'general', '123'):
        check_raises(ValueError, lambda: logic.validate_staff_override('department', invalid))


def test_unknown_department_rejected_by_logic_entry_points():
    invalid = [['department', 'asdf']]
    check_raises(ValueError, lambda: logic.evaluate_triage_logic(sample(), staff_overrides=invalid))
    check_raises(ValueError, lambda: logic.apply_staff_override({}, invalid))


def test_department_override_does_not_change_ai_risk_fields():
    response = sample()
    result = logic.apply_staff_risk_values(response, [['department', 'neurology']])
    check_equal(result, response, label='result')
    check_false('department' in result, label="'department' in result")


def run_tests():
    tests = sorted((name, fn) for name, fn in globals().items()
                   if name.startswith('test_') and callable(fn))
    first_cases = ['test_routine_case', 'test_urgent_case_escalates_nurse_to_doctor', 'test_high_severity_emergency_without_slot', 'test_chest_pain_routes_to_cardiology', 'test_headache_and_vision_routes_to_neurology', 'test_failed_or_unusable_ai_requires_manual_review']
    tests.sort(key=lambda item: (first_cases.index(item[0])
                                if item[0] in first_cases else len(first_cases), item[0]))
    print("Clinical Triage Automated Tests")
    print("Logic manager loaded from:", logic.__file__)
    print("Fixed test time:", FIXED_NOW)
    print("All cases run automatically. No live API is called.")
    passed = failed = errors = 0
    original_datetime = logic.datetime
    # A module holds the two clock functions the production module needs.
    # Only this process's clock reference is replaced, never source files.
    clock = ModuleType('fixed_test_clock')
    clock.now = fixed_now
    clock.strptime = datetime.strptime
    logic.datetime = clock
    try:
        for number, (name, function) in enumerate(tests, start=1):
            title = name[5:].replace("_", " ").title()
            print(f"\n--- Test Case {number}: {title} ---")
            try:
                function()
            except AssertionError as exc:
                failed += 1
                print(f'FAIL  {name}: {exc}')
            except Exception as exc:
                errors += 1
                print(f'ERROR {name}: {type(exc).__name__}: {exc}')
            else:
                passed += 1
                print(f'PASS  {name}')
    finally:
        logic.datetime = original_datetime
    print(f'\nTotal: {len(tests)} | Passed: {passed} | Failed: {failed} | Errors: {errors}')
    return 1 if failed or errors else 0


if __name__ == '__main__':
    sys.exit(run_tests())