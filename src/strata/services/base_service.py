#!/usr/bin/env python3
"""Minimal base class for platform services: YAML/dict loading + Pydantic validation.

This is a deliberately thin port of v1's `BaseService` — no caching, no
cross-repo path resolution, no structured logging. Those are added only when a
concrete need for them exists (see ADR-0003 and follow-ups).
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

import yaml
from pydantic import BaseModel, ValidationError

from strata.utils.diagnostics import Diagnostics

if TYPE_CHECKING:
    from strata.models.configuration_model import ConfigurationModel

ModelT = TypeVar("ModelT", bound=BaseModel)
ServiceT = TypeVar("ServiceT", bound="BaseService")  # type: ignore[type-arg]


def diagnostics_from_validation_error(exc: ValidationError) -> Diagnostics:
    """Convert a Pydantic `ValidationError` into structured diagnostics.

    Pydantic already reports `loc`, `msg` and `type` separately. The previous
    implementation collapsed each entry with `str(err)`, which produced a
    Python dict repr inside a string — unreadable and unparseable. This keeps
    the parts it already had:

    - `type` -> `code` (stable classifier, e.g. `missing`)
    - `loc`  -> `location` (`spec.execution.0.provisioner`)
    - `msg`  -> `message`

    `input` is deliberately dropped: it is the entire offending value, which
    for a document-level error is the whole document.

    Args:
        exc: The validation error raised by `model_validate`.

    Returns:
        One error diagnostic per reported problem.
    """
    diagnostics = Diagnostics()
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()))
        diagnostics.error(
            str(error.get("msg", "Invalid value")),
            location=location or None,
            code=str(error.get("type")) if error.get("type") else None,
        )
    return diagnostics


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
        self.diagnostics = Diagnostics()

    @classmethod
    def from_model(cls: type[ServiceT], model: ModelT) -> ServiceT:
        """Wrap an already-validated model, skipping re-validation.

        Returns the concrete subclass (`DeploymentService.from_model(...)`
        gives back a `DeploymentService`, not a bare `BaseService`) via the
        standard bound-`TypeVar` idiom — the project targets Python 3.10,
        which predates `typing.Self`.

        For cross-document checks: the controller runs these only after
        every document has already gone through Phase 1, so the model is
        already known-good. Calling `validate()` again would just repeat
        that work — and risks a second copy silently diverging from the one
        already sitting in the solution index.
        """
        service = cls.__new__(cls)
        service.path = None
        service.data = None
        service.model = model
        service._validated = True
        service.diagnostics = Diagnostics()
        return service

    @abstractmethod
    def _get_model_class(self) -> type[ModelT]:
        """Return the Pydantic model class used for validation."""
        raise NotImplementedError

    def _validate_dynamic(self, configuration_model: "ConfigurationModel | None" = None) -> Diagnostics:
        """Phase 2: validation requiring external/dynamic context.

        Default: no-op (nothing to check yet). Subclasses override when they
        have dynamic checks to perform (e.g. against `configuration_model`, a
        configuration registry).
        """
        return Diagnostics()

    def _load_data(self) -> dict[str, object]:
        """Load raw data from `self.data`, or from the YAML file at `self.path`."""
        if self.data is not None:
            return self.data
        assert self.path is not None
        content = Path(self.path).read_text(encoding="utf-8")
        loaded = yaml.safe_load(content)
        return loaded or {}

    def validate(self, configuration_model: "ConfigurationModel | None" = None) -> Diagnostics:
        """Run Phase 1 (schema) then Phase 2 (dynamic) validation.

        Args:
            configuration_model: Optional configuration registry for Phase 2
                cross-checks (e.g. validating a provider's type/region against
                the known provider registry). Omitted means Phase 2 is skipped.

        Returns:
            Findings for this document. `.ok` is False when any error was
            recorded; warnings and info never fail validation.
        """
        self.diagnostics = Diagnostics()
        raw = self._load_data()
        model_class = self._get_model_class()

        try:
            self.model = model_class.model_validate(raw)
        except ValidationError as exc:
            self.model = None
            self.diagnostics.extend(diagnostics_from_validation_error(exc))
            self._validated = True
            return self.diagnostics

        self.diagnostics.extend(self._validate_dynamic(configuration_model))
        if not self.diagnostics.ok:
            self.model = None
        self._validated = True
        return self.diagnostics

    def _ensure_validated(self) -> None:
        """Validate on first access if not already done; raise if invalid."""
        if not self._validated:
            self.validate()
        if self.model is None:
            raise ValueError(f"{self.__class__.__name__} is not valid: {self.diagnostics.messages()}")
