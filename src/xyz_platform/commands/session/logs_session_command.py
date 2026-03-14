#!/usr/bin/env python3
"""
===============================================================================
Script Name   : logs_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to display session execution logs.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand
from xyz_platform.controllers.session_controller import SessionController


class LogsSessionCommand(BaseSessionCommand):
    """
    Display session execution logs.

    Reads the active log file for the current session and displays
    entries filtered by time window, level, session ID, or execution ID.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        lines: int = 50,
        minutes: Optional[int] = None,
        level: Optional[str] = None,
        session_id: Optional[str] = None,
        execution_id: Optional[str] = None,
        use_current_session: bool = False,
        use_last_execution: bool = False,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        """
        Initialize the session logs command.

        Args:
            work_path: Working directory path
            lines: Number of log lines to show (default: 50)
            minutes: Show logs from last N minutes
            level: Filter by log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            session_id: Filter by session ID
            execution_id: Filter by execution ID
            use_current_session: Auto-fill session_id from the current session
            use_last_execution: Auto-fill execution_id from the last log entry
            output: Output format (json, text)
            verbose: Enable verbose output
            quiet: Suppress all console output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._lines = lines
        self._minutes = minutes
        self._level = level.upper() if level else None
        # Use distinct names to avoid collision with BaseCommand.session_id / .execution_id
        self._filter_session_id = session_id
        self._filter_execution_id = execution_id
        self._use_current_session = use_current_session
        self._use_last_execution = use_last_execution
        # Fetched log entries — shared between _after_execute and _output_data
        self._log_entries = []

    def execute(self) -> bool:
        """
        Execute the session logs command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            # Initialize
            if not self._initialize(operation="session_logs"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_logs", success=False)
                return False

            # Before
            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_logs", success=False)
                return False

            # Resolve --session flag: use current session_id set by _initialize()
            if self._use_current_session and not self._filter_session_id:
                self._filter_session_id = self.session_id
                self.logger.debug(f"Using current session ID: {self.session_id}")

            # Resolve --last-exec flag: read last_execution_id from session.json
            if self._use_last_execution and not self._filter_execution_id:
                try:
                    import json as _json

                    session_file = self._work_path / ".xyz-platform" / "session.json"
                    if session_file.exists():
                        with open(session_file, "r", encoding="utf-8") as f:
                            _data = _json.load(f)
                        eid = _data.get("session", {}).get("last_execution_id")
                        if eid:
                            self._filter_execution_id = eid
                            self.logger.debug(f"Using last execution ID: {eid}")
                        else:
                            self.logger.warning(
                                "No last_execution_id found in session.json"
                            )
                except Exception as e:
                    self.logger.warning(f"Could not read last execution ID: {e}")

            # Fetch logs
            controller = SessionController()
            success, self._log_entries, errors = controller.get_logs(
                work_path=self._work_path,
                lines=self._lines,
                minutes=self._minutes,
                level=self._level,
                session_id=self._filter_session_id,
                execution_id=self._filter_execution_id,
            )
            self._errors.extend(errors)

            if not success:
                self.logger.error(
                    f"Failed to retrieve logs in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Failed to retrieve session logs")
                self._finalize(operation="session_logs", success=False)
                return False

            # After
            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_logs", success=False)
                return False

            # Finalize
            if not self._finalize(operation="session_logs", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to display session logs: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_logs", success=False)
            return False

    def _after_execute(self) -> bool:
        """
        Render log output and populate structured output data.

        Returns:
            bool: Success status
        """
        # Build active filter summary
        filters = {}
        if self._lines:
            filters["lines"] = self._lines
        if self._minutes:
            filters["minutes"] = self._minutes
        if self._level:
            filters["level"] = self._level
        if self._filter_session_id:
            filters["session_id"] = self._filter_session_id
        if self._filter_execution_id:
            filters["execution_id"] = self._filter_execution_id

        # Always populate structured output data (used by _finalize for json/text)
        if not self._is_quiet():
            self._output_data = {
                "total_entries": len(self._log_entries),
                "filters": filters,
                "log_entries": self._log_entries,
            }

        # Console rendering
        if self._is_console_output():
            click.echo("")
            click.echo("📋  Session Logs")

            if filters:
                parts = []
                if "lines" in filters:
                    parts.append(f"lines={filters['lines']}")
                if "minutes" in filters:
                    parts.append(f"last {filters['minutes']}min")
                if "level" in filters:
                    parts.append(f"level={filters['level']}")
                if "session_id" in filters:
                    parts.append(f"session={filters['session_id'][:8]}...")
                if "execution_id" in filters:
                    parts.append(f"execution={filters['execution_id'][:8]}...")
                click.echo(f"    Filters: {' | '.join(parts)}")
                click.echo("")

            if not self._log_entries:
                click.echo("ℹ️   No log entries found")
                click.echo("")
                return super()._after_execute()

            for log in self._log_entries:
                timestamp = log.get("timestamp", "N/A")
                level = (log.get("level") or log.get("levelname") or "INFO").upper()
                message = log.get("message", "")
                exec_id = log.get("execution_id", "")

                line = f"[{timestamp}] {level:8} - {message}"
                if level == "ERROR":
                    click.secho(line, fg="red")
                elif level == "WARNING":
                    click.secho(line, fg="yellow")
                elif level == "DEBUG":
                    click.secho(line, fg="cyan")
                else:
                    click.echo(line)

                if self._is_verbose():
                    extra = log.get("extra", {})
                    if extra:
                        click.secho(f"    Extra: {extra}", fg="cyan")
                    if exec_id and exec_id != self._filter_execution_id:
                        click.secho(f"    Execution ID: {exec_id}", fg="cyan")

            click.echo("")
            click.echo(f"Total log entries: {len(self._log_entries)}")
            click.echo("")

        return super()._after_execute()

    def _finalize(self, operation: str = None, success: bool = None) -> bool:
        """
        Finalize logs command.

        Overrides the base structured-output renderer so that:
        - ``--output json``  emits a clean JSON envelope with log_entries as an array
        - ``--output text``  emits plain ``[timestamp] LEVEL - message`` lines,
          one per line, suitable for piping or redirect (no colours, no JSON)
        """
        if self._is_structured_output():
            if self._output_format == "json":
                import json

                envelope = {
                    "success": bool(success),
                    "command": operation or "",
                    "total_entries": len(self._log_entries),
                    "filters": self._output_data.get("filters", {}),
                    "log_entries": self._log_entries,
                    "messages": self._messages,
                    "errors": self._errors,
                }
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                if self._log_entries:
                    for log in self._log_entries:
                        timestamp = log.get("timestamp", "N/A")
                        level = (
                            log.get("level") or log.get("levelname") or "INFO"
                        ).upper()
                        message = log.get("message", "")
                        click.echo(f"[{timestamp}] {level:8} - {message}")
                if self._errors:
                    for err in self._errors:
                        click.echo(f"ERROR: {err}")
            # Suppress _output_format so the base _finalize only runs its
            # timing/logging side-effects and does not re-render structured output.
            fmt, self._output_format = self._output_format, ""
            result = super()._finalize(operation=operation, success=success)
            self._output_format = fmt
            return result

        return super()._finalize(operation=operation, success=success)
