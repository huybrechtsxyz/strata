"""Structured validation error for machine-readable command output."""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class ValidationError:
    """A single structured validation error.

    Fields
    ------
    code:    Machine-readable error category (e.g. ``"UNKNOWN_REFERENCE"``).
    message: Human-readable description of the problem.
    phase:   Validation phase: ``1`` = structural (Pydantic), ``2`` = cross-reference.
    field:   Dot / arrow-separated field path that caused the error (when known).
    value:   The offending value that failed validation (when known).
    context: Optional extra key-value pairs for additional diagnostic detail.
    """

    code: str
    message: str
    phase: int = 1
    field: Optional[str] = None
    value: Optional[str] = None
    context: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable dict, omitting ``None``-valued optional fields."""
        result: Dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "phase": self.phase,
        }
        if self.field is not None:
            result["field"] = self.field
        if self.value is not None:
            result["value"] = self.value
        if self.context is not None:
            result["context"] = self.context
        return result
