#!/usr/bin/env python3
"""Tests for TenantModel YAML validation."""

import pytest
from pydantic import ValidationError

from strata.models.tenant_model import TenantModel


def _minimal_tenant() -> dict:
    return {
        "meta": {"name": "c0062"},
        "spec": {
            "display_name": "GSK",
            "geographies": ["europe"],
        },
    }


def test_tenant_minimal_is_valid():
    """A minimal tenant (identifier, display name, one zone) validates."""
    model = TenantModel.model_validate(_minimal_tenant())
    assert model.meta.name == "c0062"
    assert model.spec.display_name == "GSK"
    assert model.spec.geographies == ["europe"]
    assert model.apiVersion.value == "strata.huybrechts.xyz/v2"
    assert model.kind.value == "tenant"


def test_tenant_rejects_mismatched_kind():
    """A document declaring another kind is rejected (ADR-0016)."""
    data = _minimal_tenant()
    data["kind"] = "workspace"
    with pytest.raises(ValidationError, match="Expected kind 'tenant'"):
        TenantModel.model_validate(data)


def test_tenant_requires_at_least_one_geography():
    """Data residency is mandatory — an empty geography list is rejected."""
    data = _minimal_tenant()
    data["spec"]["geographies"] = []
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)


def test_tenant_rejects_duplicate_geographies():
    """Duplicate geography entries are rejected."""
    data = _minimal_tenant()
    data["spec"]["geographies"] = ["europe", "europe"]
    with pytest.raises(ValidationError, match="Duplicate"):
        TenantModel.model_validate(data)


def test_tenant_rejects_v1_zones_field():
    """v1's spec.zones is now spec.geographies (extra='forbid' catches the old name)."""
    data = _minimal_tenant()
    data["spec"]["zones"] = ["europe"]
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)


def test_tenant_requires_display_name():
    """display_name is required — meta.name is the code, not the label."""
    data = _minimal_tenant()
    del data["spec"]["display_name"]
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)


def test_tenant_rejects_v1_code_field():
    """v1's spec.code duplicated meta.name and is not ported (extra='forbid')."""
    data = _minimal_tenant()
    data["spec"]["code"] = "c0062"
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)


def test_tenant_accepts_environment_names():
    """spec.environments names Environment documents, not file paths."""
    data = _minimal_tenant()
    data["spec"]["environments"] = ["c0062-env"]
    model = TenantModel.model_validate(data)
    assert model.spec.environments == ["c0062-env"]


def test_tenant_rejects_environment_file_path():
    """A v1-style path fails PlatformName — documents are referenced by name (ADR-0015)."""
    data = _minimal_tenant()
    data["spec"]["environments"] = ["customers/c0062/tenant.env.yaml"]
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)


def test_tenant_rejects_duplicate_environments():
    """A repeated environment reference would merge twice."""
    data = _minimal_tenant()
    data["spec"]["environments"] = ["shared-env", "shared-env"]
    with pytest.raises(ValidationError, match="Duplicate"):
        TenantModel.model_validate(data)


def test_tenant_accepts_onboarded_date():
    """onboarded parses as a real date."""
    data = _minimal_tenant()
    data["spec"]["onboarded"] = "2026-03-15"
    model = TenantModel.model_validate(data)
    assert model.spec.onboarded is not None
    assert model.spec.onboarded.year == 2026


def test_tenant_accepts_merge_layers():
    """properties/configuration/custom are free-form merge layers."""
    data = _minimal_tenant()
    data["spec"]["properties"] = {"tier": "enterprise"}
    data["spec"]["configuration"] = {"crm_id": "42"}
    data["spec"]["custom"] = {"owner": "platform-team"}
    model = TenantModel.model_validate(data)
    assert model.spec.properties == {"tier": "enterprise"}
    assert model.spec.configuration == {"crm_id": "42"}
    assert model.spec.custom == {"owner": "platform-team"}


def test_tenant_rejects_unknown_fields():
    """Extra/unknown fields are rejected (extra='forbid')."""
    data = _minimal_tenant()
    data["spec"]["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)
