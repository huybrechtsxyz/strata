#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_validate_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base class for validate commands in the XYZ Platform CLI.
===============================================================================
"""

from abc import abstractmethod
from typing import List, Optional

import click

from xyz_platform.commands.base_command import BaseCommand


class BaseValidateCommand(BaseCommand):
    """
    Base class for all validate commands.

    Extends BaseCommand with validation-specific state and helpers:
    - Validation error accumulation separate from execution errors

    File paths are always resolved relative to work_path — the directory
    where repositories are fetched by 'session fetch'.

    Execution Order:
    1. BaseCommand._initialize()                [Base: paths, timing, session]
    2. BaseValidateCommand._initialize()         [Validate-specific setup]
    3. BaseCommand._before_execute()            [Base: integration check]
    4. BaseValidateCommand._before_execute()     [File arg check]
    5. execute()                                [Actual command logic]
    6. BaseValidateCommand._after_execute()      [Validate-specific cleanup]
    7. BaseCommand._after_execute()             [Base post-execution]
    8. BaseValidateCommand._finalize()           [Logging, metrics]
    9. BaseCommand._finalize()                  [Duration, output]
    """

    def __init__(
        self,
        file_path: str = None,
        work_path: str = None,
        no_hooks: bool = False,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """
        Initialize the base validate command.

        Args:
            file_path: Path to the platform artifact file to validate
                       (relative to work_path or absolute)
            work_path: Root workspace path (defaults to CWD)
            no_hooks: If True, skip before/after lifecycle hooks
            output: Output format (json, text, or empty for human-readable)
            verbose: Enable verbose output (log replay)
            quiet: Suppress all console output
        """
        super().__init__(
            file_path=file_path,
            work_path=work_path,
            no_hooks=no_hooks,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

        # Validation errors are separate from execution errors:
        # - _errors: system/execution failures (initialization, hooks, IO)
        # - _validation_errors: file-level schema/semantic validation results
        self._validation_errors: List[str] = []

    # ── Abstract interface ───────────────────────────────────────────────────

    @abstractmethod
    def execute(self) -> bool:
        """Execute the command logic."""
        raise NotImplementedError("Subclasses must implement execute()")

    # ── Validation error helpers ─────────────────────────────────────────────

    def has_validation_errors(self) -> bool:
        """Return True if any schema/semantic validation errors were found."""
        return len(self._validation_errors) > 0

    def get_validation_errors(self) -> List[str]:
        """Return a copy of the accumulated validation error list."""
        return self._validation_errors.copy()

    # ── Lifecycle overrides ──────────────────────────────────────────────────

    def _initialize(self, operation: str = None) -> bool:
        """
        Validate-specific initialization.

        Calls parent (base) initialization first.

        Returns:
            bool: True if initialization succeeds, False otherwise
        """
        if not super()._initialize(operation=operation):
            return False

        self.logger.debug(
            "Validate command base initialized",
            extra={
                "command_class": self.__class__.__name__,
                "file_path": str(self._file_path) if self._file_path else None,
            },
        )
        return True

    def _before_execute(self) -> bool:
        """
        Validate-specific pre-execution checks.

        Ensures a --file argument was provided before delegating to the
        parent for integration validation.

        Returns:
            bool: True if pre-execution checks pass, False otherwise
        """
        if not super()._before_execute():
            return False

        if not self._file_path:
            error_msg = "No platform file specified. Use --file to provide one."
            self.logger.error(error_msg)
            self._errors.append(error_msg)
            if self._is_console_output():
                click.echo(f"\n❌  {error_msg}")
            return False

        return True

    def _after_execute(self) -> bool:
        """
        Validate-specific post-execution cleanup.

        Called BEFORE BaseCommand._after_execute().

        Returns:
            bool: True if post-execution succeeds, False otherwise
        """
        self.logger.debug(
            "Validate command post-execution",
            extra={
                "command_class": self.__class__.__name__,
                "validation_errors_count": len(self._validation_errors),
            },
        )
        return super()._after_execute()

    def _finalize(
        self, operation: str = None, success: bool = None, show_footer: bool = True
    ) -> bool:
        """
        Validate-specific finalization.

        Logs summary and delegates to BaseCommand._finalize().

        Returns:
            bool: True if finalization succeeds, False otherwise
        """
        self.logger.info(
            "Validate command finalized",
            extra={
                "command_class": self.__class__.__name__,
                "operation": operation or "validate",
                "success": success,
                "errors_count": len(self._errors),
                "validation_errors_count": len(self._validation_errors),
            },
        )
        return super()._finalize(
            operation=operation, success=success, show_footer=show_footer
        )
