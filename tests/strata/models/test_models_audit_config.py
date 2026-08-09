"""Unit tests for audit config models (policy + sinks)."""

import pytest
from pydantic import ValidationError

from strata.models.audit_config_model import (
    AuditConfigModel,
    AuditJournalModel,
    AuditPolicyModel,
    AuditSinkModel,
)


class TestAuditPolicyModel:
    def test_defaults(self):
        m = AuditPolicyModel()
        assert m.events["deployment.completed"].enabled is True
        assert m.events["command.executed"].enabled is False
        assert m.events["lock.acquired"].enabled is False
        assert m.events["build.completed"].enabled is False

    def test_override_events(self):
        m = AuditPolicyModel(events={"deployment.completed": True, "lock.acquired": True})
        assert m.events["lock.acquired"].enabled is True
        # Class-aware defaults are merged in under any explicit overrides
        assert m.events["command.executed"].enabled is False

    def test_unknown_event_type_rejected(self):
        with pytest.raises(ValidationError, match="unknown event type"):
            AuditPolicyModel(events={"nonsense_event": True})

    def test_legacy_event_type_rejected_with_rename(self):
        """A pre-ADR-0066 event name gets 'use X' instead of the generic unknown-key list."""
        with pytest.raises(ValidationError, match="was renamed.*use 'deployment.completed'"):
            AuditPolicyModel(events={"deploy_audit": True})

    def test_is_enabled_resolves_bool_and_object_shapes(self):
        m = AuditPolicyModel(events={"deployment.completed": True, "build.completed": False})
        assert m.is_enabled("deployment.completed") is True
        assert m.is_enabled("build.completed") is False

    def test_is_enabled_true_for_type_outside_closed_set(self):
        """A producer this model doesn't know about is never gated off here."""
        m = AuditPolicyModel()
        assert m.is_enabled("some.future.event") is True

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditPolicyModel(extra="bad")


class TestAuditSinkModel:
    """ADR-0066: a sink is only a routing reference to an integration — no transport fields."""

    def test_integration_reference(self):
        m = AuditSinkModel(name="sentinel-prod", integration="sentinel_prod")
        assert m.integration == "sentinel_prod"
        assert m.enabled is True
        assert m.events is None

    def test_with_event_filter(self):
        m = AuditSinkModel(
            name="sentinel-prod",
            integration="sentinel_prod",
            events=["deployment.completed", "policy.violated"],
        )
        assert m.events == ["deployment.completed", "policy.violated"]

    def test_disabled_sink(self):
        m = AuditSinkModel(name="splunk-corp", integration="splunk_corp", enabled=False)
        assert m.enabled is False

    # --- Invalid configurations ---

    def test_integration_is_required(self):
        with pytest.raises(ValidationError):
            AuditSinkModel(name="broken")

    def test_invalid_name(self):
        with pytest.raises(ValidationError):
            AuditSinkModel(name="Invalid Name!", integration="sentinel_prod")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditSinkModel(name="bad", integration="sentinel_prod", unexpected="field")

    def test_old_type_field_no_longer_accepted(self):
        """The removed built-in sink shape (type/path/address/url/headers/format) is a hard break."""
        with pytest.raises(ValidationError, match="no longer supported"):
            AuditSinkModel(name="bad", type="webhook", url="https://example.com")

    def test_old_shape_error_names_the_replacement(self):
        """The error spells out the exact replacement shape (ADR-0066 'This is a clean break')."""
        with pytest.raises(ValidationError) as exc_info:
            AuditSinkModel(name="my-webhook", type="webhook", address="https://example.com")
        message = str(exc_info.value)
        assert "integrations:" in message
        assert "capabilities: [audit]" in message
        assert "integration: my-webhook" in message

    @pytest.mark.parametrize("legacy_field", ["path", "address", "url", "headers", "format"])
    def test_each_legacy_transport_field_rejected(self, legacy_field):
        with pytest.raises(ValidationError, match="no longer supported"):
            AuditSinkModel(name="bad", integration="i", **{legacy_field: "x"})


