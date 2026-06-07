"""Show Terraform outputs for a deployment — from cache or live backend."""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import click

from strata.commands.deploy.base_deploy_command import BaseDeployCommand
from strata.deployers.terraform_deployer import TerraformDeployer
from strata.models.common_models import ProvisionerType
from strata.models.deployment_model import DeploymentStageModel

_CACHE_SUFFIX = ".tf-outputs.json"


class OutputDeployCommand(BaseDeployCommand):
    """Show Terraform outputs for a deployment.

    Default (no flags)
        Reads ``build/<stage>.tf-outputs.json`` written after the last
        ``deploy run`` or ``deploy status``.  No network calls.

    ``--refresh``
        Re-runs ``terraform output -json`` against the remote backend and
        updates the cache file.

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
