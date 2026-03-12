#!/usr/bin/env python3
"""
===============================================================================
Script Name   : base_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Base command class for the XYZ Platform.
===============================================================================
"""

from abc import abstractmethod
from datetime import datetime
import os
from pathlib import Path
import sys
from typing import List

import click

from xyz_platform.logger.logger import get_logger
from xyz_platform.utils import system


class BaseCommand:
    """Base command class for the XYZ Platform. All CLI commands should inherit from this class to ensure consistent behavior and shared functionality."""

    def __init__(
        self,
        work_path: str = None,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        """Initialize the base command."""

        # Initialize logger with the actual subclass module name
        self.logger = get_logger(self.__class__.__module__)

        # Timer attributes
        self._start_time = None
        self._end_time = None

        # Paths
        self._work_path = Path(work_path) if work_path else Path.cwd()

        # Message and error accumulation
        self._messages = []
        self._errors = []

        # Output format and flags
        self._output_format = output or ""
        self._output_verbose = verbose or False
        self._output_quiet = quiet or False

        # Determine output behavior based on quiet flag
        # Note: --verbose and --quiet mutual exclusivity is enforced in cli_common.py validators
        # Note: --output and --quiet mutual exclusivity is enforced in cli_common.py validators
        # Allowed combinations:
        #   --output json/text/yaml alone → formatted output, no logs
        #   --output json --verbose → formatted output + debug logs
        #   --quiet alone → silent execution (no output)
        #   (no flags) → default human-readable output
        # Blocked by validators:
        #   --verbose --quiet → contradictory
        #   --output json --quiet → pointless

        if self._output_quiet:
            # --quiet: Disable all console output
            self._allow_output = False
        else:
            # Default or any --output format: Enable console output
            self._allow_output = True

    @abstractmethod
    def execute(self) -> bool:
        """
        Execute the command logic.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        raise NotImplementedError("Subclasses must implement execute() method")

    def has_errors(self) -> bool:
        """Check if any errors were accumulated during execution."""
        return len(self._errors) > 0

    def get_errors(self) -> List[str]:
        """
        Get accumulated errors from command execution.

        Returns:
            List[str]: Copy of error messages list
        """
        return self._errors.copy()

    def clear_errors(self) -> None:
        """Clear accumulated errors."""
        self._errors.clear()

    def has_messages(self) -> bool:
        """Check if any messages were accumulated during execution."""
        return len(self._messages) > 0

    def get_messages(self) -> List[str]:
        """
        Get accumulated messages from command execution.

        Returns:
            List[str]: Copy of messages list
        """
        return self._messages.copy()

    def clear_messages(self) -> None:
        """Clear accumulated messages."""
        self._messages.clear()

    def ShowConsoleHeader(self, work_path: str = None):
        click.echo("=" * 80)
        click.echo(f"🚀 XYZ PLATFORM — CLI (v{system.get_cli_version()})")
        click.echo("=" * 80)
        click.echo("Automates workspace preparation, configuration, and deployment.")
        click.echo(
            f"⏱️   Timestamp       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        click.echo(f"📜  Entry point     : {' '.join(sys.argv)}")
        click.echo(f"📂  Current dir     : {os.getcwd()}")
        if work_path:
            click.echo(f"📁  Work path       : {self._work_path}")

    def ShowConsoleFooter(self):
        click.echo("=" * 80)
        click.echo("✨ Thank you for using XYZ Platform CLI!")
        click.echo("📘 Documentation: https://docs.xyzplatform.com")
        click.echo("💬 Support: https://support.xyzplatform.com")
        click.echo("=" * 80)

    def _initialize(self, operation: str = None, show_header: bool = True) -> bool:
        """
        Initialize the command before execution.
        Sets up paths, timing, and logging context.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            self._start_time = datetime.now()
            self.logger.debug(
                "Initializing command",
                extra={"command_class": self.__class__.__name__},
            )

            if show_header and self._allow_output:
                self.ShowConsoleHeader()

            self.logger.debug(
                "Command initialized successfully",
                extra={"command_class": self.__class__.__name__},
            )
            return True

        except Exception as e:
            error_msg = f"Failed to initialize command: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            return False

    def _before_execute(self, message: str = None) -> bool:
        """
        Execute pre-command logic (hooks, validation, etc.).

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Executing pre-command logic",
            extra={"command_class": self.__class__.__name__},
        )
        # Placeholder for pre-execution logic (hooks, validation, etc.)
        self.logger.debug(
            "Pre-command logic executed successfully",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _after_execute(self) -> bool:
        """
        Execute post-command logic (cleanup, notifications, etc.).

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        self.logger.debug(
            "Executing post-command logic",
            extra={"command_class": self.__class__.__name__},
        )
        # Placeholder for post-execution logic (cleanup, notifications, etc.)
        self.logger.debug(
            "Post-command logic executed successfully",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _finalize(
        self, operation: str = None, success: bool = None, show_footer: bool = True
    ) -> bool:
        """
        Finalize the command execution (logging, metrics, cleanup).

        Returns:
            bool: Success status (errors stored in self._errors)
        """

        if show_footer and self._allow_output:
            # Show messages
            if self._messages and len(self._messages) > 0:
                click.echo("💬  Messages:")
                for msg in self._messages:
                    click.secho(f"    - {msg}")
                click.echo("")

            # Show errors
            if self._errors and len(self._errors) > 0:
                click.echo("❌  Errors:")
                for err in self._errors:
                    click.secho(f"    - {err}", fg="red")
                click.echo("")

            # Show verbose logging
            if self._output_verbose:
                click.echo("🔍  Verbose logs enabled (see console output for details)")
                click.echo("")

            # Show footer
            self.ShowConsoleFooter()

        self._end_time = datetime.now()
        if self._start_time and self._end_time:
            duration = self._end_time - self._start_time
            self.logger.debug(
                "Command execution completed",
                extra={
                    "command_class": self.__class__.__name__,
                    "duration_seconds": duration.total_seconds(),
                },
            )

        return True
