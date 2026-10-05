"""
demo_ai_manager.py

Standalone script to test that your .env / GEMINI_API_KEY is set up
correctly and that ai_manager can actually reach the Gemini API — without
needing main.py or any other manager wired up.

Run it with:  python demo_ai_manager.py
"""

import managers.ai_manager as ai_manager


def main():
    print("=== ai_manager connection test ===\n")

    symptom = input("Enter a test symptom description "
                     "(or press Enter to use a default): ").strip()
    if not symptom:
        symptom = "I've had a persistent headache and mild fever for the past two days."
        print(f"Using default: \"{symptom}\"")

    print("\nCalling get_ai_triage_result()... (this hits the real API, may take a few seconds)\n")

    result = ai_manager.get_ai_triage_result(symptom, patient_history=None)

    print("--- Raw result ---")
    for key, value in result.items():
        print(f"  {key}: {value}")

    print()
    if result.get("status") == "ok":
        print("SUCCESS — your API key works and the response passed schema validation.")
    else:
        print("FAILED — see 'error_reason' above.")
        print("\nCommon causes:")
        print("  - .env is missing, misnamed, or not in the same folder as this script")
        print("  - GEMINI_API_KEY in .env is blank, has quotes/spaces, or is invalid")
        print("  - No internet connection, or the Gemini API is temporarily down")
        print("  - google-genai / python-dotenv not installed (pip install -r requirements.txt)")


if __name__ == "__main__":
    main()
