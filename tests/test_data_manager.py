"""Automated tests for data_manager. Run from the project root:

    python -m unittest tests.test_data_manager -v

Every test runs against a throwaway temp folder, so the real data/ folder
is never touched. No API key or internet connection is needed.
"""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from managers import data_manager as dm


class DataManagerTestCase(unittest.TestCase):
    """Base class: points data_manager at a temp folder for each test."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)  # folder deleted even if the test fails
        self.root = temp.name

        for name, value in (
            ("DATA_DIR", os.path.join(self.root, "patients")),
            ("STAFF_DIR", os.path.join(self.root, "staff")),
            ("SLOTS_FILE", os.path.join(self.root, "appointment_slots.json")),
        ):
            patcher = patch.object(dm, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def patient_path(self, patient_id):
        return os.path.join(dm.DATA_DIR, f"{patient_id}.json")

    def staff_path(self, staff_id):
        return os.path.join(dm.STAFF_DIR, f"{staff_id}.json")

    def write_raw(self, path, text):
        """Write text straight to disk, bypassing data_manager."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def make_patient(self, patient_id, history=None):
        record = {"patient_id": patient_id, "history": history or []}
        dm.save_patient(patient_id, record)
        return record

    def make_staff(self, staff_id, name="Test Staff", role="nurse"):
        record = {"staff_id": staff_id, "name": name, "role": role,
                  "created_at": "2030-01-01T00:00:00"}
        dm.save_staff(record)
        return record


# ===========================================================================
# Part 1: patient records
# ===========================================================================

