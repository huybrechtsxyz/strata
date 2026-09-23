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


# ---------------------------------------------------------------------------
# capabilities — container, and the x- extension tier (ADR-0021 D9)
# ---------------------------------------------------------------------------


def test_integration_accepts_container_capability():
    """'container' (Compose/Helm) is a recognized core capability."""
    data = _minimal_integration()
    data["spec"]["capabilities"] = ["container"]
    model = IntegrationModel.model_validate(data)
    assert model.spec.capabilities == {"container"}


def test_integration_accepts_x_prefixed_extension_capability():
    """An 'x-'-prefixed capability bypasses the closed vocabulary entirely."""
    data = _minimal_integration()
    data["spec"]["capabilities"] = ["secrets", "x-ticketing"]
    model = IntegrationModel.model_validate(data)
    assert model.spec.capabilities == {"secrets", "x-ticketing"}


def test_integration_accepts_multiple_x_prefixed_extensions():
    data = _minimal_integration()
    data["spec"]["capabilities"] = ["x-ticketing", "x-pagerduty"]
    model = IntegrationModel.model_validate(data)
    assert model.spec.capabilities == {"x-ticketing", "x-pagerduty"}


def test_integration_still_rejects_unprefixed_unknown_capability_alongside_extension():
    """An 'x-' capability doesn't smuggle an unprefixed typo through."""
    data = _minimal_integration()
    data["spec"]["capabilities"] = ["x-ticketing", "made-up-capability"]
    with pytest.raises(ValidationError, match="Invalid capability names"):
        IntegrationModel.model_validate(data)


# ---------------------------------------------------------------------------
# transport, version, command — open strings, no cross-field enforcement
# at schema time (ADR-0021 D1/D4: that needs the resolved class, built in
# strata.integrations, not here)
# ---------------------------------------------------------------------------


def test_integration_accepts_transport():
    data = _minimal_integration()
    data["spec"]["transport"] = "http"
    model = IntegrationModel.model_validate(data)
    assert model.spec.transport == "http"


def test_integration_transport_is_an_open_string():
    """Any transport name validates — enforcement against TRANSPORTS is runtime, not schema."""
    data = _minimal_integration()
    data["spec"]["transport"] = "carrier-pigeon"
    model = IntegrationModel.model_validate(data)
    assert model.spec.transport == "carrier-pigeon"


def test_integration_accepts_version_constraint():
    data = _minimal_integration()
    data["spec"]["version"] = ">= 1.17"
    model = IntegrationModel.model_validate(data)
    assert model.spec.version == ">= 1.17"


def test_integration_accepts_command_override():
    data = _minimal_integration()
    data["spec"]["command"] = "consul"
    model = IntegrationModel.model_validate(data)
    assert model.spec.command == "consul"


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


def test_integration_accepts_endpoints_address():
    data = _minimal_integration()
    data["spec"]["endpoints"] = {"address": "https://vault.example.com:8200"}
    model = IntegrationModel.model_validate(data)
    assert model.spec.endpoints is not None
    assert model.spec.endpoints.address == "https://vault.example.com:8200"


def test_integration_endpoints_requires_a_non_empty_address():
    data = _minimal_integration()
    data["spec"]["endpoints"] = {"address": ""}
    with pytest.raises(ValidationError):
        IntegrationModel.model_validate(data)


# ---------------------------------------------------------------------------
# lifecycle — Python-only scripts (ADR-0021 D11)
# ---------------------------------------------------------------------------


def test_integration_lifecycle_accepts_a_python_script():
    data = _minimal_integration()
    data["spec"]["lifecycle"] = {"connect_before": {"scripts": ["scripts/check.py"]}}
    model = IntegrationModel.model_validate(data)
    assert model.spec.lifecycle is not None
    assert model.spec.lifecycle.root["connect_before"].scripts == ["scripts/check.py"]


def test_integration_lifecycle_rejects_a_non_python_script():
    """Nothing dispatches a script to an interpreter yet except Python — a .ps1
    hook would pass schema validation and then fail at run time, so it is
    rejected here instead (ADR-0021 D11)."""
    data = _minimal_integration()
    data["spec"]["lifecycle"] = {"teardown": {"scripts": ["scripts/cleanup.ps1"]}}
    with pytest.raises(ValidationError, match=r"\.py"):
        IntegrationModel.model_validate(data)


def test_integration_lifecycle_supports_multiple_phases():
    data = _minimal_integration()
    data["spec"]["lifecycle"] = {
        "connect_before": {"scripts": ["scripts/a.py"]},
        "teardown": {"scripts": ["scripts/b.py"]},
    }
    model = IntegrationModel.model_validate(data)
    assert set(model.spec.lifecycle.root.keys()) == {"connect_before", "teardown"}

