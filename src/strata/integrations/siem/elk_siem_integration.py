"""ELK / Logstash SIEM sink.

Forwards structured audit events to an ELK stack via:
  - TCP JSON (Logstash TCP input) — protocol: "tcp" (default)
  - HTTP (Elasticsearch Bulk API) — protocol: "http"

Required config:
  endpoints.address:     host:port   (TCP) or http(s)://host:port  (HTTP)

Optional properties:
  protocol:        "tcp" | "http"   — default: "tcp"
  index_pattern:   str              — default: "strata-audit"
  codec:           "json"           — default: "json" (TCP only, informational)
"""

from __future__ import annotations

import json
import socket
from typing import List

try:
    import requests  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class ElkSiemIntegration(SiemBaseIntegration):
    """Forwards structured audit events to ELK via TCP (Logstash) or HTTP (Elasticsearch)."""

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        config = kwargs.get("config") or (args[0] if args else None)
        if config and config.endpoints and config.endpoints.address:
            return config.endpoints.address
        return "default"

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)

    # -------------------------------------------------------------------------
    # ISiemSink implementation
    # -------------------------------------------------------------------------

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        return self.send_batch(log_type, [payload], **kwargs)

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        try:
            protocol = self._prop("protocol", "tcp").lower()
            if protocol == "http":
                return self._send_http_bulk(log_type, payloads)
            return self._send_tcp(log_type, payloads)
        except Exception as exc:
            logger.warning(
                "elk_send_failed",
                integration=self.integration_name,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # TCP transport (Logstash JSON codec input)
    # -------------------------------------------------------------------------

    def _send_tcp(self, log_type: str, payloads: List[dict]) -> bool:
        address = self.config.endpoints.address if self.config.endpoints else ""
        if not address:
            return False

        host, _, port_str = address.rpartition(":")
        if not host:
            host = address
            port = 5000
        else:
            try:
                port = int(port_str)
            except ValueError:
                port = 5000

        try:
            with socket.create_connection((host, port), timeout=10) as sock:
                for payload in payloads:
                    tagged = {**payload, "_log_type": log_type}
                    line = json.dumps(tagged, default=str) + "\n"
                    sock.sendall(line.encode("utf-8"))
            return True
        except Exception as exc:
            logger.warning(
                "elk_tcp_send_failed",
                integration=self.integration_name,
                host=host,
                port=port,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # HTTP transport (Elasticsearch Bulk API)
    # -------------------------------------------------------------------------

    def _send_http_bulk(self, log_type: str, payloads: List[dict]) -> bool:
        address = self.config.endpoints.address if self.config.endpoints else ""
        if not address:
            return False

        index = self._prop("index_pattern", "strata-audit")
        # Elasticsearch Bulk API endpoint
        url = f"{address.rstrip('/')}/_bulk"

        # Build ndjson bulk body
        lines = []
        for payload in payloads:
            meta = {"index": {"_index": index}}
            doc = {**payload, "_log_type": log_type}
            lines.append(json.dumps(meta, default=str))
            lines.append(json.dumps(doc, default=str))
        bulk_body = "\n".join(lines) + "\n"

        try:
            if requests is None:
                logger.warning("elk_requests_unavailable", integration=self.integration_name)
                return False

            headers = {"Content-Type": "application/x-ndjson"}
            headers.update(self._build_auth_headers())
            resp = requests.post(url, data=bulk_body.encode("utf-8"), headers=headers, timeout=15)
            if resp.ok:
                return True
            logger.warning(
                "elk_http_error",
                integration=self.integration_name,
                status=resp.status_code,
                body=resp.text[:200],
            )
            return False
        except Exception as exc:
            logger.warning(
                "elk_http_send_failed",
                integration=self.integration_name,
                error=str(exc),
            )
            return False
