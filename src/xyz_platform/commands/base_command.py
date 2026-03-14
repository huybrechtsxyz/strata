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
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

import click

from xyz_platform.controllers.session_controller import SessionController
from xyz_platform.logger.logger import get_logger, reconfigure_logging
from xyz_platform.logger.context import set_context
from xyz_platform.utils import system
from xyz_platform.utils.system import generate_uuid7


class BaseCommand:
    """Base command class for the XYZ Platform. All CLI commands should inherit from this class to ensure consistent behavior and shared functionality."""

    _active_logging_config_path: Optional[str] = None

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

        # Structured result data — populated by each command's _after_execute()
        # Used when --output json/text is requested
        self._output_data: dict = {}

        # Output format and flags
        self._output_format = output or ""
        self._output_verbose = verbose or False
        self._output_quiet = quiet or False

        # Correlation IDs — set during _initialize()
        self.session_id: Optional[str] = None
        self.execution_id: Optional[str] = None

        # Integration controller (lazy-loaded)
        self._integration_controller: Optional[object] = None

    @abstractmethod
    def execute(self) -> bool:
        """
        Execute the command logic.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        raise NotImplementedError("Subclasses must implement execute() method")

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
            self._configure_session_logging()

            self._start_time = datetime.now()

            # Assign correlation IDs (UUID v7 — time-ordered)
            self.session_id = self._read_session_id()
            self.execution_id = generate_uuid7()
            set_context(
                {"session_id": self.session_id, "execution_id": self.execution_id}
            )

            self.logger.debug(
                "Initializing command",
                extra={
                    "command_class": self.__class__.__name__,
                    "session_id": self.session_id,
                    "execution_id": self.execution_id,
                },
            )

            if show_header and self._is_console_output():
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

    def _read_session_id(self) -> str:
        """
        Read the persistent session_id from session.json.

        Falls back to a fresh UUID v7 when:
        - session.json does not exist yet (e.g. during ``session init``)
        - the file is unreadable or missing the ``session_id`` key

        Returns:
            str: UUID v7 session identifier
        """
        try:
            session_file = self._work_path / ".xyz-platform" / "session.json"
            if session_file.exists():
                with open(session_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                sid = data.get("session", {}).get("session_id")
                if sid:
                    return sid
        except Exception as e:
            self.logger.debug(f"Could not read session_id from session.json: {e}")
        return generate_uuid7()

    def _configure_session_logging(self) -> None:
        """
        Auto-configure logging from session-specific YAML if available.

        Looks for `.xyz-platform/logging.yaml` under `self._work_path`.
        Reconfigures logging only when the discovered config path changes.
        """
        try:
            session_logging_config = self._work_path / ".xyz-platform" / "logging.yaml"

            if not session_logging_config.exists():
                return

            config_path = str(session_logging_config.resolve())
            if BaseCommand._active_logging_config_path == config_path:
                return

            reconfigure_logging(config_path=config_path)
            BaseCommand._active_logging_config_path = config_path

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

        # Validate integration requirements
        if not self._validate_requirements():
            return False

        # Placeholder for additional pre-execution logic (hooks, validation, etc.)
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

    def _is_quiet(self) -> bool:
        """Return True when --quiet is active: suppress all output."""
        return self._output_quiet

    def _is_verbose(self) -> bool:
        """Return True when --verbose is active: include debug log replay."""
        return self._output_verbose

    def _is_structured_output(self) -> bool:
        """Return True when --output <format> is active: emit machine-readable data (json, text, etc.)."""
        return bool(self._output_format)

    def _is_console_output(self) -> bool:
        """Return True for default human-readable console output (no --quiet, no explicit --output format)."""
        return not self._output_quiet and not bool(self._output_format)

    def _finalize(
        self, operation: str = None, success: bool = None, show_footer: bool = True
    ) -> bool:
        """
        Finalize the command execution (logging, metrics, cleanup).

        Returns:
            bool: Success status (errors stored in self._errors)
        """

        if self._is_structured_output():
            # ── Structured output (--output json / text) ─────────────────────
            envelope = {
                "success": bool(success),
                "command": operation or "",
                "data": self._output_data,
                "messages": self._messages,
                "errors": self._errors,
            }
            if self._output_format == "json":
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                click.echo(f"success: {envelope['success']}")
                click.echo(f"command: {envelope['command']}")
                for k, v in envelope["data"].items():
                    click.echo(f"{k}: {v}")
                if envelope["messages"]:
                    click.echo("messages:")
                    for m in envelope["messages"]:
                        click.echo(f"  - {m}")
                if envelope["errors"]:
                    click.echo("errors:")
                    for e in envelope["errors"]:
                        click.echo(f"  - {e}")

        elif show_footer and self._is_console_output():
            # ── Human-readable output (default) ──────────────────────────────
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
            if self._is_verbose():
                click.echo("🔍  Verbose logs enabled (see console output for details)")
                session_controller = SessionController()
                success, log_entries, errors = session_controller.get_logs(
                    work_path=self._work_path, execution_id=self.execution_id
                )

                if success and log_entries:
                    for log in log_entries:
                        timestamp = log.get("timestamp", "")
                        level = (
                            log.get("level") or log.get("levelname") or "INFO"
                        ).upper()
                        message = log.get("message", "")
                        extra = log.get("extra", {})
                        click.secho(
                            f"    [{timestamp}] {level:8} - {message}", fg="cyan"
                        )
                        if extra:
                            click.secho(f"        Extra: {extra}", fg="cyan")
                elif errors:
                    click.secho(
                        f"    Failed to retrieve logs: {errors[0]}", fg="yellow"
                    )
                else:
                    click.secho("    No logs available", fg="yellow")
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

        # Persist execution_id so subsequent commands can use --last-exec
        if self.execution_id and self._work_path:
            try:
                SessionController().update_last_execution(
                    work_path=self._work_path, execution_id=self.execution_id
                )
            except Exception:
                pass

        return True

    def _get_integration_controller(self):
        """
        Get or create the IntegrationController instance (lazy-loaded).

        Returns:
            IntegrationController: The controller instance
        """
        if self._integration_controller is None:
            # Import here to avoid circular dependencies
            from xyz_platform.controllers.integration_controller import (
                IntegrationController,
            )

            self._integration_controller = IntegrationController()
        return self._integration_controller

    def _validate_requirements(self) -> bool:
        """
        Validate that all required integrations are available.

        Called automatically by _before_execute() before command execution.
        Checks each integration declared in get_required_integrations().

        Returns:
            bool: True if all requirements met, False otherwise (errors stored in self._errors)
        """
        required = self.get_required_integrations()

        if not required:
            # No requirements - validation passes
            return True

        self.logger.debug(
            f"Validating {len(required)} required integration(s)",
            extra={
                "command_class": self.__class__.__name__,
                "integrations": list(required.keys()),
            },
        )

        integration_controller = self._get_integration_controller()

        for integration_name, operation_desc in required.items():
            is_available, error_msg = (
                integration_controller.ensure_integration_available(
                    integration_name, operation_desc
                )
            )

            if not is_available:
                self.logger.error(
                    f"Integration requirement not met: {integration_name}",
                    extra={
                        "command_class": self.__class__.__name__,
                        "integration": integration_name,
                        "operation": operation_desc,
                    },
                )
                self._errors.append(error_msg)
                return False

            self.logger.debug(
                f"Integration '{integration_name}' validated successfully",
                extra={
                    "command_class": self.__class__.__name__,
                    "integration": integration_name,
                },
            )

        self.logger.debug(
            "All integration requirements validated",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _resolve_required_integrations(self) -> Dict[str, Any]:
        """
        Resolve all integrations declared by get_required_integrations().

        Returns:
            Dict[str, Any]: Mapping of integration name to integration instance.
                Empty dict when no requirements are declared.

        Raises:
            RuntimeError: If required integrations cannot be resolved.
        """
        required = self.get_required_integrations()
        if not required:
            return {}

        integration_controller = self._get_integration_controller()
        success, integrations, errors = (
            integration_controller.resolve_required_integrations(required)
        )

        if not success:
            if errors:
                self._errors.extend(errors)
            raise RuntimeError("Failed to resolve required integrations")

        return integrations
