#!/usr/bin/env python3
"""Tests for ConfigurationModel YAML validation.

`spec.providers`/`spec.topologies` are thin `{name, file}` pointers to
standalone `ProviderConfigModel`/`TopologyConfigModel` documents (ADR-0014)
— the actual registry content (regions/resources, component roles) is
tested in `test_models_provider_config.py`/`test_models_topology_config.py`.
"""

import pytest
from pydantic import ValidationError

from strata.models.configuration_model import ConfigurationModel


def _minimal_configuration() -> dict:
    return {
        "meta": {"name": "solution-config"},
        "spec": {
            "providers": ["kamatera"],
        },
    }


def test_configuration_minimal_is_valid():
    """A minimal configuration with one provider registry pointer validates successfully."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.meta.name == "solution-config"
    assert model.spec.providers[0] == "kamatera"


def test_configuration_rejects_duplicate_provider_names():
    """Duplicate provider names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["providers"].append(data["spec"]["providers"][0])
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)


def test_configuration_topologies_is_optional():
    """spec.topologies may be omitted entirely."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.topologies is None
    assert model.spec.additional_topologies is False


def test_configuration_accepts_topology_registry_pointer():
    """spec.topologies accepts a plain TopologyConfig document name."""
    data = _minimal_configuration()
    data["spec"]["topologies"] = ["kubernetes"]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.topologies[0] == "kubernetes"


def test_configuration_rejects_duplicate_topology_names():
    """Duplicate topology type names across the registry raise a ValidationError."""
    data = _minimal_configuration()
    data["spec"]["topologies"] = ["kubernetes", "kubernetes"]
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)


# ---------------------------------------------------------------------------
# spec.security (docs/design/gap_fit_v1.md gap #7 — real usage: config-deploy's
# config/stores.yaml)
# ---------------------------------------------------------------------------


def test_configuration_security_is_optional():
    """spec.security may be omitted entirely — no restriction declared."""
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.security is None


def test_configuration_accepts_security_allow_lists():
    data = _minimal_configuration()
    data["spec"]["security"] = {
        "allowed_secret_stores": ["github", "azure-keyvault"],
        "allowed_variable_stores": ["constant", "azure-appconfig"],
        "allowed_feature_stores": ["constant", "azure-appconfig"],
    }
    model = ConfigurationModel.model_validate(data)
    assert model.spec.security.allowed_secret_stores == ["github", "azure-keyvault"]
    assert model.spec.security.allowed_variable_stores == ["constant", "azure-appconfig"]
    assert model.spec.security.allowed_feature_stores == ["constant", "azure-appconfig"]


def test_configuration_security_fields_are_each_independently_optional():
    data = _minimal_configuration()
    data["spec"]["security"] = {"allowed_secret_stores": ["azure-keyvault"]}
    model = ConfigurationModel.model_validate(data)
    assert model.spec.security.allowed_secret_stores == ["azure-keyvault"]
    assert model.spec.security.allowed_variable_stores is None
    assert model.spec.security.allowed_feature_stores is None


def test_configuration_security_rejects_unknown_fields():
    """extra='forbid' still applies to the nested security model."""
    data = _minimal_configuration()
    data["spec"]["security"] = {"allowed_widget_stores": ["nope"]}
    with pytest.raises(ValidationError):
        ConfigurationModel.model_validate(data)


# ---------------------------------------------------------------------------
# spec.paths (docs/design/path-conventions.md, docs/design/gap_fit_v1.md gap #7 —
# real usage: config-deploy's config/paths.yaml)
# ---------------------------------------------------------------------------


def test_configuration_paths_is_optional():
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.paths is None


def test_configuration_accepts_a_tenant_resolving_path_convention():
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {
            "name": "tenant-path",
            "scope": "customers/**",
            "pattern": "customers/{code}",
            "filename_pattern": "tenant.yaml",
            "resolves": "tenant",
        }
    ]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.paths[0].name == "tenant-path"
    assert model.spec.paths[0].filename_pattern == "tenant.yaml"
    assert model.spec.paths[0].resolves == "tenant"
    assert model.spec.paths[0].enforcement == "warn"


def test_configuration_filename_pattern_is_optional():
    """None (default) means any filename is accepted under a matching directory."""
    data = _minimal_configuration()
    data["spec"]["paths"] = [{"name": "hub-path", "scope": "deploy/hubs/**", "pattern": "deploy/hubs/{hub}"}]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.paths[0].filename_pattern is None


def test_configuration_filename_pattern_can_capture_with_no_directory_capture():
    """provider-path's real shape: the capture lives entirely in the filename."""
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {"name": "provider-path", "scope": "providers/**", "pattern": "providers", "filename_pattern": "{region}.yaml"}
    ]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.paths[0].pattern == "providers"
    assert model.spec.paths[0].filename_pattern == "{region}.yaml"


