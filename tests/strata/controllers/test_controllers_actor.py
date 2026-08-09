#!/usr/bin/env python3
"""Unit tests for the consolidated actor-resolution chain (ADR-0066, ADR-0067)."""

import getpass
from unittest.mock import MagicMock, patch

import pytest

from strata.controllers.actor_controller import (
    _extract_aws_identity,
    _extract_azure_identity,
    _extract_gcloud_identity,
    _resolve_cloud_cli_identity,
    _resolve_control_plane_identity,
    _safe_getpass,
    reset_actor_cache,
    resolve_actor,
)

ACTOR_ENV_VARS = ("CI_ACTOR", "GITHUB_ACTOR", "BUILD_REQUESTEDFOR", "USER", "USERNAME")


@pytest.fixture(autouse=True)
def _clear_actor_env(monkeypatch):
    """Ensure no ambient env var leaks between tests."""
    for var in ACTOR_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _clear_actor_cache():
    """resolve_actor() memoizes per process (ADR-0066 gap 3) — reset between tests so
    an earlier test's resolved identity doesn't leak into a later one."""
    reset_actor_cache()
    yield
    reset_actor_cache()


class TestResolveActorPrecedence:
    def test_control_plane_identity_wins_when_present(self):
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value="idp-user"),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity") as cloud_mock,
        ):
            assert resolve_actor() == "idp-user"
        cloud_mock.assert_not_called()

    def test_falls_back_to_cloud_cli_identity(self):
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value="az-user"),
        ):
            assert resolve_actor() == "az-user"

    def test_falls_back_to_ci_actor_env_var(self, monkeypatch):
        monkeypatch.setenv("CI_ACTOR", "ci-bot")
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
        ):
            assert resolve_actor() == "ci-bot"

    def test_falls_back_to_github_actor_env_var(self, monkeypatch):
        monkeypatch.setenv("GITHUB_ACTOR", "gh-bot")
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
        ):
            assert resolve_actor() == "gh-bot"

    def test_falls_back_to_build_requestedfor_env_var(self, monkeypatch):
        monkeypatch.setenv("BUILD_REQUESTEDFOR", "ado-bot")
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
        ):
            assert resolve_actor() == "ado-bot"

    def test_ci_actor_takes_priority_over_other_ci_vars(self, monkeypatch):
        monkeypatch.setenv("CI_ACTOR", "ci-bot")
        monkeypatch.setenv("GITHUB_ACTOR", "gh-bot")
        monkeypatch.setenv("BUILD_REQUESTEDFOR", "ado-bot")
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
        ):
            assert resolve_actor() == "ci-bot"

    def test_falls_back_to_user_env_var(self, monkeypatch):
        monkeypatch.setenv("USER", "localdev")
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
        ):
            assert resolve_actor() == "localdev"

    def test_falls_back_to_username_env_var_when_user_missing(self, monkeypatch):
        monkeypatch.setenv("USERNAME", "windev")
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
        ):
            assert resolve_actor() == "windev"

    def test_falls_back_to_getpass_when_no_env_vars_set(self):
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
            patch("strata.controllers.actor_controller._safe_getpass", return_value="os-login-user"),
        ):
            assert resolve_actor() == "os-login-user"

    def test_returns_unknown_when_everything_fails(self):
        with (
            patch("strata.controllers.actor_controller._resolve_control_plane_identity", return_value=None),
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity", return_value=None),
            patch("strata.controllers.actor_controller._safe_getpass", return_value=None),
        ):
            assert resolve_actor() == "unknown"


class TestResolveActorCaching:
    """ADR-0066 gap 3: resolve_actor() is memoized per process."""

    def test_second_call_reuses_cached_value_without_re_resolving(self):
        with (
            patch(
                "strata.controllers.actor_controller._resolve_control_plane_identity", return_value="first-user"
            ) as mock_control_plane,
            patch("strata.controllers.actor_controller._resolve_cloud_cli_identity") as mock_cloud_cli,
        ):
            assert resolve_actor() == "first-user"
            assert resolve_actor() == "first-user"

        mock_control_plane.assert_called_once()
        mock_cloud_cli.assert_not_called()

    def test_reset_actor_cache_forces_re_resolution(self):
        with patch(
            "strata.controllers.actor_controller._resolve_control_plane_identity", side_effect=["user-a", "user-b"]
        ) as mock_control_plane:
            assert resolve_actor() == "user-a"
            reset_actor_cache()
            assert resolve_actor() == "user-b"

        assert mock_control_plane.call_count == 2


class TestResolveControlPlaneIdentity:
    def test_returns_identity_from_identity_controller(self):
        mock_controller = MagicMock()
        mock_controller.get_actor_identity.return_value = "control-plane-user"
        with patch("strata.controllers.identity_controller.IdentityController", return_value=mock_controller):
            assert _resolve_control_plane_identity() == "control-plane-user"

    def test_returns_none_when_identity_controller_returns_none(self):
        mock_controller = MagicMock()
        mock_controller.get_actor_identity.return_value = None
        with patch("strata.controllers.identity_controller.IdentityController", return_value=mock_controller):
            assert _resolve_control_plane_identity() is None

    def test_never_raises_when_identity_controller_throws(self):
        with patch("strata.controllers.identity_controller.IdentityController", side_effect=RuntimeError("boom")):
            assert _resolve_control_plane_identity() is None


