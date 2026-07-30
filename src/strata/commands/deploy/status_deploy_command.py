"""Command to report the live infrastructure status of a single deployment."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.factory import DeployerFactory
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.deployment_model import DeploymentStageModel


class StatusDeployCommand(BaseDeployCommand):
    """Show the live status of a single deployment.

    Per stage, queries the remote backend and reports:
      - Resource count (from ``terraform show -json``)
      - Output count and keys
      - Last apply serial number
      - Cached output freshness (from ``.tf-outputs.json``)
      - Overall reachability (can we talk to the backend?)

    Non-terraform stages report limited info (provisioner type, reachability).

    ``--offline`` reports cached data only — no backend calls.

    For plan-diffing (what would change) use ``strata deploy plan``. For
    fleet-wide, multi-deployment scanning use ``strata rollout status``.
    """

    OPERATION = "deploy_status"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        offline: bool = False,
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
        self._offline = offline

    def get_required_integrations(self) -> Dict[str, str]:
        # Offline mode only reads the build cache — no terraform call needed.
        if self._offline:
            return {}
        return {"terraform": "querying live infrastructure state"}

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
            mode = "offline (cached)" if self._offline else "live"
            click.echo(f"\n📊  Deployment status ({mode}) — {deployment_model.meta.name}")
            click.echo(f"    {len(stages)} stage(s)\n")

        stage_results: List[Dict[str, Any]] = []

        for stage in stages:
            result = self._query_stage_state(stage)
            stage_results.append(result)
            if self._is_console_output():
                self._print_stage_state(result)

        self._output_data = {
            "file": str(self._file_path),
            "deployment": str(deployment_model.meta.name),
            "mode": "offline" if self._offline else "live",
            "stages": stage_results,
        }

        return True  # status query is best-effort; unreachable stages don't fail the command

    # ------------------------------------------------------------------
    # Per-stage state query
    # ------------------------------------------------------------------

    def _query_stage_state(self, stage: DeploymentStageModel) -> Dict[str, Any]:
        """Query the state for a single stage."""
        result: Dict[str, Any] = {
            "name": str(stage.name),
            "provisioner": stage.provisioner or "terraform",
            "scope": stage.scope or None,
            "reachable": False,
            "resources": None,
            "outputs": None,
            "serial": None,
            "cache": None,
        }

        # Check cached output file
        cache_info = self._read_output_cache(stage)
        if cache_info:
            result["cache"] = cache_info

        if self._offline:
            # Offline mode: only report cached data
            result["reachable"] = cache_info is not None
            if cache_info:
                result["outputs"] = cache_info.get("output_count")
            return result

        # Resolve provisioner type
        resolved_type, _ = DeployerFactory.resolve_type(stage, self._deployment_service)
        if resolved_type != "terraform":
            # Non-terraform: limited info (reachability via cache)
            result["reachable"] = cache_info is not None
            return result

        # Terraform: query live state
        deployer = self._create_deployer(stage)
        if deployer is None:
            return result

        # Validate workspace + environment (needed for init)
        for validate_fn in (deployer.validate_workspace, deployer.validate_environment):
            ok, msgs = validate_fn()
            if not ok:
                self._messages.extend(msgs)
                return result

        # Setup (terraform init) to reach backend
        ok, msgs = deployer.setup()
        if not ok:
            self._messages.extend(msgs)
            return result

        # Query state via terraform show -json (no plan file = current state)
        state_data = self._fetch_terraform_state(deployer)
        if state_data:
            result["reachable"] = True
            resources = state_data.get("values", {}).get("root_module", {}).get("resources", [])
            child_modules = state_data.get("values", {}).get("root_module", {}).get("child_modules", [])
            for child in child_modules:
                resources.extend(child.get("resources", []))
            result["resources"] = len(resources)
            result["serial"] = state_data.get("serial")

            ok, outputs, _ = deployer.output()
            if ok:
                result["outputs"] = len(outputs)
                result["output_keys"] = list(outputs.keys())

        return result

    def _fetch_terraform_state(self, deployer: TerraformDeployer) -> Optional[Dict[str, Any]]:
        """Run ``terraform show -json`` (current state) and return parsed data."""
        try:
            assert deployer._working_dir is not None
            assert deployer._tf is not None
            tf_result = deployer._tf.show(
                str(deployer._working_dir),
                plan_file=None,
                json_format=True,
            )
            if tf_result.returncode != 0:
                return None
            return json.loads(tf_result.stdout or "{}")
        except (RuntimeError, ValueError, json.JSONDecodeError):
            return None

    def _read_output_cache(self, stage: DeploymentStageModel) -> Optional[Dict[str, Any]]:
        """Read the cached ``.tf-outputs.json`` for a stage model."""
        return self._read_output_cache_by_name(str(stage.name))

    def _read_output_cache_by_name(self, stage_name: str) -> Optional[Dict[str, Any]]:
        """Read the cached ``.tf-outputs.json`` for a stage given by name."""
        cache_file = self._build_path / f"{stage_name}.tf-outputs.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, encoding="utf-8") as fh:
                data = json.load(fh)
            refreshed = data.get("refreshed_at")
            outputs = data.get("outputs", {})
            return {
                "refreshed_at": refreshed,
                "output_count": len(outputs),
                "output_keys": list(outputs.keys()),
            }
        except (OSError, json.JSONDecodeError):
            return None

    # ------------------------------------------------------------------
    # Console output
    # ------------------------------------------------------------------

    def _print_stage_state(self, result: Dict[str, Any]) -> None:
        name = result["name"]
        provisioner = result["provisioner"]
        reachable = result["reachable"]

        icon = "✅" if reachable else "⚠️ "
        click.echo(f"  {icon} {name}  ({provisioner})")

        if result.get("scope"):
            click.echo(f"       Scope: {result['scope']}")

        if result.get("resources") is not None:
            click.echo(f"       Resources: {result['resources']}")

        if result.get("outputs") is not None:
            click.echo(f"       Outputs: {result['outputs']}")
            if self._is_verbose() and result.get("output_keys"):
                for key in result["output_keys"]:
                    click.echo(f"         • {key}")

        if result.get("serial") is not None:
            click.echo(f"       State serial: {result['serial']}")

        cache = result.get("cache")
        if cache:
            refreshed = cache.get("refreshed_at", "unknown")
            click.echo(f"       Cache: refreshed {refreshed}")
        elif not reachable:
            click.echo("       Cache: none (no prior deploy output cached)")

        click.echo()
