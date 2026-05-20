"""Tests for strata.logger.audit — dedicated audit logger."""

import importlib
import json

import pytest

# Import the module directly (avoid name collision with the 'audit' function at package level)
_audit_mod = importlib.import_module("strata.logger.audit")

from strata.logger.audit import (
    audit,
    configure_audit_log,
    is_audit_configured,
    shutdown_audit,
)


@pytest.fixture(autouse=True)
def _reset_audit_logger():
    """Reset the audit logger state between tests."""
    # Shutdown any existing handlers before resetting
    shutdown_audit()
    _audit_mod._audit_logger = None
    yield
    # Cleanup after test
    shutdown_audit()
    _audit_mod._audit_logger = None


class TestConfigureAuditLog:
    def test_creates_log_file(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))
        assert is_audit_configured()
        # Emit something to force file creation
        audit("test.action")
        assert log_path.exists()

    def test_creates_parent_directories(self, tmp_path):
        log_path = tmp_path / "nested" / "dir" / "audit.log"
        configure_audit_log(log_path=str(log_path))
        audit("test.action")
        assert log_path.exists()

    def test_is_audit_configured_reflects_state(self, tmp_path):
        # Force unconfigured state
        _audit_mod._audit_logger = None
        assert not is_audit_configured()
        # After configure, should be configured
        configure_audit_log(log_path=str(tmp_path / "audit.log"))
        assert is_audit_configured()


class TestAudit:
    def test_silent_noop_when_not_configured(self):
        # Should not raise
        audit("test.action", outcome="success", target="some-target")

    def test_writes_ndjson_entry(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("sln.init", target="my-project", detail={"work_path": "/tmp/project"})
        shutdown_audit()

        content = log_path.read_text(encoding="utf-8").strip()
        entry = json.loads(content)
        assert entry["action"] == "sln.init"
        assert entry["outcome"] == "success"
        assert entry["target"] == "my-project"
        assert entry["detail"]["work_path"] == "/tmp/project"
        assert "ts" in entry

    def test_default_outcome_is_success(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("test.action")
        shutdown_audit()

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["outcome"] == "success"

    def test_failure_outcome(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("deploy.run", outcome="failure", target="terraform apply")
        shutdown_audit()

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["outcome"] == "failure"

    def test_multiple_entries_one_per_line(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("action.one")
        audit("action.two")
        audit("action.three")
        shutdown_audit()

        lines = log_path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            entry = json.loads(line)
            assert "action" in entry

    def test_omits_target_when_none(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("test.action")
        shutdown_audit()

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "target" not in entry

    def test_omits_detail_when_none(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("test.action")
        shutdown_audit()

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert "detail" not in entry

    def test_timestamp_is_utc_iso(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))

        audit("test.action")
        shutdown_audit()

        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        ts = entry["ts"]
        # UTC ISO format ends with +00:00
        assert "+" in ts or "Z" in ts


class TestShutdownAudit:
    def test_shutdown_flushes(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))
        audit("pre.shutdown")
        shutdown_audit()
        # File should contain the entry
        assert "pre.shutdown" in log_path.read_text(encoding="utf-8")

    def test_shutdown_noop_when_not_configured(self):
        # Should not raise
        shutdown_audit()
