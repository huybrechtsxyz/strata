#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_services_tenant.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : TenantService unit tests for strata CLI.
===============================================================================
"""

from pathlib import Path

import pytest

from strata.exceptions import ServiceNotValidatedError
from strata.models.configuration_model import ConfigurationModel
from strata.models.tenant_model import TenantModel
from strata.services.tenant_service import TenantService


def _data(relative_path: str) -> str:
    return str(Path(__file__).parent.parent.parent / "data" / relative_path)


def _make_tenant_data(code="acme", meta_name="acme", zones=None, **spec_overrides):
    """Build a minimal in-memory tenant data dict."""
    spec = {
        "code": code,
        "name": "ACME Corporation",
        "zones": zones if zones is not None else ["eu-west"],
    }
    spec.update(spec_overrides)
    return {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "tenant",
        "meta": {"name": meta_name},
        "spec": spec,
    }


def _make_config_with_zones(zone_names):
    """Build a ConfigurationModel with the given zone names."""
    zones = [{"name": z, "regions": [f"{z}-region"]} for z in zone_names]
    data = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "configuration",
        "meta": {"name": "test_config"},
        "spec": {"zones": zones},
    }
    return ConfigurationModel.model_validate(data)


class TestTenantService:
    """Tests for TenantService loading, validation, and accessors."""

    @pytest.fixture
    def service(self):
        """Load the standard tenant test fixture."""
        return TenantService(_data("tenants/tenant-standard.yaml"))

    def test_get_model_class(self, service):
        assert service._get_model_class() == TenantModel

    def test_validate_standard(self, service):
        is_valid, errors = service.validate()
        assert is_valid, f"Validation failed: {errors}"
        assert errors == []
        assert service.is_validated()
        assert service.model is not None

    def test_validate_sets_model(self, service):
        service.validate()
        assert isinstance(service.model, TenantModel)

    def test_get_kind_after_validate(self, service):
        service.validate()
        assert service.get_kind() == "tenant"

    def test_get_name_after_validate(self, service):
        service.validate()
        assert service.get_name() == "acme"

    def test_validate_empty_data(self):
        svc = TenantService(data={})
        is_valid, errors = svc.validate()
        assert not is_valid
        assert len(errors) > 0

    def test_validate_in_memory_data(self):
        svc = TenantService(data=_make_tenant_data())
        is_valid, errors = svc.validate()
        assert is_valid, f"Validation failed: {errors}"

    # --- Accessor tests ---

    def test_get_code(self, service):
        service.validate()
        assert service.get_code() == "acme"

    def test_get_display_name(self, service):
        service.validate()
        assert service.get_display_name() == "ACME Corporation"

    def test_get_zones(self, service):
        service.validate()
        zones = service.get_zones()
        assert isinstance(zones, list)
        assert len(zones) > 0

    def test_get_environments(self, service):
        service.validate()
        envs = service.get_environments()
        assert isinstance(envs, list)
        assert envs == ["environments/tiers/standard.yaml"]

    def test_get_configuration(self, service):
        service.validate()
        cfg = service.get_configuration()
        assert isinstance(cfg, dict)

    def test_get_properties(self, service):
        service.validate()
        props = service.get_properties()
        assert isinstance(props, dict)

    def test_get_custom(self, service):
        service.validate()
        custom = service.get_custom()
        assert isinstance(custom, dict)

    def test_get_properties_empty_when_unset(self):
        """get_properties returns {} when properties block is absent."""
        svc = TenantService(data=_make_tenant_data())
        svc.validate()
        assert svc.get_properties() == {}

    def test_get_custom_empty_when_unset(self):
        """get_custom returns {} when custom block is absent."""
        svc = TenantService(data=_make_tenant_data())
        svc.validate()
        assert svc.get_custom() == {}

    def test_get_properties_returns_values(self):
        """get_properties returns the configured key/value pairs."""
        svc = TenantService(data=_make_tenant_data(properties={"tier": "enterprise"}))
        svc.validate()
        assert svc.get_properties() == {"tier": "enterprise"}

    def test_get_custom_returns_values(self):
        """get_custom returns the configured key/value pairs."""
        svc = TenantService(data=_make_tenant_data(custom={"owner": "Platform Team"}))
        svc.validate()
        assert svc.get_custom() == {"owner": "Platform Team"}

    def test_get_code_before_validate_raises(self):
        svc = TenantService(data=_make_tenant_data())
        with pytest.raises(ServiceNotValidatedError):
            svc.get_code()

    def test_get_zones_before_validate_raises(self):
        svc = TenantService(data=_make_tenant_data())
        with pytest.raises(ServiceNotValidatedError):
            svc.get_zones()

    def test_get_configuration_empty_when_unset(self):
        """get_configuration returns {} when configuration block is absent."""
        svc = TenantService(data=_make_tenant_data())
        svc.validate()
        assert svc.get_configuration() == {}

    def test_get_environments_empty_when_unset(self):
        """get_environments returns [] when environments block is absent."""
        svc = TenantService(data=_make_tenant_data())
        svc.validate()
        assert svc.get_environments() == []

    # --- Phase 2 dynamic validation ---

    def test_phase2_code_matches_meta_name(self):
        """Phase 2 passes when spec.code matches meta.name."""
        svc = TenantService(data=_make_tenant_data(code="acme", meta_name="acme"))
        config = _make_config_with_zones(["eu-west"])
        is_valid, errors = svc.validate(configuration_model=config)
        assert is_valid, f"Expected valid but got errors: {errors}"

    def test_phase2_code_mismatch_fails(self):
        """Phase 2 rejects mismatched spec.code and meta.name."""
        svc = TenantService(data=_make_tenant_data(code="other_code", meta_name="acme"))
        config = _make_config_with_zones(["eu-west"])
        is_valid, errors = svc.validate(configuration_model=config)
        assert not is_valid
        assert any("must match" in e for e in errors)

    def test_phase2_zone_in_config_passes(self):
        """Phase 2 passes when tenant zone exists in configuration zones."""
        svc = TenantService(data=_make_tenant_data(zones=["eu-west"]))
        config = _make_config_with_zones(["eu-west", "us-east"])
        is_valid, errors = svc.validate(configuration_model=config)
        assert is_valid, f"Expected valid but got errors: {errors}"

    def test_phase2_unknown_zone_fails(self):
        """Phase 2 rejects a zone not defined in configuration.spec.zones."""
        svc = TenantService(data=_make_tenant_data(zones=["nonexistent-zone"]))
        config = _make_config_with_zones(["eu-west"])
        is_valid, errors = svc.validate(configuration_model=config)
        assert not is_valid
        assert any("nonexistent-zone" in e for e in errors)

    def test_phase2_no_config_zones_but_tenant_has_zones_fails(self):
        """Phase 2 rejects tenant zones when config has no zones defined."""
        svc = TenantService(data=_make_tenant_data(zones=["eu-west"]))
        # ConfigurationModel with no zones block
        config_data = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "configuration",
            "meta": {"name": "test_config"},
            "spec": {},
        }
        config = ConfigurationModel.model_validate(config_data)
        is_valid, errors = svc.validate(configuration_model=config)
        assert not is_valid
        assert any("zones" in e.lower() for e in errors)

    def test_phase2_environments_path_missing_fails(self, tmp_path):
        """Phase 2 rejects environment paths that do not exist on disk."""
        data = _make_tenant_data(environments=["environments/tiers/nonexistent.yaml"])
        svc = TenantService(data=data)
        config = _make_config_with_zones(["eu-west"])
        is_valid, errors = svc.validate(configuration_model=config, work_path=str(tmp_path))
        assert not is_valid
        assert len(errors) > 0

    def test_phase2_environments_path_exists_passes(self, tmp_path):
        """Phase 2 passes when environment file path resolves on disk."""
        env_dir = tmp_path / "environments" / "tiers"
        env_dir.mkdir(parents=True)
        (env_dir / "standard.yaml").write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: environment\n")
        data = _make_tenant_data(environments=["environments/tiers/standard.yaml"])
        svc = TenantService(data=data)
        config = _make_config_with_zones(["eu-west"])
        is_valid, errors = svc.validate(configuration_model=config, work_path=str(tmp_path))
        assert is_valid, f"Expected valid but got errors: {errors}"

    def test_phase2_without_config_model_skips_dynamic(self):
        """validate() without configuration_model skips Phase 2 (zones, code match not checked)."""
        svc = TenantService(data=_make_tenant_data(code="other_code", meta_name="acme"))
        is_valid, errors = svc.validate()
        # Phase 1 passes (structurally valid); Phase 2 skipped without config model
        assert is_valid, f"Expected Phase 1 to pass but got errors: {errors}"
