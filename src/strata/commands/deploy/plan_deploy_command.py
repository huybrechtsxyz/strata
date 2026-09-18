"""Show the resource change summary from the last saved Terraform plan."""

from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.models.deployment_model import DeploymentStageModel
from strata.utils.stage_selection import StageSelectionMode


class PlanDeployCommand(BaseDeployCommand):
    """Show the resource change summary from the last saved ``.tfplan`` file.

    Reads the plan file produced by ``deploy run --dry-run`` and prints a
    human-readable change summary (``terraform show -json <plan>``).
    No backend calls — entirely offline.

    ``--stage NAME``
        Limit display to a single deployment stage.
    """

    OPERATION = "deploy_plan"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
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
        self._stage = stage

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        # INSPECT: shows a previously saved plan — pure read (ADR-0083 D11).
        selection = self._resolve_stages(StageSelectionMode.INSPECT)
        if selection is None:
            return False
        stages = selection.to_run

        if self._is_console_output():
            click.echo(f"\n📋  Last saved plan for {len(stages)} stage(s)…\n")

        all_plans: Dict[str, Any] = {}
        any_failed = False

        for stage in stages:
            ok, plan_data, msgs = self._fetch_stage_plan(stage)
            all_plans[str(stage.name)] = plan_data if ok else {"error": "plan read failed"}
            self._messages.extend(msgs)
            if not ok:
                any_failed = True
            if self._is_console_output():
                self._print_stage_plan(str(stage.name), ok, plan_data, msgs)

        self._output_data = {
            "file": str(self._file_path),
            "stages": all_plans,
        }
        return not any_failed

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _fetch_stage_plan(self, stage: DeploymentStageModel) -> Tuple[bool, Dict[str, Any], List[str]]:
        deployer = self._create_deployer(stage)
        if deployer is None:
            return False, {}, [f"Stage '{stage.name}': unsupported provisioner type."]

        ok, msgs = deployer.validate_workspace()
        if not ok:
            return False, {}, msgs

        ok, msgs = deployer.validate_environment()
        if not ok:
            return False, {}, msgs

        ok, plan_data, msgs = deployer.show_plan()
        return ok, plan_data, msgs

    def _print_stage_plan(
        self,
        stage_name: str,
        ok: bool,
        plan_data: Dict[str, Any],
        msgs: List[str],
    ) -> None:
        icon = "✅" if ok else "❌"
        click.echo(f"  {icon}  Stage: {stage_name}")
        if not ok:
            for m in msgs:
                click.echo(f"       ⚠  {m}")
            click.echo()
            return

        changes = plan_data.get("resource_changes", [])
        if not changes:
            click.echo("       (no resource changes in saved plan)")
            click.echo()
            return

        counts: Dict[str, int] = {}
        for rc in changes:
            actions = rc.get("change", {}).get("actions", [])
            for action in actions:
                counts[action] = counts.get(action, 0) + 1

        summary_parts = [f"{v} {k}" for k, v in sorted(counts.items())]
        click.echo(f"       Changes: {', '.join(summary_parts)}")

        if self._is_verbose():
            for rc in changes:
                addr = rc.get("address", "?")
                actions = rc.get("change", {}).get("actions", [])
                click.echo(f"         {addr}  [{', '.join(actions)}]")

        click.echo()
