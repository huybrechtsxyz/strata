#!/usr/bin/env python3
"""Tests for `WebhookIntegration` — the generic HTTP audit sink
(docs/design/audit-sink-dispatch.md).

No real network: `Integration.request()` is patched per-test, so these
exercise this class's own behaviour (header assembly, credential lookup,
non-2xx handling) rather than `http_request()`, which has its own tests.
"""

import json

import pytest

from strata.integrations.capabilities import AuditSinkIntegration, find_capability_mismatches
from strata.integrations.errors import IntegrationError
from strata.integrations.webhook import WebhookIntegration
from strata.models.integration_model import Capability, IntegrationModel
from strata.utils.transport import HttpResult

_EVENT = {"specversion": "1.0", "type": "xyz.huybrechts.strata.deployment.completed"}


def _config(**configuration) -> IntegrationModel:
    return IntegrationModel.model_validate(
        {
            "meta": {"name": "audit-hook"},
            "spec": {
                "type": "webhook",
                "capabilities": ["audit"],
                "endpoints": {"address": "https://siem.example.com/collect"},
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

    monkeypatch.setattr(WebhookIntegration, "request", _fake_request)
    return captured


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_declares_the_audit_capability_and_implements_its_abc():
    assert Capability.AUDIT in WebhookIntegration.CAPABILITIES
    assert issubclass(WebhookIntegration, AuditSinkIntegration)
    assert find_capability_mismatches(WebhookIntegration) == []


def test_registry_resolves_the_webhook_type():
    """`webhook` moved out of `_KNOWN_V1_TYPES` into the real registry."""
    from strata.integrations import registry

    assert isinstance(registry.get("webhook", _config()), WebhookIntegration)


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


def test_posts_the_event_as_json(_captured):
    WebhookIntegration(_config()).send(_EVENT)

    assert _captured["method"] == "POST"
    assert json.loads(_captured["body"].decode("utf-8")) == _EVENT
    assert _captured["headers"]["Content-Type"] == "application/json"


def test_static_headers_are_sent(_captured):
    WebhookIntegration(_config(headers={"X-Tenant": "c0224"})).send(_EVENT)

    assert _captured["headers"]["X-Tenant"] == "c0224"


def test_token_env_var_becomes_a_bearer_authorization_header(_captured, monkeypatch):
    monkeypatch.setenv("MY_AUDIT_TOKEN", "s3cret")

    WebhookIntegration(_config(token_env_var="MY_AUDIT_TOKEN")).send(_EVENT)

    assert _captured["headers"]["Authorization"] == "Bearer s3cret"


def test_auth_scheme_is_configurable_for_splunk_style_targets(_captured, monkeypatch):
    """Splunk HEC wants `Authorization: Splunk <token>` — a knob, not a second class."""
    monkeypatch.setenv("MY_AUDIT_TOKEN", "s3cret")

    WebhookIntegration(_config(token_env_var="MY_AUDIT_TOKEN", auth_scheme="Splunk")).send(_EVENT)

    assert _captured["headers"]["Authorization"] == "Splunk s3cret"


def test_unset_token_env_var_raises_before_sending(_captured, monkeypatch):
    monkeypatch.delenv("MY_AUDIT_TOKEN", raising=False)

    with pytest.raises(IntegrationError, match="MY_AUDIT_TOKEN"):
        WebhookIntegration(_config(token_env_var="MY_AUDIT_TOKEN")).send(_EVENT)

    assert _captured == {}  # nothing was sent


def test_missing_endpoint_raises():
    config = IntegrationModel.model_validate(
        {
            "meta": {"name": "audit-hook"},
            "spec": {"type": "webhook", "capabilities": ["audit"]},
        }
    )

    with pytest.raises(IntegrationError, match="endpoints.address"):
        WebhookIntegration(config).send(_EVENT)


def test_non_2xx_raises_rather_than_passing_silently(_captured):
    """`http_request()` returns 4xx/5xx as a normal result — this class must escalate it."""
    _captured["response"] = HttpResult(status=503, body="service unavailable")

    with pytest.raises(IntegrationError, match="503"):
        WebhookIntegration(_config()).send(_EVENT)


def test_timeout_raises_with_a_clear_message(_captured):
    _captured["response"] = HttpResult(status=0, body="timed out", timed_out=True)

    with pytest.raises(IntegrationError, match="timed out"):
        WebhookIntegration(_config()).send(_EVENT)
