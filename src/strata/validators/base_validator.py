"""Base class for configuration validators"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.models.validation_error import ValidationError


class BaseValidator(ABC):
    """Abstract base class for configuration validators."""

    def __init__(self) -> None:
        self._messages: List[str] = []
        self._errors: List[str] = []
        self._structured_errors: List[ValidationError] = []

    def has_errors(self) -> bool:
        return len(self._errors) > 0

    def has_messages(self) -> bool:
        return len(self._messages) > 0

    def get_messages(self) -> List[str]:
        return self._messages

    def get_errors(self) -> List[str]:
        return self._errors

    def get_structured_errors(self) -> List[ValidationError]:
        """Return all accumulated structured validation errors."""
        return list(self._structured_errors)

    def add_validation_error(
        self,
        code: str,
        message: str,
        phase: int = 1,
        field: Optional[str] = None,
        value: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Append a structured validation error and its plain-text equivalent."""
        self._errors.append(message)
        self._structured_errors.append(
            ValidationError(code=code, message=message, phase=phase, field=field, value=value, context=context)
        )

    @abstractmethod
    def validate(self, work_path: Path) -> bool:
        """Validate the workspace according to the validator's logic."""
        raise NotImplementedError

    @abstractmethod
    def before_validate(self, work_path: Path) -> bool:
        """Hook executed before the validate process starts."""
        raise NotImplementedError

    @abstractmethod
    def after_validate(
        self,
        work_path: Path,
    ) -> bool:
        """Hook executed after the validator process completes."""
        raise NotImplementedError
