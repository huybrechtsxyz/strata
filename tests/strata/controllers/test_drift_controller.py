"""Unit tests for DriftController, DriftClassifier, and DriftHistoryStore."""

from typing import Any, Dict
from unittest.mock import MagicMock, patch

from strata.controllers.drift_controller import DriftClassifier, DriftController, _extract_changed_attributes
from strata.models.drift_model import DriftEntry, DriftReport, DriftSeverity
from strata.utils.drift_history import DriftHistoryStore

# ---------------------------------------------------------------------------
# DriftClassifier tests
# ---------------------------------------------------------------------------


_BASIC_RULES: Dict[str, Any] = {
    "rules": [
        {"attribute": "tags", "severity": "low"},
        {"resource_type": "azurerm_network_security_rule", "severity": "critical"},
        {"resource_type": "azurerm_virtual_machine*", "severity": "high"},
    ],
    "defaults": {"severity": "medium"},
}


class TestDriftClassifier:
    def test_attribute_rule_takes_priority(self):
        """Attribute-level rule beats resource-type rule."""
        clf = DriftClassifier(_BASIC_RULES)
        # tags change on an NSG rule → attribute rule wins (low), not type rule (critical)
        sev = clf.classify("azurerm_network_security_rule", ["tags"])
        assert sev == DriftSeverity.LOW

    def test_resource_type_exact_match(self):
        clf = DriftClassifier(_BASIC_RULES)
        sev = clf.classify("azurerm_network_security_rule", ["security_rule"])
        assert sev == DriftSeverity.CRITICAL

    def test_resource_type_glob_match(self):
        clf = DriftClassifier(_BASIC_RULES)
        sev = clf.classify("azurerm_virtual_machine_scale_set", ["sku"])
        assert sev == DriftSeverity.HIGH

    def test_default_severity_when_no_rule_matches(self):
        clf = DriftClassifier(_BASIC_RULES)
        sev = clf.classify("some_unknown_resource", ["some_attribute"])
        assert sev == DriftSeverity.MEDIUM

    def test_no_rules_returns_default(self):
        clf = DriftClassifier({"rules": [], "defaults": {"severity": "info"}})
        sev = clf.classify("any_resource", ["any_attr"])
        assert sev == DriftSeverity.INFO

    def test_case_insensitive_resource_type_match(self):
        clf = DriftClassifier(_BASIC_RULES)
        # Pattern is lowercase; resource type uppercase — should still match
        sev = clf.classify("AZURERM_NETWORK_SECURITY_RULE", ["count"])
        assert sev == DriftSeverity.CRITICAL

    def test_builtin_rules_load(self, tmp_path):
        """DriftClassifier.load() should succeed with no workspace overrides."""
        clf = DriftClassifier.load(tmp_path)
        assert clf is not None
        # A known rule should classify correctly
        sev = clf.classify("azurerm_role_assignment", ["role_definition_id"])
        assert sev == DriftSeverity.CRITICAL

    def test_workspace_rules_override_builtin(self, tmp_path):
        """Workspace-level rules file prepends and takes priority."""
        solution_dir = tmp_path / ".strata"
        solution_dir.mkdir()
        rules_file = solution_dir / "drift_rules.yaml"
        rules_file.write_text(
            "rules:\n  - resource_type: 'azurerm_role_assignment'\n    severity: info\ndefaults:\n  severity: info\n"
        )
        clf = DriftClassifier.load(tmp_path)
        sev = clf.classify("azurerm_role_assignment", ["role_definition_id"])
        assert sev == DriftSeverity.INFO


# ---------------------------------------------------------------------------
# _extract_changed_attributes tests
# ---------------------------------------------------------------------------


class TestExtractChangedAttributes:
    def test_detects_changed_values(self):
        change = {
            "before": {"size": "small", "tags": {"env": "dev"}},
            "after": {"size": "large", "tags": {"env": "dev"}},
        }
        result = _extract_changed_attributes(change)
        assert "size" in result
        assert "tags" not in result

    def test_detects_added_keys(self):
        change = {"before": {}, "after": {"new_key": "value"}}
        result = _extract_changed_attributes(change)
        assert "new_key" in result

    def test_detects_removed_keys(self):
        change = {"before": {"old_key": "val"}, "after": {}}
        result = _extract_changed_attributes(change)
        assert "old_key" in result

    def test_empty_before_and_after(self):
        assert _extract_changed_attributes({"before": {}, "after": {}}) == []


# ---------------------------------------------------------------------------
# DriftSeverity ordering tests
# ---------------------------------------------------------------------------


