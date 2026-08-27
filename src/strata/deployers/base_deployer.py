"""Abstract base class for IaC deployers (step-based provisioner style)."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from strata.logger import get_logger
from strata.models.deployment_model import DeploymentStageModel
from strata.models.workspace_model import WorkspaceIacModel
from strata.services.configuration_service import ConfigurationService
from strata.services.deployment_service import DeploymentService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.utils.resolved_values import ResolvedValues

# Canonical step name constants — use these in get_supported_steps() and callers.
STEP_SETUP = "setup"
STEP_CHECK = "check"
STEP_PLAN = "plan"
STEP_APPLY = "apply"
STEP_DESTROY = "destroy"
STEP_PLAN_DESTROY = "plan_destroy"
STEP_SHOW_PLAN = "show_plan"
STEP_OUTPUT = "output"
STEP_STATUS = "status"
STEP_HEALTH = "health"
STEP_DRIFT = "drift"


class BaseDeployer(ABC):
    """Abstract base for IaC deployers.

    Instantiate once per stage:
        deployer = MyDeployer(
            stage=stage,
            deployment_service=...,
            configuration_service=...,
            build_path=...,
            work_path=...,
        )
        ok, msgs = deployer.validate_workspace()
        ok, msgs = deployer.validate_environment()
        for step in steps_to_run:
            ok, msgs = getattr(deployer, step)()
    """

    def __init__(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        solution_controller: Optional["SolutionController"] = None,
        resolved_values: Optional["ResolvedValues"] = None,
    ):
        self.stage = stage
        self.deployment_service = deployment_service
        self.configuration_service = configuration_service
        self.build_path = build_path
        self.work_path = work_path
        self.verbose = verbose
        self.force = force
        self.solution_controller = solution_controller
        self.resolved_values = resolved_values
        self.logger = get_logger(self.__class__.__module__)

        # Effective namespace allowlist for this run (CLI --namespace, falling back
        # to stage.helm_namespaces). Not a constructor parameter — set post-construction
        # by BaseDeployCommand._create_deployer() so no deployer subclass's __init__
        # signature needs to change. Only consumed by HelmDeployer today; every other
        # deployer ignores it. None means "no filtering" (deploy all namespaces).
        self.namespace_filter: Optional[List[str]] = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @abstractmethod
    def get_deployer_name(self) -> str:
        """Return the canonical name/type of this deployer (e.g. 'terraform')."""
        raise NotImplementedError

    @abstractmethod
    def get_supported_steps(self) -> List[str]:
        """Return the ordered list of step names this deployer supports.

        Example: ["setup", "check", "plan", "apply", "destroy", "output"]
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Validation (pre-step guards)
    # ------------------------------------------------------------------

    @abstractmethod
    def validate_workspace(self) -> Tuple[bool, List[str]]:
        """Verify the workspace artefacts required by this deployer exist.

        Called before any steps run.  Should check that IaC source files
        were copied to the build path by the build pipeline.

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def validate_environment(self) -> Tuple[bool, List[str]]:
        """Verify tool-specific requirements (binary on PATH, auth, etc.).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Step methods
    # ------------------------------------------------------------------

    @abstractmethod
    def setup(self) -> Tuple[bool, List[str]]:
        """Initialise the IaC tool (e.g. terraform init).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def check(self) -> Tuple[bool, List[str]]:
        """Validate IaC configuration (e.g. terraform validate).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def plan(self) -> Tuple[bool, List[str]]:
        """Preview infrastructure changes (e.g. terraform plan).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def apply(self) -> Tuple[bool, List[str]]:
        """Apply infrastructure changes (e.g. terraform apply).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def destroy(self) -> Tuple[bool, List[str]]:
        """Destroy infrastructure resources (e.g. terraform destroy).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def plan_destroy(self) -> Tuple[bool, List[str]]:
        """Preview what destroy would remove (e.g. terraform plan -destroy).

        Returns:
            (success, messages)
        """
        raise NotImplementedError

    @abstractmethod
    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Decode the last saved plan file (e.g. terraform show -json <plan>).

        Returns:
            (success, plan_data_dict, messages)
        """
        raise NotImplementedError

    def describe_plan(self) -> List[str]:
        """Return human-readable lines describing what this deployer would execute.

        Called by the deploy pipeline in dry-run mode after validation succeeds,
        before any steps run.  Override in deployers that can surface meaningful
        plan context (e.g. AnsibleDeployer emits playbook and inventory paths).

        Returns:
            List of descriptive strings — empty list if nothing to report.
        """
        return []

    def save_plan_json(self) -> Tuple[bool, Optional[Path], List[str]]:
        """Persist the plan as a human-readable JSON file alongside the binary plan.

        Default implementation is a no-op for deployers that do not produce a
        structured plan file (e.g. Ansible). Override in deployers that do
        (e.g. TerraformDeployer).

        Returns:
            (success, path_or_None, messages)
        """
        return True, None, []

    def collect_outputs(self) -> Tuple[bool, Dict[str, Any], Dict[str, Any], List[str]]:
        """Collect outputs produced by this stage after a successful apply.

        Called by the deploy pipeline after ``apply`` completes.  The returned
        dicts are merged into ``ResolvedValues.stage_outputs`` (non-sensitive)
        and ``ResolvedValues.stage_outputs_sensitive`` (sensitive) and made
        available to all subsequent stages.

        Non-sensitive outputs are injected as ``TF_VAR_<key>`` / verbatim env
        vars into every subsequent stage subprocess.  Sensitive outputs are
        held internally by the system but never injected into subprocess
        environments, preventing accidental secret leakage.

        Sensitivity is determined by the deployer:
        - Terraform: reads the ``sensitive`` flag from ``terraform output -json``
        - Other deployers: underscore-prefix convention (``_key`` → sensitive)
        - Default (no outputs): returns empty dicts for both buckets

        Returns:
            (success, non_sensitive_outputs, sensitive_outputs, messages)
        """
        return True, {}, {}, []

    @abstractmethod
    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Retrieve infrastructure outputs (e.g. terraform output).

        Returns:
            (success, outputs_dict, messages)
        """
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Optional lifecycle methods (non-abstract, override as needed)
    # ------------------------------------------------------------------

    def status(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Query current infrastructure state.

        Override in deployers that can report live state (e.g. terraform show).
        Default returns empty data with an informational message.

        Returns:
            (success, status_data, messages)
        """
        return True, {}, [f"Status not implemented for '{self.get_deployer_name()}' provisioner"]

    def health(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Run health checks against deployed infrastructure.

        Override in deployers that can verify deployment health (e.g. helm
        status, argocd app health).  Default returns empty data with an
        informational message.

        Returns:
            (success, health_data, messages)
        """
        return True, {}, [f"Health check not implemented for '{self.get_deployer_name()}' provisioner"]

    def drift(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Detect infrastructure drift by comparing state to configuration.

        Override in deployers that can run a non-destructive plan/diff
        (e.g. ``terraform plan -detailed-exitcode -json``).  Default returns
        an informational message indicating the feature is not implemented.

        Returns:
            (success, resource_changes_dict, messages)

            ``resource_changes_dict`` must contain a key ``"resource_changes"``
            whose value is a list of resource-change dicts compatible with the
            ``terraform show -json`` schema so that ``DriftController`` can
            classify them uniformly regardless of the underlying deployer.
        """
        return True, {}, [f"Drift detection not implemented for '{self.get_deployer_name()}' provisioner"]

    # ------------------------------------------------------------------
    # Timeout helpers
    # ------------------------------------------------------------------

    def _get_timeout(self, step: str, default: int) -> int:
        """Return the per-step timeout from stage.timeouts, or *default* if not set."""
        t = getattr(self.stage, "timeouts", None)
        if t is None:
            return default
        val = getattr(t, step, None)
        return val if val is not None else default

    def _resolve_iac_model(
        self,
        stage: DeploymentStageModel,
        workspace_service,
    ) -> Optional[WorkspaceIacModel]:
        """Resolve the WorkspaceIacModel for a stage.

        Priority:
        1. stage.provisioner set — match workspace.spec.provisioners by name.
        2. stage.topology set   — find topology by name; topo.provisioner is a
                                  name reference, so look up the IaC entry by name.
        3. Single provisioner   — use it unconditionally.
        """
        spec = workspace_service.model.spec
        provisioners = spec.provisioners or []

        if not provisioners:
            return None

        # Priority 1: explicit provisioner name on stage
        if stage.provisioner:
            match = next((p for p in provisioners if p.name == stage.provisioner), None)
            if match:
                return match
            self.logger.warning(
                "stage.provisioner name not found in workspace.spec.provisioners",
                stage=stage.name,
                provisioner=stage.provisioner,
            )

        # Priority 2: resolve via topology (topo.provisioner is a name reference)
        if stage.topology:
            topo = next(
                (t for t in (spec.topology or []) if t.name == stage.topology),
                None,
            )
            if topo:
                match = next(
                    (p for p in provisioners if p.name == topo.provisioner),
                    None,
                )
                if match:
                    return match
                self.logger.warning(
                    "No workspace provisioner matches topology.provisioner name",
                    stage=stage.name,
                    topology=stage.topology,
                    provisioner_name=str(topo.provisioner),
                )

        # Priority 3: single provisioner — use it directly
        if len(provisioners) == 1:
            self.logger.debug(
                "Using sole workspace provisioner for stage (no explicit reference)",
                stage=stage.name,
                provisioner=provisioners[0].name,
            )
            return provisioners[0]

        return None
