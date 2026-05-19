"""Command to display download and setup guidance for a tool integration."""

from __future__ import annotations

import os
from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.controllers.tools_controller import ToolsController
from strata.logger import get_logger


class InstallToolsCommand(BaseCommand):
    """Show download URL, required env vars, and auth methods for an integration.

    Does not install anything — purely informational.
    """

    OPERATION = "tools_install"
    INIT_REQUIRED = False

    def __init__(
        self,
        name: str,
        env_file: Optional[str] = None,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self.logger = get_logger(self.__class__.__module__)
        self._name = name
        self._env_file = env_file

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize(show_header=False):
                self._finalize(success=False, show_footer=False)
                return False

            controller = ToolsController()
            success, info, errors = controller.install_info(self._name)

            for err in errors:
                self._errors.append(err)

            if not success:
                self._finalize(success=False, show_footer=False)
                return False

            if self._env_file:
                self._write_env_file(info)

            if self._is_console_output():
                self._print_guide(info)

            self._output_data["integration"] = info
            self._finalize(success=True, show_footer=False)
            return True

        except Exception as exc:
            error_msg = f"Failed to get install info for '{self._name}': {exc}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False, show_footer=False)
            return False

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_guide(self, info: dict) -> None:
        name = info.get("name", self._name)
        install_url = info.get("install_url") or "—"
        command = info.get("command") or "SDK only — no CLI binary"
        env_vars: list = info.get("env_vars") or []
        auth_methods: list = info.get("auth_methods") or []
        yaml_example: str = info.get("yaml_example") or ""

        click.echo("")
        click.echo(f"  Integration : {name}")
        click.echo(f"  CLI command : {command}")
        click.echo(f"  Download    : {install_url}")
        click.echo("")

        if env_vars:
            click.echo("  Environment variables:")
            col = max(len(ev["name"]) for ev in env_vars) + 2
            for ev in env_vars:
                req = "required" if ev.get("required") else "optional"
                is_set = "✓ set" if os.environ.get(ev["name"]) else "✗ not set"
                click.echo(f"    {ev['name']:<{col}}  {req:<8}  {is_set:<10}  {ev.get('purpose', '')}")
            click.echo("")

        if auth_methods:
            click.echo("  Authentication methods:")
            for am in auth_methods:
                click.echo(f"    • {am.get('method', '')}")
                click.echo(f"      {am.get('description', '')}")
            click.echo("")

        if yaml_example:
            click.echo("  YAML example:")
            for line in yaml_example.splitlines():
                click.echo(f"    {line}")
            click.echo("")

        if self._env_file:
            click.echo(f"  Env-file written to: {self._env_file}")
            click.echo("")

    # ------------------------------------------------------------------
    # Env-file writer
    # ------------------------------------------------------------------

    def _write_env_file(self, info: dict) -> None:
        if not self._env_file:
            return
        name = info.get("name", self._name)
        install_url = info.get("install_url") or ""
        env_vars: list = info.get("env_vars") or []

        lines = [
            f"# {name} environment variables",
            f"# Download: {install_url}",
            "# Source this file before running xyz commands that use this integration.",
            "# Keep this file on your machine — do NOT commit it to source control.",
            "",
        ]
        for ev in env_vars:
            req = "required" if ev.get("required") else "optional"
            lines.append(f"# {ev['name']} ({req}) — {ev.get('purpose', '')}")
            existing = os.environ.get(ev["name"])
            value = existing if existing else ""
            lines.append(f"# {ev['name']}={value}")
            lines.append("")

        content = "\n".join(lines)
        with open(self._env_file, "w", encoding="utf-8") as fh:
            fh.write(content)
