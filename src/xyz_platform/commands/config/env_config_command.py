"""Command to manage env-file sources registered in `.platform/cli.yaml`."""

from typing import Any, Dict, Optional

import click

from xyz_platform.commands.base_command import BaseCommand
from xyz_platform.controllers.env_controller import EnvController


class EnvConfigCommand(BaseCommand):
    """Add, remove, list, or show merged environment variable sources.

    Actions:
    - ``add``    — register an env file source
    - ``remove`` — unregister an env file source
    - ``list``   — show registered sources with their load order
    - ``show``   — resolve all sources and display the merged key=value result
    """

    OPERATION = "config_env"
    INIT_REQUIRED = True

    def __init__(
        self,
        action: str,
        name: Optional[str] = None,
        path: Optional[str] = None,
        order: int = 50,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: bool = False,
        quiet: bool = False,
    ) -> None:
        super().__init__(work_path=work_path, output=output, verbose=verbose, quiet=quiet)
        self._action = action
        self._env_name = name
        self._env_path = path
        self._order = order
        self._result: Dict[str, Any] = {}

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    def execute(self) -> bool:
        try:
            if not self._initialize():
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(success=False)
                return False

            if not self._run_execution():
                if self._is_console_output():
                    click.echo("\n❌  Execution failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution processing failed")
                self._finalize(success=False)
                return False

            self._finalize(success=True)
            return True

        except Exception as e:
            error_msg = f"Failed to execute env config command: {e}"
            self.logger.exception(error_msg)
            self._errors.append(error_msg)
            self._finalize(success=False)
            return False

    def _before_execute(self) -> bool:
        return super()._before_execute()

    def _run_execution(self) -> bool:
        ctrl = EnvController(self._work_path)

        if self._action == "list":
            sources = ctrl.list_sources()
            self._result = {"sources": sources}
            self._output_data = self._result
            return True

        if self._action == "add":
            if not self._env_name or not self._env_path:
                self._errors.append("Name and path required for 'add'.")
                return False
            ok, errors = ctrl.add_source(self._env_name, self._env_path, self._order)
            self._errors.extend(errors)
            if ok:
                self._result = {"added": {"name": self._env_name, "path": self._env_path, "order": self._order}}
                self._output_data = self._result
                self._messages.append(f"Added env source '{self._env_name}'")
            return ok

        if self._action == "remove":
            if not self._env_name:
                self._errors.append("Name required for 'remove'.")
                return False
            ok, errors = ctrl.remove_source(self._env_name)
            self._errors.extend(errors)
            if ok:
                self._result = {"removed": self._env_name}
                self._output_data = self._result
                self._messages.append(f"Removed env source '{self._env_name}'")
            return ok

        if self._action == "show":
            repo_map = self._build_repo_map()
            merged, warnings = ctrl.resolve_and_load(repo_map=repo_map)
            for w in warnings:
                self._messages.append(f"⚠️  {w}")
            self._result = {"variables": merged}
            self._output_data = self._result
            return True

        self._errors.append(f"Unknown action '{self._action}'.")
        return False

    def _after_execute(self) -> bool:
        if not self._is_quiet() and self._is_console_output():
            click.echo("")

            if self._action == "list":
                sources = self._result.get("sources", [])
                if not sources:
                    click.echo("  ℹ️   No env sources registered.\n")
                else:
                    click.echo(f"  📋  Env sources ({len(sources)}):\n")
                    for s in sources:
                        click.echo(f"    [{s.get('order', 0):>3}]  {s['name']}")
                        click.echo(f"          path: {s['path']}")
                    click.echo("")

            elif self._action == "add":
                added = self._result.get("added", {})
                click.echo(f"  ✅  Added env source: {added.get('name')}")
                click.echo(f"      Path:  {added.get('path')}")
                click.echo(f"      Order: {added.get('order')}\n")

            elif self._action == "remove":
                click.echo(f"  🗑️   Removed env source: {self._result.get('removed')}\n")

            elif self._action == "show":
                variables = self._result.get("variables", {})
                if not variables:
                    click.echo("  ℹ️   No environment variables resolved.\n")
                else:
                    click.echo(f"  📋  Merged environment ({len(variables)} vars):\n")
                    for k in sorted(variables):
                        click.echo(f"    {k}={variables[k]}")
                    click.echo("")

        return super()._after_execute()

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        return super()._finalize(success=success, show_footer=show_footer)

    def _build_repo_map(self) -> Dict[str, str]:
        """Build a repo_map from the loaded solution repositories."""
        repos, _ = self._solution_controller.get_repositories()
        repo_map: Dict[str, str] = {}
        for r in repos:
            repo_map[str(r.name)] = str(self._work_path / r.path)
        return repo_map
