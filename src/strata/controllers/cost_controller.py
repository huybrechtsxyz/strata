"""Cost estimation controller.

Orchestrates cost estimation for deployment builds using registered
cost estimator integrations (e.g. Infracost).

Follows the same dependency-injection pattern as DriftController:
receives a deployment_service, build_path, and solution_controller
from the CLI layer rather than re-discovering paths internally.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.integrations.capabilities import ICostEstimator
from strata.models.common_models import ProvisionerType

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController
    from strata.models.workspace_model import WorkspaceIacModel
    from strata.services.deployment_service import DeploymentService


class CostController(BaseController):
    """Controller for infrastructure cost estimation operations."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show(
        self,
        deployment_service: "DeploymentService",
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
        currency: Optional[str] = None,
        provisioner_filter: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Get cost breakdown for a deployment's terraform provisioners.

        Iterates over terraform provisioners in the workspace and runs
        ``infracost breakdown`` on each. Returns combined results.

        Args:
            deployment_service: Loaded deployment service with resolved workspace.
            build_path: Base build directory (e.g. work_path / "build").
            solution_controller: Optional solution context for path resolution.
            currency: Optional ISO 4217 currency code (e.g. "EUR", "GBP").
            provisioner_filter: Optional provisioner name to limit estimation to.

        Returns:
            Tuple of (success, result_dict).
            On success: result_dict contains cost data keyed by provisioner name.
            On failure: result_dict contains ``{"error": "<reason>"}``.
        """
        estimator = self._get_estimator()
        if estimator is None:
            return self._fail("No cost estimator available. Install infracost: https://www.infracost.io/docs/install")

        available, msg = estimator.ensure_available()
        if not available:
            return self._fail(msg)

        provisioners = self._get_terraform_provisioners(deployment_service, provisioner_filter)
        if not provisioners:
            return self._fail(
                "No terraform provisioners found in workspace."
                + (f" (filter: '{provisioner_filter}')" if provisioner_filter else "")
            )

        results: Dict[str, Any] = {}
        for iac in provisioners:
            terraform_path = self._resolve_provisioner_path(iac, deployment_service, build_path, solution_controller)
            if terraform_path is None:
                self._add_error(f"Build artifacts not found for provisioner '{iac.name}'. Run: strata build run")
                continue

            if not (terraform_path / ".terraform").exists():
                self._add_error(
                    f"Terraform not initialized for provisioner '{iac.name}' at {terraform_path}. Run: terraform init"
                )
                continue

            try:
                result = estimator.breakdown(str(terraform_path), currency=currency)
                results[str(iac.name)] = result
                self._add_message(f"Cost estimate retrieved for provisioner '{iac.name}'")
            except Exception as exc:
                self._add_error(f"Cost estimation failed for provisioner '{iac.name}': {exc}")

        if not results:
            return False, {"error": "No cost estimates could be produced", "details": self.get_errors()}

        return True, {"provisioners": results}

    def diff(
        self,
        deployment_service: "DeploymentService",
        build_path: Path,
        plan_file: str,
        solution_controller: Optional["SolutionController"] = None,
        currency: Optional[str] = None,
        provisioner_filter: Optional[str] = None,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Get cost diff for a terraform plan (used during deploy --dry-run).

        Args:
            deployment_service: Loaded deployment service.
            build_path: Base build directory.
            plan_file: Path to the terraform plan JSON file.
            solution_controller: Optional solution context.
            currency: Optional ISO 4217 currency code.
            provisioner_filter: Optional provisioner name to limit to.

        Returns:
            Tuple of (success, result_dict).
        """
        estimator = self._get_estimator()
        if estimator is None:
            return self._fail("No cost estimator available. Install infracost: https://www.infracost.io/docs/install")

        available, msg = estimator.ensure_available()
        if not available:
            return self._fail(msg)

        if not Path(plan_file).exists():
            return self._fail(f"Plan file not found: {plan_file}")

        provisioners = self._get_terraform_provisioners(deployment_service, provisioner_filter)
        if not provisioners:
            return self._fail("No terraform provisioners found in workspace.")

        # For diff, use the first matching provisioner (plan is per-provisioner)
        iac = provisioners[0]
        terraform_path = self._resolve_provisioner_path(iac, deployment_service, build_path, solution_controller)
        if terraform_path is None:
            return self._fail(f"Build artifacts not found for provisioner '{iac.name}'.")

        try:
            result = estimator.diff(str(terraform_path), plan_file, currency=currency)
            self._add_message(f"Cost diff retrieved for provisioner '{iac.name}'")
            return True, result
        except Exception as exc:
            return self._fail(f"Cost diff failed: {exc}")

    def is_available(self) -> bool:
        """Return True if a cost estimator integration is available and installed."""
        estimator = self._get_estimator()
        if estimator is None:
            return False
        available, _ = estimator.ensure_available()
        return available

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_estimator(self) -> Optional[ICostEstimator]:
        """Return the first available ICostEstimator integration, or None."""
        from strata.integrations.factory import IntegrationFactory

        for type_str in IntegrationFactory.get_known_types():
            try:
                integration = IntegrationFactory.create_by_type(type_str)
                if isinstance(integration, ICostEstimator):
                    return integration
            except Exception:
                continue
        return None

    def _get_terraform_provisioners(
        self,
        deployment_service: "DeploymentService",
        provisioner_filter: Optional[str] = None,
    ) -> List["WorkspaceIacModel"]:
        """Return terraform provisioners from the workspace.

        Args:
            deployment_service: Loaded deployment service (must have workspace resolved).
            provisioner_filter: Optional name to match a specific provisioner.

        Returns:
            List of WorkspaceIacModel entries with provisioner == "terraform".
        """
        ws_service = deployment_service.get_workspace_service()
        if ws_service is None or ws_service.model is None:
            return []

        provisioners = ws_service.model.spec.provisioners or []
        terraform_provisioners = [p for p in provisioners if p.provisioner == ProvisionerType.TERRAFORM]

        if provisioner_filter:
            terraform_provisioners = [p for p in terraform_provisioners if str(p.name) == provisioner_filter]

        return terraform_provisioners

    def _resolve_provisioner_path(
        self,
        iac: "WorkspaceIacModel",
        deployment_service: "DeploymentService",
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> Optional[Path]:
        """Resolve the terraform working directory for a provisioner.

        Uses the same path resolution as deployers:
        - solution_controller.get_provisioner_path() when available
        - Fallback: deployment_service.get_build_path() / source_path

        Returns:
            Path to the provisioner directory, or None if it doesn't exist.
        """
        try:
            if solution_controller is not None:
                path = solution_controller.get_provisioner_path(deployment_service, build_path, iac)
            else:
                # Fallback: manual resolution matching get_provisioner_path logic
                if iac.source is None:
                    return None
                source_path = iac.source.target_path or iac.source.source_path
                if source_path is None:
                    return None
                path = deployment_service.get_build_path(build_path) / source_path

            return path if path.exists() else None
        except (ValueError, AttributeError):
            return None

    def _fail(self, message: str) -> Tuple[bool, Dict[str, Any]]:
        """Add error and return a failure tuple."""
        self._add_error(message)
        return False, {"error": message}
