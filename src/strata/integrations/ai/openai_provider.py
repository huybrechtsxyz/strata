"""OpenAI and Azure OpenAI LLM provider."""

import time

import requests

from strata.integrations.ai.base_ai_provider import AiResponse, BaseAiProvider
from strata.logger import get_logger

logger = get_logger(__name__)

_OPENAI_BASE = "https://api.openai.com"
_AZURE_API_VERSION = "2024-02-01"


class OpenAiProvider(BaseAiProvider):
    """Provider for OpenAI and Azure OpenAI via the chat completions API.

    Set ``is_azure=True`` and supply ``endpoint`` for Azure OpenAI.
    The ``model`` parameter is the deployment name on Azure.
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        endpoint: str = "",
        is_azure: bool = False,
        timeout: int = 60,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/") if endpoint else _OPENAI_BASE
        self._is_azure = is_azure
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _chat_url(self) -> str:
        if self._is_azure:
            return (
                f"{self._endpoint}/openai/deployments/{self._model}/chat/completions?api-version={_AZURE_API_VERSION}"
            )
        return f"{self._endpoint}/v1/chat/completions"

    def _headers(self) -> dict:
        if self._is_azure:
            return {"api-key": self._api_key, "Content-Type": "application/json"}
        return {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}

    # ------------------------------------------------------------------
    # BaseAiProvider
    # ------------------------------------------------------------------

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> AiResponse:
        payload: dict = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if not self._is_azure:
            payload["model"] = self._model

        t0 = time.monotonic()
        try:
            resp = requests.post(self._chat_url(), headers=self._headers(), json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"OpenAI request failed: {exc}") from exc

        data = resp.json()
        duration_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage", {})
        provider = "azure_openai" if self._is_azure else "openai"
        logger.debug("openai_complete", provider=provider, model=self._model, duration_ms=duration_ms)

        choices = data.get("choices", [])
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise RuntimeError(f"Unexpected OpenAI response structure: {data}")
        content = choices[0]["message"].get("content", "")

        return AiResponse(
            content=content,
            provider=provider,
            model=self._model,
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            duration_ms=duration_ms,
        )

    def is_available(self) -> bool:
        try:
            if self._is_azure:
                url = f"{self._endpoint}/openai/models?api-version={_AZURE_API_VERSION}"
                resp = requests.get(url, headers={"api-key": self._api_key}, timeout=5)
            else:
                resp = requests.get(
                    f"{self._endpoint}/v1/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=5,
                )
            return resp.status_code == 200
        except Exception:
            return False
