"""Terraform deployer — step-based provisioner style.

Supported steps (in execution order):
  setup    — terraform init
  check    — terraform validate
  plan     — terraform plan  -out=<stage>.tfplan
  apply    — terraform apply <stage>.tfplan
  destroy  — terraform destroy  (requires force=True for -auto-approve)
  output   — terraform output -json → {name: value} dict

Typical caller sequences:
  dry-run  : setup → check → plan
  deploy   : setup → check → plan → apply
  destroy  : setup → destroy  (force=True required)
  output   : output

``line_callback`` parameter (all step methods):
  Optional ``Callable[[str, str], None]`` — called for each subprocess output
  line as it arrives.  First arg is the stream name (``"stdout"`` / ``"stderr"``),
  second is the raw text line.  Use this to stream live output to the caller
  (e.g. NDJSON events or verbose console output).
"""

import json
from contextlib import nullcontext
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from strata.controllers.value_controller import ResolvedValues, inject_tf_vars
from strata.deployers.base_deployer import (
    STEP_APPLY,
    STEP_CHECK,
    STEP_DESTROY,
    STEP_OUTPUT,
    STEP_PLAN,
    STEP_PLAN_DESTROY,
    STEP_SETUP,
    STEP_SHOW_PLAN,
    BaseDeployer,
)
from strata.integrations.terraform import TerraformIntegration
from strata.models.deployment_model import DeploymentStageModel
from strata.models.workspace_model import WorkspaceIacModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService


