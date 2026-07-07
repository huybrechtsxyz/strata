#!/usr/bin/env python3
"""Model and schema validation exceptions."""

from typing import Any, Dict, List, Optional

from .base_exception import PlatformValidationError


class ModelValidationError(PlatformValidationError):
    """Raised when Pydantic model validation fails."""

    def __init__(
        self,
        model_name: str,
        validation_errors: List[Dict[str, Any]],
        message: Optional[str] = None,
        file_path: Optional[str] = None,
    ):
        self.model_name = model_name
        self.validation_errors = validation_errors
        self.file_path = file_path

        msg = message or f"Validation failed for {model_name}"
        if file_path:
            msg = f"{msg} in {file_path}"
        super().__init__(
            message=msg,
            error_code="MODEL_VALIDATION_ERROR",
            details={"model": model_name, "errors": validation_errors, "file": file_path},
        )


class InvalidReferenceError(PlatformValidationError):
    """Raised when a reference to another resource is invalid."""

    def __init__(
        self,
        source_type: str,
        source_name: str,
        reference_type: str,
        reference_value: str,
        resolved_path: Optional[str] = None,
        available: Optional[List[str]] = None,
        file_path: Optional[str] = None,
    ):
        msg = f"{source_type} '{source_name}' references unknown {reference_type} '{reference_value}'"
        if resolved_path:
            msg += f" (resolved to: {resolved_path})"
        if available:
            msg += f". Valid options: {', '.join(sorted(available))}"
        if file_path:
            msg += f" [{file_path}]"
        super().__init__(
            message=msg,
            error_code="INVALID_REFERENCE",
            details={
                "source_type": source_type,
                "source_name": source_name,
                "reference_type": reference_type,
                "reference_value": reference_value,
                "resolved_path": resolved_path,
                "available": available,
                "file_path": file_path,
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
