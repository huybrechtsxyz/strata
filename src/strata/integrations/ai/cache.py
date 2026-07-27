"""SHA-256-keyed JSON response cache for AI analysis results.

Cache files are stored under ``.strata/cache/ai/`` (use
``strata.utils.config.get_ai_cache_dir(work_path)`` to resolve the path) with
a configurable TTL.  Failure-diagnosis responses are intentionally never
cached (always unique context).
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from strata.integrations.ai.base_ai_provider import AiResponse
from strata.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TTL_SECONDS = 86_400  # 24 h


class AiResponseCache:
    """File-backed cache for ``AiResponse`` objects keyed by content hash."""

    def __init__(self, cache_dir: Path, ttl: int = DEFAULT_TTL_SECONDS) -> None:
        self._cache_dir = cache_dir
        self._ttl = ttl
        cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _cache_key(self, prompt_version: str, content_hash: str, model: str) -> str:
        raw = f"{prompt_version}:{content_hash}:{model}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    @staticmethod
    def content_hash(data: object) -> str:
        """Return a stable SHA-256 hex digest for any JSON-serialisable object."""
        serialised = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialised.encode()).hexdigest()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, prompt_version: str, content_hash: str, model: str) -> Optional[AiResponse]:
        """Return a cached response, or ``None`` if absent or expired."""
        path = self._cache_path(self._cache_key(prompt_version, content_hash, model))
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except Exception:
            return None
        if time.time() - data.get("cached_at", 0) > self._ttl:
            path.unlink(missing_ok=True)
            return None
        r = data["response"]
        return AiResponse(
            content=r["content"],
            provider=r["provider"],
            model=r["model"],
            prompt_tokens=r["prompt_tokens"],
            completion_tokens=r["completion_tokens"],
            duration_ms=r["duration_ms"],
            cached=True,
        )

    def put(self, prompt_version: str, content_hash: str, model: str, response: AiResponse) -> None:
        """Persist a response to the cache."""
        key = self._cache_key(prompt_version, content_hash, model)
        entry = {
            "cached_at": time.time(),
            "response": {
                "content": response.content,
                "provider": response.provider,
                "model": response.model,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
                "duration_ms": response.duration_ms,
            },
        }
        try:
            self._cache_path(key).write_text(json.dumps(entry, indent=2))
            logger.debug("ai_cache_put", key=key[:12])
        except Exception as exc:
            logger.warning("ai_cache_write_failed", error=str(exc))

    def invalidate(self) -> int:
        """Remove all expired cache entries. Returns the number of files deleted."""
        deleted = 0
        now = time.time()
        for path in self._cache_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text())
                if now - data.get("cached_at", 0) > self._ttl:
                    path.unlink(missing_ok=True)
                    deleted += 1
            except Exception:
                pass
        return deleted
