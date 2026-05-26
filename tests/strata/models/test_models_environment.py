#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_environment.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.12+
Description   : Environment model using Pydantic for data validation and YAML parsing.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.environment_model import EnvironmentModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


ENVIRONMENT_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "environments")

# List of YAML files to test (extensible)
ENVIRONMENT_VALID_FILES = [
    os.path.join(ENVIRONMENT_FOLDER, "environment-standard.yaml"),
    os.path.join(ENVIRONMENT_FOLDER, "environment-insecure-secrets.yaml"),
    os.path.join(ENVIRONMENT_FOLDER, "environment-overrides.yaml"),
    os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "config", "xyz-configuration", "environments", "xyz-env-prd.yaml"
    ),
]

# List of invalid YAML files to test (extensible)
ENVIRONMENT_INVALID_FILES = [os.path.join(ENVIRONMENT_FOLDER, "environment-invalid.yaml")]


@pytest.mark.parametrize("yaml_path", ENVIRONMENT_VALID_FILES)
def test_environment_yaml_valid(yaml_path):
    """Test that an environment YAML file is a valid EnvironmentModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = EnvironmentModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", ENVIRONMENT_INVALID_FILES)
def test_environment_yaml_invalid(yaml_path):
    """Test that an environment YAML file is NOT a valid EnvironmentModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        EnvironmentModel.model_validate(data)
    model = None
    assert model is None


class TestEnvironmentStoreValidation:
    """Test environment store model validation."""

    def test_variable_store_types(self):
        """Test that variable store types are validated correctly."""
        valid_stores = ["constant", "environment", "azure-appconfig", "consul", "vault"]

        for store_type in valid_stores:
            data = {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "environment",
                "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
                "spec": {"variables": [{"key": "TEST_VAR", "store": store_type, "value": "test_value"}]},
            }
            model = EnvironmentModel.model_validate(data)
            assert model.spec.variables[0].store == store_type

    def test_secret_store_types(self):
        """Test that secret store types are validated correctly."""
        # SecretStoreType supports: constant, environment, azure-keyvault, bitwarden, vault
        valid_stores = [
            "bitwarden",
            "azure-keyvault",
            "vault",
            "constant",
            "environment",
        ]

        for store_type in valid_stores:
            data = {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "environment",
                "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
                "spec": {
                    "secrets": [
                        {
                            "key": "TEST_SECRET",
                            "store": store_type,
                            "value": "test_value",
                        }
                    ]
                },
            }
            model = EnvironmentModel.model_validate(data)
            assert model.spec.secrets[0].store == store_type

    def test_feature_store_types(self):
        """Test that feature store types are validated correctly."""
        valid_stores = ["constant", "environment", "azure-appconfig"]

        for store_type in valid_stores:
            data = {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "environment",
                "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
                "spec": {
                    "features": [
                        {
                            "key": "TEST_FEATURE",
                            "store": store_type,
                            "value": "test_value",
                        }
                    ]
                },
            }
            model = EnvironmentModel.model_validate(data)
            assert model.spec.features[0].store == store_type

    def test_invalid_variable_store_type(self):
        """Test that invalid variable store types are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {"variables": [{"key": "TEST_VAR", "store": "invalid_store", "value": "test_value"}]},
        }
        with pytest.raises(Exception) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "store" in str(exc_info.value).lower()

    def test_duplicate_variable_keys(self):
        """Test that duplicate variable keys are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "variables": [
                    {"key": "DUPLICATE_KEY", "store": "constant", "value": "value1"},
                    {"key": "DUPLICATE_KEY", "store": "constant", "value": "value2"},
                ]
            },
        }
        with pytest.raises(ValueError) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "duplicate" in str(exc_info.value).lower()
        assert "DUPLICATE_KEY" in str(exc_info.value)

    def test_duplicate_secret_keys(self):
        """Test that duplicate secret keys are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "secrets": [
                    {
                        "key": "DUPLICATE_SECRET",
                        "store": "bitwarden",
                        "value": "value1",
                    },
                    {"key": "DUPLICATE_SECRET", "store": "vault", "value": "value2"},
                ]
            },
        }
        with pytest.raises(ValueError) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "duplicate" in str(exc_info.value).lower()
        assert "DUPLICATE_SECRET" in str(exc_info.value)

    def test_duplicate_feature_keys(self):
        """Test that duplicate feature keys are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "features": [
                    {"key": "DUPLICATE_FEATURE", "store": "constant", "value": True},
                    {"key": "DUPLICATE_FEATURE", "store": "constant", "value": False},
                ]
            },
        }
        with pytest.raises(ValueError) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "duplicate" in str(exc_info.value).lower()
        assert "DUPLICATE_FEATURE" in str(exc_info.value)


