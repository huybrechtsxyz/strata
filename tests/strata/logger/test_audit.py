"""Tests for strata.logger.audit — dedicated audit logger."""

import importlib
import json
import logging.handlers

import pytest

# Import the module directly (avoid name collision with the 'audit' function at package level)
_audit_mod = importlib.import_module("strata.logger.audit")

from strata.logger.audit import (
    audit,
    configure_audit_log,
    get_audit_log_source,
    get_configured_audit_log_path,
    is_audit_configured,
    shutdown_audit,
)


@pytest.fixture(autouse=True)
def _reset_audit_logger(monkeypatch):
    """Reset the audit logger state between tests.

    This file deliberately tests ``configure_audit_log`` itself, so it neutralises the
    ADR-0066 guard that makes ``configure_audit_log`` a no-op under pytest (so the
    *rest* of the suite doesn't write to the real audit log) — otherwise every test
    here would no-op too. Deleting the env var doesn't work: pytest itself re-sets
    ``PYTEST_CURRENT_TEST`` via direct ``os.environ[...] =`` assignment at the start
    of its "call" phase, *after* fixture setup runs. Patching the read side
    (``os.environ.get``) instead is immune to that later reassignment.
    """
    import os as os_module

    real_get = os_module.environ.get

    def _fake_get(key, default=None):
        if key == "PYTEST_CURRENT_TEST":
            return default
        return real_get(key, default)

    monkeypatch.setattr(os_module.environ, "get", _fake_get)
    shutdown_audit()
    yield
    shutdown_audit()


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
        # After shutdown, should be unconfigured
        shutdown_audit()
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


class TestAuditLogProvenance:
    """ADR-0066: which layer last configured the journal (two-phase bootstrap, 'audit status')."""

    def test_defaults_to_bootstrap_source(self, tmp_path):
        configure_audit_log(log_path=str(tmp_path / "audit.log"))
        assert get_audit_log_source() == "bootstrap"

    def test_explicit_source_is_recorded(self, tmp_path):
        configure_audit_log(log_path=str(tmp_path / "audit.log"), source="spec_audit")
        assert get_audit_log_source() == "spec_audit"

    def test_path_is_recorded(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))
        assert get_configured_audit_log_path() == str(log_path)

    def test_shutdown_clears_provenance(self, tmp_path):
        configure_audit_log(log_path=str(tmp_path / "audit.log"), source="logging_yaml")
        shutdown_audit()
        assert get_audit_log_source() is None
        assert get_configured_audit_log_path() is None

    def test_source_none_before_first_configure(self):
        shutdown_audit()
        assert get_audit_log_source() is None


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


class TestConfigureAuditLogRotation:
    def test_size_rotation_is_default(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))
        handler = _audit_mod._audit_logger.handlers[0]
        assert isinstance(handler, logging.handlers.RotatingFileHandler)

    def test_explicit_size_rotation(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path), rotation="size", max_bytes=1024, backup_count=5)
        handler = _audit_mod._audit_logger.handlers[0]
        assert isinstance(handler, logging.handlers.RotatingFileHandler)
        assert handler.maxBytes == 1024
        assert handler.backupCount == 5

    def test_daily_rotation_uses_timed_handler(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path), rotation="daily", backup_count=30)
        handler = _audit_mod._audit_logger.handlers[0]
        assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
        assert handler.backupCount == 30

    def test_daily_rotation_applies_date_suffix(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path), rotation="daily", date_suffix="%Y%m%d")
        handler = _audit_mod._audit_logger.handlers[0]
        assert isinstance(handler, logging.handlers.TimedRotatingFileHandler)
        assert handler.suffix == "%Y%m%d"

    def test_daily_rotation_writes_entries(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path), rotation="daily")
        audit("test.daily", target="test-target")
        shutdown_audit()
        entry = json.loads(log_path.read_text(encoding="utf-8").strip())
        assert entry["action"] == "test.daily"
        assert entry["target"] == "test-target"


class TestPytestNoOp:
    """ADR-0066 problem 4: the test suite must never write to the real audit log."""

    def test_no_ops_under_pytest_current_test(self, tmp_path, monkeypatch):
        # Override the module-level autouse fixture's neutralisation — simulate
        # PYTEST_CURRENT_TEST actually being present for this one test.
        monkeypatch.setattr(
            _audit_mod.os.environ,
            "get",
            lambda key, default=None: "some::test (call)" if key == "PYTEST_CURRENT_TEST" else default,
        )
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path))
        assert not is_audit_configured()
        audit("test.action")
        assert not log_path.exists()

    def test_reconfigure_replaces_handler(self, tmp_path):
        log_path = tmp_path / "audit.log"
        configure_audit_log(log_path=str(log_path), rotation="size")
        configure_audit_log(log_path=str(log_path), rotation="daily")
        # Only one handler should remain after reconfigure
        assert len(_audit_mod._audit_logger.handlers) == 1
        assert isinstance(_audit_mod._audit_logger.handlers[0], logging.handlers.TimedRotatingFileHandler)
