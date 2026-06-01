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
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel


class RunDeployCommand(BaseDeployCommand):
    """Run the deploy pipeline for a deployment definition."""

    OPERATION = "deploy_run"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
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

            if not self._load_related_services():
                if self._is_console_output():
                    click.echo("\n❌  Failed to load deployment related services")
                self._finalize(success=False)
                return False

            if not self._resolve_values():
                if self._is_console_output():
                    click.echo("\n❌  Failed to resolve variables/secrets/features")
                self._finalize(success=False)
                return False

            if self._dry_run and self._is_console_output():
                click.echo("\n[DRY-RUN] Validating and planning deploy — no provisioning will run")

            if not self._execute_provisioning():
                if self._is_console_output():
                    click.echo("\n❌  Deploy provisioning failed")
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
            self._errors.append(f"Failed to execute deploy_run: {exc}")
            self.logger.exception("deploy_run failed")
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

        if self._is_console_output():
            click.echo(f"\n🚀  Deploying {len(stages_to_run)} stage(s)…")

        # Check approval gates before any provisioning
        if not self._dry_run:
            if not self._check_approvals(stages_to_run):
                return False

        for stage in stages_to_run:
            if self._is_console_output():
                label = f"[{stage.type}]" + (f" via {stage.provisioner}" if stage.provisioner else "")
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

        # --- save plan JSON for artifact upload / downstream use ---
        if STEP_PLAN in steps_to_run:
            ok_save, plan_json_path, save_msgs = deployer.save_plan_json()
            self._messages.extend(save_msgs)
            if self._is_console_output():
                for msg in save_msgs:
                    click.echo(f"      {msg}")
                if ok_save and plan_json_path:
                    click.echo(f"    plan JSON \u2192 {plan_json_path}")

        if self._is_ndjson_output():
            self.emit_ndjson(
                {
                    "event": "stage_end",
                    "stage": stage.name,
                    "success": True,
                    "ts": _dt.now(_tz.utc).isoformat(),
                }
            )

        return True

    def _create_deployer(self, stage: DeploymentStageModel):
        """Instantiate and return the deployer for *stage*, or None.

        Type resolution:
          stage.provisioner resolves to an IaC entry → match on ProvisionerType
          stage.type == 'infrastructure' or 'terraform'    → TerraformDeployer
          stage.type == 'configure' or 'ansible'           → AnsibleDeployer
        """
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

        # Convention-based fallback from stage.type
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
                force=self._force,
                resolved_values=self._resolved_values,
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
                resolved_values=self._resolved_values,
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
