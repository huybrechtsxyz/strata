#!/usr/bin/env python3
"""Tests for the audit trail config models — `spec.audit` (docs/design/audit-trail.md).

Phase 1 of that doc's Layer 2 Implementation Plan: config models only. The
written artifacts (manifest, metrics) are Phase 2, not covered here.
"""

import pytest
from pydantic import ValidationError

from strata.models.audit_model import (
    AuditConfigModel,
    AuditGitSinkTargetModel,
    AuditSinkModel,
)
from strata.models.common_models import PlatformKind
from strata.models.configuration_model import ConfigurationModel
from strata.models.reference_fields import extract_references


def _git_sink(name: str = "config-repo") -> dict:
    return {
        "name": name,
        "git": {"remote": "xyz-configuration", "branch": "main", "path": "audit"},
        "events": ["deployment.completed"],
    }


def _integration_sink(name: str = "prod-siem") -> dict:
    return {"name": name, "integration": "splunk-prod", "events": ["deployment.completed"]}


# ---------------------------------------------------------------------------
# AuditSinkModel — exactly one arm
# ---------------------------------------------------------------------------


def test_sink_accepts_git_arm():
    sink = AuditSinkModel.model_validate(_git_sink())
    assert sink.git is not None
    assert sink.integration is None


def test_sink_accepts_integration_arm():
    sink = AuditSinkModel.model_validate(_integration_sink())
    assert sink.integration == "splunk-prod"
    assert sink.git is None


def test_sink_rejects_neither_arm_set():
    with pytest.raises(ValidationError, match="exactly one of 'integration' or 'git'"):
        AuditSinkModel.model_validate({"name": "empty-sink"})


def test_sink_rejects_both_arms_set():
    data = _git_sink()
    data["integration"] = "splunk-prod"
    with pytest.raises(ValidationError, match="exactly one of 'integration' or 'git'"):
        AuditSinkModel.model_validate(data)


def test_sink_required_defaults_false():
    sink = AuditSinkModel.model_validate(_git_sink())
    assert sink.required is False


def test_sink_rejects_unknown_event_type():
    data = _git_sink()
    data["events"] = ["deployment.completed", "policy.violated"]
    with pytest.raises(ValidationError, match="unknown event type"):
        AuditSinkModel.model_validate(data)


# ---------------------------------------------------------------------------
# AuditConfigModel — event_overrides, sinks
# ---------------------------------------------------------------------------


def test_config_is_fully_optional():
    config = AuditConfigModel.model_validate({})
    assert config.event_overrides is None
    assert config.sinks is None


def test_config_rejects_unknown_event_override_key():
    with pytest.raises(ValidationError, match="unknown event type"):
        AuditConfigModel.model_validate({"event_overrides": {"deployment.completd": False}})


def test_config_accepts_known_event_override():
    config = AuditConfigModel.model_validate({"event_overrides": {"deployment.measured": False}})
    assert config.event_overrides == {"deployment.measured": False}


def test_config_rejects_duplicate_sink_names():
    with pytest.raises(ValidationError, match="Duplicate"):
        AuditConfigModel.model_validate({"sinks": [_git_sink("dup"), _integration_sink("dup")]})


def test_config_accepts_unique_sink_names():
    config = AuditConfigModel.model_validate({"sinks": [_git_sink("a"), _integration_sink("b")]})
    assert len(config.sinks) == 2


def test_config_rejects_sink_filtering_on_gate_disabled_event():
    """A sink can't filter on an event type event_overrides has disabled."""
    data = {
        "event_overrides": {"deployment.completed": False},
        "sinks": [_git_sink()],
    }
    with pytest.raises(ValidationError, match="event_overrides.deployment.completed is false"):
        AuditConfigModel.model_validate(data)


def test_config_allows_sink_filtering_on_gate_enabled_event():
    data = {
        "event_overrides": {"deployment.measured": False},
        "sinks": [_git_sink()],  # filters on deployment.completed, still enabled
    }
    config = AuditConfigModel.model_validate(data)
    assert config.sinks[0].events == ["deployment.completed"]


# ---------------------------------------------------------------------------
# ConfigurationSpecModel.audit wiring
# ---------------------------------------------------------------------------


def test_configuration_audit_is_optional():
    model = ConfigurationModel.model_validate({"meta": {"name": "cfg"}, "spec": {}})
    assert model.spec.audit is None


def test_configuration_accepts_audit_block():
    data = {
        "meta": {"name": "cfg"},
        "spec": {"audit": {"sinks": [_git_sink()]}},
    }
    model = ConfigurationModel.model_validate(data)
    assert model.spec.audit.sinks[0].git.remote == "xyz-configuration"


# ---------------------------------------------------------------------------
# Reference discoverability (ADR-0015) — the generic walker finds these fields
# without any manual registration, once reachable from a real document kind.
# ---------------------------------------------------------------------------


def test_sink_integration_is_a_discoverable_reference():
    rules = extract_references(AuditSinkModel)
    assert any(r.path == "integration" and r.kind is PlatformKind.INTEGRATION for r in rules)


def test_git_sink_remote_is_a_discoverable_remote_reference():
    rules = extract_references(AuditGitSinkTargetModel)
    assert any(r.path == "remote" and r.kind is None for r in rules)


def test_configuration_model_discovers_audit_references_transitively():
    """spec.audit.sinks[].integration/git.remote are found starting from the
    root ConfigurationModel — confirms the field is actually reachable in
    the real document tree, not just in isolation."""
    rules = extract_references(ConfigurationModel)
    assert any(r.path == "spec.audit.sinks[].integration" and r.kind is PlatformKind.INTEGRATION for r in rules)
    assert any(r.path == "spec.audit.sinks[].git.remote" and r.kind is None for r in rules)
