# ===========================================================================
# PHASE 2 DIRECTION (read before changing any manager)
# ---------------------------------------------------------------------------
# GROUND RULES
#  1. The four managers import shared classes only with `from models import ...`
#     and never import each other. models imports no manager.
#  2. main.py, staff.py and generate_slots.py NEVER import models and never call
#     a method on a model object. They call manager functions only, and may
#     pass what one manager returns to another manager. No business rules, no
#     stage/role/decision strings and no dict editing in these three files.
#  3. Objects (not dicts) travel between managers. Only data_manager converts
#     to/from dicts (to_dict / from_dict).
#  4. Saved JSON shapes and stored string values must not change: existing
#     files in data/ must keep loading.
#  5. Do not edit models/ to suit one manager. A class only one manager uses
#     lives in that manager's file.
#  6. Keep "never mutate the input": use with_...(), send_to_patient(), close().
#  7. Do not touch the test scripts until the Phase 2 manager work is done.
# ===========================================================================

# WHO RAISES, CATCHES AND SHOWS EACH ERROR (this is the report's exception matrix)
#  AIConnectionError
#     ai_manager raises it for a missing key, network failure, API error or
#       TIMEOUT. Implement the request timeout in the Gemini call.
#     ai_manager catches it in its retry loop: if error.retryable, wait
#       RETRY_DELAY_SECONDS and retry once, then return AIResult.failure(reason).
#       is_ai_available() catches it and returns False.
#     io_manager shows error.user_message through display_ai_down_patient /
#       display_ai_down_staff. The entry points only see a failed AIResult.
#  AIResponseError
#     AIResult.from_dict() raises it for bad or incomplete Gemini JSON; ai_manager
#       raises it when the reply is not valid JSON. Same retry loop, one retry.
#  DataCorruptError
#     data_manager catches TriageError around EVERY from_dict() call, moves the
#       bad file aside (quarantine) and treats the record as missing. The load_*
#       functions never raise to callers.
#  ValidationError
#     models and logic_manager raise it (bad override, past appointment, empty id).
#     io_manager catches it, shows error.user_message with display_error, and
#       asks the user again.
#  Safety net: main.py's run_safely() catches a plain Exception and shows the
#     text through io_manager.display_error. Managers handle TriageError
#     themselves, so main.py never imports this file.




# Parent of every error this application raises on purpose. Catching TriageError catches all of the errors below in one go
class TriageError(Exception):
 
    # AI manager reads .retryable in its retry loop
    # io_manager displays .user_message 
    # data_manager catches TriageError around every from_dict() call so one bad file never stops the others loading

 
    DEFAULT_USER_MESSAGE = "Something went wrong. Please try again."
    RETRYABLE = False
 
    def __init__(self, message=None, user_message=None):
        # `message` is the technical detail (goes to the log file);
        # `user_message` is what the user may be shown.
        super().__init__(message or self.DEFAULT_USER_MESSAGE)
        self._user_message = user_message or self.DEFAULT_USER_MESSAGE
 
    @property
    def user_message(self):
        # Plain-language text that is safe to show to the user
        return self._user_message
 
    @property
    def retryable(self):
        # True if trying the same step again could succeed
        return self.RETRYABLE



# AI could not be reached: no internet, a timeout, or a missing API key. Could be due to a brief outage or rate-limit spikes
class AIConnectionError(TriageError):

    # AI manager raised when the Gemini call fails and retries once, then reports the AI as unavailable.
 
    DEFAULT_USER_MESSAGE = (
        "Our triage system is currently unavailable. "
        "Please visit your GP, or go to A&E if this is an emergency."
    )
    RETRYABLE = True
 
#  AI replied but the reply is unusable due to broken JSON, a missing field, or a value that is not allowed
class AIResponseError(TriageError):
 
    # AI manager: when the reply is not valid JSON and AIResult when a field is missing or invalid. It will retry once, then reports a failed result
 
    DEFAULT_USER_MESSAGE = (
        "We could not understand the triage system's reply. Please try again later."
    )
    RETRYABLE = True



# A saved file is unreadable or has the wrong structure. It is handled by moving the bad file aside, so the app carries on
class DataCorruptError(TriageError):
 
    # Raised by: entities and users in models (from_dict) and data_manager.
    # Caught by: data_manager by moving the bad file aside and treats the record as missing, so the app keeps running
    
    DEFAULT_USER_MESSAGE = (
        "A saved record could not be read and has been set aside. "
        "Please inform a staff member."
    )
    RETRYABLE = False
 
#  User’s input broke a rule, such as a bad NRIC or an appointment date in the past
class ValidationError(TriageError):
 
    # Logic manager: override and slot checks and the entities in models
    # IO manager: Catches and shows .user_message and asks the user again

 
    DEFAULT_USER_MESSAGE = "That input is not valid. Please check it and try again."
    RETRYABLE = False
 
    def __init__(self, message=None):
        super().__init__(message, user_message=message)