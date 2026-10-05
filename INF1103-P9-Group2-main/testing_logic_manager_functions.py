"""
test_logic_manager.py

Unit tests / verification script for logic_manager.py
Tests:
1. Normal non-urgent routing & slot generation
2. Multi-condition specialist routing (e.g. chest pain -> cardiology)
3. Urgent case guardrails & priority escalation
4. API failure / error handling fallback
5. Nurse override recalculation
6. Appointment scheduling workflow state changes
"""

from logic_manager import (
    evaluate_triage_logic,
    apply_nurse_override,
    schedule_appointment,
    VALID_STATES
)

def run_tests():
    print("=== Starting Logic Manager Tests ===\n")

    # ------------------------------------------------------------------
    # Test 1: Standard Non-Urgent Case
    # ------------------------------------------------------------------
    print("Test 1: Standard Non-Urgent Case")
    ai_normal = {
        "status": "ok",
        "summary": "Patient reports mild seasonal allergies and a slight runny nose.",
        "urgency": "non-urgent",
        "severity": "low",
        "recommended_tier": "nurse",
        "specialist": "general_practice",
        "flagged_symptoms": ["runny nose", "sneezing"],
        "history_linked": False,
        "confidence": 0.95
    }
    
    outcome1 = evaluate_triage_logic(ai_normal)
    print(f"  -> Decision: {outcome1['decision']}")
    print(f"  -> Specialist: {outcome1['specialist']}")
    print(f"  -> Status: {outcome1['status']}")
    print(f"  -> Proposed Slot: {outcome1['proposed_slot']}")
    assert outcome1["decision"] == "schedule_non_urgent"
    assert outcome1["status"] == "TRIAGED"
    print("  [PASSED]\n")

    # ------------------------------------------------------------------
    # Test 2: Multi-Condition Specialist Rule (Chest Pain -> Cardiology)
    # ------------------------------------------------------------------
    print("Test 2: Multi-Condition Specialist Rule (Chest Pain)")
    ai_cardiac = {
        "status": "ok",
        "summary": "Patient experiencing sudden chest pain and shortness of breath.",
        "urgency": "urgent",
        "severity": "medium",
        "recommended_tier": "nurse",  # AI under-triaged tier, should trigger guardrail/specialist
        "specialist": "general_practice",
        "flagged_symptoms": ["chest pain", "shortness of breath"],
        "history_linked": True,
        "confidence": 0.90
    }

    outcome2 = evaluate_triage_logic(ai_cardiac)
    print(f"  -> Decision: {outcome2['decision']}")
    print(f"  -> Specialist assigned: {outcome2['specialist']} (Expected: cardiology)")
    print(f"  -> Practitioner Type: {outcome2['assigned_practitioner_type']} (Expected: doctor)")
    print(f"  -> Override Applied: {outcome2['override_applied']}")
    assert outcome2["specialist"] == "cardiology"
    assert outcome2["assigned_practitioner_type"] == "doctor"
    print("  [PASSED]\n")

    # ------------------------------------------------------------------
    # Test 3: API Failure / Error Handling Fallback
    # ------------------------------------------------------------------
    print("Test 3: API Failure Handling")
    ai_error = {
        "status": "error",
        "error_reason": "API rate limit exceeded or connection timeout."
    }

    outcome3 = evaluate_triage_logic(ai_error)
    print(f"  -> Decision: {outcome3['decision']} (Expected: manual_review)")
    print(f"  -> Flag for Manual Review: {outcome3['flag_for_manual_review']}")
    print(f"  -> Next Action: {outcome3['next_action']}")
    assert outcome3["decision"] == "manual_review"
    assert outcome3["flag_for_manual_review"] is True
    print("  [PASSED]\n")

    # ------------------------------------------------------------------
    # Test 4: Nurse Override Recalculation
    # ------------------------------------------------------------------
    print("Test 4: Nurse Override")
    # Take outcome1 and let a nurse override it to specialist routing
    outcome_to_override = evaluate_triage_logic(ai_normal)
    
    overridden_outcome = apply_nurse_override(
        current_outcome=outcome_to_override,
        override_decision="route_to_specialist",
        reason="Patient has a family history of dermatology issues requiring expert check.",
        specialist="dermatology"
    )
    
    print(f"  -> Nurse Override Flag: {overridden_outcome['nurse_override']} (Expected: True)")
    print(f"  -> New Decision: {overridden_outcome['decision']}")
    print(f"  -> New Specialist: {overridden_outcome['specialist']}")
    print(f"  -> Override Reason: {overridden_outcome['override_reason']}")
    assert overridden_outcome["nurse_override"] is True
    assert overridden_outcome["specialist"] == "dermatology"
    print("  [PASSED]\n")

    # ------------------------------------------------------------------
    # Test 5: Appointment Scheduling Workflow
    # ------------------------------------------------------------------
    print("Test 5: Appointment Scheduling Workflow")
    # Try scheduling an active triaged outcome
    booking_result = schedule_appointment(outcome1, "2026-10-10 10:00")
    
    print(f"  -> Booking Success: {booking_result['success']}")
    print(f"  -> Updated Workflow Status: {booking_result['outcome']['status']} (Expected: SCHEDULED)")
    print(f"  -> Confirmed Appointment Time: {booking_result['outcome']['proposed_slot']}")
    
    assert booking_result["success"] is True
    assert booking_result["outcome"]["status"] == "SCHEDULED"
    assert booking_result["outcome"]["proposed_slot"] == "2026-10-10 10:00"
    print("  [PASSED]\n")

    print("=== All Logic Manager Tests Passed Successfully! ===")

if __name__ == "__main__":
    run_tests()