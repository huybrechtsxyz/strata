"""Syslog SIEM sink — remote syslog forwarding, TCP by default (ADR-0066).

Was the built-in ``type: syslog`` *sink* (``AuditController._send_syslog`` /
``_format_cef``); ADR-0066 promotes it to a real integration — a sink is now only
a routing reference (``sinks[].integration``).

Config::

    integrations:
      - name: soc-collector
        type: syslog
        capabilities: [audit]
        endpoints:
          address: siem-collector.acme.internal:6514   # host:port; default port 514
        properties:
          transport: tcp+tls   # tcp (default) | udp | tcp+tls
          format: cef          # json (default) | cef (Common Event Format)

``udp`` exists only for compatibility with the old sink default — unacknowledged
cleartext is the wrong default for an audit channel, so ``tcp`` is the default here.
Oversized UDP datagrams are truncated with a logged warning rather than silently.
"""

from __future__ import annotations

import json
import socket
import ssl
from typing import List

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)

_DEFAULT_PORT = 514
_MAX_UDP_BYTES = 65000
_SOCKET_TIMEOUT = 10


class SyslogSiemIntegration(SiemBaseIntegration):
    """Forwards structured audit events to a syslog collector (was the `syslog` sink type)."""

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)

    # -------------------------------------------------------------------------
    # ISiemSink implementation
    # -------------------------------------------------------------------------

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        return self.send_batch(log_type, [payload], **kwargs)

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        address = self.config.endpoints.address if self.config.endpoints else None
        if not address:
            logger.warning("syslog_no_endpoint", integration=self.integration_name)
            return False

        head, sep, tail = address.rpartition(":")
        if sep:
            host, port = head, (int(tail) if tail else _DEFAULT_PORT)
        else:
            host, port = address, _DEFAULT_PORT

        transport = self._prop("transport", "tcp")
        fmt = self._prop("format", "json")

        result = True
        for payload in payloads:
            body = self._format_cef(payload, log_type) if fmt == "cef" else json.dumps(payload, default=str)
            message = f"<14>{body}"
            if not self._send_one(host, port, transport, message):
                result = False
        return result

    # -------------------------------------------------------------------------
    # Transport
    # -------------------------------------------------------------------------

    def _send_one(self, host: str, port: int, transport: str, message: str) -> bool:
        data = message.encode("utf-8")
        try:
            if transport == "udp":
                if len(data) > _MAX_UDP_BYTES:
                    logger.warning(
                        "syslog_message_truncated",
                        integration=self.integration_name,
                        original_bytes=len(data),
                        max_bytes=_MAX_UDP_BYTES,
                    )
                    data = data[:_MAX_UDP_BYTES]
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    sock.sendto(data, (host, port))
                finally:
                    sock.close()
                return True

            # tcp or tcp+tls — stream transport, newline-framed
            raw_sock = socket.create_connection((host, port), timeout=_SOCKET_TIMEOUT)
            stream = raw_sock
            try:
                if transport == "tcp+tls":
                    context = ssl.create_default_context()
                    stream = context.wrap_socket(raw_sock, server_hostname=host)
                stream.sendall(data + b"\n")
            finally:
                stream.close()
            return True
        except Exception as exc:
            logger.warning(
                "syslog_send_failed",
                integration=self.integration_name,
                transport=transport,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # CEF formatting — moved from AuditController._format_cef (ADR-0066)
    # -------------------------------------------------------------------------

    @staticmethod
    def _format_cef(data: dict, event_type: str = "unknown") -> str:
        """Format a CloudEvents-enveloped audit event (ADR-0066) as CEF (Common Event Format).

        CEF:Version|Device Vendor|Device Product|Device Version|Signature ID|
            Name|Severity|Extension

        Severity mapping: success → 3 (Low), failure → 7 (High); no outcome (e.g. a
        domain event with no success/failure concept) defaults to Low.

        *data* is the full envelope ``forward()`` builds — ``specversion``/``type``/
        ``time`` at the top level, ECS fields under ``data.event``/``data.user``/
        ``data.labels``, and the original flat payload under ``data.strata``.
        """
        ce_data = data.get("data", {})
        event = ce_data.get("event", {})
        labels = ce_data.get("labels", {})
        user = ce_data.get("user", {})

        outcome = event.get("outcome")
        success = outcome != "failure"
        severity = 7 if outcome == "failure" else 3
        timestamp = data.get("time", "")
        actor = user.get("name", "") or ""
        deployment = labels.get("deployment", "unknown")
        execution_id = labels.get("execution_id", "") or ""
        signature_id = data.get("type", event_type)

        def _cef_escape(v: str) -> str:
            return v.replace("\\", "\\\\").replace("=", "\\=").replace("\n", "\\n")

        ext_parts = [
            f"rt={_cef_escape(str(timestamp))}",
            f"src={_cef_escape(str(actor))}",
            f"dst={_cef_escape(str(deployment))}",
            f"act={'success' if success else 'failure'}",
            f"externalId={_cef_escape(str(execution_id))}",
            f"msg={_cef_escape(json.dumps(data, default=str))}",
        ]
        extension = " ".join(ext_parts)

        return (
            f"CEF:0|strata|strata-audit|1.0"
            f"|{_cef_escape(str(signature_id))}|{_cef_escape(event_type)}|{severity}|{extension}"
        )
