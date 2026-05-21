"""Base command class for Strata CLI commands."""

import json
import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import click

from strata.controllers.integration_controller import IntegrationController
from strata.controllers.solution_controller import SolutionController
from strata.logger import audit, configure_audit_log, get_logger, shutdown_audit
from strata.logger.context import set_context
from strata.logger.logger import reconfigure_logging
from strata.utils.config import DEFAULT_BUILD_PATH, DOCS_URL, SOLUTION_DIR, SUPPORT_URL
from strata.utils.system import generate_uuid, resolve_path, resolve_work_path
from strata.utils.version import get_version


class BaseCommand(ABC):
    """Base command class for Strata CLI commands."""

    OPERATION = "base_command"
    INIT_REQUIRED = True  # By default, commands require an initialized solution (solution.json)

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

        # Correlation IDs — set during _initialize()
        self._solution_controller: SolutionController = SolutionController(self._work_path)
        self._execution_id: str = generate_uuid()

        # Structured result data
        self._output_data: dict = {}
        self._output_format = output or "console"
        self._output_verbose = verbose or False
        self._output_quiet = quiet or False

        # Logging context (must be after _output_verbose/_output_quiet are set)
        self._configure_session_logging()

        # Integration controller (lazy-loaded)
        self._integration_controller: Optional[IntegrationController] = None

        # Merged configuration from active profile configfile_paths (set during _initialize)
        self._merged_config: Optional[Dict[str, Any]] = None

        # Message and error accumulation
        self._messages: List[str] = []
        self._errors: List[str] = []

    @abstractmethod
    def execute(self, *args, **kwargs):
        """Execute the command. To be implemented by subclasses."""
        raise NotImplementedError("Subclasses must implement the execute method.")

    def _get_build_path(self) -> Path:
        """Return the resolved build output path.

        Uses ``_configuration_service.get_default_build_path()`` when the
        configuration service is already loaded (i.e. after ``_before_execute``
        has run), otherwise falls back to ``work_path / DEFAULT_BUILD_PATH``.
        """
        if getattr(self, "_configuration_service", None) is not None:
            return self._configuration_service.get_default_build_path(  # type: ignore[union-attr]
                self._work_path, create_path=False
            )
        return self._work_path / DEFAULT_BUILD_PATH

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
        click.echo("─" * 80)
        click.echo(f"🚀 Strata — CLI (v{get_version()})")
        click.echo("─" * 80)
        click.echo("Automates workspace preparation, configuration, and deployment.")
        click.echo(f"⏱️   Timestamp       : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        click.echo(f"📜  Entry point     : {' '.join(sys.argv)}")
        click.echo(f"📂  Current dir     : {os.getcwd()}")
        if work_path:
            click.echo(f"📁  Work path       : {work_path}")

    @classmethod
    def show_console_footer(cls) -> None:
        click.echo("─" * 80)
        click.echo("✨ Thank you for using Strata CLI!")
        click.echo(f"📘 Documentation: {DOCS_URL}")
        click.echo(f"💬 Support: {SUPPORT_URL}")
        click.echo("─" * 80)

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

    # Public helper methods

    def get_integration_controller(self) -> IntegrationController:
        """
        Get or create the IntegrationController instance (lazy-loaded).

        Returns:
            IntegrationController: The controller instance
        """
        if self._integration_controller is None:
            self._integration_controller = IntegrationController()
        return self._integration_controller

    # Lifecycle methods

    def _initialize(self, show_header: bool = True) -> bool:
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
            # (e.g. during `strata sln init` itself)
            solution_id: str = "unknown"
            solution_path = SolutionController.get_solution_json_path(self._work_path)
            if solution_path.exists():
                self._solution_controller.load()
                solution_id = self._solution_controller.get_solution_id()
            elif self.INIT_REQUIRED:
                error_msg = f"Solution configuration not found at {solution_path}. This command requires an initialized solution. Please run 'strata sln init' first."
                self.logger.error(error_msg)
                self._errors.append(error_msg)
                return False

            set_context({"solution_id": solution_id, "execution_id": self._execution_id})

            # Configure audit log (separate from application logs)
            audit_path = self._work_path / SOLUTION_DIR / "audit.log"
            configure_audit_log(log_path=str(audit_path))

            # Start session operation if specified
            self._start_session_operation()

            # Load env-file sources from active profile and inject into os.environ
            self._load_env_sources()

            # Load and merge configfile_paths from active profile
            self._load_config_sources()

            if show_header and self._is_console_output():
                self.show_console_header()

            self.logger.debug(
                "Command initialized successfully",
                extra={
                    "command_class": self.__class__.__name__,
                    "solution_id": solution_id,
                    "execution_id": self._execution_id,
                },
            )

            return True
        except Exception as e:
            self.logger.error(
                "Failed to initialize command",
                extra={
                    "error": str(e),
                    "exc_info": True,
                    "execution_id": self._execution_id,
                    "command_class": self.__class__.__name__,
                },
            )
            self._errors.append(f"Failed to initialize command: {e}")
            return False

    def _before_execute(self) -> bool:
        """Optional steps before main execution."""
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
        self.logger.debug(
            "Finalizing command execution",
            extra={"command_class": self.__class__.__name__, "success": success},
        )

        if self._is_ndjson_output():
            # ── NDJSON mode: emit final "complete" event ──────────────────────
            from datetime import timezone

            self.emit_ndjson(
                {
                    "event": "complete",
                    "success": bool(success),
                    "command": self.OPERATION,
                    "execution_id": self._execution_id,
                    "timestamp": self._start_time.isoformat(),
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "data": self._output_data,
                    "messages": self._messages,
                    "errors": self._errors,
                }
            )

        elif self._is_structured_output():
            # ── Structured output (--output json / text) ─────────────────────
            envelope: Dict[str, Any] = {
                "success": bool(success),
                "command": self.OPERATION,
                "execution_id": self._execution_id,
                "timestamp": self._start_time.isoformat(),
                "data": self._output_data,
                "messages": self._messages,
                "errors": self._errors,
            }
            if self._output_format == "json":
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                click.echo(f"success: {envelope['success']}")
                click.echo(f"command: {envelope['command']}")
                click.echo(f"execution_id: {envelope['execution_id']}")
                click.echo(f"timestamp: {envelope['timestamp']}")
                for k, v in envelope["data"].items():
                    if isinstance(v, list):
                        click.echo(f"{k}:")
                        for item in v:
                            if isinstance(item, dict):
                                first = True
                                for ik, iv in item.items():
                                    prefix = "  - " if first else "    "
                                    click.echo(f"{prefix}{ik}: {iv}")
                                    first = False
                            else:
                                click.echo(f"  - {item}")
                    elif isinstance(v, dict):
                        click.echo(f"{k}:")
                        for dk, dv in v.items():
                            click.echo(f"  {dk}: {dv}")
                    else:
                        click.echo(f"{k}: {v}")
                if envelope["messages"]:
                    click.echo("messages:")
                    for m in envelope["messages"]:
                        click.echo(f"  - {m}")
                if envelope["errors"]:
                    click.echo("errors:")
                    for err in envelope["errors"]:
                        click.echo(f"  - {err}")

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
                click.echo("❌  Errors:", err=True)
                for err in self._errors:
                    click.secho(f"    - {err}", fg="red", err=True)
                click.echo("", err=True)

            # Show verbose logging
            if self._is_verbose():
                self._print_verbose_logs()

            # Show footer
            self.show_console_footer()

        self._end_time = datetime.now()
        duration = self._end_time - self._start_time

        # Emit audit entry for every command execution
        audit(
            f"command.{self.OPERATION}",
            outcome="success" if success else "failure",
            target=" ".join(sys.argv[1:]) if len(sys.argv) > 1 else self.OPERATION,
            detail={
                "execution_id": self._execution_id,
                "duration_ms": round(duration.total_seconds() * 1000),
                "error_count": len(self._errors),
            },
        )
        shutdown_audit()

        self.logger.debug(
            "Command execution completed",
            extra={
                "command_class": self.__class__.__name__,
                "duration_seconds": duration.total_seconds(),
            },
        )

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
        return bool(self._output_format) and self._output_format not in ("console", "ndjson")

    def _is_ndjson_output(self) -> bool:
        """Return True when --output ndjson is active: emit events as newline-delimited JSON."""
        return self._output_format == "ndjson"

    def _is_console_output(self) -> bool:
        """Return True for default human-readable console output (no --quiet, no explicit --output format)."""
        return not self._output_quiet and (not bool(self._output_format) or self._output_format == "console")

    def emit_ndjson(self, event: Dict[str, Any]) -> None:
        """Emit one NDJSON event line immediately to stdout.

        Each call writes exactly one ``\\n``-terminated JSON object.  Callers
        should add a ``"ts"`` field with the current ISO-8601 timestamp when the
        event carries timing information.
        """
        click.echo(json.dumps(event, default=str), nl=True)

    def make_ndjson_line_callback(self, step: str, stage: Optional[str] = None) -> "Callable[[str, str], None]":
        """Return a ``line_callback`` that emits NDJSON ``line`` events for every subprocess output line.

        The returned callable signature matches the one expected by
        ``run_command(line_callback=...)`` and ``TerraformDeployer`` step methods:
        ``(stream: str, text: str) -> None``.
        """
        from datetime import timezone

        def _cb(stream: str, text: str) -> None:
            event: Dict[str, Any] = {
                "event": "line",
                "step": step,
                "stream": stream,
                "text": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
            if stage is not None:
                event["stage"] = stage
            self.emit_ndjson(event)

        return _cb

    # Internal helper methods

    # Configure session logging based on .strata/logging.yaml if it exists, and refresh logger
    def _configure_session_logging(self) -> None:
        """
        Auto-configure logging from session-specific YAML if available.

        Looks for ``.strata/logging.yaml`` under ``self._work_path``.
        Reconfigures logging only when the discovered config path changes.
        Falls back to enabling a default session log file for crash diagnostics.
        """
        # Logger must exist — raise immediately if it cannot be created.
        self.logger = get_logger(self.__class__.__module__)

        try:
            logging_config = SolutionController.get_logging_config_path(self._work_path)
            if logging_config is None:
                self._configure_default_session_log()
                return

            if not logging_config.exists():
                self._configure_default_session_log()
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
            try:
                self.logger.warning(
                    f"Failed to auto-configure session logging: {str(e)}",
                    extra={"command_class": self.__class__.__name__},
                )
            except Exception:
                # Best-effort only; never raise from logging setup
                pass

    def _configure_default_session_log(self) -> None:
        """Enable a JSON session log file for crash diagnostics when no YAML config exists."""
        from datetime import date

        log_dir = self._work_path / SOLUTION_DIR / "logs"
        if not log_dir.parent.exists():
            return  # work_path/.strata/ doesn't exist yet (pre-init)

        session_log = log_dir / f"session-{date.today().isoformat()}.json"
        level = "DEBUG" if self._output_verbose else "WARNING"
        reconfigure_logging(
            level=level,
            enable_console=not self._output_quiet,
            enable_json_file=True,
            log_file_path=str(session_log),
        )
        self.logger = get_logger(self.__class__.__module__)

    # Get the work path based on input or default to current directory
    def _get_current_workpath(self, work_path: Optional[str]) -> Path:
        """Resolve the workspace root — delegates to ``utils.system.resolve_work_path``."""
        return resolve_work_path(work_path or None)

    # Print log lines for the current execution when --verbose is active
    def _print_verbose_logs(self) -> None:
        """Print log lines for the current execution when --verbose is active."""
        try:
            ok, entries, _ = self._solution_controller.get_logs(
                work_path=self._work_path,
                execution_id=self._execution_id,
            )
            if not ok or not entries:
                return
            click.echo()
            click.echo("─" * 80)
            click.echo(f"📋  Execution log  [{self._execution_id}]")
            click.echo("─" * 80)
            for entry in entries:
                ts = entry.get("timestamp", "")
                lvl = entry.get("level", "").upper()
                msg = entry.get("event", entry.get("message", ""))
                click.echo(f"  {ts}  {lvl:<8}  {msg}")
        except Exception as e:
            self.logger.debug(f"Could not display verbose logs: {e}")

    # Start a session operation for tracking in SessionController (e.g., for last_execution_id)
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
            self._solution_controller.update_last_execution(self._execution_id)
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

    # Load env-file sources from cli.yaml and inject into os.environ
    def _load_env_sources(self) -> None:
        """Inject the active profile's ``envfile`` paths into ``os.environ``.

        Reads ``envfile_paths`` from the currently active profile in
        ``solution.json``, resolves ``@repo_name/…`` references, parses each
        ``.env`` file in declaration order, and injects the variables into the
        current process environment.

        Runs after solution load so ``@repo_name/…`` paths can be resolved.
        Non-fatal — missing files produce debug warnings, never block execution.
        """
        try:
            from strata.utils.system import resolve_path

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                return

            envfile_paths = profile.envfile_paths or []
            if not envfile_paths:
                return

            repo_map: Dict[str, str] = self._solution_controller.get_repo_map()

            from strata.controllers.env_controller import EnvController

            for entry in envfile_paths:
                raw_path = str(entry.path)
                name = str(entry.name)
                try:
                    resolved = resolve_path(str(self._work_path), raw_path, repo_map=repo_map)
                except ValueError as e:
                    self.logger.debug(f"Env source '{name}': {e}")
                    continue

                if not resolved.exists():
                    self.logger.debug(f"Env source '{name}': file not found at {resolved}")
                    continue

                file_vars = EnvController._parse_env_file(resolved)
                for key, value in file_vars.items():
                    os.environ[key] = value
                self.logger.debug(
                    "Loaded env source from active profile",
                    name=name,
                    path=str(resolved),
                    vars_count=len(file_vars),
                )

        except Exception as e:
            # Env loading must never block command execution
            self.logger.debug(f"Failed to load env sources: {e}")

    def _load_config_sources(self) -> None:
        """Load and deep-merge the active profile's ``configfile_paths`` into ``self._merged_config``.

        Files are merged in declaration order (later entries override earlier ones).
        The merged result is also written to ``.strata/configuration.yaml`` as a
        debug artifact — write failures are non-fatal.

        Runs after ``_load_env_sources()`` so env vars are available for any
        variable substitution performed by downstream consumers.
        """
        try:
            from strata.utils.config import SOLUTION_CONFIGURATION_FILE, SOLUTION_DIR
            from strata.utils.configuration_loader import ConfigurationLoader
            from strata.utils.system import resolve_path

            profile, _ = self._solution_controller.get_active_profile()
            if profile is None:
                return

            configfile_paths = profile.configfile_paths or []
            if not configfile_paths:
                return

            repo_map: Dict[str, str] = self._solution_controller.get_repo_map()

            resolved_paths: List[Path] = []
            for entry in configfile_paths:
                name = str(entry.name)
                try:
                    resolved = resolve_path(str(self._work_path), str(entry.path), repo_map=repo_map)
                except ValueError as e:
                    self.logger.debug(f"Config source '{name}': {e}")
                    continue
                if not resolved.exists():
                    self.logger.debug(f"Config source '{name}': file not found at {resolved}")
                    continue
                resolved_paths.append(resolved)

            if not resolved_paths:
                return

            loader = ConfigurationLoader()
            merged = loader.load_and_merge_yaml_files(resolved_paths)
            self._merged_config = merged
            self.logger.debug(
                "Loaded and merged config sources from active profile",
                files=len(resolved_paths),
                keys=len(merged),
            )

            # Write debug artifact — non-fatal
            try:
                import yaml

                debug_path = self._work_path / SOLUTION_DIR / SOLUTION_CONFIGURATION_FILE
                with open(debug_path, "w", encoding="utf-8") as fh:
                    yaml.safe_dump(merged, fh, sort_keys=False)
            except Exception as e:
                self.logger.warning(f"Failed to write merged config debug artifact: {e}")

        except Exception as e:
            # Config loading must never block command execution
            self.logger.debug(f"Failed to load config sources: {e}")

    # Validate declared integration requirements (e.g., check if 'git' is available for 'repository clone operations')
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

        integration_controller = self.get_integration_controller()

        for integration_name, operation_desc in required.items():
            is_available, error_msg = integration_controller.ensure_integration_available(
                integration_name, operation_desc
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
