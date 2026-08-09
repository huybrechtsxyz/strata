#!/usr/bin/env python3
"""Unit tests for `sln doctor`'s identity-integration auth checks and --login flag (ADR-0067)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_sln import sln_group
from strata.commands.sln.doctor_sln_command import DoctorSlnCommand


class TestLoginFlagPlumbing:
    def test_login_flag_implies_deep_and_is_passed_through(self, tmp_path):
        runner = CliRunner()
        with (
            patch.object(DoctorSlnCommand, "__init__", return_value=None) as mock_init,
            patch.object(DoctorSlnCommand, "execute", return_value=True),
        ):
            runner.invoke(sln_group, ["doctor", "--login", "--work-path", str(tmp_path)])

        _, kwargs = mock_init.call_args
        assert kwargs["login"] is True
        assert kwargs["deep"] is True

    def test_deep_without_login_does_not_set_login(self, tmp_path):
        runner = CliRunner()
        with (
            patch.object(DoctorSlnCommand, "__init__", return_value=None) as mock_init,
            patch.object(DoctorSlnCommand, "execute", return_value=True),
        ):
            runner.invoke(sln_group, ["doctor", "--deep", "--work-path", str(tmp_path)])

        _, kwargs = mock_init.call_args
        assert kwargs["login"] is False
        assert kwargs["deep"] is True


def _command(login: bool = False) -> DoctorSlnCommand:
    return DoctorSlnCommand(work_path=".", deep=True, login=login)


class TestCheckIdentityIntegrations:
    def _patch_workspace(self, integration):
        profile = MagicMock()
        profile.configfile_paths = []
        sol = MagicMock()
        sol.get_active_profile.return_value = (profile, None)

        svc = MagicMock()
        svc.is_initialized.return_value = True
        svc.get_integrations_with_capability.return_value = ["idp"] if integration else []
        svc.get_integration.return_value = integration

        return sol, svc

    def test_no_active_profile_returns_no_results(self):
        cmd = _command()
        sol = MagicMock()
        sol.get_active_profile.return_value = (None, None)
        with patch("strata.controllers.solution_controller.SolutionController", return_value=sol):
            results = cmd._check_identity_integrations()
        assert results == []

    def test_reports_pass_for_authenticated_integration(self):
        cmd = _command()
        integration = MagicMock()
        integration.check_auth.return_value = (True, "Authenticated as dev@example.com")
        sol, svc = self._patch_workspace(integration)

        with (
            patch("strata.controllers.solution_controller.SolutionController", return_value=sol),
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
        ):
            results = cmd._check_identity_integrations()

        assert len(results) == 1
        assert results[0].status == "pass"
        assert results[0].name == "auth_identity_idp"
        integration.login.assert_not_called()

    def test_login_flag_triggers_login_on_failure(self):
        cmd = _command(login=True)
        integration = MagicMock()
        integration.check_auth.return_value = (False, "Not logged in.")
        integration.login.return_value = (True, "Logged in as dev@example.com")
        sol, svc = self._patch_workspace(integration)

        with (
            patch("strata.controllers.solution_controller.SolutionController", return_value=sol),
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
        ):
            results = cmd._check_identity_integrations()

        integration.login.assert_called_once()
        assert results[0].status == "pass"

    def test_without_login_flag_reports_fail_and_fix_hint(self):
        cmd = _command(login=False)
        integration = MagicMock()
        integration.check_auth.return_value = (False, "Not logged in.")
        sol, svc = self._patch_workspace(integration)

        with (
            patch("strata.controllers.solution_controller.SolutionController", return_value=sol),
            patch("strata.services.integration_service.IntegrationService.get_instance", return_value=svc),
        ):
            results = cmd._check_identity_integrations()

        integration.login.assert_not_called()
        assert results[0].status == "fail"
        assert "--login" in results[0].fix_hint

    def test_exception_is_swallowed_and_returns_empty(self):
        cmd = _command()
        with patch("strata.controllers.solution_controller.SolutionController", side_effect=RuntimeError("boom")):
            results = cmd._check_identity_integrations()
        assert results == []
