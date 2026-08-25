#!/usr/bin/env python3
"""Unit tests for KrokiIntegration."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.kroki import DEFAULT_KROKI_ADDRESS, KrokiIntegration
from strata.models.capabilities import IDiagramRenderer
from strata.models.integration_model import IntegrationEndpointsSpecModel, IntegrationModel


def _cfg(name: str = "kroki", address: str = "") -> IntegrationModel:
    endpoints = IntegrationEndpointsSpecModel(address=address) if address else None
    return IntegrationModel(name=name, type="kroki", capabilities={"diagram_render"}, endpoints=endpoints)


class TestKrokiIntegrationMetadata:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_capabilities_include_diagram_renderer(self):
        assert IDiagramRenderer in KrokiIntegration.CAPABILITIES

    def test_is_subclass_of_idiagram_renderer(self):
        assert issubclass(KrokiIntegration, IDiagramRenderer)

    def test_get_version_command_is_empty(self):
        """HTTP-based — no CLI version command."""
        i = KrokiIntegration(_cfg())
        assert i.get_version_command() == []

    def test_setup_info_has_install_url_and_yaml_example(self):
        i = KrokiIntegration(_cfg())
        info = i.get_setup_info()
        assert "kroki.io" in info["install_url"]
        assert info["command"] is None
        assert "kroki" in info["yaml_example"]


class TestKrokiIntegrationAddressResolution:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_defaults_to_public_instance(self):
        i = KrokiIntegration(_cfg())
        assert i.address == DEFAULT_KROKI_ADDRESS

    def test_declared_endpoint_overrides_default(self):
        i = KrokiIntegration(_cfg(address="https://kroki.example.internal"))
        assert i.address == "https://kroki.example.internal"

    def test_declared_endpoint_strips_trailing_slash(self):
        i = KrokiIntegration(_cfg(address="https://kroki.example.internal/"))
        assert i.address == "https://kroki.example.internal"

    def test_env_var_overrides_default_when_no_declared_endpoint(self, monkeypatch):
        monkeypatch.setenv("STRATA_KROKI_ADDRESS", "https://self-hosted.example.com")
        i = KrokiIntegration(_cfg())
        assert i.address == "https://self-hosted.example.com"

    def test_declared_endpoint_wins_over_env_var(self, monkeypatch):
        monkeypatch.setenv("STRATA_KROKI_ADDRESS", "https://self-hosted.example.com")
        i = KrokiIntegration(_cfg(address="https://kroki.example.internal"))
        assert i.address == "https://kroki.example.internal"


class TestKrokiIntegrationAvailability:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_is_available_true_by_default(self):
        """Always available out of the box — the public instance needs no setup."""
        i = KrokiIntegration(_cfg())
        assert i.is_available() is True

    def test_ensure_available_success(self):
        i = KrokiIntegration(_cfg())
        ok, msg = i.ensure_available()
        assert ok is True
        assert msg == ""


class TestKrokiIntegrationRender:
    def setup_method(self):
        BaseIntegration._instances.clear()

    def test_render_posts_to_expected_url_and_returns_bytes(self):
        i = KrokiIntegration(_cfg())
        response = MagicMock(status_code=200, content=b"<svg>...</svg>")
        with patch("strata.integrations.kroki.requests.post", return_value=response) as mock_post:
            result = i.render("graph TD; A-->B", "mermaid", "svg")

        assert result == b"<svg>...</svg>"
        mock_post.assert_called_once_with(
            f"{DEFAULT_KROKI_ADDRESS}/mermaid/svg",
            json={"diagram_source": "graph TD; A-->B"},
            timeout=30,
        )

    def test_render_raises_on_non_200_response(self):
        i = KrokiIntegration(_cfg())
        response = MagicMock(status_code=400, text="bad diagram source")
        with patch("strata.integrations.kroki.requests.post", return_value=response):
            with pytest.raises(RuntimeError, match="Kroki returned HTTP 400"):
                i.render("not a diagram", "mermaid", "svg")

    def test_render_raises_runtime_error_on_network_failure(self):
        i = KrokiIntegration(_cfg())
        with patch("strata.integrations.kroki.requests.post", side_effect=requests.ConnectionError("boom")):
            with pytest.raises(RuntimeError, match="Failed to reach Kroki"):
                i.render("graph TD; A-->B", "mermaid", "svg")

    def test_render_against_self_hosted_address(self):
        i = KrokiIntegration(_cfg(address="https://kroki.internal.example.com"))
        response = MagicMock(status_code=200, content=b"PNGDATA")
        with patch("strata.integrations.kroki.requests.post", return_value=response) as mock_post:
            result = i.render("graph TD; A-->B", "mermaid", "png")

        assert result == b"PNGDATA"
        assert mock_post.call_args[0][0] == "https://kroki.internal.example.com/mermaid/png"
