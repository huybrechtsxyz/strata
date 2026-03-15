#!/usr/bin/env python3
"""
===============================================================================
Script Name   : list_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to list items in an XYZ Platform session.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class ListSessionCommand(BaseSessionCommand):
    """
    List items tracked in the XYZ Platform session workspace.

    Displays all repositories currently registered in session.json,
    showing their name, type, URL, and branch.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
    ):
        """
        Initialize the list command.

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
        self._repositories: list = []

    def execute(self) -> bool:
        """
        Execute the list command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            if not self._initialize(operation="session_list"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_list", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_list", success=False)
                return False

            self._repositories = self._session_controller.get_repositories()

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_list", success=False)
                return False

            if not self._finalize(operation="session_list", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to list session items: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_list", success=False)
            return False

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        self._output_data = {"repositories": self._repositories}

        if self._is_console_output():
            if not self._repositories:
                click.echo("\n📋  No items in session workspace.\n")
            else:
                click.echo(f"\n📋  Session items ({len(self._repositories)}):")
                for repo in self._repositories:
                    click.echo(f"    • {repo.get('name', '—')}")
                    if repo.get("type"):
                        click.echo(f"      Type:   {repo['type']}")
                    if repo.get("url"):
                        click.echo(f"      URL:    {repo['url']}")
                    if repo.get("branch"):
                        click.echo(f"      Branch: {repo['branch']}")
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
                    "repositories": self._repositories,
                    "messages": self._messages,
                    "errors": self._errors,
                }
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                for repo in self._repositories:
                    parts = [
                        repo.get("name", ""),
                        repo.get("type", ""),
                        repo.get("url", ""),
                        repo.get("branch", ""),
                    ]
                    click.echo("  ".join(p for p in parts if p))
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
