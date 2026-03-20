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

            if self._dry_run and self._is_console_output():
                click.echo(
                    "\n[DRY-RUN] Validating and planning deploy — no provisioning will run"
                )

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
                click.echo(f"\n  ▶  Stage: {stage.name}  {label}")

            if self._dry_run:
                click.echo(
                    f"  [DRY-RUN] Would provision stage '{stage.name}' "
                    f"(type={stage.type}, scope={stage.scope})"
                )
                continue

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
        """Dispatch a single stage to its provisioner.

        TODO: Provisioners are not yet implemented in this repository.
              When provisioners are added, replace this stub:

              - Resolve `stage.provisioner` (or fall back to `stage.type`) to
                a registered provisioner class.
              - Instantiate it with the relevant service models and paths.
              - Call `provisioner.run(stage, deployment_service=..., build_path=...)`.
              - Return the provisioner's success flag.

              Example structure to target:
                  from xyz_platform.provisioners.factory import ProvisionerFactory
                  provisioner = ProvisionerFactory.get(stage.provisioner or stage.type)
                  return provisioner.run(
                      stage=stage,
                      deployment_service=self._deployment_service,
                      configuration_service=self._configuration_service,
                      build_path=self._build_path,
                      work_path=self._work_path,
                      force=self._force,
                  )
        """
        self.logger.warning(
            "Provisioners not yet implemented — stage skipped",
            extra={"stage": stage.name, "type": stage.type},
        )
        if self._is_console_output():
            click.echo(
                f"  ⚠️  Stage '{stage.name}': provisioner not yet implemented — skipped."
            )
        # Return True so the overall run does not fail on the stub; change to False
        # once provisioners are wired and the stub should be removed.
        return True

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
