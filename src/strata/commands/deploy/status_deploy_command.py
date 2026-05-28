"""Command to report live Terraform outputs and saved plan details for a deployment."""

from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel


class StatusDeployCommand(BaseDeployCommand):
    """Report deployment status for a deployment definition.

    Two modes (select with flags; default = live outputs):

    Default (no flags)
        For each stage: runs ``terraform output -json`` → prints live
        infrastructure outputs from the remote backend.

    ``--plan``
        Reads the last saved ``.tfplan`` file produced by
        ``deploy run --dry-run`` and shows a human-readable change summary
        (``terraform show -json <plan>``).  No network calls to the backend.

    For execution history use ``strata deploy history``.
    """

    OPERATION = "deploy_status"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        show_plan: bool = False,
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
        self._show_plan = show_plan

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

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

            ok = self._run_plan_status() if self._show_plan else self._run_live_outputs()

            if not self._after_execute():
                self._finalize(success=False)
                return False

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_status: {exc}")
            self.logger.exception("deploy_status failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Mode: live outputs
    # -------------------------------------------------------------------------

    def _run_live_outputs(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        stages = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages
        if self._stage and not stages:
            self._errors.append(f"Stage '{self._stage}' not found. Available: {[s.name for s in all_stages]}")
            return False

        if self._is_console_output():
            click.echo(f"\n📡  Live outputs for {len(stages)} stage(s)…\n")

        all_outputs: Dict[str, Any] = {}
        any_failed = False

        for stage in stages:
            ok, outputs, msgs = self._fetch_stage_outputs(stage)
            self._messages.extend(msgs)
            all_outputs[str(stage.name)] = outputs if ok else {"error": "output fetch failed"}
            if not ok:
                any_failed = True
            if self._is_console_output():
                self._print_stage_outputs(str(stage.name), ok, outputs, msgs)

        self._output_data = {
            "file": str(self._file_path),
            "mode": "live_outputs",
            "stages": all_outputs,
        }
        return not any_failed

    def _fetch_stage_outputs(self, stage: DeploymentStageModel) -> Tuple[bool, Dict[str, Any], List[str]]:
        deployer = self._create_deployer(stage)
        if deployer is None:
            return False, {}, [f"Stage '{stage.name}': unsupported provisioner type '{stage.type}'."]

        for validate_fn in (deployer.validate_workspace, deployer.validate_environment):
            ok, msgs = validate_fn()
            if not ok:
                return False, {}, msgs

        # setup (init) so output can reach the backend
        ok, msgs = deployer.setup()
        if not ok:
            return False, {}, msgs

        ok, outputs, msgs = deployer.output()
        return ok, outputs, msgs

    def _print_stage_outputs(
        self,
        stage_name: str,
        ok: bool,
        outputs: Dict[str, Any],
        msgs: List[str],
    ) -> None:
        icon = "✅" if ok else "❌"
        click.echo(f"  {icon}  Stage: {stage_name}")
        if not ok:
            for m in msgs:
                click.echo(f"       ⚠  {m}")
        elif outputs:
            for k, v in outputs.items():
                click.echo(f"       • {k}: {v}")
        else:
            click.echo("       (no outputs defined)")
        click.echo()

    # -------------------------------------------------------------------------
    # Mode: plan details
    # -------------------------------------------------------------------------

    def _run_plan_status(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        stages = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages
        if self._stage and not stages:
            self._errors.append(f"Stage '{self._stage}' not found. Available: {[s.name for s in all_stages]}")
            return False

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
            "mode": "plan",
            "stages": all_plans,
        }
        return not any_failed

    def _fetch_stage_plan(self, stage: DeploymentStageModel) -> Tuple[bool, Dict[str, Any], List[str]]:
        deployer = self._create_deployer(stage)
        if deployer is None:
            return False, {}, [f"Stage '{stage.name}': unsupported provisioner type '{stage.type}'."]

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

        # Summarise resource changes from the plan
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

    # -------------------------------------------------------------------------
    # Deployer factory (identical to RunDeployCommand)
    # -------------------------------------------------------------------------

    def _create_deployer(self, stage: DeploymentStageModel):
        resolved_type: Optional[str] = None

        if stage.provisioner and self._deployment_service is not None:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service:
                spec = workspace_service.model.spec  # type: ignore[union-attr]
                iac = next(
                    (p for p in (spec.provisioners or []) if p.name == stage.provisioner),
                    None,
                )
                if iac and iac.provisioner == ProvisionerType.TERRAFORM:
                    resolved_type = "terraform"
                elif iac and iac.provisioner == ProvisionerType.ANSIBLE:
                    resolved_type = "ansible"

        if resolved_type is None:
            if stage.type in ("infrastructure", "terraform"):
                resolved_type = "terraform"
            elif stage.type in ("configure", "initialize", "ansible"):
                resolved_type = "ansible"

        if resolved_type == "terraform":
            return TerraformDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        if resolved_type == "ansible":
            from strata.deployers.ansible_deployer import AnsibleDeployer

            return AnsibleDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        return None
