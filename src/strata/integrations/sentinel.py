#!/usr/bin/env python3
"""Azure Sentinel audit sink — `type: sentinel`, Monitor Logs Ingestion API
(docs/design/audit-sink-dispatch.md).

**Correction recorded in that design's D2**: checked directly against v1's
real `siem/sentinel_integration.py` and found no HMAC signature anywhere —
that belongs to the legacy, now-deprecated HTTP Data Collector API. v1's
actual target, and this class's, is the modern **Monitor Logs Ingestion
API** (DCR/stream-based):
``POST {dce}/dataCollectionRules/{dcr-id}/streams/{stream-name}?api-version=2023-01-01``,
authenticated with a plain AAD bearer token — no custom cryptography.

``TRANSPORTS = {"sdk"}``, not `"http"`: token acquisition needs
`azure.identity.DefaultAzureCredential` (already a strata dependency),
matching `azure_keyvault_resolver.py`'s own precedent exactly — the SDK
carries its own auth chain, reimplementing it by hand would be the
leaner-is-worse direction ADR-0021 already rejected once. The actual event
POST is still a plain REST call via the inherited `self.request()`: there
is no dedicated Monitor Ingestion SDK client here, matching v1's own choice
not to pull one in either.

The acquired token is cached on the instance, not re-fetched per `send()`
call — a long deploy dispatching several audit events should not
re-authenticate for each one.

Configuration (all from the bound `Integration` document):

- ``spec.endpoints.address`` — the Data Collection Endpoint (DCE) base URL.
  **Required**: a real, separate Azure resource from the DCR itself.
- ``spec.configuration.data_collection_rule_id`` — the DCR's immutable ID.
  **Required**.
- ``spec.configuration.stream_name`` — the custom stream (e.g.
  ``Custom-DeployAudit_CL``). **Required**.

Both configuration keys are checked before any token/send is attempted —
fail before any side effect, the same discipline already used elsewhere in
this codebase.
"""

import json
from typing import Any, ClassVar

from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import DefaultAzureCredential

from strata.integrations.capabilities import AuditSinkIntegration
from strata.integrations.errors import IntegrationError
from strata.models.integration_model import Capability, IntegrationModel

_TOKEN_SCOPE = "https://monitor.azure.com/.default"
_API_VERSION = "2023-01-01"


class SentinelIntegration(AuditSinkIntegration):
    """POST a rendered audit event to an Azure Sentinel DCR stream via the Logs Ingestion API."""

    TYPE: ClassVar[str] = "sentinel"
    CAPABILITIES: ClassVar[frozenset[str]] = frozenset({Capability.AUDIT})
    TRANSPORTS: ClassVar[frozenset[str]] = frozenset({"sdk"})
    #: No CLI form at all — this integration exists only as an SDK-authenticated HTTP caller.
    COMMAND: ClassVar[str | None] = None

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._credential: TokenCredential | None = None

    def send(self, event: dict[str, Any]) -> None:
        """POST `[event]` to the configured DCR stream.

        The Logs Ingestion API wants a JSON **array** body (confirmed
        directly from v1's real implementation) — this keeps the design's
        D5 one-request-per-event rule, posting an array of exactly one.

        Raises:
            IntegrationError: No endpoint, or `data_collection_rule_id`/
                `stream_name`, is configured; the AAD token could not be
                acquired; or the endpoint did not answer with a 2xx.
        """
        if self.config is None or self.config.spec.endpoints is None:
            raise IntegrationError(
                f"{self.name}: no 'spec.endpoints.address' configured — a Sentinel audit sink needs the "
                "Data Collection Endpoint (DCE) URL."
            )
        dcr_id, stream_name = self._required_configuration()
        token = self._get_token()

        path = f"dataCollectionRules/{dcr_id}/streams/{stream_name}?api-version={_API_VERSION}"
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        body = json.dumps([event]).encode("utf-8")

        # `http_request()` returns a 4xx/5xx as a normal result, never an
        # exception, so a non-2xx has to be turned into a failure explicitly.
        result = self.request("POST", path, headers=headers, body=body)
        if not result.is_successful:
            detail = "timed out" if result.timed_out else f"HTTP {result.status}: {result.body.strip()[:200]}"
            raise IntegrationError(f"{self.name}: audit event delivery failed — {detail}")

    def _required_configuration(self) -> tuple[str, str]:
        """`(data_collection_rule_id, stream_name)`, both required.

        Raises:
            IntegrationError: Either key is missing/empty.
        """
        configuration = (self.config.spec.configuration if self.config is not None else None) or {}
        dcr_id = configuration.get("data_collection_rule_id")
        stream_name = configuration.get("stream_name")
        missing = [
            name for name, value in (("data_collection_rule_id", dcr_id), ("stream_name", stream_name)) if not value
        ]
        if missing:
            raise IntegrationError(
                f"{self.name}: configuration.{' and configuration.'.join(missing)} required for a "
                "Sentinel audit sink, not set."
            )
        return str(dcr_id), str(stream_name)

    def _get_token(self) -> str:
        """An AAD bearer token for the Monitor Logs Ingestion scope, cached on this instance.

        Raises:
            IntegrationError: The credential chain could not authenticate.
        """
        if self._credential is None:
            self._credential = DefaultAzureCredential()
        try:
            return self._credential.get_token(_TOKEN_SCOPE).token
        except ClientAuthenticationError as exc:
            raise IntegrationError(f"{self.name}: could not acquire an Azure AD token: {exc}") from exc
