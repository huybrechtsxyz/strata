"""Build Helm artifacts from namespace module definitions.

For each namespace that contains at least one module with ``spec.type: helm``,
this builder writes per-module output files to::

    {build_path}/{namespace}/{module}/values.yaml
    {build_path}/{namespace}/{module}/meta.yaml

Security: this builder never writes resolved secret or variable values.
Secrets and variable/feature references are emitted as ``${KEY}`` substitution
tokens.  The deployer injects real values via ``--set`` flags at deploy time.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import yaml

from strata.builders.base_builder import BaseBuilder
from strata.models.common_models import ServiceDeployerType
from strata.models.module_model import ModuleServiceModel
from strata.services.deployment_service import DeploymentService
from strata.services.module_service import ModuleService
from strata.utils.system import resolve_path

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class HelmBuilder(BaseBuilder):
    """Builder that generates ``values.yaml`` and ``meta.yaml`` files for each
    Helm module.

    One values file and one meta file are created per Helm module.  Service
    names are prefixed with the module name (``{module}-{service}``); the
    prefix is omitted when the module name equals the service name.
    """

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Generate values.yaml and meta.yaml for each namespace with helm modules.

        Args:
            deployment_service: Fully-loaded deployment service.
            work_path: Workspace root directory used to resolve module file refs.
            build_path: Root build output directory.
            dry_run: When True, skip writing output files.
            solution_controller: Optional controller for canonical path helpers.

        Returns:
            bool: True on success, False on failure.
        """
        try:
            namespace_services = deployment_service.get_namespace_services() or {}

            if not namespace_services:
                if self.verbose:
                    self._messages.append("No namespaces found — skipping helm build")
                return True

            deployment_build_path = deployment_service.get_build_path(build_path)

            for ns_name, ns_service in namespace_services.items():
                if not ns_service.is_validated() or not ns_service.model:
                    continue

                ok = self._build_namespace(
                    namespace_name=str(ns_name),
                    ns_service=ns_service,
                    work_path=work_path,
                    deployment_build_path=deployment_build_path,
                    dry_run=dry_run,
                )
                if not ok:
                    return False

            return True

        except Exception as exc:
            msg = f"Helm build failed: {exc}"
            self.logger.exception("Helm build failed", error=str(exc))
            self._errors.append(msg)
            return False

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Pre-build validation: verify that the deployment service is ready.

        Args:
            deployment_service: Deployment service to validate.
            work_path: Working directory path.
            build_path: Build output directory path.
            solution_controller: Optional solution controller.

        Returns:
            bool: True on success, False on failure.
        """
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            self._errors.append("Workspace service is not available")
            return False

        if self.verbose:
            self._messages.append("Helm pre-build validation passed")

        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Post-build hook.  A workspace with no helm modules is valid.

        Args:
            deployment_service: Deployment service.
            work_path: Working directory path.
            build_path: Build output directory path.
            dry_run: When True, skip output file existence checks.
            solution_controller: Optional solution controller.

        Returns:
            bool: Always True — absence of helm files is not an error.
        """
        if dry_run and self.verbose:
            self._messages.append("[DRY-RUN] Skipping helm output file check")
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_namespace(
        self,
        namespace_name: str,
        ns_service: Any,
        work_path: Path,
        deployment_build_path: Path,
        dry_run: bool,
    ) -> bool:
        """Build values.yaml and meta.yaml for all helm modules in one namespace.

        Iterates all modules referenced by the namespace, loads each one, and
        writes per-module output for modules whose ``spec.type`` is ``helm``.

        Args:
            namespace_name: Name of the namespace.
            ns_service: NamespaceService instance.
            work_path: Workspace root for resolving module file references.
            deployment_build_path: Resolved build output directory.
            dry_run: When True, skip file I/O.

        Returns:
            bool: True on success, False on failure.
        """
        modules = ns_service.model.spec.modules
        if not modules:
            return True

        for module_ref in modules:
            try:
                module_path = resolve_path(str(work_path), module_ref.file)
            except (ValueError, Exception) as exc:
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_ref.name}': "
                    f"cannot resolve file '{module_ref.file}': {exc}"
                )
                return False

            if not module_path.exists():
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_ref.name}': file not found: '{module_path}'"
                )
                return False

            mod_service = ModuleService.load(str(module_path), validate=True)
            if not mod_service.is_validated() or not mod_service.model:
                errs = mod_service.get_validation_errors()
                self._errors.append(
                    f"Namespace '{namespace_name}', module '{module_ref.name}': validation failed: {'; '.join(errs)}"
                )
                return False

            module = mod_service.model
            if module.spec.type != ServiceDeployerType.HELM:
                # Not a helm module — skip silently
                continue

            if not module.spec.services:
                # Helm module declared but no services — skip
                continue

            module_name = str(module.meta.name)
            values_doc, meta_doc = self._render_module_artifacts(
                namespace_name=namespace_name,
                module_name=module_name,
                services=module.spec.services,
                release_name=module.spec.release_name,
                kubernetes_namespace=module.spec.kubernetes_namespace,
            )

            module_dir = deployment_build_path / namespace_name / module_name
            values_path = module_dir / "values.yaml"
            meta_path = module_dir / "meta.yaml"

            if dry_run:
                if self.verbose:
                    self._messages.append(f"[DRY-RUN] Would write: {values_path}")
                    self._messages.append(f"[DRY-RUN] Would write: {meta_path}")
                continue

            module_dir.mkdir(parents=True, exist_ok=True)

            try:
                with values_path.open("w", encoding="utf-8") as fh:
                    yaml.dump(
                        values_doc,
                        fh,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
            except OSError as exc:
                self._errors.append(f"Failed to write '{values_path}': {exc}")
                return False

            try:
                with meta_path.open("w", encoding="utf-8") as fh:
                    yaml.dump(
                        meta_doc,
                        fh,
                        default_flow_style=False,
                        sort_keys=False,
                        allow_unicode=True,
                    )
            except OSError as exc:
                self._errors.append(f"Failed to write '{meta_path}': {exc}")
                return False

            if self.verbose:
                self._messages.append(f"Wrote helm values: {values_path}")
                self._messages.append(f"Wrote helm meta: {meta_path}")

        return True

    def _render_module_artifacts(
        self,
        namespace_name: str,
        module_name: str,
        services: List[ModuleServiceModel],
        release_name: Optional[str],
        kubernetes_namespace: Optional[str],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Render values.yaml and meta.yaml dicts for a helm module.

        Args:
            namespace_name: Namespace name (used as default Kubernetes namespace).
            module_name: Module name (used for service key prefix and release name default).
            services: List of ``ModuleServiceModel`` instances.
            release_name: Optional override for the Helm release name.
            kubernetes_namespace: Optional override for the Kubernetes namespace.

        Returns:
            Tuple of (values_doc dict, meta_doc dict).
        """

        def prefixed(svc_name: str) -> str:
            """Prefix service name with module name unless they are the same."""
            return svc_name if svc_name == module_name else f"{module_name}-{svc_name}"

        values_doc: Dict[str, Any] = {}

        for svc in services:
            svc_name = str(svc.name)
            entry_name = prefixed(svc_name)
            entry: Dict[str, Any] = {}

            # Environment variables — never write resolved secret/variable values
            if svc.environment:
                env_dict: Dict[str, str] = {}
                for env in svc.environment:
                    if env.value is not None:
                        env_dict[env.key] = env.value
                    elif env.var is not None:
                        env_dict[env.key] = f"${{{env.var}}}"
                    elif env.secret is not None:
                        env_dict[env.key] = f"${{{env.secret}}}"
                    elif env.feature is not None:
                        env_dict[env.key] = f"${{{env.feature}}}"
                if env_dict:
                    entry["env"] = env_dict

            # PersistentVolumeClaim entries — only for mounts with storage_class set
            if svc.mounts:
                persistence: Dict[str, Any] = {}
                for mount in svc.mounts:
                    if mount.storage_class is not None:
                        pvc_key = str(mount.name) if mount.name is not None else "data"
                        pvc_entry: Dict[str, Any] = {"storageClass": mount.storage_class}
                        pvc_entry["accessMode"] = mount.access_mode or "ReadWriteOnce"
                        if mount.storage_size:
                            pvc_entry["size"] = mount.storage_size
                        persistence[pvc_key] = pvc_entry
                if persistence:
                    entry["persistence"] = persistence

            # Merge service-level configuration overrides verbatim (last wins)
            if svc.configuration:
                entry.update(svc.configuration)

            values_doc[entry_name] = entry

        meta_doc: Dict[str, Any] = {
            "releaseName": release_name if release_name else module_name,
            "namespace": kubernetes_namespace if kubernetes_namespace else namespace_name,
        }

        return values_doc, meta_doc
