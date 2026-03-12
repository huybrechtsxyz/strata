#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_environment.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : EnvironmentService test fixtures and utilities for xyz-platform CLI tests.
===============================================================================
"""

import pytest
from pathlib import Path

from xyz_platform.models.environment_model import EnvironmentModel
from xyz_platform.services.environment_service import EnvironmentService


# Test data paths
DATA_DIR = Path(__file__).parent.parent.parent / "data"
ENVIRONMENT_STANDARD = DATA_DIR / "environments" / "environment-standard.yaml"
ENVIRONMENT_OVERRIDES = DATA_DIR / "environments" / "environment-overrides.yaml"
ENVIRONMENT_INSECURE = DATA_DIR / "environments" / "environment-insecure-secrets.yaml"


class TestEnvironmentService:
    """Test environment service basic functionality."""

    @pytest.fixture
    def get_environment_service(self):
        return EnvironmentService(path=str(ENVIRONMENT_STANDARD))

    def test_get_model_class(self, get_environment_service):
        service = get_environment_service
        model_class = service._get_model_class()
        assert model_class == EnvironmentModel

    def test_environment_service_load_valid(self):
        """Test loading a valid environment."""
        service = EnvironmentService(path=str(ENVIRONMENT_STANDARD))
        # Validate to populate the model
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.model is not None
        assert service.model.meta.name == "valid_environment"

    def test_environment_with_overrides(self):
        """Test loading an environment with workspace overrides."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.model.meta.name == "production"
        assert service.model.spec.overrides is not None
        assert service.model.spec.overrides.resources is not None
        assert len(service.model.spec.overrides.resources) > 0


