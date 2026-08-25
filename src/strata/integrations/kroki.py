"""Kroki integration — renders diagram text (Mermaid) to SVG/PNG via a simple HTTP call.

Kroki (https://kroki.io) is a free, open-source diagram-rendering service. The
public instance needs no account, no API key, and no CLI install — a diagram is
rendered with a single HTTP POST of the diagram source and gets image bytes back.

Self-hosting: set ``endpoints.address`` to a self-hosted Kroki instance. Kroki's
own docs note that Mermaid rendering specifically needs the core ``yuzutech/kroki``
image *plus* the ``yuzutech/kroki-mermaid`` companion container running alongside
it — the core server alone does not render Mermaid.
https://docs.kroki.io/kroki/setup/install/

Configuration YAML::

    integrations:
      - name: kroki
        type: kroki
        capabilities: [diagram_render]
        endpoints:
          address: https://kroki.io   # or your self-hosted instance
"""

import os
from typing import Any, List, Optional, Tuple

import requests

from strata.integrations.base_integration import BaseIntegration
from strata.logger import get_logger
from strata.models.capabilities import IDiagramRenderer
from strata.models.integration_model import IntegrationModel

logger = get_logger(__name__)

DEFAULT_KROKI_ADDRESS = "https://kroki.io"
_REQUEST_TIMEOUT = 30  # seconds


class KrokiIntegration(BaseIntegration):
    """Kroki integration — HTTP diagram rendering, no CLI binary, no auth.

    ``is_available()``/``ensure_available()`` only confirm an endpoint address
    is configured (matching the pattern used by HTTP-based SIEM sink
    integrations) — there is no CLI version command to shell out to, and no
    round trip to Kroki happens until ``render()`` is actually called.
    """

    COMMAND = "kroki"  # no real CLI binary; kept for BaseIntegration bookkeeping only
    CAPABILITIES = [IDiagramRenderer]

    @classmethod
    def _get_instance_key_static(cls, class_ref: Any, *args: Any, **kwargs: Any) -> str:
        """Separate singleton instances per endpoint (public vs. self-hosted)."""
        config = kwargs.get("config") or (args[0] if args else None)
        if config is not None and config.endpoints and config.endpoints.address:
            return config.endpoints.address
        return DEFAULT_KROKI_ADDRESS

    def __init__(self, config: IntegrationModel) -> None:
        super().__init__(config)
        address: Optional[str] = None
        if self.config.endpoints and self.config.endpoints.address:
            address = self._resolve_env_vars(self.config.endpoints.address)
        if not address:
            # Self-hosting without a declared integration: point STRATA_KROKI_ADDRESS
            # at your own instance. A declared 'endpoints.address' (above) always
            # wins over this when both are present.
            address = os.environ.get("STRATA_KROKI_ADDRESS")
        self.address = (address or DEFAULT_KROKI_ADDRESS).rstrip("/")

        logger.debug(
            "Kroki integration initialized",
            name=self.integration_name,
            address=self.address,
            is_default_public_instance=(self.address == DEFAULT_KROKI_ADDRESS),
        )

    # ------------------------------------------------------------------
    # BaseIntegration abstract methods
    # ------------------------------------------------------------------

    def get_version_command(self) -> List[str]:
        """Not applicable — Kroki is an HTTP API, not a local CLI binary."""
        return []

    def parse_version(self, version_output: str) -> str:
        """Not applicable for HTTP-based integrations."""
        return "n/a"

    def is_available(self, use_cache: bool = True) -> bool:
        """Return True when an endpoint address is configured.

        Always True out of the box — ``self.address`` defaults to the public
        instance — unless a caller explicitly configured an empty address.
        """
        if use_cache and self._is_available is not None:
            return self._is_available
        self._is_available = bool(self.address)
        return self._is_available

    def ensure_available(self) -> Tuple[bool, str]:
        if not self.is_available():
            return False, f"{self.integration_name}: no Kroki endpoint address configured"
        return True, ""

    def get_setup_info(self) -> dict:
        return {
            "name": self.integration_type,
            "command": None,
            "install_url": "https://docs.kroki.io/kroki/setup/install/",
            "env_vars": [],
            "auth_methods": [],
            "yaml_example": (
                "integrations:\n"
                "  - name: kroki\n"
                "    type: kroki\n"
                "    capabilities: [diagram_render]\n"
                "    endpoints:\n"
                "      address: https://kroki.io   # or your self-hosted instance\n"
            ),
        }

    # ------------------------------------------------------------------
    # IDiagramRenderer protocol
    # ------------------------------------------------------------------

    def render(self, diagram_source: str, diagram_type: str = "mermaid", output_format: str = "svg") -> bytes:
        """Render *diagram_source* to *output_format* bytes via Kroki's HTTP API.

        Uses ``POST /<diagram_type>/<output_format>`` with the source as a JSON
        body (``{"diagram_source": ...}``) — no encoding of the diagram text is
        needed for POST (unlike Kroki's GET-with-deflate-base64 URL form).

        Args:
            diagram_source: Raw diagram text (e.g. Mermaid syntax).
            diagram_type: Diagram language the source is written in.
            output_format: Desired image format (e.g. 'svg', 'png').

        Returns:
            Rendered image bytes.

        Raises:
            RuntimeError: If Kroki is unreachable or returns a non-2xx response.
        """
        url = f"{self.address}/{diagram_type}/{output_format}"
        try:
            response = requests.post(
                url,
                json={"diagram_source": diagram_source},
                timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Failed to reach Kroki at {self.address}: {exc}") from exc

        if response.status_code != 200:
            raise RuntimeError(
                f"Kroki returned HTTP {response.status_code} rendering {diagram_type}/{output_format}: "
                f"{response.text[:500]}"
            )
        return response.content
