#!/usr/bin/env python3
"""Minimal base class for platform services: YAML/dict loading + Pydantic validation.

This is a deliberately thin port of v1's `BaseService` — no caching, no
cross-repo path resolution, no structured logging. Those are added only when a
concrete need for them exists (see ADR-0003 and follow-ups).
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Generic, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

ModelT = TypeVar("ModelT", bound=BaseModel)


class BaseService(ABC, Generic[ModelT]):
    """Base class for platform services.

    Loads raw data (from a YAML file path or an in-memory dict) and validates it
    against a Pydantic model in two phases:

    - Phase 1 (schema): `model_class.model_validate(raw)` — always runs.
    - Phase 2 (dynamic): `_validate_dynamic()` — business-logic checks that need
      external/dynamic context (e.g. cross-checking against a configuration
      registry). Defaults to a no-op; subclasses override when they have such
      checks to perform.
    """

    def __init__(self, path: str | None = None, data: dict[str, object] | None = None):
        if path is None and data is None:
            raise ValueError("Either path or data must be provided")
        self.path = path
        self.data = data
        self.model: ModelT | None = None
        self._validated = False
        self._errors: list[str] = []

    @abstractmethod
    def _get_model_class(self) -> type[ModelT]:
        """Return the Pydantic model class used for validation."""
        raise NotImplementedError

    def _validate_dynamic(self) -> tuple[bool, list[str]]:
        """Phase 2: validation requiring external/dynamic context.

        Default: no-op (nothing to check yet). Subclasses override when they
        have dynamic checks to perform (e.g. against a configuration registry).
        """
        return True, []

    def _load_data(self) -> dict[str, object]:
        """Load raw data from `self.data`, or from the YAML file at `self.path`."""
        if self.data is not None:
            return self.data
        assert self.path is not None
        content = Path(self.path).read_text(encoding="utf-8")
        loaded = yaml.safe_load(content)
        return loaded or {}

    def validate(self) -> tuple[bool, list[str]]:
        """Run Phase 1 (schema) then Phase 2 (dynamic) validation.

        Returns:
            (is_valid, error_messages)
        """
        self._errors = []
        raw = self._load_data()
        model_class = self._get_model_class()

        try:
            self.model = model_class.model_validate(raw)
        except ValidationError as exc:
            self.model = None
            self._errors = [str(err) for err in exc.errors()]
            self._validated = True
            return False, self._errors

        dynamic_ok, dynamic_errors = self._validate_dynamic()
        if not dynamic_ok:
            self.model = None
            self._errors = dynamic_errors
            self._validated = True
            return False, self._errors

        self._validated = True
        return True, []

    def _ensure_validated(self) -> None:
        """Validate on first access if not already done; raise if invalid."""
        if not self._validated:
            self.validate()
        if self.model is None:
            raise ValueError(f"{self.__class__.__name__} is not valid: {self._errors}")
