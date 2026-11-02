#!/usr/bin/env python3
"""Tests for `OtelIntegration` — the OTLP/HTTP JSON audit sink
(docs/design/audit-sink-dispatch.md).

No real network: `Integration.request()` is patched per-test, same
conventions as `test_integrations_webhook.py`.
"""

import json

import pytest

from strata.integrations.capabilities import AuditSinkIntegration, find_capability_mismatches
from strata.integrations.errors import IntegrationError
from strata.integrations.otel import OtelIntegration
from strata.models.integration_model import Capability, IntegrationModel
from strata.utils.transport import HttpResult

_EVENT = {
    "specversion": "1.0",
    "type": "xyz.huybrechts.strata.deployment.completed",
    "time": "2026-10-03T12:00:00+00:00",
}


def _config(**configuration) -> IntegrationModel:
    return IntegrationModel.model_validate(
        {
            "meta": {"name": "elk-otel"},
            "spec": {
                "type": "otel",
                "capabilities": ["audit"],
                "endpoints": {"address": "https://otel-collector.example.com:4318"},
                "configuration": configuration or None,
            },
        }
    )


@pytest.fixture
def _captured(monkeypatch):
    """Capture what `send()` hands to the transport, returning 200 by default."""
    captured: dict = {}

    def _fake_request(self, method, path, *, headers=None, body=None, timeout=30):
        captured.update(method=method, path=path, headers=headers or {}, body=body)
        return captured.get("response", HttpResult(status=200, body="ok"))

    monkeypatch.setattr(OtelIntegration, "request", _fake_request)
    return captured


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_declares_the_audit_capability_and_implements_its_abc():
    assert Capability.AUDIT in OtelIntegration.CAPABILITIES
    assert issubclass(OtelIntegration, AuditSinkIntegration)
    assert find_capability_mismatches(OtelIntegration) == []


def test_registry_resolves_the_otel_type():
    """`otel` moved out of `_KNOWN_V1_TYPES` into the real registry."""
    from strata.integrations import registry

    assert isinstance(registry.get("otel", _config()), OtelIntegration)


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


def test_posts_to_v1_logs(_captured):
    OtelIntegration(_config()).send(_EVENT)

    assert _captured["method"] == "POST"
    assert _captured["path"] == "v1/logs"
    assert _captured["headers"]["Content-Type"] == "application/json"


def test_body_is_an_otlp_logs_service_request_with_the_event_stringified(_captured):
    OtelIntegration(_config()).send(_EVENT)

    payload = json.loads(_captured["body"].decode("utf-8"))
    log_record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    assert json.loads(log_record["body"]["stringValue"]) == _EVENT
    assert log_record["severityNumber"] == 9
    assert log_record["severityText"] == "INFO"
    assert {"key": "event.type", "value": {"stringValue": _EVENT["type"]}} in log_record["attributes"]


def test_uses_the_events_own_time_not_wall_clock(_captured):
    """v1's own defect: `time.time()` (send time). Fixed here — parses the event's own `time`."""
    from datetime import datetime

    OtelIntegration(_config()).send(_EVENT)

    payload = json.loads(_captured["body"].decode("utf-8"))
    log_record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    expected_nanos = int(datetime.fromisoformat(_EVENT["time"]).timestamp() * 1_000_000_000)
    assert log_record["timeUnixNano"] == str(expected_nanos)


def test_default_resource_attributes(_captured):
    OtelIntegration(_config()).send(_EVENT)

    payload = json.loads(_captured["body"].decode("utf-8"))
    attributes = payload["resourceLogs"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "strata-audit"}} in attributes


def test_configured_resource_attributes_are_merged_over_the_default(_captured):
    OtelIntegration(_config(resource_attributes={"deployment.environment": "prod"})).send(_EVENT)

    payload = json.loads(_captured["body"].decode("utf-8"))
    attributes = payload["resourceLogs"][0]["resource"]["attributes"]
    assert {"key": "service.name", "value": {"stringValue": "strata-audit"}} in attributes
    assert {"key": "deployment.environment", "value": {"stringValue": "prod"}} in attributes


def test_missing_endpoint_raises():
    config = IntegrationModel.model_validate(
        {
            "meta": {"name": "elk-otel"},
            "spec": {"type": "otel", "capabilities": ["audit"]},
        }
    )

    with pytest.raises(IntegrationError, match="endpoints.address"):
        OtelIntegration(config).send(_EVENT)


def test_non_2xx_raises_rather_than_passing_silently(_captured):
    _captured["response"] = HttpResult(status=503, body="service unavailable")

    with pytest.raises(IntegrationError, match="503"):
        OtelIntegration(_config()).send(_EVENT)


def test_timeout_raises_with_a_clear_message(_captured):
    _captured["response"] = HttpResult(status=0, body="timed out", timed_out=True)

    with pytest.raises(IntegrationError, match="timed out"):
        OtelIntegration(_config()).send(_EVENT)


def test_missing_time_falls_back_without_raising(_captured):
    """Defensive only — the real renderer always sets `time`."""
    event = {"specversion": "1.0", "type": "xyz.huybrechts.strata.deployment.completed"}

    OtelIntegration(_config()).send(event)

    payload = json.loads(_captured["body"].decode("utf-8"))
    log_record = payload["resourceLogs"][0]["scopeLogs"][0]["logRecords"][0]
    assert int(log_record["timeUnixNano"]) > 0
