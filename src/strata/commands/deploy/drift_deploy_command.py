"""Command to detect infrastructure drift across deployment stages."""

from typing import List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.controllers.drift_controller import DriftController
from strata.models.deployment_model import DeploymentStageModel
from strata.models.drift_model import DriftSeverity
from strata.utils.drift_history import DriftHistoryStore


class DriftDeployCommand(BaseDeployCommand):
    """Detect configuration drift between infrastructure state and Terraform code.

    For each deployment stage, runs ``terraform plan -detailed-exitcode`` without
    saving a plan file and classifies any changes by severity (critical → info).
    History is persisted so you can see how long resources have been drifting.

    Exit codes:
      0  — no drift found, or drift found but below the --severity threshold
      3  — drift found at or above the --severity threshold (validation failure)
      1  — execution error (terraform error, auth failure, etc.)
    """

    OPERATION = "deploy_drift"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        severity: Optional[str] = None,
        baseline: bool = False,
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
        self._severity_threshold = DriftSeverity(severity or "info")
        self._baseline = baseline

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

            if not self._run_lifecycle_phase(
                "deploy_drift",
                context={"file": str(self._file_path)},
            ):
                if self._is_console_output():
                    click.echo("\n❌  Drift lifecycle hook failed")
                self._finalize(success=False)
                return False

            ok = self._run_drift_detection()

            if not self._after_execute():
                self._finalize(success=False)
                return False

            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_drift: {exc}")
            self.logger.exception("deploy_drift failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Core
    # -------------------------------------------------------------------------

    def _run_drift_detection(self) -> bool:
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
            thresh = self._severity_threshold.value
            click.echo(f"\n🔍  Checking drift for {len(stages)} stage(s) (threshold: {thresh})…\n")

        controller = DriftController()
        report = controller.detect_drift(
            stages=stages,
            deployment_service=self._deployment_service,  # type: ignore[arg-type]
            configuration_service=self._configuration_service,  # type: ignore[arg-type]
            build_path=self._build_path,
            work_path=self._work_path,
            verbose=self._is_verbose(),
            solution_controller=self._solution_controller,
        )

        # Propagate controller errors and messages
        self._errors.extend(controller.get_errors())
        self._messages.extend(controller.get_messages())

        # Render results
        if self._is_console_output():
            self._print_report(report)

        # Store for JSON output
        self._output_data = report.to_dict()

        # --baseline mode: acknowledge all detected entries, reset history, always exit 0
        if self._baseline:
            deployment_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
            history = DriftHistoryStore(self._work_path, deployment_name)
            history.load()
            history.reset_baseline()
            for entry in report.entries:
                history.acknowledge(entry.address, reason="baseline")
            history.save()

            if self._is_console_output():
                if report.has_drift:
                    click.echo(
                        f"\n  📌  Baseline set: {len(report.entries)} entry(ies) acknowledged "
                        "— they will be suppressed in future drift checks.\n"
                    )
                else:
                    click.echo("\n  ✅  Baseline set: no drift found — history reset.\n")
            return True

        # Determine success: drift above threshold → exit 3
        if report.above_threshold(self._severity_threshold):
            return False

        return True

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _print_report(self, report) -> None:  # type: ignore[no-untyped-def]
        from strata.models.drift_model import DriftSeverity

        if not report.has_drift:
            click.echo("  ✅  No drift detected — infrastructure matches configuration.\n")
            return

        severity_icons = {
            DriftSeverity.CRITICAL: "🔴",
            DriftSeverity.HIGH: "🟠",
            DriftSeverity.MEDIUM: "🟡",
            DriftSeverity.LOW: "🔵",
            DriftSeverity.INFO: "⚪",
        }
        action_icons = {
            "create": "+",
            "delete": "-",
            "update": "~",
            "replace": "±",
        }

        # Group by stage
        by_stage: dict = {}
        for entry in report.entries:
            by_stage.setdefault(entry.stage, []).append(entry)

        for stage_name, entries in by_stage.items():
            click.echo(f"  Stage: {stage_name}")
            click.echo(f"  {'ADDRESS':<52}  {'ACTION':<8}  {'SEVERITY':<10}  CHANGED ATTRIBUTES")
            click.echo(f"  {'-' * 52}  {'-' * 8}  {'-' * 10}  {'-' * 30}")

            for entry in sorted(entries, key=lambda e: DriftSeverity.ordered().index(e.severity)):
                icon = severity_icons.get(entry.severity, "  ")
                action_sym = action_icons.get(entry.action, "?")
                attrs_preview = ", ".join(entry.changed_attributes[:5])
                if len(entry.changed_attributes) > 5:
                    attrs_preview += f" (+{len(entry.changed_attributes) - 5} more)"
                # Truncate long addresses
                addr = entry.address
                if len(addr) > 52:
                    addr = addr[:49] + "..."
                click.echo(f"  {addr:<52}  [{action_sym}]{' ':<5}  {icon} {entry.severity.value:<8}  {attrs_preview}")
                if entry.consecutive_checks > 1:
                    click.echo(f"  {'':>54}  (drifting for {entry.consecutive_checks} consecutive check(s))")
            click.echo()

        s = report.summary
        click.echo(
            f"  Summary: {s.total()} change(s) — "
            f"🔴 {s.critical} critical  🟠 {s.high} high  "
            f"🟡 {s.medium} medium  🔵 {s.low} low  ⚪ {s.info} info\n"
        )
