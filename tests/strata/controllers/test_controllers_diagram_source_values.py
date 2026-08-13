#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source_values.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : VARIABLES/SECRETS/FEATURES/VALUES diagram source resolver tests (ADR-0034 Task E).
===============================================================================
"""

import pytest

import strata.controllers.diagram_source_controller as module
from strata.controllers.diagram_resolve_controller import DiagramResolveController
from strata.controllers.diagram_source_controller import DiagramSourceController
from strata.models.diagram_model import DiagramSourceModel


def _source(type_: str, filter_: dict | None = None) -> DiagramSourceModel:
    payload: dict = {"type": type_}
    if filter_ is not None:
        payload["filter"] = filter_
    return DiagramSourceModel.model_validate(payload)


DEPLOYMENT_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: sample_deploy
spec:
  workspace:
    name: sample_ws
    file: workspace.yaml
  environments:
    - file: env-prd.yaml
"""

WORKSPACE_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: sample_ws
spec: {}
"""

ENVIRONMENT_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: env_prd
spec:
  variables:
    - key: REGION
      store: constant
      value: eu-west
    - key: SUBSCRIPTION_ID
      store: environment
      value: AZURE_SUBSCRIPTION_ID
    - key: APP_TOGGLE
      store: azure-appconfig
      value: app/toggle
  secrets:
    - key: DB_PASSWORD
      store: vault
      value: secret/data/db#password
    - key: API_TOKEN
      store: environment
      value: API_TOKEN
  features:
    - key: NEW_UI
      store: constant
      value: true
"""


@pytest.fixture
def values_workspace(tmp_path):
    (tmp_path / "deploy.yaml").write_text(DEPLOYMENT_YAML, encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
    (tmp_path / "env-prd.yaml").write_text(ENVIRONMENT_YAML, encoding="utf-8")
    return tmp_path


class TestVariablesSource:
    @pytest.fixture
    def variables(self, values_workspace):
        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        return controller.resolve([_source("variables")])["variables"]

    def test_one_node_per_declared_variable(self, variables):
        assert {n["label"] for n in variables["nodes"]} == {"REGION", "SUBSCRIPTION_ID", "APP_TOGGLE"}

    def test_uri_is_environment_plus_variable_key(self, variables):
        node = next(n for n in variables["nodes"] if n["label"] == "REGION")
        assert node["uri"] == "strata://environment/env_prd/variable/REGION"

    def test_no_value_or_pointer_is_ever_exposed(self, variables):
        """A 'constant' today is one accidental edit away from holding a real secret —
        never show a value or pointer for any kind, regardless of store type."""
        for node in variables["nodes"]:
            assert "value" not in node["metadata"]
            assert "pointer" not in node["metadata"]

    def test_metadata_is_exactly_store_and_environment(self, variables):
        for node in variables["nodes"]:
            assert set(node["metadata"]) == {"store", "environment"}

    def test_offline_vs_live_status(self, variables):
        by_label = {n["label"]: n for n in variables["nodes"]}
        assert by_label["REGION"]["status"] == "offline"
        assert by_label["SUBSCRIPTION_ID"]["status"] == "offline"
        assert by_label["APP_TOGGLE"]["status"] == "live"


class TestSecretsSourceNeverLeaksAValue:
    """Security-critical: a secret node must never carry its resolved value or pointer."""

    @pytest.fixture
    def secrets(self, values_workspace):
        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        return controller.resolve([_source("secrets")])["secrets"]

    def test_one_node_per_declared_secret(self, secrets):
        assert {n["label"] for n in secrets["nodes"]} == {"DB_PASSWORD", "API_TOKEN"}

    def test_no_value_field_for_any_secret(self, secrets):
        assert all("value" not in n["metadata"] for n in secrets["nodes"])

    def test_no_pointer_field_for_any_secret(self, secrets):
        """Even the store pointer (env var name, vault path) is withheld — key + store only."""
        assert all("pointer" not in n["metadata"] for n in secrets["nodes"])

    def test_metadata_is_exactly_key_store_environment(self, secrets):
        for node in secrets["nodes"]:
            assert set(node["metadata"]) == {"store", "environment"}

    def test_never_calls_resolve_values(self, values_workspace, monkeypatch):
        """The diagram source must never touch ValueController.resolve_values — it
        always attempts live store contact and returns the actual secret value."""
        from strata.controllers.value_controller import ValueController

        def _fail(*args, **kwargs):
            raise AssertionError("resolve_values() must never be called by a diagram source")

        monkeypatch.setattr(ValueController, "resolve_values", _fail)
        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        controller.resolve([_source("secrets"), _source("values")])

    def test_offline_vs_live_status(self, secrets):
        by_label = {n["label"]: n for n in secrets["nodes"]}
        assert by_label["DB_PASSWORD"]["status"] == "live"
        assert by_label["API_TOKEN"]["status"] == "offline"

    def test_uri_is_environment_plus_secret_key(self, secrets):
        node = next(n for n in secrets["nodes"] if n["label"] == "DB_PASSWORD")
        assert node["uri"] == "strata://environment/env_prd/secret/DB_PASSWORD"


class TestFeaturesSource:
    def test_constant_feature_carries_no_value_either(self, values_workspace):
        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("features")])["features"]
        assert result["nodes"][0]["label"] == "NEW_UI"
        assert "value" not in result["nodes"][0]["metadata"]
        assert "pointer" not in result["nodes"][0]["metadata"]
        assert set(result["nodes"][0]["metadata"]) == {"store", "environment"}


class TestValuesSourceIsTheUnion:
    def test_combines_variables_secrets_and_features(self, values_workspace):
        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("values")])["values"]
        kinds = {n["kind"] for n in result["nodes"]}
        assert kinds == {"variable", "secret", "feature"}
        assert len(result["nodes"]) == 6

    def test_secrets_in_the_union_still_carry_no_value(self, values_workspace):
        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("values")])["values"]
        assert all("value" not in n["metadata"] and "pointer" not in n["metadata"] for n in result["nodes"])


class TestValueSourcesShareOneEnvironmentResolution:
    def test_environment_is_resolved_once_across_all_four_sources(self, values_workspace, monkeypatch):
        original = module.EnvironmentService.validate
        calls = []

        def counting_validate(self, *args, **kwargs):
            calls.append(1)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(module.EnvironmentService, "validate", counting_validate)

        controller = DiagramSourceController(values_workspace, entry="deploy.yaml", no_validate=True)
        controller.resolve(
            [
                _source("variables"),
                _source("secrets"),
                _source("features"),
                _source("values"),
                _source("environments"),
            ]
        )
        assert len(calls) == 1


class TestValueUriRoundTrip:
    def test_variable_uri_resolves(self, values_workspace):
        result = DiagramResolveController(values_workspace).resolve("strata://environment/env_prd/variable/REGION")
        assert result is not None
        assert result["file"] == "env-prd.yaml"

    def test_secret_uri_resolves(self, values_workspace):
        result = DiagramResolveController(values_workspace).resolve("strata://environment/env_prd/secret/DB_PASSWORD")
        assert result is not None

    def test_feature_uri_resolves(self, values_workspace):
        result = DiagramResolveController(values_workspace).resolve("strata://environment/env_prd/feature/NEW_UI")
        assert result is not None
