"""Command to display the drift-check run history for a deployment."""

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.utils.drift_history import DriftHistoryStore


class DriftHistoryDeployCommand(BaseDeployCommand):
    """Show the drift-check run history and acknowledged addresses for a deployment.

    Reads ``.strata/drift/{deployment}.drift.json`` and renders:

    - A table of recent drift-check runs with their timestamps, entry counts,
      and max severity seen.
    - A list of currently acknowledged (suppressed) resource addresses.

    Example::

        strata deploy drift history -f deploy/prod.yaml
        strata deploy drift history -f deploy/prod.yaml --last 5
        strata deploy drift history -f deploy/prod.yaml --output json
    """

    OPERATION = "deploy_drift_history"

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
    # Core
    # -------------------------------------------------------------------------

    def _run(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deployment_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
        history = DriftHistoryStore(self._work_path, deployment_name)
        history.load()

        runs = history.list_runs(last=self._last)
        acknowledged = history.list_acknowledged()
        baseline_at = history.get_baseline_at()

        if self._is_console_output():
            self._print_history(deployment_name, runs, acknowledged, baseline_at)

        self._output_data = {
            "deployment": deployment_name,
            "baseline_at": baseline_at,
            "runs": runs,
            "acknowledged": acknowledged,
        }

        return True

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _print_history(
        self,
        deployment_name: str,
        runs: List[Dict[str, Any]],
        acknowledged: List[Dict[str, Any]],
        baseline_at: Optional[str],
    ) -> None:
        click.echo(f"\nDrift History — {deployment_name}")
        click.echo("━" * 60)

        if baseline_at:
            click.echo(f"  Baseline set: {baseline_at}")
            click.echo()

        # ---- Run history ----
        click.echo(f"  Recent Runs  (last {len(runs)} shown)\n")
        if not runs:
            click.echo("  No drift-check runs recorded yet.\n")
        else:
            click.echo(f"  {'WHEN':<28}  {'DRIFTED RESOURCES':<6}  ADDRESSES")
            click.echo(f"  {'-' * 28}  {'-' * 17}  {'-' * 40}")
            for run in reversed(runs):  # most recent first
                checked_at = run.get("checked_at", "—")
                addresses: List[str] = run.get("addresses", [])
                count = len(addresses)
                addr_preview = ", ".join(addresses[:3])
                if count > 3:
                    addr_preview += f" (+{count - 3} more)"
                count_str = f"{count} resource(s)" if count else "clean"
                click.echo(f"  {checked_at:<28}  {count_str:<17}  {addr_preview}")
            click.echo()

        # ---- Acknowledged addresses ----
        if acknowledged:
            click.echo(f"  Acknowledged Addresses ({len(acknowledged)} suppressed)\n")
            click.echo(f"  {'ADDRESS':<52}  {'REASON'}")
            click.echo(f"  {'-' * 52}  {'-' * 30}")
            for entry in acknowledged:
                addr = entry["address"]
                if len(addr) > 52:
                    addr = addr[:49] + "..."
                reason = entry.get("acknowledged_reason", "")
                click.echo(f"  {addr:<52}  {reason}")
            click.echo()
        else:
            click.echo("  No acknowledged addresses.\n")
