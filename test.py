from managers.temp.base_manager import BaseManager


class TestManager(BaseManager):

    def get_manager_name(self):
        return "Test Manager"


manager = TestManager()

manager.log_info("Testing information message.")
manager.log_warning("Testing warning message.")
manager.log_error("Testing error message.")