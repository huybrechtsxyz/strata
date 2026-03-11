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
from typing import Any, List, Optional, Tuple
from xyz_platform.models.configuration_model import ConfigurationModel
from xyz_platform.models.environment_model import (
    EnvironmentModel,
)
from xyz_platform.models.common_models import (
    PlatformKind,
    PlatformVersion,
)
from xyz_platform.models.store_models import validate_store_security_policy
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

    def __init__(self, path: str = None, data: dict = None):
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
            if meta is None:
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
        from xyz_platform.models.environment_model import EnvironmentSpecModel

        variables = list(merged_vars.values()) if merged_vars else None
        secrets = list(merged_secrets.values()) if merged_secrets else None

        spec = EnvironmentSpecModel(
            variables=variables,
            secrets=secrets,
            features=merged_features,
        )

        return EnvironmentModel(
            apiVersion=PlatformVersion.v1.value,
            kind=PlatformKind.ENVIRONMENT.value,
            meta=meta if meta else {"name": "merged"},
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
        spec = self.model.spec

        # Validate against security policies if configuration is provided
        if configuration_model and configuration_model.spec.security:
            security = configuration_model.spec.security
            errors = validate_store_security_policy(
                variables=spec.variables,
                secrets=spec.secrets,
                features=spec.features,
                allowed_variable_stores=security.allowed_variable_stores,
                allowed_secret_stores=security.allowed_secret_stores,
                allowed_feature_stores=security.allowed_feature_stores,
            )
            return len(errors) == 0, errors

        return True, []
