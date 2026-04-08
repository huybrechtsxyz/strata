"""Service for managing project model I/O operations."""

import json
import threading
from pathlib import Path
from typing import Any, Optional, Dict

from xyz_platform.models.project_model import ProjectModel
from xyz_platform.services.base_service import BaseService
from xyz_platform.exceptions.service_exception import (
    PlatformFileNotFoundError,
    ServiceLoadError,
)
from xyz_platform.exceptions.model_exception import ModelValidationError


class ProjectService(BaseService):
    """Service class for project kinds (Centralized Singleton pattern)."""

    _instances: Dict[str, "ProjectService"] = {}
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
        """Initialize the ProjectService (only once per singleton instance)."""
        if getattr(self, "_initialized", False):
            return

        super().__init__(path=path, data=data)
        self.model: Optional[ProjectModel] = None

        # Mark initialized to avoid re-running __init__ on singleton
        self._initialized = True

    @classmethod
    def get_instance(cls) -> "ProjectService":
        """Get singleton instance."""
        return cls()

    @classmethod
    def reset(cls):
        """Reset all singleton instances (useful for testing)."""
        with cls._lock:
            cls._instances.clear()

    def _get_model_class(self):
        """Return a generic model class for project kinds."""
        return ProjectModel  # A generic empty model

    def _validate_dynamic(self, configuration_model=None, work_path=None):
        """Project services have no dynamic validation."""
        return True, []

    def load_from_json(self, path: Path) -> ProjectModel:
        """Load a ProjectModel from a JSON file.

        Args:
            path: Path to JSON file.

        Returns:
            ProjectModel instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValidationError: If the JSON does not match ProjectModel.
        """
        self.logger.debug("Loading project model from JSON", path=str(path))

        if not path.exists():
            raise PlatformFileNotFoundError(str(path), file_type="project.json")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Validate/construct model; model_validate may raise validation errors
            self.model = ProjectModel.model_validate(data)

            self.logger.debug("Loaded project model", name=getattr(self.model.meta, "name", "<unknown>"))

            return self.model

        except Exception as e:
            # Convert pydantic/model validation errors into our ModelValidationError
            validation_errors: Any = getattr(e, "errors", None)
            if validation_errors is not None:
                raise ModelValidationError(
                    model_name="ProjectModel",
                    validation_errors=validation_errors,
                    message=str(e),
                ) from e

            # JSON parsing or other IO errors -> service load error
            raise ServiceLoadError("ProjectService", str(e), cause=e) from e

    def save_to_json(self, project: ProjectModel, path: Path, indent: int = 2) -> None:
        """Serialise a PlatformModel to a JSON file.

        Args:
            platform: PlatformModel to serialise.
            path: Destination path.
            indent: JSON indentation level (default 2).
        """
        self.logger.debug("Saving project model to JSON", path=str(path))

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            project.model_dump_json(indent=indent, exclude_none=True),
            encoding="utf-8",
        )

        self.logger.debug("Saved project model to JSON", name=project.meta.name)
