"""Tests for BaseAiProvider ABC and AiResponse dataclass."""

import pytest

from strata.integrations.ai.base_ai_provider import AiResponse, BaseAiProvider


class _ConcreteProvider(BaseAiProvider):
    """Minimal concrete implementation for testing the ABC."""

    def complete(self, system_prompt, user_prompt, max_tokens=4096, temperature=0.1):
        return AiResponse(
            content="ok",
            provider="test",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            duration_ms=100,
        )

    def is_available(self):
        return True


class TestAiResponse:
    def test_total_tokens(self):
        r = AiResponse(
            content="x",
            provider="p",
            model="m",
            prompt_tokens=30,
            completion_tokens=10,
            duration_ms=50,
        )
        assert r.total_tokens == 40

    def test_cached_default_false(self):
        r = AiResponse(content="x", provider="p", model="m", prompt_tokens=0, completion_tokens=0, duration_ms=0)
        assert r.cached is False

    def test_cached_flag(self):
        r = AiResponse(
            content="x", provider="p", model="m", prompt_tokens=0, completion_tokens=0, duration_ms=0, cached=True
        )
        assert r.cached is True


class TestBaseAiProvider:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseAiProvider()  # type: ignore[abstract]

    def test_concrete_complete(self):
        p = _ConcreteProvider()
        r = p.complete("sys", "user")
        assert r.content == "ok"
        assert r.provider == "test"

    def test_concrete_is_available(self):
        p = _ConcreteProvider()
        assert p.is_available() is True
