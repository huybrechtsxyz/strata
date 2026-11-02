#!/usr/bin/env python3
"""Tests for `SentinelIntegration` — the Azure Sentinel audit sink via the
Monitor Logs Ingestion API (docs/design/audit-sink-dispatch.md).

No real network, no real Azure AD: `Integration.request()` is patched the
same way `test_integrations_webhook.py` does, and `_credential` is replaced
with a fake `get_token()` object directly on the instance, the same pattern
`test_azure_keyvault_resolver.py` uses for `_client`.
"""

import json

import pytest
from azure.core.exceptions import ClientAuthenticationError

from strata.integrations.capabilities import AuditSinkIntegration, find_capability_mismatches
from strata.integrations.errors import IntegrationError
from strata.integrations.sentinel import SentinelIntegration
from strata.models.integration_model import Capability, IntegrationModel
from strata.utils.transport import HttpResult

_EVENT = {"specversion": "1.0", "type": "xyz.huybrechts.strata.deployment.completed"}


def _config(**configuration) -> IntegrationModel:
    return IntegrationModel.model_validate(
        {
            "meta": {"name": "sentinel-audit"},
            "spec": {
                "type": "sentinel",
                "capabilities": ["audit"],
                "endpoints": {"address": "https://my-dce.ingest.monitor.azure.com"},
                "configuration": configuration
                or {"data_collection_rule_id": "dcr-abc123", "stream_name": "Custom-DeployAudit_CL"},
            },
        }
    )


class _FakeToken:
    def __init__(self, token: str) -> None:
        self.token = token


class _FakeCredential:
    def __init__(self, token: str = "fake-aad-token") -> None:
        self._token = token

    def get_token(self, *scopes) -> _FakeToken:
        return _FakeToken(self._token)


class _FailingCredential:
    def get_token(self, *scopes) -> _FakeToken:
        raise ClientAuthenticationError("no managed identity available")


@pytest.fixture
def _captured(monkeypatch):
    """Capture what `send()` hands to the transport, returning 200 by default."""
    captured: dict = {}

    def _fake_request(self, method, path, *, headers=None, body=None, timeout=30):
        captured.update(method=method, path=path, headers=headers or {}, body=body)
        return captured.get("response", HttpResult(status=200, body="ok"))

    monkeypatch.setattr(SentinelIntegration, "request", _fake_request)
    return captured


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


def test_declares_the_audit_capability_and_implements_its_abc():
    assert Capability.AUDIT in SentinelIntegration.CAPABILITIES
    assert issubclass(SentinelIntegration, AuditSinkIntegration)
    assert find_capability_mismatches(SentinelIntegration) == []


def test_class_declares_sdk_transport():
    """Not 'http' — token acquisition needs the azure-identity credential chain."""
    assert SentinelIntegration.TRANSPORTS == {"sdk"}


def test_registry_resolves_the_sentinel_type():
    """`sentinel` moved out of `_KNOWN_V1_TYPES` into the real registry."""
    from strata.integrations import registry

    assert isinstance(registry.get("sentinel", _config()), SentinelIntegration)


# ---------------------------------------------------------------------------
# send()
# ---------------------------------------------------------------------------


def test_posts_to_the_dcr_stream_url_with_a_bearer_token(_captured):
    sink = SentinelIntegration(_config())
    sink._credential = _FakeCredential("s3cr3t-token")

    sink.send(_EVENT)

    assert _captured["method"] == "POST"
    assert _captured["path"] == "dataCollectionRules/dcr-abc123/streams/Custom-DeployAudit_CL?api-version=2023-01-01"
    assert _captured["headers"]["Authorization"] == "Bearer s3cr3t-token"
    assert _captured["headers"]["Content-Type"] == "application/json"


def test_body_is_a_json_array_of_one_event_not_a_single_object(_captured):
    """The Logs Ingestion API wants an array — confirmed from v1's real implementation."""
    sink = SentinelIntegration(_config())
    sink._credential = _FakeCredential()

    sink.send(_EVENT)

    assert json.loads(_captured["body"].decode("utf-8")) == [_EVENT]


def test_token_is_cached_across_multiple_sends(_captured):
    sink = SentinelIntegration(_config())
    fake_credential = _FakeCredential()
    sink._credential = fake_credential

    sink.send(_EVENT)
    cached_after_first_send = sink._credential
    sink.send(_EVENT)

    assert sink._credential is cached_after_first_send is fake_credential


def test_missing_endpoint_raises():
    config = IntegrationModel.model_validate(
        {
            "meta": {"name": "sentinel-audit"},
            "spec": {
                "type": "sentinel",
                "capabilities": ["audit"],
                "configuration": {"data_collection_rule_id": "dcr-abc123", "stream_name": "Custom-DeployAudit_CL"},
            },
        }
    )

    with pytest.raises(IntegrationError, match="endpoints.address"):
        SentinelIntegration(config).send(_EVENT)


def test_missing_data_collection_rule_id_raises_before_any_token_acquisition(_captured):
    sink = SentinelIntegration(_config(stream_name="Custom-DeployAudit_CL"))

    with pytest.raises(IntegrationError, match="data_collection_rule_id"):
        sink.send(_EVENT)

    assert sink._credential is None  # no token was ever requested
    assert _captured == {}  # nothing was sent


def test_missing_stream_name_raises():
    sink = SentinelIntegration(_config(data_collection_rule_id="dcr-abc123"))

    with pytest.raises(IntegrationError, match="stream_name"):
        sink.send(_EVENT)


def test_authentication_failure_raises_a_clear_integration_error():
    sink = SentinelIntegration(_config())
    sink._credential = _FailingCredential()

    with pytest.raises(IntegrationError, match="Azure AD token"):
        sink.send(_EVENT)


def test_non_2xx_raises_rather_than_passing_silently(_captured):
    _captured["response"] = HttpResult(status=503, body="service unavailable")
    sink = SentinelIntegration(_config())
    sink._credential = _FakeCredential()

    with pytest.raises(IntegrationError, match="503"):
        sink.send(_EVENT)


def test_timeout_raises_with_a_clear_message(_captured):
    _captured["response"] = HttpResult(status=0, body="timed out", timed_out=True)
    sink = SentinelIntegration(_config())
    sink._credential = _FakeCredential()

    with pytest.raises(IntegrationError, match="timed out"):
        sink.send(_EVENT)
