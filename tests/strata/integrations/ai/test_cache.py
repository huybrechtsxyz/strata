"""Tests for AiResponseCache."""

from pathlib import Path

from strata.integrations.ai.base_ai_provider import AiResponse
from strata.integrations.ai.cache import AiResponseCache


def _response(content="test result") -> AiResponse:
    return AiResponse(
        content=content,
        provider="ollama",
        model="llama3",
        prompt_tokens=10,
        completion_tokens=5,
        duration_ms=100,
    )


class TestAiResponseCacheBasic:
    def test_miss_returns_none(self, tmp_path: Path):
        cache = AiResponseCache(tmp_path)
        assert cache.get("v1", "abc123", "llama3") is None

    def test_put_and_get(self, tmp_path: Path):
        cache = AiResponseCache(tmp_path)
        r = _response("the answer")
        cache.put("v1", "abc123", "llama3", r)
        retrieved = cache.get("v1", "abc123", "llama3")
        assert retrieved is not None
        assert retrieved.content == "the answer"
        assert retrieved.cached is True

    def test_different_keys_dont_collide(self, tmp_path: Path):
        cache = AiResponseCache(tmp_path)
        cache.put("v1", "hash1", "m", _response("r1"))
        cache.put("v1", "hash2", "m", _response("r2"))
        assert cache.get("v1", "hash1", "m").content == "r1"
        assert cache.get("v1", "hash2", "m").content == "r2"

    def test_expired_entry_returns_none(self, tmp_path: Path):
        cache = AiResponseCache(tmp_path, ttl=0)
        cache.put("v1", "h", "m", _response())
        # TTL=0 means any entry is immediately expired
        assert cache.get("v1", "h", "m") is None


class TestAiResponseCacheContentHash:
    def test_same_content_same_hash(self):
        h1 = AiResponseCache.content_hash({"a": 1, "b": [2, 3]})
        h2 = AiResponseCache.content_hash({"b": [2, 3], "a": 1})
        assert h1 == h2  # sort_keys=True ensures stable ordering

    def test_different_content_different_hash(self):
        h1 = AiResponseCache.content_hash({"x": 1})
        h2 = AiResponseCache.content_hash({"x": 2})
        assert h1 != h2


class TestAiResponseCacheInvalidate:
    def test_invalidate_removes_expired(self, tmp_path: Path):
        cache = AiResponseCache(tmp_path, ttl=0)
        cache.put("v1", "h1", "m", _response())
        cache.put("v1", "h2", "m", _response())
        deleted = cache.invalidate()
        assert deleted == 2
        assert not any(tmp_path.glob("*.json"))

    def test_invalidate_keeps_fresh(self, tmp_path: Path):
        cache = AiResponseCache(tmp_path, ttl=3600)
        cache.put("v1", "h1", "m", _response())
        deleted = cache.invalidate()
        assert deleted == 0
