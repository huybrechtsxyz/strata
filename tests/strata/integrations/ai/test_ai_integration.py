"""Tests for AiAgentIntegration."""

from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.ai.ai_integration import AiAgentIntegration
from strata.integrations.ai.base_ai_provider import AiResponse
from strata.integrations.base_integration import BaseIntegration
from strata.models.integration_model import IntegrationModel


def _cfg(provider="ollama", model="llama3", extra_props=None) -> IntegrationModel:
    props = {"provider": provider, "model": model}
    if extra_props:
        props.update(extra_props)
    return IntegrationModel(name="ai-test", type="ai_agent", properties=props)


def _mock_response(
    content='{"summary":"ok","risk":"low","creates":1,"updates":0,"replaces":0,"deletes":0,"concerns":[],"recommendations":[]}',
):
    return AiResponse(
        content=content,
        provider="ollama",
        model="llama3",
        prompt_tokens=50,
        completion_tokens=20,
        duration_ms=200,
    )


class TestAiAgentIntegrationInit:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_provider_type_from_properties(self):
        i = AiAgentIntegration(_cfg("anthropic"))
        assert i._provider_type == "anthropic"

    def test_defaults(self):
        i = AiAgentIntegration(_cfg())
        assert i._temperature == 0.1
        assert i._max_tokens == 4096
        assert i._timeout == 60

    def test_custom_props(self):
        i = AiAgentIntegration(_cfg(extra_props={"temperature": 0.5, "max_tokens": 1000}))
        assert i._temperature == 0.5
        assert i._max_tokens == 1000


class TestAiAgentIntegrationAvailability:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_is_available_delegates_to_provider(self):
        i = AiAgentIntegration(_cfg())
        mock_provider = MagicMock()
        mock_provider.is_available.return_value = True
        i._provider = mock_provider
        assert i.is_available() is True

    def test_is_available_returns_false_on_exception(self):
        i = AiAgentIntegration(_cfg())
        mock_provider = MagicMock()
        mock_provider.is_available.side_effect = RuntimeError("connection refused")
        i._provider = mock_provider
        assert i.is_available() is False

    def test_ensure_available_returns_error_when_unavailable(self):
        i = AiAgentIntegration(_cfg())
        i._is_available = False
        ok, msg = i.ensure_available()
        assert not ok
        assert "ollama" in msg.lower()


class TestAiAgentIntegrationProviderFactory:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_builds_ollama_provider(self):
        i = AiAgentIntegration(_cfg("ollama", "llama3"))
        from strata.integrations.ai.ollama_provider import OllamaProvider

        assert isinstance(i.provider, OllamaProvider)

    def test_raises_on_unknown_provider(self):
        i = AiAgentIntegration(_cfg("nonexistent_provider"))
        with pytest.raises(ValueError, match="Unknown AI provider"):
            _ = i.provider

    def test_provider_cached(self):
        i = AiAgentIntegration(_cfg())
        p1 = i.provider
        p2 = i.provider
        assert p1 is p2


class TestAiAgentIntegrationAnalysePlan:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_analyse_plan_calls_provider_and_audits(self):
        i = AiAgentIntegration(_cfg())
        mock_provider = MagicMock()
        mock_provider.complete.return_value = _mock_response()
        i._provider = mock_provider

        with patch("strata.integrations.ai.ai_integration.audit") as mock_audit:
            # work_path=None disables caching so the provider is always called
            r = i.analyse_plan({"stages": []}, {"deployment": "test"})

        assert r.content is not None
        mock_provider.complete.assert_called_once()
        mock_audit.assert_called_once()
        assert "ai_agent.analyse_plan" in mock_audit.call_args[0]

    def test_hook_enabled(self):
        i = AiAgentIntegration(_cfg(extra_props={"enabled_hooks": ["deploy_plan_after"]}))
        assert i.hook_enabled("deploy_plan_after") is True
        assert i.hook_enabled("build_sbom_after") is False


class TestAiAgentIntegrationAbstractMethods:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_get_version_command_returns_empty(self):
        i = AiAgentIntegration(_cfg())
        assert i.get_version_command() == []

    def test_parse_version_returns_model_name(self):
        i = AiAgentIntegration(_cfg(model="gpt-4o"))
        assert i.parse_version("anything") == "gpt-4o"
