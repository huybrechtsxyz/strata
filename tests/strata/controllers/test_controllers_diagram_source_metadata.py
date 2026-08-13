#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source_metadata.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : STAGES/ENVIRONMENTS/TENANTS diagram source resolver tests (ADR-0034 Task C).
===============================================================================
"""

import pytest

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
  tenant: acme
  environments:
    - file: env-prd.yaml
      scope: shared
    - file: env-prd.yaml
      scope: tenant
  stages:
    - name: infra
      provisioner: terraform
    - name: platform
      provisioner: helm
      depends_on:
        - infra
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
    - key: FOO
      store: constant
      value: bar
  secrets:
    - key: DB_PASSWORD
      store: environment
      value: DB_PASSWORD
"""

TENANT_ACME_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: acme
spec:
  code: acme
  name: Acme Corp
  zones:
    - eu_west
"""

TENANT_GLOBEX_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: globex
spec:
  code: globex
  name: Globex Corp
  zones:
    - us_east
"""


@pytest.fixture
def metadata_workspace(tmp_path):
    (tmp_path / "deploy.yaml").write_text(DEPLOYMENT_YAML, encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
    (tmp_path / "env-prd.yaml").write_text(ENVIRONMENT_YAML, encoding="utf-8")
    (tmp_path / "tenants").mkdir()
    (tmp_path / "tenants" / "acme.yaml").write_text(TENANT_ACME_YAML, encoding="utf-8")
    (tmp_path / "tenants" / "globex.yaml").write_text(TENANT_GLOBEX_YAML, encoding="utf-8")
    return tmp_path


class TestStagesSource:
    @pytest.fixture
    def stages(self, metadata_workspace):
        controller = DiagramSourceController(metadata_workspace, entry="deploy.yaml", no_validate=True)
        return controller.resolve([_source("stages")])["stages"]

    def test_one_node_per_stage(self, stages):
        assert {n["id"] for n in stages["nodes"]} == {"infra", "platform"}

    def test_uri_is_deployment_plus_stage_name(self, stages):
        node = next(n for n in stages["nodes"] if n["id"] == "platform")
        assert node["uri"] == "strata://deployment/sample_deploy/stage/platform"

    def test_location_points_at_the_deployment_file(self, stages):
        node = stages["nodes"][0]
        assert node["location"] == {"file": "deploy.yaml"}

    def test_metadata(self, stages):
        node = next(n for n in stages["nodes"] if n["id"] == "platform")
        assert node["metadata"]["provisioner"] == "helm"
        assert node["metadata"]["on_failure"] == "stop"

    def test_depends_on_becomes_an_edge(self, stages):
        assert {"source": "platform", "target": "infra", "label": "depends_on"} in stages["edges"]

    def test_no_entry_point_reports_an_error(self, tmp_path):
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("stages")])["stages"]
        assert result["nodes"] == []
        assert controller.has_errors()

    def test_workspace_entry_is_not_a_deployment(self, metadata_workspace):
        """--entry pointing at a workspace file has no stages to report — not a crash."""
        controller = DiagramSourceController(metadata_workspace, entry="workspace.yaml", no_validate=True)
        result = controller.resolve([_source("stages")])["stages"]
        assert result["nodes"] == []
        assert controller.has_errors()
        assert "not a deployment" in controller.get_errors()[0]


