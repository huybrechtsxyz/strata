"""Command to show cost history for a deployment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand


class HistoryCostCommand(BaseDeployCommand):
    """Show historical cost snapshots for a deployment.

    Reads from ``.strata/cost/{deployment}.cost-history.json`` — the history
    file is populated automatically each time ``strata cost show`` runs.

    Exit codes:
      0  — history displayed (or empty history, which is not an error)
      1  — system error (deployment file not found, etc.)
    """

    OPERATION = "cost_history"
    SHOW_CHROME = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        last: int = 10,
        output: Optional[str] = None,
        verbose: Optional[bool] = None,
        quiet: Optional[bool] = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._last = last

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        from strata.utils.cost_history import CostHistoryStore

        deployment_name = str(self._deployment_service.get_name())
        store = CostHistoryStore(self._work_path, deployment_name)
        store.load()
        snapshots = store.list_snapshots(last=self._last)

        self._output_data = {
            "deployment": deployment_name,
            "snapshots": snapshots,
        }

        if self._is_console_output():
            self._render_history(deployment_name, snapshots)

        return True

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _render_history(self, deployment_name: str, snapshots: List[Dict[str, Any]]) -> None:
        if not snapshots:
            click.echo(f"\n  No cost history found for '{deployment_name}'.")
            click.echo("  Run: strata cost show -f <deployment.yaml>\n")
            return

        click.echo("")
        click.echo("─" * 70)
        click.echo(f"💰 Cost History — {deployment_name} (last {len(snapshots)} snapshots)")
        click.echo("─" * 70)
        click.echo(f"\n{'Date (UTC)':<22} {'Version':<10} {'Monthly':>14} {'Delta':>12}")
        click.echo(f"{'─' * 22} {'─' * 10} {'─' * 14} {'─' * 12}")

        for snap in snapshots:
            recorded = snap.get("recorded_at", "—")[:19].replace("T", " ")
            version = snap.get("version", "—")[:9]
            total = snap.get("total_monthly")
            currency = snap.get("currency", "")
            delta = snap.get("delta_from_previous")

            total_str = f"{total:>12.2f} {currency}" if total is not None else "—"
            if delta is None:
                delta_str = "—"
            elif delta >= 0:
                delta_str = f"+{delta:.2f}"
            else:
                delta_str = f"{delta:.2f}"

            click.echo(f"{recorded:<22} {version:<10} {total_str:>14} {delta_str:>12}")

        click.echo("")
