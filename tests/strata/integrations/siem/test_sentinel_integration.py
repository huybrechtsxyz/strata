"""Tests for SentinelIntegration (Azure Monitor DCR Logs Ingestion)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from strata.integrations.siem.sentinel_integration import SentinelIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _make_sentinel_config(
    address: str = "https://my-dce.eastus-1.ingest.monitor.azure.com",
    dcr_id: str = "dcr-abc123",
    stream: str = "Custom-DeployAudit_CL",
) -> IntegrationModel:
    return IntegrationModel(
        name="sentinel",
        type="sentinel",
        enabled=True,
        endpoints=IntegrationEndpointsSpecModel(address=address),
        properties={"data_collection_rule_id": dcr_id, "stream_name": stream},
    )


class TestSentinelBuildUrl:
    def test_builds_correct_url(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)
        url = siem._build_url()
        assert "/dataCollectionRules/dcr-abc123/streams/Custom-DeployAudit_CL" in url
        assert "api-version=2023-01-01" in url

    def test_returns_none_when_no_dcr_id(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config(dcr_id="")
        siem = SentinelIntegration(config=cfg)
        assert siem._build_url() is None

    def test_returns_none_when_no_stream(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config(stream="")
        siem = SentinelIntegration(config=cfg)
        assert siem._build_url() is None

    def test_returns_none_when_no_endpoint(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        cfg.endpoints = None
        siem = SentinelIntegration(config=cfg)
        assert siem._build_url() is None


class TestSentinelSendBatch:
    def test_send_batch_success(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)

        mock_resp = MagicMock()
        mock_resp.ok = True

        with patch.object(siem, "_get_access_token", return_value="mytoken"):
            with patch.object(siem, "_post_json", return_value=True) as mock_post:
                result = siem.send_batch("deploy_audit", [{"key": "val"}])

        assert result is True
        mock_post.assert_called_once()
        args = mock_post.call_args
        assert (
            args.kwargs.get("extra_headers", args[1].get("extra_headers", {})).get("Authorization") == "Bearer mytoken"
        )

    def test_send_batch_returns_false_when_no_token(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)

        with patch.object(siem, "_get_access_token", return_value=None):
            result = siem.send_batch("deploy_audit", [{"key": "val"}])

        assert result is False

    def test_send_event_delegates_to_send_batch(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)

        with patch.object(siem, "send_batch", return_value=True) as mock_batch:
            result = siem.send_event("deploy_audit", {"k": "v"})

        assert result is True
        mock_batch.assert_called_once_with("deploy_audit", [{"k": "v"}])

    def test_send_batch_returns_false_on_exception(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)

        with patch.object(siem, "_get_access_token", side_effect=Exception("auth error")):
            result = siem.send_batch("deploy_audit", [{}])

        assert result is False


class TestSentinelGetAccessToken:
    def test_returns_token_string(self):
        """Token is returned from a pre-injected credential (avoids inline import issues)."""
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)

        mock_credential = MagicMock()
        mock_token = MagicMock()
        mock_token.token = "access_token_value"
        mock_credential.get_token.return_value = mock_token
        siem._credential = mock_credential  # inject pre-built credential

        token = siem._get_access_token()

        assert token == "access_token_value"
        mock_credential.get_token.assert_called_once()

    def test_returns_none_when_credential_raises(self):
        SentinelIntegration._instances.clear()
        cfg = _make_sentinel_config()
        siem = SentinelIntegration(config=cfg)

        mock_credential = MagicMock()
        mock_credential.get_token.side_effect = Exception("CredentialUnavailableError")
        siem._credential = mock_credential

        token = siem._get_access_token()

        assert token is None
