#!/usr/bin/env python3
"""
===============================================================================
Script Name   : test_controllers_diagram_source_history.py
Author        : Vincent Huybrechts
Version       : 1.0.0
Python Version: 3.13+
Description   : HISTORY/PROMOTION/APPROVALS diagram source resolver tests (ADR-0034 Task D).
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


PROMOTION_RECORD_YAML = """apiVersion: strata.huybrechts.xyz/v1
kind: promotion-record
meta:
  name: prom-20260711-prd
  labels:
    target: iac_core
    ring: prd
    outcome: completed
spec:
  target:
    type: remote
    name: iac_core
    from_version: v2.4.0
    to_version: v2.5.0
  strategy: infra-cautious
  progression: standard
  rings: [dev, test, prd]
  outcome: completed
  initiated_by: test-user
  hostname: test-host
  started_at: "2026-07-11T12:00:00Z"
  completed_at: "2026-07-11T12:05:00Z"
  branch: promote/iac_core-v2.5.0-prd
  gates:
    - gate: require_progression_order
      ring: prd
      require: all
      checked_at: "2026-07-11T12:00:00Z"
      passed: true
      detail: "all rings passed"
    - gate: require_quorum
      ring: prd
      require: any_one
      checked_at: "2026-07-11T12:00:01Z"
      passed: false
      detail: "quorum not met"
