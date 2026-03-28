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

from xyz_platform.controllers.integration_controller import IntegrationController
from xyz_platform.controllers.lifecycle_controller import LifecycleController
from xyz_platform.controllers.session_controller import SessionController
from xyz_platform.controllers.workspace_controller import WorkspaceController
from xyz_platform.logger.logger import get_logger, reconfigure_logging
from xyz_platform.logger.context import set_context
from xyz_platform.services.base_service import BaseService
from xyz_platform.utils import system
from xyz_platform.utils.system import generate_uuid


class BaseCommand:
    """Base command class for the XYZ Platform. All CLI commands should inherit from this class to ensure consistent behavior and shared functionality."""

    _active_logging_config_path: Optional[str] = None

    OPERATION = "base_command"  # Default operation name, subclasses should override this with specific operation names (e.g., 'validate_module', 'deploy_workspace', etc.)

    # Initialization parameters common to all commands

    def __init__(
        self,
        file_path: Optional[str] = None,
        work_path: Optional[str] = None,
        env_path: Optional[str] = None,
        env_file: Optional[str] = None,
        no_hooks: bool = False,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ):
        """Initialize the base command."""

        # Initialize logger with the actual subclass module name
        self.logger = get_logger(self.__class__.__module__)

        # Timer attributes
        self._start_time: datetime
        self._end_time: datetime

        # Standard paths
        self._file_path = file_path
        self._work_path = Path(work_path) if work_path else Path.cwd()
        self._env_path = Path(env_path) if env_path else None
        self._env_file = Path(env_file) if env_file else None
        self._build_path = None
        self._object_path = None
        self._dist_path = None

        # Execution flags
        self._no_hooks = no_hooks

        # Message and error accumulation
        self._messages = []
        self._errors = []

        # Structured result data — populated by each command's _after_execute()
        # Used when --output json/text is requested
        self._output_data: dict = {}

        # Output format and flags
        self._output_format = output or "console"
        self._output_verbose = verbose or False
        self._output_quiet = quiet or False

        # Correlation IDs — set during _initialize()
        self._project_id: str
        self._execution_id: str

        # Session controller — one instance per command run
        self._session_controller = SessionController()

        # Integration controller (lazy-loaded)
        self._integration_controller: Optional[IntegrationController] = None

        # Lifecycle controller (lazy-loaded)
        self._lifecycle_controller: Optional[LifecycleController] = None

        # Workspace controller (lazy-loaded)
        self._workspace_controller: Optional[WorkspaceController] = None

    # Abstract method to be implemented by subclasses

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

    # Integration requirements declaration — subclasses override this to declare what external tools they need

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

    def ShowConsoleHeader(self, work_path: Optional[str] = None) -> None:
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

    def ShowConsoleFooter(self) -> None:
        click.echo("=" * 80)
        click.echo("✨ Thank you for using XYZ Platform CLI!")
        click.echo("📘 Documentation: https://docs.xyzplatform.com")
        click.echo("💬 Support: https://support.xyzplatform.com")
        click.echo("=" * 80)

    # Lifecycle methods

    def _initialize(
        self, require_project: bool = True, show_header: bool = True
    ) -> bool:
        """
        Initialize the command before execution.
        Sets up paths, timing, and logging context.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            self._configure_session_logging()
            self._start_time = datetime.now()
            self.logger.debug(
                "Initializing command",
                extra={"command_class": self.__class__.__name__},
            )

            # Set up work path (default to current directory)
            self._work_path = self._get_workspace_controller().get_workspace_workpath(
                self._work_path
            )
            if not self._work_path.exists():
                error_msg = f"Work path does not exist: {self._work_path}"
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            # Load state.json into memory once for this command run
            if not self._session_controller.load_project(self._work_path):
                if require_project:
                    error_msg = "No project found. Please run 'xyz project init' first."
                    self.logger.error(error_msg)
                    self._errors.append(error_msg)
                    return False

            # Assign correlation IDs (UUID v7 — time-ordered)
            self._project_id = (
                self._session_controller.get_project_id() or generate_uuid()
            )
            self._execution_id = generate_uuid()
            set_context(
                {"project_id": self._project_id, "execution_id": self._execution_id}
            )

            # Start session operation if specified
            self._start_session_operation()
            self.logger.debug(
                "Initializing command",
                extra={
                    "command_class": self.__class__.__name__,
                    "project_id": self._project_id,
                    "execution_id": self._execution_id,
                },
            )

            if show_header and self._is_console_output():
                self.ShowConsoleHeader()

            (
                success,
                errors,
            ) = self._get_workspace_controller().load_environment_variables(
                self._work_path, env_path=self._env_path, env_file=self._env_file
            )
            if not success:
                if errors:
                    self._errors.extend(errors)
                return False
            self.logger.debug(
                "Environment variables loaded",
                extra={"command_class": self.__class__.__name__},
            )

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

    def _before_execute(self) -> bool:
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

        self.logger.debug(
            "Post-command logic executed successfully",
            extra={"command_class": self.__class__.__name__},
        )
        return True

    def _finalize(
        self,
        success: bool = False,
        show_footer: bool = True,
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
                "command": self.OPERATION,
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
                success, log_entries, errors = self._session_controller.get_logs(
                    work_path=self._work_path, execution_id=self._execution_id
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
        duration = self._end_time - self._start_time
        self.logger.debug(
            "Command execution completed",
            extra={
                "command_class": self.__class__.__name__,
                "duration_seconds": duration.total_seconds(),
            },
        )

        # Complete the named operation in session state
        self._complete_session_operation(success=success)
        return True

    # Output and logging

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
        return (
            not self._output_quiet
            and not bool(self._output_format)
            or self._output_format == "console"
        )

    # Integration resolution and validation

    def _get_integration_controller(self) -> IntegrationController:
        """
        Get or create the IntegrationController instance (lazy-loaded).

        Returns:
            IntegrationController: The controller instance
        """
        if self._integration_controller is None:
            self._integration_controller = IntegrationController()
        return self._integration_controller

    def _get_lifecycle_controller(self) -> LifecycleController:
        """
        Get or create the LifecycleController instance (lazy-loaded).

        Returns:
            LifecycleController: The controller instance
        """
        if self._lifecycle_controller is None:
            self._lifecycle_controller = LifecycleController(enable_templating=True)
        return self._lifecycle_controller

    def _get_workspace_controller(self) -> WorkspaceController:
        """
        Get or create the WorkspaceController instance (lazy-loaded).

        Returns:
            WorkspaceController: The controller instance
        """
        if self._workspace_controller is None:
            self._workspace_controller = WorkspaceController()
        return self._workspace_controller

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

    # Session operation helpers

    def _start_session_operation(self) -> None:
        """
        Mark the start of a named operation in the session state.

        Records the current execution_id against the session and persists it.
        The current SessionController tracks last-execution rather than named
        operation trees, so this calls update_last_execution + save_session.

        Args:
            operation: Name of the operation (e.g., 'tools_status', 'session_init')
        """
        try:
            self._session_controller.update_last_execution(self._execution_id)
            self._session_controller.save_session()
            self.logger.debug(
                f"Started session operation: {self.OPERATION}",
                extra={"operation": self.OPERATION, "execution_id": self._execution_id},
            )
        except Exception as e:
            # Session tracking must never block command execution
            self.logger.warning(
                f"Failed to start session operation '{self.OPERATION}': {e}",
                extra={"operation": self.OPERATION},
            )

    def _complete_session_operation(self, success: bool, flush: bool = True) -> None:
        """
        Mark the completion of a named operation in the session state.

        Persists session to disk so the last-execution record reflects the
        completed run.

        Args:
            operation: Name of the operation (e.g., 'tools_status', 'session_init')
            success: Whether the operation completed successfully
            flush: Whether to save session state to disk (default: True)
        """
        try:
            if flush:
                self._session_controller.save_session()
            self.logger.debug(
                f"Completed session operation: {self.OPERATION}",
                extra={"operation": self.OPERATION, "success": success},
            )
        except Exception as e:
            self.logger.warning(
                f"Failed to complete session operation '{self.OPERATION}': {e}",
                extra={"operation": self.OPERATION},
            )

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

    # Lifecycle hooks and validation

    def _execute_config_hooks(
        self,
        phase_name: str,
        context: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Any] = None,
    ) -> bool:
        """
        Execute a list of lifecycle hooks (scripts/commands).

        Args:

        Returns:
            bool: True if all hooks executed successfully, False otherwise (errors stored in self._errors)
        """
        if self._no_hooks:
            self.logger.debug(
                "Skipping configuration lifecycle hooks execution (--no-hooks)"
            )
            return True

        self.logger.debug(
            f"Executing configuration lifecycle hooks for phase: {phase_name}",
            extra={"phase": phase_name},
        )

        lifecycle_controller = self._get_lifecycle_controller()
        success, errors = lifecycle_controller.execute_configuration_phase(
            phase_name=phase_name,
            work_path=self._work_path,
            context=context,
            progress_callback=progress_callback,
        )

        if not success and errors:
            self._errors.extend(errors)
            self.logger.error(
                f"Configuration lifecycle hooks execution failed for phase: {phase_name}",
                extra={"phase": phase_name, "errors": errors},
            )
        elif success:
            self.logger.debug(
                f"Configuration lifecycle hooks executed successfully for phase: {phase_name}",
                extra={"phase": phase_name},
            )
        return success

    def _execute_workspace_hooks(
        self,
        base_service: BaseService,
        phase_name: str,
        context: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Any] = None,
        add_config_model: bool = False,
    ) -> bool:
        """
        Execute a list of lifecycle hooks (scripts/commands).

        Args:

        Returns:
            bool: True if all hooks executed successfully, False otherwise (errors stored in self._errors)
        """
        if self._no_hooks:
            self.logger.debug(
                "Skipping workspace lifecycle hooks execution (--no-hooks)"
            )
            return True

        self.logger.debug(
            f"Executing workspace lifecycle hooks for phase: {phase_name}",
            extra={"phase": phase_name},
        )

        lifecycle_controller = self._get_lifecycle_controller()
        success, errors = lifecycle_controller.execute_workspace_phase(
            base_service=base_service,
            phase_name=phase_name,
            work_path=self._work_path,
            context=context,
            progress_callback=progress_callback,
            add_config_model=add_config_model,
        )

        if not success and errors:
            self._errors.extend(errors)
            self.logger.error(
                f"Workspace lifecycle hooks execution failed for phase: {phase_name}",
                extra={"phase": phase_name, "errors": errors},
            )
        elif success:
            self.logger.debug(
                f"Workspace lifecycle hooks executed successfully for phase: {phase_name}",
                extra={"phase": phase_name},
            )
        return success
