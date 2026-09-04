"""Build Bicep artifacts by copying provisioner source into the build output.

Unlike Terraform/Ansible, Bicep has no generated-vars-file concept (no
``platform.json`` projection) — ARM deployments consume ``.bicep`` files and an
optional ``parameters_file`` directly from the copied source tree, and
``BicepDeployer`` reads its ``configuration`` block at deploy time. This builder's
only job is the provisioner-source copy step every other IaC provisioner already
has (ADR-0071 — Bicep previously had no builder-side copy step at all, so
``BicepDeployer.validate_workspace()`` would always fail with "Run 'strata build
run' first").
"""

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Optional

from strata.builders.base_builder import BaseBuilder
from strata.models.common_models import ProvisionerType
from strata.services.deployment_service import DeploymentService

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class BicepBuilder(BaseBuilder):
    """Builder that copies Bicep provisioner source files into the build output."""

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Validate that the deployment service is ready."""
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            self._errors.append("Workspace service is not available")
            return False

        if self.verbose:
            self._messages.append("Bicep pre-build validation passed")

        return True

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Copy Bicep source files from each provisioner's source_path into the build."""
        try:
            return self._copy_provisioner_source(
                deployment_service=deployment_service,
                build_path=build_path,
                work_path=work_path,
                repo_map=repo_map or {},
                dry_run=dry_run,
                solution_controller=solution_controller,
            )
        except Exception as exc:
            error_msg = f"Failed to build Bicep artifacts: {exc}"
            self.logger.exception("Failed to build Bicep artifacts", error=str(exc))
            self._errors.append(error_msg)
            return False

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """No-op — BicepDeployer.validate_workspace() already checks for *.bicep files."""
        return True

    # ------------------------------------------------------------------
    # Bicep provisioner source copy
    # ------------------------------------------------------------------

    def _copy_provisioner_source(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Copy bicep source files from each provisioner's source_path into the build.

        For each bicep provisioner declared in the workspace:
          source  = repo_root / source_path   (repo_root = work_path when repo not in repo_map)
          dest    = solution_controller.get_provisioner_path(deployment_service, build_path, prov)

        In dry_run mode only logs the planned copy; no files are written.
        """
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return True  # Nothing to copy — workspace not loaded

        provisioners = workspace_service.model.spec.provisioners or []
        deployment_build_path = deployment_service.get_build_path(build_path)
        template_context = self._build_template_context(deployment_service)

        for prov in provisioners:
            if prov.provisioner != ProvisionerType.BICEP:
                continue

            source = prov.source
            if source.source_path is None:
                self._errors.append(
                    f"Provisioner '{prov.name}' has no source_path — bicep provisioners must declare a source_path."
                )
                return False

            repo_name = str(source.repository) if source.repository else ""

            # Resolve repository root: use repo_map when available, fall back to work_path
            if repo_map and repo_name and repo_name in repo_map:
                repo_root = Path(repo_map[repo_name])
            else:
                repo_root = work_path

            src_dir = repo_root / source.source_path
            dest_dir = (
                solution_controller.get_provisioner_path(deployment_service, build_path, prov)
                if solution_controller is not None
                else deployment_build_path / (source.target_path or source.source_path)
            )

            # When source.reference is set, extract from the pinned ref using git archive
            # instead of copying from the (potentially different) working tree checkout.
            # SourceModel.reference is generic (valid for any git-based source), not
            # Terraform-specific — bicep honors it too (ADR-0071).
            if source.reference:
                if dry_run:
                    self._messages.append(
                        f"[DRY-RUN] Would extract bicep source at ref '{source.reference}': "
                        f"{repo_root}/{source.source_path} -> {dest_dir}"
                    )
                    continue

                ok, msg = self._extract_source_at_ref(
                    repo_root=repo_root,
                    source_path=source.source_path,
                    ref=source.reference,
                    dest_dir=dest_dir,
                    provisioner_name=prov.name,
                )
                if not ok:
                    self._errors.append(msg)
                    return False
                self._apply_templates_to_dir(dest_dir, template_context)
                self._messages.append(
                    f"Extracted bicep source at ref '{source.reference}': "
                    f"{repo_root}/{source.source_path} -> {dest_dir}"
                )
                continue

            if dry_run:
                self._messages.append(f"[DRY-RUN] Would copy bicep source: {src_dir} -> {dest_dir}")
                if not src_dir.exists():
                    self._errors.append(f"Bicep source directory not found: {src_dir} (provisioner: {prov.name})")
                    return False
                continue

            if not src_dir.exists():
                self._errors.append(f"Bicep source directory not found: {src_dir} (provisioner: {prov.name})")
                return False

            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            self._apply_templates_to_dir(dest_dir, template_context)
            self._messages.append(f"Copied bicep source: {src_dir} -> {dest_dir}")

        return True
