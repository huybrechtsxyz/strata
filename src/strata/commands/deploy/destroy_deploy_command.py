from typing import List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.deployers.base_deployer import (
    STEP_DESTROY,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
)
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel


class DestroyDeployCommand(BaseDeployCommand):
    """Tear down provisioned infrastructure for a deployment definition.

    Step sequences:
        --dry-run        : setup → plan_destroy  (shows what would be removed)
        --force          : setup → destroy       (auto-approve, non-interactive)
    """

    OPERATION = "deploy_destroy"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        scope: Optional[str] = None,
        force: bool = False,
        dry_run: bool = False,
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
        self._scope = scope
        self._force = force
        self._dry_run = dry_run
        self._resolved_values: Optional[ResolvedValues] = None

    # -------------------------------------------------------------------------
    # Public entry point
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

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._finalize(success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Planning destroy — no infrastructure will be removed")
            elif self._is_console_output():
                click.echo("\n⚠️  --destroy: removing provisioned infrastructure per stage")

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Destroy failed")
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._finalize(success=False)
                return False

            self._output_data.update(
                {
                    "file": str(self._file_path),
                    "build_path": str(self._build_path),
                    "stage": self._stage,
                    "force": self._force,
                    "dry_run": self._dry_run,
                }
            )

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_destroy: {exc}")
            self.logger.exception("deploy_destroy failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Internal pipeline steps
    # -------------------------------------------------------------------------

    def _resolve_values(self) -> bool:
        controller = ValueController()
        ok, resolved, errors = controller.resolve_values(
            self._deployment_service,  # type: ignore[arg-type]
            strict=False,  # type: ignore[arg-type]
        )
        self._resolved_values = resolved
        if errors:
            for err in errors:
                self.logger.warning("Value resolution warning: %s", err)
        return True

    def _execute_provisioning(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        if not all_stages:
            if self._is_console_output():
                click.echo("⚠️  No deployment stages defined — nothing to destroy.")
            return True

        stages_to_run = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages

        if self._stage and not stages_to_run:
            self._errors.append(f"Stage '{self._stage}' not found. Available: {[s.name for s in all_stages]}")
            return False

        # Filter by --scope label when supplied
        if self._scope:
            stages_to_run = [s for s in stages_to_run if s.scope == self._scope]
            if not stages_to_run:
                self._errors.append(
                    f"No stages match scope '{self._scope}'. "
                    f"Available scopes: {[s.scope for s in all_stages if s.scope]}"
                )
                return False

        if self._is_console_output():
            action = "Planning destroy for" if self._dry_run else "Destroying"
            click.echo(f"\n💣  {action} {len(stages_to_run)} stage(s)…")

        for stage in stages_to_run:
            if self._is_console_output():
                label = f"[{stage.type}]"
                if stage.provisioner:
                    label += f" via {stage.provisioner}"
                elif stage.topology:
                    label += f" topology:{stage.topology}"
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"\n  ▶  {prefix}Stage: {stage.name}  {label}")

            ok = self._execute_stage_destroy(stage)
            if not ok:
                if stage.on_failure == "continue":
                    if self._is_console_output():
                        click.echo(f"  ⚠️  Stage '{stage.name}' failed — on_failure=continue, proceeding.")
                    continue
                self._errors.append(f"Stage '{stage.name}' failed (on_failure=stop).")
                return False

        if self._is_console_output() and not self._dry_run:
            click.echo("\n✅  All stages destroyed.")

        return True

    def _execute_stage_destroy(self, stage: DeploymentStageModel) -> bool:
        deployer = self._create_deployer(stage)
        if deployer is None:
            return False

        # Pre-flight validation
        for _label, validate_fn in (
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

        # Step sequence
        if self._dry_run:
            steps_to_run = [STEP_SETUP, STEP_PLAN_DESTROY]
        else:
            if not self._force:
                self._errors.append(
                    f"Stage '{stage.name}': --force is required to run destroy "
                    "(non-interactive execution needs -auto-approve). "
                    "Use --dry-run to preview what would be removed."
                )
                return False
            steps_to_run = [STEP_SETUP, STEP_DESTROY]

        supported = deployer.get_supported_steps()

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

        Resolution (mutually exclusive — exactly one required at runtime):
        - stage.provisioner → look up named provisioner entry in workspace
        - stage.topology    → look up topology by name → derive provisioner type
                              (errors if topology not found or provisioner is ambiguous)
        An error is appended to self._errors when resolution fails.
        """
        resolved_type: Optional[str] = None
        _iac = None

        if self._deployment_service is None:
            self._errors.append(f"Stage '{stage.name}': deployment service not loaded.")
            return None

        workspace_service = self._deployment_service.get_workspace_service()
        if workspace_service is None:
            self._errors.append(f"Stage '{stage.name}': workspace service not loaded.")
            return None

        spec = workspace_service.model.spec  # type: ignore[union-attr]
        _provisioners = spec.provisioners or []
        _available = [str(p.name) for p in _provisioners]

        if stage.provisioner:
            _iac = next((p for p in _provisioners if p.name == stage.provisioner), None)
            if _iac and _iac.provisioner == ProvisionerType.TERRAFORM:
                resolved_type = "terraform"
            elif _iac and _iac.provisioner == ProvisionerType.ANSIBLE:
                resolved_type = "ansible"
            elif _iac and _iac.provisioner == ProvisionerType.COMPOSE:
                resolved_type = "compose"
            elif _iac and _iac.provisioner == ProvisionerType.HELM:
                resolved_type = "helm"

        elif stage.topology:
            _topologies = spec.topology or []
            topo = next((t for t in _topologies if str(t.name) == stage.topology), None)
            if topo is None:
                _topo_names = [str(t.name) for t in _topologies]
                self._errors.append(
                    f"Stage '{stage.name}': topology '{stage.topology}' not found in workspace. "
                    f"Available: {_topo_names if _topo_names else ['(none defined)']}"
                )
                return None
            matching = [p for p in _provisioners if p.provisioner == topo.provisioner]
            if not matching:
                self._errors.append(
                    f"Stage '{stage.name}': topology '{stage.topology}' requires provisioner type "
                    f"'{topo.provisioner.value}' but no matching provisioner is defined in the workspace."
                )
                return None
            if len(matching) > 1:
                names = [str(p.name) for p in matching]
                self._errors.append(
                    f"Stage '{stage.name}': topology '{stage.topology}' is ambiguous — "
                    f"multiple '{topo.provisioner.value}' provisioners found: {names}. "
                    "Specify 'provisioner' explicitly to disambiguate."
                )
                return None
            _iac = matching[0]
            if _iac.provisioner == ProvisionerType.TERRAFORM:
                resolved_type = "terraform"
            elif _iac.provisioner == ProvisionerType.ANSIBLE:
                resolved_type = "ansible"
            elif _iac.provisioner == ProvisionerType.COMPOSE:
                resolved_type = "compose"
            elif _iac.provisioner == ProvisionerType.HELM:
                resolved_type = "helm"

        if resolved_type is None:
            if not stage.provisioner and not stage.topology:
                self._errors.append(
                    f"Stage '{stage.name}': either 'provisioner' or 'topology' is required — "
                    "name a workspace provisioner entry directly, or name a workspace topology "
                    "to derive the provisioner from the topology definition."
                )
            elif stage.provisioner and _iac is None:
                self._errors.append(
                    f"Stage '{stage.name}': provisioner '{stage.provisioner}' not found in workspace. "
                    f"Available: {_available if _available else ['(none defined)']}"
                )
            elif _iac is not None:
                self._errors.append(
                    f"Stage '{stage.name}': provisioner has unsupported type "
                    f"'{_iac.provisioner}'. Supported: terraform, ansible, compose, helm."
                )
            return None

        if resolved_type == "terraform":
            return TerraformDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=self._force,
                resolved_values=self._resolved_values,
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
                force=self._force,
                resolved_values=self._resolved_values,
            )

        if resolved_type == "compose":
            from strata.deployers.compose_deployer import ComposeDeployer

            return ComposeDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=self._force,
                resolved_values=self._resolved_values,
            )

        if resolved_type == "helm":
            from strata.deployers.helm_deployer import HelmDeployer

            return HelmDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=self._force,
                resolved_values=self._resolved_values,
            )

        return None
