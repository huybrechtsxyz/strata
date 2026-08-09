"""Tests for WebhookSiemIntegration (ADR-0066 — was the `webhook` sink type)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.siem.webhook_siem_integration import WebhookSiemIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


@pytest.fixture(autouse=True)
def _isolate():
    BaseIntegration._instances.clear()
    yield
    BaseIntegration._instances.clear()


def _make_config(
    name: str = "webhook",
    address: str = "https://ingest.acme.internal/v1/events",
    properties: dict | None = None,
) -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="webhook",
        enabled=True,
        endpoints=IntegrationEndpointsSpecModel(address=address) if address else None,
        properties=properties,
    )


class TestEnsureAvailable:
    def test_https_address_is_available(self):
        integ = WebhookSiemIntegration(_make_config())
        ok, _ = integ.ensure_available()
        assert ok is True

    def test_http_address_is_rejected_by_default(self):
        integ = WebhookSiemIntegration(_make_config(address="http://ingest.acme.internal/v1/events"))
        ok, detail = integ.ensure_available()
        assert ok is False
        assert "https://" in detail

    def test_http_address_allowed_with_allow_insecure(self):
        integ = WebhookSiemIntegration(
            _make_config(address="http://localhost:8080/events", properties={"allow_insecure": True})
        )
        ok, _ = integ.ensure_available()
        assert ok is True

    def test_no_endpoint_is_unavailable(self):
        integ = WebhookSiemIntegration(_make_config(address=""))
        ok, _ = integ.ensure_available()
        assert ok is False


class TestSendBatch:
    def test_posts_each_payload(self):
        integ = WebhookSiemIntegration(_make_config())
        with patch.object(integ, "_post_json", return_value=True) as mock_post:
            result = integ.send_batch("deployment.completed", [{"a": 1}, {"b": 2}])
        assert result is True
        assert mock_post.call_count == 2

    def test_passes_non_secret_headers_from_properties(self):
        integ = WebhookSiemIntegration(_make_config(properties={"headers": {"X-Scope-OrgID": "acme"}}))
        with patch.object(integ, "_post_json", return_value=True) as mock_post:
            integ.send_batch("deployment.completed", [{"a": 1}])
        _, kwargs = mock_post.call_args
        assert kwargs["extra_headers"] == {"X-Scope-OrgID": "acme"}

    def test_returns_false_when_no_endpoint(self):
        integ = WebhookSiemIntegration(_make_config(address=""))
        result = integ.send_batch("deployment.completed", [{"a": 1}])
        assert result is False

    def test_returns_false_for_insecure_address(self):
        integ = WebhookSiemIntegration(_make_config(address="http://ingest.acme.internal/v1/events"))
        result = integ.send_batch("deployment.completed", [{"a": 1}])
        assert result is False

    def test_returns_false_when_any_post_fails(self):
        integ = WebhookSiemIntegration(_make_config())
        with patch.object(integ, "_post_json", side_effect=[True, False]):
            result = integ.send_batch("deployment.completed", [{"a": 1}, {"b": 2}])
        assert result is False


class TestSendEvent:
    def test_delegates_to_send_batch(self):
        integ = WebhookSiemIntegration(_make_config())
        with patch.object(integ, "send_batch", return_value=True) as mock_batch:
            result = integ.send_event("deployment.completed", {"a": 1})
        assert result is True
        mock_batch.assert_called_once_with("deployment.completed", [{"a": 1}])
