"""Build Terraform tfvars artifacts from the generated platform model.

Security: this builder NEVER writes resolved variable/feature/secret values.
It only documents required keys.
"""

import json
import shutil
from glob import glob
from pathlib import Path
from typing import Any, Dict, List, Optional

from strata.builders.base_builder import BaseBuilder
from strata.models.environment_model import IncludeMergeStrategy
from strata.models.platform_artifact_model import PlatformArtifactModel
from strata.services.deployment_service import DeploymentService
from strata.services.platform_artifact_service import PlatformService
from strata.utils.terraform_loader import TerraformLoader


class TerraformBuilder(BaseBuilder):
    """Builder for Terraform tfvars generation."""

    def __init__(self, verbose: bool = False) -> None:
        super().__init__(verbose=verbose)

        # Requirement tracking
        self.variable_refs: Dict[str, Dict[str, Any]] = {}
        self.feature_refs: Dict[str, Dict[str, Any]] = {}
        self.secret_refs: Dict[str, Dict[str, Any]] = {}

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
        platform_model: Optional["PlatformArtifactModel"] = None,
        repo_map: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Build Terraform tfvars files from ``platform.json``.

        Args:
            deployment_service: Loaded deployment service
            work_path: Working directory path
            build_path: Build output directory path
            dry_run: When True, build all vars in memory but skip writing
                output files.  A summary of planned outputs is emitted.
            platform_model: Pre-assembled PlatformModel (used in dry-run so
                that the on-disk ``platform.json`` is not required).
            repo_map: Repository name → absolute path mapping for @repo/ resolution.

        Returns:
            bool: True on success, False on failure.
        """
        try:
            self.variable_refs = {}
            self.feature_refs = {}
            self.secret_refs = {}

            deployment_build_path = deployment_service.get_build_path(build_path)

            if platform_model is not None:
                # Caller supplied the model directly (e.g. during dry-run)
                if self.verbose:
                    model_name = (
                        platform_model.meta.name
                        if platform_model and getattr(platform_model, "meta", None)
                        else "<unknown>"
                    )
                    self._messages.append(f"Using pre-assembled platform model: {model_name}")
            else:
                platform_path = deployment_build_path / "platform.json"

                if not platform_path.exists():
                    self._errors.append("Platform model not found. Run platform build first.")
                    return False

                platform_service = PlatformService.load(str(platform_path), validate=True)
                if not platform_service.is_validated() or not platform_service.model:
                    self._errors.append("Platform model validation failed")
                    return False

                platform_model = platform_service.model
                if self.verbose and platform_model and getattr(platform_model, "meta", None):
                    self._messages.append(f"Loaded platform model: {platform_model.meta.name}")

            if platform_model is None:
                error_msg = "Platform model is None after loading"
                self.logger.error("Platform model is None after loading")
                self._errors.append(error_msg)
                return False

            terraform_vars = self._build_terraform_vars(platform_model, deployment_service, [])

            if dry_run:
                terraform_path = deployment_build_path / "terraform"
                planned = [
                    "workspace.auto.tfvars.json",
                    "providers.auto.tfvars.json",
                    "topologies.auto.tfvars.json",
                    "modules.auto.tfvars.json",
                    "namespaces.auto.tfvars.json",
                    "firewalls.auto.tfvars.json",
                    "tf_required_variables.json",
                    "tf_required_features.json",
                    "tf_required_secrets.json",
                ]
                for resource_type in terraform_vars.get("resources_by_category", {}):
                    planned.append(f"resx_{resource_type}.auto.tfvars.json")
                for filename in planned:
                    self._messages.append(f"[DRY-RUN] Would write: {terraform_path / filename}")
                self._messages.append(f"[DRY-RUN] Planned {len(planned)} Terraform artifact file(s)")
                variables_count = len(terraform_vars.get("required_variables", {}).get("variables", []))
                features_count = len(terraform_vars.get("required_features", {}).get("features", []))
                secrets_count = len(terraform_vars.get("required_secrets", {}).get("secrets", []))
                if variables_count or features_count or secrets_count:
                    self._messages.append(
                        f"[DRY-RUN] Requires: {variables_count} variable(s), "
                        f"{features_count} feature(s), {secrets_count} secret(s)"
                    )

                # Validate source copy in dry-run mode (no writes)
                copy_ok = self._copy_provisioner_source(
                    deployment_service=deployment_service,
                    build_path=build_path,
                    work_path=work_path,
                    repo_map=repo_map or {},
                    dry_run=True,
                )
                if not copy_ok:
                    return False

                # Validate includes in dry-run mode (no writes)
                include_ok = self._process_includes(
                    deployment_service=deployment_service,
                    build_path=build_path,
                    work_path=work_path,
                    repo_map=repo_map or {},
                    dry_run=True,
                )
                if not include_ok:
                    return False

                return True

            self._messages.extend(self._save_terraform_vars(terraform_vars, deployment_service, build_path))

            # Copy terraform source files from the provisioner source_path into the build
            copy_ok = self._copy_provisioner_source(
                deployment_service=deployment_service,
                build_path=build_path,
                work_path=work_path,
                repo_map=repo_map or {},
                dry_run=False,
            )
            if not copy_ok:
                return False

            # Process environment includes (merge/override .tf/.tfvars files on top)
            include_ok = self._process_includes(
                deployment_service=deployment_service,
                build_path=build_path,
                work_path=work_path,
                repo_map=repo_map or {},
                dry_run=False,
            )
            if not include_ok:
                return False

            return True

        except Exception as exc:
            error_msg = f"Failed to build Terraform artifacts: {exc}"
            self.logger.exception("Failed to build Terraform artifacts", error=str(exc))
            self._errors.append(error_msg)
            return False

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """Hook executed before build starts."""
        if not deployment_service.is_validated():
            self._errors.append("Deployment service is not validated")
            return False

        if not dry_run:
            deployment_build_path = deployment_service.get_build_path(build_path)
            platform_path = deployment_build_path / "platform.json"

            if not platform_path.exists():
                self._errors.append(f"Platform model not found at: {platform_path}. Run platform build first.")
                return False

        if self.verbose:
            self._messages.append("Pre-build validation passed for Terraform artifacts")

        return True

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> bool:
        """Hook executed after build completes."""
        if dry_run:
            if self.verbose:
                self._messages.append("[DRY-RUN] Skipping Terraform artifact file-existence check")
            return True

        deployment_build_path = deployment_service.get_build_path(build_path)
        terraform_path = deployment_build_path / "terraform"

        base_files = [
            "workspace.auto.tfvars.json",
            "providers.auto.tfvars.json",
            "topologies.auto.tfvars.json",
            "modules.auto.tfvars.json",
            "namespaces.auto.tfvars.json",
            "firewalls.auto.tfvars.json",
            "tf_required_variables.json",
            "tf_required_features.json",
            "tf_required_secrets.json",
        ]

        missing = [f for f in base_files if not (terraform_path / f).exists()]
        if missing:
            self._errors.append(f"Terraform artifact files missing: {', '.join(missing)}")
            return False

        type_files = list(terraform_path.glob("resx_*.auto.tfvars.json"))
        if self.verbose:
            self._messages.append(f"Terraform artifacts created at: {terraform_path}")
            self._messages.append(f"Generated {len(base_files)} base files + {len(type_files)} type files")

        return True

    def _build_terraform_vars(
        self,
        platform: PlatformArtifactModel,
        deployment_service: DeploymentService,
        messages: List[str],
    ) -> Dict[str, Any]:
        """Build all Terraform tfvars payloads."""
        # Collect environment-declared variables and secrets
        self._collect_environment_variables(deployment_service)
        self._collect_environment_secrets(deployment_service)
        return {
            "workspace": self._build_workspace_vars(platform, messages),
            "providers": self._build_provider_vars(platform, messages),
            "topologies": self._build_topology_vars(platform, messages),
            "resources_by_category": self._build_resources_by_category(platform, deployment_service, messages),
            "modules": self._build_module_vars(platform, messages),
            "namespaces": self._build_namespace_vars(platform, messages),
            "firewalls": self._build_firewall_vars(platform, messages),
            "required_variables": self._document_required_variables(),
            "required_features": self._document_required_features(),
            "required_secrets": self._document_required_secrets(),
        }

    def _build_workspace_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build workspace-level tfvars payload."""
        workspace = platform.spec.workspace
        workspace_labels = workspace.labels or {}
        deployment_labels = platform.meta.labels or {}

        workspace_version = workspace_labels.get("version", "1.0.0")
        deployment_version = deployment_labels.get("version", workspace_version)
        environment = deployment_labels.get("environment", "production")

        payload = {
            "workspace_name": workspace.name,
            "workspace_version": workspace_version,
            "deployment_name": platform.meta.name,
            "environment": environment,
            "platform_version": getattr(platform.apiVersion, "value", platform.apiVersion),
            "labels": workspace_labels,
            "metadata": {
                "deployment_version": deployment_version,
                "workspace_description": (
                    workspace.annotations.get("description", "") if workspace.annotations else ""
                ),
                "deployment_description": (
                    platform.meta.annotations.get("description", "") if platform.meta.annotations else ""
                ),
                "workspace_tags": workspace.tags or [],
                "deployment_tags": platform.meta.tags or [],
            },
        }

        if self.verbose:
            messages.append(f"Built workspace vars: {workspace.name}")

        return payload

    def _build_provider_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build provider tfvars payload."""
        providers_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.providers:
            for provider in platform.spec.providers:
                providers_dict[provider.name] = {
                    "type": provider.properties.type,
                    "region": provider.properties.region,
                    "engine": provider.properties.engine,
                    "version": provider.properties.version,
                    "description": provider.description,
                    "labels": provider.labels or {},
                    "tags": provider.tags or [],
                }

                if provider.references:
                    for key in provider.references.variables or []:
                        self._track_variable(
                            key,
                            f"Variable referenced by provider {provider.name}",
                            [provider.name],
                        )
                    for key in provider.references.features or []:
                        self._track_feature(
                            key,
                            f"Feature referenced by provider {provider.name}",
                            [provider.name],
                        )
                    for key in provider.references.secrets or []:
                        self._track_secret(
                            key,
                            f"Secret referenced by provider {provider.name}",
                            [provider.name],
                        )

        if self.verbose:
            messages.append(f"Built provider vars: {len(providers_dict)} providers")

        return {"platform_providers": providers_dict}

    def _build_resources_by_category(
        self,
        platform: PlatformArtifactModel,
        deployment_service: DeploymentService,
        messages: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Build resource tfvars grouped by resource type."""
        resources_by_category: Dict[str, Dict[str, Dict[str, Any]]] = {}

        if platform.spec.resources:
            for resource in platform.spec.resources:
                resource_type = (
                    resource.properties.resource_type.lower() if resource.properties.resource_type else "uncategorized"
                )

                if resource_type not in resources_by_category:
                    resources_by_category[resource_type] = {}

                resource_data: Dict[str, Any] = {
                    "type": resource.properties.resource_type,
                    "provider": resource.properties.provider_type,
                    "category": resource.properties.category,
                    "subcategory": resource.properties.subcategory,
                    "unit_cost": resource.properties.unit_cost,
                    "description": (resource.annotations.get("description", "") if resource.annotations else ""),
                    "labels": resource.labels or {},
                    "tags": resource.tags or [],
                }

                if resource.configuration:
                    resource_data["configuration"] = resource.configuration
                if resource.storage:
                    resource_data["storage"] = resource.storage.model_dump(exclude_none=True)
                if resource.firewalls:
                    resource_data["firewalls"] = resource.firewalls
                if resource.firewall:
                    resource_data["firewall"] = resource.firewall

                self._track_resource_requirements(resource)
                resources_by_category[resource_type][resource.name] = resource_data

        result: Dict[str, Dict[str, Any]] = {}
        for resource_type, resources_dict in resources_by_category.items():
            result[resource_type] = {"resources": resources_dict}
            if self.verbose:
                messages.append(f"Built {resource_type} resources: {len(resources_dict)} resources")

        return result

    def _build_module_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build module tfvars payload."""
        modules_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.modules:
            for module in platform.spec.modules:
                modules_dict[module.name] = {
                    "repository": module.source.repository,
                    "source_path": module.source.source_path,
                    "target_path": module.source.target_path,
                    "description": (module.annotations.get("description", "") if module.annotations else ""),
                    "labels": module.labels or {},
                    "tags": module.tags or [],
                    "properties": (module.properties.model_dump(exclude_none=True) if module.properties else {}),
                }

                if module.references:
                    for key in module.references.variables or []:
                        self._track_variable(
                            key,
                            f"Variable referenced by module {module.name}",
                            [module.name],
                        )
                    for key in module.references.features or []:
                        self._track_feature(
                            key,
                            f"Feature referenced by module {module.name}",
                            [module.name],
                        )
                    for key in module.references.secrets or []:
                        self._track_secret(
                            key,
                            f"Secret referenced by module {module.name}",
                            [module.name],
                        )

        return {"modules": modules_dict}

    def _build_topology_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build topology tfvars payload."""
        topologies_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.topologies:
            for topology in platform.spec.topologies:
                components = []
                for component in topology.components:
                    entry: Dict[str, Any] = {"resource": component.resource}
                    if getattr(component, "role", None):
                        entry["role"] = component.role
                    count = getattr(component, "count", 1)
                    entry["count"] = count
                    components.append(entry)

                volumes = []
                if topology.volumes:
                    for volume in topology.volumes:
                        volumes.append({"name": volume.name, "type": volume.type})

                topologies_dict[topology.name] = {
                    "type": topology.type,
                    "provider": topology.provider,
                    "provisioner": topology.provisioner.value,
                    "components": components,
                    "volumes": volumes,
                }

        return {"topologies": topologies_dict}

    def _build_namespace_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build namespace tfvars payload."""
        namespaces_dict: Dict[str, Any] = {}

        if platform.spec.namespaces:
            for namespace in platform.spec.namespaces:
                namespaces_dict[namespace.name] = {
                    "description": (namespace.annotations.get("description", "") if namespace.annotations else ""),
                    "labels": namespace.labels or {},
                    "tags": namespace.tags or [],
                    "modules": [str(m.module) for m in namespace.modules] if namespace.modules else [],
                }

        if self.verbose:
            messages.append(f"Built namespace vars: {len(namespaces_dict)} namespaces")

        return {"namespaces": namespaces_dict}

    def _build_firewall_vars(self, platform: PlatformArtifactModel, messages: List[str]) -> Dict[str, Any]:
        """Build firewall tfvars payload."""
        firewalls_dict: Dict[str, Any] = {}

        if platform.spec.firewalls:
            for firewall in platform.spec.firewalls:
                rules: Dict[str, Any] = {
                    "reset": firewall.reset or False,
                    "defaults": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.defaults]
                    if firewall.defaults
                    else [],
                    "deny": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.deny]
                    if firewall.deny
                    else [],
                    "allow": [r.model_dump(exclude_none=True, by_alias=True) for r in firewall.allow]
                    if firewall.allow
                    else [],
                }
                firewalls_dict[firewall.name] = {
                    "description": (firewall.annotations.get("description", "") if firewall.annotations else ""),
                    "labels": firewall.labels or {},
                    "tags": firewall.tags or [],
                    "rules": rules,
                }

        if self.verbose:
            messages.append(f"Built firewall vars: {len(firewalls_dict)} firewalls")

        return {"firewalls": firewalls_dict}

    def _document_required_variables(self) -> Dict[str, Any]:
        return {"variables": list(self.variable_refs.values())}

    def _document_required_features(self) -> Dict[str, Any]:
        return {"features": list(self.feature_refs.values())}

    def _document_required_secrets(self) -> Dict[str, Any]:
        return {"secrets": list(self.secret_refs.values())}

    def _save_terraform_vars(
        self,
        terraform_vars: Dict[str, Any],
        deployment_service: DeploymentService,
        build_path: Path,
    ) -> List[str]:
        """Write all Terraform payload files."""
        messages: List[str] = []

        try:
            deployment_build_path = deployment_service.get_build_path(build_path)
            terraform_path = deployment_build_path / "terraform"
            terraform_path.mkdir(parents=True, exist_ok=True)

            self._write_json(
                terraform_path / "workspace.auto.tfvars.json",
                terraform_vars["workspace"],
            )
            self._write_json(
                terraform_path / "providers.auto.tfvars.json",
                terraform_vars["providers"],
            )
            self._write_json(
                terraform_path / "topologies.auto.tfvars.json",
                terraform_vars["topologies"],
            )
            self._write_json(terraform_path / "modules.auto.tfvars.json", terraform_vars["modules"])
            self._write_json(terraform_path / "namespaces.auto.tfvars.json", terraform_vars["namespaces"])
            self._write_json(terraform_path / "firewalls.auto.tfvars.json", terraform_vars["firewalls"])

            for resource_type, payload in terraform_vars["resources_by_category"].items():
                self._write_json(terraform_path / f"resx_{resource_type}.auto.tfvars.json", payload)

            self._write_json(
                terraform_path / "tf_required_variables.json",
                terraform_vars["required_variables"],
            )
            self._write_json(
                terraform_path / "tf_required_features.json",
                terraform_vars["required_features"],
            )
            self._write_json(
                terraform_path / "tf_required_secrets.json",
                terraform_vars["required_secrets"],
            )

            messages.append(f"✓ Terraform artifacts saved to: {terraform_path}")

        except Exception as exc:
            error_msg = f"Failed to save Terraform artifacts: {exc}"
            self.logger.exception("Failed to save Terraform artifacts", error=str(exc))
            messages.append(error_msg)

        return messages

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # ------------------------------------------------------------------
    # Terraform provisioner source copy
    # ------------------------------------------------------------------

    def _copy_provisioner_source(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
    ) -> bool:
        """Copy terraform source files from each provisioner's source_path into the build.

        For each terraform provisioner declared in the workspace:
          source  = repo_root / source_path   (repo_root = work_path when repo not in repo_map)
          dest    = deployment_build_path / (target_path or source_path)

        In dry_run mode only logs the planned copy; no files are written.
        """
        workspace_service = deployment_service.get_workspace_service()
        if workspace_service is None or workspace_service.model is None:
            return True  # Nothing to copy — workspace not loaded

        provisioners = workspace_service.model.spec.provisioners or []
        deployment_build_path = deployment_service.get_build_path(build_path)

        for prov in provisioners:
            if prov.provisioner.value != "terraform":
                continue

            source = prov.source
            repo_name = str(source.repository)

            # Resolve repository root: use repo_map when available, fall back to work_path
            if repo_map and repo_name in repo_map:
                repo_root = Path(repo_map[repo_name])
            else:
                repo_root = work_path

            src_dir = repo_root / source.source_path
            dest_dir = deployment_build_path / (source.target_path or source.source_path)

            if dry_run:
                self._messages.append(f"[DRY-RUN] Would copy terraform source: {src_dir} → {dest_dir}")
                if not src_dir.exists():
                    self._errors.append(f"Terraform source directory not found: {src_dir} (provisioner: {prov.name})")
                    return False
                continue

            if not src_dir.exists():
                self._errors.append(f"Terraform source directory not found: {src_dir} (provisioner: {prov.name})")
                return False

            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src_dir, dest_dir, dirs_exist_ok=True)
            self._messages.append(f"Copied terraform source: {src_dir} → {dest_dir}")

        return True

    # ------------------------------------------------------------------
    # Terraform file includes (merge external .tf / .tfvars into build)
    # ------------------------------------------------------------------

    def _process_includes(
        self,
        deployment_service: DeploymentService,
        build_path: Path,
        work_path: Path,
        repo_map: Dict[str, str],
        dry_run: bool = False,
    ) -> bool:
        """Validate and execute terraform file includes from environment model.

        Collects includes from:
        - Environment-wide: spec.overrides.includes
        - Per-resource: spec.overrides.resources[].includes

        Phase 1: Validate all includes (resolve paths, check files exist).
        Phase 2: Execute merges/concatenations (skipped in dry_run mode).

        Returns:
            True on success, False if validation fails.
        """
        from strata.utils.system import resolve_path

        env_service = deployment_service.get_environment_service()
        if env_service is None or env_service.model is None:
            return True  # No environment — nothing to include

        overrides = getattr(env_service.model.spec, "overrides", None)
        if overrides is None:
            return True

        # Collect all include directives with their context
        include_tasks: List[Dict[str, Any]] = []

        # Environment-wide includes
        if overrides.includes:
            for inc in overrides.includes:
                include_tasks.append({"include": inc, "context": "environment-wide"})

        # Per-resource includes
        if overrides.resources:
            for res_override in overrides.resources:
                if res_override.includes:
                    for inc in res_override.includes:
                        include_tasks.append(
                            {
                                "include": inc,
                                "context": f"resource:{res_override.resource}",
                            }
                        )

        if not include_tasks:
            return True

        # Resolve output directory
        deployment_build_path = deployment_service.get_build_path(build_path)
        terraform_path = deployment_build_path / "terraform"

        # Phase 1: Validate all includes
        resolved_tasks: List[Dict[str, Any]] = []
        for task in include_tasks:
            inc = task["include"]
            context = task["context"]

            # Resolve source path(s)
            source_files: List[Path] = []
            try:
                resolved_source = resolve_path(str(work_path), inc.source, repo_map=repo_map)
                source_str = str(resolved_source)

                # Support glob patterns
                if "*" in source_str or "?" in source_str:
                    matched = sorted(glob(source_str))
                    if not matched and not inc.optional:
                        self._errors.append(f"Include source glob matched no files: {inc.source} ({context})")
                        return False
                    source_files = [Path(m) for m in matched]
                else:
                    if not resolved_source.exists():
                        if not inc.optional:
                            self._errors.append(
                                f"Include source not found: {inc.source} → {resolved_source} ({context})"
                            )
                            return False
                    else:
                        source_files = [resolved_source]

            except ValueError as exc:
                self._errors.append(f"Include source resolution error: {exc} ({context})")
                return False

            if not source_files:
                # Optional include with no matching files — skip
                self._messages.append(f"⊘ Include skipped (optional, not found): {inc.source} ({context})")
                continue

            # Validate target filename (no path traversal)
            target_path = terraform_path / inc.target
            try:
                target_path.resolve().relative_to(terraform_path.resolve())
            except ValueError:
                self._errors.append(f"Include target escapes terraform directory: {inc.target} ({context})")
                return False

            # Sort by order field if specified
            if inc.order is not None:
                sort_key = inc.order
            else:
                sort_key = len(resolved_tasks)

            resolved_tasks.append(
                {
                    "source_files": source_files,
                    "target": target_path,
                    "strategy": inc.strategy,
                    "context": context,
                    "order": sort_key,
                    "source_ref": inc.source,
                }
            )

        # Sort resolved tasks by order
        resolved_tasks.sort(key=lambda t: t["order"])

        # Dry-run reporting
        if dry_run:
            for task in resolved_tasks:
                strategy_label = task["strategy"].value
                src_count = len(task["source_files"])
                self._messages.append(
                    f"[DRY-RUN] Would {strategy_label} {src_count} file(s) → {task['target'].name} ({task['context']})"
                )
            if resolved_tasks:
                self._messages.append(f"[DRY-RUN] Planned {len(resolved_tasks)} include operation(s)")
            return True

        # Phase 2: Execute
        loader = TerraformLoader()
        for task in resolved_tasks:
            src_files: List[Path] = task["source_files"]
            target: Path = task["target"]
            strategy: IncludeMergeStrategy = task["strategy"]
            ctx: str = task["context"]
            source_ref: str = task["source_ref"]

            try:
                if strategy == IncludeMergeStrategy.MERGE:
                    result = loader.load_and_merge(src_files)
                    loader.write(result, target)
                elif strategy == IncludeMergeStrategy.CONCATENATE:
                    content = loader.concatenate(src_files)
                    loader.write_raw(content, target)

                self._messages.append(f"✓ Include {strategy.value}: {source_ref} → {target.name} ({ctx})")

            except Exception as exc:
                self._errors.append(f"Include {strategy.value} failed: {source_ref} → {target.name}: {exc} ({ctx})")
                return False

        return True

    def _track_resource_requirements(self, resource: Any) -> None:
        if not resource.references:
            return

        for key in resource.references.variables or []:
            self._track_variable(
                key,
                f"Variable referenced by resource {resource.name}",
                [resource.name],
            )

        for key in resource.references.features or []:
            self._track_feature(
                key,
                f"Feature referenced by resource {resource.name}",
                [resource.name],
            )

        for key in resource.references.secrets or []:
            self._track_secret(
                key,
                f"Secret referenced by resource {resource.name}",
                [resource.name],
            )

    def _track_variable(
        self,
        key: str,
        description: str,
        used_by: List[str],
        store: Optional[str] = None,
        value: Any = None,
    ) -> None:
        if key not in self.variable_refs:
            entry: Dict[str, Any] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "used_by": list(used_by),
            }
            if store is not None:
                entry["store"] = store
            if value is not None:
                entry["value"] = value
            self.variable_refs[key] = entry
        else:
            existing = self.variable_refs[key]
            merged = set(existing.get("used_by", [])) | set(used_by)
            existing["used_by"] = sorted(merged)

    def _track_feature(self, key: str, description: str, used_by: List[str]) -> None:
        if key not in self.feature_refs:
            self.feature_refs[key] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "used_by": list(used_by),
            }
        else:
            existing = self.feature_refs[key]
            merged = set(existing.get("used_by", [])) | set(used_by)
            existing["used_by"] = sorted(merged)

    def _collect_environment_variables(self, deployment_service: DeploymentService) -> None:
        """Track variables declared in the deployment's environment file."""
        try:
            env_service = deployment_service.get_environment_service()
        except Exception:
            return
        if env_service is None or env_service.model is None:
            return
        variables = env_service.model.spec.variables if env_service.model.spec else None
        if not variables:
            return
        env_name = env_service.get_name() or "environment"
        for variable in variables:
            self._track_variable(
                key=variable.key,
                description=variable.description or f"Variable from {env_name} ({variable.store.value})",
                used_by=[env_name],
                store=variable.store.value,
                value=variable.value,
            )

    def _collect_environment_secrets(self, deployment_service: DeploymentService) -> None:
        """Track secrets declared in the deployment's environment file."""
        try:
            env_service = deployment_service.get_environment_service()
        except Exception:
            return
        if env_service is None or env_service.model is None:
            return
        secrets = env_service.model.spec.secrets if env_service.model.spec else None
        if not secrets:
            return
        env_name = env_service.get_name() or "environment"
        for secret in secrets:
            self._track_secret(
                key=secret.key,
                description=secret.description or f"Secret from {env_name} ({secret.store.value})",
                used_by=[env_name],
            )

    def _track_secret(self, key: str, description: str, used_by: List[str]) -> None:
        if key not in self.secret_refs:
            self.secret_refs[key] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "suggested_key_vault_name": key.replace("_", "-"),
                "used_by": list(used_by),
            }
        else:
            existing = self.secret_refs[key]
            merged = set(existing.get("used_by", [])) | set(used_by)
            existing["used_by"] = sorted(merged)
