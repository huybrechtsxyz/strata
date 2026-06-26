"""Show Terraform outputs for a deployment — from cache, live backend, or stored artifacts."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel
from strata.utils.config import SOLUTION_DIR, SOLUTION_OUTPUTS_DIR

_CACHE_SUFFIX = ".tf-outputs.json"
_DEFAULT_OUTPUTS_PATH = f"{SOLUTION_DIR}/{SOLUTION_OUTPUTS_DIR}"


class OutputDeployCommand(BaseDeployCommand):
    """Show Terraform outputs for a deployment.

    Default (no flags)
        Reads ``build/<stage>.tf-outputs.json`` written after the last
        ``deploy run`` or ``deploy status``.  No network calls.

    ``--refresh``
        Re-runs ``terraform output -json`` against the remote backend and
        updates the cache file.

    ``--version VERSION``
        Show stored output artifacts for a specific version tag from the
        durable outputs directory.

    ``--all-versions``
        Show stored output artifacts for every version found in the outputs
        directory.

    ``--stage NAME``
        Limit to a single deployment stage.

    ``--key NAME``
        Print a single output key (useful for scripting).
    """

    OPERATION = "deploy_output"

    def __init__(
        self,
        file: Optional[str] = None,
        work_path: Optional[str] = None,
        stage: Optional[str] = None,
        key: Optional[str] = None,
        refresh: bool = False,
        version: Optional[str] = None,
        all_versions: bool = False,
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
        self._key = key
        self._refresh = refresh
        self._version = version
        self._all_versions = all_versions

    # -------------------------------------------------------------------------
    # Entry point
    # -------------------------------------------------------------------------

    def execute(self) -> bool:
        try:
            if not self._initialize():
                self._finalize(success=False)
                return False

            if not self._before_execute():
                self._finalize(success=False)
                return False

            ok = self._run()
            self._finalize(success=ok)
            return ok

        except Exception as exc:
            self._errors.append(f"Failed to execute deploy_output: {exc}")
            self.logger.exception("deploy_output failed")
            self._finalize(success=False)
            return False

    # -------------------------------------------------------------------------
    # Core logic
    # -------------------------------------------------------------------------

    def _run(self) -> bool:
        if self._deployment_service is None:
            self._errors.append("Deployment service not loaded")
            return False

        # Route to stored artifacts mode when --version or --all-versions is used
        if self._version or self._all_versions:
            return self._run_artifacts()

        spec = self._deployment_service.model.spec  # type: ignore[union-attr]
        all_stages: List[DeploymentStageModel] = spec.stages or []
        terraform_stages = [s for s in all_stages if self._is_terraform_stage(s)]

        if self._stage:
            terraform_stages = [s for s in terraform_stages if s.name == self._stage]
            if not terraform_stages:
                self._errors.append(
                    f"Stage '{self._stage}' not found or is not a terraform stage. "
                    f"Available terraform stages: {[s.name for s in all_stages if self._is_terraform_stage(s)]}"
                )
                return False

        if not terraform_stages:
            if self._is_console_output():
                click.echo("  (no terraform stages in this deployment)")
            self._output_data = {"file": str(self._file_path), "stages": {}}
            return True

        if self._is_console_output():
            mode = "fetching from backend" if self._refresh else "reading from cache"
            click.echo(f"\n  Terraform outputs ({mode})…\n")

        all_outputs: Dict[str, Any] = {}
        any_failed = False

        for stage in terraform_stages:
            ok, outputs, cached_at, msgs = self._get_stage_outputs(stage)
            self._messages.extend(msgs)

            # Apply --key filter for display and structured output
            filtered = {k: v for k, v in outputs.items() if not self._key or k == self._key}

            all_outputs[str(stage.name)] = {
                "outputs": filtered,
                "cached_at": cached_at,
                "error": None if ok else (msgs[-1] if msgs else "unknown error"),
            }
            if not ok:
                any_failed = True
            if self._is_console_output():
                self._print_stage(str(stage.name), ok, filtered, cached_at, msgs)

        self._output_data = {
            "file": str(self._file_path),
            "mode": "refresh" if self._refresh else "cache",
            "stages": all_outputs,
        }
        return not any_failed

    # -------------------------------------------------------------------------
    # Per-stage fetch
    # -------------------------------------------------------------------------

    def _get_stage_outputs(
        self,
        stage: DeploymentStageModel,
    ) -> Tuple[bool, Dict[str, Any], Optional[str], List[str]]:
        """Return (ok, outputs, cached_at, messages)."""
        if not self._refresh:
            return self._read_cache(stage)

        # Refresh: instantiate deployer, init, run terraform output
        deployer = self._create_deployer(stage)
        if deployer is None:
            return False, {}, None, [f"Stage '{stage.name}': could not create deployer."]

        for validate_fn in (deployer.validate_workspace, deployer.validate_environment):
            ok, msgs = validate_fn()
            if not ok:
                return False, {}, None, msgs

        ok, msgs = deployer.setup()
        if not ok:
            return False, {}, None, msgs

        # output() writes the cache as a side effect
        ok, outputs, msgs = deployer.output()
        return ok, outputs, None, msgs

    def _read_cache(
        self,
        stage: DeploymentStageModel,
    ) -> Tuple[bool, Dict[str, Any], Optional[str], List[str]]:
        """Read build/<stage>.tf-outputs.json without touching the backend."""
        cache_file: Path = self._build_path / f"{stage.name}{_CACHE_SUFFIX}"
        if not cache_file.exists():
            return (
                False,
                {},
                None,
                [
                    f"No cached outputs for stage '{stage.name}' ({cache_file.name}). "
                    "Run 'strata deploy run' first, or use --refresh to fetch from backend."
                ],
            )
        try:
            with open(cache_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return True, data.get("outputs", {}), data.get("refreshed_at"), []
        except (OSError, ValueError) as exc:
            return False, {}, None, [f"Failed to read cache {cache_file.name}: {exc}"]

    # -------------------------------------------------------------------------
    # Console rendering
    # -------------------------------------------------------------------------

    def _print_stage(
        self,
        stage_name: str,
        ok: bool,
        outputs: Dict[str, Any],
        cached_at: Optional[str],
        msgs: List[str],
    ) -> None:
        icon = "✅" if ok else "❌"
        ts = f"  (cached {cached_at})" if cached_at and not self._refresh else ""
        click.echo(f"  {icon}  Stage: {stage_name}{ts}")
        if not ok:
            for m in msgs:
                click.echo(f"       ⚠  {m}")
        elif outputs:
            for k, v in outputs.items():
                click.echo(f"       • {k}: {v}")
        else:
            label = f"key '{self._key}' not found" if self._key else "no outputs defined"
            click.echo(f"       ({label})")
        click.echo()

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------

    def _is_terraform_stage(self, stage: DeploymentStageModel) -> bool:
        """Return True when the stage resolves to a terraform provisioner."""
        if not stage.provisioner or self._deployment_service is None:
            return False
        workspace_service = self._deployment_service.get_workspace_service()
        if not workspace_service:
            return False
        spec = workspace_service.model.spec  # type: ignore[union-attr]
        provisioners = spec.provisioners or []
        iac = next((p for p in provisioners if p.name == stage.provisioner), None)
        return iac is not None and iac.provisioner == ProvisionerType.TERRAFORM

    def _create_deployer(self, stage: DeploymentStageModel) -> Optional[TerraformDeployer]:
        """Instantiate TerraformDeployer for *stage*, or None if resolution fails."""
        if not stage.provisioner or self._deployment_service is None:
            self._errors.append(f"Stage '{stage.name}': missing provisioner reference.")
            return None

        workspace_service = self._deployment_service.get_workspace_service()
        if not workspace_service:
            self._errors.append(f"Stage '{stage.name}': workspace service not loaded.")
            return None

        spec = workspace_service.model.spec  # type: ignore[union-attr]
        provisioners = spec.provisioners or []
        iac = next((p for p in provisioners if p.name == stage.provisioner), None)
        if iac is None or iac.provisioner != ProvisionerType.TERRAFORM:
            self._errors.append(f"Stage '{stage.name}': provisioner '{stage.provisioner}' is not terraform.")
            return None

        return TerraformDeployer(
            stage=stage,
            deployment_service=self._deployment_service,  # type: ignore[arg-type]
            configuration_service=self._configuration_service,  # type: ignore[arg-type]
            build_path=self._build_path,
            work_path=self._work_path,
            verbose=self._is_verbose(),
            solution_controller=self._solution_controller,
        )

    # -------------------------------------------------------------------------
    # Stored artifacts mode (--version / --all-versions)
    # -------------------------------------------------------------------------

    def _run_artifacts(self) -> bool:
        """Show stored output artifacts written by ``deploy run``."""
        deploy_meta = self._deployment_service.model.meta  # type: ignore[union-attr]
        deployment_name = str(deploy_meta.name)

        outputs_config = self._get_outputs_config()
        if outputs_config is not None and not outputs_config.enabled:
            if self._is_console_output():
                click.echo("  Outputs artifact storage is disabled for this deployment.")
            self._output_data = {"deployment": deployment_name, "artifacts": []}
            return True

        base_path = outputs_config.path if outputs_config else _DEFAULT_OUTPUTS_PATH
        outputs_dir = self._work_path / base_path / deployment_name

        if not outputs_dir.exists():
            if self._is_console_output():
                click.echo(f"\n  No stored outputs found for deployment '{deployment_name}'.")
                click.echo(f"  Expected location: {outputs_dir}")
                click.echo("  Run 'strata deploy run' with outputs configured to generate them.\n")
            self._output_data = {"deployment": deployment_name, "artifacts": []}
            return True

        versions = self._resolve_artifact_versions(outputs_dir, deploy_meta)

        artifacts: List[Dict[str, Any]] = []
        for ver in versions:
            ver_dir = outputs_dir / ver
            if not ver_dir.is_dir():
                continue
            for artifact_file in sorted(ver_dir.glob("*.json")):
                stage_name = artifact_file.stem
                if self._stage and stage_name != self._stage:
                    continue
                entry = self._read_artifact(artifact_file)
                if entry is not None:
                    artifacts.append(entry)

        if self._is_console_output():
            self._render_artifacts_console(deployment_name, artifacts)

        self._output_data = {
            "deployment": deployment_name,
            "artifacts": artifacts,
        }
        return True

    def _resolve_artifact_versions(self, outputs_dir: Path, deploy_meta: Any) -> List[str]:
        """Return the list of version strings to display."""
        if self._all_versions:
            return sorted(
                [v.name for v in outputs_dir.iterdir() if v.is_dir()],
                reverse=True,
            )
        if self._version:
            return [self._version]
        labels = deploy_meta.labels or {}
        return [str(labels.get("version", "unknown"))]

    def _read_artifact(self, path: Path) -> Optional[Dict[str, Any]]:
        """Read a single artifact JSON file, applying --key filter if set."""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data: Dict[str, Any] = json.load(fh)
            if self._key:
                outputs = data.get("outputs", {})
                data = dict(data)
                data["outputs"] = {self._key: outputs[self._key]} if self._key in outputs else {}
                data["key_filter"] = self._key
            return data
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self.logger.warning("Failed to read outputs artifact", path=str(path), error=str(exc))
            self._messages.append(f"  ⚠  Skipped {path.name}: {exc}")
            return None

    def _render_artifacts_console(self, deployment_name: str, artifacts: List[Dict[str, Any]]) -> None:
        """Render stored artifacts to the console, grouped by version."""
        click.echo(f"\n  Stored outputs — deployment '{deployment_name}'\n")

        if not artifacts:
            msg = "  No stored outputs found"
            if self._stage:
                msg += f" for stage '{self._stage}'"
            if self._version:
                msg += f" at version '{self._version}'"
            click.echo(msg + ".\n")
            return

        by_version: Dict[str, List[Dict[str, Any]]] = {}
        for entry in artifacts:
            ver = entry.get("version", "unknown")
            by_version.setdefault(ver, []).append(entry)

        for ver, entries in by_version.items():
            click.echo(f"  Version: {ver}")
            for entry in entries:
                stage = entry.get("stage", "?")
                written_at = entry.get("written_at", "")
                outputs = entry.get("outputs", {})
                ts = f"  (written {written_at})" if written_at else ""
                click.echo(f"    Stage: {stage}{ts}")
                if outputs:
                    for k, v in outputs.items():
                        click.echo(f"      • {k}: {v}")
                else:
                    label = f"key '{self._key}' not found" if self._key else "no outputs stored"
                    click.echo(f"      ({label})")
                click.echo()
