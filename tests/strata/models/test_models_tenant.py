#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_tenant.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Tests for TenantModel YAML validation in strata.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.tenant_model import TenantModel, TenantReferencesModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


TENANT_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tenants")

TENANT_VALID_FILES = [
    os.path.join(TENANT_FOLDER, "tenant-standard.yaml"),
]

TENANT_INVALID_FILES = [
    os.path.join(TENANT_FOLDER, "tenant-invalid.yaml"),
]


@pytest.mark.parametrize("yaml_path", TENANT_VALID_FILES)
def test_tenant_yaml_valid(yaml_path):
    """Test that a tenant YAML file is a valid TenantModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = TenantModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", TENANT_INVALID_FILES)
def test_tenant_yaml_invalid(yaml_path):
    """Test that a tenant YAML file is NOT a valid TenantModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        TenantModel.model_validate(data)


class TestTenantModel:
    """Unit tests for TenantModel validation."""

    def _valid_data(self, **spec_overrides):
        spec = {
            "code": "acme",
            "name": "ACME Corporation",
            "zones": ["eu-west"],
        }
        spec.update(spec_overrides)
        return {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "tenant",
            "meta": {"name": "acme"},
            "spec": spec,
        }

    def test_minimal_valid_model(self):
        """TenantModel validates with only required fields."""
        model = TenantModel.model_validate(self._valid_data())
        assert model is not None
        assert model.kind == "tenant"
        assert model.meta.name == "acme"
        assert model.spec.code == "acme"
        assert model.spec.name == "ACME Corporation"
        assert model.spec.zones == ["eu-west"]

    def test_kind_is_fixed(self):
        """kind must be 'tenant' — other values are rejected."""
        data = self._valid_data()
        data["kind"] = "workspace"
        with pytest.raises(ValidationError):
            TenantModel.model_validate(data)

    def test_api_version_is_fixed(self):
        """apiVersion must be the platform version — other values are rejected."""
        data = self._valid_data()
        data["apiVersion"] = "strata.huybrechts.xyz/v99"
        with pytest.raises(ValidationError):
            TenantModel.model_validate(data)

    def test_optional_fields_absent(self):
        """TenantModel loads cleanly when all optional fields are absent."""
        model = TenantModel.model_validate(self._valid_data())
        assert model.spec.onboarded is None
        assert model.spec.environments is None
        assert model.spec.configuration is None
        assert model.spec.references is None

    def test_optional_fields_present(self):
        """All optional fields populate correctly."""
        data = self._valid_data(
            onboarded="2025-06-01",
            environments=["environments/tiers/enterprise.yaml"],
            configuration={"crm_id": "1001", "support_level": "premium"},
            references={
                "variables": ["company_domain"],
                "secrets": ["api_key"],
                "features": ["sso_enabled"],
            },
        )
        model = TenantModel.model_validate(data)
        assert model.spec.onboarded is not None
        assert model.spec.environments == ["environments/tiers/enterprise.yaml"]
        assert model.spec.configuration == {"crm_id": "1001", "support_level": "premium"}
        assert model.spec.references is not None
        assert model.spec.references.variables == ["company_domain"]
        assert model.spec.references.secrets == ["api_key"]
        assert model.spec.references.features == ["sso_enabled"]

    def test_zones_required_non_empty(self):
        """zones must contain at least one entry."""
        data = self._valid_data(zones=[])
        with pytest.raises(ValidationError):
            TenantModel.model_validate(data)

    def test_zones_duplicate_rejected(self):
        """Duplicate zone entries in a single tenant are rejected."""
        data = self._valid_data(zones=["eu-west", "eu-west"])
        with pytest.raises(ValidationError, match="Duplicate zone entries"):
            TenantModel.model_validate(data)

    def test_zones_multiple_distinct(self):
        """Multiple distinct zones are accepted."""
        model = TenantModel.model_validate(self._valid_data(zones=["eu-west", "us-east"]))
        assert model.spec.zones == ["eu-west", "us-east"]

    def test_meta_name_must_be_platform_name(self):
        """meta.name must match PlatformName pattern (lowercase, no spaces)."""
        data = self._valid_data()
        data["meta"]["name"] = "ACME Corp"  # uppercase + space
        with pytest.raises(ValidationError):
            TenantModel.model_validate(data)

    def test_code_must_be_platform_name(self):
        """spec.code must match PlatformName pattern."""
        data = self._valid_data(code="ACME-Corp")  # uppercase
        with pytest.raises(ValidationError):
            TenantModel.model_validate(data)

    def test_extra_fields_rejected(self):
        """Extra fields in spec are rejected (extra='forbid')."""
        data = self._valid_data()
        data["spec"]["unknown_field"] = "should-fail"
        with pytest.raises(ValidationError):
            TenantModel.model_validate(data)

    def test_configuration_arbitrary_dict(self):
        """configuration block accepts arbitrary key/value pairs."""
        cfg = {"crm_id": "42", "tier": "gold", "max_users": 500, "enabled": True}
        model = TenantModel.model_validate(self._valid_data(configuration=cfg))
        assert model.spec.configuration == cfg

    def test_references_all_optional(self):
        """TenantReferencesModel fields are all optional."""
        ref = TenantReferencesModel.model_validate({})
        assert ref.variables is None
        assert ref.secrets is None
        assert ref.features is None

    def test_references_partial(self):
        """TenantReferencesModel accepts a subset of reference types."""
        ref = TenantReferencesModel.model_validate({"variables": ["domain"]})
        assert ref.variables == ["domain"]
        assert ref.secrets is None
        assert ref.features is None
