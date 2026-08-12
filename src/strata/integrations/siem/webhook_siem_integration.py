"""Webhook SIEM sink — generic HTTP POST audit forwarding (ADR-0066, ADR-0065).

Was the built-in ``type: webhook`` *sink* (``AuditController._send_webhook``); ADR-0066
promotes it to a real integration, since "a sink is a connection to another system" and
a webhook clearly is one — a sink is now only a routing reference (``sinks[].integration``).
This is also ADR-0065's primary transport to a first-party strata state service.

Config::

    integrations:
      - name: strata-ingest
        type: webhook
        capabilities: [audit]
        endpoints:
          address: https://ingest.acme.internal/v1/events   # must be https:// (see allow_insecure)
        authentication:
          method: api_key
          api_key:
            api_key: "${secret:ingest_token}"
            header_name: Authorization
        properties:
          headers:                # non-secret routing headers only — credentials go
            X-Scope-OrgID: acme   # through `authentication`, never here (ADR-0066 problem 9)
          allow_insecure: false    # set true only for local/plaintext testing

Inherits retry/backoff and auth-header construction from ``SiemBaseIntegration._post_json``.
"""

from __future__ import annotations

from typing import List, Tuple

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)


class WebhookSiemIntegration(SiemBaseIntegration):
    """Forwards structured audit events to a generic HTTPS webhook (was the `webhook` sink type)."""

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)

    # -------------------------------------------------------------------------
    # Availability — enforce https:// unless explicitly opted out
    # -------------------------------------------------------------------------

    def ensure_available(self) -> Tuple[bool, str]:
        ok, detail = super().ensure_available()
        if not ok:
            return ok, detail
        address = self.config.endpoints.address if self.config.endpoints else ""
        insecure, reason = self._rejects_insecure_address(address)
        if insecure:
            self._info = reason
            return False, reason
        return True, ""

    def _rejects_insecure_address(self, address: str) -> Tuple[bool, str]:
        """Return (True, reason) when *address* is plaintext and not explicitly allowed."""
        if address.lower().startswith("https://"):
            return False, ""
        if self._prop("allow_insecure", False):
            return False, ""
        return True, (
            f"{self.integration_name}: endpoints.address must use https:// "
            "(set properties.allow_insecure: true to allow plaintext, e.g. local testing)"
        )

    # -------------------------------------------------------------------------
    # ISiemSink implementation
    # -------------------------------------------------------------------------

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        return self.send_batch(log_type, [payload], **kwargs)

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        address = self.config.endpoints.address if self.config.endpoints else None
        if not address:
            logger.warning("webhook_no_endpoint", integration=self.integration_name)
            return False

        insecure, reason = self._rejects_insecure_address(address)
        if insecure:
            logger.warning("webhook_insecure_address_rejected", integration=self.integration_name, reason=reason)
            return False

        extra_headers = self._prop("headers", {}) or {}
        result = True
        for payload in payloads:
            if not self._post_json(address, payload, extra_headers=extra_headers):
                result = False
        return result
