"""Tests for OllamaProvider."""

from unittest.mock import MagicMock, patch

import pytest

from strata.integrations.ai.ollama_provider import OllamaProvider


def _mock_response(content="hello", prompt_tokens=10, eval_count=5):
    mock = MagicMock()
    mock.status_code = 200
    mock.json.return_value = {
        "message": {"role": "assistant", "content": content},
        "prompt_eval_count": prompt_tokens,
        "eval_count": eval_count,
    }
    mock.raise_for_status = MagicMock()
    return mock


class TestOllamaProviderInit:
    def test_defaults(self):
        p = OllamaProvider()
        assert p._endpoint == "http://localhost:11434"
        assert p._model == "llama3"
        assert p._timeout == 60

    def test_custom_endpoint_strips_slash(self):
        p = OllamaProvider(endpoint="http://myhost:11434/", model="mistral")
        assert p._endpoint == "http://myhost:11434"

    def test_custom_model(self):
        p = OllamaProvider(model="phi3")
        assert p._model == "phi3"


class TestOllamaProviderComplete:
    def test_successful_completion(self):
        p = OllamaProvider(model="llama3")
        with patch("strata.integrations.ai.ollama_provider.requests.post", return_value=_mock_response("result")):
            r = p.complete("system", "user")
        assert r.content == "result"
        assert r.provider == "ollama"
        assert r.model == "llama3"
        assert r.prompt_tokens == 10
        assert r.completion_tokens == 5
        assert r.duration_ms >= 0

    def test_raises_on_request_error(self):
        import requests as _req
        p = OllamaProvider()
        with patch("strata.integrations.ai.ollama_provider.requests.post", side_effect=_req.ConnectionError("refused")):
            with pytest.raises(RuntimeError, match="Ollama request failed"):
                p.complete("sys", "user")

    def test_payload_structure(self):
        p = OllamaProvider(model="mistral", endpoint="http://host:11434")
        captured = {}
        mock_resp = _mock_response()

        def fake_post(url, json, timeout):
            captured["url"] = url
            captured["json"] = json
            return mock_resp

        with patch("strata.integrations.ai.ollama_provider.requests.post", side_effect=fake_post):
            p.complete("sys_prompt", "user_prompt", max_tokens=512, temperature=0.5)

        assert captured["url"] == "http://host:11434/api/chat"
        msgs = captured["json"]["messages"]
        assert msgs[0] == {"role": "system", "content": "sys_prompt"}
        assert msgs[1] == {"role": "user", "content": "user_prompt"}
        assert captured["json"]["options"]["num_predict"] == 512
        assert captured["json"]["options"]["temperature"] == 0.5


class TestOllamaProviderIsAvailable:
    def test_available_on_200(self):
        p = OllamaProvider()
        mock_resp = MagicMock(status_code=200)
        with patch("strata.integrations.ai.ollama_provider.requests.get", return_value=mock_resp):
            assert p.is_available() is True

    def test_unavailable_on_non_200(self):
        p = OllamaProvider()
        mock_resp = MagicMock(status_code=404)
        with patch("strata.integrations.ai.ollama_provider.requests.get", return_value=mock_resp):
            assert p.is_available() is False

    def test_unavailable_on_exception(self):
        p = OllamaProvider()
        with patch("strata.integrations.ai.ollama_provider.requests.get", side_effect=ConnectionError()):
            assert p.is_available() is False
