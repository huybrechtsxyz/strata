#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source_live_state.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : DRIFT/OUTPUTS/LOCKS/REPOSITORIES/SBOM diagram source resolver
                tests (ADR-0034 Tasks F & G).
===============================================================================
"""

import json

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
  labels:
    version: v1.0.0
spec:
  workspace:
    name: sample_ws
    file: workspace.yaml
  locking:
    enabled: true
    strategy: wrap
    wait_timeout: 30m
    force_unlock_after: 8h
  stages:
    - name: infra
      provisioner: terraform
"""

WORKSPACE_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: sample_ws
spec: {}
"""

DEPLOYMENT_NO_LOCKING_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: sample_deploy
  labels:
    version: v1.0.0
spec:
  workspace:
    name: sample_ws
    file: workspace.yaml
  stages:
    - name: infra
      provisioner: terraform
"""

DEPLOYMENT_NO_VERSION_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: sample_deploy
spec:
  workspace:
    name: sample_ws
    file: workspace.yaml
  stages:
    - name: infra
      provisioner: terraform
"""


@pytest.fixture
def deployment_workspace(tmp_path):
    (tmp_path / "deploy.yaml").write_text(DEPLOYMENT_YAML, encoding="utf-8")
    (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
    return tmp_path


class TestDriftSource:
    @pytest.fixture
    def workspace_with_drift(self, deployment_workspace):
        drift_dir = deployment_workspace / ".strata" / "drift"
        drift_dir.mkdir(parents=True)
        (drift_dir / "sample_deploy.drift.json").write_text(
            json.dumps(
                {
                    "deployment": "sample_deploy",
                    "entries": {
                        "azurerm_resource_group.main": {
                            "first_detected": "2026-07-01T12:00:00Z",
                            "last_detected": "2026-07-06T09:00:00Z",
                            "consecutive_checks": 3,
                            "acknowledged": False,
                        },
                        "azurerm_network_security_rule.allow_ssh": {
                            "first_detected": "2026-06-01T12:00:00Z",
                            "last_detected": "2026-06-02T12:00:00Z",
                            "consecutive_checks": 1,
                            "acknowledged": True,
                        },
                        "azurerm_storage_account.logs": {
                            "first_detected": "2026-05-01T12:00:00Z",
                            "last_detected": "2026-05-01T12:00:00Z",
                            "consecutive_checks": 1,
                            "acknowledged": False,
                        },
                    },
                    "runs": [
                        {
                            "checked_at": "2026-07-06T09:00:00Z",
                            "addresses": ["azurerm_resource_group.main"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return deployment_workspace

    def _drift(self, workspace_with_drift):
        controller = DiagramSourceController(workspace_with_drift, entry="deploy.yaml", no_validate=True)
        return controller.resolve([_source("drift")])["drift"]

    def test_one_node_per_tracked_address(self, workspace_with_drift):
        result = self._drift(workspace_with_drift)
        assert {n["id"] for n in result["nodes"]} == {
            "azurerm_resource_group_main",
            "azurerm_network_security_rule_allow_ssh",
            "azurerm_storage_account_logs",
        }

    def test_status_reflects_run_and_acknowledgement(self, workspace_with_drift):
        result = self._drift(workspace_with_drift)
        by_id = {n["id"]: n for n in result["nodes"]}
        assert by_id["azurerm_resource_group_main"]["status"] == "drifting"
        assert by_id["azurerm_network_security_rule_allow_ssh"]["status"] == "acknowledged"
        assert by_id["azurerm_storage_account_logs"]["status"] == "resolved"

    def test_no_uri_or_location(self, workspace_with_drift):
        result = self._drift(workspace_with_drift)
        node = result["nodes"][0]
        assert "uri" not in node
        assert "location" not in node

    def test_metadata_has_no_extra_fields(self, workspace_with_drift):
        result = self._drift(workspace_with_drift)
        node = next(n for n in result["nodes"] if n["id"] == "azurerm_resource_group_main")
        assert set(node["metadata"]) == {"first_detected", "last_detected", "consecutive_checks"}

    def test_no_history_file_yields_empty(self, deployment_workspace):
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("drift")])["drift"]
        assert result == {"nodes": [], "edges": []}
        assert not controller.has_errors()


class TestLocksSource:
    def test_declared_locking_reported(self, deployment_workspace):
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("locks")])["locks"]
        assert len(result["nodes"]) == 1
        node = result["nodes"][0]
        assert node["status"] == "enabled"
        assert node["metadata"] == {
            "strategy": "wrap",
            "wait_timeout": "30m",
            "force_unlock_after": "8h",
        }
        assert node["location"] == {"file": "deploy.yaml"}

    def test_no_locking_block_yields_empty(self, tmp_path):
        (tmp_path / "deploy.yaml").write_text(DEPLOYMENT_NO_LOCKING_YAML, encoding="utf-8")
        (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        controller = DiagramSourceController(tmp_path, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("locks")])["locks"]
        assert result == {"nodes": [], "edges": []}

    def test_disabled_locking_status(self, tmp_path):
        deploy_yaml = DEPLOYMENT_YAML.replace("enabled: true", "enabled: false")
        (tmp_path / "deploy.yaml").write_text(deploy_yaml, encoding="utf-8")
        (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        controller = DiagramSourceController(tmp_path, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("locks")])["locks"]
        assert result["nodes"][0]["status"] == "disabled"


def _write_outputs_artifact(build_dir, outputs, sensitive_keys):
    build_dir.mkdir(parents=True)
    (build_dir / "deployment-outputs.json").write_text(
        json.dumps({"outputs": outputs, "sensitive_keys": sensitive_keys}),
        encoding="utf-8",
    )


class TestOutputsSource:
    def test_keys_present_values_never_read(self, deployment_workspace):
        build_dir = deployment_workspace / "build" / "sample_deploy-v1.0.0"
        _write_outputs_artifact(
            build_dir,
            outputs={"infra": {"vnet_id": "/subscriptions/.../virtualNetworks/main", "region": "westeurope"}},
            sensitive_keys=["infra.admin_password"],
        )
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("outputs")])["outputs"]
        ids = {n["id"] for n in result["nodes"]}
        assert ids == {"infra_vnet_id", "infra_region", "infra_admin_password"}
        for node in result["nodes"]:
            assert "value" not in node["metadata"]
            assert node["metadata"]["stage"] == "infra"

    def test_sensitive_status_flag(self, deployment_workspace):
        build_dir = deployment_workspace / "build" / "sample_deploy-v1.0.0"
        _write_outputs_artifact(
            build_dir,
            outputs={"infra": {"vnet_id": "value"}},
            sensitive_keys=["infra.admin_password"],
        )
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("outputs")])["outputs"]
        by_id = {n["id"]: n for n in result["nodes"]}
        assert by_id["infra_vnet_id"]["status"] == "available"
        assert by_id["infra_admin_password"]["status"] == "sensitive"

    def test_missing_artifact_reports_error(self, deployment_workspace):
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("outputs")])["outputs"]
        assert result == {"nodes": [], "edges": []}
        assert controller.has_errors()
        assert "deploy run" in controller.get_errors()[0]

    def test_missing_version_reports_error(self, tmp_path):
        (tmp_path / "deploy.yaml").write_text(DEPLOYMENT_NO_VERSION_YAML, encoding="utf-8")
        (tmp_path / "workspace.yaml").write_text(WORKSPACE_YAML, encoding="utf-8")
        controller = DiagramSourceController(tmp_path, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("outputs")])["outputs"]
        assert result == {"nodes": [], "edges": []}
        assert controller.has_errors()
        assert "meta.labels.version" in controller.get_errors()[0]


class TestSbomSource:
    def _write_sbom(self, build_dir, components):
        build_dir.mkdir(parents=True)
        (build_dir / "sbom.json").write_text(
            json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": components}),
            encoding="utf-8",
        )

    def test_one_node_per_component(self, deployment_workspace):
        build_dir = deployment_workspace / "build" / "sample_deploy-v1.0.0"
        self._write_sbom(
            build_dir,
            components=[
                {
                    "type": "library",
                    "name": "openssl",
                    "version": "1.0.1e",
                    "purl": "pkg:generic/openssl@1.0.1e",
                    "properties": [{"name": "strata:tag-stability", "value": "pinned"}],
                },
                {"type": "container", "name": "nginx", "version": "1.27", "purl": "pkg:docker/nginx@1.27"},
            ],
        )
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("sbom")])["sbom"]
        assert len(result["nodes"]) == 2
        openssl = next(n for n in result["nodes"] if n["label"] == "openssl@1.0.1e")
        assert openssl["kind"] == "library"
        assert openssl["metadata"]["purl"] == "pkg:generic/openssl@1.0.1e"
        assert openssl["metadata"]["properties"] == {"strata:tag-stability": "pinned"}

    def test_missing_artifact_reports_error(self, deployment_workspace):
        controller = DiagramSourceController(deployment_workspace, entry="deploy.yaml", no_validate=True)
        result = controller.resolve([_source("sbom")])["sbom"]
        assert result == {"nodes": [], "edges": []}
        assert controller.has_errors()
        assert "build sbom" in controller.get_errors()[0]


SOLUTION_JSON = {
    "apiVersion": "strata.huybrechts.xyz/v1",
    "kind": "solution",
    "meta": {"name": "sample_solution"},
    "spec": {
        "solution_id": "sol-0001",
        "repositories": [
            {
                "name": "haven",
                "url": "https://x-access-token:secrettoken123@github.com/example/haven.git",
                "path": "repos/haven",
                "type": "gitops",
                "branch": "main",
            },
            {
                "name": "local_repo",
                "url": ".",
                "path": "local_repo",
                "type": "local",
                "branch": "main",
            },
        ],
    },
}


class TestRepositoriesSource:
    @pytest.fixture
    def solution_workspace(self, tmp_path):
        strata_dir = tmp_path / ".strata"
        strata_dir.mkdir()
        (strata_dir / "solution.json").write_text(json.dumps(SOLUTION_JSON), encoding="utf-8")
        return tmp_path

    def test_one_node_per_repository(self, solution_workspace):
        controller = DiagramSourceController(solution_workspace)
        result = controller.resolve([_source("repositories")])["repositories"]
        assert {n["id"] for n in result["nodes"]} == {"haven", "local_repo"}

    def test_credentials_redacted_from_url(self, solution_workspace):
        controller = DiagramSourceController(solution_workspace)
        result = controller.resolve([_source("repositories")])["repositories"]
        haven = next(n for n in result["nodes"] if n["id"] == "haven")
        assert "secrettoken123" not in haven["metadata"]["url"]
        assert haven["metadata"]["url"] == "https://github.com/example/haven.git"

    def test_status_reflects_local_path_existence(self, solution_workspace):
        controller = DiagramSourceController(solution_workspace)
        result = controller.resolve([_source("repositories")])["repositories"]
        haven = next(n for n in result["nodes"] if n["id"] == "haven")
        assert haven["status"] == "missing"

    def test_no_solution_file_reports_error(self, tmp_path):
        controller = DiagramSourceController(tmp_path)
        result = controller.resolve([_source("repositories")])["repositories"]
        assert result == {"nodes": [], "edges": []}
        assert controller.has_errors()
        assert "sln init" in controller.get_errors()[0]
