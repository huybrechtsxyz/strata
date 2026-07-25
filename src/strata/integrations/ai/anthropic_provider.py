"""Anthropic Claude LLM provider via the /v1/messages API."""

import time

import requests

from strata.integrations.ai.base_ai_provider import AiResponse, BaseAiProvider
from strata.logger import get_logger

logger = get_logger(__name__)

_BASE_URL = "https://api.anthropic.com"
_API_VERSION = "2023-06-01"


class AnthropicProvider(BaseAiProvider):
    """Provider for Anthropic Claude models."""

    def __init__(self, model: str, api_key: str, timeout: int = 60) -> None:
        self._model = model
        self._api_key = api_key
        self._timeout = timeout

    def _headers(self) -> dict:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": _API_VERSION,
            "Content-Type": "application/json",
        }

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> AiResponse:
        payload: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        t0 = time.monotonic()
        try:
            resp = requests.post(
                f"{_BASE_URL}/v1/messages",
                headers=self._headers(),
                json=payload,
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Anthropic request failed: {exc}") from exc

        data = resp.json()
        duration_ms = int((time.monotonic() - t0) * 1000)
        usage = data.get("usage", {})
        logger.debug("anthropic_complete", model=self._model, duration_ms=duration_ms)

        content_blocks = data.get("content", [])
        if not content_blocks or not isinstance(content_blocks[0], dict):
            raise RuntimeError(f"Unexpected Anthropic response structure: {data}")
        content = content_blocks[0].get("text", "")

        return AiResponse(
            content=content,
            provider="anthropic",
            model=self._model,
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            duration_ms=duration_ms,
        )

    def is_available(self) -> bool:
        try:
            resp = requests.get(
                f"{_BASE_URL}/v1/models",
                headers=self._headers(),
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:
            return False
