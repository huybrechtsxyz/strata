"""OpenTelemetry SIEM sink (OTLP/HTTP JSON exporter).

Forwards structured audit events as OTel Log Records to any OTLP-compatible
backend (Grafana Loki, Datadog, Splunk, ELK via OTel Collector, etc.) using
the OTLP/HTTP JSON format:

  POST {endpoint}/v1/logs
  Content-Type: application/json
  Body: OTLP LogsServiceRequest (JSON)

This implementation uses ``requests`` only — no OTLP exporter package required.
The OTLP/HTTP JSON schema is a stable, well-documented wire format.

Config:
  endpoints.address:   https://otel-collector.internal:4318  (OTLP/HTTP port)

Optional properties:
  protocol:            "http" | "grpc"   — default: "http" (grpc falls back to http)
  resource_attributes: dict              — extra OTel resource attributes
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class OtelSiemIntegration(SiemBaseIntegration):
    """Forwards structured audit events to any OTLP-compatible backend via OTLP/HTTP JSON."""

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
            url = self._build_url()
            if not url:
                return False
            body = self._build_otlp_request(log_type, payloads)
            return self._post_json(url, body)
        except Exception as exc:
            logger.warning(
                "otel_send_failed",
                integration=self.integration_name,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _build_url(self) -> Optional[str]:
        if not self.config.endpoints or not self.config.endpoints.address:
            logger.warning("otel_no_endpoint", integration=self.integration_name)
            return None
        base = self.config.endpoints.address.rstrip("/")
        return f"{base}/v1/logs"

    def _build_otlp_request(self, log_type: str, payloads: List[dict]) -> Dict[str, Any]:
        """Build an OTLP/HTTP JSON LogsServiceRequest."""
        # Resource attributes
        resource_attrs = {
            "service.name": "strata-audit",
            "log_type": log_type,
        }
        extra = self._prop("resource_attributes", {})
        if isinstance(extra, dict):
            resource_attrs.update(extra)

        log_records = []
        for payload in payloads:
            log_records.append(
                {
                    "timeUnixNano": str(int(time.time() * 1e9)),
                    "severityNumber": 9,  # INFO
                    "severityText": "INFO",
                    "body": {"stringValue": self._json_str(payload)},
                    "attributes": [
                        {"key": "log_type", "value": {"stringValue": log_type}},
                    ],
                }
            )

        return {
            "resourceLogs": [
                {
                    "resource": {
                        "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in resource_attrs.items()]
                    },
                    "scopeLogs": [
                        {
                            "scope": {"name": "strata.audit"},
                            "logRecords": log_records,
                        }
                    ],
                }
            ]
        }

    @staticmethod
    def _json_str(payload: dict) -> str:
        import json

        return json.dumps(payload, default=str)
