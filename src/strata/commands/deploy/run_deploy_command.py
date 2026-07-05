import uuid
from datetime import datetime as _dt
from datetime import timezone as _tz
from typing import Callable, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_PLAN,
    STEP_SETUP,
)
from strata.deployers.factory import DeployerFactory
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockHandle,
)
from strata.models.deployment_manifest_model import (
    ManifestOutputsReferenceModel,
)
from strata.models.deployment_model import DeploymentStageModel


class RunDeployCommand(BaseDeployCommand):
    """Run the deploy pipeline for a deployment definition."""

    OPERATION = "deploy_run"

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
        self._execution_id: str = str(uuid.uuid4())

    # -------------------------------------------------------------------------
    # Finalize override — writes deploy-log before standard finalization
    # -------------------------------------------------------------------------

    def _finalize(self, success: bool = False, show_footer: bool = True) -> bool:
        """Write deploy-log audit evidence, then delegate to parent finalize."""
        if self._deploy_started_at and not self._dry_run:
            self._write_deploy_log(success)
        return super()._finalize(success=success, show_footer=show_footer)

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

            self._record_deploy_start()

            if not self._load_related_services():
                if self._is_console_output():
                    click.echo("\n❌  Failed to load deployment related services")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                self._finalize(success=False)
                return False

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                self._finalize(success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Validating and planning deploy — no provisioning will run")

            if not self._run_lifecycle_phase(
                "deploy_run_before",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Pre-deploy lifecycle hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                self._finalize(success=False)
                return False

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Deploy provisioning failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                self._finalize(success=False)
                return False

            if not self._run_lifecycle_phase(
                "deploy_configure",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n\u274c  Configure lifecycle hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                self._finalize(success=False)
                return False

            if not self._run_lifecycle_phase(
                "deploy_run_after",
                context={"file": str(self._file_path), "stage": self._stage, "dry_run": self._dry_run},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Post-deploy lifecycle hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
                self._finalize(success=False)
                return False

            if not self._after_execute():
                if self._is_console_output():
                    click.echo("\n❌  Post-execution hook failed")
                self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
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

            manifest_path = self._write_deployment_manifest(
                action="deploy",
                status="success",
                dry_run=self._dry_run,
            )
            if manifest_path and self._is_console_output():
                click.echo(f"\n📋  Deployment manifest: {manifest_path}")

            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_run: {exc}")
            self.logger.exception("deploy_run failed")
            self._finalize(success=False)
            self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
            return False

    # -------------------------------------------------------------------------
    # Internal pipeline steps
    # -------------------------------------------------------------------------

    def _write_deploy_log(self, success: bool) -> None:
        """Assemble and write deploy-log via AuditController.

        This is best-effort — failures are logged as WARNING and never
        affect the deployment exit code (ADR 0018, decision #2).
        """
        try:
            from strata.controllers.audit_controller import AuditController
            from strata.models.deploy_log_model import (
                DeployLogModel,
                DeployLogStageModel,
                DeployLogStepModel,
            )
            from strata.utils.config import SOLUTION_DEPLOY_LOG_DIR, SOLUTION_DIR

            # Assemble per-stage data from manifest stage results
            stages: List[DeployLogStageModel] = []
            for sr in self._stage_results:
                stage_steps: List[DeployLogStepModel] = []
                if sr.steps:
                    for step_name in sr.steps:
                        stage_steps.append(DeployLogStepModel(step=step_name, success=True, duration_seconds=0.0))

                stages.append(
                    DeployLogStageModel(
                        name=sr.name,
                        provisioner=sr.provisioner,
                        topology=sr.topology,
                        success=(sr.status == "success"),
                        started_at=sr.started_at or self._deploy_started_at or "",
                        completed_at=sr.completed_at or _dt.now(_tz.utc).isoformat(),
                        duration_seconds=float(sr.duration_seconds or 0),
                        steps=stage_steps,
                        errors=[sr.error] if sr.error else [],
                    )
                )

            # Calculate total duration
            completed_at = _dt.now(_tz.utc).isoformat()
            try:
                duration = (
                    _dt.fromisoformat(completed_at) - _dt.fromisoformat(self._deploy_started_at or completed_at)
                ).total_seconds()
            except (ValueError, TypeError):
                duration = 0.0

            # Get git context (best-effort)
            commit_sha = self._get_git_field("rev-parse", "HEAD")
            commit_message = self._get_git_field("log", "--format=%s", "-1")
            commit_author = self._get_git_field("log", "--format=%ae", "-1")

            # Get version
            from strata import __version__

            # Resolve deployment metadata
            deployment_name = ""
            workspace_name = None
            environment = None
            if self._deployment_service and self._deployment_service.model:
                deployment_name = self._deployment_service.model.meta.name
                spec = self._deployment_service.model.spec
                if spec:
                    workspace_name = spec.workspace.name if spec.workspace else None
                    layers = spec.layers
                    environment = layers.get("environment") if layers else None

            payload = DeployLogModel(
                execution_id=self._execution_id,
                timestamp=self._deploy_started_at or completed_at,
                version=__version__,
                commit_sha=commit_sha,
                commit_message=commit_message,
                commit_author=commit_author,
                deployment=deployment_name or "unknown",
                workspace=workspace_name,
                environment=environment,
                file=str(self._file_path or ""),
                force=self._force,
                dry_run=False,
                success=success,
                duration_seconds=duration,
                stages=stages,
                errors=list(self._errors),
                messages=list(self._messages),
            )

            # Resolve audit config (structure + base path)
            structure = "by-execution"
            base_path = self._work_path / SOLUTION_DIR / SOLUTION_DEPLOY_LOG_DIR
            resolved_audit_cfg = None
            if self._configuration_service:
                resolved_audit_cfg = getattr(getattr(self._configuration_service.model, "spec", None), "audit", None)
                if resolved_audit_cfg:
                    structure = resolved_audit_cfg.structure or structure
                base_path = self._configuration_service.get_deploy_log_path(self._work_path, create_path=True)

            # Write via AuditController
            controller = AuditController(
                work_path=self._work_path,
                siem_sinks=self._resolve_siem_sinks(resolved_audit_cfg),
            )
            ok, path = controller.write_deploy_log(
                payload=payload,
                base_path=base_path,
                structure=structure,
            )

            if ok and path and self._is_console_output():
                self._audit_log_path = str(path.relative_to(self._work_path))
                click.echo(f"  📝  Deploy-log: {self._audit_log_path}")
            elif ok and path:
                self._audit_log_path = str(path.relative_to(self._work_path))

        except Exception as exc:
            self.logger.warning("deploy_log_write_failed", error=str(exc))

    def _get_git_field(self, *args: str) -> Optional[str]:
        """Run a git command and return stdout, or None on failure."""
        try:
            from strata.utils.system import run_command

            result = run_command(["git"] + list(args), cwd=str(self._work_path), timeout=10)
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    def _resolve_siem_sinks(self, audit_config=None) -> list:
        """Resolve integration-backed SIEM sinks from the current configuration.

        Iterates audit_config.sinks, finds integration-backed entries, instantiates
        them via IntegrationFactory, and returns those that implement ISiemSink.
        Always returns a list (may be empty). Never raises.
        """
        sinks: list = []
        if not audit_config or not audit_config.sinks:
            return sinks
        if not self._configuration_service or not self._configuration_service.model:
            return sinks

        integration_models = getattr(getattr(self._configuration_service.model, "spec", None), "integrations", []) or []
        integration_map = {m.name: m for m in integration_models}

        from strata.integrations.capabilities import ISiemSink
        from strata.integrations.factory import IntegrationFactory

        for sink in audit_config.sinks:
            if not sink.enabled or not sink.integration:
                continue
            model = integration_map.get(str(sink.integration))
            if not model or not model.enabled:
                continue
            # Check event filter
            if sink.events and "deploy_audit" not in sink.events:
                continue
            try:
                instance = IntegrationFactory.create(model)
                if isinstance(instance, ISiemSink):
                    sinks.append(instance)
            except Exception as exc:
                self.logger.warning(
                    "siem_sink_resolve_failed",
                    name=sink.integration,
                    error=str(exc),
                )
        return sinks

    def _load_related_services(self) -> bool:
        """Services are already loaded by BaseDeployCommand._before_execute."""
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
            self._deployment_service,  # type: ignore[arg-type]
            strict=False,  # type: ignore[arg-type]
        )

        self._resolved_values = resolved

        if errors:
            for err in errors:
                self.logger.warning("Value resolution warning: %s", err)
            if self._is_console_output():
                click.echo(f"  ⚠️  {len(errors)} value(s) could not be resolved (see logs for details).")

        if self._is_console_output() and not resolved.is_empty():
            click.echo(
                f"  ✓  Resolved {len(resolved.variables)} variable(s), "
                f"{len(resolved.secrets)} secret(s), "
                f"{len(resolved.features)} feature(s)."
            )

        # Always log STRATA_CONTEXT/STRATA_SENSITIVE at DEBUG; show under --verbose
        self.logger.debug("strata_context_resolved", **resolved.debug_summary())
        if self._is_verbose() and self._is_console_output() and not resolved.is_empty():
            summary = resolved.debug_summary()
            ctx = summary["strata_context"]
            sens = summary["strata_sensitive"]
            click.echo("  STRATA_CONTEXT:")
            for section, values in ctx.items():
                if values:
                    for k, v in values.items():
                        click.echo(f"    [{section}] {k} = {v}")
            click.echo("  STRATA_SENSITIVE (keys only):")
            for section, masked in sens.items():
                if masked:
                    for k in masked:
                        click.echo(f"    [{section}] {k} = ***")

        # ok is always True in non-strict mode — keep going even with warnings
        return True

    def _execute_provisioning(self) -> bool:
        """Iterate deployment stages and invoke the appropriate provisioner per stage."""
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        if not all_stages:
            if self._is_console_output():
                click.echo("⚠️  No deployment stages defined — nothing to deploy.")
            return True

        # Filter to a single stage when --stage is supplied
        stages_to_run = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages

        if self._stage and not stages_to_run:
            self._errors.append(
                f"Stage '{self._stage}' not found in deployment definition. Available: {[s.name for s in all_stages]}"
            )
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
            click.echo(f"\n🚀  Deploying {len(stages_to_run)} stage(s)…")

        # Check approval gates before any provisioning
        if not self._dry_run:
            if not self._check_approvals(stages_to_run):
                return False

        # Acquire deployment lock wrapping the full stage pipeline
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

                ok = self._execute_stage_provisioning(stage)
                if not ok:
                    if stage.on_failure == "continue":
                        if self._is_console_output():
                            click.echo(f"  ⚠️  Stage '{stage.name}' failed — on_failure=continue, proceeding.")
                        continue
                    # Default: stop
                    self._errors.append(f"Stage '{stage.name}' failed (on_failure=stop).")
                    return False

            if self._is_console_output() and not self._dry_run:
                click.echo("\n✅  All stages completed.")

            return True
        finally:
            if lock_handle is not None and lock_backend is not None:
                self._release_lock(lock_backend, lock_handle)

    def _execute_stage_provisioning(self, stage: DeploymentStageModel) -> bool:
        """Instantiate the deployer for *stage*, validate, then run the step sequence.

        Step sequences:
          dry-run  : setup → check → plan
          destroy  : setup → destroy  (requires --force for -auto-approve)
          normal   : setup → check → plan → apply
        """
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

        # --- pre-flight validation ---
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

        # --- determine step sequence ---
        if self._dry_run:
            steps_to_run = [STEP_SETUP, STEP_CHECK, STEP_PLAN]
        else:
            steps_to_run = [STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY]

        # --- dry-run: surface deployer-specific plan context before steps run ---
        if self._dry_run and self._is_console_output():
            for line in deployer.describe_plan():
                click.echo(f"    [DRY-RUN] {line}")

        supported = deployer.get_supported_steps()

        # --- emit stage-start event (NDJSON) ---
        if self._is_ndjson_output():
            self.emit_ndjson(
                {
                    "event": "stage_start",
                    "stage": stage.name,
                    "ts": _dt.now(_tz.utc).isoformat(),
                }
            )

        # --- stage-level before hook ---
        if not self._run_hierarchy_lifecycle_phase(
            "deploy_stage_before",
            context={"stage": str(stage.name), "dry_run": self._dry_run},
        ):
            self._errors.append(f"Stage '{stage.name}': deploy_stage_before lifecycle hook failed.")
            self._record_stage_result(
                stage_name=str(stage.name),
                provisioner=stage.provisioner,
                topology=stage.topology,
                status="failed",
                started_at=stage_started,
                completed_at=_dt.now(_tz.utc).isoformat(),
                error="deploy_stage_before hook failed",
            )
            return False

        # --- execute each step ---
        for step_name in steps_to_run:
            if step_name not in supported:
                self._errors.append(
                    f"Stage '{stage.name}': step '{step_name}' is not supported "
                    f"by deployer '{deployer.get_deployer_name()}'."
                )
                return False

            # Build the appropriate line callback for this step.
            line_cb: Optional[Callable[[str, str], None]] = None
            if self._is_ndjson_output():
                # Tier 2: stream each subprocess output line as an NDJSON event.
                self.emit_ndjson(
                    {
                        "event": "step_start",
                        "step": step_name,
                        "stage": stage.name,
                        "ts": _dt.now(_tz.utc).isoformat(),
                    }
                )
                line_cb = self.make_ndjson_line_callback(step=step_name, stage=stage.name)
            elif self._is_verbose():
                # Tier 1: print subprocess lines live to the console as they arrive.
                # Use the deployer name (e.g. "terraform") so the output is clearly
                # attributed, with a │ gutter to separate it from strata messages.
                def _make_verbose_cb(tool: str) -> Callable[[str, str], None]:
                    def _cb(stream: str, text: str) -> None:
                        if stream == "stderr":
                            click.secho(f"      {tool} │ {text}", fg="yellow", err=True)
                        else:
                            click.secho(f"      {tool} │ {text}", fg="cyan")

                    return _cb

                line_cb = _make_verbose_cb(deployer.get_deployer_name())

            if self._is_console_output():
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"    {prefix}{step_name}")

            step_fn = getattr(deployer, step_name)
            # Steps that support line_callback accept it as a keyword arg;
            # output/show_plan return (bool, dict, list) and don't stream.

            # --- deploy_apply_before hook ---
            if step_name == STEP_APPLY:
                if not self._run_hierarchy_lifecycle_phase(
                    "deploy_apply_before",
                    context={"stage": str(stage.name), "dry_run": self._dry_run},
                ):
                    self._errors.append(f"Stage '{stage.name}': deploy_apply_before lifecycle hook blocked apply.")
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="deploy_apply_before hook blocked apply",
                    )
                    return False

            if step_name in (STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY, STEP_DESTROY):
                ok, msgs = step_fn(line_callback=line_cb)
            else:
                ok, msgs = step_fn()
            self._messages.extend(msgs)
            if self._is_console_output():
                for msg in msgs:
                    click.echo(f"      {msg}")
                if self._dry_run and not msgs:
                    click.echo(f"      (no extra information available for '{step_name}' in dry run)")
            if not ok:
                self._errors.extend(msgs)
                if self._is_ndjson_output():
                    self.emit_ndjson(
                        {
                            "event": "step_end",
                            "step": step_name,
                            "stage": stage.name,
                            "success": False,
                            "ts": _dt.now(_tz.utc).isoformat(),
                        }
                    )
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

            if self._is_ndjson_output():
                self.emit_ndjson(
                    {
                        "event": "step_end",
                        "step": step_name,
                        "stage": stage.name,
                        "success": True,
                        "ts": _dt.now(_tz.utc).isoformat(),
                    }
                )

            # --- deploy_apply_after hook ---
            if step_name == STEP_APPLY:
                if not self._run_hierarchy_lifecycle_phase(
                    "deploy_apply_after",
                    context={"stage": str(stage.name), "dry_run": self._dry_run},
                ):
                    self._errors.append(f"Stage '{stage.name}': deploy_apply_after lifecycle hook failed.")
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="deploy_apply_after hook failed",
                    )
                    return False

            # --- plan gate: enforce deploy_plan_after hook before apply ---
            if step_name == STEP_PLAN and STEP_APPLY in steps_to_run:
                if not self._run_lifecycle_phase(
                    "deploy_plan_after",
                    context={"stage": str(stage.name), "dry_run": self._dry_run},
                ):
                    self._errors.append(f"Stage '{stage.name}': deploy_plan_after lifecycle hook blocked apply.")
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="deploy_plan_after hook blocked apply",
                    )
                    return False

                # --- policy evaluation: plan phase ---
                if not self._evaluate_phase_policies("plan", stage, deployer):
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="Plan policy denied deployment",
                    )
                    return False

                # --- policy evaluation: deploy phase ---
                if not self._evaluate_phase_policies("deploy", stage, deployer):
                    self._record_stage_result(
                        stage_name=str(stage.name),
                        provisioner=stage.provisioner,
                        topology=stage.topology,
                        status="failed",
                        started_at=stage_started,
                        completed_at=_dt.now(_tz.utc).isoformat(),
                        steps=steps_to_run,
                        error="Deploy policy denied deployment",
                    )
                    return False

        # --- save plan JSON for artifact upload / downstream use ---
        if STEP_PLAN in steps_to_run:
            ok_save, plan_json_path, save_msgs = deployer.save_plan_json()
            self._messages.extend(save_msgs)
            if self._is_console_output():
                for msg in save_msgs:
                    click.echo(f"      {msg}")
                if ok_save and plan_json_path:
                    click.echo(f"    plan JSON \u2192 {plan_json_path}")

        # --- collect outputs for downstream stages ---
        out_path = None
        if STEP_APPLY in steps_to_run:
            _ok_out, _outputs, _sensitive, _out_msgs = deployer.collect_outputs()
            if _ok_out and self._resolved_values is not None:
                if _outputs:
                    self._resolved_values.stage_outputs.update(_outputs)
                if _sensitive:
                    self._resolved_values.stage_outputs_sensitive.update(_sensitive)
                if self._is_console_output() and (_outputs or _sensitive):
                    _sens_note = f", {len(_sensitive)} sensitive (not injected)" if _sensitive else ""
                    click.echo(f"    \u2713  Collected {len(_outputs)} output(s){_sens_note} for downstream stages.")
                if _outputs or _sensitive:
                    self.logger.debug(
                        "stage_outputs_collected",
                        stage=stage.name,
                        **self._resolved_values.debug_summary(),
                    )
                    if self._is_verbose() and self._is_console_output():
                        if _outputs:
                            for k, v in _outputs.items():
                                click.echo(f"      [stage_output] {k} = {v}")
                        if _sensitive:
                            for k in _sensitive:
                                click.echo(f"      [stage_output_sensitive] {k} = ***")
            if _ok_out:
                out_path = self._write_outputs_artifact(str(stage.name), _outputs, _sensitive)
                if out_path and self._is_console_output():
                    click.echo(f"    outputs \u2192 {out_path}")
        elif self._dry_run and self._is_console_output():
            click.echo("    [DRY-RUN] Stage outputs not captured \u2014 apply did not run.")

        if self._is_ndjson_output():
            self.emit_ndjson(
                {
                    "event": "stage_end",
                    "stage": stage.name,
                    "success": True,
                    "ts": _dt.now(_tz.utc).isoformat(),
                }
            )

        # Record stage result for deployment manifest
        stage_outputs = None
        if STEP_APPLY in steps_to_run and self._resolved_values is not None:
            stage_outputs = dict(self._resolved_values.stage_outputs) if self._resolved_values.stage_outputs else None

        outputs_artifact_ref: Optional[ManifestOutputsReferenceModel] = None
        if out_path is not None and self._deployment_service is not None:
            deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
            labels = deploy_meta.labels or {}
            version = str(labels.get("version", "unknown"))
            try:
                rel = str(out_path.relative_to(self._work_path))
            except ValueError:
                rel = str(out_path)
            outputs_artifact_ref = ManifestOutputsReferenceModel(
                path=rel,
                stage=str(stage.name),
                version=version,
                written_at=_dt.now(_tz.utc).isoformat(),
            )

        self._record_stage_result(
            stage_name=str(stage.name),
            provisioner=stage.provisioner,
            topology=stage.topology,
            status="success",
            started_at=stage_started,
            completed_at=_dt.now(_tz.utc).isoformat(),
            steps=steps_to_run,
            outputs=stage_outputs,
            outputs_artifact=outputs_artifact_ref,
        )

        # --- stage-level after hook ---
        if not self._run_hierarchy_lifecycle_phase(
            "deploy_stage_after",
            context={"stage": str(stage.name), "dry_run": self._dry_run},
        ):
            self._errors.append(f"Stage '{stage.name}': deploy_stage_after lifecycle hook failed.")
            return False

        return True

    def _evaluate_phase_policies(self, phase: str, stage: DeploymentStageModel, deployer) -> bool:
        """Evaluate policies for *phase* ('plan' or 'deploy'). Returns False if any deny-enforcement policy fails."""
        from strata.models.deployment_manifest_model import ManifestPolicyResultModel
        from strata.validators.policies.base_policy import PolicyContext
        from strata.validators.policies.policy_engine import PolicyEngine

        if self._configuration_service is None:
            return True

        spec = self._configuration_service.model.spec if self._configuration_service.model else None
        policy_models = getattr(spec, "policies", None) or []
        phase_policies = [p for p in policy_models if p.phase == phase and p.enabled]
        if not phase_policies:
            return True

        # Load plan JSON from the deployer (available for both plan and deploy phases)
        plan_data = None
        if hasattr(deployer, "show_plan"):
            _, plan_data, _ = deployer.show_plan()

        context = PolicyContext(
            phase=phase,
            work_path=self._work_path,
            deployment_service=self._deployment_service,
            configuration_service=self._configuration_service,
            plan_data=plan_data,
            build_path=self._build_path,
        )

        engine = PolicyEngine(phase_policies)
        results = engine.evaluate(phase, context)

        denied = False
        for policy_model, result in zip(phase_policies, results, strict=False):
            self._policy_results.append(
                ManifestPolicyResultModel(
                    policy_name=result.policy_name,
                    policy_type=policy_model.type,
                    phase=phase,
                    enforcement=result.enforcement,
                    passed=result.passed,
                    violations=result.violations or [],
                )
            )
            if result.passed:
                if self._is_verbose() and self._is_console_output():
                    click.echo(f"    \u2713  Policy '{result.policy_name}' passed")
            else:
                for v in result.violations:
                    if result.enforcement == "deny":
                        click.echo(f"    \u2717  Policy '{result.policy_name}' DENIED: {v}")
                        self._errors.append(f"Policy '{result.policy_name}': {v}")
                        denied = True
                    elif result.enforcement == "warn":
                        click.echo(f"    \u26a0  Policy '{result.policy_name}' warning: {v}")
                    elif result.enforcement == "audit" and self._is_verbose():
                        click.echo(f"    \u00b7  Policy '{result.policy_name}' audit: {v}")
        return not denied

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
                solution_controller=self._solution_controller,
            )
        except ValueError as exc:
            self._errors.append(str(exc))
            return None

    def _check_approvals(self, stages_to_run: List[DeploymentStageModel]) -> bool:
        """Log approval metadata declared in spec.approvals before executing stages.

        Approvals are metadata-only — enforcement is delegated to the CI/CD system
        (ADO environment gate, GitHub Actions environment, etc.).  The CLI logs
        which approvers apply per stage so the audit trail is clear.

        Empty approvers dict → no gate (treated as absent).
        Stage without an approval override → all spec-level approvers apply.
        """
        if self._deployment_service is None:
            return True
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        approvals = getattr(spec, "approvals", None)

        if not approvals or not approvals.approvers:
            return True

        for stage in stages_to_run:
            if stage.approval:
                active_keys = stage.approval.approvers
            else:
                active_keys = list(approvals.approvers.keys())

            if active_keys:
                active_approvers = [
                    f"{approvals.approvers[k].type}:{approvals.approvers[k].value}"
                    for k in active_keys
                    if k in approvals.approvers
                ]
                self.logger.info(
                    "Approval gate declared",
                    stage=stage.name,
                    approvers=active_approvers,
                )

        return True
