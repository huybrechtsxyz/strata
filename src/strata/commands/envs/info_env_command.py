"""Command to display current workspace context (solution, profile, version, work path)."""

from typing import Dict, Optional

import click

from strata.commands.base_command import BaseCommand
from strata.utils.version import get_version


class InfoEnvCommand(BaseCommand):
    """Display current workspace context — instant, no external calls.

    Answers: "Where am I and what's active?"

    Shows:
      - Solution name and ID
      - Active profile name and config file path
      - strata version
      - Work path

    This command is intentionally minimal.  For health checks and integration
    status use ``strata env doctor``.  For deployment state use
    ``strata env status``.
    """

    OPERATION = "env_info"

    def __init__(
        self,
        work_path: Optional[str] = None,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ) -> None:
        super().__init__(
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )

    def get_required_integrations(self) -> Dict[str, str]:
        return {}

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _execute(self) -> bool:
        solution = self._solution_controller.solution
        if solution is None:
            self._errors.append("No solution loaded.")
            return False

        # ── Solution ──────────────────────────────────────────────────
        solution_name = str(solution.meta.name)
        solution_id = self._solution_controller.get_solution_id()

        # ── Active profile ────────────────────────────────────────────
        active_profile, _ = self._solution_controller.get_active_profile()
        profile_name: Optional[str] = str(active_profile.name) if active_profile else None

        # First configfile path for the active profile (the primary config file)
        config_path: Optional[str] = None
        if active_profile and active_profile.configfile_paths:
            config_path = active_profile.configfile_paths[0].path

        # ── Version + work path ───────────────────────────────────────
        version = get_version()
        work_path = str(self._work_path)

        # ── Populate output data (JSON envelope) ─────────────────────
        self._output_data = {
            "solution": {
                "name": solution_name,
                "id": solution_id,
            },
            "profile": {
                "name": profile_name,
                "active": active_profile is not None,
                "config": config_path,
            },
            "version": version,
            "work_path": work_path,
        }

        # ── Console output ────────────────────────────────────────────
        if self._is_console_output():
            self._print_console(
                solution_name=solution_name,
                solution_id=solution_id,
                profile_name=profile_name,
                config_path=config_path,
                version=version,
                work_path=work_path,
            )

        return True

    # ------------------------------------------------------------------
    # Console rendering
    # ------------------------------------------------------------------

    def _print_console(
        self,
        solution_name: str,
        solution_id: str,
        profile_name: Optional[str],
        config_path: Optional[str],
        version: str,
        work_path: str,
    ) -> None:
        label_w = 12  # fixed label column width

        profile_display = f"{profile_name} ✔ (active)" if profile_name else "(none)"
        config_display = config_path or "(none)"

        click.echo("")
        click.echo(f"  {'Solution:':<{label_w}}{solution_name} ({solution_id})")
        click.echo(f"  {'Profile:':<{label_w}}{profile_display}")
        click.echo(f"  {'Config:':<{label_w}}{config_display}")
        click.echo(f"  {'Version:':<{label_w}}strata v{version}")
        click.echo(f"  {'Work path:':<{label_w}}{work_path}")
        click.echo("")
