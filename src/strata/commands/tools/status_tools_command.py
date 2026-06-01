"""Command to list all known integrations and their availability."""

from __future__ import annotations

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.tools_controller import ToolsController
from strata.logger import get_logger


class StatusToolsCommand(BaseCommand):
    """List all known integrations and their availability status."""

    OPERATION = "tools_status"
    INIT_REQUIRED = False

    def __init__(
        self,
        deployment_file: Optional[str] = None,
        filter_required: bool = False,
        filter_optional: bool = False,
        filter_available: bool = False,
        filter_missing: bool = False,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._deployment_file = deployment_file
        self._filter_required = filter_required
        self._filter_optional = filter_optional
        self._filter_available = filter_available
        self._filter_missing = filter_missing
        self._has_missing_required = False

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def has_validation_errors(self) -> bool:
        return self._has_missing_required

    def execute(self) -> bool:
        try:
            if not self._initialize(show_header=False):
                self._finalize(success=False, show_footer=False)
                return False

            controller = ToolsController()
            success, rows, errors = controller.status(
                deployment_file=self._deployment_file,
                work_path=str(self._work_path) if self._work_path else None,
            )

            for err in errors:
                self._errors.append(err)

            # Warn when requirement filters are used without --file context
            if (self._filter_required or self._filter_optional) and not self._deployment_file:
                click.echo(
                    "Warning: --required/--optional require --file to have any effect. "
                    "No deployment context — all integrations have no requirement level.",
                    err=True,
                )

            # Apply filters
            if self._deployment_file and not self._filter_required and not self._filter_optional:
                # --file given with no requirement filter: show only configured integrations
                rows = [r for r in rows if r.get("requirement") is not None]
            elif self._filter_required or self._filter_optional:
                # Requirement filter(s): show union of selected levels (combinable)
                allowed = set()
                if self._filter_required:
                    allowed.add("required")
                if self._filter_optional:
                    allowed.add("optional")
                rows = [r for r in rows if r.get("requirement") in allowed]
            if self._filter_available:
                rows = [r for r in rows if r["available"]]
            if self._filter_missing:
                rows = [r for r in rows if not r["available"]]

            if self._is_console_output():
                self._print_table(rows, deployment_mode=bool(self._deployment_file))

            self._output_data["integrations"] = rows

            # Treat as validation failure (exit 3) when filtering for missing
            # required integrations and any were found
            if self._filter_missing and rows and any(r.get("requirement") == "required" for r in rows):
                missing_names = [r["name"] for r in rows if r.get("requirement") == "required"]
                self._errors.append(f"Required integrations not available: {', '.join(missing_names)}")
                self._has_missing_required = True
                success = False

            self._finalize(success=success, show_footer=False)
            return success

        except Exception as exc:
            error_msg = f"Failed to list tool integrations: {exc}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False, show_footer=False)
            return False

    def _print_table(self, rows: list, deployment_mode: bool = False) -> None:
        col_name = 22
        col_req = 12
        col_avail = 12
        col_ver = 14
        col_cmd = 12
        col_caps = 30

        if deployment_mode:
            header = (
                f"{'Name':<{col_name}} {'Requirement':<{col_req}} {'Available':<{col_avail}}"
                f" {'Version':<{col_ver}} {'Command':<{col_cmd}} {'Capabilities':<{col_caps}}"
            )
        else:
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
            avail_icon = "\u2713" if row["available"] else "\u2717"
            version_str = row["version"] or "not found"
            command_str = row["command"] or "-"
            caps_str = ", ".join(row["capabilities"]) if row["capabilities"] else "-"
            req_str = row.get("requirement") or "\u2014"

            if deployment_mode:
                click.echo(
                    f"{row['name']:<{col_name}} {req_str:<{col_req}} {avail_icon:<{col_avail}}"
                    f" {version_str:<{col_ver}} {command_str:<{col_cmd}} {caps_str:<{col_caps}}"
                )
            else:
                click.echo(
                    f"{row['name']:<{col_name}} {avail_icon:<{col_avail}} {version_str:<{col_ver}}"
                    f" {command_str:<{col_cmd}} {caps_str:<{col_caps}}"
                )

        click.echo(separator)
        available_count = sum(1 for r in rows if r["available"])
        click.echo(f"{available_count}/{len(rows)} integrations available")
        click.echo("")
