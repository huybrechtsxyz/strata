"""Command to show the live status of a deployed environment."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.factory import DeployerFactory
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel


class StatusEnvCommand(BaseDeployCommand):
    """Show the live status of a deployed environment.

    Single deployment (-f): per-stage detail — resources, outputs, serial, cache.
    Multi deployment (--all / --path DIR): one-line summary per deployment found.
    Multi-deployment mode is always offline (reads build cache only).

    Per stage (single-deployment live mode), queries the remote backend and reports:
      - Resource count (from ``terraform show -json``)
      - Output count and keys
      - Last apply serial number
      - Cached output freshness (from ``.tf-outputs.json``)
      - Overall reachability (can we talk to the backend?)

    Non-terraform stages report limited info (provisioner type, reachability).
    """

    OPERATION = "env_status"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        offline: bool = False,
        path: Optional[str] = None,
        all_deployments: bool = False,
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
        self._path = path
        self._all = all_deployments

    def get_required_integrations(self) -> dict:
        # Multi-deployment and offline modes only read the build cache — no terraform call needed.
        if self._offline or self._all or self._path:
            return {}
        return {"terraform": "querying live infrastructure state"}

    def _before_execute(self) -> bool:
        # Multi-deployment mode needs no deployment file — skip the BaseDeployCommand
        # file-loading validation and let _execute() handle the scan directly.
        if self._all or self._path:
            return True
        return super()._before_execute()

    def _execute(self) -> bool:
        if self._all or self._path:
            return self._run_multi()
        return self._run_single()

    def _run_multi(self) -> bool:
        """Scan for deployment YAML files and show a one-line status per deployment."""
        scan_root = Path(self._path).resolve() if self._path else self._work_path
        if not scan_root.exists() or not scan_root.is_dir():
            self._errors.append(f"Path does not exist or is not a directory: {scan_root}")
            return False

        entries: List[Dict[str, Any]] = []
        for yaml_file in sorted(scan_root.rglob("*.yaml")):
            entry = self._extract_deployment_status(yaml_file)
            if entry is not None:
                entries.append(entry)

        if self._is_console_output():
            if not entries:
                click.echo(f"\n  (no deployment manifests found under {scan_root})\n")
            else:
                mode_label = f"--path {scan_root}" if self._path else "--all"
                click.echo(f"\n📊  Deployment Status — {len(entries)} deployment(s) [{mode_label}]\n")
                for entry in entries:
                    self._print_deployment_summary(entry)

        self._output_data = {
            "scan_path": str(scan_root),
            "deployments": entries,
        }
        return True

    def _extract_deployment_status(self, yaml_path: Path) -> Optional[Dict[str, Any]]:
        """Parse a YAML file and return a status summary if it is a deployment manifest."""
        try:
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        except Exception:
            return None

        if not isinstance(raw, dict):
            return None
        if raw.get("kind") != "deployment":
            return None

        meta = raw.get("meta") or {}
        spec = raw.get("spec") or {}
        stages = spec.get("stages") or []

        stage_summaries: List[Dict[str, Any]] = []
        for stage in stages:
            stage_name = stage.get("name") or ""
            provisioner = stage.get("provisioner") or "terraform"
            cache_info = self._read_output_cache_by_name(str(stage_name))
            stage_summaries.append(
                {
                    "name": str(stage_name),
                    "provisioner": str(provisioner),
                    "cached": cache_info is not None,
                    "cache": cache_info,
                }
            )

        cached_count = sum(1 for s in stage_summaries if s["cached"])
        return {
            "file": str(yaml_path.resolve()),
            "name": meta.get("name") or "",
            "stages": stage_summaries,
            "stage_count": len(stage_summaries),
            "cached_count": cached_count,
        }

    def _print_deployment_summary(self, entry: Dict[str, Any]) -> None:
        name = entry["name"] or entry["file"]
        stage_count = entry["stage_count"]
        cached_count = entry["cached_count"]
        stages = entry["stages"]

        if stage_count == 0:
            status_icon = "⬜"
        elif cached_count == stage_count:
            status_icon = "✅"
        elif cached_count > 0:
            status_icon = "⚠️ "
        else:
            status_icon = "⬜"

        click.echo(f"  {status_icon} {name}  ({cached_count}/{stage_count} stages cached)")

        for stage in stages:
            stage_icon = "✓" if stage["cached"] else "○"
            cache = stage.get("cache")
            cache_detail = ""
            if cache:
                refreshed = cache.get("refreshed_at", "unknown")
                out_count = cache.get("output_count", 0)
                cache_detail = f"  {refreshed}  {out_count} output(s)"
            click.echo(f"      {stage_icon} {stage['name']}{cache_detail}")

        click.echo()

    # ------------------------------------------------------------------
    # Single-deployment mode
    # ------------------------------------------------------------------

    def _run_single(self) -> bool:
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
            click.echo(f"\n📊  Environment status ({mode}) — {deployment_model.meta.name}")
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
        provisioner_type = self._resolve_provisioner_type(stage)
        if provisioner_type != "terraform":
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

    def _resolve_provisioner_type(self, stage: DeploymentStageModel) -> str:
        """Resolve the provisioner type string for a stage."""
        if not stage.provisioner or self._deployment_service is None:
            return "unknown"
        workspace_service = self._deployment_service.get_workspace_service()
        if not workspace_service or workspace_service.model is None:
            return "unknown"
        spec = workspace_service.model.spec
        provisioners = spec.provisioners or []
        match = next((p for p in provisioners if p.name == stage.provisioner), None)
        if not match:
            return "unknown"
        if match.provisioner == ProvisionerType.TERRAFORM:
            return "terraform"
        if match.provisioner == ProvisionerType.HELM:
            return "helm"
        if match.provisioner == ProvisionerType.COMPOSE:
            return "compose"
        if match.provisioner == ProvisionerType.ANSIBLE:
            return "ansible"
        return "unknown"

    # ------------------------------------------------------------------
    # Deployer factory
    # ------------------------------------------------------------------

    def _create_deployer(self, stage: DeploymentStageModel) -> Optional[TerraformDeployer]:
        """Create a TerraformDeployer for the given stage (terraform only)."""
        if self._deployment_service is None:
            return None
        return DeployerFactory.create(  # type: ignore[return-value]
            "terraform",
            stage=stage,
            deployment_service=self._deployment_service,
            configuration_service=self._configuration_service,  # type: ignore[arg-type]
            build_path=self._build_path,
            work_path=self._work_path,
            verbose=self._is_verbose(),
            solution_controller=self._solution_controller,
        )

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
