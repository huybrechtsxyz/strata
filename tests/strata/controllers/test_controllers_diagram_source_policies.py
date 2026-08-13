#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source_policies.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : POLICIES diagram source resolver tests (ADR-0034 Task E).
===============================================================================
"""

import pytest

from strata.controllers.diagram_resolve_controller import DiagramResolveController
from strata.controllers.diagram_source_controller import DiagramSourceController
from strata.models.configuration_model import ConfigurationModel
from strata.models.diagram_model import DiagramSourceModel
from strata.services.configuration_service import ConfigurationService


def _source(type_: str) -> DiagramSourceModel:
    return DiagramSourceModel.model_validate({"type": type_})


@pytest.fixture(autouse=True)
def reset_configuration_service():
    """The ConfigurationService is a process-wide singleton — isolate every test."""
    ConfigurationService.reset()
    yield
    ConfigurationService.reset()


def _configuration_service_with(policies: list) -> ConfigurationService:
    svc = ConfigurationService.get_instance()
    svc.model = ConfigurationModel.model_validate(
        {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "configuration",
            "meta": {"name": "sample_config"},
            "spec": {"policies": policies},
        }
    )
    return svc


class TestPoliciesRequireAnActiveProfile:
    def test_no_configuration_loaded_is_a_clear_error_not_an_empty_list(self, tmp_path):
        """Zero policies declared and 'no config loaded' must not look the same."""
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("policies")])["policies"]
        assert result["nodes"] == []
        assert controller.has_errors()
        assert "active profile" in controller.get_errors()[0]

    def test_injected_configuration_service_is_used_over_the_singleton(self, tmp_path):
        config_service = _configuration_service_with(
            [{"name": "require_tags", "type": "required_tags", "phase": "validate", "enforcement": "deny"}]
        )
        controller = DiagramSourceController(tmp_path, no_validate=True, configuration_service=config_service)
        result = controller.resolve([_source("policies")])["policies"]
        assert len(result["nodes"]) == 1


class TestPoliciesNodeShape:
    @pytest.fixture
    def policies(self, tmp_path):
        config_service = _configuration_service_with(
            [
                {
                    "name": "require_tags",
                    "type": "required_tags",
                    "phase": "validate",
                    "enforcement": "deny",
                    "enabled": True,
                },
                {
                    "name": "cost_cap",
                    "type": "cost_threshold",
                    "phase": "plan",
                    "enforcement": "warn",
                    "enabled": False,
                },
            ]
        )
        controller = DiagramSourceController(tmp_path, no_validate=True, configuration_service=config_service)
        return controller.resolve([_source("policies")])["policies"]

    def test_one_node_per_declared_policy(self, policies):
        assert {n["id"] for n in policies["nodes"]} == {"require_tags", "cost_cap"}

    def test_status_reflects_enabled_flag(self, policies):
        by_id = {n["id"]: n for n in policies["nodes"]}
        assert by_id["require_tags"]["status"] == "enabled"
        assert by_id["cost_cap"]["status"] == "disabled"

    def test_uri_is_configuration_plus_policy_name(self, policies):
        node = next(n for n in policies["nodes"] if n["id"] == "require_tags")
        assert node["uri"] == "strata://configuration/sample_config/policy/require_tags"

    def test_metadata(self, policies):
        node = next(n for n in policies["nodes"] if n["id"] == "cost_cap")
        assert node["metadata"] == {"type": "cost_threshold", "phase": "plan", "enforcement": "warn"}

    def test_no_declared_policies_is_an_empty_list_with_no_error(self, tmp_path):
        config_service = _configuration_service_with([])
        controller = DiagramSourceController(tmp_path, no_validate=True, configuration_service=config_service)
        result = controller.resolve([_source("policies")])["policies"]
        assert result["nodes"] == []
        assert not controller.has_errors()


class TestPolicyUriRoundTrip:
    def test_policy_uri_resolves(self, tmp_path):
        (tmp_path / "configuration.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: configuration\n"
            "meta:\n"
            "  name: sample_config\n"
            "spec:\n"
            "  policies:\n"
            "    - name: require_tags\n"
            "      type: required_tags\n"
            "      phase: validate\n"
            "      enforcement: deny\n",
            encoding="utf-8",
        )
        result = DiagramResolveController(tmp_path).resolve("strata://configuration/sample_config/policy/require_tags")
        assert result is not None
        assert result["file"] == "configuration.yaml"