class TestDriftSeverityOrdering:
    def test_critical_greater_than_high(self):
        assert DriftSeverity.CRITICAL > DriftSeverity.HIGH

    def test_high_greater_than_medium(self):
        assert DriftSeverity.HIGH > DriftSeverity.MEDIUM

    def test_medium_greater_than_low(self):
        assert DriftSeverity.MEDIUM > DriftSeverity.LOW

    def test_low_greater_than_info(self):
        assert DriftSeverity.LOW > DriftSeverity.INFO

    def test_critical_ge_itself(self):
        assert DriftSeverity.CRITICAL >= DriftSeverity.CRITICAL

    def test_info_le_critical(self):
        assert DriftSeverity.INFO <= DriftSeverity.CRITICAL


# ---------------------------------------------------------------------------
# DriftReport tests
# ---------------------------------------------------------------------------


class TestDriftReport:
    def _make_entry(self, sev: DriftSeverity) -> DriftEntry:
        return DriftEntry(
            address=f"azurerm_res.foo_{sev.value}",
            resource_type="azurerm_res",
            action="update",
            severity=sev,
            stage="infra",
            changed_attributes=["attr"],
        )

    def test_has_drift_false_when_empty(self):
        report = DriftReport(deployment="d", checked_at="2026-01-01T00:00:00Z", stages_checked=["s"])
        assert not report.has_drift

    def test_has_drift_true_when_entries(self):
        entry = self._make_entry(DriftSeverity.LOW)
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"], entries=[entry])
        assert report.has_drift

    def test_max_severity_none_when_empty(self):
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"])
        assert report.max_severity is None

    def test_max_severity_returns_highest(self):
        entries = [
            self._make_entry(DriftSeverity.LOW),
            self._make_entry(DriftSeverity.HIGH),
            self._make_entry(DriftSeverity.MEDIUM),
        ]
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"], entries=entries)
        assert report.max_severity == DriftSeverity.HIGH

    def test_above_threshold_false_when_empty(self):
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"])
        assert not report.above_threshold(DriftSeverity.INFO)

    def test_above_threshold_true_for_high_entry_vs_medium_threshold(self):
        entry = self._make_entry(DriftSeverity.HIGH)
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"], entries=[entry])
        assert report.above_threshold(DriftSeverity.MEDIUM)

    def test_above_threshold_false_below_threshold(self):
        entry = self._make_entry(DriftSeverity.LOW)
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"], entries=[entry])
        assert not report.above_threshold(DriftSeverity.HIGH)

    def test_to_dict_structure(self):
        entry = self._make_entry(DriftSeverity.CRITICAL)
        report = DriftReport(deployment="d", checked_at="t", stages_checked=["s"], entries=[entry])
        d = report.to_dict()
        assert d["has_drift"] is True
        assert d["max_severity"] == "critical"
        assert len(d["entries"]) == 1
        assert d["entries"][0]["address"] == entry.address


# ---------------------------------------------------------------------------
# DriftHistoryStore tests
# ---------------------------------------------------------------------------


