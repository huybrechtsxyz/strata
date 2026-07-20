"""Command to detect drift between desired and actual infrastructure state."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.factory import DeployerFactory
from strata.models.deployment_model import DeploymentStageModel


class DriftEnvCommand(BaseDeployCommand):
    """Detect drift between desired configuration and live infrastructure.

    Runs ``terraform plan`` per stage to determine what would change if you
    applied now.  Reports per-stage:
      - Whether drift exists (changes detected or not)
      - Counts of create / update / delete / replace actions
      - Detailed resource addresses (in verbose mode)

    Non-terraform stages are skipped with a notice.
    """

    OPERATION = "env_drift"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
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

    def get_required_integrations(self) -> Dict[str, str]:
        return {"terraform": "running terraform plan for drift detection"}

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    def _execute(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        deployment_model = self._deployment_service.model
        if deployment_model is None:
            self._errors.append("Deployment model not loaded")
            return False

        spec = deployment_model.spec
        all_stages: List[DeploymentStageModel] = spec.stages or []

        stages = [s for s in all_stages if s.name == self._stage] if self._stage else all_stages
        if self._stage and not stages:
            self._errors.append(f"Stage '{self._stage}' not found. Available: {[str(s.name) for s in all_stages]}")
            return False

        if self._is_console_output():
            click.echo(f"\n🔍  Drift detection — {deployment_model.meta.name}")
            click.echo(f"    {len(stages)} stage(s)\n")

        stage_results: List[Dict[str, Any]] = []
        any_error = False

        for stage in stages:
            result = self._detect_stage_drift(stage)
            stage_results.append(result)
            if result.get("error"):
                any_error = True
            if self._is_console_output():
                self._print_stage_drift(result)

        # Summary
        total_drifted = sum(1 for r in stage_results if r.get("has_drift") is True)
        total_clean = sum(1 for r in stage_results if r.get("has_drift") is False)
        total_error = sum(1 for r in stage_results if r.get("error"))

        if self._is_console_output():
            click.echo("━" * 50)
            parts = []
            if total_clean:
                parts.append(f"✅ {total_clean} clean")
            if total_drifted:
                parts.append(f"⚠️  {total_drifted} drifted")
            if total_error:
                parts.append(f"❌ {total_error} error")
            click.echo(f"    Summary: {' │ '.join(parts)}\n")

        self._output_data = {
            "file": str(self._file_path),
            "deployment": str(deployment_model.meta.name),
            "summary": {
                "clean": total_clean,
                "drifted": total_drifted,
                "error": total_error,
            },
            "stages": stage_results,
        }

        return not any_error

    # ------------------------------------------------------------------
    # Per-stage drift detection
    # ------------------------------------------------------------------

    def _detect_stage_drift(self, stage: DeploymentStageModel) -> Dict[str, Any]:
        """Run terraform plan on a single stage and parse change summary."""
        result: Dict[str, Any] = {
            "name": str(stage.name),
            "provisioner": stage.provisioner or "terraform",
            "has_drift": None,
            "changes": None,
            "error": None,
        }

        # Only terraform supports plan-based drift detection
        resolved_type, _ = DeployerFactory.resolve_type(stage, self._deployment_service)
        if resolved_type != "terraform":
            result["error"] = f"Drift detection not supported for '{resolved_type or 'unknown'}' provisioner"
            return result

        deployer = self._create_deployer(stage)
        if deployer is None:
            result["error"] = "Could not create deployer"
            return result

        # Validate workspace + environment
        for validate_fn in (
            deployer.validate_workspace,
            deployer.validate_environment,
        ):
            ok, msgs = validate_fn()
            if not ok:
                result["error"] = "; ".join(msgs)
                self._messages.extend(msgs)
                return result

        # Setup (terraform init)
        ok, msgs = deployer.setup()
        if not ok:
            result["error"] = "; ".join(msgs)
            self._messages.extend(msgs)
            return result

        # Run plan (terraform plan -detailed-exitcode)
        ok, msgs = deployer.plan()
        self._messages.extend(msgs)
        if not ok:
            result["error"] = "; ".join(msgs)
            return result

        # Check if plan detected changes
        if deployer._plan_has_changes is False:
            result["has_drift"] = False
            result["changes"] = {"create": 0, "update": 0, "delete": 0, "replace": 0}
            return result

        # Changes detected — parse the plan for details
        result["has_drift"] = True
        ok, plan_data, msgs = deployer.show_plan()
        self._messages.extend(msgs)
        if not ok:
            # Plan ran but we can't parse it — still report drift exists
            result["changes"] = {"create": 0, "update": 0, "delete": 0, "replace": 0}
            return result

        # Count resource changes by action
        resource_changes = plan_data.get("resource_changes", [])
        counts: Dict[str, int] = {"create": 0, "update": 0, "delete": 0, "replace": 0}
        resources: List[Dict[str, Any]] = []

        for rc in resource_changes:
            change = rc.get("change", {})
            actions = change.get("actions", [])
            addr = rc.get("address", "?")

            # Map terraform actions to our categories
            if actions == ["no-op"] or actions == ["read"]:
                continue
            if "create" in actions and "delete" in actions:
                counts["replace"] += 1
                resources.append({"address": addr, "action": "replace"})
            elif "create" in actions:
                counts["create"] += 1
                resources.append({"address": addr, "action": "create"})
            elif "update" in actions:
                counts["update"] += 1
                resources.append({"address": addr, "action": "update"})
            elif "delete" in actions:
                counts["delete"] += 1
                resources.append({"address": addr, "action": "delete"})

        result["changes"] = counts
        result["resources"] = resources
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_stage_drift(self, result: Dict[str, Any]) -> None:
        name = result["name"]
        has_drift = result.get("has_drift")
        error = result.get("error")

        if error:
            click.echo(f"  ❌ {name}  — {error}")
            click.echo()
            return

        if has_drift is False:
            click.echo(f"  ✅ {name}  — no drift")
            click.echo()
            return

        # Drift detected
        changes = result.get("changes", {})
        parts = []
        if changes.get("create"):
            parts.append(f"+{changes['create']} create")
        if changes.get("update"):
            parts.append(f"~{changes['update']} update")
        if changes.get("delete"):
            parts.append(f"-{changes['delete']} delete")
        if changes.get("replace"):
            parts.append(f"±{changes['replace']} replace")

        summary = ", ".join(parts) if parts else "changes detected"
        click.echo(f"  ⚠️  {name}  — drift detected: {summary}")

        if self._is_verbose() and result.get("resources"):
            for res in result["resources"]:
                action_icon = {
                    "create": "+",
                    "update": "~",
                    "delete": "-",
                    "replace": "±",
                }.get(res["action"], "?")
                click.echo(f"       {action_icon} {res['address']}")

        click.echo()
