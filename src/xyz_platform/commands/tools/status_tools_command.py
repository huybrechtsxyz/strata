"""Command to list all known integrations and their availability."""

from __future__ import annotations

import os
from typing import Dict, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.tools_controller import ToolsController
from xyz_platform.logger import get_logger


class StatusToolsCommand(BaseCommand):
    """List all known integrations and their availability status."""

    OPERATION = "tools_status"
    INIT_REQUIRED = False

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize(show_header=False):
                self._finalize(success=False, show_footer=False)
                return False

            controller = ToolsController()
            success, rows, errors = controller.status()

            for err in errors:
                self._errors.append(err)

            if self._is_console_output():
                self._print_table(rows)

            self._output_data["integrations"] = rows
            self._finalize(success=success, show_footer=False)
            return success

        except Exception as exc:
            error_msg = f"Failed to list tool integrations: {exc}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False, show_footer=False)
            return False

    def _print_table(self, rows: list) -> None:
        col_name = 22
        col_avail = 12
        col_ver = 14
        col_cmd = 12
        col_caps = 30

        header = (
            f"{'Name':<{col_name}} {'Available':<{col_avail}} {'Version':<{col_ver}}"
            f" {'Command':<{col_cmd}} {'Capabilities':<{col_caps}}"
        )
        separator = "-" * len(header)

        click.echo("")
        click.echo("Integration Status")
        click.echo(separator)
        click.echo(header)
        click.echo(separator)

        for row in rows:
            avail_icon = "✓" if row["available"] else "✗"
            version_str = row["version"] or "not found"
            command_str = row["command"] or "-"
            caps_str = ", ".join(row["capabilities"]) if row["capabilities"] else "-"

            click.echo(
                f"{row['name']:<{col_name}} {avail_icon:<{col_avail}} {version_str:<{col_ver}}"
                f" {command_str:<{col_cmd}} {caps_str:<{col_caps}}"
            )

        click.echo(separator)
        available_count = sum(1 for r in rows if r["available"])
        click.echo(f"{available_count}/{len(rows)} integrations available")
        click.echo("")