def _mock_integration_service(capability_map):
    """capability_map: {capability_class: [(name, integration_or_None), ...]}"""
    svc = MagicMock()
    svc.is_initialized.return_value = True

    def get_integrations_with_capability(capability):
        return [name for name, _ in capability_map.get(capability, [])]

    def get_integration(name):
        for entries in capability_map.values():
            for entry_name, integration in entries:
                if entry_name == name:
                    return integration
        return None

    svc.get_integrations_with_capability.side_effect = get_integrations_with_capability
    svc.get_integration.side_effect = get_integration
    return svc


class TestResolveCloudCliIdentity:
    def test_prefers_azure_over_aws_and_gcloud(self):
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        azure = MagicMock()
        azure.ensure_available.return_value = (True, "")
        azure.get_signed_in_user.return_value = {"name": "azure-user"}

        aws = MagicMock()
        aws.ensure_available.return_value = (True, "")

        svc = _mock_integration_service(
            {
                IAzureTool: [("azure_cli", azure)],
                IAWSTool: [("aws_cli", aws)],
                IGCloudTool: [],
            }
        )
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert _resolve_cloud_cli_identity() == "azure-user"
        aws.get_identity.assert_not_called()

    def test_falls_back_to_aws_when_azure_unavailable(self):
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        azure = MagicMock()
        azure.ensure_available.return_value = (False, "not logged in")

        aws = MagicMock()
        aws.ensure_available.return_value = (True, "")
        aws.get_identity.return_value = {"Arn": "arn:aws:sts::123456789012:assumed-role/DevRole/alice"}

        svc = _mock_integration_service(
            {
                IAzureTool: [("azure_cli", azure)],
                IAWSTool: [("aws_cli", aws)],
                IGCloudTool: [],
            }
        )
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert _resolve_cloud_cli_identity() == "alice"

    def test_falls_back_to_gcloud_when_azure_and_aws_unavailable(self):
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        aws = MagicMock()
        aws.ensure_available.return_value = (False, "")

        gcloud = MagicMock()
        gcloud.ensure_available.return_value = (True, "")
        gcloud.get_account.return_value = "gcp-user@example.com"

        svc = _mock_integration_service(
            {
                IAzureTool: [],
                IAWSTool: [("aws_cli", aws)],
                IGCloudTool: [("gcloud_cli", gcloud)],
            }
        )
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert _resolve_cloud_cli_identity() == "gcp-user@example.com"

    def test_returns_none_when_nothing_configured(self):
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        svc = _mock_integration_service({IAzureTool: [], IAWSTool: [], IGCloudTool: []})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert _resolve_cloud_cli_identity() is None

    def test_initializes_integrations_if_not_already(self):
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        svc = _mock_integration_service({IAzureTool: [], IAWSTool: [], IGCloudTool: []})
        svc.is_initialized.return_value = False
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            _resolve_cloud_cli_identity()
        # Called once per capability lookup (azure/aws/gcloud) via the shared resolver helper.
        svc.initialize_integrations.assert_called()

    def test_skips_integration_that_resolves_to_none_by_name(self):
        from strata.models.capabilities import IAWSTool, IAzureTool, IGCloudTool

        svc = _mock_integration_service({IAzureTool: [("azure_cli", None)], IAWSTool: [], IGCloudTool: []})
        with patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc):
            assert _resolve_cloud_cli_identity() is None

    def test_never_raises_when_integration_service_throws(self):
        with patch(
            "strata.services.integration_service.IntegrationService.get_instance",
            side_effect=RuntimeError("boom"),
        ):
            assert _resolve_cloud_cli_identity() is None


class TestExtractAzureIdentity:
    def test_returns_name_from_signed_in_user(self):
        integration = MagicMock()
        integration.get_signed_in_user.return_value = {"name": "alice@example.com"}
        assert _extract_azure_identity(integration) == "alice@example.com"

    def test_returns_none_when_no_signed_in_user(self):
        integration = MagicMock()
        integration.get_signed_in_user.return_value = None
        assert _extract_azure_identity(integration) is None

    def test_returns_none_when_integration_lacks_method(self):
        integration = object()
        assert _extract_azure_identity(integration) is None


class TestExtractAwsIdentity:
    def test_extracts_last_segment_of_arn(self):
        integration = MagicMock()
        integration.get_identity.return_value = {"Arn": "arn:aws:iam::123456789012:user/alice"}
        assert _extract_aws_identity(integration) == "alice"

    def test_returns_whole_arn_when_no_slash(self):
        integration = MagicMock()
        integration.get_identity.return_value = {"Arn": "arn-without-slash"}
        assert _extract_aws_identity(integration) == "arn-without-slash"

    def test_returns_none_when_arn_missing(self):
        integration = MagicMock()
        integration.get_identity.return_value = {}
        assert _extract_aws_identity(integration) is None

    def test_returns_none_when_identity_missing(self):
        integration = MagicMock()
        integration.get_identity.return_value = None
        assert _extract_aws_identity(integration) is None


class TestExtractGcloudIdentity:
    def test_returns_account(self):
        integration = MagicMock()
        integration.get_account.return_value = "gcp-user@example.com"
        assert _extract_gcloud_identity(integration) == "gcp-user@example.com"

    def test_returns_none_when_no_account(self):
        integration = MagicMock()
        integration.get_account.return_value = None
        assert _extract_gcloud_identity(integration) is None


class TestSafeGetpass:
    def test_returns_getuser_result(self):
        with patch.object(getpass, "getuser", return_value="localuser"):
            assert _safe_getpass() == "localuser"

    def test_returns_none_on_exception(self):
        with patch.object(getpass, "getuser", side_effect=OSError("no tty")):
            assert _safe_getpass() is None
