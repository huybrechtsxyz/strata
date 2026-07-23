"""Tests for AzureCLIIntegration — all subprocess calls mocked."""

from unittest.mock import MagicMock, patch

import pytest

try:
    from strata.integrations.azure_cli import AzureCLIIntegration
    from strata.models.integration_model import IntegrationModel

    IMPL_MISSING = False
except ImportError:
    AzureCLIIntegration = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="AzureCLIIntegration not available")


# ===========================================================================
# Helpers
# ===========================================================================

_AZ_ACCOUNT_SHOW = """{
  "id": "aaaaaaaa-0000-0000-0000-000000000001",
  "name": "my-subscription",
  "tenantId": "bbbbbbbb-0000-0000-0000-000000000002",
  "state": "Enabled"
}"""

_AZ_TOKEN = '{"accessToken": "eyJfake_token", "expiresOn": "2026-07-24 00:00:00.000000", "tokenType": "Bearer"}'

_AZ_VERSION = '{"azure-cli": "2.61.0", "azure-cli-core": "2.61.0"}'


def _make() -> AzureCLIIntegration:
    config = IntegrationModel(name="azure", type="azure_cli")
    return AzureCLIIntegration(config)


def _ok(stdout: str = ""):
    r = MagicMock()
    r.returncode = 0
    r.stdout = stdout
    r.stderr = ""
    return r


def _fail(stderr: str = "ERROR"):
    r = MagicMock()
    r.returncode = 1
    r.stdout = ""
    r.stderr = stderr
    return r


# ===========================================================================
# parse_version
# ===========================================================================


class TestParseVersion:
    def test_parses_json_output(self):
        az = _make()
        result = az.parse_version(_AZ_VERSION)
        assert result == "2.61.0"

    def test_fallback_regex(self):
        az = _make()
        result = az.parse_version('"azure-cli": "2.55.0"')
        assert result == "2.55.0"

    def test_invalid_returns_stripped(self):
        az = _make()
        result = az.parse_version("  unknown  ")
        assert result == "unknown"


# ===========================================================================
# ensure_available
# ===========================================================================


class TestEnsureAvailable:
    def test_not_installed(self):
        az = _make()
        with patch.object(az, "is_available", return_value=False):
            ok, msg = az.ensure_available()
        assert not ok
        assert "not installed" in msg.lower()

    def test_installed_not_logged_in(self):
        az = _make()
        with (
            patch.object(az, "is_available", return_value=True),
            patch.object(az, "_run_integration", return_value=_fail("not logged")),
        ):
            ok, msg = az.ensure_available()
        assert not ok
        assert "az login" in msg

    def test_installed_and_authenticated(self):
        az = _make()
        with (
            patch.object(az, "is_available", return_value=True),
            patch.object(az, "_run_integration", return_value=_ok(_AZ_ACCOUNT_SHOW)),
        ):
            ok, msg = az.ensure_available()
        assert ok
        assert msg == ""
        assert "my-subscription" in (az._info or "")

    def test_info_set_on_success(self):
        az = _make()
        with (
            patch.object(az, "is_available", return_value=True),
            patch.object(az, "_run_integration", return_value=_ok(_AZ_ACCOUNT_SHOW)),
        ):
            az.ensure_available()
        assert "my-subscription" in (az._info or "")


# ===========================================================================
# get_subscription
# ===========================================================================


class TestGetSubscription:
    def test_returns_subscription_dict(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok(_AZ_ACCOUNT_SHOW)):
            sub = az.get_subscription()
        assert sub is not None
        assert sub["id"] == "aaaaaaaa-0000-0000-0000-000000000001"
        assert sub["name"] == "my-subscription"
        assert sub["tenantId"] == "bbbbbbbb-0000-0000-0000-000000000002"

    def test_returns_none_when_not_logged_in(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_fail()):
            sub = az.get_subscription()
        assert sub is None

    def test_returns_none_on_exception(self):
        az = _make()
        with patch.object(az, "_run_integration", side_effect=RuntimeError("boom")):
            sub = az.get_subscription()
        assert sub is None

    def test_returns_none_on_empty_output(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok("")):
            sub = az.get_subscription()
        assert sub is None


# ===========================================================================
# get_access_token
# ===========================================================================


class TestGetAccessToken:
    def setup_method(self):
        # Clear class-level token cache before each test
        AzureCLIIntegration._token_cache.clear()

    def test_returns_token(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok(_AZ_TOKEN)):
            token = az.get_access_token()
        assert token == "eyJfake_token"

    def test_returns_none_on_failure(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_fail()):
            token = az.get_access_token()
        assert token is None

    def test_token_cached_second_call_not_subprocess(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok(_AZ_TOKEN)) as mock:
            az.get_access_token("https://management.azure.com")
            az.get_access_token("https://management.azure.com")
        # Only one subprocess call — second hit cache
        assert mock.call_count == 1

    def test_different_resources_cached_separately(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok(_AZ_TOKEN)) as mock:
            az.get_access_token("https://management.azure.com")
            az.get_access_token("https://vault.azure.net")
        assert mock.call_count == 2

    def test_clear_token_cache(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok(_AZ_TOKEN)) as mock:
            az.get_access_token()
            az.clear_token_cache()
            az.get_access_token()
        assert mock.call_count == 2


# ===========================================================================
# bicep_version
# ===========================================================================


class TestBicepVersion:
    def test_returns_version_string(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_ok("Bicep CLI version 0.28.1 (abc123)")):
            ver = az.bicep_version()
        assert ver == "0.28.1"

    def test_returns_none_when_not_installed(self):
        az = _make()
        with patch.object(az, "_run_integration", return_value=_fail()):
            ver = az.bicep_version()
        assert ver is None

    def test_returns_none_on_exception(self):
        az = _make()
        with patch.object(az, "_run_integration", side_effect=RuntimeError("not found")):
            ver = az.bicep_version()
        assert ver is None


# ===========================================================================
# run_az passthrough
# ===========================================================================


class TestRunAz:
    def test_passes_args_to_integration(self):
        az = _make()
        expected = _ok('{"result": "ok"}')
        with patch.object(az, "_run_integration", return_value=expected) as mock:
            result = az.run_az(["deployment", "group", "create", "--resource-group", "my-rg"])
        mock.assert_called_once()
        call_args = mock.call_args[0][0]
        assert "deployment" in call_args
        assert "my-rg" in call_args
        assert result == expected
