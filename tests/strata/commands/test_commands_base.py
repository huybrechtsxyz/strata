#!/usr/bin/env python3
"""Unit tests for BaseCommand's audit-mutating-operation classification (ADR-0066)."""

from strata.commands.base_command import BaseCommand


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
