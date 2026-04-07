#!/usr/bin/env python3
"""Base exception classes for xyz-platform."""

from typing import Dict, Any, Optional


class PlatformException(Exception):
    """Base exception for all xyz-platform errors."""

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        cause: Optional[Exception] = None,
    ):
        super().__init__(message)
        self.message = message
        self.error_code = error_code or self.__class__.__name__
        self.details = details or {}
        self.cause = cause

    def to_dict(self) -> Dict[str, Any]:
        """Convert exception to dictionary for structured logging/API responses."""
        result = {
            "error": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details,
        }
        if self.cause:
            result["cause"] = str(self.cause)
        return result

    def __str__(self) -> str:
        parts = [f"{self.error_code}: {self.message}"]
        if self.details:
            parts.append(f"Details: {self.details}")
        if self.cause:
            parts.append(f"Caused by: {self.cause}")
        return " | ".join(parts)


class PlatformConfigurationError(PlatformException):
    """Raised when platform configuration is invalid or missing."""

    pass


class PlatformNotFoundError(PlatformException):
    """Raised when a requested resource is not found."""

    pass


class PlatformValidationError(PlatformException):
    """Raised when validation fails."""

    pass


class PlatformStateError(PlatformException):
    """Raised when an operation is attempted in an invalid state."""

    pass
