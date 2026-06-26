"""Azure Sentinel (Monitor Logs Ingestion API) SIEM sink.

Uses the DCR-based Logs Ingestion API:
  POST https://{dce-endpoint}/dataCollectionRules/{dcr-immutable-id}/streams/{stream-name}
       ?api-version=2023-01-01

Authentication: azure-identity DefaultAzureCredential (managed identity, service principal,
or Azure CLI — whichever is available in the environment).

Required properties in config.properties:
  - data_collection_rule_id: str   (immutable DCR ID, e.g. "dcr-abc123")
  - stream_name:             str   (custom stream name, e.g. "Custom-DeployAudit_CL")

The DCE address comes from config.endpoints.address.
"""

from __future__ import annotations

from typing import Any, List, Optional

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.logger import get_logger
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)

# Azure Monitor Logs Ingestion API version
_API_VERSION = "2023-01-01"
# Scope required to obtain a bearer token for the Logs Ingestion API
_INGESTION_SCOPE = "https://monitor.azure.com/.default"


class SentinelIntegration(SiemBaseIntegration):
    """Forwards structured audit events to Azure Sentinel via the DCR Logs Ingestion API."""

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        config = kwargs.get("config") or (args[0] if args else None)
        if config and config.endpoints and config.endpoints.address:
            return config.endpoints.address
        return "default"

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)
        self._credential: Optional[Any] = None

    # -------------------------------------------------------------------------
    # ISiemSink implementation
    # -------------------------------------------------------------------------

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        """Send a single event to the Sentinel stream."""
        return self.send_batch(log_type, [payload], **kwargs)

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        """Send a batch of events to the Sentinel DCR stream."""
        try:
            url = self._build_url()
            if not url:
                return False

            token = self._get_access_token()
            if not token:
                logger.warning("sentinel_auth_failed", integration=self.integration_name)
                return False

            # Tag each record with log_type
            tagged = [{**p, "_log_type": log_type} for p in payloads]
            extra_headers = {"Authorization": f"Bearer {token}"}
            return self._post_json(url, tagged, extra_headers=extra_headers)

        except Exception as exc:
            logger.warning(
                "sentinel_send_failed",
                integration=self.integration_name,
                error=str(exc),
            )
            return False

    # -------------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------------

    def _build_url(self) -> Optional[str]:
        if not self.config.endpoints or not self.config.endpoints.address:
            logger.warning("sentinel_no_endpoint", integration=self.integration_name)
            return None
        dce = self.config.endpoints.address.rstrip("/")
        dcr_id = self._prop("data_collection_rule_id")
        stream = self._prop("stream_name")
        if not dcr_id or not stream:
            logger.warning(
                "sentinel_missing_properties",
                integration=self.integration_name,
                has_dcr=bool(dcr_id),
                has_stream=bool(stream),
            )
            return None
        return f"{dce}/dataCollectionRules/{dcr_id}/streams/{stream}?api-version={_API_VERSION}"

    def _get_access_token(self) -> Optional[str]:
        """Acquire a bearer token using DefaultAzureCredential."""
        try:
            from azure.identity import DefaultAzureCredential  # type: ignore[import-untyped]

            if self._credential is None:
                self._credential = DefaultAzureCredential()
            token = self._credential.get_token(_INGESTION_SCOPE)
            return token.token
        except Exception as exc:
            logger.warning(
                "sentinel_token_error",
                integration=self.integration_name,
                error=str(exc),
            )
            return None
