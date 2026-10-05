"""Offline tests: run from the project root with python -m unittest test_staff_validation -v."""
import contextlib
import copy
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch, Mock

from managers import logic_manager as logic
from managers import io_manager as prompts


AI_RESULT = {
    'status': 'ok', 'summary': 'Example report', 'severity': 'low',
    'urgency': 'non-urgent', 'specialist': 'general_practice',
    'recommended_tier': 'nurse', 'flagged_symptoms': [], 'history_linked': False,
}


class StaffValidationTests(unittest.TestCase):
    def test_allowed_values_and_normalization(self):
        for field, options in logic.STAFF_OVERRIDE_OPTIONS.items():
            for value in options:
                with self.subTest(field=field, value=value):
                    self.assertEqual(logic.validate_staff_override(field, ' ' + value.upper() + ' '), value)

    def test_invalid_values(self):
        for field in ('severity', 'urgency'):
            for value in ('', ' ', 'critical', 'very urgent', '1', None, 3, [], True):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValueError):
                        logic.validate_staff_override(field, value)

    def test_malformed_overrides(self):
        for value in ('severity', {}, [['severity']], [['severity', 'low', 'extra']],
                      ['severity'], [['unknown', 'low']], [[[], 'low']]):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    logic.validate_staff_overrides(value)

    def test_other_fields_keep_text_support(self):
        self.assertEqual(logic.validate_staff_override('department', ' Cardiology '), 'Cardiology')
        self.assertEqual(logic.validate_staff_override('proposed_slot', ' next month '), 'next month')
        self.assertEqual(logic.validate_staff_overrides(None), [])

    def test_latest_staff_value_wins_without_mutation(self):
        overrides = [['severity', 'medium'], ['severity', 'HIGH'], ['urgency', 'urgent']]
        before = copy.deepcopy(overrides)
        result = logic.apply_staff_risk_values(AI_RESULT, overrides)
        self.assertEqual(result['severity'], 'high')
        self.assertEqual(result['urgency'], 'urgent')
        self.assertEqual(AI_RESULT['severity'], 'low')
        self.assertEqual(overrides, before)

    def test_evaluation_enforces_staff_value(self):
        outcome = logic.evaluate_triage_logic(AI_RESULT, staff_overrides=[['severity', 'high']])
        self.assertEqual(outcome['decision'], 'urgent_priority')
        self.assertIsNone(outcome['proposed_slot'])

    def test_logic_entry_points_reject_invalid_values(self):
        invalid = [['severity', 'critical']]
        with self.assertRaises(ValueError):
            logic.evaluate_triage_logic(AI_RESULT, staff_overrides=invalid)
        with self.assertRaises(ValueError):
            logic.apply_staff_override({}, AI_RESULT, invalid)

    def test_fixed_choice_reprompts(self):
        with patch('builtins.input', side_effect=['critical', '', ' HIGH ']) as read:
            with contextlib.redirect_stdout(io.StringIO()) as output:
                verdict = prompts.prompt_override_verdict('severity', logic.STAFF_OVERRIDE_OPTIONS['severity'])
        self.assertEqual(verdict, 'high')
        self.assertEqual(read.call_count, 3)
        self.assertIn('low/medium/high', output.getvalue())

    def test_staff_review_rejects_before_ai_and_saves_exact_selection(self):
        # Load main with fake AI/data managers: no API calls or patient files.
        fake_ai = types.ModuleType('managers.ai_manager')
        fake_data = types.ModuleType('managers.data_manager')
        spec = importlib.util.spec_from_file_location('validation_main', Path(__file__).with_name('main.py'))
        app = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'managers.ai_manager': fake_ai, 'managers.data_manager': fake_data}):
            spec.loader.exec_module(app)
        case = {'ai_result': dict(AI_RESULT), 'symptom_description': 'Example report'}
        app.ai_manager = Mock()
        app.ai_manager.get_ai_override_result.return_value = dict(AI_RESULT)
        app.data_manager = Mock()
        app.data_manager.list_pending_cases.return_value = [('TEST', case)]
        app.data_manager.load_patient.return_value = None
        app.data_manager.save_pending_case.return_value = True
        app.io_manager = Mock()
        app.io_manager.prompt_select_case.return_value = ('TEST', case)
        app.io_manager.prompt_staff_accept.return_value = False
        app.io_manager.prompt_override_field.return_value = 'severity'
        app.io_manager.prompt_override_verdict.side_effect = ['critical', ' HIGH ']
        app.io_manager.prompt_staff_confirm_correct.return_value = True
        app.run_staff_review({'staff_id': 'STF0001'})
        app.io_manager.display_error.assert_called_once()
        app.ai_manager.get_ai_override_result.assert_called_once()
        self.assertEqual(case['staff_overrides'], [['severity', 'high']])
        self.assertEqual(case['ai_result']['severity'], 'high')
        self.assertEqual(case['outcome']['decision'], 'urgent_priority')
        self.assertEqual(case['stage'], 'awaiting_patient')
        app.data_manager.save_pending_case.assert_called_once()


if __name__ == '__main__':
    unittest.main()
