"""Tests for ElkSiemIntegration (Logstash TCP / Elasticsearch HTTP)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from strata.integrations.siem.elk_siem_integration import ElkSiemIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _make_elk_config(
    address: str = "logstash.internal:5044",
    protocol: str = "tcp",
    index: str = "strata-audit",
) -> IntegrationModel:
    return IntegrationModel(
        name="elk",
        type="elk",
        enabled=True,
        endpoints=IntegrationEndpointsSpecModel(address=address),
        properties={"protocol": protocol, "index_pattern": index},
    )


class TestElkSendTcp:
    def test_send_tcp_success(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config()
        siem = ElkSiemIntegration(config=cfg)

        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.sendall = MagicMock()

        with patch("strata.integrations.siem.elk_siem_integration.socket") as mock_socket:
            mock_socket.create_connection.return_value = mock_sock
            result = siem.send_batch("deploy_audit", [{"execution_id": "abc"}])

        assert result is True
        mock_socket.create_connection.assert_called_once()

    def test_send_tcp_failure_returns_false(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config()
        siem = ElkSiemIntegration(config=cfg)

        with patch("strata.integrations.siem.elk_siem_integration.socket") as mock_socket:
            mock_socket.create_connection.side_effect = OSError("connection refused")
            result = siem.send_batch("deploy_audit", [{"execution_id": "abc"}])

        assert result is False

    def test_send_tcp_parses_port_from_address(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config(address="myhost:9999")
        siem = ElkSiemIntegration(config=cfg)

        mock_sock = MagicMock()
        mock_sock.__enter__ = lambda s: s
        mock_sock.__exit__ = MagicMock(return_value=False)
        mock_sock.sendall = MagicMock()

        with patch("strata.integrations.siem.elk_siem_integration.socket") as mock_socket:
            mock_socket.create_connection.return_value = mock_sock
            siem.send_batch("deploy_audit", [{}])
            mock_socket.create_connection.assert_called_once_with(("myhost", 9999), timeout=10)


class TestElkSendHttp:
    def test_send_http_bulk_success(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config(address="http://elasticsearch:9200", protocol="http")
        siem = ElkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.elk_siem_integration.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            result = siem.send_batch("deploy_audit", [{"execution_id": "abc"}])

        assert result is True
        mock_req.post.assert_called_once()
        url_arg = mock_req.post.call_args[0][0]
        assert url_arg.endswith("/_bulk")

    def test_send_http_bulk_uses_index_from_properties(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config(address="http://es:9200", protocol="http", index="my-custom-index")
        siem = ElkSiemIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.elk_siem_integration.requests") as mock_req:
            mock_req.post.return_value = mock_resp
            siem.send_batch("deploy_audit", [{"k": "v"}])

        body_bytes = mock_req.post.call_args[1]["data"]
        body_str = body_bytes.decode("utf-8") if isinstance(body_bytes, bytes) else body_bytes
        assert "my-custom-index" in body_str

    def test_send_http_bulk_returns_false_when_no_address(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config(address="http://es:9200", protocol="http")
        siem = ElkSiemIntegration(config=cfg)
        siem.config.endpoints = None

        result = siem._send_http_bulk("deploy_audit", [{}])
        assert result is False


class TestElkSendEvent:
    def test_send_event_delegates_to_send_batch(self):
        ElkSiemIntegration._instances.clear()
        cfg = _make_elk_config()
        siem = ElkSiemIntegration(config=cfg)

        with patch.object(siem, "send_batch", return_value=True) as mock_batch:
            result = siem.send_event("deploy_audit", {"k": "v"})

        assert result is True
        mock_batch.assert_called_once_with("deploy_audit", [{"k": "v"}])
