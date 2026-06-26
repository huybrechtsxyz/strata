"""Tests for OtelSiemIntegration (OTLP/HTTP JSON exporter)."""

from __future__ import annotations

from unittest.mock import patch

from strata.integrations.siem.otel_siem_integration import OtelSiemIntegration
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _make_otel_config(
    address: str = "https://otel-collector.internal:4318",
    resource_attributes: dict | None = None,
) -> IntegrationModel:
    props = {}
    if resource_attributes:
        props["resource_attributes"] = resource_attributes
    return IntegrationModel(
        name="otel",
        type="otel",
        enabled=True,
        endpoints=IntegrationEndpointsSpecModel(address=address),
        properties=props or None,
    )


class TestOtelBuildUrl:
    def test_appends_v1_logs_path(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        siem = OtelSiemIntegration(config=cfg)
        url = siem._build_url()
        assert url == "https://otel-collector.internal:4318/v1/logs"

    def test_strips_trailing_slash(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config(address="https://otel.example.com/")
        siem = OtelSiemIntegration(config=cfg)
        url = siem._build_url()
        assert url == "https://otel.example.com/v1/logs"

    def test_returns_none_when_no_endpoint(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        cfg.endpoints = None
        siem = OtelSiemIntegration(config=cfg)
        assert siem._build_url() is None


class TestOtelBuildOtlpRequest:
    def test_produces_resource_logs_structure(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        siem = OtelSiemIntegration(config=cfg)
        body = siem._build_otlp_request("deploy_audit", [{"execution_id": "abc"}])

        assert "resourceLogs" in body
        rl = body["resourceLogs"][0]
        assert "resource" in rl
        assert "scopeLogs" in rl
        scope = rl["scopeLogs"][0]
        assert scope["scope"]["name"] == "strata.audit"
        assert len(scope["logRecords"]) == 1

    def test_includes_service_name_in_resource_attrs(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        siem = OtelSiemIntegration(config=cfg)
        body = siem._build_otlp_request("deploy_audit", [{}])

        attrs = {a["key"]: a["value"]["stringValue"] for a in body["resourceLogs"][0]["resource"]["attributes"]}
        assert attrs.get("service.name") == "strata-audit"

    def test_merges_extra_resource_attributes(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config(resource_attributes={"env": "prod", "region": "eu-west"})
        siem = OtelSiemIntegration(config=cfg)
        body = siem._build_otlp_request("deploy_audit", [{}])

        attrs = {a["key"]: a["value"]["stringValue"] for a in body["resourceLogs"][0]["resource"]["attributes"]}
        assert attrs.get("env") == "prod"
        assert attrs.get("region") == "eu-west"


class TestOtelSendBatch:
    def test_send_batch_success(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        siem = OtelSiemIntegration(config=cfg)

        with patch.object(siem, "_post_json", return_value=True) as mock_post:
            result = siem.send_batch("deploy_audit", [{"execution_id": "abc"}])

        assert result is True
        mock_post.assert_called_once()
        url_arg = mock_post.call_args[0][0]
        assert url_arg.endswith("/v1/logs")

    def test_send_batch_returns_false_when_no_endpoint(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        cfg.endpoints = None
        siem = OtelSiemIntegration(config=cfg)

        result = siem.send_batch("deploy_audit", [{}])
        assert result is False

    def test_send_batch_returns_false_on_exception(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        siem = OtelSiemIntegration(config=cfg)

        with patch.object(siem, "_post_json", side_effect=Exception("boom")):
            result = siem.send_batch("deploy_audit", [{}])

        assert result is False

    def test_send_event_delegates_to_send_batch(self):
        OtelSiemIntegration._instances.clear()
        cfg = _make_otel_config()
        siem = OtelSiemIntegration(config=cfg)

        with patch.object(siem, "send_batch", return_value=True) as mock_batch:
            result = siem.send_event("deploy_audit", {"k": "v"})

        assert result is True
        mock_batch.assert_called_once_with("deploy_audit", [{"k": "v"}])
