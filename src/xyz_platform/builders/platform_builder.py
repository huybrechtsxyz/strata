#!/usr/bin/env python3
"""
===============================================================================
Module Name   : platform_builder.py
Author        : XYZ Platform Team
Version       : 1.0.0
Python Version: 3.12+
Description   : Build the platform model by assembling a PlatformModel from
                all services loaded by the deployment pipeline and writing
                the result to platform.json / platform.yaml.
===============================================================================
"""

from pathlib import Path
from typing import List, Optional, Tuple

from xyz_platform.builders.base_builder import BaseBuilder
from xyz_platform.models.platform_model import (
    PlatformFirewallModel,
    PlatformLifecycleModel,
    PlatformMetaModel,
    PlatformModel,
    PlatformModuleModel,
    PlatformNamespaceModel,
    PlatformProviderModel,
    PlatformResourceModel,
    PlatformSpecModel,
    PlatformTopologyModel,
    PlatformWorkspaceModel,
)
from xyz_platform.models.store_models import (
    FeatureStoreModel,
    SecretStoreModel,
    VariableStoreModel,
)
from xyz_platform.services.deployment_service import DeploymentService
from xyz_platform.services.platform_service import PlatformService
from xyz_platform.logger import get_logger


class PlatformBuilder(BaseBuilder):
    """Builder that assembles a PlatformModel artifact from a fully-loaded
    DeploymentService and persists it as platform.json / platform.yaml."""

    def __init__(self, verbose: bool = False):
        """Initialise the platform builder.

        Args:
            verbose: When True emit extra progress messages.
        """
        self.logger = get_logger(__name__)
        self.verbose = verbose
        self.configuration_service = None
        self._last_platform_model: Optional[PlatformModel] = None

    # ------------------------------------------------------------------
    # BaseBuilder interface
    # ------------------------------------------------------------------

    def build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        configuration_service=None,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Assemble and persist the platform model.

        Args:
            deployment_service: Fully-loaded deployment service (related
                services must already be loaded and validated).
            work_path: Working directory path.
            build_path: Root build output directory.
            configuration_service: Optional configuration service used to
                compute the artifact_path from deployment layers.
            dry_run: When True, build the model in memory but skip writing
                output files.  The assembled model is stored in
                ``self._last_platform_model`` for downstream use.

        Returns:
            Tuple[bool, List[str]]: (success, messages).
        """
        messages: List[str] = []
        self.configuration_service = configuration_service
        self._last_platform_model = None

        try:
            platform, build_messages = self._build_platform(deployment_service)
            messages.extend(build_messages)

            if platform is None:
                return False, messages

            self._last_platform_model = platform

            if dry_run:
                deployment_build_path = deployment_service.get_build_path(build_path)
                messages.append(
                    f"[DRY-RUN] Would write platform model to: {deployment_build_path / 'platform.json'}"
                )
                messages.append(
                    f"[DRY-RUN] Would write platform model to: {deployment_build_path / 'platform.yaml'}"
                )
                return True, messages

            save_messages = self._save_platform(
                platform, deployment_service, build_path
            )
            messages.extend(save_messages)

            return True, messages

        except Exception as exc:
            msg = f"Failed to build platform model: {exc}"
            self.logger.error(msg, exc_info=True)
            messages.append(msg)
            return False, messages

    def before_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
    ) -> Tuple[bool, List[str]]:
        """Pre-build validation hook.

        Verifies that the deployment service and its workspace are ready
        before any build work begins.

        Args:
            deployment_service: Deployment service to validate.
            work_path: Working directory path.
            build_path: Build output directory path.

        Returns:
            Tuple[bool, List[str]]: (success, messages).
        """
        messages: List[str] = []

        if not deployment_service.is_validated():
            messages.append("Deployment service is not validated")
            return False, messages

        workspace_service = deployment_service.get_workspace_service()
        if not workspace_service:
            messages.append("Workspace service is not available")
            return False, messages

        if self.verbose:
            messages.append("Pre-build validation passed")

        return True, messages

    def after_build(
        self,
        deployment_service: DeploymentService,
        work_path: Path,
        build_path: Path,
        dry_run: bool = False,
    ) -> Tuple[bool, List[str]]:
        """Post-build verification hook.

        Confirms that the expected output files were written successfully.
        When *dry_run* is True file-existence checks are skipped.

        Args:
            deployment_service: Deployment service.
            work_path: Working directory path.
            build_path: Build output directory path.
            dry_run: When True, skip output file existence checks.

        Returns:
            Tuple[bool, List[str]]: (success, messages).
        """
        messages: List[str] = []

        if dry_run:
            if self.verbose:
                messages.append(
                    "[DRY-RUN] Skipping platform model file-existence check"
                )
            return True, messages

        deployment_build_path = deployment_service.get_build_path(build_path)
        json_path = deployment_build_path / "platform.json"
        yaml_path = deployment_build_path / "platform.yaml"

        if json_path.exists() and yaml_path.exists():
            if self.verbose:
                messages.append(
                    f"Platform model files created at: {deployment_build_path}"
                )
        else:
            messages.append("Platform model files were not created")
            return False, messages

        return True, messages

    # ------------------------------------------------------------------
    # Internal build logic
    # ------------------------------------------------------------------

    def _build_platform(
        self, deployment_service: DeploymentService
    ) -> Tuple[Optional[PlatformModel], List[str]]:
        """Assemble the PlatformModel from the deployment service hierarchy.

        Args:
            deployment_service: Fully-loaded deployment service.

        Returns:
            Tuple[Optional[PlatformModel], List[str]]: (model or None, messages).
        """
        messages: List[str] = []

        try:
            deployment_model = deployment_service.model

            # Derive environment for meta label enrichment
            environment_service = deployment_service.get_environment_service()

            # Build meta
            meta = PlatformMetaModel.from_deployment_meta(
                deployment_model.meta, environment_service
            )

            # Build spec
            configuration_model = (
                getattr(self.configuration_service, "model", None)
                if self.configuration_service
                else None
            )
            spec = self._build_spec(
                deployment_service, configuration_model=configuration_model
            )

            platform = PlatformModel(meta=meta, spec=spec)

            if self.verbose:
                messages.append(f"Built platform model: {platform.meta.name}")

            return platform, messages

        except Exception as exc:
            msg = f"Failed to assemble platform model: {exc}"
            self.logger.error(msg, exc_info=True)
            messages.append(msg)
            return None, messages

    def _build_spec(
        self,
        deployment_service: DeploymentService,
        configuration_model=None,
    ) -> PlatformSpecModel:
        """Build the PlatformSpecModel from workspace and deployment data.

        Args:
            deployment_service: Fully-loaded deployment service.
            configuration_model: Optional configuration model for
                artifact_path computation.

        Returns:
            PlatformSpecModel.
        """
        deployment_model = deployment_service.model
        workspace_service = deployment_service.get_workspace_service()
        workspace_model = workspace_service.model

        # ------------------------------------------------------------------
        # Artifact path (optional — requires configuration_model)
        # ------------------------------------------------------------------
        artifact_path: Optional[str] = None
        if configuration_model and hasattr(deployment_service, "get_artifact_path"):
            artifact_path = deployment_service.get_artifact_path(configuration_model)
            if self.verbose:
                self.logger.debug(f"Computed artifact_path: {artifact_path}")

        # ------------------------------------------------------------------
        # Workspace identity
        # ------------------------------------------------------------------
        workspace = PlatformWorkspaceModel.from_workspace_model(workspace_model)

        # Shorthand for workspace related services
        ws_related = workspace_service._related_services or {}

        # ------------------------------------------------------------------
        # Providers
        # ------------------------------------------------------------------
        providers = None
        provider_services = ws_related.get("providers", {})
        if provider_services:
            providers = [
                PlatformProviderModel.from_provider_model(svc.model)
                for svc in provider_services.values()
            ]

        # ------------------------------------------------------------------
        # Topologies
        # ------------------------------------------------------------------
        topologies = None
        if workspace_model.spec.topology:
            topologies = [
                PlatformTopologyModel(**topo.model_dump())
                for topo in workspace_model.spec.topology
            ]
            if self.verbose:
                self.logger.debug(f"Built {len(topologies)} topology/topologies")

        # ------------------------------------------------------------------
        # Resources (firewall references resolved later)
        # ------------------------------------------------------------------
        resources = None
        resource_firewall_map: dict = {}  # {resource_name: merged_fw_name}

        resource_services = ws_related.get("resources", {})
        if resource_services:
            # Map resource names → their workspace firewall reference lists
            resource_to_firewalls: dict = {}
            if workspace_model.spec.resources:
                for res_ref in workspace_model.spec.resources:
                    if res_ref.firewalls:
                        resource_to_firewalls[res_ref.name] = res_ref.firewalls

            resources = [
                PlatformResourceModel.from_resource_model(
                    svc.model,
                    firewalls=resource_to_firewalls.get(svc.get_name()),
                )
                for svc in resource_services.values()
            ]

        # ------------------------------------------------------------------
        # Namespaces
        # ------------------------------------------------------------------
        namespaces = None
        namespace_services = ws_related.get("namespaces", {})
        if namespace_services:
            namespaces = [
                PlatformNamespaceModel.from_namespace_model(svc.model)
                for svc in namespace_services.values()
            ]

        # ------------------------------------------------------------------
        # Modules (flat dict keyed by name in workspace._related_services)
        # ------------------------------------------------------------------
        modules = None
        module_services = ws_related.get("modules", {})
        if module_services:
            all_modules = [
                PlatformModuleModel.from_module_model(svc.model)
                for svc in module_services.values()
            ]
            if all_modules:
                modules = all_modules
                self.logger.debug(f"Built {len(modules)} module(s)")
            else:
                self.logger.warning("No modules were built for platform model")

        # ------------------------------------------------------------------
        # Firewalls: original definitions + merged resource firewalls
        # ------------------------------------------------------------------
        firewalls_list = []

        firewall_services = ws_related.get("firewalls", {})
        if firewall_services:
            for fw_service in firewall_services.values():
                firewalls_list.append(
                    PlatformFirewallModel.from_firewall_model(fw_service.model)
                )
            self.logger.debug(
                f"Added {len(firewall_services)} original firewall definition(s)"
            )

        # Merged firewalls synthesised from resources with multiple fw refs
        if resource_services:
            for resource_name, resource_service in resource_services.items():
                if (
                    hasattr(resource_service, "merged_firewall")
                    and resource_service.merged_firewall
                ):
                    merged_fw_name = f"{resource_name}_merged_fw"
                    self.logger.debug(
                        f"Adding merged firewall '{merged_fw_name}' "
                        f"for resource '{resource_name}'"
                    )
                    merged = PlatformFirewallModel.from_firewall_model(
                        resource_service.merged_firewall
                    )
                    # model_copy() is Pydantic v2; fall back to direct assignment
                    merged.name = merged_fw_name  # type: ignore[assignment]
                    firewalls_list.append(merged)
                    resource_firewall_map[resource_name] = merged_fw_name

        # Back-fill merged firewall reference onto each resource
        if resources and resource_firewall_map:
            for resource in resources:
                if resource.name in resource_firewall_map:
                    resource.firewall = resource_firewall_map[resource.name]
                    self.logger.debug(
                        f"Resource '{resource.name}' references merged firewall "
                        f"'{resource.firewall}'"
                    )

        firewalls = firewalls_list if firewalls_list else None
        if firewalls:
            self.logger.debug(
                f"Built {len(firewalls)} firewall(s) "
                f"({len(resource_firewall_map)} merged, "
                f"{len(firewalls) - len(resource_firewall_map)} original)"
            )

        # ------------------------------------------------------------------
        # Variables / Secrets / Features  (optional — service may not exist yet)
        # ------------------------------------------------------------------
        all_variables: Optional[List[VariableStoreModel]] = None
        all_secrets: Optional[List[SecretStoreModel]] = None
        all_features: Optional[List[FeatureStoreModel]] = None

        variable_service = (
            deployment_service.get_variable_service()
            if hasattr(deployment_service, "get_variable_service")
            else None
        )
        secret_service = (
            deployment_service.get_secret_service()
            if hasattr(deployment_service, "get_secret_service")
            else None
        )
        feature_service = (
            deployment_service.get_feature_service()
            if hasattr(deployment_service, "get_feature_service")
            else None
        )

        if variable_service:
            keys = variable_service.get_variable_list()
            self.logger.debug(f"Found {len(keys)} unique variable key(s)")
            all_variables = (
                [VariableStoreModel(**variable_service.get_struct(k)) for k in keys]
                if keys
                else None
            )

        if secret_service:
            keys = secret_service.get_secret_list()
            self.logger.debug(f"Found {len(keys)} unique secret key(s)")
            all_secrets = (
                [
                    SecretStoreModel(**secret_service.get_struct(k, resolve=False))
                    for k in keys
                ]
                if keys
                else None
            )

        if feature_service:
            keys = feature_service.get_flag_list()
            self.logger.debug(f"Found {len(keys)} unique feature key(s)")
            all_features = (
                [FeatureStoreModel(**feature_service.get_struct(k)) for k in keys]
                if keys
                else None
            )

        # ------------------------------------------------------------------
        # Assemble spec
        # ------------------------------------------------------------------
        lifecycle_model = None
        if deployment_model.spec.lifecycle:
            lifecycle_model = PlatformLifecycleModel.model_validate(
                deployment_model.spec.lifecycle.model_dump()
            )

        return PlatformSpecModel(
            workspace=workspace,
            providers=providers,
            topologies=topologies,
            resources=resources,
            namespaces=namespaces,
            modules=modules,
            firewalls=firewalls,
            deployment=deployment_model.spec.layers,
            artifact_path=artifact_path,
            stages=deployment_model.spec.stages,
            approvals=deployment_model.spec.approvals,
            lifecycle=lifecycle_model,
            properties=deployment_model.spec.properties,
            custom=deployment_model.spec.custom,
            features=all_features,
            variables=all_variables,
            secrets=all_secrets,
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_platform(
        self,
        platform: PlatformModel,
        deployment_service: DeploymentService,
        build_path: Path,
    ) -> List[str]:
        """Persist the PlatformModel to JSON and YAML.

        Args:
            platform: Assembled platform model.
            deployment_service: Used to resolve the deployment build path.
            build_path: Root build directory.

        Returns:
            List[str]: Progress / error messages.
        """
        messages: List[str] = []

        try:
            deployment_build_path = deployment_service.get_build_path(build_path)
            deployment_build_path.mkdir(parents=True, exist_ok=True)

            service = PlatformService()
            service.verbose = self.verbose

            json_path = deployment_build_path / "platform.json"
            yaml_path = deployment_build_path / "platform.yaml"

            service.save_both_formats(platform, json_path, yaml_path)

            if self.verbose:
                messages.append(f"Saved platform model to: {json_path}")
                messages.append(f"Saved platform model to: {yaml_path}")

        except Exception as exc:
            msg = f"Failed to save platform model: {exc}"
            self.logger.error(msg, exc_info=True)
            messages.append(msg)

        return messages
