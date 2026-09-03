"""Build Docker Compose artifacts from namespace module definitions.

For each namespace that contains at least one module with ``spec.type: compose``,
this builder writes a ``docker-compose.yml`` to::

    {build_path}/{namespace}/docker-compose.yml

Security: this builder never writes resolved secret or variable values.
Secrets and variable/feature references are emitted as ``${KEY}`` substitution
tokens.  The deployer injects real values via a ``.env`` file at deploy time.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

import yaml

from strata.builders.base_builder import BaseBuilder
from strata.models.common_models import ServiceDeployerType
from strata.models.module_model import ModuleCheckModel, ModuleServiceModel
from strata.services.deployment_service import DeploymentService
from strata.services.module_service import ModuleService
from strata.utils.system import resolve_path

if TYPE_CHECKING:
    from strata.controllers.solution_controller import SolutionController


class ComposeBuilder(BaseBuilder):
    """Builder that generates ``docker-compose.yml`` files for each namespace.

    One compose file is created per namespace.  All modules in the namespace
    whose ``spec.type`` is ``compose`` are merged into a single file.
    Service names are prefixed with the module name (``{module}-{service}``);
    the prefix is omitted when the module name equals the service name.
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
        """Generate docker-compose.yml for each namespace with compose modules.

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
                    self._messages.append("No namespaces found — skipping compose build")
                return True

            deployment_build_path = deployment_service.get_build_path(build_path)
            template_context = self._build_template_context(deployment_service)

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

                ok = self._copy_namespace_module_files(
                    namespace_name=str(ns_name),
                    ns_service=ns_service,
                    work_path=work_path,
                    deployment_build_path=deployment_build_path,
                    template_context=template_context,
                    dry_run=dry_run,
                )
                if not ok:
                    return False

            return True

        except Exception as exc:
            msg = f"Compose build failed: {exc}"
            self.logger.exception("Compose build failed", error=str(exc))
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
            self._messages.append("Compose pre-build validation passed")

        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        solution_controller: Optional["SolutionController"] = None,
    ) -> bool:
        """Post-build hook.  A workspace with no compose modules is valid.

        Args:
            deployment_service: Deployment service.
            work_path: Working directory path.
            build_path: Build output directory path.
            dry_run: When True, skip output file existence checks.
            solution_controller: Optional solution controller.

        Returns:
            bool: Always True — absence of compose files is not an error.
        """
        if dry_run and self.verbose:
            self._messages.append("[DRY-RUN] Skipping compose output file check")
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
        """Build docker-compose.yml for one namespace.

        Iterates all modules referenced by the namespace, loads each one, and
        collects compose services from modules whose ``spec.type`` is ``compose``.
        Writes (or dry-runs) the resulting file.

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

        compose_services: Dict[str, Any] = {}
        top_volumes: Dict[str, Any] = {}
        # Pass-through mode: resolved path to an externally-supplied compose file.
        # Exactly one per namespace; cannot be mixed with generative services.
        passthrough_file: Optional[Path] = None
        passthrough_module: Optional[str] = None

        # First pass: load all compose modules and build a registry of
        # module_name → set(service_names) for cross-module depends_on validation.
        loaded_modules: List[Tuple[str, Any]] = []  # (module_name, module_model)
        namespace_service_registry: Dict[str, set] = {}

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
            if module.spec.type != ServiceDeployerType.COMPOSE:
                # Not a compose module — skip silently
                continue

            module_name = str(module.meta.name)
            loaded_modules.append((module_name, module))

            # Register service names for this module
            if module.spec.services:
                namespace_service_registry[module_name] = {str(s.name) for s in module.spec.services}

        # Second pass: render compose output from loaded modules
        for module_name, module in loaded_modules:
            # Pass-through: explicit external compose file reference
            if module.spec.compose_file:
                if passthrough_file is not None:
                    self._errors.append(
                        f"Namespace '{namespace_name}': only one compose_file per namespace is allowed "
                        f"('{passthrough_module}' and '{module_name}' both set compose_file)."
                    )
                    return False
                try:
                    resolved = resolve_path(str(work_path), module.spec.compose_file)
                except (ValueError, Exception) as exc:
                    self._errors.append(
                        f"Namespace '{namespace_name}', module '{module_name}': "
                        f"cannot resolve compose_file '{module.spec.compose_file}': {exc}"
                    )
                    return False
                if not resolved.exists():
                    self._errors.append(
                        f"Namespace '{namespace_name}', module '{module_name}': compose_file not found: '{resolved}'"
                    )
                    return False
                passthrough_file = resolved
                passthrough_module = module_name
                continue

            # Generative: build from spec.services
            if not module.spec.services:
                # Compose module declared but no services and no compose_file — skip
                continue

            svc_entries, vol_entries, xmod_errors = self._render_module_services(
                namespace_name=namespace_name,
                module_name=module_name,
                services=module.spec.services,
                namespace_service_registry=namespace_service_registry,
            )
            if xmod_errors:
                self._errors.extend(xmod_errors)
                return False
            compose_services.update(svc_entries)
            top_volumes.update(vol_entries)

        # Cannot mix pass-through and generative in the same namespace
        if passthrough_file is not None and compose_services:
            self._errors.append(
                f"Namespace '{namespace_name}': cannot mix compose_file (pass-through) and "
                f"spec.services (generative) in the same namespace."
            )
            return False

        # Pass-through: copy the external compose file verbatim
        if passthrough_file is not None:
            ns_path = deployment_build_path / namespace_name / "docker-compose.yml"
            if dry_run:
                if self.verbose:
                    self._messages.append(f"[DRY-RUN] Would copy: {passthrough_file} → {ns_path}")
                return True
            ns_dir = deployment_build_path / namespace_name
            ns_dir.mkdir(parents=True, exist_ok=True)
            try:
                import shutil

                shutil.copy2(passthrough_file, ns_path)
            except OSError as exc:
                self._errors.append(f"Failed to copy '{passthrough_file}' to '{ns_path}': {exc}")
                return False
            if self.verbose:
                self._messages.append(f"Copied compose file: {passthrough_file} → {ns_path}")
            return True

        if not compose_services:
            # No compose modules in this namespace — nothing to write
            return True

        compose_doc: Dict[str, Any] = {"services": compose_services}
        if top_volumes:
            compose_doc["volumes"] = top_volumes

        if dry_run:
            ns_path = deployment_build_path / namespace_name / "docker-compose.yml"
            if self.verbose:
                self._messages.append(f"[DRY-RUN] Would write: {ns_path}")
            return True

        ns_dir = deployment_build_path / namespace_name
        ns_dir.mkdir(parents=True, exist_ok=True)
        compose_path = ns_dir / "docker-compose.yml"

        try:
            with compose_path.open("w", encoding="utf-8") as fh:
                yaml.dump(
                    compose_doc,
                    fh,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                )
        except OSError as exc:
            self._errors.append(f"Failed to write '{compose_path}': {exc}")
            return False

        if self.verbose:
            self._messages.append(f"Wrote compose file: {compose_path}")

        return True

    def _copy_namespace_module_files(
        self,
        namespace_name: str,
        ns_service: Any,
        work_path: Path,
        deployment_build_path: Path,
        template_context: Dict[str, Any],
        dry_run: bool,
    ) -> bool:
        """Copy ``spec.files`` entries for all compose modules in one namespace.

        Output lands in ``{build}/{namespace}/{module}/`` alongside the compose file.

        Args:
            namespace_name: Name of the namespace.
            ns_service: NamespaceService instance.
            work_path: Workspace root for resolving file references.
            deployment_build_path: Resolved build output directory.
            template_context: STRATA_* substitution variables.
            dry_run: When True, log what would happen but skip file I/O.

        Returns:
            bool: True on success, False on failure.
        """
        modules = ns_service.model.spec.modules
        if not modules:
            return True

        for module_ref in modules:
            try:
                module_path = resolve_path(str(work_path), module_ref.file)
            except (ValueError, Exception):
                continue  # already reported by _build_namespace
            if not module_path.exists():
                continue

            mod_service = ModuleService.load(str(module_path), validate=True)
            if not mod_service.is_validated() or not mod_service.model:
                continue

            module = mod_service.model
            if module.spec.type != ServiceDeployerType.COMPOSE:
                continue
            if not module.spec.files:
                continue

            module_name = str(module.meta.name)
            dest_dir = deployment_build_path / namespace_name / module_name
            label = f"Namespace '{namespace_name}', module '{module_name}'"

            if not self._copy_module_files(
                files=module.spec.files,
                work_path=work_path,
                dest_dir=dest_dir,
                template_context=template_context,
                module_label=label,
                dry_run=dry_run,
            ):
                return False

        return True

    def _render_module_services(
        self,
        namespace_name: str,
        module_name: str,
        services: List[ModuleServiceModel],
        namespace_service_registry: Optional[Dict[str, set]] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[str]]:
        """Render all services for a module into Docker Compose service entries.

        Args:
            namespace_name: Namespace name (used for volume key prefix).
            module_name: Module name (used for service name prefix).
            services: List of ``ModuleServiceModel`` instances.
            namespace_service_registry: Map of module_name → set(service_names)
                for all compose modules in this namespace.  Used to validate and
                resolve cross-module ``@module/service`` depends_on refs.

        Returns:
            Tuple of (compose_services dict, top_level_volumes dict, list of errors).
        """
        registry = namespace_service_registry or {}
        service_names = {str(s.name) for s in services}
        errors: List[str] = []

        def prefixed(svc_name: str, mod: str = module_name) -> str:
            """Prefix service name with module name unless they are the same."""
            return svc_name if svc_name == mod else f"{mod}-{svc_name}"

        def resolve_dep(dep: str) -> Optional[str]:
            """Resolve a depends_on entry to its prefixed compose service name.

            Returns the resolved name, or None if an error was recorded.
            """
            # NOTE: this @ is the @module/service cross-module dependency
            # convention — unrelated to the @repo_name/... cross-repo file
            # reference convention (ADR-0073); do not swap this for
            # is_cross_repo_ref()/resolve_path().
            if not dep.startswith("@"):
                # Intra-module: rewrite to prefixed form
                return prefixed(dep) if dep in service_names else dep

            ref = dep[1:]
            parts = ref.split("/", 1)
            target_module = parts[0]
            target_service = parts[1] if len(parts) > 1 else target_module

            if target_module not in registry:
                errors.append(
                    f"Namespace '{namespace_name}', module '{module_name}': "
                    f"depends_on '{dep}' references module '{target_module}' "
                    f"which is not a compose module in this namespace. "
                    f"Available modules: {sorted(registry.keys())}."
                )
                return None

            if target_service not in registry[target_module]:
                errors.append(
                    f"Namespace '{namespace_name}', module '{module_name}': "
                    f"depends_on '{dep}' references service '{target_service}' "
                    f"which does not exist in module '{target_module}'. "
                    f"Available services: {sorted(registry[target_module])}."
                )
                return None

            return prefixed(target_service, mod=target_module)

        compose_services: Dict[str, Any] = {}
        top_volumes: Dict[str, Any] = {}

        for svc in services:
            svc_name = str(svc.name)
            entry_name = prefixed(svc_name)
            entry: Dict[str, Any] = {}

            if svc.image:
                entry["image"] = svc.image

            if svc.command:
                entry["command"] = svc.command

            if svc.restart:
                entry["restart"] = svc.restart

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
                    entry["environment"] = env_dict

            # Port mappings
            if svc.ports:
                entry["ports"] = list(svc.ports)

            # Volume and bind mounts
            if svc.mounts:
                vol_list: List[str] = []
                for mount in svc.mounts:
                    target = mount.target_path or "/data"
                    if mount.volume_ref is not None:
                        vol_key = f"{namespace_name}_{module_name}_{mount.volume_ref}"
                        top_volumes[vol_key] = {}
                        vol_list.append(f"{vol_key}:{target}")
                    elif mount.source_path is not None:
                        vol_list.append(f"{mount.source_path}:{target}")
                if vol_list:
                    entry["volumes"] = vol_list

            # depends_on — resolve intra-module and cross-module (@module/service) refs
            if svc.depends_on:
                resolved_deps = []
                for dep in svc.depends_on:
                    resolved = resolve_dep(dep)
                    if resolved is None:
                        # Error already recorded by resolve_dep
                        continue
                    resolved_deps.append(resolved)
                if errors:
                    # Bail early — return partial results with errors
                    return compose_services, top_volumes, errors
                entry["depends_on"] = resolved_deps

            # Healthcheck
            if svc.healthcheck:
                hc = self._render_healthcheck(svc.healthcheck)
                if hc:
                    entry["healthcheck"] = hc

            # Merge service-level configuration overrides verbatim (last wins)
            if svc.configuration:
                entry.update(svc.configuration)

            compose_services[entry_name] = entry

        return compose_services, top_volumes, errors

    def _render_healthcheck(self, check: ModuleCheckModel) -> Dict[str, Any]:
        """Render a ``ModuleCheckModel`` into a Docker Compose healthcheck block.

        Args:
            check: Health check model.

        Returns:
            Dict with Docker Compose healthcheck keys, or empty dict if no
            useful test command can be derived.
        """
        hc: Dict[str, Any] = {}

        if check.command:
            hc["test"] = ["CMD", *check.command]
        elif check.type == "http" and check.target:
            hc["test"] = ["CMD-SHELL", f"curl -sf {check.target} || exit 1"]
        elif check.type == "tcp" and check.target:
            hc["test"] = ["CMD-SHELL", f"nc -z {check.target} || exit 1"]

        if check.interval:
            hc["interval"] = check.interval
        if check.timeout:
            hc["timeout"] = check.timeout
        if check.retries is not None:
            hc["retries"] = check.retries

        return hc
