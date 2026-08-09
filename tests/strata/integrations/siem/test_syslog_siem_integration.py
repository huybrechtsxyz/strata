"""Tests for SyslogSiemIntegration (ADR-0066 — was the `syslog` sink type)."""

from __future__ import annotations

import json
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.siem.syslog_siem_integration import SyslogSiemIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


@pytest.fixture(autouse=True)
def _isolate():
    BaseIntegration._instances.clear()
    yield
    BaseIntegration._instances.clear()


def _make_config(
    name: str = "syslog",
    address: str = "siem-collector.acme.internal:6514",
    properties: dict | None = None,
) -> IntegrationModel:
    return IntegrationModel(
        name=name,
        type="syslog",
        enabled=True,
        endpoints=IntegrationEndpointsSpecModel(address=address) if address else None,
        properties=properties,
    )


class TestSendBatch:
    def test_returns_false_when_no_endpoint(self):
        integ = SyslogSiemIntegration(_make_config(address=""))
        assert integ.send_batch("deployment.completed", [{"a": 1}]) is False

    def test_defaults_to_tcp_transport(self):
        integ = SyslogSiemIntegration(_make_config())
        with patch.object(integ, "_send_one", return_value=True) as mock_send:
            integ.send_batch("deployment.completed", [{"a": 1}])
        args, _ = mock_send.call_args
        assert args[2] == "tcp"

    def test_honours_transport_property(self):
        integ = SyslogSiemIntegration(_make_config(properties={"transport": "udp"}))
        with patch.object(integ, "_send_one", return_value=True) as mock_send:
            integ.send_batch("deployment.completed", [{"a": 1}])
        args, _ = mock_send.call_args
        assert args[2] == "udp"

    def test_parses_host_and_port(self):
        integ = SyslogSiemIntegration(_make_config(address="collector.example.com:601"))
        with patch.object(integ, "_send_one", return_value=True) as mock_send:
            integ.send_batch("deployment.completed", [{"a": 1}])
        host, port, _, _ = mock_send.call_args[0]
        assert host == "collector.example.com"
        assert port == 601

    def test_defaults_port_514_when_not_specified(self):
        integ = SyslogSiemIntegration(_make_config(address="collector.example.com"))
        with patch.object(integ, "_send_one", return_value=True) as mock_send:
            integ.send_batch("deployment.completed", [{"a": 1}])
        host, port, _, _ = mock_send.call_args[0]
        assert host == "collector.example.com"
        assert port == 514

    def test_formats_json_by_default(self):
        integ = SyslogSiemIntegration(_make_config())
        with patch.object(integ, "_send_one", return_value=True) as mock_send:
            integ.send_batch("deployment.completed", [{"a": 1}])
        _, _, _, message = mock_send.call_args[0]
        assert json.dumps({"a": 1}) in message

    def test_formats_cef_when_requested(self):
        integ = SyslogSiemIntegration(_make_config(properties={"format": "cef"}))
        with patch.object(integ, "_send_one", return_value=True) as mock_send:
            integ.send_batch("deployment.completed", [{"a": 1}])
        _, _, _, message = mock_send.call_args[0]
        assert "CEF:0|strata|strata-audit" in message

    def test_returns_false_when_any_send_fails(self):
        integ = SyslogSiemIntegration(_make_config())
        with patch.object(integ, "_send_one", side_effect=[True, False]):
            result = integ.send_batch("deployment.completed", [{"a": 1}, {"b": 2}])
        assert result is False


class TestSendEvent:
    def test_delegates_to_send_batch(self):
        integ = SyslogSiemIntegration(_make_config())
        with patch.object(integ, "send_batch", return_value=True) as mock_batch:
            result = integ.send_event("deployment.completed", {"a": 1})
        assert result is True
        mock_batch.assert_called_once_with("deployment.completed", [{"a": 1}])


class TestSendOneTransport:
    def test_udp_sends_datagram(self):
        integ = SyslogSiemIntegration(_make_config())
        mock_sock = MagicMock()
        with patch("socket.socket", return_value=mock_sock):
            result = integ._send_one("host", 514, "udp", "<14>hello")
        assert result is True
        mock_sock.sendto.assert_called_once()
        mock_sock.close.assert_called_once()

    def test_udp_truncates_oversized_message(self):
        integ = SyslogSiemIntegration(_make_config())
        mock_sock = MagicMock()
        huge_message = "<14>" + ("x" * 70000)
        with patch("socket.socket", return_value=mock_sock):
            integ._send_one("host", 514, "udp", huge_message)
        sent_data = mock_sock.sendto.call_args[0][0]
        assert len(sent_data) == 65000

    def test_tcp_sends_stream(self):
        integ = SyslogSiemIntegration(_make_config())
        mock_sock = MagicMock()
        with patch("socket.create_connection", return_value=mock_sock):
            result = integ._send_one("host", 514, "tcp", "<14>hello")
        assert result is True
        mock_sock.sendall.assert_called_once_with(b"<14>hello\n")

    def test_tcp_tls_wraps_socket(self):
        integ = SyslogSiemIntegration(_make_config())
        raw_sock = MagicMock()
        wrapped_sock = MagicMock()
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = wrapped_sock
        with (
            patch("socket.create_connection", return_value=raw_sock),
            patch("ssl.create_default_context", return_value=mock_context),
        ):
            result = integ._send_one("host", 6514, "tcp+tls", "<14>hello")
        assert result is True
        mock_context.wrap_socket.assert_called_once_with(raw_sock, server_hostname="host")
        wrapped_sock.sendall.assert_called_once_with(b"<14>hello\n")

    def test_returns_false_on_socket_exception(self):
        integ = SyslogSiemIntegration(_make_config())
        with patch("socket.create_connection", side_effect=OSError("connection refused")):
            result = integ._send_one("host", 514, "tcp", "<14>hello")
        assert result is False


class TestFormatCef:
    """ADR-0066 step 5: _format_cef reads the CloudEvents/ECS envelope forward() builds."""

    def _envelope(self, **data_overrides) -> dict:
        data: Dict[str, Any] = {"event": {}, "user": {}, "labels": {}, "strata": {}}
        data.update(data_overrides)
        return {"specversion": "1.0", "type": "xyz.huybrechts.strata.deployment.completed", "time": "", "data": data}

    def test_success_severity_is_low(self):
        cef = SyslogSiemIntegration._format_cef(self._envelope(event={"outcome": "success"}))
        assert "|3|" in cef

    def test_failure_severity_is_high(self):
        cef = SyslogSiemIntegration._format_cef(self._envelope(event={"outcome": "failure"}))
        assert "|7|" in cef

    def test_no_outcome_defaults_to_low(self):
        cef = SyslogSiemIntegration._format_cef(self._envelope(event={}))
        assert "|3|" in cef

    def test_escapes_special_characters(self):
        cef = SyslogSiemIntegration._format_cef(self._envelope(user={"name": "a=b\\c"}))
        assert "a\\=b\\\\c" in cef

    def test_includes_deployment_and_execution_id(self):
        cef = SyslogSiemIntegration._format_cef(
            self._envelope(labels={"deployment": "my_deploy", "execution_id": "abc-123"})
        )
        assert "dst=my_deploy" in cef
        assert "externalId=abc-123" in cef

    def test_signature_id_uses_cloudevents_type(self):
        cef = SyslogSiemIntegration._format_cef(self._envelope())
        assert "xyz.huybrechts.strata.deployment.completed" in cef
