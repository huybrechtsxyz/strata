"""Base command class for XYZ Platform CLI commands."""

import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import click

from xyz_platform.controllers.solution_controller import SolutionController
from xyz_platform.logger import get_logger
from xyz_platform.logger.logger import reconfigure_logging
from xyz_platform.utils.system import resolve_path
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
        # Timer attributes
        self._start_time: datetime
        self._end_time: datetime

        # Paths
        self._work_path: Path = self._get_current_workpath(work_path)

        # Logging context
        self._configure_session_logging()

        # Correlation IDs — set during _initialize()
        self._solution_controller: SolutionController = SolutionController(self._work_path)
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

    # Console output methods

    @classmethod
    def show_console_header(cls, work_path: Optional[str] = None) -> None:
        click.echo("=" * 80)
        click.echo(f"🚀 XYZ PLATFORM — CLI (v{get_version()})")
        click.echo("=" * 80)
        click.echo("Automates workspace preparation, configuration, and deployment.")
        click.echo(f"⏱️   Timestamp       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(f"📜  Entry point     : {' '.join(sys.argv)}")
        click.echo(f"📂  Current dir     : {os.getcwd()}")
        if work_path:
            click.echo(f"📁  Work path       : {work_path}")

    @classmethod
    def show_console_footer(cls) -> None:
        click.echo("=" * 80)
        click.echo("✨ Thank you for using XYZ Platform CLI!")
        click.echo("📘 Documentation: https://docs.xyzplatform.com")
        click.echo("💬 Support: https://support.xyzplatform.com")
        click.echo("=" * 80)

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
            self._configure_session_logging()

            self.logger.debug(
                "Initializing command",
                extra={"command_class": self.__class__.__name__},
            )

            if not self._work_path.exists():
                error_msg = f"Work path does not exist: {self._work_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Try to load — but don't fail hard if solution.json doesn't exist yet
            # (e.g. during `xyz solution init` itself)
            solution_path = SolutionController.get_solution_json_path(self._work_path)
            if solution_path.exists():
                self._solution_controller.load()

            # Assign correlation IDs (UUID v7 — time-ordered)
            self._project_id = self._session_controller.get_project_id() or generate_uuid()
            self._execution_id = generate_uuid()
            set_context({"project_id": self._project_id, "execution_id": self._execution_id})

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

    # Internal helper methods

    def _configure_session_logging(self) -> None:
        """
        Auto-configure logging from session-specific YAML if available.

        Looks for ``.platform/logging.yaml`` under ``self._work_path``.
        Reconfigures logging only when the discovered config path changes.
        """
        try:
            logging_config = SolutionController.get_logging_config_path(self._work_path)
            if logging_config is None:
                return

            if not logging_config.exists():
                return

            config_path = resolve_path(str(logging_config))
            reconfigure_logging(config_path=config_path)

            # Refresh logger after reconfiguration
            self.logger = get_logger(self.__class__.__module__)
            self.logger.debug(
                "Session logging configuration loaded",
                extra={
                    "command_class": self.__class__.__name__,
                    "logging_config_path": config_path,
                },
            )

        except Exception as e:
            # Logging configuration should not block command execution
            self.logger.warning(
                f"Failed to auto-configure session logging: {str(e)}",
                extra={"command_class": self.__class__.__name__},
            )

    # Get the work path based on input or default to current directory
    def _get_current_workpath(self, work_path: Optional[str]) -> Path:
        """Get the work path for the given workspace."""
        work_path_obj: Path
        # If work_path is provided, use it directly
        if work_path is not None and work_path != "":
            work_path_obj = Path(work_path).resolve()
            self.logger.debug(
                "Target work directory from argument",
                extra={"work_path": str(work_path_obj)},
            )
            return work_path_obj

        # Use current working directory as default
        if Path.cwd().is_absolute():
            work_path_obj = Path.cwd()
        else:
            work_path_obj = Path.cwd().resolve()
        self.logger.debug("Target work directory (default)", extra={"work_path": str(work_path)})
        return work_path_obj
