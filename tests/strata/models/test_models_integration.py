#!/usr/bin/env python3
"""Tests for IntegrationModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.integration_model import IntegrationModel


def _minimal_integration() -> dict:
    return {
        "meta": {"name": "vault-main"},
        "spec": {"type": "vault", "capabilities": ["secrets"]},
    }


def test_integration_minimal_is_valid():
    """A minimal integration document validates successfully."""
    model = IntegrationModel.model_validate(_minimal_integration())
    assert model.meta.name == "vault-main"
    assert model.spec.type == "vault"
    assert model.spec.capabilities == {"secrets"}
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "integration"


def test_integration_defaults():
    """required/enabled default sensibly; capabilities may be empty."""
    data = {"meta": {"name": "git-main"}, "spec": {"type": "git"}}
    model = IntegrationModel.model_validate(data)
    assert model.spec.required is False
    assert model.spec.enabled is True
    assert model.spec.capabilities == set()


def test_integration_accepts_custom_type():
    """`type` is an open string — a custom/unrecognized tool name is accepted."""
    data = _minimal_integration()
    data["spec"]["type"] = "my-custom-secret-plugin"
    model = IntegrationModel.model_validate(data)
    assert model.spec.type == "my-custom-secret-plugin"


def test_integration_rejects_unknown_capability():
    """An unrecognized capability name is rejected (closed vocabulary)."""
    data = _minimal_integration()
    data["spec"]["capabilities"] = ["made-up-capability"]
    with pytest.raises(ValidationError, match="Invalid capability names"):
        IntegrationModel.model_validate(data)


def test_integration_accepts_multiple_known_capabilities():
    """Multiple recognized capability names are accepted together."""
    data = _minimal_integration()
    data["spec"]["capabilities"] = ["variables", "secrets", "features"]
    model = IntegrationModel.model_validate(data)
    assert model.spec.capabilities == {"variables", "secrets", "features"}


def test_integration_accepts_authentication():
    """authentication reuses AuthenticationModel directly."""
    data = _minimal_integration()
    data["spec"]["authentication"] = {"method": "cli", "cli": {"use_cli": True}}
    model = IntegrationModel.model_validate(data)
    assert model.spec.authentication is not None
    assert model.spec.authentication.method == "cli"


def test_integration_rejects_unknown_fields():
    """Unknown top-level spec fields are rejected (extra='forbid')."""
    data = _minimal_integration()
    data["spec"]["bogus"] = "field"
    with pytest.raises(ValidationError):
        IntegrationModel.model_validate(data)
