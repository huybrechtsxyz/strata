#!/usr/bin/env python3
"""Unit tests for IntegrationService.resolve_for_provisioner() (ADR-0079)."""

from unittest.mock import MagicMock

import pytest

from strata.exceptions import IntegrationResolutionError
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.opentofu import OpenTofuIntegration
from strata.integrations.terraform import TerraformIntegration
from strata.models.integration_model import IntegrationModel
from strata.services.integration_service import IntegrationService


def _tf(name="terraform") -> TerraformIntegration:
    return TerraformIntegration(IntegrationModel(name=name, type="terraform"))


def _tofu(name="opentofu") -> OpenTofuIntegration:
    return OpenTofuIntegration(IntegrationModel(name=name, type="opentofu"))


def _iac_model(name="core_iac", integration=None):
    m = MagicMock()
    m.name = name
    m.integration = integration
    return m


class TestResolveForProvisioner:
    def setup_method(self):
        BaseIntegration._instances.clear()
        IntegrationService.reset()

    def teardown_method(self):
        BaseIntegration._instances.clear()
        IntegrationService.reset()

    def test_explicit_integration_name_wins(self):
        svc = IntegrationService.get_instance()
        tf = _tf("primary")
        svc.registry.register_integration("primary", tf)
        svc.registry.register_integration("secondary", _tf("secondary"))

        result = svc.resolve_for_provisioner(_iac_model(integration="primary"), TerraformIntegration)
        assert result is tf

    def test_explicit_integration_name_missing_raises(self):
        svc = IntegrationService.get_instance()

        with pytest.raises(IntegrationResolutionError, match="not registered"):
            svc.resolve_for_provisioner(_iac_model(integration="missing"), TerraformIntegration)

    def test_explicit_integration_wrong_class_raises(self):
        svc = IntegrationService.get_instance()
        svc.registry.register_integration("not_tf", MagicMock(spec=BaseIntegration, integration_type="git"))

        with pytest.raises(IntegrationResolutionError, match="not compatible"):
            svc.resolve_for_provisioner(_iac_model(integration="not_tf"), TerraformIntegration)

    def test_auto_bind_sole_candidate(self):
        svc = IntegrationService.get_instance()
        tf = _tf("only_one")
        svc.registry.register_integration("only_one", tf)

        result = svc.resolve_for_provisioner(_iac_model(integration=None), TerraformIntegration)
        assert result is tf

    def test_auto_bind_zero_candidates_raises(self):
        svc = IntegrationService.get_instance()

        with pytest.raises(IntegrationResolutionError, match="no TerraformIntegration registered"):
            svc.resolve_for_provisioner(_iac_model(integration=None), TerraformIntegration)

    def test_auto_bind_ambiguous_candidates_raises(self):
        svc = IntegrationService.get_instance()
        svc.registry.register_integration("legacy", _tf("legacy"))
        svc.registry.register_integration("current", _tf("current"))

        with pytest.raises(IntegrationResolutionError, match="ambiguous"):
            svc.resolve_for_provisioner(_iac_model(integration=None), TerraformIntegration)

    def test_opentofu_subclass_counts_as_terraform_candidate(self):
        """OpenTofuIntegration subclasses TerraformIntegration — a workspace with
        provisioner: terraform must still auto-bind to a lone `type: opentofu`
        integration (matches the pre-existing isinstance() behavior)."""
        svc = IntegrationService.get_instance()
        tofu = _tofu()
        svc.registry.register_integration("opentofu", tofu)

        result = svc.resolve_for_provisioner(_iac_model(integration=None), TerraformIntegration)
        assert result is tofu

    def test_opentofu_and_terraform_together_are_ambiguous(self):
        svc = IntegrationService.get_instance()
        svc.registry.register_integration("terraform", _tf())
        svc.registry.register_integration("opentofu", _tofu())

        with pytest.raises(IntegrationResolutionError, match="ambiguous"):
            svc.resolve_for_provisioner(_iac_model(integration=None), TerraformIntegration)

    def test_explicit_name_for_opentofu_narrows_to_terraform_class(self):
        """A provisioner explicitly requesting a plain TerraformIntegration by name
        should still succeed when the named entry is an OpenTofuIntegration, since
        the class check is isinstance-based (subclass compatible)."""
        svc = IntegrationService.get_instance()
        tofu = _tofu("primary")
        svc.registry.register_integration("primary", tofu)

        result = svc.resolve_for_provisioner(_iac_model(integration="primary"), TerraformIntegration)
        assert result is tofu
