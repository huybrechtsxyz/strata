"""Splunk HTTP Event Collector (HEC) SIEM sink.

Forwards structured audit events to Splunk via the HEC endpoint:
  POST https://{host}:8088/services/collector

Authentication: Splunk HEC token passed as ``Authorization: Splunk <token>``.
The token is read from ``config.authentication.api_key.api_key``.

Required config:
  endpoints.address:       https://splunk.host:8088   (HEC base URL)
  authentication.method:   api_key
  authentication.api_key.api_key:  <HEC token>  (supports ${SPLUNK_HEC_TOKEN})

Optional properties:
  index:       str    — Splunk index  (default: "main")
  source:      str    — event source  (default: "strata")
  sourcetype:  str    — sourcetype    (default: "_json")
  channel:     str    — HEC channel GUID (optional, for indexer acknowledgement)
"""

from __future__ import annotations

import json
import time
from typing import Dict, List, Optional

try:
    import requests  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

from strata.integrations.siem.base_siem_integration import (
    _MAX_RETRIES,
    _REQUESTS_TIMEOUT,
    _RETRY_BACKOFF,
    SiemBaseIntegration,
)
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)

# HEC endpoint paths
_HEC_EVENT_PATH = "/services/collector"
_HEC_HEALTH_PATH = "/services/collector/health"


class SplunkSiemIntegration(SiemBaseIntegration):
    """Forwards structured audit events to Splunk via the HTTP Event Collector (HEC)."""

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
        """Send a single event to the Splunk HEC endpoint."""
        return self.send_batch(log_type, [payload], **kwargs)

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        """Send a batch of events to the Splunk HEC endpoint.

        Uses newline-delimited HEC events (one JSON object per line) which is
        the most efficient form — no array wrapping.
        """
        try:
            url = self._build_url()
            if not url:
                return False

            token = self._get_hec_token()
            if not token:
                logger.warning("splunk_missing_token", integration=self.integration_name)
                return False

            body_str = self._build_hec_body(log_type, payloads)
            extra_headers: Dict[str, str] = {"Authorization": f"Splunk {token}"}

            channel = self._prop("channel")
            if channel:
                extra_headers["X-Splunk-Request-Channel"] = str(channel)

            return self._post_raw(url, body_str.encode("utf-8"), extra_headers=extra_headers)

        except Exception as exc:
            logger.warning(
                "splunk_send_failed",
                integration=self.integration_name,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # Connectivity check
    # -------------------------------------------------------------------------

    def check_connectivity(self) -> bool:
        """Probe the HEC health endpoint. Returns True if Splunk is reachable."""
        if not self.config.endpoints or not self.config.endpoints.address:
            return False
        base = self.config.endpoints.address.rstrip("/")
        health_url = f"{base}{_HEC_HEALTH_PATH}"
        token = self._get_hec_token()
        if not token:
            return False
        try:
            if requests is None:
                return False
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Splunk {token}",
            }
            resp = requests.get(health_url, headers=headers, timeout=10)
            return resp.ok
        except Exception as exc:
            logger.warning(
                "splunk_health_check_failed",
                integration=self.integration_name,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _build_url(self) -> Optional[str]:
        if not self.config.endpoints or not self.config.endpoints.address:
            logger.warning("splunk_no_endpoint", integration=self.integration_name)
            return None
        base = self.config.endpoints.address.rstrip("/")
        return f"{base}{_HEC_EVENT_PATH}"

    def _get_hec_token(self) -> Optional[str]:
        """Extract the HEC token from authentication config."""
        auth = self.config.authentication
        if not auth:
            return None
        if getattr(auth, "method", None) == "api_key" and auth.api_key:
            return auth.api_key.api_key or None
        return None

    def _build_hec_body(self, log_type: str, payloads: List[dict]) -> str:
        """Build newline-delimited HEC event body.

        Each line is a complete HEC event JSON:
          {"event": {...}, "index": "main", "source": "strata", "sourcetype": "_json"}
        """
        index = self._prop("index", "main")
        source = self._prop("source", "strata")
        sourcetype = self._prop("sourcetype", "_json")

        lines = []
        for payload in payloads:
            hec_event = {
                "event": {**payload, "_log_type": log_type},
                "index": index,
                "source": source,
                "sourcetype": sourcetype,
            }
            lines.append(json.dumps(hec_event, default=str))
        return "\n".join(lines)

    def _post_raw(
        self,
        url: str,
        body: bytes,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """HTTP POST raw bytes to *url* with retry logic.

        Separate from ``_post_json`` in the base class because HEC uses
        newline-delimited JSON (not a single JSON object/array).
        """
        if requests is None:
            logger.warning("splunk_requests_unavailable", integration=self.integration_name)
            return False

        headers = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)

        backoff = _RETRY_BACKOFF
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(url, data=body, headers=headers, timeout=_REQUESTS_TIMEOUT)
                if resp.ok:
                    return True
                if resp.status_code < 500:
                    logger.warning(
                        "splunk_http_client_error",
                        integration=self.integration_name,
                        status=resp.status_code,
                        body=resp.text[:200],
                    )
                    return False
                logger.warning(
                    "splunk_http_server_error",
                    integration=self.integration_name,
                    status=resp.status_code,
                    attempt=attempt,
                )
            except Exception as exc:
                logger.warning(
                    "splunk_http_exception",
                    integration=self.integration_name,
                    attempt=attempt,
                    error=str(exc),
                )
            if attempt < _MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2

        return False
