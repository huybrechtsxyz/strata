#!/usr/bin/env python3
"""
===============================================================================
Script Name   : environment_service.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Environment service class for handling environment configurations
===============================================================================
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.environment_model import (
    EnvironmentModel,
    EnvironmentResourceOverrideModel,
    EnvironmentModuleOverrideModel,
    EnvironmentProviderOverrideModel,
)

from xyz_platform.models.store_models import (
    validate_store_security_policy,
    VariableStoreModel,
    SecretStoreModel,
    FeatureStoreModel,
    VariableStoreType,
    SecretStoreType,
    FeatureStoreType,
)
from xyz_platform.services.base_service import BaseService


class EnvironmentService(BaseService):
    """
    Service for handling environment configurations.

    Provides methods for:
    - Environment validation
    - Variable access and management
    - Secret access and management
    - Feature flag handling
    - Environment file merging
    """

    def __init__(self, path: Optional[str] = None, data: Optional[dict] = None):
        """Initialize the EnvironmentService."""
        super().__init__(path=path, data=data)
        self.model: Optional[EnvironmentModel] = None

    @classmethod
    def merge_envfiles(cls, envfiles: List[str], work_path: Path) -> EnvironmentModel:
        """
        Merge multiple environment files into a single EnvironmentModel.

        Later files override earlier files for conflicting keys.
        Feature flags from the last file take precedence.

        Args:
            envfiles: List of environment file paths to merge (relative to work_path)
            work_path: Base working directory for resolving relative paths

        Returns:
            EnvironmentModel: Merged environment configuration

        Raises:
            ValueError: If any environment file is invalid
        """
        merged_vars = {}
        merged_secrets = {}
        merged_features = None
        meta = None

        for envfile_path in envfiles:
            env_service = cls(str(work_path / envfile_path))
            is_valid, errors = env_service.validate()
            if not is_valid:
                raise ValueError(
                    f"Invalid environment file: {envfile_path}\nErrors: {errors}"
                )

            env_model = env_service.get_model()
            if not env_model:
                continue

            if meta is None and env_model.meta:
                meta = env_model.meta

            # Merge variables (later files override earlier ones)
            if env_model.spec and env_model.spec.variables:
                for var in env_model.spec.variables:
                    merged_vars[var.key] = var

            # Merge secrets (later files override earlier ones)
            if env_model.spec and env_model.spec.secrets:
                for secret in env_model.spec.secrets:
                    merged_secrets[secret.key] = secret

            # Last features wins
            if env_model.spec and env_model.spec.features:
                merged_features = env_model.spec.features

        # Build merged EnvironmentModel
        from xyz_platform.models.environment_model import (
            EnvironmentSpecModel,
            EnvironmentMetaModel,
        )

        variables = list(merged_vars.values()) if merged_vars else None
        secrets = list(merged_secrets.values()) if merged_secrets else None

        spec = EnvironmentSpecModel(
            variables=variables,
            secrets=secrets,
            features=merged_features,
            lifecycle=None,
            properties=None,
            custom=None,
            overrides=None,
        )

        if meta is None:
            meta = EnvironmentMetaModel(
                name="Unknown", annotations=None, labels=None, tags=None
            )

        return EnvironmentModel(
            # apiVersion=PlatformVersion.v1.value, --- IGNORE ---
            # kind=PlatformKind.ENVIRONMENT.value, --- IGNORE ---
            meta=meta,
            spec=spec,
        )

    # Internal methods for validation phases

    def _get_model_class(self):
        """Return the EnvironmentModel class for validation."""
        return EnvironmentModel

    def _validate_dynamic(
        self,
        configuration_model: Optional["ConfigurationModel"] = None,
        work_path: Optional[str] = None,
    ) -> Tuple[bool, List[str]]:
        """
        Phase 2: Dynamic validation against configuration.

        Environment validation checks:
        - Security policies: Validates secrets/variables/features against allowed store types
        - Unique keys: Handled by MODEL validators in EnvironmentSpecModel

        Variables/secrets ADD to or OVERWRITE workspace values (no cross-references needed).

        Args:
            configuration_model: Optional ConfigurationModel for security policy validation

        Returns:
            Tuple[bool, List[str]]: (success, list of error messages)
        """
        if not self.model:
            return True, []
        spec = self.model.spec

        # Validate against security policies if configuration is provided
        if configuration_model and configuration_model.spec.security:
            security = configuration_model.spec.security
            # Convert string store types to enum values
            allowed_variable_stores = (
                [VariableStoreType(s) for s in security.allowed_variable_stores]
                if security.allowed_variable_stores
                else None
            )
            allowed_secret_stores = (
                [SecretStoreType(s) for s in security.allowed_secret_stores]
                if security.allowed_secret_stores
                else None
            )
            allowed_feature_stores = (
                [FeatureStoreType(s) for s in security.allowed_feature_stores]
                if security.allowed_feature_stores
                else None
            )
            errors = validate_store_security_policy(
                variables=spec.variables,
                secrets=spec.secrets,
                features=spec.features,
                allowed_variable_stores=allowed_variable_stores,
                allowed_secret_stores=allowed_secret_stores,
                allowed_feature_stores=allowed_feature_stores,
            )
            return len(errors) == 0, errors

        return True, []

    # Service methods for accessing environment details

    def get_variables(self) -> List[VariableStoreModel]:
        """
        Get all variables defined in the environment.

        Returns:
            List[VariableStoreModel]: List of variable store models
        """
        self._ensure_validated()
        if self.model and self.model.spec and self.model.spec.variables:
            return self.model.spec.variables
        return []

    def get_secrets(self) -> List[SecretStoreModel]:
        """
        Get all secrets defined in the environment.

        Returns:
            List[SecretStoreModel]: List of secret store models
        """
        self._ensure_validated()
        if self.model and self.model.spec and self.model.spec.secrets:
            return self.model.spec.secrets
        return []

    def get_features(self) -> List[FeatureStoreModel]:
        """
        Get all feature flags defined in the environment.

        Returns:
            List[FeatureStoreModel]: List of feature store models
        """
        self._ensure_validated()
        if self.model and self.model.spec and self.model.spec.features:
            return self.model.spec.features
        return []

    # Helper methods for workspace override application

    def has_overrides(self) -> bool:
        """Check if environment has any workspace overrides."""
        self._ensure_validated()
        if not self.model or not self.model.spec or not self.model.spec.overrides:
            return False

        overrides = self.model.spec.overrides
        return bool(
            overrides.resources
            or overrides.modules
            or overrides.providers
            or overrides.properties
        )

    def get_resource_override(
        self, resource_name: str
    ) -> Optional[EnvironmentResourceOverrideModel]:
        """
        Get resource override by name.

        Args:
            resource_name: Name of the resource to get override for

        Returns:
            Optional[EnvironmentResourceOverrideModel]: Resource override if found, None otherwise
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.resources
        ):
            return None
        return next(
            (
                r
                for r in self.model.spec.overrides.resources
                if r.resource == resource_name
            ),
            None,
        )

    def get_module_override(
        self, resource_name: str, module_name: str, slot_type: str = "main"
    ) -> Optional[EnvironmentModuleOverrideModel]:
        """
        Get module override by resource, module name, and slot type.

        Args:
            resource_name: Name of the resource containing the module
            module_name: Name of the module to get override for
            slot_type: Deployment slot type (default: "main")

        Returns:
            Optional[EnvironmentModuleOverrideModel]: Module override if found, None otherwise
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.modules
        ):
            return None
        return next(
            (
                m
                for m in self.model.spec.overrides.modules
                if m.resource == resource_name
                and m.module == module_name
                and (m.slot_type or "main") == slot_type
            ),
            None,
        )

    def get_provider_override(
        self, provider_name: str
    ) -> Optional[EnvironmentProviderOverrideModel]:
        """
        Get provider override by name.

        Args:
            provider_name: Name of the provider to get override for

        Returns:
            Optional[EnvironmentProviderOverrideModel]: Provider override if found, None otherwise
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.providers
        ):
            return None
        return next(
            (
                p
                for p in self.model.spec.overrides.providers
                if p.provider == provider_name
            ),
            None,
        )

    def get_overridden_resource_names(self) -> Set[str]:
        """
        Get set of all resource names that have overrides.

        Returns:
            Set[str]: Set of resource names with overrides
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.resources
        ):
            return set()
        return {r.resource for r in self.model.spec.overrides.resources}

    def get_overridden_provider_names(self) -> Set[str]:
        """
        Get set of all provider names that have overrides.

        Returns:
            Set[str]: Set of provider names with overrides
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.providers
        ):
            return set()
        return {p.provider for p in self.model.spec.overrides.providers}

    def get_overridden_module_keys(self) -> Set[tuple]:
        """
        Get set of all (resource, module, slot_type) tuples that have overrides.

        Returns:
            Set[tuple]: Set of (resource_name, module_name, slot_type) tuples with overrides
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.modules
        ):
            return set()
        return {
            (m.resource, m.module, m.slot_type or "main")
            for m in self.model.spec.overrides.modules
        }

    def get_merged_properties(
        self, workspace_properties: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Merge environment properties with workspace properties.

        Precedence order (lowest to highest):
        1. Workspace properties (base layer)
        2. Environment properties (middle layer)
        3. Environment override properties (highest precedence)

        Args:
            workspace_properties: Optional workspace properties to merge with

        Returns:
            Dict[str, Any]: Merged properties dictionary
        """
        self._ensure_validated()
        result = workspace_properties.copy() if workspace_properties else {}

        # Merge environment properties
        if self.model and self.model.spec and self.model.spec.properties:
            result.update(self.model.spec.properties)

        # Apply override properties (highest precedence)
        if (
            self.model
            and self.model.spec
            and self.model.spec.overrides
            and self.model.spec.overrides.properties
        ):
            result.update(self.model.spec.overrides.properties)

        return result
