#!/usr/bin/env python3
"""
===============================================================================
Script Name   : show_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to display XYZ Platform session details.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class ShowSessionCommand(BaseSessionCommand):
    """
    Display details of the current XYZ Platform session.

    Shows session ID, workspace name, created timestamp, work path,
    last execution, logging paths, workspace/environment config,
    and a summary of tracked repositories.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
    ):
        """
        Initialize the show command.

        Args:
            work_path: Root working directory
            output: Output format (json, text)
            verbose: Enable verbose output
        """
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
        )
        self._session_info: dict = {}

    def execute(self) -> bool:
        """
        Execute the show command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            if not self._initialize(operation="session_show"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_show", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_show", success=False)
                return False

            data = self._session_controller.get_session_data() or {}
            self._session_info = {
                "session": data.get("session", {}),
                "logging": data.get("logging", {}),
                "workspace": data.get("workspace", {}),
                "environment": data.get("environment", {}),
                "repository_count": len(data.get("repositories", [])),
            }

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_show", success=False)
                return False

            if not self._finalize(operation="session_show", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to show session: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_show", success=False)
            return False

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        self._output_data = self._session_info

        if self._is_console_output():
            s = self._session_info.get("session", {})
            log = self._session_info.get("logging", {})
            ws = self._session_info.get("workspace", {})
            env = self._session_info.get("environment", {})
            repo_count = self._session_info.get("repository_count", 0)

            click.echo("\n🔍  Session details:")
            if s.get("name"):
                click.echo(f"    • Name:              {s['name']}")
            if s.get("session_id"):
                click.echo(f"    • Session ID:        {s['session_id']}")
            if s.get("created"):
                click.echo(f"    • Created:           {s['created']}")
            if s.get("work_path"):
                click.echo(f"    • Work path:         {s['work_path']}")
            if s.get("last_execution_id"):
                click.echo(f"    • Last execution:    {s['last_execution_id']}")

            if log.get("config_path") or log.get("log_path"):
                click.echo("    • Logging:")
                if log.get("config_path"):
                    click.echo(f"        Config:          {log['config_path']}")
                if log.get("log_path"):
                    click.echo(f"        Log path:        {log['log_path']}")

            if ws.get("active") or ws.get("config_path"):
                click.echo("    • Workspace:")
                if ws.get("active"):
                    click.echo(f"        Active:          {ws['active']}")
                if ws.get("config_path"):
                    click.echo(f"        Config path:     {ws['config_path']}")

            if env.get("active"):
                click.echo(f"    • Environment:       {env['active']}")

            click.echo(f"    • Repositories:      {repo_count}")
            click.echo("")

        return super()._after_execute()

    def _finalize(
        self, operation: str = "", success: bool = True, show_footer: bool = True
    ) -> bool:
        """Override structured output renderer for --output json/text."""
        if self._is_structured_output():
            if self._output_format == "json":
                import json

                envelope = {
                    "success": bool(success),
                    "command": operation or "",
                    **self._session_info,
                    "messages": self._messages,
                    "errors": self._errors,
                }
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                s = self._session_info.get("session", {})
                for key in (
                    "name",
                    "session_id",
                    "created",
                    "work_path",
                    "last_execution_id",
                ):
                    if s.get(key):
                        click.echo(f"session.{key}={s[key]}")
                log = self._session_info.get("logging", {})
                for key in ("config_path", "log_path"):
                    if log.get(key):
                        click.echo(f"logging.{key}={log[key]}")
                ws = self._session_info.get("workspace", {})
                for key in ("active", "config_path"):
                    if ws.get(key):
                        click.echo(f"workspace.{key}={ws[key]}")
                env = self._session_info.get("environment", {})
                if env.get("active"):
                    click.echo(f"environment.active={env['active']}")
                click.echo(
                    f"repository_count={self._session_info.get('repository_count', 0)}"
                )
                if self._errors:
                    for err in self._errors:
                        click.echo(f"error: {err}")

            # Suppress _output_format so base _finalize only runs side-effects
            fmt, self._output_format = self._output_format, ""
            result = super()._finalize(
                operation=operation, success=success, show_footer=False
            )
            self._output_format = fmt
            return result

        return super()._finalize(operation=operation, success=success)
