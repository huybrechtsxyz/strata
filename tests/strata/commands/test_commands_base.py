#!/usr/bin/env python3
"""Unit tests for BaseCommand's audit-mutating-operation classification (ADR-0066)."""

from unittest.mock import MagicMock, patch

import pytest

from strata.commands.base_command import BaseCommand
from strata.logger import get_audit_log_source, get_logger, shutdown_audit
from strata.models.audit_config_model import AuditConfigModel, AuditJournalModel


def _op(name: str) -> bool:
    class _Cmd(BaseCommand):
        OPERATION = name

    return _Cmd._is_audit_mutating_operation()


class TestIsAuditMutatingOperation:
    def test_list_suffix_is_read_only(self):
        assert _op("workitem_list") is False
        assert _op("secret_list") is False
        assert _op("deploy_list") is False

    def test_show_suffix_is_read_only(self):
        assert _op("workitem_show") is False
        assert _op("deploy_show") is False

    def test_status_suffix_is_read_only(self):
        assert _op("tools_status") is False
        assert _op("deploy_status") is False
        assert _op("cache_status") is False

    def test_schema_prefix_is_read_only(self):
        assert _op("schema_get") is False
        assert _op("schema_list") is False
        assert _op("schema_export") is False

    def test_mutating_operations_are_audited(self):
        assert _op("deploy_run") is True
        assert _op("build_run") is True
        assert _op("solution_repo_add") is True
        assert _op("secret_put") is True
        assert _op("workitem_approve") is True
        assert _op("new") is True

    def test_history_and_get_operations_are_still_audited(self):
        """Not in ADR-0066's explicit read-only list — left audited deliberately."""
        assert _op("deploy_history") is True
        assert _op("secret_get") is True


def _make_command(tmp_path):
    cmd = BaseCommand(work_path=str(tmp_path))
    cmd.logger = get_logger("test.commands.base")
    return cmd


class TestApplyAuditJournalConfig:
    """ADR-0066 step 6: Phase 1 reconfiguration of the journal from spec.audit.journal."""

    @pytest.fixture(autouse=True)
    def _neutralize_pytest_noop_guard(self, monkeypatch):
        """`configure_audit_log()` no-ops under pytest (see logger/audit.py); this
        class exercises it directly, so neutralise the guard the same way
        `tests/strata/logger/test_audit.py` does — patch the read side, since
        deleting PYTEST_CURRENT_TEST doesn't stick (pytest re-sets it after fixture
        setup, at the start of the "call" phase).
        """
        import os as os_module

        real_get = os_module.environ.get

        def _fake_get(key, default=None):
            if key == "PYTEST_CURRENT_TEST":
                return default
            return real_get(key, default)

        monkeypatch.setattr(os_module.environ, "get", _fake_get)

    def setup_method(self):
        shutdown_audit()

    def teardown_method(self):
        shutdown_audit()

    def _config_service_with(self, journal):
        mock_service = MagicMock()
        mock_service.model.spec.audit = AuditConfigModel(journal=journal) if journal else None
        return mock_service

    def test_noop_when_journal_not_declared(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=self._config_service_with(None),
        ):
            cmd._apply_audit_journal_config()
        assert get_audit_log_source() is None

    def test_reconfigures_from_journal_path(self, tmp_path):
        cmd = _make_command(tmp_path)
        journal = AuditJournalModel(path="custom/audit.log")
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=self._config_service_with(journal),
        ):
            cmd._apply_audit_journal_config()
        assert get_audit_log_source() == "spec_audit"

    def test_skips_when_logging_yaml_already_configured(self, tmp_path):
        from strata.logger import configure_audit_log

        cmd = _make_command(tmp_path)
        configure_audit_log(log_path=str(tmp_path / "existing.log"), source="logging_yaml")
        journal = AuditJournalModel(path="custom/audit.log")
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            return_value=self._config_service_with(journal),
        ):
            cmd._apply_audit_journal_config()
        # logging.yaml outranks spec.audit.journal — source must remain unchanged
        assert get_audit_log_source() == "logging_yaml"

    def test_resolution_failure_is_non_fatal(self, tmp_path):
        cmd = _make_command(tmp_path)
        with patch(
            "strata.services.configuration_service.ConfigurationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            # Should not raise
            cmd._apply_audit_journal_config()
