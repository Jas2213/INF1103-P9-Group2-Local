import logging
import os
from abc import ABC, abstractmethod

class BaseManager(ABC):
    """Base class that provides shared functionality for all managers"""

    def __init__(self):
        """"Get the name of the manager using this base class"""
        self.manager_name = self.get_manager_name()


        """Ensure the data folder exists for log file"""
        os.makedirs("data", exist_ok=True)

        """Create a separate logger for each manager"""
        self.logger = logging.getLogger(
            f"patient_triage.{self.manager_name}"
        )

        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        """Prevent duplicate handlers if the manager is initialised again"""
        if not self.logger.handlers:
            file_handler = logging.FileHandler(
                "data/manager.log",
                encoding="utf-8"
            )

            formatter = logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )

            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)

    @abstractmethod
    def get_manager_name(self):
        """Return the name of the manager."""
        pass

    def log_info(self, message):
        """Record an informational message."""
        self.logger.info(message)

    def log_warning(self, message):
        """Record a warning message."""
        self.logger.warning(message)

    def log_error(self, message):
        """Record an error message."""
        self.logger.error(message)