class TestAuditConfigModel:
    def test_defaults(self):
        m = AuditConfigModel()
        assert m.policy.events["deployment.completed"].enabled is True
        assert m.sinks == []

    def test_with_sinks(self):
        m = AuditConfigModel(
            sinks=[
                AuditSinkModel(name="webhook-prod", integration="strata-ingest"),
                AuditSinkModel(name="sentinel-prod", integration="sentinel_prod"),
            ]
        )
        assert len(m.sinks) == 2
        assert m.sinks[0].integration == "strata-ingest"
        assert m.sinks[1].integration == "sentinel_prod"

    def test_with_custom_policy(self):
        m = AuditConfigModel(
            policy=AuditPolicyModel(events={"deployment.completed": True, "lock.acquired": True}),
        )
        assert m.policy.events["lock.acquired"].enabled is True

    def test_serialization_round_trip(self):
        m = AuditConfigModel(
            policy=AuditPolicyModel(),
            sinks=[AuditSinkModel(name="webhook-prod", integration="strata-ingest", enabled=True)],
        )
        data = m.model_dump(exclude_none=True)
        restored = AuditConfigModel(**data)
        assert restored.sinks[0].name == "webhook-prod"

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditConfigModel(extra="bad")


class TestAuditJournalModel:
    """ADR-0066: spec.audit.journal is the primary, committed location for the local NDJSON journal."""

    def test_defaults_are_none(self):
        m = AuditJournalModel()
        assert m.path is None
        assert m.rotation is None
        assert m.max_bytes is None
        assert m.backup_count is None
        assert m.date_suffix is None

    def test_explicit_fields(self):
        m = AuditJournalModel(path=".strata/audit.log", rotation="daily", backup_count=7, date_suffix="%Y%m%d")
        assert m.path == ".strata/audit.log"
        assert m.rotation == "daily"
        assert m.backup_count == 7

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValidationError):
            AuditJournalModel(extra="bad")

    def test_audit_config_journal_defaults_to_none(self):
        assert AuditConfigModel().journal is None

    def test_audit_config_journal_can_be_set(self):
        m = AuditConfigModel(journal=AuditJournalModel(path="custom/audit.log"))
        assert m.journal.path == "custom/audit.log"


class TestGateFilterConsistency:
    """ADR-0066: a sink naming an event the gate has disabled is a validation error."""

    def test_sink_filter_matching_disabled_gate_is_rejected(self):
        with pytest.raises(ValidationError, match="policy.events.build.completed is false"):
            AuditConfigModel(
                policy=AuditPolicyModel(),  # build.completed defaults to False
                sinks=[AuditSinkModel(name="s", integration="i", events=["build.completed"])],
            )

    def test_sink_filter_matching_enabled_gate_is_valid(self):
        m = AuditConfigModel(
            policy=AuditPolicyModel(events={"build.completed": True}),
            sinks=[AuditSinkModel(name="s", integration="i", events=["build.completed"])],
        )
        assert m.sinks[0].events == ["build.completed"]

    def test_sink_filter_with_unknown_event_type_is_rejected(self):
        with pytest.raises(ValidationError, match="unknown event type"):
            AuditConfigModel(sinks=[AuditSinkModel(name="s", integration="i", events=["deploy_audit"])])

    def test_sink_with_no_filter_is_unaffected(self):
        m = AuditConfigModel(sinks=[AuditSinkModel(name="s", integration="i")])
        assert m.sinks[0].events is None

    def test_workitem_events_are_recognised_and_enabled_by_default(self):
        m = AuditConfigModel(sinks=[AuditSinkModel(name="s", integration="i", events=["workitem.created"])])
        assert m.sinks[0].events == ["workitem.created"]
        assert m.policy.is_enabled("workitem.created") is True
        assert m.policy.is_enabled("workitem.resumed") is True
