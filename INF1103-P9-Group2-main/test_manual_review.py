"""Offline integration tests: python -m unittest test_manual_review -v"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from managers import data_manager, logic_manager

spec = importlib.util.spec_from_file_location('manual_review_app', Path(__file__).with_name('main.py'))
app = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, {'managers.ai_manager': types.ModuleType('managers.ai_manager')}):
    spec.loader.exec_module(app)


class ManualReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = patch.object(data_manager, 'DATA_DIR', self.temp.name)
        self.directory.start()
        self.addCleanup(self.directory.stop)
        app.data_manager = data_manager
        app.ai_manager = Mock()
        app.ai_manager.get_ai_triage_result.return_value = {'status': 'error', 'error_reason': 'Test outage'}
        app.io_manager = Mock()
        app.io_manager.prompt_issue_type.return_value = 'new'
        app.io_manager.prompt_symptom_description.return_value = 'Original patient report'
        self.patient = 'S1234567A'

    def submit(self):
        app.run_patient_submission(self.patient)
        return data_manager.load_pending_case(self.patient)

    def review(self, decision):
        app.io_manager.prompt_select_case.return_value = data_manager.list_pending_cases('awaiting_staff')[0]
        app.io_manager.prompt_manual_review_decision.return_value = decision
        app.io_manager.prompt_staff_confirm_correct.return_value = True
        app.run_staff_review({'staff_id': 'STF0001'})

    def test_error_saved_and_listed_without_summary_confirmation(self):
        case = self.submit()
        self.assertEqual(case['symptom_description'], 'Original patient report')
        self.assertEqual(case['stage'], 'awaiting_staff')
        self.assertEqual(case['ai_result']['status'], 'error')
        self.assertEqual(case['outcome']['decision'], 'manual_review')
        self.assertTrue(case['outcome']['flag_for_manual_review'])
        self.assertIsNone(case['outcome']['proposed_slot'])
        self.assertEqual(len(data_manager.list_pending_cases('awaiting_staff')), 1)
        app.io_manager.display_ai_summary_and_confirm.assert_not_called()

    def test_api_failure_status_supported(self):
        app.ai_manager.get_ai_triage_result.return_value = {'status': 'api_failure'}
        self.assertEqual(self.submit()['outcome']['decision'], 'manual_review')

    def test_raised_exception_still_saved_without_exception_details(self):
        app.ai_manager.get_ai_triage_result.side_effect = RuntimeError('sensitive setup detail')
        case = self.submit()
        self.assertEqual(case['outcome']['decision'], 'manual_review')
        self.assertNotIn('sensitive', str(case))

    def test_non_dict_response_still_saved(self):
        app.ai_manager.get_ai_triage_result.return_value = None
        self.assertEqual(self.submit()['outcome']['decision'], 'manual_review')

    def test_successful_ai_flow_still_confirms_summary(self):
        app.ai_manager.get_ai_triage_result.return_value = {'status': 'ok', 'summary': 'Example'}
        app.io_manager.display_ai_summary_and_confirm.return_value = True
        case = self.submit()
        self.assertIsNone(case['outcome'])
        app.io_manager.display_ai_summary_and_confirm.assert_called_once()

    def test_save_failure_does_not_claim_success(self):
        with patch.object(data_manager, 'save_pending_case', return_value=False):
            self.submit()
        app.io_manager.display_error.assert_called_once()
        messages = str(app.io_manager.display_message.call_args_list)
        self.assertNotIn('has been saved', messages)
        self.assertIsNone(data_manager.load_pending_case(self.patient))

    def test_duplicate_submission_preserves_pending_case(self):
        case = self.submit()
        self.assertEqual(self.submit(), case)
        app.ai_manager.get_ai_triage_result.assert_called_once()

    def test_deferred_manual_case_stays_queued_without_ai_call(self):
        before = self.submit()
        self.review('manual_review')
        self.assertEqual(data_manager.load_pending_case(self.patient), before)
        app.ai_manager.get_ai_override_result.assert_not_called()

    def test_routine_manual_review_to_booking_and_history(self):
        self.submit()
        app.io_manager.prompt_nonempty_text.side_effect = ['Staff assessment notes', 'bad date', '2099-10-20 09:00']
        app.io_manager.prompt_override_verdict.return_value = 'general_practice'
        self.review('schedule_non_urgent')
        case = data_manager.load_pending_case(self.patient)
        self.assertEqual(case['stage'], 'awaiting_patient')
        self.assertEqual(case['ai_result']['status'], 'error')
        self.assertEqual(case['review_source'], 'staff_manual')
        app.io_manager.display_error.assert_called_once()
        app.ai_manager.get_ai_override_result.assert_not_called()
        app.io_manager.prompt_patient_accept_proposal.return_value = True
        app.run_patient_response(self.patient)
        self.assertIsNone(data_manager.load_pending_case(self.patient))
        record = data_manager.load_patient(self.patient)['history'][-1]
        self.assertEqual(record['outcome']['status'], 'SCHEDULED')
        self.assertEqual(record['review_source'], 'staff_manual')
        self.assertEqual(record['reviewed_by'], 'STF0001')
        self.assertEqual(record['outcome']['override_reason'], 'Staff assessment notes')

    def test_urgent_manual_review_has_no_booking(self):
        self.submit()
        app.io_manager.prompt_nonempty_text.return_value = 'Staff emergency decision'
        self.review('urgent_priority')
        self.assertIsNone(data_manager.load_pending_case(self.patient)['outcome']['proposed_slot'])
        app.run_patient_response(self.patient)
        app.io_manager.prompt_patient_accept_proposal.assert_not_called()
        self.assertEqual(data_manager.load_patient(self.patient)['history'][-1]['patient_decision'], 'acknowledged')

    def test_failed_review_save_keeps_original_queue_entry(self):
        before = self.submit()
        app.io_manager.prompt_nonempty_text.return_value = 'Staff emergency decision'
        with patch.object(data_manager, 'save_pending_case', return_value=False):
            self.review('urgent_priority')
        self.assertEqual(data_manager.load_pending_case(self.patient), before)
        app.io_manager.display_error.assert_called_once()

    def test_manual_slot_and_reason_validation(self):
        for invalid in ('2000-01-01 09:00', '2099-02-30 09:00', 'tomorrow', ''):
            with self.subTest(slot=invalid), self.assertRaises(ValueError):
                logic_manager.validate_manual_slot(invalid)
        with self.assertRaises(ValueError):
            logic_manager.build_manual_review_outcome('urgent_priority', '')
        outcome = logic_manager.build_manual_review_outcome('route_to_specialist', 'Staff notes', 'cardiology', '2099-10-20 09:00')
        self.assertEqual(outcome['specialist'], 'cardiology')
        self.assertEqual(outcome['assigned_practitioner_type'], 'doctor')


if __name__ == '__main__':
    unittest.main()
