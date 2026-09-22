#!/usr/bin/env python3
"""Tests for EnvironmentModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.environment_model import EnvironmentModel


def _minimal_environment() -> dict:
    return {
        "meta": {"name": "prd"},
        "spec": {
            "variables": [{"key": "REGION", "store": "constant", "value": "westeurope"}],
            "secrets": [{"key": "DB_PASSWORD", "store": "infisical", "value": "DB_PASSWORD"}],
            "features": [{"key": "ENABLE_X", "store": "constant", "value": "true"}],
        },
    }


def test_environment_minimal_is_valid():
    """An environment declaring the three store kinds validates."""
    model = EnvironmentModel.model_validate(_minimal_environment())
    assert model.meta.name == "prd"
    assert model.spec.variables[0].key == "REGION"
    assert model.spec.secrets[0].key == "DB_PASSWORD"
    assert model.spec.features[0].key == "ENABLE_X"
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "environment"


def test_environment_rejects_mismatched_kind():
    """A document declaring another kind is rejected (ADR-0016)."""
    data = _minimal_environment()
    data["kind"] = "workspace"
    with pytest.raises(ValidationError, match="Expected kind 'environment'"):
        EnvironmentModel.model_validate(data)


def test_environment_allows_empty_spec():
    """All stores are optional — an environment may carry only properties."""
    model = EnvironmentModel.model_validate({"meta": {"name": "empty"}, "spec": {"properties": {"tier": "std"}}})
    assert model.spec.variables is None
    assert model.spec.properties == {"tier": "std"}


def test_environment_rejects_duplicate_variable_keys():
    """Duplicate keys within one store are rejected."""
    data = _minimal_environment()
    data["spec"]["variables"].append({"key": "REGION", "store": "constant", "value": "eastus"})
    with pytest.raises(ValidationError, match="Duplicate"):
        EnvironmentModel.model_validate(data)


def test_environment_rejects_duplicate_secret_keys():
    """Duplicate secret keys are rejected."""
    data = _minimal_environment()
    data["spec"]["secrets"].append({"key": "DB_PASSWORD", "store": "constant", "value": "x"})
    with pytest.raises(ValidationError, match="Duplicate"):
        EnvironmentModel.model_validate(data)


def test_environment_allows_same_key_across_different_stores():
    """'${var:X}' and '${secret:X}' are different tokens — the name may repeat."""
    data = _minimal_environment()
    data["spec"]["variables"] = [{"key": "SHARED", "store": "constant", "value": "a"}]
    data["spec"]["secrets"] = [{"key": "SHARED", "store": "constant", "value": "b"}]
    model = EnvironmentModel.model_validate(data)
    assert model.spec.variables[0].key == "SHARED"
    assert model.spec.secrets[0].key == "SHARED"


def test_environment_accepts_secret_generate_spec():
    """Store-model features carry through — e.g. generated secrets."""
    data = _minimal_environment()
    data["spec"]["secrets"] = [
        {"key": "ROOT_PW", "store": "infisical", "value": "ROOT_PW", "generate": {"type": "urlsafe", "length": 32}}
    ]
    model = EnvironmentModel.model_validate(data)
    assert model.spec.secrets[0].generate.type.value == "urlsafe"


def test_environment_rejects_v1_overrides_block():
    """v1's overrides subtree is not ported — 0 of 26 real documents use it."""
    data = _minimal_environment()
    data["spec"]["overrides"] = {"resources": [{"resource": "web-vm", "enabled": False}]}
    with pytest.raises(ValidationError):
        EnvironmentModel.model_validate(data)


def test_environment_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_environment()
    data["spec"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        EnvironmentModel.model_validate(data)
