"""Command that shows what ``strata build run`` would write, then runs terraform plan.

Two-layer output
----------------
1. **Artifact diff** — which ``.tfvars.json`` / ``platform.json`` files would
   change vs. what is already on disk in ``.strata/build/<deployment>/``.
   Computed by building into a temp directory and comparing the results.
   Always available (no Terraform required).

2. **Terraform plan** — resource-level add / change / destroy per stage.
   Runs ``terraform init → validate → plan`` against the freshly-built temp
   artifacts.  Skipped when ``--artifacts-only`` is given or when the Terraform
   binary is not available.
"""

import difflib
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from strata.builders.platform_builder import PlatformBuilder
from strata.builders.terraform_builder import TerraformBuilder
from strata.commands.builders.base_build_command import BaseBuildCommand
from strata.controllers.value_controller import ResolvedValues, ValueController
from strata.deployers.base_deployer import STEP_CHECK, STEP_PLAN, STEP_SETUP
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.deployment_model import DeploymentStageModel


class PlanBuildCommand(BaseBuildCommand):
    """Show the build plan: artifact diff + per-stage terraform plan."""

    OPERATION = "build_plan"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        artifacts_only: bool = False,
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
        self._artifacts_only = artifacts_only

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

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

            success = self._run_plan()
            self._finalize(success=success)
            return success

        except Exception as exc:
            self._errors.append(f"Failed to execute build_plan: {exc}")
            self.logger.exception("build_plan failed")
            self._finalize(success=False)
            return False

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _run_plan(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deployment_name = str(self._deployment_service.model.meta.name)  # type: ignore[union-attr]
        real_build_path = self._deployment_service.get_build_path(self._build_path)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_build_path = Path(tmp)

            # Layer 1: build artifacts into temp dir
            if not self._build_to_temp(tmp_build_path):
                return False

            tmp_deployment_path = self._deployment_service.get_build_path(tmp_build_path)

            # Compute artifact diff (temp vs current on-disk build)
            diff_rows = self._compute_artifact_diff(tmp_deployment_path, real_build_path)

            # Layer 2: terraform plan per stage (from temp build)
            plan_results: List[Dict[str, Any]] = []
            if not self._artifacts_only:
                resolved = self._resolve_values()
                plan_results = self._run_terraform_plan(tmp_build_path, resolved)

        self._output_data = {
            "file": str(self._file_path),
            "deployment": deployment_name,
            "artifact_diff": diff_rows,
            "terraform_plan": plan_results,
        }

        if self._is_console_output():
            self._print_console(deployment_name, diff_rows, plan_results)

        return True

    # ------------------------------------------------------------------
    # Layer 1: build artifacts into temp dir
    # ------------------------------------------------------------------

    def _build_to_temp(self, tmp_build_path: Path) -> bool:
        """Run platform + terraform builders into *tmp_build_path* (no dry-run)."""
        if self._deployment_service is None:
            return False

        # --- Platform builder ---
        pb = PlatformBuilder(
            verbose=self._is_verbose(),
            configuration_service=self._configuration_service,
        )
        ok = pb.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=tmp_build_path,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(pb.get_messages())
        if not ok:
            self._errors.extend(pb.get_errors())
            return False

        ok = pb.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=tmp_build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(pb.get_messages())
        if not ok:
            self._errors.extend(pb.get_errors())
            return False

        platform_model = pb._last_platform_model

        ok = pb.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=tmp_build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(pb.get_messages())
        if not ok:
            self._errors.extend(pb.get_errors())
            return False

        # --- Terraform builder ---
        tb = TerraformBuilder(verbose=self._is_verbose())

        ok = tb.before_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=tmp_build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(tb.get_messages())
        if not ok:
            self._errors.extend(tb.get_errors())
            return False

        ok = tb.build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=tmp_build_path,
            dry_run=False,
            platform_model=platform_model,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(tb.get_messages())
        if not ok:
            self._errors.extend(tb.get_errors())
            return False

        ok = tb.after_build(
            deployment_service=self._deployment_service,
            work_path=self._work_path,
            build_path=tmp_build_path,
            dry_run=False,
            solution_controller=self._solution_controller,
        )
        self._messages.extend(tb.get_messages())
        if not ok:
            self._errors.extend(tb.get_errors())
            return False

        return True

    # ------------------------------------------------------------------
    # Layer 1: artifact diff
    # ------------------------------------------------------------------

    def _compute_artifact_diff(self, tmp_dir: Path, real_dir: Path) -> List[Dict[str, Any]]:
        """Compare every file in *tmp_dir* against the same path in *real_dir*."""
        rows: List[Dict[str, Any]] = []
        if not tmp_dir.exists():
            return rows

        for tmp_file in sorted(tmp_dir.rglob("*")):
            if not tmp_file.is_file():
                continue
            rel = tmp_file.relative_to(tmp_dir)
            real_file = real_dir / rel

            if not real_file.exists():
                rows.append({"status": "new", "path": str(rel).replace("\\", "/"), "lines_changed": None})
            else:
                tmp_text = tmp_file.read_text(encoding="utf-8", errors="replace")
                real_text = real_file.read_text(encoding="utf-8", errors="replace")
                if tmp_text == real_text:
                    rows.append({"status": "unchanged", "path": str(rel).replace("\\", "/"), "lines_changed": 0})
                else:
                    diff = list(difflib.unified_diff(real_text.splitlines(), tmp_text.splitlines()))
                    # Subtract the 3 header lines (+++ --- @@)
                    changed = max(
                        sum(1 for ln in diff if ln.startswith(("+", "-")) and not ln.startswith(("+++", "---"))), 1
                    )
                    rows.append({"status": "changed", "path": str(rel).replace("\\", "/"), "lines_changed": changed})

        return rows

    # ------------------------------------------------------------------
    # Layer 2: terraform plan
    # ------------------------------------------------------------------

    def _resolve_values(self) -> Optional[ResolvedValues]:
        if self._deployment_service is None:
            return None
        controller = ValueController()
        _, resolved, errors = controller.resolve_values(self._deployment_service, strict=False)
        for err in errors:
            self.logger.warning("Value resolution warning: %s", err)
        return resolved

    def _run_terraform_plan(
        self,
        tmp_build_path: Path,
        resolved: Optional[ResolvedValues],
    ) -> List[Dict[str, Any]]:
        if self._deployment_service is None:
            return []

        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []

        if self._stage:
            stages = [s for s in all_stages if s.name == self._stage]
            if not stages:
                self._errors.append(f"Stage '{self._stage}' not found. Available: {[s.name for s in all_stages]}")
                return []
        else:
            stages = all_stages

        return [self._plan_stage(stage, tmp_build_path, resolved) for stage in stages]

    def _plan_stage(
        self,
        stage: DeploymentStageModel,
        tmp_build_path: Path,
        resolved: Optional[ResolvedValues],
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "stage": stage.name,
            "ok": False,
            "messages": [],
            "error": None,
        }

        if self._configuration_service is None:
            result["error"] = "Configuration service not loaded"
            return result

        deployer = TerraformDeployer(
            stage=stage,
            deployment_service=self._deployment_service,  # type: ignore[arg-type]
            configuration_service=self._configuration_service,
            build_path=tmp_build_path,
            work_path=self._work_path,
            verbose=True,  # always capture full terraform output
            force=False,
            resolved_values=resolved,
            solution_controller=self._solution_controller,
        )

        for label, validate_fn in (
            ("workspace", deployer.validate_workspace),
            ("environment", deployer.validate_environment),
        ):
            ok, msgs = validate_fn()
            result["messages"].extend(msgs)
            if not ok:
                result["error"] = f"Terraform {label} validation failed"
                return result

        for step_name, step_fn in (
            (STEP_SETUP, deployer.setup),
            (STEP_CHECK, deployer.check),
            (STEP_PLAN, deployer.plan),
        ):
            ok, msgs = step_fn()
            result["messages"].extend(msgs)
            if not ok:
                result["error"] = f"terraform {step_name} failed"
                return result

        result["ok"] = True
        return result

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    _SEP = "─" * 60

    def _print_console(
        self,
        deployment_name: str,
        diff_rows: List[Dict[str, Any]],
        plan_results: List[Dict[str, Any]],
    ) -> None:
        click.echo(f"\n📋  Build Plan — {deployment_name}")
        click.echo(f"  {self._SEP}")

        self._print_artifact_diff(diff_rows)

        for pr in plan_results:
            self._print_terraform_plan(pr)

        self._print_summary(diff_rows, plan_results)

    def _print_artifact_diff(self, rows: List[Dict[str, Any]]) -> None:
        click.echo("\n  Artifact changes:")
        click.echo(f"  {self._SEP}")

        if not rows:
            click.echo("  (no build artifacts produced)")
            return

        col = max((len(r["path"]) for r in rows), default=10)
        for row in rows:
            if row["status"] == "new":
                marker, note = "+", "new file"
            elif row["status"] == "changed":
                n = row["lines_changed"]
                marker, note = "~", f"{n} line(s) changed"
            else:
                marker, note = "=", "no change"
            click.echo(f"  {marker}  {row['path']:<{col}}  {note}")

    def _print_terraform_plan(self, pr: Dict[str, Any]) -> None:
        click.echo(f"\n  Terraform plan  [stage: {pr['stage']}]")
        click.echo(f"  {self._SEP}")
        for msg in pr["messages"]:
            for line in msg.splitlines():
                click.echo(f"  {line}")
        if pr["error"]:
            click.echo(f"  ❌  {pr['error']}")
        elif pr["ok"]:
            click.echo("  ✅  Plan complete")

    def _print_summary(self, diff_rows: List[Dict[str, Any]], plan_results: List[Dict[str, Any]]) -> None:
        click.echo(f"\n  {self._SEP}")
        new_c = sum(1 for r in diff_rows if r["status"] == "new")
        chg_c = sum(1 for r in diff_rows if r["status"] == "changed")
        unc_c = sum(1 for r in diff_rows if r["status"] == "unchanged")
        parts = []
        if new_c:
            parts.append(f"{new_c} new")
        if chg_c:
            parts.append(f"{chg_c} changed")
        if unc_c:
            parts.append(f"{unc_c} unchanged")
        click.echo(f"  Artifacts: {', '.join(parts) if parts else 'none'}")
        if plan_results:
            tf_ok = sum(1 for p in plan_results if p["ok"])
            tf_fail = len(plan_results) - tf_ok
            click.echo(f"  Terraform: {tf_ok} stage(s) planned" + (f", {tf_fail} failed" if tf_fail else ""))
        click.echo("")