class TestEnvironmentsSource:
    @pytest.fixture
    def environments(self, metadata_workspace):
        controller = DiagramSourceController(metadata_workspace, entry="deploy.yaml", no_validate=True)
        return controller.resolve([_source("environments")])["environments"]

    def test_node_identity_comes_from_the_referenced_document(self, environments):
        """A bare-string environments[] entry has no name of its own."""
        assert {n["id"] for n in environments["nodes"]} == {"env_prd"}

    def test_referenced_twice_with_different_scopes_is_one_node(self, environments):
        node = environments["nodes"][0]
        assert set(node["metadata"]["scopes"]) == {"shared", "tenant"}

    def test_uri_is_document_level_only(self, environments):
        assert environments["nodes"][0]["uri"] == "strata://environment/env_prd"

    def test_variable_and_secret_counts(self, environments):
        node = environments["nodes"][0]
        assert node["metadata"]["variable_count"] == 1
        assert node["metadata"]["secret_count"] == 1

    def test_cross_repo_reference_is_skipped_not_crashed(self, tmp_path):
        (tmp_path / "deploy.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: deployment\n"
            "meta:\n"
            "  name: sample_deploy\n"
            "spec:\n"
            "  workspace:\n"
            "    name: sample_ws\n"
            "    file: workspace.yaml\n"
            '  environments:\n    - "@other_repo/env.yaml"\n',
            encoding="utf-8",
        )
        (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        controller = DiagramSourceController(tmp_path, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("environments")])["environments"]
        assert result["nodes"] == []
        assert "cross-repository" in controller.get_errors()[0]

    def test_relative_ref_from_nested_deployment_resolves_against_work_path(self, tmp_path):
        """Regression test: environments[].file must resolve against work_path,
        not the deployment file's own directory — matching BaseService._resolve_file_path()."""
        (tmp_path / "deploy").mkdir()
        (tmp_path / "deploy" / "deploy.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: deployment\n"
            "meta:\n"
            "  name: sample_deploy\n"
            "spec:\n"
            "  workspace:\n"
            "    name: sample_ws\n"
            "    file: workspace.yaml\n"
            "  environments:\n"
            "    - file: env-prd.yaml\n",
            encoding="utf-8",
        )
        (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        (tmp_path / "env-prd.yaml").write_text(ENVIRONMENT_YAML, encoding="utf-8")

        controller = DiagramSourceController(tmp_path, entry="deploy/deploy.yaml", no_validate=True)
        result = controller.resolve([_source("environments")])["environments"]

        assert not controller.get_errors()
        assert {n["id"] for n in result["nodes"]} == {"env_prd"}


class TestTenantsSource:
    @pytest.fixture
    def tenants(self, metadata_workspace):
        controller = DiagramSourceController(metadata_workspace, entry="deploy.yaml", no_validate=True)
        return controller.resolve([_source("tenants")])["tenants"]

    def test_finds_every_tenant_document_in_the_tree(self, tenants):
        """No workspace-level reference list to walk — a full-tree kind scan instead."""
        assert {n["id"] for n in tenants["nodes"]} == {"acme", "globex"}

    def test_uri_is_document_level(self, tenants):
        node = next(n for n in tenants["nodes"] if n["id"] == "acme")
        assert node["uri"] == "strata://tenant/acme"

    def test_metadata(self, tenants):
        node = next(n for n in tenants["nodes"] if n["id"] == "acme")
        assert node["metadata"]["code"] == "acme"
        assert node["metadata"]["zones"] == ["eu_west"]

    def test_active_flag_matches_the_deployments_tenant(self, tenants):
        acme = next(n for n in tenants["nodes"] if n["id"] == "acme")
        globex = next(n for n in tenants["nodes"] if n["id"] == "globex")
        assert acme["metadata"]["active"] is True
        assert globex["metadata"]["active"] is False

    def test_works_with_no_resolvable_deployment_at_all(self, tmp_path):
        """Tenants must list even when there is no deployment anywhere in the workspace."""
        (tmp_path / "tenants").mkdir()
        (tmp_path / "tenants" / "acme.yaml").write_text(TENANT_ACME_YAML, encoding="utf-8")
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("tenants")])["tenants"]
        assert {n["id"] for n in result["nodes"]} == {"acme"}
        assert result["nodes"][0]["metadata"]["active"] is False
        assert not controller.has_errors()

    def test_missing_deployment_does_not_surface_as_an_error(self, tmp_path):
        """The best-effort active-tenant lookup must not contaminate errors for this source."""
        (tmp_path / "tenants").mkdir()
        (tmp_path / "tenants" / "acme.yaml").write_text(TENANT_ACME_YAML, encoding="utf-8")
        controller = DiagramSourceController(tmp_path, no_validate=True)
        controller.resolve([_source("tenants")])
        assert not controller.has_errors()

    def test_invalid_tenant_document_is_skipped_with_an_error(self, metadata_workspace):
        (metadata_workspace / "tenants" / "broken.yaml").write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: tenant\nmeta:\n  name: broken\nspec: {}\n",
            encoding="utf-8",
        )
        controller = DiagramSourceController(metadata_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("tenants")])["tenants"]
        assert {n["id"] for n in result["nodes"]} == {"acme", "globex"}
        assert controller.has_errors()


class TestStagesAndTenantsShareTheDeploymentLookup:
    def test_deployment_is_resolved_once_across_stages_environments_and_tenants(self, metadata_workspace, monkeypatch):
        import strata.controllers.diagram_source_controller as module

        original = module.GraphController.resolve_deployment
        calls = []

        def counting_resolve(self):
            calls.append(1)
            return original(self)

        monkeypatch.setattr(module.GraphController, "resolve_deployment", counting_resolve)

        controller = DiagramSourceController(metadata_workspace, entry="deploy.yaml", no_validate=True)
        controller.resolve([_source("stages"), _source("environments"), _source("tenants")])
        assert len(calls) == 1

    def test_a_deployment_error_surfaces_exactly_once_even_when_tenants_looked_first(self, tmp_path):
        """tenants' silent lookup must not suppress the error for stages/environments,
        nor cause it to appear twice when both run in the same resolve() call."""
        controller = DiagramSourceController(tmp_path, no_validate=True)
        controller.resolve([_source("tenants"), _source("stages")])
        assert controller.get_errors().count("No deployment file found. Use --entry to specify one.") == 1
