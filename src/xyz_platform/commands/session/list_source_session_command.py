#!/usr/bin/env python3
"""
===============================================================================
Script Name   : list_source_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to list items in an XYZ Platform session.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.base_command import BaseCommand


class ListSourceSessionCommand(BaseCommand):
    """
    List items tracked in the XYZ Platform session workspace.

    Displays all repositories currently registered in session.json,
    showing their name, type, URL, and branch.
    """

    OPERATION_NAME = "session_source_list"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
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
            if not self._initialize():
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            self._repositories = self._session_controller.get_repositories()

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            if not self._finalize(success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to list session items: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _initialize(
        self, require_session: bool = False, show_header: bool = True
    ) -> bool:
        if not super()._initialize(require_session, show_header):
            return False
        self.logger.debug("List source session command initializing")
        return True

    def _before_execute(self) -> bool:
        """Validate session state before execution."""
        if not super()._before_execute():
            return False
        self.logger.debug("List source session command pre-execution validation")
        return True

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

        self.logger.debug("List source session command post-execution processing")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """Override structured output renderer for --output json/text."""
        if self._is_structured_output():
            if self._output_format == "json":
                import json

                envelope = {
                    "success": bool(success),
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
            self._output_format = ""
            show_footer = False

        return super()._finalize(success=success, show_footer=show_footer)
