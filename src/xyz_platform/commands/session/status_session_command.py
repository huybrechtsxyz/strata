#!/usr/bin/env python3
"""
===============================================================================
Script Name   : status_session_command.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to check on-disk status of session workspace items.
===============================================================================
"""

from typing import Optional

import click

from xyz_platform.commands.session.base_session_command import BaseSessionCommand


class StatusSessionCommand(BaseSessionCommand):
    """
    Check on-disk status of all items tracked in the session workspace.

    For each registered repository reports whether the folder exists on disk,
    the currently checked-out git branch, and whether it matches the
    registered branch.  Status is one of: ok, missing, branch_mismatch.
    """

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
    ):
        """
        Initialize the status command.

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
        self._status_items: list = []

    def execute(self) -> bool:
        """
        Execute the status command.

        Returns:
            bool: Success status (errors stored in self._errors)
        """
        try:
            if not self._initialize(operation="session_status"):
                self.logger.error(f"Initialization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="session_status", success=False)
                return False

            if not self._before_execute():
                self.logger.error(
                    f"Pre-execution validation failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="session_status", success=False)
                return False

            self._status_items = self._session_controller.get_session_status(
                work_path=self._work_path,
            )

            if not self._after_execute():
                self.logger.error(
                    f"Post-execution hook failed in {self.__class__.__name__}"
                )
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="session_status", success=False)
                return False

            if not self._finalize(operation="session_status", success=True):
                self.logger.error(f"Finalization failed in {self.__class__.__name__}")
                if self._is_console_output():
                    click.echo("\n❌  Finalization failed")
                return False

            return True

        except Exception as e:
            error_msg = f"Failed to get session status: {str(e)}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(operation="session_status", success=False)
            return False

    def _after_execute(self) -> bool:
        """Populate output data and render console feedback."""
        _STATUS_ICON = {"ok": "✅", "missing": "❌", "branch_mismatch": "⚠️ "}

        self._output_data = {"repositories": self._status_items}

        if self._is_console_output():
            if not self._status_items:
                click.echo("\n📊  No items in session workspace.\n")
            else:
                click.echo(f"\n📊  Session status ({len(self._status_items)} items):")
                for item in self._status_items:
                    icon = _STATUS_ICON.get(item.get("status", "ok"), "❓")
                    click.echo(f"    {icon}  {item.get('name', '—')}")
                    if item.get("type"):
                        click.echo(f"         Type:    {item['type']}")
                    if item.get("url"):
                        click.echo(f"         URL:     {item['url']}")
                    reg = item.get("registered_branch")
                    cur = item.get("current_branch")
                    if reg:
                        click.echo(
                            f"         Branch:  registered={reg}  current={cur or '—'}"
                        )
                    if item.get("status") == "missing":
                        click.echo(f"         ⚠  Folder not found on disk")
                    elif item.get("status") == "branch_mismatch":
                        click.echo(
                            f"         ⚠  Branch mismatch — run 'git checkout {reg}'"
                        )
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
                    "repositories": self._status_items,
                    "messages": self._messages,
                    "errors": self._errors,
                }
                click.echo(json.dumps(envelope, indent=2, default=str))
            else:  # text
                for item in self._status_items:
                    parts = [
                        item.get("name", ""),
                        item.get("status", ""),
                        "exists" if item.get("folder_exists") else "missing",
                        item.get("current_branch") or "",
                        item.get("registered_branch") or "",
                    ]
                    click.echo("  ".join(p for p in parts if p))
                if self._errors:
                    for err in self._errors:
                        click.echo(f"error: {err}")

            fmt, self._output_format = self._output_format, ""
            result = super()._finalize(
                operation=operation, success=success, show_footer=False
            )
            self._output_format = fmt
            return result

        return super()._finalize(
            operation=operation, success=success, show_footer=show_footer
        )