class TestEnvironmentOverrides:
    """Test environment override validation."""

    def test_resource_overrides(self):
        """Test that resource overrides are validated correctly."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {
                            "resource": "manager",
                            "count": 3,
                            "configuration": {"cpu": 4, "ram_mb": 8192},
                        }
                    ]
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        assert model.spec.overrides.resources[0].resource == "manager"
        assert model.spec.overrides.resources[0].count == 3

    def test_module_overrides(self):
        """Test that module overrides are validated correctly."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "modules": [
                        {
                            "resource": "manager",
                            "module": "traefik",
                            "slot_type": "main",
                            "enabled": True,
                        }
                    ]
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        assert model.spec.overrides.modules[0].resource == "manager"
        assert model.spec.overrides.modules[0].module == "traefik"
        assert model.spec.overrides.modules[0].slot_type == "main"

    def test_duplicate_resource_overrides(self):
        """Test that duplicate resource overrides are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {"resource": "manager", "count": 3},
                        {"resource": "manager", "count": 5},
                    ]
                }
            },
        }
        with pytest.raises(ValueError) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "duplicate" in str(exc_info.value).lower()
        assert "manager" in str(exc_info.value).lower()

    def test_duplicate_provider_overrides(self):
        """Test that duplicate provider overrides are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "providers": [
                        {"provider": "kamatera_europe", "description": "First"},
                        {"provider": "kamatera_europe", "description": "Second"},
                    ]
                }
            },
        }
        with pytest.raises(ValueError) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "duplicate" in str(exc_info.value).lower()
        assert "kamatera_europe" in str(exc_info.value).lower()

    def test_module_slot_type_validation(self):
        """Test that module slot_type is validated."""
        valid_slots = ["main", "staging", "canary", "sidecar", "init"]

        for slot in valid_slots:
            data = {
                "apiVersion": "strata.huybrechts.xyz/v1",
                "kind": "environment",
                "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
                "spec": {
                    "overrides": {
                        "modules": [
                            {
                                "resource": "manager",
                                "module": "traefik",
                                "slot_type": slot,
                            }
                        ]
                    }
                },
            }
            model = EnvironmentModel.model_validate(data)
            assert model.spec.overrides.modules[0].slot_type == slot


