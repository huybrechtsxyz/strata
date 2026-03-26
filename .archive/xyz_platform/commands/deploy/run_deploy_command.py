#!/usr/bin/env python3
"""
===============================================================================
Script Name   : run_deploy_command.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Command to execute deploy operations.
===============================================================================
"""

from typing import List, Optional

import click

from xyz_platform.commands.deploy.base_deploy_command import BaseDeployCommand
from xyz_platform.controllers.workspace_controller import WorkspaceController
from xyz_platform.deployers.base_deployer import (
    STEP_SETUP,
    STEP_CHECK,
    STEP_PLAN,
    STEP_APPLY,
    STEP_DESTROY,
)
from xyz_platform.deployers.terraform_deployer import TerraformDeployer
from xyz_platform.models.common_models import ProvisionerType
from xyz_platform.models.deployment_model import DeploymentStageModel


class RunDeployCommand(BaseDeployCommand):
    """Run the deploy pipeline for a deployment definition."""

    def __init__(
        self,
        file: str = None,
        work_path: str = None,
        stage: str = None,
        force: bool = False,
        dry_run: bool = False,
        destroy: bool = False,
        no_hooks: bool = False,
        output: str = None,
        verbose: bool = None,
        quiet: bool = None,
    ):
        super().__init__(
            file=file,
            work_path=work_path,
            no_hooks=no_hooks,
            output=output,
            verbose=verbose,
            quiet=quiet,
        )
        self._stage = stage
        self._force = force
        self._dry_run = dry_run
        self._destroy = destroy
        self._resolved_values: Optional[ResolvedValues] = None

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize(operation="deploy_run"):
                if self._is_console_output():
                    click.echo("\n❌  Initialization failed")
                self._finalize(operation="deploy_run", success=False)
                return False

            if not self._before_execute():
                if self._is_console_output():
                    click.echo("\n❌  Pre-execution validation failed")
                self._finalize(operation="deploy_run", success=False)
                return False

            if not self._load_related_services():
                if self._is_console_output():
                    click.echo("\n❌  Failed to load deployment related services")
                self._finalize(operation="deploy_run", success=False)
                return False

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._finalize(operation="deploy_run", success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo(
                    "\n[DRY-RUN] Validating and planning deploy — no provisioning will run"
                )

            if self._destroy and self._is_console_output():
                click.echo("\n⚠️  --destroy flag: running destroy step per stage.")

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Deploy provisioning failed")
                self._finalize(operation="deploy_run", success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(operation="deploy_run", success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "object_path": str(self._object_path),
                    "stage": self._stage,
                    "force": self._force,
                    "dry_run": self._dry_run,
                    "destroy": self._destroy,
                }
            )

            self._finalize(operation="deploy_run", success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_run: {exc}")
            self.logger.exception("deploy_run failed")
            self._finalize(operation="deploy_run", success=False)
            return False

    # -------------------------------------------------------------------------
    # Internal pipeline steps
    # -------------------------------------------------------------------------

    def _load_related_services(self) -> bool:
        """Load deployment related services from session-managed work path."""
        workspace_controller = WorkspaceController()
        _, load_success = workspace_controller.load_related_services(
            deployment_service=self._deployment_service,
            objects_path=self._work_path,
            stage_name=self._stage,
        )
        if not load_success:
            self._errors.extend(self._deployment_service.get_validation_errors())
            return False

        data_success, data_errors = workspace_controller.load_related_service_data(
            deployment_service=self._deployment_service,
            stage_name=self._stage,
        )
        if not data_success:
            self._errors.extend(data_errors)
            return False

        return True

    def _resolve_values(self) -> bool:
        """Resolve variables, secrets, and feature flags from the environment.

        Populates ``self._resolved_values`` which is later passed to the
        deployer so it can inject TF_VAR_* env vars around each terraform step.

        Non-strict mode: resolution warnings are logged but do not abort the
        deploy (missing optional values may be handled by Terraform defaults).
        """
        controller = ValueController()
        ok, resolved, errors = controller.resolve_values(
            self._deployment_service, strict=False
        )

        self._resolved_values = resolved

        if errors:
            for err in errors:
                self.logger.warning("Value resolution warning: %s", err)
            if self._is_console_output():
                click.echo(
                    f"  ⚠️  {len(errors)} value(s) could not be resolved "
                    "(see logs for details)."
                )

        if self._is_console_output() and not resolved.is_empty():
            click.echo(
                f"  ✓  Resolved {len(resolved.variables)} variable(s), "
                f"{len(resolved.secrets)} secret(s), "
                f"{len(resolved.features)} feature(s)."
            )

        # ok is always True in non-strict mode — keep going even with warnings
        return True

    def _execute_provisioning(self) -> bool:
        """Iterate deployment stages and invoke the appropriate provisioner per stage."""
        spec = self._deployment_service.model.spec
        all_stages: List[DeploymentStageModel] = spec.stages or []

        if not all_stages:
            if self._is_console_output():
                click.echo("⚠️  No deployment stages defined — nothing to deploy.")
            return True

        # Filter to a single stage when --stage is supplied
        stages_to_run = (
            [s for s in all_stages if s.name == self._stage]
            if self._stage
            else all_stages
        )

        if self._stage and not stages_to_run:
            self._errors.append(
                f"Stage '{self._stage}' not found in deployment definition. "
                f"Available: {[s.name for s in all_stages]}"
            )
            return False

        if self._is_console_output():
            click.echo(f"\n🚀  Deploying {len(stages_to_run)} stage(s)…")

        # Check approval gates before any provisioning
        if not self._dry_run:
            if not self._check_approvals(stages_to_run):
                return False

        for stage in stages_to_run:
            if self._is_console_output():
                label = f"[{stage.type}]" + (
                    f" via {stage.provisioner}" if stage.provisioner else ""
                )
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"\n  ▶  {prefix}Stage: {stage.name}  {label}")

            ok = self._execute_stage_provisioning(stage)
            if not ok:
                if stage.on_failure == "continue":
                    if self._is_console_output():
                        click.echo(
                            f"  ⚠️  Stage '{stage.name}' failed — on_failure=continue, proceeding."
                        )
                    continue
                # Default: stop
                self._errors.append(f"Stage '{stage.name}' failed (on_failure=stop).")
                return False

        if self._is_console_output() and not self._dry_run:
            click.echo("\n✅  All stages completed.")

        return True

    def _execute_stage_provisioning(self, stage: DeploymentStageModel) -> bool:
        """Instantiate the deployer for *stage*, validate, then run the step sequence.

        Step sequences:
          dry-run  : setup → check → plan
          destroy  : setup → destroy  (requires --force for -auto-approve)
          normal   : setup → check → plan → apply
        """
        deployer = self._create_deployer(stage)
        if deployer is None:
            self._errors.append(
                f"Stage '{stage.name}': no deployer available for "
                f"type='{stage.type}' / provisioner='{stage.provisioner}'. "
                "Currently supported: infrastructure (terraform)."
            )
            return False

        # --- pre-flight validation ---
        for label, validate_fn in (
            ("workspace", deployer.validate_workspace),
            ("environment", deployer.validate_environment),
        ):
            ok, msgs = validate_fn()
            self._messages.extend(msgs)
            if self._is_console_output():
                for msg in msgs:
                    click.echo(f"    {msg}")
            if not ok:
                self._errors.extend(msgs)
                return False

        # --- determine step sequence ---
        if self._destroy:
            if not self._force:
                self._errors.append(
                    f"Stage '{stage.name}': --force is required to run terraform destroy "
                    "(non-interactive execution needs -auto-approve)."
                )
                return False
            steps_to_run = [STEP_SETUP, STEP_DESTROY]
        elif self._dry_run:
            steps_to_run = [STEP_SETUP, STEP_CHECK, STEP_PLAN]
        else:
            steps_to_run = [STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY]

        supported = deployer.get_supported_steps()

        # --- execute each step ---
        for step_name in steps_to_run:
            if step_name not in supported:
                self._errors.append(
                    f"Stage '{stage.name}': step '{step_name}' is not supported "
                    f"by deployer '{deployer.get_deployer_name()}'."
                )
                return False

            if self._is_console_output():
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"    {prefix}{step_name}")

            step_fn = getattr(deployer, step_name)
            ok, msgs = step_fn()
            self._messages.extend(msgs)
            if self._is_console_output():
                for msg in msgs:
                    click.echo(f"      {msg}")
            if not ok:
                self._errors.extend(msgs)
                return False

        return True

    def _create_deployer(self, stage: DeploymentStageModel):
        """Instantiate and return the deployer for *stage*, or None.

        Type resolution (same logic as old _select_deployer, but now the
        deployer receives full context via its constructor):
          stage.provisioner resolves to a terraform IaC entry  → TerraformDeployer
          stage.type == 'infrastructure' or 'terraform'        → TerraformDeployer

        TODO: extend with additional deployer types as they are implemented.
        """
        is_terraform = False

        if stage.provisioner:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service:
                spec = workspace_service.model.spec
                iac = next(
                    (
                        p
                        for p in (spec.provisioners or [])
                        if p.name == stage.provisioner
                    ),
                    None,
                )
                if iac and iac.provisioner == ProvisionerType.TERRAFORM:
                    is_terraform = True

        if not is_terraform and stage.type in ("infrastructure", "terraform"):
            is_terraform = True

        if is_terraform:
            return TerraformDeployer(
                stage=stage,
                deployment_service=self._deployment_service,
                configuration_service=self._configuration_service,
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=self._force,
                resolved_values=self._resolved_values,
            )

        return None

    def _check_approvals(self, stages_to_run: List[DeploymentStageModel]) -> bool:
        """Check deployment approval gates before executing stages.

        TODO: When an approval/gate system is implemented, integrate it here.
              For now, non-interactive approval checks can be performed based
              on `self._deployment_service.model.spec.approvals`.
        """
        spec = self._deployment_service.model.spec
        approvals = getattr(spec, "approvals", None) or []

        if not approvals:
            return True

        stage_names = {s.name for s in stages_to_run}
        pending = [a for a in approvals if getattr(a, "stage", None) in stage_names]

        if pending:
            self.logger.debug(
                "Approval gates present",
                extra={"count": len(pending)},
            )
            # TODO: Implement gate evaluation (e.g., check approval status via
            #       an external approval service or prompt interactively when
            #       `--force` is not set).

        return True
