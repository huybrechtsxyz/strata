"""Command to show infrastructure cost estimates for a deployment."""

from typing import Any, Dict, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.cost_controller import CostController


class ShowCostCommand(BaseDeployCommand):
    """Show cost estimate for a deployment's terraform provisioners.

    Loads the deployment, resolves terraform build artifacts, and invokes
    the Infracost integration (ICostEstimator capability) to produce a
    per-resource monthly cost breakdown.

    Requires:
    - ``strata build run`` to have been executed (terraform artifacts exist)
    - ``terraform init`` to have been run in the build directory
    - ``infracost`` binary installed and in PATH

    Exit codes:
      0  — cost estimate produced successfully
      1  — system error (infracost missing, credentials invalid, etc.)
      3  — validation error (no deployment file, no terraform provisioners)
    """

    OPERATION = "cost_show"
    SHOW_CHROME = True

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        currency: Optional[str] = None,
        provisioner: Optional[str] = None,
        force_refresh: bool = False,
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
        self._currency = currency
        self._provisioner_filter = provisioner
        self._force_refresh = force_refresh

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        controller = CostController(work_path=self._work_path)
        success, result = controller.show(
            deployment_service=self._deployment_service,
            build_path=self._build_path,
            solution_controller=self._solution_controller,
            currency=self._currency,
            provisioner_filter=self._provisioner_filter,
            force_refresh=self._force_refresh,
        )

        # Propagate controller messages/errors
        self._messages.extend(controller.get_messages())
        self._errors.extend(controller.get_errors())

        # Store for JSON output
        self._output_data = result

        # Console rendering
        if success and self._is_console_output():
            self._render_cost_table(result)
        elif not success and self._is_console_output():
            error_msg = result.get("error", "Cost estimation failed")
            click.echo(f"\n❌  {error_msg}\n")

        return success

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _render_cost_table(self, result: Dict[str, Any]) -> None:
        """Render cost breakdown as a console table."""
        provisioners = result.get("provisioners", {})

        for prov_name, prov_data in provisioners.items():
            click.echo("")
            click.echo("─" * 60)
            click.echo(f"💰 Cost Estimate — provisioner: {prov_name}")
            click.echo("─" * 60)

            breakdown = prov_data.get("breakdown", prov_data.get("projects", [{}]))

            # Handle Infracost output format (projects[] or breakdown{})
            resources = []
            total_monthly = "0.00"

            if isinstance(breakdown, dict):
                resources = breakdown.get("resources", [])
                total_monthly = breakdown.get("totalMonthlyCost", "0.00")
            elif isinstance(breakdown, list):
                # Infracost v0.10+ uses projects[].breakdown.resources
                for project in breakdown if isinstance(breakdown, list) else [breakdown]:
                    proj_breakdown = project.get("breakdown", {})
                    resources.extend(proj_breakdown.get("resources", []))
                    total_monthly = proj_breakdown.get("totalMonthlyCost", total_monthly)

            # Also check top-level totalMonthlyCost
            if "totalMonthlyCost" in prov_data:
                total_monthly = prov_data["totalMonthlyCost"]

            if resources:
                # Header
                click.echo(f"\n{'Resource':<45} {'Monthly':>12}")
                click.echo(f"{'─' * 45} {'─' * 12}")

                for resource in resources:
                    name = resource.get("name", "unknown")
                    monthly = resource.get("monthlyCost", "0.00")
                    if monthly and monthly != "0":
                        # Truncate long names
                        display_name = name if len(name) <= 44 else name[:41] + "..."
                        click.echo(f"{display_name:<45} {monthly:>12}")

                click.echo(f"{'─' * 45} {'─' * 12}")
                click.echo(f"{'Total':<45} {total_monthly:>12}")
            else:
                click.echo("\n  No priced resources found.")

            click.echo("")
