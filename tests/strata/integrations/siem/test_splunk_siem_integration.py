"""Tests for SplunkSiemIntegration (HTTP Event Collector)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from strata.integrations.siem.splunk_siem_integration import SplunkSiemIntegration
from strata.models.auth_models import APIKeyAuthenticationModel, AuthenticationModel
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _make_splunk_config(
    address: str = "https://splunk.internal:8088",
    token: str = "test-hec-token",
    index: str = "main",
    source: str = "strata",
    sourcetype: str = "_json",
    channel: str | None = None,
) -> IntegrationModel:
    props = {"index": index, "source": source, "sourcetype": sourcetype}
    if channel:
        props["channel"] = channel
    return IntegrationModel(
        name="splunk_hec",
        type="splunk",
        enabled=True,
        endpoints=IntegrationEndpointsSpecModel(address=address),
        authentication=AuthenticationModel(
            method="api_key",
            api_key=APIKeyAuthenticationModel(api_key=token, header_name="Authorization"),
        ),
        properties=props,
    )


class TestSplunkSendEvent:
    def test_send_event_delegates_to_send_batch(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        with patch.object(siem, "send_batch", return_value=True) as mock_batch:
            result = siem.send_event("deploy_audit", {"key": "value"})

        assert result is True
        mock_batch.assert_called_once_with("deploy_audit", [{"key": "value"}])


class TestSplunkSendBatch:
    def test_send_batch_success(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            result = siem.send_batch("deploy_audit", [{"execution_id": "abc"}])

        assert result is True
        call_kwargs = mock_req.post.call_args
        assert call_kwargs is not None
        url_arg = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1]["url"]
        assert url_arg.endswith("/services/collector")

    def test_send_batch_sets_splunk_auth_header(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config(token="my-secret-token")
        siem = SplunkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            siem.send_batch("deploy_audit", [{"x": 1}])

        headers = mock_req.post.call_args[1]["headers"]
        assert headers.get("Authorization") == "Splunk my-secret-token"

    def test_send_batch_includes_channel_header_when_configured(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config(channel="chan-guid-1234")
        siem = SplunkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            siem.send_batch("deploy_audit", [{}])

        headers = mock_req.post.call_args[1]["headers"]
        assert headers.get("X-Splunk-Request-Channel") == "chan-guid-1234"

    def test_send_batch_missing_token_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = IntegrationModel(
            name="splunk_hec",
            type="splunk",
            enabled=True,
            endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088"),
        )
        siem = SplunkSiemIntegration(config=cfg)

        result = siem.send_batch("deploy_audit", [{"x": 1}])
        assert result is False

    def test_send_batch_missing_endpoint_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = IntegrationModel(
            name="splunk_hec",
            type="splunk",
            enabled=True,
        )
        siem = SplunkSiemIntegration(config=cfg)

        result = siem.send_batch("deploy_audit", [{"x": 1}])
        assert result is False

    def test_send_batch_client_error_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 403
        mock_resp.text = "Invalid token"

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            result = siem.send_batch("deploy_audit", [{"x": 1}])

        assert result is False

    def test_send_batch_retries_on_server_error(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        # First two calls: 503, third: 200
        ok_resp = MagicMock()
        ok_resp.ok = True
        err_resp = MagicMock()
        err_resp.ok = False
        err_resp.status_code = 503

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            with patch("strata.integrations.siem.splunk_siem_integration.time") as mock_time:
                mock_req.post.side_effect = [err_resp, err_resp, ok_resp]
                mock_time.sleep = MagicMock()
                result = siem.send_batch("deploy_audit", [{"x": 1}])

        assert result is True
        assert mock_req.post.call_count == 3

    def test_send_batch_exhausts_retries_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        err_resp = MagicMock()
        err_resp.ok = False
        err_resp.status_code = 503

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            with patch("strata.integrations.siem.splunk_siem_integration.time") as mock_time:
                mock_req.post.return_value = err_resp
                mock_time.sleep = MagicMock()
                result = siem.send_batch("deploy_audit", [{"x": 1}])

        assert result is False
        assert mock_req.post.call_count == 3

    def test_send_batch_network_exception_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            with patch("strata.integrations.siem.splunk_siem_integration.time"):
                mock_req.post.side_effect = OSError("connection refused")
                result = siem.send_batch("deploy_audit", [{"x": 1}])

        assert result is False


class TestSplunkHecBody:
    def test_hec_body_structure(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config(index="ops", source="strata-prod", sourcetype="json")
        siem = SplunkSiemIntegration(config=cfg)

        body = siem._build_hec_body("deploy_audit", [{"execution_id": "x1"}, {"execution_id": "x2"}])
        lines = body.strip().split("\n")

        assert len(lines) == 2
        event1 = json.loads(lines[0])
        assert event1["index"] == "ops"
        assert event1["source"] == "strata-prod"
        assert event1["sourcetype"] == "json"
        assert event1["event"]["execution_id"] == "x1"
        assert event1["event"]["_log_type"] == "deploy_audit"

    def test_hec_body_defaults(self):
        SplunkSiemIntegration._instances.clear()
        cfg = IntegrationModel(
            name="s",
            type="splunk",
            endpoints=IntegrationEndpointsSpecModel(address="https://splunk:8088"),
        )
        siem = SplunkSiemIntegration(config=cfg)
        body = siem._build_hec_body("t", [{}])
        event = json.loads(body)
        assert event["index"] == "main"
        assert event["source"] == "strata"
        assert event["sourcetype"] == "_json"


class TestSplunkConnectivity:
    def test_check_connectivity_success(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            mock_req.get.return_value = mock_resp
            result = siem.check_connectivity()

        assert result is True
        url_called = mock_req.get.call_args[0][0]
        assert url_called.endswith("/services/collector/health")

    def test_check_connectivity_no_endpoint_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = IntegrationModel(name="s", type="splunk")
        siem = SplunkSiemIntegration(config=cfg)
        assert siem.check_connectivity() is False

    def test_check_connectivity_request_error_returns_false(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)

        with patch("strata.integrations.siem.splunk_siem_integration.requests") as mock_req:
            mock_req.get.side_effect = OSError("network unreachable")
            result = siem.check_connectivity()

        assert result is False


class TestSplunkIsAvailable:
    def test_available_when_endpoint_configured(self):
        SplunkSiemIntegration._instances.clear()
        cfg = _make_splunk_config()
        siem = SplunkSiemIntegration(config=cfg)
        assert siem.is_available() is True

    def test_not_available_when_no_endpoint(self):
        SplunkSiemIntegration._instances.clear()
        cfg = IntegrationModel(name="s", type="splunk")
        siem = SplunkSiemIntegration(config=cfg)
        assert siem.is_available() is False
