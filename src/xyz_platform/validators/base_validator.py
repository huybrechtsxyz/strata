"""Base class for configuration validators"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import List


class BaseValidator(ABC):
    """Abstract base class for configuration validators."""

    def __init__(self) -> None:
        self._messages: List[str] = []
        self._errors: List[str] = []

    def has_errors(self) -> bool:
        return len(self._errors) > 0

    def has_messages(self) -> bool:
        return len(self._messages) > 0

    def get_messages(self) -> List[str]:
        return self._messages

    def get_errors(self) -> List[str]:
        return self._errors

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
