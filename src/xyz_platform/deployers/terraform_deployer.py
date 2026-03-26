#!/usr/bin/env python3
"""
===============================================================================
Script Name   : terraform_deployer.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Terraform deployer — step-based provisioner style.

                Supported steps (in execution order):
                  setup    — terraform init
                  check    — terraform validate
                  plan     — terraform plan  -out=<stage>.tfplan
                  apply    — terraform apply <stage>.tfplan
                  destroy  — terraform destroy  (requires --force / auto_approve)
                  output   — terraform output -json → {name: value} dict

                Typical caller sequences:
                  dry-run  : setup → check → plan
                  deploy   : setup → check → plan → apply
                  destroy  : setup → destroy  (--force required)
                  output   : output

                Working-directory resolution (priority order):
                  1. stage.provisioner (name) → workspace.spec.provisioners[name]
                  2. stage.topology (name)    → topology.provisioner type
                                              → workspace.spec.provisioners[.provisioner==type]
                  3. Single provisioner in workspace → used unconditionally

                  working_dir = deployment_build_path / iac_model.source.target_path
                  plan_file   = working_dir / "<stage.name>.tfplan"

                TODO (rollback): on_failure=rollback is not yet implemented.
===============================================================================
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

from xyz_platform.deployers.base_deployer import BaseDeployer
from xyz_platform.integrations.terraform import TerraformIntegration
from xyz_platform.logger import get_logger
from xyz_platform.models.deployment_model import DeploymentStageModel
from xyz_platform.models.workspace_model import WorkspaceIacModel
from xyz_platform.services.configuration_service import ConfigurationService
from xyz_platform.services.deployment_service import DeploymentService

logger = get_logger(__name__)


class TerraformDeployer(BaseDeployer):
    """Runs a deployment stage using Terraform (init → validate → plan → apply).

    A single instance can be reused across stages within the same deploy run.
    """

    def __init__(self, verbose: bool = False):
        self.verbose = verbose

    # ------------------------------------------------------------------
    # BaseDeployer interface
    # ------------------------------------------------------------------

    def before_deploy(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        build_path: Path,
        work_path: Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Verify the terraform working directory and var files exist."""
        messages: List[str] = []

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        iac_model = self._resolve_iac_model(stage, workspace_service)
        if not iac_model:
            messages.append(
                f"Stage '{stage.name}': cannot resolve a terraform provisioner. "
                "Set stage.provisioner (by name) or stage.topology with a workspace "
                "topology that has provisioner='terraform'."
            )
            return False, messages

        working_dir = self._get_working_dir(deployment_service, build_path, iac_model)

        if not working_dir.exists():
            messages.append(
                f"Terraform working directory does not exist: {working_dir}\n"
                "  Run 'xyz build run' first to copy IaC artefacts to the build folder."
            )
            return False, messages

        # Check that *.tf files are present
        tf_files = list(working_dir.glob("*.tf"))
        if not tf_files:
            messages.append(
                f"No *.tf files found in: {working_dir}\n"
                "  The build step should have copied Terraform source code here."
            )
            return False, messages

        if self.verbose:
            messages.append(
                f"Terraform working directory OK: {working_dir} "
                f"({len(tf_files)} .tf file(s))"
            )

        return True, messages

    def deploy(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        configuration_service: "ConfigurationService",
        build_path: Path,
        work_path: Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Run terraform init → validate → plan [→ apply].

        When *dry_run* is True the apply step is skipped and all output is
        prefixed with ``[DRY-RUN]``.
        """
        messages: List[str] = []

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        iac_model = self._resolve_iac_model(stage, workspace_service)
        if not iac_model:
            messages.append(
                f"Stage '{stage.name}': cannot resolve a terraform provisioner. "
                "Set stage.provisioner (by name) or stage.topology with a workspace "
                "topology that has provisioner='terraform'."
            )
            return False, messages

        working_dir = self._get_working_dir(deployment_service, build_path, iac_model)
        plan_file = working_dir / f"{stage.name}.tfplan"
        backend_config = self._build_backend_config(iac_model)

        try:
            tf = self._get_terraform_integration(iac_model.name)
        except RuntimeError as exc:
            messages.append(str(exc))
            return False, messages

        # ------------------------------------------------------------------
        # Step 1: init
        # ------------------------------------------------------------------
        prefix = "[DRY-RUN] " if dry_run else ""
        messages.append(f"{prefix}terraform init  ({working_dir})")
        if dry_run:
            messages.append(
                "[DRY-RUN] Would run: terraform init"
                + (" -reconfigure" if backend_config else "")
            )
        else:
            try:
                result = tf.init(
                    str(working_dir),
                    backend_config=backend_config or None,
                    reconfigure=bool(backend_config),
                )
                if result.returncode != 0:
                    messages.append(f"terraform init failed:\n{result.stderr}")
                    return False, messages
                if self.verbose:
                    messages.append(result.stdout.strip())
            except RuntimeError as exc:
                messages.append(f"terraform init error: {exc}")
                return False, messages

        # ------------------------------------------------------------------
        # Step 2: validate
        # ------------------------------------------------------------------
        messages.append(f"{prefix}terraform validate")
        if dry_run:
            messages.append("[DRY-RUN] Would run: terraform validate")
        else:
            try:
                result = tf.validate(str(working_dir))
                if result.returncode != 0:
                    messages.append(f"terraform validate failed:\n{result.stderr}")
                    return False, messages
                if self.verbose:
                    messages.append(result.stdout.strip())
            except RuntimeError as exc:
                messages.append(f"terraform validate error: {exc}")
                return False, messages

        # ------------------------------------------------------------------
        # Step 3: plan
        # ------------------------------------------------------------------
        messages.append(f"{prefix}terraform plan  → {plan_file.name}")
        if dry_run:
            messages.append(
                f"[DRY-RUN] Would run: terraform plan -out={plan_file.name}"
            )
        else:
            try:
                result = tf.plan(str(working_dir), out_file=str(plan_file))
                if result.returncode != 0:
                    messages.append(f"terraform plan failed:\n{result.stderr}")
                    return False, messages
                if self.verbose:
                    messages.append(result.stdout.strip())
            except RuntimeError as exc:
                messages.append(f"terraform plan error: {exc}")
                return False, messages

        if dry_run:
            messages.append(
                f"[DRY-RUN] Stage '{stage.name}' plan complete — apply skipped."
            )
            return True, messages

        # ------------------------------------------------------------------
        # Step 4: apply (uses saved plan file — no interactive prompt needed)
        # ------------------------------------------------------------------
        messages.append(f"terraform apply  {plan_file.name}")
        try:
            result = tf.apply(str(working_dir), plan_file=str(plan_file))
            if result.returncode != 0:
                messages.append(f"terraform apply failed:\n{result.stderr}")
                return False, messages
            if self.verbose:
                messages.append(result.stdout.strip())
        except RuntimeError as exc:
            messages.append(f"terraform apply error: {exc}")
            return False, messages

        messages.append(f"✓ Stage '{stage.name}' applied successfully.")
        return True, messages

    def after_deploy(
        self,
        stage: "DeploymentStageModel",
        deployment_service: "DeploymentService",
        build_path: Path,
        work_path: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Post-deploy verification — confirm plan file was written (or skipped in dry-run)."""
        messages: List[str] = []

        if dry_run:
            if self.verbose:
                messages.append("[DRY-RUN] Skipping post-deploy verification.")
            return True, messages

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        iac_model = self._resolve_iac_model(stage, workspace_service)
        if not iac_model:
            messages.append(
                f"Stage '{stage.name}': cannot resolve a terraform provisioner. "
                "Set stage.provisioner (by name) or stage.topology with a workspace "
                "topology that has provisioner='terraform'."
            )
            return False, messages

        working_dir = self._get_working_dir(deployment_service, build_path, iac_model)
        plan_file = working_dir / f"{stage.name}.tfplan"

        if not plan_file.exists():
            messages.append(f"Expected plan file not found after apply: {plan_file}")
            # Not blocking — apply may have consumed and removed the plan file

        if self.verbose:
            messages.append(f"Post-deploy check passed for stage '{stage.name}'.")

        return True, messages

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_iac_model(
        self,
        stage: "DeploymentStageModel",
        workspace_service,
    ) -> Optional["WorkspaceIacModel"]:
        """Resolve the WorkspaceIacModel for a stage.

        Priority:
        1. stage.provisioner set → match workspace.spec.provisioners by name
        2. stage.topology set   → find topology → get its ProvisionerType
                                 → match workspace.spec.provisioners by .provisioner type
        3. Single provisioner workspace → use it unconditionally
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
            logger.warning(
                "stage.provisioner name not found in workspace.spec.provisioners",
                extra={"stage": stage.name, "provisioner": stage.provisioner},
            )

        # Priority 2: resolve via topology
        if stage.topology:
            topo = next(
                (t for t in (spec.topology or []) if t.name == stage.topology),
                None,
            )
            if topo:
                match = next(
                    (p for p in provisioners if p.provisioner == topo.provisioner),
                    None,
                )
                if match:
                    return match
                logger.warning(
                    "No workspace provisioner matches topology.provisioner type",
                    extra={
                        "stage": stage.name,
                        "topology": stage.topology,
                        "provisioner_type": str(topo.provisioner),
                    },
                )

        # Priority 3: single provisioner — use it directly
        if len(provisioners) == 1:
            logger.debug(
                "Using sole workspace provisioner for stage (no explicit reference)",
                extra={"stage": stage.name, "provisioner": provisioners[0].name},
            )
            return provisioners[0]

        return None

    def _get_working_dir(
        self,
        deployment_service: "DeploymentService",
        build_path: Path,
        iac_model: "WorkspaceIacModel",
    ) -> Path:
        """Return the filesystem path where terraform commands should run.

        The build step copies IaC source from the fetched repository to:
            deployment_build_path / iac_model.source.target_path

        If target_path is not set on the source model we fall back to
        ``terraform/<iac_model.name>`` as a safe default.
        """
        deployment_build_path = deployment_service.get_build_path(build_path)
        target = iac_model.source.target_path or f"terraform/{iac_model.name}"
        return deployment_build_path / target

    def _build_backend_config(
        self, iac_model: "WorkspaceIacModel"
    ) -> Optional[Dict[str, str]]:
        """Extract backend configuration key-value pairs from the IaC model.

        Returns None when no backend is configured.
        """
        if not iac_model.backend:
            return None

        config = iac_model.backend.configuration or {}
        # configuration values may contain ${var:...} / ${secret:...} references;
        # for now pass them through as-is — secret resolution is out of scope here.
        return {k: str(v) for k, v in config.items()} if config else None

    @staticmethod
    def _get_terraform_integration(name: str) -> TerraformIntegration:
        """Return the registered TerraformIntegration instance by name.

        Integrations are managed by `IntegrationService` / `IntegrationRegistry`.
        If the requested integration is not registered or not a
        `TerraformIntegration`, raise `RuntimeError` so callers can handle it.
        """
        from xyz_platform.services.integration_service import IntegrationService

        svc = IntegrationService.get_instance()
        integration = svc.get_integration(name)
        if integration is None:
            raise RuntimeError(
                f"Terraform integration '{name}' is not registered. Ensure integrations are initialized."
            )
        if not isinstance(integration, TerraformIntegration):
            raise RuntimeError(
                f"Integration '{name}' is not a TerraformIntegration (found {type(integration).__name__})."
            )
        return integration