class TestDriftHistoryStore:
    def test_load_creates_empty_state_when_no_file(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        assert store.get_entry("nonexistent") is None

    def test_record_run_creates_entry(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        store.record_run("2026-01-01T00:00:00Z", ["aws_instance.web"])
        entry = store.get_entry("aws_instance.web")
        assert entry is not None
        assert entry["consecutive_checks"] == 1
        assert entry["first_detected"] == "2026-01-01T00:00:00Z"

    def test_record_run_increments_consecutive(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        store.record_run("2026-01-01T00:00:00Z", ["aws_instance.web"])
        store.record_run("2026-01-02T00:00:00Z", ["aws_instance.web"])
        entry = store.get_entry("aws_instance.web")
        assert entry["consecutive_checks"] == 2

    def test_record_run_resets_consecutive_when_resolved(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        store.record_run("2026-01-01T00:00:00Z", ["aws_instance.web"])
        # Second run: address no longer drifting
        store.record_run("2026-01-02T00:00:00Z", [])
        entry = store.get_entry("aws_instance.web")
        assert entry["consecutive_checks"] == 0

    def test_save_and_reload(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        store.record_run("2026-01-01T00:00:00Z", ["some_resource.name"])
        store.save()

        store2 = DriftHistoryStore(tmp_path, "my-deploy")
        store2.load()
        entry = store2.get_entry("some_resource.name")
        assert entry is not None
        assert entry["consecutive_checks"] == 1

    def test_first_detected_preserved_across_runs(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        store.record_run("2026-01-01T00:00:00Z", ["res.a"])
        store.save()

        store2 = DriftHistoryStore(tmp_path, "my-deploy")
        store2.load()
        store2.record_run("2026-01-02T00:00:00Z", ["res.a"])
        store2.save()

        entry = store2.get_entry("res.a")
        assert entry["first_detected"] == "2026-01-01T00:00:00Z"
        assert entry["consecutive_checks"] == 2

    def test_gitignore_created(self, tmp_path):
        store = DriftHistoryStore(tmp_path, "my-deploy")
        store.load()
        store.record_run("2026-01-01T00:00:00Z", ["res.a"])
        store.save()
        gitignore = tmp_path / ".gitignore"
        assert gitignore.exists()
        assert ".strata/drift/" in gitignore.read_text()


# ---------------------------------------------------------------------------
# DriftController unit tests
# ---------------------------------------------------------------------------


class TestDriftController:
    def _make_stage(self, name: str = "infra") -> MagicMock:
        stage = MagicMock()
        stage.name = name
        stage.provisioner = "my-provisioner"
        stage.topology = None
        return stage

    def _make_deployment_service(self) -> MagicMock:
        svc = MagicMock()
        svc.model.meta.name = "test-deploy"
        svc.model.spec.stages = []
        return svc

    def test_detect_drift_no_stages_returns_empty_report(self, tmp_path):
        ctrl = DriftController()
        dep_svc = self._make_deployment_service()
        cfg_svc = MagicMock()

        report = ctrl.detect_drift(
            stages=[],
            deployment_service=dep_svc,
            configuration_service=cfg_svc,
            build_path=tmp_path,
            work_path=tmp_path,
        )
        assert isinstance(report, DriftReport)
        assert not report.has_drift
        assert report.stages_checked == []

    def test_detect_drift_deployer_resolve_fails_records_error(self, tmp_path):
        ctrl = DriftController()
        dep_svc = self._make_deployment_service()
        cfg_svc = MagicMock()
        stage = self._make_stage()

        with patch(
            "strata.controllers.drift_controller.DeployerFactory.resolve_type",
            return_value=(None, ["some resolution error"]),
        ):
            report = ctrl.detect_drift(
                stages=[stage],
                deployment_service=dep_svc,
                configuration_service=cfg_svc,
                build_path=tmp_path,
                work_path=tmp_path,
            )

        assert isinstance(report, DriftReport)
        assert not report.has_drift
        assert ctrl.has_errors()

    def test_detect_drift_deployer_returns_no_changes(self, tmp_path):
        ctrl = DriftController()
        dep_svc = self._make_deployment_service()
        cfg_svc = MagicMock()
        stage = self._make_stage()

        deployer_mock = MagicMock()
        deployer_mock.validate_workspace.return_value = (True, [])
        deployer_mock.validate_environment.return_value = (True, [])
        deployer_mock.setup.return_value = (True, [])
        deployer_mock.drift.return_value = (True, {"resource_changes": []}, [])

        with (
            patch(
                "strata.controllers.drift_controller.DeployerFactory.resolve_type",
                return_value=("terraform", []),
            ),
            patch(
                "strata.controllers.drift_controller.DeployerFactory.create",
                return_value=deployer_mock,
            ),
        ):
            report = ctrl.detect_drift(
                stages=[stage],
                deployment_service=dep_svc,
                configuration_service=cfg_svc,
                build_path=tmp_path,
                work_path=tmp_path,
            )

        assert not report.has_drift
        assert "infra" in report.stages_checked

    def test_detect_drift_classifies_changes_correctly(self, tmp_path):
        ctrl = DriftController()
        dep_svc = self._make_deployment_service()
        cfg_svc = MagicMock()
        stage = self._make_stage()

        resource_changes = [
            {
                "address": "azurerm_network_security_rule.allow_http",
                "type": "azurerm_network_security_rule",
                "change": {
                    "actions": ["update"],
                    "before": {"priority": 100},
                    "after": {"priority": 200},
                },
            }
        ]

        deployer_mock = MagicMock()
        deployer_mock.validate_workspace.return_value = (True, [])
        deployer_mock.validate_environment.return_value = (True, [])
        deployer_mock.setup.return_value = (True, [])
        deployer_mock.drift.return_value = (True, {"resource_changes": resource_changes}, [])

        with (
            patch(
                "strata.controllers.drift_controller.DeployerFactory.resolve_type",
                return_value=("terraform", []),
            ),
            patch(
                "strata.controllers.drift_controller.DeployerFactory.create",
                return_value=deployer_mock,
            ),
        ):
            report = ctrl.detect_drift(
                stages=[stage],
                deployment_service=dep_svc,
                configuration_service=cfg_svc,
                build_path=tmp_path,
                work_path=tmp_path,
            )

        assert report.has_drift
        assert len(report.entries) == 1
        assert report.entries[0].severity == DriftSeverity.CRITICAL
        assert report.entries[0].address == "azurerm_network_security_rule.allow_http"
        assert report.summary.critical == 1
