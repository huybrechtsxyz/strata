#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_models_customer.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : Tests for CustomerModel YAML validation in strata.
===============================================================================
"""

import os

import pytest
import yaml
from pydantic import ValidationError

from strata.models.customer_model import CustomerModel, CustomerReferencesModel


@pytest.fixture(autouse=True)
def set_pythonpath_env(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "src")


CUSTOMER_FOLDER = os.path.join(os.path.dirname(__file__), "..", "..", "data", "customers")

CUSTOMER_VALID_FILES = [
    os.path.join(CUSTOMER_FOLDER, "customer-standard.yaml"),
]

CUSTOMER_INVALID_FILES = [
    os.path.join(CUSTOMER_FOLDER, "customer-invalid.yaml"),
]


@pytest.mark.parametrize("yaml_path", CUSTOMER_VALID_FILES)
def test_customer_yaml_valid(yaml_path):
    """Test that a customer YAML file is a valid CustomerModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    model = CustomerModel.model_validate(data)
    assert model is not None


@pytest.mark.parametrize("yaml_path", CUSTOMER_INVALID_FILES)
def test_customer_yaml_invalid(yaml_path):
    """Test that a customer YAML file is NOT a valid CustomerModel."""
    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    with pytest.raises(ValidationError):
        CustomerModel.model_validate(data)


class TestCustomerModel:
    """Unit tests for CustomerModel validation."""

    def _valid_data(self, **spec_overrides):
        spec = {
            "code": "acme",
            "name": "ACME Corporation",
            "zones": ["eu-west"],
        }
        spec.update(spec_overrides)
        return {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "customer",
            "meta": {"name": "acme"},
            "spec": spec,
        }

    def test_minimal_valid_model(self):
        """CustomerModel validates with only required fields."""
        model = CustomerModel.model_validate(self._valid_data())
        assert model is not None
        assert model.kind == "customer"
        assert model.meta.name == "acme"
        assert model.spec.code == "acme"
        assert model.spec.name == "ACME Corporation"
        assert model.spec.zones == ["eu-west"]

    def test_kind_is_fixed(self):
        """kind must be 'customer' — other values are rejected."""
        data = self._valid_data()
        data["kind"] = "workspace"
        with pytest.raises(ValidationError):
            CustomerModel.model_validate(data)

    def test_api_version_is_fixed(self):
        """apiVersion must be the platform version — other values are rejected."""
        data = self._valid_data()
        data["apiVersion"] = "strata.huybrechts.xyz/v99"
        with pytest.raises(ValidationError):
            CustomerModel.model_validate(data)

    def test_optional_fields_absent(self):
        """CustomerModel loads cleanly when all optional fields are absent."""
        model = CustomerModel.model_validate(self._valid_data())
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
        model = CustomerModel.model_validate(data)
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
            CustomerModel.model_validate(data)

    def test_zones_duplicate_rejected(self):
        """Duplicate zone entries in a single customer are rejected."""
        data = self._valid_data(zones=["eu-west", "eu-west"])
        with pytest.raises(ValidationError, match="Duplicate zone entries"):
            CustomerModel.model_validate(data)

    def test_zones_multiple_distinct(self):
        """Multiple distinct zones are accepted."""
        model = CustomerModel.model_validate(self._valid_data(zones=["eu-west", "us-east"]))
        assert model.spec.zones == ["eu-west", "us-east"]

    def test_meta_name_must_be_platform_name(self):
        """meta.name must match PlatformName pattern (lowercase, no spaces)."""
        data = self._valid_data()
        data["meta"]["name"] = "ACME Corp"  # uppercase + space
        with pytest.raises(ValidationError):
            CustomerModel.model_validate(data)

    def test_code_must_be_platform_name(self):
        """spec.code must match PlatformName pattern."""
        data = self._valid_data(code="ACME-Corp")  # uppercase
        with pytest.raises(ValidationError):
            CustomerModel.model_validate(data)

    def test_extra_fields_rejected(self):
        """Extra fields in spec are rejected (extra='forbid')."""
        data = self._valid_data()
        data["spec"]["unknown_field"] = "should-fail"
        with pytest.raises(ValidationError):
            CustomerModel.model_validate(data)

    def test_configuration_arbitrary_dict(self):
        """configuration block accepts arbitrary key/value pairs."""
        cfg = {"crm_id": "42", "tier": "gold", "max_users": 500, "enabled": True}
        model = CustomerModel.model_validate(self._valid_data(configuration=cfg))
        assert model.spec.configuration == cfg

    def test_references_all_optional(self):
        """CustomerReferencesModel fields are all optional."""
        ref = CustomerReferencesModel.model_validate({})
        assert ref.variables is None
        assert ref.secrets is None
        assert ref.features is None

    def test_references_partial(self):
        """CustomerReferencesModel accepts a subset of reference types."""
        ref = CustomerReferencesModel.model_validate({"variables": ["domain"]})
        assert ref.variables == ["domain"]
        assert ref.secrets is None
        assert ref.features is None
