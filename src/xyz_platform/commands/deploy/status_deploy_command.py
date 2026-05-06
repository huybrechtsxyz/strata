"""Command to report live Terraform outputs, saved plan details, and deploy history."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import click

from xyz_platform.commands.deploy.base_deploy_command import BaseDeployCommand
from xyz_platform.deployers.terraform_deployer import TerraformDeployer
from xyz_platform.models.common_models import ProvisionerType
from xyz_platform.models.deployment_model import DeploymentStageModel

# Deploy operations to include in history scans
_DEPLOY_OPERATIONS = {"deploy_run", "deploy_destroy"}


class StatusDeployCommand(BaseDeployCommand):
    """Report deployment status for a deployment definition.

    Three modes (select with flags; default = live outputs):

    Default (no flags)
        For each stage: runs ``terraform output -json`` → prints live
        infrastructure outputs from the remote backend.

    ``--plan``
        Reads the last saved ``.tfplan`` file produced by
        ``deploy run --dry-run`` and shows a human-readable change summary
        (``terraform show -json <plan>``).  No network calls to the backend.

    ``--history``
        Scans ``.platform/logs/`` for entries from ``deploy_run`` and
        ``deploy_destroy`` operations and shows a table of past executions.
    """

    OPERATION = "deploy_status"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        show_plan: bool = False,
        show_history: bool = False,
        lines: int = 50,
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
        self._show_history = show_history
        self._history_lines = lines

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

            # History mode does not need the deployment file
            if self._show_history:
                ok = self._run_history()
                self._finalize(success=ok)
                return ok

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
    # Mode: history
    # -------------------------------------------------------------------------

    def _run_history(self) -> bool:
        if self._is_console_output():
            click.echo("\n📜  Deploy history (from workspace logs)…\n")

        ok, entries, errors = self._solution_controller.get_logs(
            work_path=self._work_path,
            lines=self._history_lines * 20,  # over-fetch; we filter below
        )
        self._errors.extend(errors)
        if not ok:
            return False

        # Keep only deploy start/finish events
        deploy_events = [
            e
            for e in entries
            if e.get("command") in _DEPLOY_OPERATIONS
            or e.get("operation") in _DEPLOY_OPERATIONS
            or (e.get("event", "").startswith("Command execution") and e.get("command", "") in _DEPLOY_OPERATIONS)
        ]

        # Group by execution_id → last entry per execution (has success/fail info)
        by_exec: Dict[str, Dict[str, Any]] = {}
        for e in deploy_events:
            eid = e.get("execution_id", "")
            if eid:
                by_exec[eid] = e  # later entries overwrite; last = finalize event

        history: List[Dict[str, Any]] = []
        for eid, entry in by_exec.items():
            ts_raw = entry.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                ts = ts_raw[:16] if ts_raw else "?"

            history.append(
                {
                    "when": ts,
                    "operation": entry.get("command", entry.get("operation", "?")),
                    "execution_id": eid,
                    "success": entry.get("success"),
                }
            )

        history.sort(key=lambda r: r["when"], reverse=True)
        history = history[: self._history_lines]

        self._output_data = {
            "mode": "history",
            "total": len(history),
            "entries": history,
        }

        if self._is_console_output():
            if not history:
                click.echo("  (no deploy history found in workspace logs)")
                click.echo("  Tip: logs are written to .platform/logs/ — older runs may have been cleaned.")
            else:
                click.echo(f"  {'WHEN':<18}  {'OPERATION':<20}  {'RESULT'}")
                click.echo("  " + "-" * 55)
                for row in history:
                    result = (
                        "✅ success" if row["success"] is True else "❌ failed" if row["success"] is False else "  —"
                    )
                    click.echo(f"  {row['when']:<18}  {row['operation']:<20}  {result}")
                    if self._is_verbose():
                        click.echo(f"  {'':18}  id: {row['execution_id']}")
            click.echo()

        return True

    # -------------------------------------------------------------------------
    # Deployer factory (identical to RunDeployCommand)
    # -------------------------------------------------------------------------

    def _create_deployer(self, stage: DeploymentStageModel) -> Optional[TerraformDeployer]:
        is_terraform = False

        if stage.provisioner and self._deployment_service is not None:
            workspace_service = self._deployment_service.get_workspace_service()
            if workspace_service:
                spec = workspace_service.model.spec  # type: ignore[union-attr]
                iac = next(
                    (p for p in (spec.provisioners or []) if p.name == stage.provisioner),
                    None,
                )
                if iac and iac.provisioner == ProvisionerType.TERRAFORM:
                    is_terraform = True

        if not is_terraform and stage.type in ("infrastructure", "terraform"):
            is_terraform = True

        if is_terraform:
            return TerraformDeployer(
                stage=stage,
                deployment_service=self._deployment_service,  # type: ignore[arg-type]
                configuration_service=self._configuration_service,  # type: ignore[arg-type]
                build_path=self._build_path,
                work_path=self._work_path,
                verbose=self._is_verbose(),
            )

        return None