class TestEnvironmentStoreValidation:
    """Test environment store validation in service context."""

    def test_valid_variable_stores(self):
        """Test that valid variable stores are accepted."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_vars", "labels": {"version": "1.0.0"}},
            "spec": {
                "variables": [
                    {"key": "VAR_CONSTANT", "store": "constant", "value": "test"},
                    {"key": "VAR_ENV", "store": "environment", "value": "ENV_VAR"},
                    {"key": "VAR_CONSUL", "store": "consul", "value": "config/key"},
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert len(service.model.spec.variables) == 3

    def test_valid_secret_stores(self):
        """Test that valid secret stores are accepted."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_secrets", "labels": {"version": "1.0.0"}},
            "spec": {
                "secrets": [
                    {"key": "SECRET_BW", "store": "bitwarden", "value": "item-id"},
                    {"key": "SECRET_VAULT", "store": "vault", "value": "secret/path"},
                    {
                        "key": "SECRET_AKV",
                        "store": "azure-keyvault",
                        "value": "secret-name",
                    },
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert len(service.model.spec.secrets) == 3

    def test_valid_feature_stores(self):
        """Test that valid feature stores are accepted."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_features", "labels": {"version": "1.0.0"}},
            "spec": {
                "features": [
                    {"key": "FEATURE_CONST", "store": "constant", "value": True},
                    {
                        "key": "FEATURE_ENV",
                        "store": "environment",
                        "value": "FEATURE_FLAG",
                    },
                    {
                        "key": "FEATURE_APPCONFIG",
                        "store": "azure-appconfig",
                        "value": "flag-key",
                    },
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert len(service.model.spec.features) == 3

    def test_duplicate_variable_keys_rejected(self):
        """Test that duplicate variable keys are rejected."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_dup_vars", "labels": {"version": "1.0.0"}},
            "spec": {
                "variables": [
                    {"key": "DUPLICATE", "store": "constant", "value": "first"},
                    {"key": "DUPLICATE", "store": "constant", "value": "second"},
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert not is_valid, "Should fail validation for duplicate keys"
        assert any("duplicate" in error.lower() for error in errors)

    def test_duplicate_secret_keys_rejected(self):
        """Test that duplicate secret keys are rejected."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_dup_secrets", "labels": {"version": "1.0.0"}},
            "spec": {
                "secrets": [
                    {"key": "DUPLICATE", "store": "bitwarden", "value": "first"},
                    {"key": "DUPLICATE", "store": "vault", "value": "second"},
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert not is_valid, "Should fail validation for duplicate keys"
        assert any("duplicate" in error.lower() for error in errors)


class TestEnvironmentOverrides:
    """Test environment override validation."""

    def test_resource_override_validation(self):
        """Test that resource overrides are validated."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_overrides", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {
                            "resource": "manager",
                            "count": 3,
                            "enabled": True,
                            "configuration": {"cpu": 4},
                        }
                    ]
                }
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.model.spec.overrides.resources[0].resource == "manager"
        assert service.model.spec.overrides.resources[0].count == 3

    def test_module_override_validation(self):
        """Test that module overrides are validated."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_mod_overrides", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "modules": [
                        {
                            "resource": "manager",
                            "module": "traefik",
                            "slot_type": "main",
                            "enabled": True,
                            "configuration": {"replicas": 2},
                        }
                    ]
                }
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.model.spec.overrides.modules[0].resource == "manager"
        assert service.model.spec.overrides.modules[0].module == "traefik"
        assert service.model.spec.overrides.modules[0].slot_type == "main"

    def test_duplicate_resource_overrides_rejected(self):
        """Test that duplicate resource overrides are rejected."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_dup_resource", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {"resource": "manager", "count": 3},
                        {"resource": "manager", "count": 5},
                    ]
                }
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert not is_valid, "Should fail validation for duplicate resource overrides"
        assert any("duplicate" in error.lower() for error in errors)

    def test_provider_override_validation(self):
        """Test that provider overrides are validated."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_prov_overrides", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "providers": [
                        {
                            "provider": "kamatera_europe",
                            "description": "Production datacenter",
                        }
                    ]
                }
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.model.spec.overrides.providers[0].provider == "kamatera_europe"


class TestEnvironmentSecurityValidation:
    """Test environment security-related validation."""

    def test_insecure_secret_stores_detected(self):
        """Test that insecure secret stores can be detected."""
        service = EnvironmentService(path=str(ENVIRONMENT_INSECURE))
        is_valid, errors = service.validate()

        # Model validation should pass - insecure stores are valid stores
        assert is_valid, f"Model validation failed: {errors}"

        # Check that we have the expected secrets with different store types
        assert service.model.spec.secrets is not None
        assert len(service.model.spec.secrets) >= 3

        # Verify the insecure stores are present
        secret_stores = {s.store for s in service.model.spec.secrets}
        assert "constant" in secret_stores or "environment" in secret_stores

    def test_constant_store_for_secrets(self):
        """Test that constant store can be used for secrets (though not recommended for prod)."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_const_secret", "labels": {"version": "1.0.0"}},
            "spec": {
                "secrets": [
                    {
                        "key": "NOT_SECURE",
                        "store": "constant",
                        "value": "plaintext-secret",
                    }
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        # Model allows this - higher-level validation would catch production security issues
        assert is_valid, f"Validation failed: {errors}"

    def test_secure_stores_for_secrets(self):
        """Test that secure stores work correctly for secrets."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_secure", "labels": {"version": "1.0.0"}},
            "spec": {
                "secrets": [
                    {"key": "SECURE_SECRET", "store": "bitwarden", "value": "item-id"},
                    {"key": "SECURE_VAULT", "store": "vault", "value": "secret/path"},
                    {
                        "key": "SECURE_AKV",
                        "store": "azure-keyvault",
                        "value": "secret-name",
                    },
                ]
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        # Verify all secrets use secure stores
        for secret in service.model.spec.secrets:
            assert secret.store in ["bitwarden", "vault", "azure-keyvault"]


class TestEnvironmentOverrideHelpers:
    """Test environment service override helper methods."""

    def test_has_overrides_true(self):
        """Test has_overrides returns True when overrides exist."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert service.has_overrides() is True

    def test_has_overrides_false(self):
        """Test has_overrides returns False when no overrides exist."""
        service = EnvironmentService(path=str(ENVIRONMENT_STANDARD))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        # Standard environment has no overrides
        assert service.has_overrides() is False

    def test_get_resource_override(self):
        """Test getting a resource override by name."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        # Get an existing resource override
        override = service.get_resource_override("manager")
        assert override is not None
        assert override.resource == "manager"
        assert override.count == 3
        assert override.configuration is not None

        # Get a non-existent resource override
        override = service.get_resource_override("nonexistent")
        assert override is None

    def test_get_module_override(self):
        """Test getting a module override by resource, module, and slot_type."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        # Get an existing module override (main slot_type)
        override = service.get_module_override("manager", "traefik", "main")
        assert override is not None
        assert override.resource == "manager"
        assert override.module == "traefik"
        assert override.configuration is not None

        # Get canary slot_type
        override_canary = service.get_module_override("manager", "traefik", "canary")
        assert override_canary is not None
        assert override_canary.slot_type == "canary"

        # Get a non-existent module override
        override = service.get_module_override("nonexistent", "nonexistent", "main")
        assert override is None

    def test_get_provider_override(self):
        """Test getting a provider override by name."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        # Get an existing provider override
        override = service.get_provider_override("kamatera_europe")
        assert override is not None
        assert override.provider == "kamatera_europe"

        # Get a non-existent provider override
        override = service.get_provider_override("nonexistent")
        assert override is None

    def test_get_overridden_resource_names(self):
        """Test getting set of all overridden resource names."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        resource_names = service.get_overridden_resource_names()
        assert isinstance(resource_names, set)
        assert "manager" in resource_names
        assert "worker" in resource_names
        assert len(resource_names) == 2

    def test_get_overridden_provider_names(self):
        """Test getting set of all overridden provider names."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        provider_names = service.get_overridden_provider_names()
        assert isinstance(provider_names, set)
        assert "kamatera_europe" in provider_names
        assert len(provider_names) == 1

    def test_get_overridden_module_keys(self):
        """Test getting set of all overridden module keys."""
        service = EnvironmentService(path=str(ENVIRONMENT_OVERRIDES))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        module_keys = service.get_overridden_module_keys()
        assert isinstance(module_keys, set)
        # Each key is (resource, module, slot_type)
        for key in module_keys:
            assert isinstance(key, tuple)
            assert len(key) == 3

    def test_get_merged_properties_no_workspace(self):
        """Test merging properties without workspace properties."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_props", "labels": {"version": "1.0.0"}},
            "spec": {
                "properties": {"env_prop": "env_value"},
                "overrides": {"properties": {"override_prop": "override_value"}},
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        merged = service.get_merged_properties()
        assert merged["env_prop"] == "env_value"
        assert merged["override_prop"] == "override_value"

    def test_get_merged_properties_with_workspace(self):
        """Test merging properties with workspace properties."""
        env_data = {
            "apiVersion": "platform.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_props", "labels": {"version": "1.0.0"}},
            "spec": {
                "properties": {"env_prop": "env_value", "shared_prop": "from_env"},
                "overrides": {
                    "properties": {
                        "override_prop": "override_value",
                        "shared_prop": "from_override",
                    }
                },
            },
        }
        service = EnvironmentService(data=env_data)
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        workspace_props = {
            "workspace_prop": "workspace_value",
            "shared_prop": "from_workspace",
        }
        merged = service.get_merged_properties(workspace_props)

        # Verify precedence: workspace < env < override
        assert merged["workspace_prop"] == "workspace_value"  # From workspace
        assert merged["env_prop"] == "env_value"  # From env
        assert merged["override_prop"] == "override_value"  # From override
        assert merged["shared_prop"] == "from_override"  # Override wins

    def test_get_variables(self):
        """Test getting variables from environment."""
        service = EnvironmentService(path=str(ENVIRONMENT_STANDARD))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        variables = service.get_variables()
        assert isinstance(variables, list)
        # Standard environment should have variables
        assert len(variables) > 0

    def test_get_secrets(self):
        """Test getting secrets from environment."""
        service = EnvironmentService(path=str(ENVIRONMENT_STANDARD))
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"

        secrets = service.get_secrets()
        assert isinstance(secrets, list)
        # Standard environment should have secrets
        assert len(secrets) > 0