class TestPatientRecords(DataManagerTestCase):

    def test_missing_file_returns_none(self):
        self.assertIsNone(dm.load_patient("S1234567A"))

    def test_save_then_load_round_trip(self):
        record = {"patient_id": "S1234567A", "history": [{"symptom_description": "cough"}]}
        self.assertTrue(dm.save_patient("S1234567A", record))
        self.assertEqual(dm.load_patient("S1234567A"), record)

    def test_patient_id_is_normalised(self):
        record = {"patient_id": "S1234567A", "history": []}
        dm.save_patient(" s1234567a ", record)
        self.assertTrue(os.path.exists(self.patient_path("S1234567A")))
        self.assertEqual(dm.load_patient("S1234567A"), record)

    def test_corrupt_json_is_quarantined(self):
        path = self.patient_path("S1234567A")
        self.write_raw(path, "{ this is not json")
        self.assertIsNone(dm.load_patient("S1234567A"))
        self.assertFalse(os.path.exists(path))
        self.assertTrue(os.path.exists(path + ".corrupt"))

    def test_empty_file_is_treated_as_corrupt(self):
        path = self.patient_path("S1234567A")
        self.write_raw(path, "")
        self.assertIsNone(dm.load_patient("S1234567A"))
        self.assertTrue(os.path.exists(path + ".corrupt"))

    def test_wrong_shape_is_rejected_and_quarantined(self):
        bad_shapes = [
            [],                                              # a list, not a dict
            {"patient_id": "S1234567A"},                     # no history key
            {"patient_id": "S1234567A", "history": "none"},  # history not a list
        ]
        path = self.patient_path("S1234567A")
        for bad in bad_shapes:
            with self.subTest(bad=bad):
                self.write_raw(path, json.dumps(bad))
                self.assertIsNone(dm.load_patient("S1234567A"))
                self.assertFalse(os.path.exists(path))
                os.remove(path + ".corrupt")  # reset for the next shape

    def test_no_temp_file_left_after_save(self):
        dm.save_patient("S1234567A", {"patient_id": "S1234567A", "history": []})
        leftovers = [n for n in os.listdir(dm.DATA_DIR) if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_save_failure_returns_false_instead_of_crashing(self):
        with patch("managers.data_manager.os.replace", side_effect=OSError("disk full")):
            result = dm.save_patient("S1234567A", {"patient_id": "S1234567A", "history": []})
        self.assertFalse(result)
        self.assertFalse(os.path.exists(self.patient_path("S1234567A")))

    def test_save_overwrites_existing_record(self):
        dm.save_patient("S1234567A", {"patient_id": "S1234567A", "history": []})
        updated = {"patient_id": "S1234567A", "history": [{"symptom_description": "fever"}]}
        dm.save_patient("S1234567A", updated)
        self.assertEqual(dm.load_patient("S1234567A"), updated)

    def test_load_creates_data_folder_if_missing(self):
        self.assertFalse(os.path.exists(dm.DATA_DIR))
        dm.load_patient("S1234567A")
        self.assertTrue(os.path.isdir(dm.DATA_DIR))


# ===========================================================================
# Part 2: pending cases and loading all patients
# ===========================================================================

class TestPendingCases(DataManagerTestCase):

    def test_pending_case_creates_record_for_new_patient(self):
        case = {"stage": "awaiting_staff"}
        self.assertTrue(dm.save_pending_case("S1234567A", case))
        record = dm.load_patient("S1234567A")
        self.assertEqual(record["history"], [])
        self.assertEqual(record["pending_case"], case)
        self.assertEqual(record["patient_id"], "S1234567A")

    def test_pending_case_keeps_existing_history(self):
        history = [{"symptom_description": "cough"}]
        self.make_patient("S1234567A", history)
        dm.save_pending_case("S1234567A", {"stage": "awaiting_staff"})
        self.assertEqual(dm.load_patient("S1234567A")["history"], history)

    def test_load_pending_case_none_without_patient(self):
        self.assertIsNone(dm.load_pending_case("S1234567A"))

    def test_load_pending_case_none_when_no_open_case(self):
        self.make_patient("S1234567A")
        self.assertIsNone(dm.load_pending_case("S1234567A"))

    def test_stage_filter_returns_only_matching_cases(self):
        staff_case = {"stage": "awaiting_staff"}
        patient_case = {"stage": "awaiting_patient"}
        dm.save_pending_case("S1111111A", staff_case)
        dm.save_pending_case("S2222222B", patient_case)
        self.assertEqual(dm.list_pending_cases("awaiting_staff"), [["S1111111A", staff_case]])
        self.assertEqual(dm.list_pending_cases("awaiting_patient"), [["S2222222B", patient_case]])

    def test_list_without_stage_returns_all_open_cases(self):
        dm.save_pending_case("S1111111A", {"stage": "awaiting_staff"})
        dm.save_pending_case("S2222222B", {"stage": "awaiting_patient"})
        self.make_patient("S3333333C")  # no open case: must not be listed
        self.assertEqual(len(dm.list_pending_cases()), 2)

    def test_close_case_moves_it_into_history(self):
        dm.save_pending_case("S1234567A", {"stage": "awaiting_patient"})
        self.assertTrue(dm.close_pending_case("S1234567A", {"patient_decision": "accepted"}))
        record = dm.load_patient("S1234567A")
        self.assertEqual(len(record["history"]), 1)
        self.assertEqual(record["history"][0]["patient_decision"], "accepted")
        self.assertIn("timestamp", record["history"][0])
        self.assertNotIn("pending_case", record)

    def test_close_case_keeps_existing_timestamp(self):
        dm.save_pending_case("S1234567A", {"stage": "awaiting_patient"})
        dm.close_pending_case("S1234567A", {"timestamp": "2030-01-01T00:00:00"})
        record = dm.load_patient("S1234567A")
        self.assertEqual(record["history"][0]["timestamp"], "2030-01-01T00:00:00")

    def test_close_case_unknown_patient_returns_false(self):
        self.assertFalse(dm.close_pending_case("S9999999Z", {"patient_decision": "accepted"}))

    def test_close_case_does_not_modify_callers_dict(self):
        dm.save_pending_case("S1234567A", {"stage": "awaiting_patient"})
        final = {"patient_decision": "accepted"}
        before = copy.deepcopy(final)
        dm.close_pending_case("S1234567A", final)
        self.assertEqual(final, before)

    def test_load_all_skips_corrupt_files(self):
        for pid in ("S1111111A", "S2222222B", "S3333333C"):
            self.make_patient(pid)
        corrupt = self.patient_path("S4444444D")
        self.write_raw(corrupt, "not json")
        patients = dm.load_all_patients()
        self.assertEqual([p["patient_id"] for p in patients],
                         ["S1111111A", "S2222222B", "S3333333C"])
        self.assertTrue(os.path.exists(corrupt + ".corrupt"))

    def test_load_all_ignores_quarantined_files(self):
        self.make_patient("S1111111A")
        self.write_raw(self.patient_path("S2222222B") + ".corrupt", "old bad data")
        self.assertEqual(len(dm.load_all_patients()), 1)

    def test_load_all_on_empty_folder_returns_empty_list(self):
        self.assertEqual(dm.load_all_patients(), [])


# ===========================================================================
# Part 3: staff accounts
# ===========================================================================

class TestStaffAccounts(DataManagerTestCase):

    def test_load_staff_missing_returns_none(self):
        self.assertIsNone(dm.load_staff("STF0001"))

    def test_load_staff_corrupt_is_quarantined(self):
        path = self.staff_path("STF0001")
        self.write_raw(path, "{ broken")
        self.assertIsNone(dm.load_staff("STF0001"))
        self.assertTrue(os.path.exists(path + ".corrupt"))

    def test_load_staff_without_staff_id_is_rejected(self):
        path = self.staff_path("STF0001")
        self.write_raw(path, json.dumps({"name": "No Id", "role": "nurse"}))
        self.assertIsNone(dm.load_staff("STF0001"))
        self.assertTrue(os.path.exists(path + ".corrupt"))

    def test_staff_round_trip(self):
        record = self.make_staff("STF0001", name="Alice", role="doctor")
        self.assertEqual(dm.load_staff("STF0001"), record)

    def test_staff_id_lookup_is_case_insensitive(self):
        record = self.make_staff("STF0001")
        self.assertEqual(dm.load_staff(" stf0001 "), record)

    def test_list_staff_is_sorted_by_id(self):
        self.make_staff("STF0002")
        self.make_staff("STF0001")
        self.assertEqual([r["staff_id"] for r in dm.list_staff()], ["STF0001", "STF0002"])

    def test_list_staff_skips_corrupt_files(self):
        self.make_staff("STF0001")
        self.write_raw(self.staff_path("STF0002"), "garbage")
        self.assertEqual([r["staff_id"] for r in dm.list_staff()], ["STF0001"])

    def test_next_staff_id_when_empty(self):
        self.assertEqual(dm.next_staff_id(), "STF0001")

    def test_next_staff_id_after_existing_accounts(self):
        self.make_staff("STF0001")
        self.make_staff("STF0002")
        self.assertEqual(dm.next_staff_id(), "STF0003")

    def test_next_staff_id_uses_highest_number_when_gap(self):
        self.make_staff("STF0001")
        self.make_staff("STF0005")
        self.assertEqual(dm.next_staff_id(), "STF0006")

    def test_next_staff_id_ignores_unrelated_files(self):
        self.make_staff("STF0001")
        for junk in ("notes.txt", "STFabcd.json", "README"):
            self.write_raw(os.path.join(dm.STAFF_DIR, junk), "x")
        self.assertEqual(dm.next_staff_id(), "STF0002")

    def test_next_staff_id_never_reuses_quarantined_id(self):
        # Regression test for the fix: STF0003 went corrupt and was renamed.
        self.make_staff("STF0001")
        self.write_raw(self.staff_path("STF0003") + ".corrupt", "old bad data")
        self.assertEqual(dm.next_staff_id(), "STF0004")

    def test_save_staff_failure_returns_false(self):
        record = {"staff_id": "STF0001", "name": "A", "role": "nurse"}
        with patch("managers.data_manager.os.replace", side_effect=OSError("disk full")):
            self.assertFalse(dm.save_staff(record))


# ===========================================================================
# Part 4: appointment slots
# ===========================================================================

AVAILABLE = {"status": "available", "patient_id": None}
BOOKED = {"status": "booked", "patient_id": "S9999999Z"}


class TestAppointmentSlots(DataManagerTestCase):

    def make_calendar(self, calendar):
        dm.save_slots(calendar)

    def test_missing_calendar_returns_empty_dict(self):
        self.assertEqual(dm.load_slots(), {})

    def test_corrupt_calendar_returns_empty_dict_and_is_quarantined(self):
        self.write_raw(dm.SLOTS_FILE, "{ broken")
        self.assertEqual(dm.load_slots(), {})
        self.assertTrue(os.path.exists(dm.SLOTS_FILE + ".corrupt"))

    def test_calendar_with_wrong_type_returns_empty_dict(self):
        self.write_raw(dm.SLOTS_FILE, json.dumps(["2030-01-10 09:00"]))
        self.assertEqual(dm.load_slots(), {})
        self.assertTrue(os.path.exists(dm.SLOTS_FILE + ".corrupt"))

    def test_is_slot_available(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE, "2030-01-10 09:30": BOOKED})
        self.assertTrue(dm.is_slot_available("2030-01-10 09:00"))
        self.assertFalse(dm.is_slot_available("2030-01-10 09:30"))  # booked
        self.assertFalse(dm.is_slot_available("2030-01-10 10:00"))  # not in calendar

    def test_next_available_returns_earliest_slot(self):
        self.make_calendar({
            "2030-01-10 10:00": AVAILABLE,
            "2030-01-10 09:30": AVAILABLE,
            "2030-01-11 09:00": AVAILABLE,
        })
        self.assertEqual(dm.find_next_available_slot(datetime(2030, 1, 10, 9, 0)),
                         "2030-01-10 09:30")

    def test_next_available_skips_booked_slots(self):
        self.make_calendar({"2030-01-10 09:00": BOOKED, "2030-01-10 09:30": AVAILABLE})
        self.assertEqual(dm.find_next_available_slot(datetime(2030, 1, 10, 9, 0)),
                         "2030-01-10 09:30")

    def test_next_available_includes_the_earliest_time_itself(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE})
        self.assertEqual(dm.find_next_available_slot(datetime(2030, 1, 10, 9, 0)),
                         "2030-01-10 09:00")

    def test_next_available_ignores_slots_before_earliest(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE})
        self.assertIsNone(dm.find_next_available_slot(datetime(2030, 1, 10, 9, 30)))

    def test_next_available_none_when_everything_booked(self):
        self.make_calendar({"2030-01-10 09:00": BOOKED})
        self.assertIsNone(dm.find_next_available_slot(datetime(2030, 1, 1, 0, 0)))

    def test_next_available_skips_malformed_entries(self):
        self.make_calendar({
            "tomorrow": AVAILABLE,            # bad key
            "2030-01-10 09:00": "oops",       # bad value
            "2030-01-10 09:30": AVAILABLE,
        })
        self.assertEqual(dm.find_next_available_slot(datetime(2030, 1, 10, 9, 0)),
                         "2030-01-10 09:30")

    def test_book_slot_succeeds_once(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE})
        self.assertTrue(dm.book_slot("2030-01-10 09:00", "S1234567A"))
        self.assertEqual(dm.load_slots()["2030-01-10 09:00"],
                         {"status": "booked", "patient_id": "S1234567A"})

    def test_double_booking_is_refused(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE})
        dm.book_slot("2030-01-10 09:00", "S1111111A")
        self.assertFalse(dm.book_slot("2030-01-10 09:00", "S2222222B"))
        self.assertEqual(dm.load_slots()["2030-01-10 09:00"]["patient_id"], "S1111111A")

    def test_book_unknown_slot_returns_false(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE})
        self.assertFalse(dm.book_slot("2030-01-10 10:00", "S1234567A"))

    def test_release_frees_a_booked_slot(self):
        self.make_calendar({"2030-01-10 09:00": BOOKED})
        self.assertTrue(dm.release_slot("2030-01-10 09:00"))
        self.assertEqual(dm.load_slots()["2030-01-10 09:00"],
                         {"status": "available", "patient_id": None})

    def test_released_slot_can_be_booked_again(self):
        self.make_calendar({"2030-01-10 09:00": AVAILABLE})
        dm.book_slot("2030-01-10 09:00", "S1111111A")
        dm.release_slot("2030-01-10 09:00")
        self.assertTrue(dm.book_slot("2030-01-10 09:00", "S2222222B"))

    def test_release_rejects_empty_none_and_unknown_slots(self):
        self.make_calendar({"2030-01-10 09:00": BOOKED})
        for slot in ("", None, "2030-01-10 10:00"):
            with self.subTest(slot=slot):
                self.assertFalse(dm.release_slot(slot))


if __name__ == "__main__":
    unittest.main()