class TerraformDeployer(BaseDeployer):
    """Runs a deployment stage using Terraform (init → validate → plan → apply).

    Context is passed once to the constructor; step methods carry no arguments.
    Call validate_workspace() then validate_environment() before running steps.
    """

    def __init__(
        self,
        stage: DeploymentStageModel,
        deployment_service: DeploymentService,
        configuration_service: ConfigurationService,
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional[ResolvedValues] = None,
        solution_controller=None,
    ) -> None:
        super().__init__(
            stage=stage,
            deployment_service=deployment_service,
            configuration_service=configuration_service,
            build_path=build_path,
            work_path=work_path,
            verbose=verbose,
            force=force,
            solution_controller=solution_controller,
        )
        self.resolved_values = resolved_values
        self._iac_model: Optional[WorkspaceIacModel] = None
        self._working_dir: Optional[Path] = None
        self._plan_file: Optional[Path] = None
        self._tf: Optional[TerraformIntegration] = None
        # Set by plan(); None = plan not yet run, False = no changes, True = changes present
        self._plan_has_changes: Optional[bool] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "terraform"

    def get_supported_steps(self) -> List[str]:
        return [
            STEP_SETUP,
            STEP_CHECK,
            STEP_PLAN,
            STEP_APPLY,
            STEP_DESTROY,
            STEP_PLAN_DESTROY,
            STEP_SHOW_PLAN,
            STEP_OUTPUT,
        ]

    # ------------------------------------------------------------------
    # Validation (pre-step guards)
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the terraform working directory and .tf files exist."""
        messages: List[str] = []

        workspace_service = self.deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        self._iac_model = self._resolve_iac_model(self.stage, workspace_service)
        if not self._iac_model:
            messages.append(
                f"Stage '{self.stage.name}': cannot resolve a terraform provisioner. "
                "Set stage.provisioner (by name) or stage.topology with a workspace "
                "topology that has provisioner='terraform'."
            )
            return False, messages

        if self.solution_controller is not None:
            self._working_dir = self.solution_controller.get_provisioner_path(
                self.deployment_service, self.build_path, self._iac_model
            )
        else:
            self._working_dir = self._get_working_dir(self.deployment_service, self.build_path, self._iac_model)
        self._plan_file = self._working_dir / f"{self.stage.name}.tfplan"

        if not self._working_dir.exists():
            messages.append(
                f"Terraform working directory does not exist: {self._working_dir}\n"
                "  Run 'strata build run' first to copy IaC artefacts to the build folder."
            )
            return False, messages

        tf_files = list(self._working_dir.glob("*.tf"))
        if not tf_files:
            messages.append(
                f"No *.tf files found in: {self._working_dir}\n"
                "  The build step should have copied Terraform source code here."
            )
            return False, messages

        if self.verbose:
            messages.append(f"Terraform working directory OK: {self._working_dir} ({len(tf_files)} .tf file(s))")

        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify Terraform binary is on PATH and the integration can be resolved."""
        messages: List[str] = []

        if self._iac_model is None:
            messages.append("validate_workspace() must succeed before validate_environment()")
            return False, messages

        try:
            self._tf = self._get_terraform_integration(self._iac_model.name)
        except RuntimeError as exc:
            messages.append(str(exc))
            return False, messages

        if not self._tf.is_available():
            messages.append("Terraform binary not found on PATH. Install Terraform and ensure it is accessible.")
            return False, messages

        return True, messages

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform init"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._iac_model is not None
        assert self._tf is not None

        backend_config = self._build_backend_config(self._iac_model)
        messages.append(f"terraform init  ({self._working_dir})")

        try:
            result = self._tf.init(
                str(self._working_dir),
                backend_config=backend_config or None,
                reconfigure=bool(backend_config),
                line_callback=line_callback,
                timeout=self._get_timeout("setup", 300),
            )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform init failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform init error: {exc}")
            return False, messages

        return True, messages

    def check(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform validate"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._tf is not None

        messages.append("terraform validate")

        try:
            result = self._tf.validate(
                str(self._working_dir),
                timeout=self._get_timeout("check", 60),
            )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform validate failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform validate error: {exc}")
            return False, messages

        return True, messages

    def plan(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform plan -detailed-exitcode -out=<stage>.tfplan

        Uses ``-detailed-exitcode`` so the exit code carries change-detection:
          0 = success, no changes  → sets _plan_has_changes = False
          1 = error
          2 = success, changes present → sets _plan_has_changes = True
        """
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        messages.append(f"terraform plan  \u2192 {self._plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.plan(
                    str(self._working_dir),
                    out_file=str(self._plan_file),
                    detailed_exitcode=True,
                    line_callback=line_callback,
                    timeout=self._get_timeout("plan", 600),
                )
            # -detailed-exitcode contract: 0=no changes, 1=error, 2=changes present
            if result.returncode == 0:
                self._plan_has_changes = False
                messages.append("\u21b3 No changes \u2014 infrastructure is already up to date.")
            elif result.returncode == 2:
                self._plan_has_changes = True
            else:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform plan failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform plan error: {exc}")
            return False, messages

        return True, messages

    def apply(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform apply <stage>.tfplan

        Short-circuits when ``plan()`` determined there are no changes
        (_plan_has_changes is False).  Downstream stages still run.
        """
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        if self._plan_has_changes is False:
            messages.append("\u21b3 No changes \u2014 apply skipped.")
            return True, messages

        messages.append(f"terraform apply  {self._plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.apply(
                    str(self._working_dir),
                    plan_file=str(self._plan_file),
                    line_callback=line_callback,
                    timeout=self._get_timeout("apply", 1800),
                )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform apply failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform apply error: {exc}")
            return False, messages

        messages.append(f"\u2713 Stage '{self.stage.name}' applied successfully.")
        return True, messages

    def destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """terraform destroy (-auto-approve when force=True)"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._tf is not None

        messages.append("terraform destroy")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.destroy(
                    str(self._working_dir),
                    auto_approve=self.force,
                    line_callback=line_callback,
                    timeout=self._get_timeout("destroy", 1800),
                )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform destroy failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip() and line_callback is None:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform destroy error: {exc}")
            return False, messages

        messages.append(f"\u2713 Stage '{self.stage.name}' destroyed successfully.")
        return True, messages

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """terraform plan -destroy  (preview what destroy would remove)"""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        messages.append(f"terraform plan -destroy  \u2192 {self._plan_file.name}")

        try:
            ctx = inject_tf_vars(self.resolved_values) if self.resolved_values else nullcontext()
            with ctx:
                result = self._tf.plan(
                    str(self._working_dir),
                    out_file=str(self._plan_file),
                    destroy=True,
                )
            if result.returncode != 0:
                output = "\n".join(filter(None, [result.stderr, result.stdout]))
                messages.append(f"terraform plan -destroy failed:\n{output}")
                return False, messages
            if self.verbose and result.stdout.strip():
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform plan -destroy error: {exc}")
            return False, messages

        return True, messages

    def collect_outputs(self) -> Tuple[bool, Dict[str, Any], Dict[str, Any], List[str]]:
        """Collect Terraform outputs after a successful apply, split by sensitivity.

        Runs ``terraform output -json`` and reads the ``sensitive`` flag on each
        output descriptor.  Non-sensitive outputs are returned in the first dict
        and will be injected as ``TF_VAR_<key>`` env vars for downstream stages.
        Sensitive outputs (``sensitive = true`` in Terraform) are returned in the
        second dict — the pipeline holds them internally but never injects them
        into subprocess environments.

        Returns:
            (success, non_sensitive_outputs, sensitive_outputs, messages)
        """
        messages: List[str] = []
        non_sensitive: Dict[str, Any] = {}
        sensitive: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, non_sensitive, sensitive, messages
        assert self._working_dir is not None
        assert self._tf is not None

        try:
            result = self._tf.output(str(self._working_dir))
            if result.returncode != 0:
                messages.append(f"terraform output failed:\n{result.stderr}")
                return False, non_sensitive, sensitive, messages
            raw: Dict[str, Any] = json.loads(result.stdout or "{}")
            for key, descriptor in raw.items():
                val = descriptor.get("value")
                if descriptor.get("sensitive", False):
                    sensitive[key] = val
                else:
                    non_sensitive[key] = val
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform output error: {exc}")
            return False, non_sensitive, sensitive, messages

        return True, non_sensitive, sensitive, messages

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """terraform output -json -> {name: value} dict"""
        messages: List[str] = []
        outputs: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, outputs, messages
        assert self._working_dir is not None
        assert self._tf is not None

        messages.append("terraform output -json")

        try:
            result = self._tf.output(str(self._working_dir))
            if result.returncode != 0:
                messages.append(f"terraform output failed:\n{result.stderr}")
                return False, outputs, messages
            raw: Dict[str, Any] = json.loads(result.stdout or "{}")
            # terraform output -json returns {name: {value: ..., type: ...}}
            outputs = {k: v.get("value") for k, v in raw.items()}
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform output error: {exc}")
            return False, outputs, messages

        return True, outputs, messages

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """terraform show -json <stage>.tfplan  → raw plan data dict"""
        messages: List[str] = []
        data: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, data, messages
        assert self._working_dir is not None
        assert self._plan_file is not None
        assert self._tf is not None

        if not self._plan_file.exists():
            messages.append(f"No saved plan found at {self._plan_file}. Run 'strata deploy run --dry-run' first.")
            return False, data, messages

        messages.append(f"terraform show -json  {self._plan_file.name}")

        try:
            result = self._tf.show(
                str(self._working_dir),
                plan_file=str(self._plan_file),
                json_format=True,
            )
            if result.returncode != 0:
                messages.append(f"terraform show failed:\n{result.stderr}")
                return False, data, messages
            data = json.loads(result.stdout or "{}")
        except (RuntimeError, ValueError, json.JSONDecodeError) as exc:
            messages.append(f"terraform show error: {exc}")
            return False, data, messages

        return True, data, messages

    def save_plan_json(self) -> Tuple[bool, Optional[Path], List[str]]:
        """Save the plan as JSON alongside the binary: {stage}.tfplan → {stage}.tfplan.json.

        Calls show_plan() internally; the plan step must have run first.
        """
        ok, data, msgs = self.show_plan()
        if not ok or not data:
            return False, None, msgs
        assert self._plan_file is not None  # guaranteed by show_plan() / _ready()
        out_path = self._plan_file.parent / f"{self.stage.name}.tfplan.json"
        try:
            out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as exc:
            msgs.append(f"Could not write plan JSON: {exc}")
            return False, None, msgs
        return True, out_path, msgs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ready(self, messages: List[str]) -> bool:
        """Guard: ensure validate_workspace + validate_environment have been called."""
        if self._working_dir is None or self._plan_file is None:
            messages.append("validate_workspace() must be called before running steps")
            return False
        if self._tf is None:
            messages.append("validate_environment() must be called before running steps")
            return False
        return True

    def _get_working_dir(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        iac_model: WorkspaceIacModel,
    ) -> Path:
        """Return the filesystem path where terraform commands should run.

        The build step copies IaC source to:
            deployment_build_path / iac_model.source.target_path
        Falls back to ``terraform/{iac_model.name}`` when target_path is unset,
        so the deployer working directory aligns with both the terraform source
        location and where the builder writes the auto.tfvars.json files.
        """
        deployment_build_path = deployment_service.get_build_path(build_path)
        target = iac_model.source.target_path or (Path("terraform") / iac_model.name)
        return deployment_build_path / target

    def _build_backend_config(self, iac_model: WorkspaceIacModel) -> Optional[Dict[str, str]]:
        """Extract backend configuration key-value pairs from the IaC model."""
        if not iac_model.backend:
            return None
        config = iac_model.backend.configuration or {}
        return {k: str(v) for k, v in config.items()} if config else None

    @staticmethod
    def _get_terraform_integration(name: str) -> TerraformIntegration:
        """Return the registered TerraformIntegration instance by name."""
        from strata.services.integration_service import IntegrationService

        svc = IntegrationService.get_instance()
        integration = svc.get_integration(name)
        if integration is None:
            raise RuntimeError(
                f"Terraform integration '{name}' is not registered. Ensure integrations are initialized."
            )
        if not isinstance(integration, TerraformIntegration):
            raise RuntimeError(
                f"Integration '{name}' is not a TerraformIntegration (got {type(integration).__name__})."
            )
        return integration
