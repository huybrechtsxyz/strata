"""Azure OpenAI provider that acquires bearer tokens via the existing AzureCLIIntegration.

Uses ``AzureCLIIntegration.get_access_token()`` with the Cognitive Services resource
scope — no API key required, just ``az login``.  The integration instance and token
are cached for the lifetime of the provider to avoid spawning a subprocess on every
HTTP request.
"""

from typing import Optional

from strata.integrations.ai.openai_provider import OpenAiProvider
from strata.logger import get_logger

logger = get_logger(__name__)

_COGSERVICES_RESOURCE = "https://cognitiveservices.azure.com/"


class AzureCliProvider(OpenAiProvider):
    """Azure OpenAI backed by ``az account get-access-token`` (no stored key).

    The ``AzureCLIIntegration`` instance is created once and reused; its
    own in-process token cache (``_token_cache``) handles token lifetime.
    """

    def __init__(self, model: str, endpoint: str, timeout: int = 60) -> None:
        # Pass empty api_key — _headers() is overridden to use a fresh token.
        super().__init__(model=model, api_key="", endpoint=endpoint, is_azure=True, timeout=timeout)
        self._cli_integration: Optional[object] = None  # AzureCLIIntegration, lazy init

    # ------------------------------------------------------------------
    # Token acquisition via AzureCLIIntegration (cached in-process)
    # ------------------------------------------------------------------

    def _ensure_cli_integration(self) -> "object":
        if self._cli_integration is None:
            from strata.integrations.azure_cli import AzureCLIIntegration
            from strata.models.integration_model import IntegrationModel

            self._cli_integration = AzureCLIIntegration(IntegrationModel(name="azure_cli_ai", type="azure_cli"))
        return self._cli_integration

    def _get_token(self) -> str:
        cli = self._ensure_cli_integration()
        token = cli.get_access_token(resource=_COGSERVICES_RESOURCE)  # type: ignore[union-attr]
        if not token:
            raise RuntimeError("Could not acquire Azure CLI token — run 'az login' first")
        return token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._get_token()}", "Content-Type": "application/json"}

    def is_available(self) -> bool:
        try:
            self._get_token()
            return True
        except Exception:
            return False
