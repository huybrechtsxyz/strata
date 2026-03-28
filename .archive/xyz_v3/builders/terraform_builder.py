#!/usr/bin/env python3
"""
===============================================================================
Module Name   : terraform_builder.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Build Terraform tfvars artifacts from the generated platform model.

                Security: this builder NEVER writes resolved variable/feature/
                secret values. It only documents required keys.
===============================================================================
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from xyz_platform.builders.base_builder import BaseBuilder
from xyz_platform.logger import get_logger
from xyz_platform.models.platform_model import PlatformModel
from xyz_platform.services.deployment_service import DeploymentService
from xyz_platform.services.platform_service import PlatformService


class TerraformBuilder(BaseBuilder):
    """Builder for Terraform tfvars generation."""

    def __init__(self, verbose: bool = False):
        self.logger = get_logger(__name__)
        self.verbose = verbose

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
        platform_model: Optional["PlatformModel"] = None,
    ) -> Tuple[bool, List[str]]:
        """Build Terraform tfvars files from ``platform.json``.

        Args:
            deployment_service: Loaded deployment service
            work_path: Working directory path
            build_path: Build output directory path
            dry_run: When True, build all vars in memory but skip writing
                output files.  A summary of planned outputs is emitted.
            platform_model: Pre-assembled PlatformModel (used in dry-run so
                that the on-disk ``platform.json`` is not required).

        Returns:
            Tuple[bool, List[str]]: (success, messages)
        """
        messages: List[str] = []

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
                    messages.append(f"Using pre-assembled platform model: {model_name}")
            else:
                platform_path = deployment_build_path / "platform.json"

                if not platform_path.exists():
                    messages.append(
                        "Platform model not found. Run platform build first."
                    )
                    return False, messages

                platform_service = PlatformService.load(
                    str(platform_path), validate=True
                )
                if not platform_service.is_validated() or not platform_service.model:
                    messages.append("Platform model validation failed")
                    return False, messages

                platform_model = platform_service.model
                if (
                    self.verbose
                    and platform_model
                    and getattr(platform_model, "meta", None)
                ):
                    messages.append(
                        f"Loaded platform model: {platform_model.meta.name}"
                    )

            if platform_model is None:
                error_msg = "Platform model is None after loading"
                self.logger.error(error_msg)
                messages.append(error_msg)
                return False, messages

            terraform_vars = self._build_terraform_vars(
                platform_model, deployment_service, messages
            )

            if dry_run:
                terraform_path = deployment_build_path / "terraform"
                planned = [
                    "workspace.auto.tfvars.json",
                    "providers.auto.tfvars.json",
                    "topologies.auto.tfvars.json",
                    "modules.auto.tfvars.json",
                    "tf_required_variables.json",
                    "tf_required_features.json",
                    "tf_required_secrets.json",
                ]
                for resource_type in terraform_vars.get("resources_by_category", {}):
                    planned.append(f"resx_{resource_type}.auto.tfvars.json")
                for filename in planned:
                    messages.append(
                        f"[DRY-RUN] Would write: {terraform_path / filename}"
                    )
                messages.append(
                    f"[DRY-RUN] Planned {len(planned)} Terraform artifact file(s)"
                )
                variables_count = len(
                    terraform_vars.get("required_variables", {}).get("variables", [])
                )
                features_count = len(
                    terraform_vars.get("required_features", {}).get("features", [])
                )
                secrets_count = len(
                    terraform_vars.get("required_secrets", {}).get("secrets", [])
                )
                if variables_count or features_count or secrets_count:
                    messages.append(
                        f"[DRY-RUN] Requires: {variables_count} variable(s), "
                        f"{features_count} feature(s), {secrets_count} secret(s)"
                    )
                return True, messages

            messages.extend(
                self._save_terraform_vars(
                    terraform_vars, deployment_service, build_path
                )
            )

            return True, messages

        except Exception as exc:
            error_msg = f"Failed to build Terraform artifacts: {exc}"
            self.logger.error(error_msg, exc_info=True)
            messages.append(error_msg)
            return False, messages

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Hook executed before build starts."""
        messages: List[str] = []

        if not deployment_service.is_validated():
            messages.append("Deployment service is not validated")
            return False, messages

        if not dry_run:
            deployment_build_path = deployment_service.get_build_path(build_path)
            platform_path = deployment_build_path / "platform.json"

            if not platform_path.exists():
                messages.append(
                    f"Platform model not found at: {platform_path}. Run platform build first."
                )
                return False, messages

        if self.verbose:
            messages.append("Pre-build validation passed for Terraform artifacts")

        return True, messages

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Hook executed after build completes."""
        messages: List[str] = []

        if dry_run:
            if self.verbose:
                messages.append(
                    "[DRY-RUN] Skipping Terraform artifact file-existence check"
                )
            return True, messages

        deployment_build_path = deployment_service.get_build_path(build_path)
        terraform_path = deployment_build_path / "terraform"

        base_files = [
            "workspace.auto.tfvars.json",
            "providers.auto.tfvars.json",
            "topologies.auto.tfvars.json",
            "modules.auto.tfvars.json",
            "tf_required_variables.json",
            "tf_required_features.json",
            "tf_required_secrets.json",
        ]

        missing = [f for f in base_files if not (terraform_path / f).exists()]
        if missing:
            messages.append(f"Terraform artifact files missing: {', '.join(missing)}")
            return False, messages

        type_files = list(terraform_path.glob("resx_*.auto.tfvars.json"))
        if self.verbose:
            messages.append(f"Terraform artifacts created at: {terraform_path}")
            messages.append(
                f"Generated {len(base_files)} base files + {len(type_files)} type files"
            )

        return True, messages

    def _build_terraform_vars(
        self,
        platform: PlatformModel,
        deployment_service: DeploymentService,
        messages: List[str],
    ) -> Dict[str, Any]:
        """Build all Terraform tfvars payloads."""
        return {
            "workspace": self._build_workspace_vars(platform, messages),
            "providers": self._build_provider_vars(platform, messages),
            "topologies": self._build_topology_vars(platform, messages),
            "resources_by_category": self._build_resources_by_category(
                platform, deployment_service, messages
            ),
            "modules": self._build_module_vars(platform, messages),
            "required_variables": self._document_required_variables(),
            "required_features": self._document_required_features(),
            "required_secrets": self._document_required_secrets(),
        }

    def _build_workspace_vars(
        self, platform: PlatformModel, messages: List[str]
    ) -> Dict[str, Any]:
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
            "platform_version": str(platform.apiVersion),
            "labels": workspace_labels,
            "metadata": {
                "deployment_version": deployment_version,
                "workspace_description": (
                    workspace.annotations.get("description", "")
                    if workspace.annotations
                    else ""
                ),
                "deployment_description": (
                    platform.meta.annotations.get("description", "")
                    if platform.meta.annotations
                    else ""
                ),
                "workspace_tags": workspace.tags or [],
                "deployment_tags": platform.meta.tags or [],
            },
        }

        if self.verbose:
            messages.append(f"Built workspace vars: {workspace.name}")

        return payload

    def _build_provider_vars(
        self, platform: PlatformModel, messages: List[str]
    ) -> Dict[str, Any]:
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
        platform: PlatformModel,
        deployment_service: DeploymentService,
        messages: List[str],
    ) -> Dict[str, Dict[str, Any]]:
        """Build resource tfvars grouped by resource type."""
        resources_by_category: Dict[str, Dict[str, Dict[str, Any]]] = {}

        if platform.spec.resources:
            for resource in platform.spec.resources:
                resource_type = (
                    resource.properties.resource_type.lower()
                    if resource.properties.resource_type
                    else "uncategorized"
                )

                if resource_type not in resources_by_category:
                    resources_by_category[resource_type] = {}

                resource_data: Dict[str, Any] = {
                    "type": resource.properties.resource_type,
                    "provider": resource.properties.provider_type,
                    "category": resource.properties.category,
                    "subcategory": resource.properties.subcategory,
                    "unit_cost": resource.properties.unit_cost,
                    "description": (
                        resource.annotations.get("description", "")
                        if resource.annotations
                        else ""
                    ),
                    "labels": resource.labels or {},
                    "tags": resource.tags or [],
                }

                if resource.configuration:
                    resource_data["configuration"] = resource.configuration
                if resource.storage:
                    resource_data["storage"] = resource.storage.model_dump(
                        exclude_none=True
                    )
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
                messages.append(
                    f"Built {resource_type} resources: {len(resources_dict)} resources"
                )

        return result

    def _build_module_vars(
        self, platform: PlatformModel, messages: List[str]
    ) -> Dict[str, Any]:
        """Build module tfvars payload."""
        modules_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.modules:
            for module in platform.spec.modules:
                modules_dict[module.name] = {
                    "repository": module.source.repository,
                    "source_path": module.source.source_path,
                    "target_path": module.source.target_path,
                    "description": (
                        module.annotations.get("description", "")
                        if module.annotations
                        else ""
                    ),
                    "labels": module.labels or {},
                    "tags": module.tags or [],
                    "properties": (
                        module.properties.model_dump(exclude_none=True)
                        if module.properties
                        else {}
                    ),
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

    def _build_topology_vars(
        self, platform: PlatformModel, messages: List[str]
    ) -> Dict[str, Any]:
        """Build topology tfvars payload."""
        topologies_dict: Dict[str, Dict[str, Any]] = {}

        if platform.spec.topologies:
            for topology in platform.spec.topologies:
                components = []
                for component in topology.components:
                    components.append({"resource": component.resource})

                volumes = []
                if topology.volumes:
                    for volume in topology.volumes:
                        volumes.append({"name": volume.name, "type": volume.type})

                topologies_dict[topology.name] = {
                    "type": topology.type,
                    "provider": topology.provider,
                    "provisioner": str(topology.provisioner),
                    "components": components,
                    "volumes": volumes,
                }

        return {"topologies": topologies_dict}

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
            self._write_json(
                terraform_path / "modules.auto.tfvars.json", terraform_vars["modules"]
            )

            for resource_type, payload in terraform_vars[
                "resources_by_category"
            ].items():
                self._write_json(
                    terraform_path / f"resx_{resource_type}.auto.tfvars.json", payload
                )

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
            self.logger.error(error_msg, exc_info=True)
            messages.append(error_msg)

        return messages

    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

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

    def _track_variable(self, key: str, description: str, used_by: List[str]) -> None:
        if key not in self.variable_refs:
            self.variable_refs[key] = {
                "key": key,
                "description": description,
                "required": True,
                "suggested_env_var": f"TF_VAR_{key}",
                "used_by": list(used_by),
            }
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
