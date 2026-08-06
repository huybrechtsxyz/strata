"""Command to show cost diff for a terraform plan."""

from typing import Any, Dict, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.cost_controller import CostController


class DiffCostCommand(BaseDeployCommand):
    """Show cost diff between current state and a terraform plan.

    Reads an existing terraform plan JSON file and runs ``infracost diff``
    to show how much the planned changes will add or remove from the
    monthly cost.

    Typical workflow::

        strata build run -f deploy/prd.yaml
        terraform -chdir=build/.../terraform plan -out=plan.tfplan
        terraform -chdir=build/.../terraform show -json plan.tfplan > plan.json
        strata cost diff -f deploy/prd.yaml --plan-file plan.json

    Requires a cost estimator (e.g. Infracost) declared in ``spec.integrations``
    (``type: infracost``, ``capabilities: [cost]``) and its binary installed.

    Exit codes:
      0  — diff produced successfully
      1  — system error (infracost missing, credentials invalid, etc.)
      3  — validation error (no deployment file, plan file not found, etc.)
    """

    OPERATION = "cost_diff"
    SHOW_CHROME = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        plan_file: Optional[str] = None,
        currency: Optional[str] = None,
        provisioner: Optional[str] = None,
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
        self._plan_file = plan_file
        self._currency = currency
        self._provisioner_filter = provisioner

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        if not self._plan_file:
            self._errors.append("No plan file specified. Use --plan-file.")
            return False

        controller = CostController(work_path=self._work_path)
        success, result = controller.diff(
            deployment_service=self._deployment_service,
            build_path=self._build_path,
            plan_file=self._plan_file,
            solution_controller=self._solution_controller,
            currency=self._currency,
            provisioner_filter=self._provisioner_filter,
        )

        self._messages.extend(controller.get_messages())
        self._errors.extend(controller.get_errors())
        self._output_data = result

        if success and self._is_console_output():
            self._render_diff_table(result)
        elif not success and self._is_console_output():
            click.echo(f"\n❌  {result.get('error', 'Cost diff failed')}\n")

        return success

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _render_diff_table(self, result: Dict[str, Any]) -> None:
        """Render cost diff as a console table showing before/after."""
        # Infracost diff output structure
        diff = result.get("diff", {})
        past_total = result.get("pastTotalMonthlyCost", diff.get("pastTotalMonthlyCost", "0.00"))
        total = result.get("totalMonthlyCost", diff.get("totalMonthlyCost", "0.00"))

        try:
            past_f = float(past_total or 0)
            total_f = float(total or 0)
            delta_f = total_f - past_f
            delta_sign = "+" if delta_f >= 0 else ""
            delta_str = f"{delta_sign}{delta_f:.2f}"
        except (ValueError, TypeError):
            delta_str = "n/a"

        click.echo("")
        click.echo("─" * 60)
        click.echo("💰 Cost Diff")
        click.echo("─" * 60)

        resources = diff.get("resources", [])
        if resources:
            changed = [r for r in resources if r.get("monthlyCost") not in (None, "0", "0.00")]
            if changed:
                click.echo(f"\n{'Resource':<45} {'Monthly':>12}")
                click.echo(f"{'─' * 45} {'─' * 12}")
                for resource in changed:
                    name = resource.get("name", "unknown")
                    monthly = resource.get("monthlyCost", "0.00")
                    display_name = name if len(name) <= 44 else name[:41] + "..."
                    click.echo(f"{display_name:<45} {monthly:>12}")
                click.echo(f"{'─' * 45} {'─' * 12}")

        click.echo(f"\n  Before: {past_total}/month")
        click.echo(f"  After:  {total}/month")
        click.echo(f"  Delta:  {delta_str}/month")
        click.echo("")
