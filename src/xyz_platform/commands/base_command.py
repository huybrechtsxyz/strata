"""Base command class for XYZ Platform CLI commands."""

import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional

import click

from xyz_platform.logger import get_logger
from xyz_platform.utils.version import get_version


class BaseCommand(ABC):
    """Base command class for XYZ Platform CLI commands."""

    OPERATION = "base_command"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        """Initialize the base command."""
        # Paths
        self._work_path: str = work_path or os.getcwd()

        # Timer attributes
        self._start_time: datetime
        self._end_time: datetime

        # Logging context
        self.logger = get_logger(self.__class__.__module__)

        # Correlation IDs — set during _initialize()
        self._project_id: str
        self._execution_id: str

        # Structured result data
        self._output_data: dict = {}
        self._output_format = output or "console"
        self._output_verbose = verbose or False
        self._output_quiet = quiet or False

        # Message and error accumulation
        self._messages: List[str] = []
        self._errors: List[str] = []

    @abstractmethod
    def execute(self, *args, **kwargs):
        """Execute the command. To be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement the execute method.")

    @abstractmethod
    def get_required_integrations(self) -> Dict[str, str]:
        """
        Declare required integrations for this command.

        Subclasses override this to declare what external tools they need.
        Returns a dict mapping integration names to operation descriptions.

        Example:
            return {"git": "repository clone operations", "docker": "container build"}

        Returns:
            Dict[str, str]: Empty dict (no requirements by default)
        """
        return {}

    # Message and error handling methods

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

    # Console output methods

    def show_console_header(self, work_path: Optional[str] = None) -> None:
        click.echo("=" * 80)
        click.echo(f"🚀 XYZ PLATFORM — CLI (v{get_version()})")
        click.echo("=" * 80)
        click.echo("Automates workspace preparation, configuration, and deployment.")
        click.echo(f"⏱️   Timestamp       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(f"📜  Entry point     : {' '.join(sys.argv)}")
        click.echo(f"📂  Current dir     : {os.getcwd()}")
        if work_path:
            click.echo(f"📁  Work path       : {self._work_path}")

    def show_console_Footer(self) -> None:
        click.echo("=" * 80)
        click.echo("✨ Thank you for using XYZ Platform CLI!")
        click.echo("📘 Documentation: https://docs.xyzplatform.com")
        click.echo("💬 Support: https://support.xyzplatform.com")
        click.echo("=" * 80)

    # Lifecycle methods

    def _initialize(self) -> bool:
        """
        Initialize the command before execution.
        Sets up paths, timing, and logging context.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            self._start_time = datetime.now()
            return True
        except Exception as e:
            self.logger.error(
                "Failed to initialize command",
                error=str(e),
                exc_info=True,
            )
            self._errors.append(f"Failed to initialize command: {e}")
            return False

    def _before_execute(self) -> bool:
        """Optional steps before main execution."""
        return True

    def _after_execute(self) -> bool:
        """Optional steps after main execution."""
        return True

    def _finalize(self) -> bool:
        """Optional finalization after execution."""
        return True

    # Output configuration

    def _is_quiet(self) -> bool:
        """Return True when --quiet is active: suppress all output."""
        return self._output_quiet

    def _is_verbose(self) -> bool:
        """Return True when --verbose is active: include debug log replay."""
        return self._output_verbose

    def _is_structured_output(self) -> bool:
        """Return True when --output <format> is active: emit machine-readable data (json, text, etc.)."""
        return bool(self._output_format) and self._output_format != "console"

    def _is_console_output(self) -> bool:
        """Return True for default human-readable console output (no --quiet, no explicit --output format)."""
        return not self._output_quiet and (not bool(self._output_format) or self._output_format == "console")
