"""Abstract base class for SIEM sink integrations.

All concrete SIEM integrations (Sentinel, ELK, OTel) extend this class.
Provides:
- Shared HTTP transport with retry + timeout (via requests)
- Auth header construction from IntegrationModel.authentication
- Graceful failure — send_event / send_batch always return bool, never raise
- Abstract send_event / send_batch for subclasses to implement
"""

from __future__ import annotations

import json
import time
from abc import abstractmethod
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ISiemSink
from strata.logger import get_logger

logger = get_logger(__name__)

_REQUESTS_TIMEOUT = 15  # seconds
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0  # seconds, doubled each retry


class SiemBaseIntegration(BaseIntegration):
    """Abstract base for all SIEM sink integrations.

    Subclasses must implement ``send_event`` and ``send_batch``.
    HTTP transport helpers (``_post_json``) are provided here.
    """

    # SIEM integrations are HTTP-based — no CLI command.
    # Override the abstract methods with no-ops.
    CAPABILITIES: list = [ISiemSink]

    # -------------------------------------------------------------------------
    # BaseIntegration abstract method stubs (SIEM has no CLI version command)
    # -------------------------------------------------------------------------

    def get_version_command(self) -> List[str]:
        """Not applicable for HTTP-based SIEM integrations."""
        return []

    def parse_version(self, version_output: str) -> str:
        """Not applicable for HTTP-based SIEM integrations."""
        return "n/a"

    def is_available(self, use_cache: bool = True) -> bool:
        """Return True when the configured endpoint address is present."""
        if use_cache and self._is_available is not None:
            return self._is_available
        self._is_available = bool(self.config.endpoints and self.config.endpoints.address)
        return self._is_available

    def ensure_available(self) -> Tuple[bool, str]:
        if not self.is_available():
            msg = f"{self.integration_name}: no endpoint address configured"
            self._info = msg
            return False, msg
        address = self.config.endpoints.address if self.config.endpoints else "unknown"
        self._info = f"{self.integration_name} endpoint: {address}"
        return True, ""

    def get_setup_info(self) -> dict:
        return {
            "name": self.integration_type,
            "command": None,
            "install_url": None,
            "env_vars": [],
            "auth_methods": [],
            "yaml_example": None,
        }

    # -------------------------------------------------------------------------
    # ISiemSink protocol — subclasses must implement
    # -------------------------------------------------------------------------

    @abstractmethod
    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        """Send a single structured event to the SIEM sink."""
        ...

    @abstractmethod
    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        """Send a batch of structured events to the SIEM sink."""
        ...

    # -------------------------------------------------------------------------
    # Shared HTTP transport
    # -------------------------------------------------------------------------

    def _post_json(
        self,
        url: str,
        body: Any,
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """HTTP POST JSON body to *url*.

        Retries up to ``_MAX_RETRIES`` times on transient errors (5xx, network).
        Returns True on 2xx, False otherwise.
        """
        if requests is None:
            logger.warning("siem_requests_unavailable", integration=self.integration_name)
            return False

        headers = {"Content-Type": "application/json"}
        headers.update(self._build_auth_headers())
        if extra_headers:
            headers.update(extra_headers)

        body_bytes = json.dumps(body, default=str).encode("utf-8")
        backoff = _RETRY_BACKOFF

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = requests.post(url, data=body_bytes, headers=headers, timeout=_REQUESTS_TIMEOUT)
                if resp.ok:
                    return True
                if resp.status_code < 500:
                    # Client error — don't retry
                    logger.warning(
                        "siem_http_client_error",
                        integration=self.integration_name,
                        status=resp.status_code,
                        body=resp.text[:200],
                    )
                    return False
                # Server error — retry
                logger.warning(
                    "siem_http_server_error",
                    integration=self.integration_name,
                    status=resp.status_code,
                    attempt=attempt,
                )
            except Exception as exc:
                logger.warning(
                    "siem_http_exception",
                    integration=self.integration_name,
                    attempt=attempt,
                    error=str(exc),
                )
            if attempt < _MAX_RETRIES:
                time.sleep(backoff)
                backoff *= 2

        return False

    def _build_auth_headers(self) -> Dict[str, str]:
        """Build authentication headers from ``config.authentication``."""
        auth = self.config.authentication
        if not auth:
            return {}

        method = getattr(auth, "method", None)

        # api_key method: use APIKeyAuthenticationModel
        if method == "api_key" and auth.api_key:
            key = auth.api_key.api_key or ""
            header_name = auth.api_key.header_name or "X-API-Key"
            return {header_name: key}

        # oauth2 method with access token (bearer)
        if method == "oauth2" and auth.oauth2:
            token = getattr(auth.oauth2, "client_secret", None) or ""
            return {"Authorization": f"Bearer {token}"}

        # managed_identity / other methods handled per-integration (e.g. Sentinel gets token from SDK)
        return {}

    # -------------------------------------------------------------------------
    # Helper: config properties bag
    # -------------------------------------------------------------------------

    def _prop(self, key: str, default: Any = None) -> Any:
        """Retrieve a value from ``config.properties`` by key."""
        props = self.config.properties or {}
        return props.get(key, default)
