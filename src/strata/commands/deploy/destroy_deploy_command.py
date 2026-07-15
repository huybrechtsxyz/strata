from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.deployers.base_deployer import (
    STEP_DESTROY,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
)
from strata.deployers.factory import DeployerFactory
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockHandle,
)
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
        force_lock: bool = False,
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
        self._force_lock = force_lock
        self._resolved_values: Optional[ResolvedValues] = None

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def _execute(self) -> bool:
        try:
            self._record_deploy_start()

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Planning destroy \u2014 no infrastructure will be removed")
            elif self._is_console_output():
                click.echo("\n⚠️  --destroy: removing provisioned infrastructure per stage")

            if not self._run_lifecycle_phase(
                "deploy_destroy_before",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-destroy lifecycle hook failed")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Destroy failed")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
                return False

            if not self._run_lifecycle_phase(
                "deploy_destroy_after",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-destroy lifecycle hook failed")
                self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
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

            manifest_path = self._write_deployment_manifest(
                action="destroy",
                status="success",
                dry_run=self._dry_run,
            )
            if manifest_path and self._is_console_output():
                click.echo(f"\n📋  Deployment manifest: {manifest_path}")

            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_destroy: {exc}")
            self.logger.exception("deploy_destroy failed")
            self._write_deployment_manifest(action="destroy", status="failed", dry_run=self._dry_run)
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

        # Acquire deployment lock wrapping the full stage pipeline.
        # Destroy is a destructive operation and must hold the lock.
        lock_handle: Optional[LockHandle] = None
        lock_backend: Optional[BaseLockBackend] = None
        if self._should_lock():
            lock_backend = self._resolve_lock_backend(stages_to_run)
            lock_handle = self._acquire_lock(lock_backend)
            if lock_handle is None:
                return False

        try:
            for stage in stages_to_run:
                if self._is_console_output():
                    label = f"[{stage.name}]"
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
        finally:
            if lock_handle is not None and lock_backend is not None:
                self._release_lock(lock_backend, lock_handle)

    def _execute_stage_destroy(self, stage: DeploymentStageModel) -> bool:
        stage_started = _dt.now(_tz.utc).isoformat()
        deployer = self._create_deployer(stage)
        if deployer is None:
            self._record_stage_result(
                stage_name=str(stage.name),
                provisioner=stage.provisioner,
                topology=stage.topology,
                status="failed",
                started_at=stage_started,
                completed_at=_dt.now(_tz.utc).isoformat(),
                error="Failed to create deployer",
            )
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
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    error=f"Validation '{_label}' failed",
                )
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
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    error="--force flag required",
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
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    steps=steps_to_run,
                    error=f"Step '{step_name}' not supported",
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
                self._record_stage_result(
                    stage_name=str(stage.name),
                    provisioner=stage.provisioner,
                    topology=stage.topology,
                    status="failed",
                    started_at=stage_started,
                    completed_at=_dt.now(_tz.utc).isoformat(),
                    steps=steps_to_run,
                    error=f"Step '{step_name}' failed",
                )
                return False

        self._record_stage_result(
            stage_name=str(stage.name),
            provisioner=stage.provisioner,
            topology=stage.topology,
            status="success",
            started_at=stage_started,
            completed_at=_dt.now(_tz.utc).isoformat(),
            steps=steps_to_run,
        )

        return True

    def _create_deployer(self, stage: DeploymentStageModel):
        """Instantiate and return the deployer for *stage*, or None.

        Resolution (mutually exclusive — exactly one required at runtime):
        - stage.provisioner → look up named provisioner entry in workspace
        - stage.topology    → look up topology by name → derive provisioner type
                              (errors if topology not found or provisioner is ambiguous)
        An error is appended to self._errors when resolution fails.
        """
        if self._deployment_service is None:
            self._errors.append(f"Stage '{stage.name}': deployment service not loaded.")
            return None

        resolved_type, errors = DeployerFactory.resolve_type(stage, self._deployment_service)
        if errors:
            self._errors.extend(errors)
        if resolved_type is None:
            return None

        # Filter STRATA_SENSITIVE to only secrets declared by this stage
        _stage_values = self._resolved_values.for_stage(stage.secrets) if self._resolved_values else None

        try:
            return DeployerFactory.create(
                resolved_type,
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
                force=self._force,
                resolved_values=_stage_values,
            )
        except ValueError as exc:
            self._errors.append(str(exc))
            return None
