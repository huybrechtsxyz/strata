"""Unit tests for audit config models (policy + sinks)."""

import pytest
from pydantic import ValidationError

from strata.models.audit_config_model import (
    AuditConfigModel,
    AuditPolicyModel,
    AuditSinkModel,
)


class TestAuditPolicyModel:
    def test_defaults(self):
        m = AuditPolicyModel()
        assert m.events["deploy_audit"] is True
        assert m.events["cli_action"] is True
        assert m.events["lock_event"] is False
        assert m.events["build_event"] is False

    def test_override_events(self):
        m = AuditPolicyModel(events={"deploy_audit": True, "lock_event": True})
        assert m.events["lock_event"] is True
        # Only the provided keys exist (no defaults merged — user controls the map)
        assert "cli_action" not in m.events

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditPolicyModel(extra="bad")


class TestAuditSinkModel:
    # --- Valid configurations ---

    def test_stdout_minimal(self):
        m = AuditSinkModel(name="ci-stdout", type="stdout")
        assert m.type == "stdout"
        assert m.integration is None
        assert m.enabled is True
        assert m.events is None

    def test_ndjson_with_path(self):
        m = AuditSinkModel(name="local-log", type="ndjson", path=".strata/audit-stream.ndjson")
        assert m.path == ".strata/audit-stream.ndjson"

    def test_syslog_with_address(self):
        m = AuditSinkModel(name="syslog-local", type="syslog", address="localhost:514")
        assert m.address == "localhost:514"

    def test_webhook_with_url(self):
        m = AuditSinkModel(name="slack-hook", type="webhook", url="https://hooks.example.com/audit")
        assert m.url == "https://hooks.example.com/audit"

    def test_webhook_with_headers(self):
        m = AuditSinkModel(
            name="webhook-auth",
            type="webhook",
            url="https://hooks.example.com/audit",
            headers={"Authorization": "Bearer token123"},
        )
        assert m.headers == {"Authorization": "Bearer token123"}

    def test_integration_reference(self):
        m = AuditSinkModel(name="sentinel-prod", integration="sentinel_prod")
        assert m.integration == "sentinel_prod"
        assert m.type is None

    def test_with_event_filter(self):
        m = AuditSinkModel(
            name="sentinel-prod",
            integration="sentinel_prod",
            events=["deploy_audit", "policy_violation"],
        )
        assert m.events == ["deploy_audit", "policy_violation"]

    def test_disabled_sink(self):
        m = AuditSinkModel(name="splunk-corp", integration="splunk_corp", enabled=False)
        assert m.enabled is False

    # --- Invalid configurations ---

    def test_no_type_no_integration(self):
        with pytest.raises(ValidationError, match="must specify either"):
            AuditSinkModel(name="broken")

    def test_both_type_and_integration(self):
        with pytest.raises(ValidationError, match="cannot specify both"):
            AuditSinkModel(name="broken", type="stdout", integration="sentinel_prod")

    def test_stdout_with_extra_fields(self):
        with pytest.raises(ValidationError, match="no extra fields"):
            AuditSinkModel(name="bad", type="stdout", path="/tmp/log")

    def test_ndjson_missing_path(self):
        with pytest.raises(ValidationError, match="requires 'path'"):
            AuditSinkModel(name="bad", type="ndjson")

    def test_ndjson_with_address(self):
        with pytest.raises(ValidationError, match="only accepts 'path'"):
            AuditSinkModel(name="bad", type="ndjson", path="/tmp/log", address="localhost:514")

    def test_syslog_missing_address(self):
        with pytest.raises(ValidationError, match="requires 'address'"):
            AuditSinkModel(name="bad", type="syslog")

    def test_syslog_with_url(self):
        with pytest.raises(ValidationError, match="only accepts 'address'"):
            AuditSinkModel(name="bad", type="syslog", address="localhost:514", url="https://x.com")

    def test_webhook_missing_url(self):
        with pytest.raises(ValidationError, match="requires 'url'"):
            AuditSinkModel(name="bad", type="webhook")

    def test_webhook_with_path(self):
        with pytest.raises(ValidationError, match="only accepts 'url'"):
            AuditSinkModel(name="bad", type="webhook", url="https://x.com", path="/tmp")

    def test_integration_with_type_specific_fields(self):
        with pytest.raises(ValidationError, match="must not have type-specific"):
            AuditSinkModel(name="bad", integration="sentinel_prod", path="/tmp")

    def test_invalid_name(self):
        with pytest.raises(ValidationError):
            AuditSinkModel(name="Invalid Name!", type="stdout")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditSinkModel(name="bad", type="stdout", unexpected="field")


class TestAuditConfigModel:
    def test_defaults(self):
        m = AuditConfigModel()
        assert m.policy.events["deploy_audit"] is True
        assert m.sinks == []

    def test_with_sinks(self):
        m = AuditConfigModel(
            sinks=[
                AuditSinkModel(name="ci-stdout", type="stdout"),
                AuditSinkModel(name="sentinel-prod", integration="sentinel_prod"),
            ]
        )
        assert len(m.sinks) == 2
        assert m.sinks[0].type == "stdout"
        assert m.sinks[1].integration == "sentinel_prod"

    def test_with_custom_policy(self):
        m = AuditConfigModel(
            policy=AuditPolicyModel(events={"deploy_audit": True, "lock_event": True}),
        )
        assert m.policy.events["lock_event"] is True

    def test_serialization_round_trip(self):
        m = AuditConfigModel(
            policy=AuditPolicyModel(),
            sinks=[AuditSinkModel(name="ci-stdout", type="stdout", enabled=True)],
        )
        data = m.model_dump(exclude_none=True)
        restored = AuditConfigModel(**data)
        assert restored.sinks[0].name == "ci-stdout"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditConfigModel(extra="bad")
