import os
import socket
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
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.integrations.lock.base_lock_backend import (
    BaseLockBackend,
    LockBackendError,
    LockHandle,
    LockTimeoutError,
)
from strata.models.common_models import ProvisionerType
from strata.models.deployment_manifest_model import (
    ManifestLockReferenceModel,
    ManifestOutputsReferenceModel,
)
from strata.models.deployment_model import DeploymentStageModel
from strata.utils.duration import parse_duration


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

            manifest_path = self._write_deployment_manifest(
                action="deploy",
                status="success",
                dry_run=self._dry_run,
            )
            if manifest_path and self._is_console_output():
                click.echo(f"\n📋  Deployment manifest: {manifest_path}")

            self._finalize(success=True)
            return True

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_run: {exc}")
            self.logger.exception("deploy_run failed")
            self._write_deployment_manifest(action="deploy", status="failed", dry_run=self._dry_run)
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Internal pipeline steps
    # -------------------------------------------------------------------------

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
        if not self._run_lifecycle_phase(
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
                def _make_verbose_cb(sn: str) -> Callable[[str, str], None]:
                    def _cb(stream: str, text: str) -> None:
                        if stream == "stderr":
                            click.secho(f"      [{sn}] {text}", fg="yellow", err=True)
                        else:
                            click.echo(f"      [{sn}] {text}")

                    return _cb

                line_cb = _make_verbose_cb(step_name)

            if self._is_console_output():
                prefix = "[DRY-RUN] " if self._dry_run else ""
                click.echo(f"    {prefix}{step_name}")

            step_fn = getattr(deployer, step_name)
            # Steps that support line_callback accept it as a keyword arg;
            # output/show_plan return (bool, dict, list) and don't stream.
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
        if not self._run_lifecycle_phase(
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
            # topo.provisioner is a name reference — look up the IaC entry directly by name
            _iac = next((p for p in _provisioners if p.name == topo.provisioner), None)
            if _iac is None:
                self._errors.append(
                    f"Stage '{stage.name}': topology '{stage.topology}' references provisioner "
                    f"'{topo.provisioner}' which is not defined in the workspace."
                )
                return None
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

        # Filter STRATA_SENSITIVE to only secrets declared by this stage
        _stage_values = self._resolved_values.for_stage(stage.secrets) if self._resolved_values else None

        if resolved_type == "terraform":
            return TerraformDeployer(
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
                resolved_values=_stage_values,
                solution_controller=self._solution_controller,
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
                resolved_values=_stage_values,
                solution_controller=self._solution_controller,
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
                resolved_values=_stage_values,
                solution_controller=self._solution_controller,
            )

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

    # -------------------------------------------------------------------------
    # Lock helpers
    # -------------------------------------------------------------------------

    def _should_lock(self) -> bool:
        """Return True if locking is enabled and this is not a dry-run."""
        if self._dry_run:
            return False
        if self._deployment_service is None:
            return False
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        locking = getattr(spec, "locking", None)
        return locking is not None and locking.enabled

    def _resolve_lock_backend(self, stages: List[DeploymentStageModel]) -> BaseLockBackend:
        """Return the lock backend from the first Terraform provisioner with a backend.

        Falls back to ``LocalLockBackend`` when no matching provisioner is found.
        """
        from strata.integrations.lock.lock_factory import LockFactory

        if self._deployment_service is not None:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service is not None:
                spec = workspace_service.model.spec  # type: ignore[union-attr]
                provisioners = spec.provisioners or []
                for stage in stages:
                    if stage.provisioner:
                        iac = next(
                            (p for p in provisioners if p.name == stage.provisioner),
                            None,
                        )
                        if iac and iac.provisioner == ProvisionerType.TERRAFORM and iac.backend:
                            return LockFactory.create(iac.backend, self._work_path)
        from strata.integrations.lock.lock_factory import LockFactory  # noqa: PLC0415

        return LockFactory.create(None, self._work_path)

    def _acquire_lock(self, backend: BaseLockBackend) -> Optional[LockHandle]:
        """Acquire the deployment lock. Returns the handle or ``None`` on failure."""
        if self._deployment_service is None:
            return None
        deploy_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        locking = getattr(spec, "locking", None)
        wait_timeout: str = getattr(locking, "wait_timeout", "30m") if locking else "30m"
        timeout_seconds = parse_duration(wait_timeout)
        holder = os.environ.get("GITHUB_ACTOR") or os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"
        try:
            handle = backend.acquire(
                deployment_name=deploy_name,
                holder=holder,
                reason="strata deploy run",
                timeout_seconds=timeout_seconds,
            )
            self._lock_ref = ManifestLockReferenceModel(
                lock_id=handle.lock_id,
                backend=handle.backend_type,
                acquired_at=handle.acquired_at,
                holder=holder,
                hostname=socket.gethostname(),
            )
            if self._is_console_output():
                click.echo(f"\n🔒  Lock acquired ({handle.backend_type}) for '{deploy_name}'")
            self.logger.info(
                "deploy_lock_acquired",
                lock_id=handle.lock_id,
                backend=handle.backend_type,
            )
            return handle
        except LockTimeoutError as exc:
            self._errors.append(str(exc))
            if self._is_console_output():
                click.echo(
                    f"\n🔒  Could not acquire lock — held by {exc.holder!r}. "
                    "Run `strata deploy lock status` for details."
                )
            self.logger.error(
                "deploy_lock_timeout",
                deployment=deploy_name,
                holder=exc.holder,
            )
            return None
        except LockBackendError as exc:
            self._errors.append(f"Lock backend error: {exc}")
            self.logger.error("deploy_lock_backend_error", error=str(exc))
            return None

    def _release_lock(self, backend: BaseLockBackend, handle: LockHandle) -> None:
        """Release the deployment lock. Safe to call in a ``finally`` block."""
        released_at = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            backend.release(handle)
            if self._lock_ref is not None:
                self._lock_ref = self._lock_ref.model_copy(update={"released_at": released_at})
            if self._is_console_output():
                click.echo(f"\n🔓  Lock released ({handle.backend_type})")
            self.logger.info("deploy_lock_released", lock_id=handle.lock_id)
        except Exception as exc:  # noqa: BLE001
            self.logger.warning(
                "deploy_lock_release_failed",
                lock_id=handle.lock_id,
                error=str(exc),
            )
