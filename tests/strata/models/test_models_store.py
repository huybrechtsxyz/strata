#!/usr/bin/env python3
"""Tests for FeatureStoreModel/VariableStoreModel/SecretStoreModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.store_model import FeatureStoreModel, SecretStoreModel, VariableStoreModel


def test_feature_store_constant_is_valid():
    """A constant-store feature flag with a literal value validates successfully."""
    model = FeatureStoreModel.model_validate({"key": "enable_new_ui", "store": "constant", "value": "true"})
    assert model.key == "enable_new_ui"
    assert model.store.value == "constant"
    assert model.value == "true"


def test_feature_store_environment_is_valid():
    """An environment-store feature flag reads its value from an env var name."""
    model = FeatureStoreModel.model_validate(
        {"key": "enable_new_ui", "store": "environment", "value": "ENABLE_NEW_UI"}
    )
    assert model.store.value == "environment"


def test_feature_store_integration_backed_is_valid():
    """An integration-backed store (azure-appconfig/flagsmith) is a recognized value."""
    model = FeatureStoreModel.model_validate(
        {"key": "enable_new_ui", "store": "azure-appconfig", "value": "flags/enable-new-ui"}
    )
    assert model.store.value == "azure-appconfig"


def test_feature_store_rejects_unknown_store_type():
    """An unrecognized store type is rejected (closed enum, unlike Provisioner.tool)."""
    with pytest.raises(ValidationError):
        FeatureStoreModel.model_validate({"key": "enable_new_ui", "store": "made-up-store", "value": "x"})


def test_feature_store_default_rejected_on_builtin_store():
    """'default' is rejected on constant/environment stores — there is no store to seed."""
    with pytest.raises(ValidationError, match="not valid on built-in store"):
        FeatureStoreModel.model_validate(
            {"key": "enable_new_ui", "store": "constant", "value": "true", "default": "false"}
        )


def test_feature_store_default_accepted_on_integration_backed_store():
    """'default' is accepted on an integration-backed store."""
    model = FeatureStoreModel.model_validate(
        {
            "key": "enable_new_ui",
            "store": "azure-appconfig",
            "value": "flags/enable-new-ui",
            "default": "false",
        }
    )
    assert model.default == "false"


def test_feature_store_rejects_unknown_fields():
    """Unknown top-level fields are rejected (extra='forbid')."""
    with pytest.raises(ValidationError):
        FeatureStoreModel.model_validate(
            {"key": "enable_new_ui", "store": "constant", "value": "true", "bogus": "field"}
        )


# ---------------------------------------------------------------------------
# VariableStoreModel
# ---------------------------------------------------------------------------


def test_variable_store_constant_is_valid():
    """A constant-store variable with a literal value validates successfully."""
    model = VariableStoreModel.model_validate({"key": "region", "store": "constant", "value": "eu-west"})
    assert model.key == "region"
    assert model.value == "eu-west"


def test_variable_store_integration_backed_is_valid():
    """An integration-backed store (vault/consul/etc.) is a recognized value."""
    model = VariableStoreModel.model_validate({"key": "region", "store": "vault", "value": "secret/region"})
    assert model.store.value == "vault"


def test_variable_store_type_matches_constant_value():
    """A declared 'type' on a constant store must match the literal value's Python type."""
    model = VariableStoreModel.model_validate(
        {"key": "replicas", "store": "constant", "value": 3, "type": "number"}
    )
    assert model.type.value == "number"


def test_variable_store_type_mismatch_on_constant_is_rejected():
    """A declared 'type' that doesn't match the constant value's Python type is rejected."""
    with pytest.raises(ValidationError, match="type=number requires a numeric value"):
        VariableStoreModel.model_validate({"key": "replicas", "store": "constant", "value": "not-a-number", "type": "number"})


def test_variable_store_type_not_checked_for_non_constant_store():
    """'type' is not cross-checked against 'value' for non-constant stores (resolved at deploy time)."""
    model = VariableStoreModel.model_validate(
        {"key": "replicas", "store": "vault", "value": "secret/replicas", "type": "number"}
    )
    assert model.type.value == "number"


def test_variable_store_default_rejected_on_builtin_store():
    """'default' is rejected on constant/environment stores."""
    with pytest.raises(ValidationError, match="not valid on built-in store"):
        VariableStoreModel.model_validate(
            {"key": "region", "store": "constant", "value": "eu-west", "default": "eu-fr"}
        )


def test_variable_store_default_accepted_on_integration_backed_store():
    """'default' is accepted on an integration-backed store."""
    model = VariableStoreModel.model_validate(
        {"key": "region", "store": "vault", "value": "secret/region", "default": "eu-fr"}
    )
    assert model.default == "eu-fr"


# ---------------------------------------------------------------------------
# SecretStoreModel
# ---------------------------------------------------------------------------


def test_secret_store_constant_is_valid():
    """A constant-store secret with a literal value validates successfully."""
    model = SecretStoreModel.model_validate({"key": "db_password", "store": "constant", "value": "hunter2"})
    assert model.key == "db_password"


def test_secret_store_github_is_valid():
    """A github-store secret reads its value from a runner-injected env var name."""
    model = SecretStoreModel.model_validate({"key": "api_key", "store": "github", "value": "MY_API_KEY"})
    assert model.store.value == "github"


def test_secret_store_generate_rejected_on_builtin_store():
    """'generate' is rejected on constant/environment/github stores."""
    with pytest.raises(ValidationError, match="not valid on built-in store"):
        SecretStoreModel.model_validate(
            {
                "key": "db_password",
                "store": "constant",
                "value": "hunter2",
                "generate": {"type": "urlsafe"},
            }
        )


def test_secret_store_generate_accepted_on_integration_backed_store():
    """'generate' is accepted on an integration-backed store."""
    model = SecretStoreModel.model_validate(
        {
            "key": "db_password",
            "store": "azure-keyvault",
            "value": "db-password",
            "generate": {"type": "urlsafe", "length": 24},
        }
    )
    assert model.generate.type.value == "urlsafe"
    assert model.generate.length == 24


def test_secret_store_rotate_policy_rotate_requires_generate():
    """A 'rotate' policy of 'rotate' requires a 'generate' spec."""
    with pytest.raises(ValidationError, match="requires a 'generate' spec"):
        SecretStoreModel.model_validate(
            {
                "key": "db_password",
                "store": "azure-keyvault",
                "value": "db-password",
                "rotate": {"max_age": 30, "policy": "rotate"},
            }
        )


def test_secret_store_rotate_policy_warn_does_not_require_generate():
    """A 'rotate' policy of 'warn' does not require a 'generate' spec."""
    model = SecretStoreModel.model_validate(
        {
            "key": "db_password",
            "store": "azure-keyvault",
            "value": "db-password",
            "rotate": {"max_age": 30, "policy": "warn"},
        }
    )
    assert model.rotate.policy.value == "warn"


def test_secret_store_version_rejected_for_github():
    """'version' is rejected for github-store secrets (GitHub Secrets aren't versioned)."""
    with pytest.raises(ValidationError, match="not supported for store type 'github'"):
        SecretStoreModel.model_validate(
            {"key": "api_key", "store": "github", "value": "MY_API_KEY", "version": "1"}
        )


def test_secret_store_rejects_unknown_store_type():
    """An unrecognized store type is rejected (closed enum)."""
    with pytest.raises(ValidationError):
        SecretStoreModel.model_validate({"key": "db_password", "store": "made-up-store", "value": "x"})