def test_configuration_accepts_a_structural_path_convention_with_no_resolves():
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {"name": "provider-path", "scope": "providers/**", "pattern": "providers", "filename_pattern": "{region}.yaml"}
    ]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.paths[0].resolves is None
    assert model.spec.paths[0].segments is None


def test_configuration_accepts_a_layers_resolving_path_convention_with_segments():
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {
            "name": "control-path",
            "scope": "deploy/control/**",
            "pattern": "deploy/control/{control}",
            "resolves": "layers",
            "segments": [{"name": "control", "pattern": "^[a-z][a-z0-9-]*$"}],
        }
    ]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.paths[0].segments[0].name == "control"


def test_configuration_rejects_layers_resolving_convention_without_segments():
    """docs/design/path-conventions.md's Open Question #1 (resolved: required)."""
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {
            "name": "control-path",
            "scope": "deploy/control/**",
            "pattern": "deploy/control/{control}",
            "resolves": "layers",
        }
    ]
    with pytest.raises(ValidationError, match="requires 'segments'"):
        ConfigurationModel.model_validate(data)


def test_configuration_rejects_layers_resolving_convention_with_empty_segments():
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {
            "name": "control-path",
            "scope": "deploy/control/**",
            "pattern": "deploy/control/{control}",
            "resolves": "layers",
            "segments": [],
        }
    ]
    with pytest.raises(ValidationError, match="requires 'segments'"):
        ConfigurationModel.model_validate(data)


def test_configuration_rejects_unknown_resolves_value():
    data = _minimal_configuration()
    data["spec"]["paths"] = [{"name": "bogus-path", "scope": "bogus/**", "pattern": "bogus/{x}", "resolves": "bogus"}]
    with pytest.raises(ValidationError):
        ConfigurationModel.model_validate(data)


def test_configuration_rejects_duplicate_path_convention_names():
    data = _minimal_configuration()
    entry = {
        "name": "tenant-path",
        "scope": "customers/**",
        "pattern": "customers/{code}",
        "filename_pattern": "tenant.yaml",
    }
    data["spec"]["paths"] = [entry, dict(entry)]
    with pytest.raises(ValidationError, match="Duplicate"):
        ConfigurationModel.model_validate(data)


def test_configuration_path_convention_enforcement_can_be_set_to_deny():
    data = _minimal_configuration()
    data["spec"]["paths"] = [
        {
            "name": "provider-path",
            "scope": "providers/**",
            "pattern": "providers",
            "filename_pattern": "{region}.yaml",
            "enforcement": "deny",
        }
    ]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.paths[0].enforcement == "deny"


# ---------------------------------------------------------------------------
# spec.cve_allowed (docs/archive/cve-scanner-integration.md Phase 3)
# ---------------------------------------------------------------------------


def test_configuration_cve_allowed_is_optional():
    model = ConfigurationModel.model_validate(_minimal_configuration())
    assert model.spec.cve_allowed is None


def test_configuration_accepts_cve_allowed_entries():
    data = _minimal_configuration()
    data["spec"]["cve_allowed"] = [
        {"id": "CVE-2024-1234", "reason": "false positive"},
        {"id": "CVE-2024-5678", "reason": "scoped to one package", "package": "openssl", "expires": "2026-12-31"},
    ]
    model = ConfigurationModel.model_validate(data)
    assert model.spec.cve_allowed[0].id == "CVE-2024-1234"
    assert model.spec.cve_allowed[0].package is None
    assert model.spec.cve_allowed[1].package == "openssl"
    assert model.spec.cve_allowed[1].expires == "2026-12-31"


def test_configuration_cve_allowed_rejects_a_non_iso_expires_date():
    data = _minimal_configuration()
    data["spec"]["cve_allowed"] = [{"id": "CVE-2024-1234", "reason": "false positive", "expires": "31-12-2026"}]
    with pytest.raises(ValidationError, match="not a valid ISO date"):
        ConfigurationModel.model_validate(data)


def test_configuration_cve_allowed_rejects_unknown_fields():
    data = _minimal_configuration()
    data["spec"]["cve_allowed"] = [{"id": "CVE-2024-1234", "reason": "false positive", "unknown_field": "oops"}]
    with pytest.raises(ValidationError):
        ConfigurationModel.model_validate(data)
