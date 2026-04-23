"""Service for managing solution model I/O operations."""

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from xyz_platform.exceptions.model_exception import ModelValidationError
from xyz_platform.exceptions.service_exception import (
    PlatformFileNotFoundError,
    ServiceLoadError,
)
from xyz_platform.models.solution_model import SolutionModel
from xyz_platform.services.base_service import BaseService


class SolutionService(BaseService["SolutionModel"]):
    """Service class for solution kinds (Centralized Singleton pattern)."""

    _instances: Dict[str, "SolutionService"] = {}
    _lock = threading.Lock()

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        """Get instance key for singleton. Override for multiple instances."""
        return "default"

    def __new__(cls, *args, **kwargs):
        """Create or return existing singleton instance (thread-safe)."""
        instance_key = cls._get_instance_key_static(cls, *args, **kwargs)
        full_key = f"{cls.__name__}::{instance_key}"

        with cls._lock:
            if full_key not in cls._instances:
                instance = super().__new__(cls)
                instance._initialized = False
                cls._instances[full_key] = instance
            return cls._instances[full_key]

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the SolutionService (only once per singleton instance)."""
        if getattr(self, "_initialized", False):
            return

        super().__init__(path=path, data=data)
        self.model = None

        # Mark initialized to avoid re-running __init__ on singleton
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "SolutionService":
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset(cls):
        """Reset all singleton instances (useful for testing)."""
        with cls._lock:
            cls._instances.clear()

    def _get_model_class(self):
        """Return the model class for solution kinds."""
        return SolutionModel

    def _validate_dynamic(self, configuration_model=None, work_path=None):
        """Solution services have no dynamic validation."""
        return True, []

    def load_from_json(self, path: Path) -> SolutionModel:
        """Load a SolutionModel from a JSON file.

        Args:
            path: Path to JSON file.

        Returns:
            SolutionModel instance.

        Raises:
            PlatformFileNotFoundError: If the file does not exist.
            ModelValidationError: If the JSON does not match SolutionModel.
            ServiceLoadError: For JSON parsing or other I/O errors.
        """
        self.logger.debug("Loading solution model from JSON", path=str(path))

        if not path.exists():
            raise PlatformFileNotFoundError(str(path), file_type="solution.json")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.model = SolutionModel.model_validate(data)

            self.logger.debug("Loaded solution model", name=getattr(self.model.meta, "name", "<unknown>"))

            return self.model

        except Exception as e:
            validation_errors: Any = getattr(e, "errors", None)
            if validation_errors is not None:
                raise ModelValidationError(
                    model_name="SolutionModel",
                    validation_errors=validation_errors,
                    message=str(e),
                ) from e

            raise ServiceLoadError("SolutionService", str(e), cause=e) from e

    def save_to_json(self, solution: SolutionModel, path: Path, indent: int = 2) -> None:
        """Serialise a SolutionModel to a JSON file.

        Args:
            solution: SolutionModel to serialise.
            path: Destination path.
            indent: JSON indentation level (default 2).
        """
        self.logger.debug("Saving solution model to JSON", path=str(path))

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            solution.model_dump_json(indent=indent, exclude_none=True),
            encoding="utf-8",
        )

        self.logger.debug("Saved solution model to JSON", name=solution.meta.name)
