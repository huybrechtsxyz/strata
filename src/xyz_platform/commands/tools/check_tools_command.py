"""Command to deep-check a single integration by name."""

from __future__ import annotations

import os
from typing import Dict, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.tools_controller import ToolsController
from xyz_platform.logger import get_logger


class CheckToolsCommand(BaseCommand):
    """Deep-check a single integration: availability, env vars, auth methods."""

    OPERATION = "tools_check"
    INIT_REQUIRED = False

    def __init__(
        self,
        name: str,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._name = name

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize(show_header=False):
                self._finalize(success=False, show_footer=False)
                return False

            controller = ToolsController()
            success, detail, errors = controller.check(self._name)

            for err in errors:
                self._errors.append(err)

            if self._is_console_output():
                self._print_detail(detail, errors)

            self._output_data["integration"] = detail
            self._finalize(success=success, show_footer=False)
            return success

        except Exception as exc:
            error_msg = f"Failed to check integration '{self._name}': {exc}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False, show_footer=False)
            return False

    def _print_detail(self, detail: dict, errors: list) -> None:
        if not detail:
            for err in errors:
                click.echo(f"Error: {err}", err=True)
            return

        avail_icon = "✓  available" if detail.get("available") else "✗  not available"
        version_str = detail.get("version") or "not detected"
        command_str = detail.get("command") or "SDK only — no CLI binary"
        install_url = detail.get("install_url") or "—"
        caps = detail.get("capabilities") or []

        click.echo("")
        click.echo(f"Integration: {detail.get('name', self._name)}")
        click.echo("-" * 60)
        click.echo(f"  Status    : {avail_icon}")
        click.echo(f"  Version   : {version_str}")
        click.echo(f"  Command   : {command_str}")
        click.echo(f"  Install   : {install_url}")
        click.echo(f"  Caps      : {', '.join(caps) if caps else '—'}")

        env_vars = detail.get("env_vars") or []
        if env_vars:
            click.echo("")
            click.echo("  Environment variables:")
            col_n = 30
            col_p = 52
            col_r = 8
            col_s = 8
            click.echo(f"    {'Variable':<{col_n}} {'Purpose':<{col_p}} {'Req':<{col_r}} {'Set':<{col_s}}")
            click.echo(f"    {'-' * col_n} {'-' * col_p} {'-' * col_r} {'-' * col_s}")
            for ev in env_vars:
                is_set = "yes" if os.environ.get(ev["name"]) else "no"
                req_str = "yes" if ev.get("required") else "no"
                click.echo(
                    f"    {ev['name']:<{col_n}} {ev.get('purpose', ''):<{col_p}} {req_str:<{col_r}} {is_set:<{col_s}}"
                )

        auth_methods = detail.get("auth_methods") or []
        if auth_methods:
            click.echo("")
            click.echo("  Auth methods:")
            for am in auth_methods:
                click.echo(f"    - {am['method']}: {am['description']}")

        yaml_example = detail.get("yaml_example")
        if yaml_example:
            click.echo("")
            click.echo("  Minimal YAML:")
            for line in yaml_example.splitlines():
                click.echo(f"    {line}")

        if errors:
            click.echo("")
            for err in errors:
                click.echo(f"  ⚠  {err}")

        click.echo("")
