"""Ollama LLM provider — local, no auth, uses /api/chat endpoint."""

import time

import requests

from strata.integrations.ai.base_ai_provider import AiResponse, BaseAiProvider
from strata.logger import get_logger

logger = get_logger(__name__)

_DEFAULT_ENDPOINT = "http://localhost:11434"
_DEFAULT_MODEL = "llama3"


class OllamaProvider(BaseAiProvider):
    """Provider for local Ollama models via the /api/chat endpoint."""

    def __init__(self, endpoint: str = _DEFAULT_ENDPOINT, model: str = _DEFAULT_MODEL, timeout: int = 60) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._model = model
        self._timeout = timeout

    def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.1,
    ) -> AiResponse:
        url = f"{self._endpoint}/api/chat"
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        t0 = time.monotonic()
        try:
            resp = requests.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc

        data = resp.json()
        duration_ms = int((time.monotonic() - t0) * 1000)
        logger.debug("ollama_complete", model=self._model, duration_ms=duration_ms)

        message = data.get("message")
        if not isinstance(message, dict) or "content" not in message:
            raise RuntimeError(f"Unexpected Ollama response structure: {data}")

        return AiResponse(
            content=message["content"],
            provider="ollama",
            model=self._model,
            prompt_tokens=data.get("prompt_eval_count", 0),
            completion_tokens=data.get("eval_count", 0),
            duration_ms=duration_ms,
        )

    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self._endpoint}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False
