#!/usr/bin/env python3
"""Tests for TenantService loading and validation."""

from strata.models.provider_config_model import ProviderConfigModel
from strata.services.tenant_service import TenantService


def _tenant(**spec_overrides) -> dict:
    spec = {
        "display_name": "GSK",
        "geographies": ["europe"],
        "properties": {"tier": "standard"},
    }
    spec.update(spec_overrides)
    return {"meta": {"name": "c0062"}, "spec": spec}


def _provider_config(*regions) -> ProviderConfigModel:
    return ProviderConfigModel.model_validate(
        {
            "meta": {"name": "azure"},
            "spec": {
                "description": "Azure",
                "additional_regions": False,
                "regions": list(regions),
                "additional_resources": True,
            },
        }
    )


def test_tenant_service_validates_from_data():
    """A TenantService constructed from an in-memory dict validates successfully."""
    service = TenantService(data=_tenant())
    is_valid, errors = service.validate()
    assert is_valid
    assert errors == []
    assert service.model is not None
    assert service.model.spec.display_name == "GSK"


def test_geographies_accepted_when_declared_by_a_provider_region():
    """A geography that some provider region declares is valid."""
    service = TenantService(data=_tenant())
    service.validate()

    configs = {"azure": _provider_config({"name": "westeurope", "geography": "europe"})}
    is_valid, errors = service.validate_geographies_against_provider_configs(configs)
    assert is_valid
    assert errors == []


def test_geographies_rejected_when_no_provider_region_declares_them():
    """A typo'd or unknown geography is rejected, listing the known ones."""
    service = TenantService(data=_tenant(geographies=["europ"]))
    service.validate()

    configs = {"azure": _provider_config({"name": "westeurope", "geography": "europe"})}
    is_valid, errors = service.validate_geographies_against_provider_configs(configs)
    assert not is_valid
    assert any("europ" in e and "europe" in e for e in errors)


def test_geographies_skipped_when_no_provider_declares_any():
    """With no geography tags anywhere, there is nothing to check against."""
    service = TenantService(data=_tenant())
    service.validate()

    configs = {"azure": _provider_config({"name": "westeurope"})}
    is_valid, errors = service.validate_geographies_against_provider_configs(configs)
    assert is_valid
    assert errors == []

