"""Bicep deployer — Azure-native IaC provisioner using ARM deployments.

Uses the Azure CLI (``az deployment {scope} create``) to deploy Bicep
templates.  No state file or backend configuration is required — ARM
manages deployment state server-side.

Supported steps (in execution order):

  setup        — ``az bicep build`` (syntax validation + module bundling)
  check        — ``az bicep build`` (alias: validates without deploying)
  plan         — ``az deployment {scope} what-if`` (ARM change preview)
  apply        — ``az deployment {scope} create`` (deploy)
  destroy      — ``az deployment {scope} delete`` (remove deployment record)
  plan_destroy — what-if with mode=Complete (preview what delete would remove)
  output       — ``az deployment {scope} show`` (ARM deployment outputs)
  show_plan    — return last what-if result dict

Deployment scopes:
  resourceGroup    — ``az deployment group ...``   (default)
  subscription     — ``az deployment sub ...``
  managementGroup  — ``az deployment mg ...``
  tenant           — ``az deployment tenant ...``

Workspace YAML::

    provisioners:
      - name: infrastructure
        provisioner: bicep
        source:
          repository: my-repo
          source_path: bicep         # directory containing main.bicep
        configuration:
          scope: resourceGroup       # resourceGroup | subscription | managementGroup | tenant
          resource_group: my-rg      # required when scope=resourceGroup
          location: westeurope       # required for subscription/managementGroup/tenant
          management_group_id: mg-0  # required when scope=managementGroup
          deployment_name: strata    # optional: ARM deployment name (default: strata-{stage})
          parameters_file: params.json  # optional: relative to source_path
          mode: Incremental          # Incremental (default) | Complete (resourceGroup only)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

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
from strata.integrations.azure_cli import AzureCLIIntegration
from strata.models.workspace_model import WorkspaceIacModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.models.deployment_model import DeploymentStageModel
    from strata.utils.resolved_values import ResolvedValues

# Valid ARM deployment scopes
_SCOPES = ("resourceGroup", "subscription", "managementGroup", "tenant")

# Map scope → az subcommand group
_SCOPE_CMD = {
    "resourceGroup": "group",
    "subscription": "sub",
    "managementGroup": "mg",
    "tenant": "tenant",
}


class BicepDeployer(BaseDeployer):
    """Deploys Azure infrastructure via Bicep templates and ARM deployments.

    Steps mirror the Terraform deployer pattern:
      validate_workspace  → check .bicep source files exist
      validate_environment → check az is installed and authenticated
      setup               → az bicep build (syntax check + module bundle)
      check               → az bicep build (alias)
      plan                → az deployment {scope} what-if
      apply               → az deployment {scope} create
      destroy             → az deployment {scope} delete
      output              → az deployment {scope} show --query properties.outputs
    """

    def __init__(
        self,
        stage: "DeploymentStageModel",
        deployment_service: DeploymentService,
        configuration_service: ConfigurationService,
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        solution_controller: Optional["SolutionController"] = None,
        resolved_values: Optional["ResolvedValues"] = None,
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
            resolved_values=resolved_values,
        )
        self._iac_model: Optional[WorkspaceIacModel] = None
        self._working_dir: Optional[Path] = None
        self._az: Optional[AzureCLIIntegration] = None  # set in validate_environment
        self._last_whatif: Optional[Dict[str, Any]] = None  # saved by plan()

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def get_deployer_name(self) -> str:
        return "bicep"

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
    # Validation
    # ------------------------------------------------------------------

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify that .bicep source files exist in the build directory."""
        messages: List[str] = []

        workspace_service = self.deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        self._iac_model = self._resolve_iac_model(self.stage, workspace_service)
        if not self._iac_model:
            messages.append(
                f"Stage '{self.stage.name}': cannot resolve a bicep provisioner. "
                "Set stage.provisioner to the provisioner name or ensure a workspace "
                "topology with provisioner='bicep' exists."
            )
            return False, messages

        if self.solution_controller is not None:
            self._working_dir = self.solution_controller.get_provisioner_path(
                self.deployment_service, self.build_path, self._iac_model
            )
        else:
            # Compute path directly — bicep deployer has no _get_working_dir helper
            self._working_dir = self.build_path / self._iac_model.name

        if not self._working_dir.exists():
            messages.append(
                f"Bicep working directory does not exist: {self._working_dir}\n"
                "  Run 'strata build run' first to copy IaC artefacts to the build folder."
            )
            return False, messages

        bicep_files = list(self._working_dir.glob("*.bicep"))
        if not bicep_files:
            messages.append(
                f"No *.bicep files found in: {self._working_dir}\n"
                "  The build step should have copied Bicep source here."
            )
            return False, messages

        if self.verbose:
            messages.append(f"Bicep working directory OK: {self._working_dir} ({len(bicep_files)} .bicep file(s))")

        return True, messages

    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify Azure CLI is installed and authenticated."""
        messages: List[str] = []

        if self._iac_model is None:
            messages.append("validate_workspace() must succeed before validate_environment()")
            return False, messages

        from strata.integrations.azure_cli import AzureCLIIntegration
        from strata.models.integration_model import IntegrationModel

        config = IntegrationModel(name="azure", type="azure_cli")
        self._az = AzureCLIIntegration(config)

        ok, reason = self._az.ensure_available()
        if not ok:
            messages.append(f"Azure CLI not ready: {reason}")
            return False, messages

        if self.verbose:
            messages.append(f"Azure CLI: {self._az._info}")

        return True, messages

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    def setup(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """az bicep build — validates syntax and bundles modules."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        assert self._az is not None  # guaranteed by _ready()
        template = self._main_template()
        if template is None:
            messages.append(f"No main.bicep found in {self._working_dir}")
            return False, messages

        messages.append(f"az bicep build  {template.name}")
        result = self._az.run_az(
            ["bicep", "build", "--file", str(template)],
            timeout=self._get_timeout("setup", 120),
        )
        if result.returncode != 0:
            messages.append(f"az bicep build failed:\n{result.stderr or result.stdout}")
            return False, messages

        if self.verbose and result.stdout.strip():
            messages.append(result.stdout.strip())

        return True, messages

    def check(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """Alias for setup — Bicep has no separate validate command."""
        return self.setup(line_callback=line_callback)

    def plan(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """az deployment {scope} what-if — ARM change preview."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        assert self._az is not None  # guaranteed by _ready()
        scope = self._scope()
        template = self._main_template()
        if template is None:
            messages.append(f"No main.bicep found in {self._working_dir}")
            return False, messages

        cmd = self._deployment_cmd(scope, "what-if")
        cmd += ["--template-file", str(template), "--output", "json", "--no-prompt"]
        params = self._parameters_file()
        if params:
            cmd += ["--parameters", f"@{params}"]

        messages.append(f"az deployment {_SCOPE_CMD[scope]} what-if  →  {template.name}")
        result = self._az.run_az(cmd, timeout=self._get_timeout("plan", 300))

        if result.returncode not in (0, 1):  # 0=no changes, 1=changes detected (both ok)
            messages.append(f"az deployment what-if failed:\n{result.stderr or result.stdout}")
            return False, messages

        # Parse and cache what-if output
        try:
            self._last_whatif = json.loads(result.stdout) if result.stdout else {}
        except json.JSONDecodeError:
            self._last_whatif = {"raw": result.stdout}

        if self.verbose and result.stdout:
            messages.append(result.stdout.strip())

        return True, messages

    def apply(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """az deployment {scope} create — deploy the Bicep template."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        assert self._az is not None  # guaranteed by _ready()
        scope = self._scope()
        template = self._main_template()
        if template is None:
            messages.append(f"No main.bicep found in {self._working_dir}")
            return False, messages

        deployment_name = self._deployment_name()
        cmd = self._deployment_cmd(scope, "create")
        cmd += [
            "--template-file",
            str(template),
            "--name",
            deployment_name,
            "--mode",
            self._mode(),
            "--output",
            "json",
            "--no-prompt",
        ]
        params = self._parameters_file()
        if params:
            cmd += ["--parameters", f"@{params}"]

        messages.append(f"az deployment {_SCOPE_CMD[scope]} create  →  {template.name} (deployment: {deployment_name})")
        result = self._az.run_az(cmd, timeout=self._get_timeout("apply", 1800))

        if result.returncode != 0:
            messages.append(f"az deployment create failed:\n{result.stderr or result.stdout}")
            return False, messages

        if self.verbose and result.stdout:
            messages.append(result.stdout.strip())

        messages.append(f"✓ Stage '{self.stage.name}' deployed successfully.")
        return True, messages

    def destroy(
        self,
        line_callback: Optional[Callable[[str, str], None]] = None,
    ) -> Tuple[bool, List[str]]:
        """az deployment {scope} delete — remove the ARM deployment record."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        assert self._az is not None  # guaranteed by _ready()
        scope = self._scope()
        deployment_name = self._deployment_name()
        cmd = self._deployment_cmd(scope, "delete")
        cmd += ["--name", deployment_name]
        if self.force:
            cmd += ["--yes"]

        messages.append(f"az deployment {_SCOPE_CMD[scope]} delete  {deployment_name}")
        result = self._az.run_az(cmd, timeout=self._get_timeout("destroy", 600))

        if result.returncode != 0:
            messages.append(f"az deployment delete failed:\n{result.stderr or result.stdout}")
            return False, messages

        messages.append(f"✓ Stage '{self.stage.name}' deployment record deleted.")
        return True, messages

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """what-if with mode=Complete — shows what would be removed."""
        messages: List[str] = []
        if not self._ready(messages):
            return False, messages

        assert self._az is not None  # guaranteed by _ready()
        scope = self._scope()
        if scope != "resourceGroup":
            messages.append("plan_destroy (mode=Complete) is only supported for resourceGroup scope.")
            return False, messages

        template = self._main_template()
        if template is None:
            messages.append(f"No main.bicep found in {self._working_dir}")
            return False, messages

        cmd = self._deployment_cmd(scope, "what-if")
        cmd += ["--template-file", str(template), "--mode", "Complete", "--output", "json", "--no-prompt"]

        messages.append("az deployment group what-if --mode Complete")
        result = self._az.run_az(cmd, timeout=self._get_timeout("plan_destroy", 300))

        if result.returncode not in (0, 1):
            messages.append(f"az deployment what-if failed:\n{result.stderr or result.stdout}")
            return False, messages

        if self.verbose and result.stdout:
            messages.append(result.stdout.strip())

        return True, messages

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """az deployment {scope} show → ARM deployment outputs."""
        messages: List[str] = []
        outputs: Dict[str, Any] = {}

        if not self._ready(messages):
            return False, outputs, messages

        assert self._az is not None  # guaranteed by _ready()
        scope = self._scope()
        deployment_name = self._deployment_name()
        cmd = self._deployment_cmd(scope, "show")
        cmd += ["--name", deployment_name, "--output", "json", "--query", "properties.outputs"]

        messages.append(f"az deployment {_SCOPE_CMD[scope]} show  {deployment_name}")
        result = self._az.run_az(cmd, timeout=self._get_timeout("output", 60))

        if result.returncode != 0:
            messages.append(f"az deployment show failed:\n{result.stderr or result.stdout}")
            return False, outputs, messages

        try:
            raw = json.loads(result.stdout or "{}")
            # ARM outputs: {"key": {"type": "String", "value": "..."}}
            outputs = {k: v.get("value") for k, v in (raw or {}).items()}
        except json.JSONDecodeError as exc:
            messages.append(f"Failed to parse ARM outputs: {exc}")
            return False, outputs, messages

        self._write_outputs_cache(outputs)
        return True, outputs, messages

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Return the last what-if result saved by plan()."""
        if self._last_whatif is None:
            return False, {}, ["No what-if result available. Run plan() first."]
        return True, self._last_whatif, []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ready(self, messages: List[str]) -> bool:
        """Guard: validate_workspace + validate_environment must have run."""
        if self._iac_model is None:
            messages.append("validate_workspace() has not been called.")
            return False
        if self._az is None:
            messages.append("validate_environment() has not been called.")
            return False
        return True

    def _config(self) -> Dict[str, Any]:
        """Return the provisioner configuration dict (or {})."""
        if self._iac_model and self._iac_model.configuration:
            return dict(self._iac_model.configuration)
        return {}

    def _scope(self) -> str:
        """Return the ARM deployment scope (default: resourceGroup)."""
        scope = self._config().get("scope", "resourceGroup")
        if scope not in _SCOPES:
            return "resourceGroup"
        return scope

    def _deployment_name(self) -> str:
        cfg = self._config()
        return cfg.get("deployment_name") or f"strata-{self.stage.name}"

    def _mode(self) -> str:
        return self._config().get("mode", "Incremental")

    def _main_template(self) -> Optional[Path]:
        """Return path to main.bicep (or first .bicep file if main.bicep absent)."""
        if self._working_dir is None:
            return None
        main = self._working_dir / "main.bicep"
        if main.exists():
            return main
        files = list(self._working_dir.glob("*.bicep"))
        return files[0] if files else None

    def _parameters_file(self) -> Optional[str]:
        """Return path to parameters file if configured and exists."""
        cfg = self._config()
        pf = cfg.get("parameters_file")
        if not pf or self._working_dir is None:
            return None
        path = self._working_dir / pf
        return str(path) if path.exists() else None

    def _deployment_cmd(self, scope: str, subcommand: str) -> List[str]:
        """Build base az deployment command for the given scope and subcommand."""
        group = _SCOPE_CMD[scope]
        cmd = ["deployment", group, subcommand]
        cfg = self._config()

        if scope == "resourceGroup":
            rg = cfg.get("resource_group")
            if rg:
                cmd += ["--resource-group", rg]
        elif scope in ("subscription", "managementGroup", "tenant"):
            location = cfg.get("location")
            if location:
                cmd += ["--location", location]
            if scope == "managementGroup":
                mg_id = cfg.get("management_group_id")
                if mg_id:
                    cmd += ["--management-group-id", mg_id]

        return cmd

    def _write_outputs_cache(self, outputs: Dict[str, Any]) -> None:
        """Write outputs to build/<stage>.bicep-outputs.json."""
        cache_file = self.build_path / f"{self.stage.name}.bicep-outputs.json"
        try:
            data = {
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
                "outputs": outputs,
            }
            with open(cache_file, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, default=str)
        except OSError:
            pass  # non-fatal

    def _check_working_dir(self) -> Tuple[bool, List[str]]:
        """Verify _working_dir exists and contains .bicep files. Used by tests."""
        messages: List[str] = []
        if self._working_dir is None:
            return False, ["Working directory not set"]
        if not self._working_dir.exists():
            messages.append(
                f"Bicep working directory does not exist: {self._working_dir}\n"
                "  Run 'strata build run' first to copy IaC artefacts to the build folder."
            )
            return False, messages
        bicep_files = list(self._working_dir.glob("*.bicep"))
        if not bicep_files:
            messages.append(
                f"No *.bicep files found in: {self._working_dir}\n"
                "  The build step should have copied Bicep source here."
            )
            return False, messages
        return True, messages
