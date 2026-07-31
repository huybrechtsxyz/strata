"""Tests for the ``put``/``get``/``rotate``/``status``/``list`` secret commands.

These are the deployment-aware secret subcommands that had **zero** test
coverage before this file (see ``_lesson.md`` T1) — only ``generate``/``mask``
(the two stateless utility commands) were exercised by
``test_commands_secret.py`` / ``test_cli_secret.py``.

Strategy: mock ``DeploymentService.load()`` to return a deployment whose
environment exposes a fixed list of ``SecretStoreModel`` items (no real YAML
parsing involved — that pipeline already has its own coverage elsewhere), and
mock ``ValueController._get_integration_by_type`` to return a fake integration
object. This isolates the command logic itself: argument validation, error
messages, output shape, and exit codes.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from strata.commands.cli_secret import secret_group
from strata.models.store_models import (
    SecretGenerateSpec,
    SecretRotatePolicy,
    SecretRotateSpec,
    SecretStoreModel,
    SecretStoreType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _init_workspace(tmp_path):
    """These commands use BaseCommand's default strict _initialize(), which
    requires an initialized workspace (.strata/solution.json) before
    _execute() ever runs — unrelated to the DeploymentService mocking below.
    Auto-applied to every test in this file so failures reflect the command
    logic under test, not a missing workspace.
    """
    strata_dir = tmp_path / ".strata"
    strata_dir.mkdir(parents=True, exist_ok=True)
    solution = {
        "apiVersion": "strata.huybrechts.xyz/v1",
        "kind": "solution",
        "meta": {"name": "test_solution"},
        "spec": {"solution_id": "test-solution-id", "repositories": [], "profiles": []},
    }
    (strata_dir / "solution.json").write_text(json.dumps(solution), encoding="utf-8")


def _secret(
    key="DB_PASSWORD",
    store=SecretStoreType.AZURE_KEYVAULT,
    value="myapp-db-password",
    generate=None,
    rotate=None,
) -> SecretStoreModel:
    return SecretStoreModel(key=key, store=store, value=value, generate=generate, rotate=rotate)


def _mock_deployment_service(secrets) -> MagicMock:
    """A mock DeploymentService whose environment exposes *secrets*."""
    env_svc = MagicMock()
    env_svc.get_secrets.return_value = secrets
    dep_svc = MagicMock()
    dep_svc.is_valid = True
    dep_svc.get_environment_service.return_value = env_svc
    return dep_svc


def _invalid_deployment_service() -> MagicMock:
    dep_svc = MagicMock()
    dep_svc.is_valid = False
    return dep_svc


# ---------------------------------------------------------------------------
# secret list
# ---------------------------------------------------------------------------


class TestSecretList:
    def test_missing_file_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(secret_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_cannot_load_deployment_file_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.secret.list_secret_command.DeploymentService.load",
            return_value=_invalid_deployment_service(),
        ):
            result = runner.invoke(secret_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_empty_secrets_console_message(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.secret.list_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service([]),
        ):
            result = runner.invoke(secret_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "No secrets defined." in result.output

    def test_lists_secret_keys_and_stores(self, tmp_path):
        secrets = [_secret(key="API_KEY", store=SecretStoreType.BITWARDEN, value="ref-1")]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.list_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(secret_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "API_KEY" in result.output
        assert "bitwarden" in result.output

    def test_annotates_generate_and_rotate(self, tmp_path):
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
                rotate=SecretRotateSpec(max_age=90, policy="warn"),
            )
        ]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.list_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(secret_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "generate:password/32" in result.output
        assert "rotate:90d/warn" in result.output

    def test_json_output_structure(self, tmp_path):
        secrets = [_secret(key="API_KEY", store=SecretStoreType.BITWARDEN, value="ref-1")]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.list_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(
                secret_group, ["list", "-f", "deploy.yaml", "--output", "json", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["success"] is True
        assert envelope["command"] == "secret_list"
        data = envelope["data"]
        assert data["count"] == 1
        assert data["secrets"][0]["key"] == "API_KEY"
        assert data["secrets"][0]["store"] == "bitwarden"


# ---------------------------------------------------------------------------
# secret get
# ---------------------------------------------------------------------------


class TestSecretGet:
    def test_missing_file_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(secret_group, ["get", "DB_PASSWORD", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_key_not_found_in_environment(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.secret.get_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service([_secret(key="OTHER_KEY")]),
        ):
            result = runner.invoke(
                secret_group, ["get", "DB_PASSWORD", "-f", "deploy.yaml", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_resolves_and_masks_by_default(self, tmp_path):
        integration = MagicMock()
        integration.get_secret.return_value = "supersecretvalue"
        secrets = [_secret(key="DB_PASSWORD", store=SecretStoreType.AZURE_KEYVAULT, value="myapp-db-password")]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.get_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group, ["get", "DB_PASSWORD", "-f", "deploy.yaml", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0
        assert "supersecretvalue" not in result.output
        assert "*" in result.output

    def test_unmask_shows_full_value(self, tmp_path):
        integration = MagicMock()
        integration.get_secret.return_value = "supersecretvalue"
        secrets = [_secret(key="DB_PASSWORD")]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.get_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                ["get", "DB_PASSWORD", "-f", "deploy.yaml", "--unmask", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "supersecretvalue" in result.output

    def test_not_found_in_store_exits_nonzero(self, tmp_path):
        integration = MagicMock()
        integration.get_secret.return_value = None
        secrets = [_secret(key="DB_PASSWORD", generate=None)]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.get_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group, ["get", "DB_PASSWORD", "-f", "deploy.yaml", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0

    def test_json_output_structure(self, tmp_path):
        integration = MagicMock()
        integration.get_secret.return_value = "supersecretvalue"
        secrets = [_secret(key="DB_PASSWORD", store=SecretStoreType.AZURE_KEYVAULT)]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.get_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                [
                    "get",
                    "DB_PASSWORD",
                    "-f",
                    "deploy.yaml",
                    "--unmask",
                    "--output",
                    "json",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["success"] is True
        assert envelope["command"] == "secret_get"
        data = envelope["data"]
        assert data["key"] == "DB_PASSWORD"
        assert data["store"] == "azure-keyvault"
        assert data["found"] is True
        assert data["value"] == "supersecretvalue"
        assert data["masked"] is False


# ---------------------------------------------------------------------------
# secret put
# ---------------------------------------------------------------------------


class TestSecretPut:
    def test_missing_file_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(secret_group, ["put", "DB_PASSWORD", "--value", "x", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_value_and_generate_mutually_exclusive(self, tmp_path):
        secrets = [_secret(key="DB_PASSWORD", generate=SecretGenerateSpec(type="password", length=32))]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.put_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(
                secret_group,
                ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--value", "x", "--generate", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_neither_value_nor_generate_errors(self, tmp_path):
        secrets = [_secret(key="DB_PASSWORD")]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.put_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(
                secret_group, ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0
        assert "Either --value or --generate is required" in result.output

    def test_key_not_found_in_environment(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.secret.put_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service([_secret(key="OTHER_KEY")]),
        ):
            result = runner.invoke(
                secret_group,
                ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--value", "x", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_explicit_value_written_via_integration(self, tmp_path):
        integration = MagicMock()
        integration.set_secret.return_value = True
        secrets = [_secret(key="DB_PASSWORD", store=SecretStoreType.AZURE_KEYVAULT, value="myapp-db-password")]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.put_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--value", "newvalue", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        integration.set_secret.assert_called_once_with("myapp-db-password", "newvalue")
        assert "written" in result.output.lower()

    def test_generate_without_generate_spec_errors(self, tmp_path):
        secrets = [_secret(key="DB_PASSWORD", generate=None)]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.put_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(
                secret_group, ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--generate", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0
        assert "no generate spec" in result.output

    def test_generate_uses_generate_spec(self, tmp_path):
        integration = MagicMock()
        integration.set_secret.return_value = True
        secrets = [
            _secret(
                key="DB_PASSWORD",
                store=SecretStoreType.AZURE_KEYVAULT,
                value="myapp-db-password",
                generate=SecretGenerateSpec(type="password", length=16),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.put_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group, ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--generate", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0
        assert integration.set_secret.call_count == 1
        written_value = integration.set_secret.call_args[0][1]
        assert len(written_value) == 16

    def test_no_integration_registered_errors(self, tmp_path):
        secrets = [_secret(key="DB_PASSWORD", store=SecretStoreType.AZURE_KEYVAULT)]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.put_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=None,
            ),
        ):
            result = runner.invoke(
                secret_group,
                ["put", "DB_PASSWORD", "-f", "deploy.yaml", "--value", "x", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "No integration registered" in result.output

    def test_json_output_structure(self, tmp_path):
        integration = MagicMock()
        integration.set_secret.return_value = True
        secrets = [_secret(key="DB_PASSWORD", store=SecretStoreType.AZURE_KEYVAULT, value="myapp-db-password")]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.put_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                [
                    "put",
                    "DB_PASSWORD",
                    "-f",
                    "deploy.yaml",
                    "--value",
                    "newvalue",
                    "--output",
                    "json",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["success"] is True
        assert envelope["command"] == "secret_put"
        data = envelope["data"]
        assert data["key"] == "DB_PASSWORD"
        assert data["store"] == "azure-keyvault"
        assert data["source"] == "provided"
        assert data["written"] is True


# ---------------------------------------------------------------------------
# secret rotate
# ---------------------------------------------------------------------------


class TestSecretRotate:
    def test_missing_file_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(secret_group, ["rotate", "DB_PASSWORD", "--force", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_key_not_found_in_environment(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.secret.rotate_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service([_secret(key="OTHER_KEY")]),
        ):
            result = runner.invoke(
                secret_group,
                ["rotate", "DB_PASSWORD", "-f", "deploy.yaml", "--force", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "not found" in result.output

    def test_no_generate_spec_errors(self, tmp_path):
        secrets = [_secret(key="DB_PASSWORD", generate=None)]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.rotate_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(
                secret_group,
                ["rotate", "DB_PASSWORD", "-f", "deploy.yaml", "--force", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0
        assert "cannot auto-rotate" in result.output

    def test_force_skips_confirmation_and_rotates(self, tmp_path):
        integration = MagicMock()
        integration.update_secret.return_value = True
        secrets = [
            _secret(
                key="DB_PASSWORD",
                store=SecretStoreType.AZURE_KEYVAULT,
                value="myapp-db-password",
                generate=SecretGenerateSpec(type="password", length=32),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.rotate_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                ["rotate", "DB_PASSWORD", "-f", "deploy.yaml", "--force", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0, result.output
        integration.update_secret.assert_called_once()
        assert integration.update_secret.call_args[0][0] == "myapp-db-password"

    def test_without_force_prompts_for_confirmation(self, tmp_path):
        integration = MagicMock()
        integration.update_secret.return_value = True
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.rotate_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                ["rotate", "DB_PASSWORD", "-f", "deploy.yaml", "--work-path", str(tmp_path)],
                input="y\n",
            )
        assert result.exit_code == 0, result.output
        assert "Rotate secret" in result.output
        integration.update_secret.assert_called_once()

    def test_declining_confirmation_aborts(self, tmp_path):
        integration = MagicMock()
        secrets = [_secret(key="DB_PASSWORD", generate=SecretGenerateSpec(type="password", length=32))]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.rotate_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                ["rotate", "DB_PASSWORD", "-f", "deploy.yaml", "--work-path", str(tmp_path)],
                input="n\n",
            )
        assert result.exit_code != 0
        integration.update_secret.assert_not_called()

    def test_json_output_structure(self, tmp_path):
        integration = MagicMock()
        integration.update_secret.return_value = True
        secrets = [
            _secret(
                key="DB_PASSWORD",
                store=SecretStoreType.AZURE_KEYVAULT,
                value="myapp-db-password",
                generate=SecretGenerateSpec(type="password", length=32),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.rotate_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group,
                [
                    "rotate",
                    "DB_PASSWORD",
                    "-f",
                    "deploy.yaml",
                    "--force",
                    "--output",
                    "json",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        assert envelope["success"] is True
        assert envelope["command"] == "secret_rotate"
        data = envelope["data"]
        assert data["key"] == "DB_PASSWORD"
        assert data["rotated"] is True
        assert data["generator"] == "password/32"


# ---------------------------------------------------------------------------
# secret status
# ---------------------------------------------------------------------------


class TestSecretStatus:
    def test_missing_file_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(secret_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_no_secrets_with_rotate_spec(self, tmp_path):
        secrets = [_secret(key="STATIC", rotate=None)]
        runner = CliRunner()
        with patch(
            "strata.commands.secret.status_secret_command.DeploymentService.load",
            return_value=_mock_deployment_service(secrets),
        ):
            result = runner.invoke(secret_group, ["status", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "No secrets with rotation policy" in result.output

    def test_ok_secret_not_overdue(self, tmp_path):
        integration = MagicMock()
        meta = MagicMock()
        meta.updated_at = datetime.now(timezone.utc) - timedelta(days=5)
        meta.created_at = None
        integration.get_secret_metadata.return_value = meta
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
                rotate=SecretRotateSpec(max_age=90, policy=SecretRotatePolicy.WARN),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.status_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(secret_group, ["status", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "ok" in result.output

    def test_overdue_secret_exits_3(self, tmp_path):
        integration = MagicMock()
        meta = MagicMock()
        meta.updated_at = datetime.now(timezone.utc) - timedelta(days=100)
        meta.created_at = None
        integration.get_secret_metadata.return_value = meta
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
                rotate=SecretRotateSpec(max_age=90, policy=SecretRotatePolicy.WARN),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.status_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(secret_group, ["status", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 3
        assert "OVERDUE" in result.output

    def test_no_integration_registered(self, tmp_path):
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
                rotate=SecretRotateSpec(max_age=90, policy=SecretRotatePolicy.WARN),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.status_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=None,
            ),
        ):
            result = runner.invoke(secret_group, ["status", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "no_integration" in result.output

    def test_no_metadata_available(self, tmp_path):
        integration = MagicMock()
        integration.get_secret_metadata.return_value = None
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
                rotate=SecretRotateSpec(max_age=90, policy=SecretRotatePolicy.WARN),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.status_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(secret_group, ["status", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "no_metadata" in result.output

    def test_json_output_structure(self, tmp_path):
        integration = MagicMock()
        meta = MagicMock()
        meta.updated_at = datetime.now(timezone.utc) - timedelta(days=5)
        meta.created_at = None
        integration.get_secret_metadata.return_value = meta
        secrets = [
            _secret(
                key="DB_PASSWORD",
                generate=SecretGenerateSpec(type="password", length=32),
                rotate=SecretRotateSpec(max_age=90, policy=SecretRotatePolicy.WARN),
            )
        ]
        runner = CliRunner()
        with (
            patch(
                "strata.commands.secret.status_secret_command.DeploymentService.load",
                return_value=_mock_deployment_service(secrets),
            ),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=integration,
            ),
        ):
            result = runner.invoke(
                secret_group, ["status", "-f", "deploy.yaml", "--output", "json", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0
        envelope = json.loads(result.output)
        data = envelope["data"]
        assert data["overdue"] == 0
        assert data["secrets"][0]["key"] == "DB_PASSWORD"
        assert data["secrets"][0]["status"] == "ok"
