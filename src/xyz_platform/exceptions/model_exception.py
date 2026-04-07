#!/usr/bin/env python3
"""Model and schema validation exceptions."""

from typing import List, Dict, Any, Optional
from .base_exception import PlatformValidationError


class ModelValidationError(PlatformValidationError):
    """Raised when Pydantic model validation fails."""

    def __init__(
        self,
        model_name: str,
        validation_errors: List[Dict[str, Any]],
        message: Optional[str] = None,
    ):
        self.model_name = model_name
        self.validation_errors = validation_errors

        msg = message or f"Validation failed for {model_name}"
        super().__init__(
            message=msg,
            error_code="MODEL_VALIDATION_ERROR",
            details={"model": model_name, "errors": validation_errors},
        )


class DuplicateNameError(PlatformValidationError):
    """Raised when duplicate names are found where uniqueness is required."""

    def __init__(self, entity_type: str, name: str, location: Optional[str] = None):
        msg = f"Duplicate {entity_type} name: '{name}'"
        if location:
            msg += f" in {location}"

        super().__init__(
            message=msg,
            error_code="DUPLICATE_NAME",
            details={"entity_type": entity_type, "name": name, "location": location},
        )


class InvalidReferenceError(PlatformValidationError):
    """Raised when a reference to another resource is invalid."""

    def __init__(
        self,
        source_type: str,
        source_name: str,
        reference_type: str,
        reference_value: str,
    ):
        msg = (
            f"{source_type} '{source_name}' references unknown "
            f"{reference_type} '{reference_value}'"
        )
        super().__init__(
            message=msg,
            error_code="INVALID_REFERENCE",
            details={
                "source_type": source_type,
                "source_name": source_name,
                "reference_type": reference_type,
                "reference_value": reference_value,
            },
        )


class UnsupportedKindError(PlatformValidationError):
    """Raised when an unsupported resource kind is encountered."""

    def __init__(self, kind: str, supported_kinds: Optional[List[str]] = None):
        msg = f"Unsupported resource kind: '{kind}'"
        if supported_kinds:
            msg += f". Supported: {', '.join(supported_kinds)}"

        super().__init__(
            message=msg,
            error_code="UNSUPPORTED_KIND",
            details={"kind": kind, "supported_kinds": supported_kinds},
        )


class SchemaVersionError(PlatformValidationError):
    """Raised when resource schema version is incompatible."""

    def __init__(self, actual_version: str, expected_version: str):
        msg = (
            f"Schema version mismatch: got '{actual_version}', "
            f"expected '{expected_version}'"
        )
        super().__init__(
            message=msg,
            error_code="SCHEMA_VERSION_MISMATCH",
            details={"actual": actual_version, "expected": expected_version},
        )