"""


@pytest.fixture
def promotion_workspace(tmp_path):
    records_dir = tmp_path / ".strata" / "promotions" / "records"
    records_dir.mkdir(parents=True)
    (records_dir / "prom-20260711-prd.yaml").write_text(PROMOTION_RECORD_YAML, encoding="utf-8")
    return tmp_path


def _fake_get_logs(entries):
    def _get_logs(self, work_path, lines=50, minutes=None, level=None, session_id=None, execution_id=None):
        return True, entries, []

    return _get_logs


DEPLOY_EVENTS = [
    {
        "command": "deploy_run",
        "execution_id": "exec-001",
        "timestamp": "2026-07-11T12:00:00Z",
        "success": True,
        "file": "deploy/deploy-prd.yaml",
        "stage": "infrastructure",
    },
    {
        "command": "deploy_run",
        "execution_id": "exec-002",
        "timestamp": "2026-07-12T09:00:00Z",
        "success": False,
        "file": "deploy/deploy-prd.yaml",
        "stage": "platform",
    },
    {
        "command": "deploy_destroy",
        "execution_id": "exec-003",
        "timestamp": "2026-07-10T08:00:00Z",
        "success": None,
        "file": "deploy/deploy-prd.yaml",
        "stage": "",
    },
    {
        # Not a deploy operation — must not appear in the history source at all.
        "command": "schema_list",
        "execution_id": "exec-999",
        "timestamp": "2026-07-13T10:00:00Z",
        "success": True,
    },
]


class TestHistorySource:
    def test_only_deploy_operations_are_included(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module.SolutionController, "get_logs", _fake_get_logs(DEPLOY_EVENTS))
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("history")])["history"]
        ids = {n["id"] for n in result["nodes"]}
        assert ids == {"exec_001", "exec_002", "exec_003"}

    def test_sorted_newest_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module.SolutionController, "get_logs", _fake_get_logs(DEPLOY_EVENTS))
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("history")])["history"]
        assert [n["metadata"]["execution_id"] for n in result["nodes"]] == ["exec-002", "exec-001", "exec-003"]

    def test_status_reflects_the_success_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module.SolutionController, "get_logs", _fake_get_logs(DEPLOY_EVENTS))
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("history")])["history"]
        by_id = {n["metadata"]["execution_id"]: n for n in result["nodes"]}
        assert by_id["exec-001"]["status"] == "success"
        assert by_id["exec-002"]["status"] == "failed"
        assert by_id["exec-003"]["status"] == "unknown"

    def test_no_uri_or_location(self, tmp_path, monkeypatch):
        """A deploy log entry is not a workspace object — nothing to point at."""
        monkeypatch.setattr(module.SolutionController, "get_logs", _fake_get_logs(DEPLOY_EVENTS))
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("history")])["history"]
        assert all("uri" not in n and "location" not in n for n in result["nodes"])

    def test_no_log_file_yields_no_nodes_no_error(self, tmp_path, monkeypatch):
        monkeypatch.setattr(module.SolutionController, "get_logs", _fake_get_logs([]))
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("history")])["history"]
        assert result["nodes"] == []
        assert not controller.has_errors()


class TestPromotionSource:
    @pytest.fixture
    def promotion(self, promotion_workspace):
        controller = DiagramSourceController(promotion_workspace, no_validate=True)
        return controller.resolve([_source("promotion")])["promotion"]

    def test_one_node_per_record(self, promotion):
        assert {n["id"] for n in promotion["nodes"]} == {"prom_20260711_prd"}

    def test_status_is_the_records_own_outcome_value(self, promotion):
        assert promotion["nodes"][0]["status"] == "completed"

    def test_uri_is_the_promotion_record_document(self, promotion):
        assert promotion["nodes"][0]["uri"] == "strata://promotion-record/prom-20260711-prd"

    def test_location_points_at_the_record_file(self, promotion):
        node = promotion["nodes"][0]
        assert node["location"] == {"file": ".strata/promotions/records/prom-20260711-prd.yaml"}

    def test_metadata(self, promotion):
        node = promotion["nodes"][0]
        assert node["metadata"]["target"] == "remote/iac_core"
        assert node["metadata"]["from_version"] == "v2.4.0"
        assert node["metadata"]["to_version"] == "v2.5.0"

    def test_no_records_directory_yields_no_nodes_no_error(self, tmp_path):
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("promotion")])["promotion"]
        assert result["nodes"] == []
        assert not controller.has_errors()


class TestApprovalsSource:
    @pytest.fixture
    def approvals(self, promotion_workspace):
        controller = DiagramSourceController(promotion_workspace, no_validate=True)
        return controller.resolve([_source("approvals")])["approvals"]

    def test_one_node_per_gate_result(self, approvals):
        assert len(approvals["nodes"]) == 2

    def test_status_reflects_pass_fail(self, approvals):
        by_label = {n["label"]: n for n in approvals["nodes"]}
        assert by_label["require_progression_order (prd)"]["status"] == "passed"
        assert by_label["require_quorum (prd)"]["status"] == "failed"

    def test_uri_is_promotion_record_plus_gate(self, approvals):
        node = next(n for n in approvals["nodes"] if "require_quorum" in n["id"])
        assert node["uri"] == "strata://promotion-record/prom-20260711-prd/gate/require_quorum"

    def test_metadata(self, approvals):
        node = next(n for n in approvals["nodes"] if "require_quorum" in n["id"])
        assert node["metadata"]["detail"] == "quorum not met"
        assert node["metadata"]["promotion"] == "prom-20260711-prd"

    def test_no_records_directory_yields_no_nodes_no_error(self, tmp_path):
        controller = DiagramSourceController(tmp_path, no_validate=True)
        result = controller.resolve([_source("approvals")])["approvals"]
        assert result["nodes"] == []
        assert not controller.has_errors()


class TestPromotionUriRoundTrip:
    def test_promotion_record_uri_resolves(self, promotion_workspace):
        result = DiagramResolveController(promotion_workspace).resolve("strata://promotion-record/prom-20260711-prd")
        assert result is not None
        assert result["file"] == ".strata/promotions/records/prom-20260711-prd.yaml"

    def test_gate_uri_resolves(self, promotion_workspace):
        result = DiagramResolveController(promotion_workspace).resolve(
            "strata://promotion-record/prom-20260711-prd/gate/require_quorum"
        )
        assert result is not None
        assert result["child_kind"] == "gate"
        assert result["child_name"] == "require_quorum"