class TestEnvironmentIncludes:
    """Test environment terraform file include validation."""

    def test_resource_override_with_includes(self):
        """Test that resource overrides can have includes."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {
                            "resource": "manager",
                            "includes": [
                                {
                                    "source": "@haven/terraform/waf/listeners/*.tf",
                                    "target": "waf_listeners.tf",
                                    "strategy": "concatenate",
                                }
                            ],
                        }
                    ]
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        includes = model.spec.overrides.resources[0].includes
        assert includes is not None
        assert len(includes) == 1
        assert includes[0].source == "@haven/terraform/waf/listeners/*.tf"
        assert includes[0].target == "waf_listeners.tf"
        assert includes[0].strategy.value == "concatenate"

    def test_resource_override_with_merge_strategy(self):
        """Test include with merge strategy."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {
                            "resource": "worker",
                            "includes": [
                                {
                                    "source": "@haven/customers/standard.tfvars",
                                    "target": "customers.auto.tfvars.json",
                                    "strategy": "merge",
                                    "order": 0,
                                },
                                {
                                    "source": "@haven/customers/acme/overrides.tfvars",
                                    "target": "customers.auto.tfvars.json",
                                    "strategy": "merge",
                                    "order": 1,
                                },
                            ],
                        }
                    ]
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        includes = model.spec.overrides.resources[0].includes
        assert len(includes) == 2
        assert includes[0].strategy.value == "merge"
        assert includes[0].order == 0
        assert includes[1].order == 1

    def test_env_wide_includes(self):
        """Test environment-wide includes (on EnvironmentOverridesModel)."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "@haven/terraform/shared/providers.tf",
                            "target": "providers.tf",
                            "strategy": "concatenate",
                        },
                        {
                            "source": "@haven/terraform/shared/backend.tf",
                            "target": "backend.tf",
                            "strategy": "concatenate",
                            "optional": True,
                        },
                    ]
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        includes = model.spec.overrides.includes
        assert includes is not None
        assert len(includes) == 2
        assert includes[0].optional is False
        assert includes[1].optional is True

    def test_include_defaults(self):
        """Test include field defaults (strategy=concatenate, optional=False, order=None)."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "@haven/terraform/extra.tf",
                            "target": "extra.tf",
                        }
                    ]
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        include = model.spec.overrides.includes[0]
        assert include.strategy.value == "concatenate"
        assert include.optional is False
        assert include.order is None

    def test_include_invalid_strategy_rejected(self):
        """Test that invalid strategy values are rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "@haven/terraform/x.tf",
                            "target": "x.tf",
                            "strategy": "invalid_strategy",
                        }
                    ]
                }
            },
        }
        with pytest.raises(ValidationError):
            EnvironmentModel.model_validate(data)

    def test_include_empty_source_rejected(self):
        """Test that empty source is rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "",
                            "target": "output.tf",
                        }
                    ]
                }
            },
        }
        with pytest.raises(ValidationError):
            EnvironmentModel.model_validate(data)

    def test_include_empty_target_rejected(self):
        """Test that empty target is rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "@haven/terraform/x.tf",
                            "target": "",
                        }
                    ]
                }
            },
        }
        with pytest.raises(ValidationError):
            EnvironmentModel.model_validate(data)

    def test_include_path_traversal_rejected(self):
        """Test that path traversal in target is rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "@haven/terraform/x.tf",
                            "target": "../../etc/passwd",
                        }
                    ]
                }
            },
        }
        with pytest.raises(ValidationError) as exc_info:
            EnvironmentModel.model_validate(data)
        assert "traversal" in str(exc_info.value).lower()

    def test_include_negative_order_rejected(self):
        """Test that negative order is rejected."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "includes": [
                        {
                            "source": "@haven/terraform/x.tf",
                            "target": "output.tf",
                            "order": -1,
                        }
                    ]
                }
            },
        }
        with pytest.raises(ValidationError):
            EnvironmentModel.model_validate(data)

    def test_include_both_resource_and_env_wide(self):
        """Test that includes can exist at both resource and env-wide levels."""
        data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "environment",
            "meta": {"name": "test_env", "labels": {"version": "1.0.0"}},
            "spec": {
                "overrides": {
                    "resources": [
                        {
                            "resource": "manager",
                            "includes": [
                                {
                                    "source": "@haven/terraform/waf/*.tf",
                                    "target": "waf.tf",
                                }
                            ],
                        }
                    ],
                    "includes": [
                        {
                            "source": "@haven/terraform/shared/backend.tf",
                            "target": "backend.tf",
                        }
                    ],
                }
            },
        }
        model = EnvironmentModel.model_validate(data)
        assert len(model.spec.overrides.resources[0].includes) == 1
        assert len(model.spec.overrides.includes) == 1
