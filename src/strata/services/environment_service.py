"""Service for loading and validating environment configurations."""

from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from strata.models.configuration_model import ConfigurationModel
from strata.models.environment_model import (
    EnvironmentModel,
    EnvironmentModuleOverrideModel,
    EnvironmentProviderOverrideModel,
    EnvironmentRemoteOverrideModel,
    EnvironmentResourceOverrideModel,
)
from strata.models.store_models import (
    FeatureStoreModel,
    FeatureStoreType,
    SecretStoreModel,
    SecretStoreType,
    VariableStoreModel,
    VariableStoreType,
    validate_store_security_policy,
)
from strata.services.base_service import BaseService


class EnvironmentService(BaseService["EnvironmentModel"]):
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
        self.model = None

    def on_init(self) -> None:
        """Lifecycle hook: called after __init__ completes."""
        pass

    def on_ready(self) -> None:
        """Lifecycle hook: called after validation succeeds."""
        pass

    def on_shutdown(self) -> None:
        """Lifecycle hook: called before cleanup/destruction."""
        pass

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
                raise ValueError(f"Invalid environment file: {envfile_path}\nErrors: {errors}")

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
        from strata.models.environment_model import (
            EnvironmentMetaModel,
            EnvironmentSpecModel,
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
            meta = EnvironmentMetaModel(name="Unknown", annotations=None, labels=None, tags=None)

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
                [SecretStoreType(s) for s in security.allowed_secret_stores] if security.allowed_secret_stores else None
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
            if errors:
                return False, errors

        # Phase 2: cross-check remote overrides against known configuration remotes
        if configuration_model and spec and spec.overrides and spec.overrides.remotes:
            known_remote_names = {str(r.name) for r in (configuration_model.spec.remotes or [])}
            remote_errors = []
            for remote_override in spec.overrides.remotes:
                if str(remote_override.remote) not in known_remote_names:
                    remote_errors.append(
                        f"Remote override '{remote_override.remote}' does not match any remote "
                        f"defined in configuration spec.remotes. "
                        f"Known remotes: {sorted(known_remote_names) or '(none)'}"
                    )
            if remote_errors:
                return False, remote_errors

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
            overrides.resources or overrides.modules or overrides.providers or overrides.properties or overrides.remotes
        )

    def get_overridden_remote_names(self) -> Set[str]:
        """Return the set of remote names that have a reference override in this environment."""
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.remotes
        ):
            return set()
        return {str(r.remote) for r in self.model.spec.overrides.remotes}

    def get_remote_override(self, remote_name: str) -> Optional[EnvironmentRemoteOverrideModel]:
        """Return the remote override for *remote_name*, or ``None`` if absent."""
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.remotes
        ):
            return None
        return next(
            (r for r in self.model.spec.overrides.remotes if str(r.remote) == remote_name),
            None,
        )

    def get_resource_override(self, resource_name: str) -> Optional[EnvironmentResourceOverrideModel]:
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
            (r for r in self.model.spec.overrides.resources if r.resource == resource_name),
            None,
        )

    def get_module_override(
        self,
        module_name: str,
        resource_name: Optional[str] = None,
        namespace_name: Optional[str] = None,
        slot_type: Optional[str] = None,
    ) -> Optional[EnvironmentModuleOverrideModel]:
        """
        Get module override matching the given context.

        Matching rules (most specific wins):
        1. Exact match on module + resource/namespace + slot_type
        2. Match on module + resource/namespace (any slot)
        3. Match on module only (applies to all instances)

        Args:
            module_name: Module meta.name to look up
            resource_name: Resource context (optional)
            namespace_name: Namespace context (optional)
            slot_type: Deployment slot type (optional)

        Returns:
            Optional[EnvironmentModuleOverrideModel]: Best matching override, or None
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.modules
        ):
            return None

        candidates = [m for m in self.model.spec.overrides.modules if m.module == module_name]
        if not candidates:
            return None

        # Score candidates by specificity (higher = more specific)
        best: Optional[EnvironmentModuleOverrideModel] = None
        best_score = -1
        for candidate in candidates:
            score = 0
            # Check resource match
            if candidate.resource is not None:
                if resource_name and candidate.resource == resource_name:
                    score += 2
                else:
                    continue  # resource specified but doesn't match
            # Check namespace match
            if candidate.namespace is not None:
                if namespace_name and candidate.namespace == namespace_name:
                    score += 2
                else:
                    continue  # namespace specified but doesn't match
            # Check slot_type match
            if candidate.slot_type is not None:
                if slot_type and candidate.slot_type == slot_type:
                    score += 1
                else:
                    continue  # slot specified but doesn't match
            if score > best_score:
                best_score = score
                best = candidate

        return best

    def get_provider_override(self, provider_name: str) -> Optional[EnvironmentProviderOverrideModel]:
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
            (p for p in self.model.spec.overrides.providers if p.provider == provider_name),
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
        Get set of all module override identifiers.

        Returns:
            Set[tuple]: Set of (module_name, resource_or_none, namespace_or_none, slot_type_or_none) tuples
        """
        self._ensure_validated()
        if (
            not self.model
            or not self.model.spec
            or not self.model.spec.overrides
            or not self.model.spec.overrides.modules
        ):
            return set()
        return {(m.module, m.resource, m.namespace, m.slot_type) for m in self.model.spec.overrides.modules}

    def get_merged_properties(self, workspace_properties: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        if self.model and self.model.spec and self.model.spec.overrides and self.model.spec.overrides.properties:
            result.update(self.model.spec.overrides.properties)

        return result
