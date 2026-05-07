#!/usr/bin/env python3
"""Path validation exceptions for CLI and workspace inputs."""

from typing import Optional

from .base_exception import PlatformValidationError


class PathValidationError(PlatformValidationError):
    """Raised when a CLI/workspace path is invalid or does not match expected type."""

    def __init__(
        self,
        option: str,
        provided: str,
        expected: str,
        resolved: Optional[str] = None,
        work_path: Optional[str] = None,
        message: Optional[str] = None,
    ):
        msg = message or f"Invalid value for {option}: '{provided}' ({expected})"
        super().__init__(
            message=msg,
            error_code="PATH_VALIDATION_ERROR",
            details={
                "option": option,
                "provided": provided,
                "expected": expected,
                "resolved": resolved,
                "work_path": work_path,
            },
        )
