"""Tests for SiemBaseIntegration HTTP transport utilities."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from strata.integrations.siem.base_siem_integration import SiemBaseIntegration
from strata.models.auth_models import APIKeyAuthenticationModel, AuthenticationModel
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


class _TestSiem(SiemBaseIntegration):
    """Minimal concrete subclass that implements the abstract ISiemSink methods."""

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        return True

    def send_batch(self, log_type: str, payloads, **kwargs) -> bool:
        return True

    @classmethod
    def _get_instance_key_static(cls, class_ref, *args, **kwargs) -> str:
        return "test"


def _make_config(
    address: str = "https://otel.example.com",
    authentication: AuthenticationModel | None = None,
    properties: dict | None = None,
) -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(
        name="test-siem",
        type="otel",
        enabled=True,
        authentication=authentication,
        endpoints=endpoints,
        properties=properties,
    )


def _make_api_key_auth(key: str = "abc123", header: str | None = None) -> AuthenticationModel:
    return AuthenticationModel(
        method="api_key",
        api_key=APIKeyAuthenticationModel(api_key=key, header_name=header),
    )


class TestIsAvailable:
    def test_returns_true_when_endpoint_set(self):
        cfg = _make_config(address="https://example.com")
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        siem._is_available = None
        assert siem.is_available() is True

    def test_returns_false_when_no_endpoint(self):
        cfg = _make_config(address="https://example.com")
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        siem.config.endpoints = None
        siem._is_available = None
        assert siem.is_available() is False

    def test_caches_result(self):
        cfg = _make_config(address="https://example.com")
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        siem._is_available = True
        siem.config.endpoints = None
        assert siem.is_available(use_cache=True) is True


class TestBuildAuthHeaders:
    def test_api_key_header_default_name(self):
        cfg = _make_config(authentication=_make_api_key_auth("my-key"))
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        headers = siem._build_auth_headers()
        assert headers == {"X-API-Key": "my-key"}

    def test_api_key_header_custom_name(self):
        cfg = _make_config(authentication=_make_api_key_auth("my-key", header="Authorization"))
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        headers = siem._build_auth_headers()
        assert headers == {"Authorization": "my-key"}

    def test_no_auth_returns_empty(self):
        cfg = _make_config()
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        assert siem._build_auth_headers() == {}


class TestProp:
    def test_returns_property_value(self):
        cfg = _make_config(properties={"my_key": "my_val"})
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        assert siem._prop("my_key") == "my_val"

    def test_returns_default_when_missing(self):
        cfg = _make_config(properties={})
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        assert siem._prop("missing", "fallback") == "fallback"

    def test_returns_default_when_no_properties(self):
        cfg = _make_config(properties=None)
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)
        assert siem._prop("any") is None


class TestPostJson:
    def test_successful_post_returns_true(self):
        cfg = _make_config()
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = siem._post_json("https://example.com/v1/logs", {"data": "value"})

        assert result is True
        mock_requests.post.assert_called_once()

    def test_post_client_error_no_retry(self):
        cfg = _make_config()
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = "bad request"

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = siem._post_json("https://example.com/v1/logs", {})

        assert result is False
        assert mock_requests.post.call_count == 1

    def test_post_server_error_retries_three_times(self):
        # max_retries defaults to 1 (no retry, ADR-0065 step 2.5) — explicitly
        # configure a higher value here to test the retry-loop mechanics themselves.
        cfg = _make_config(properties={"max_retries": 3, "retry_backoff_seconds": 0})
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503
        mock_resp.text = "service unavailable"

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            with patch("strata.integrations.siem.base_siem_integration.time") as mock_time:
                mock_time.sleep = MagicMock()
                mock_requests.post.return_value = mock_resp
                result = siem._post_json("https://example.com/v1/logs", {})

        assert result is False
        assert mock_requests.post.call_count == 3

    def test_post_exception_returns_false(self):
        cfg = _make_config()
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            with patch("strata.integrations.siem.base_siem_integration.time") as mock_time:
                mock_time.sleep = MagicMock()
                mock_requests.post.side_effect = Exception("connection refused")
                result = siem._post_json("https://example.com/v1/logs", {})

        assert result is False

    def test_default_max_retries_is_one_no_retry_on_server_error(self):
        """ADR-0065 step 2.5: default is no retry — resend is the real recovery path."""
        cfg = _make_config()
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            with patch("strata.integrations.siem.base_siem_integration.time") as mock_time:
                mock_time.sleep = MagicMock()
                mock_requests.post.return_value = mock_resp
                result = siem._post_json("https://example.com/v1/logs", {})

        assert result is False
        assert mock_requests.post.call_count == 1
        mock_time.sleep.assert_not_called()

    def test_max_retries_property_is_floored_at_one(self):
        """A configured 0 or negative max_retries must not disable the single attempt entirely."""
        cfg = _make_config(properties={"max_retries": 0})
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            mock_requests.post.return_value = mock_resp
            result = siem._post_json("https://example.com/v1/logs", {})

        assert result is True
        assert mock_requests.post.call_count == 1

    def test_retry_backoff_seconds_property_is_honoured(self):
        cfg = _make_config(properties={"max_retries": 2, "retry_backoff_seconds": 5})
        _TestSiem._instances.clear()
        siem = _TestSiem(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 503

        with patch("strata.integrations.siem.base_siem_integration.requests") as mock_requests:
            with patch("strata.integrations.siem.base_siem_integration.time") as mock_time:
                mock_time.sleep = MagicMock()
                mock_requests.post.return_value = mock_resp
                siem._post_json("https://example.com/v1/logs", {})

        mock_time.sleep.assert_called_once_with(5.0)
