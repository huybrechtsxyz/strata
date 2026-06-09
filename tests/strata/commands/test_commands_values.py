"""Tests for the `values` command group (list / get / set / resolve)."""

import os
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_values import values_group


class TestValuesList:
    def test_missing_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["list", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_basic_list_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(values_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_type_filter_variables(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--type", "variables", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_type_filter_secrets(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--type", "secrets", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_type_filter_features(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--type", "features", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_invalid_type_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            values_group, ["list", "-f", "deploy.yaml", "--type", "badtype", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 2

    def test_show_store_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--show-store", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_unresolved_flag(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--unresolved", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["list", "-f", "deploy.yaml", "--stage", "production", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.list_values_deploy_command.ListValuesDeployCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(values_group, ["list", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestValuesGet:
    def test_missing_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["get", "DB_PASSWORD", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_missing_key_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["get", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_single_key_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.get_values_deploy_command.GetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group, ["get", "-f", "deploy.yaml", "DB_PASSWORD", "--work-path", str(tmp_path)]
            )
        assert result.exit_code == 0

    def test_multiple_keys_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.get_values_deploy_command.GetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                ["get", "-f", "deploy.yaml", "DB_PASSWORD", "API_KEY", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.get_values_deploy_command.GetValuesDeployCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                values_group, ["get", "-f", "deploy.yaml", "MISSING_KEY", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0


class TestValuesSet:
    """Tests for `strata values set`."""

    def test_missing_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["set", "--key", "FOO", "--value", "bar", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_missing_key_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            values_group, ["set", "-f", "deploy.yaml", "--value", "bar", "--work-path", str(tmp_path)]
        )
        assert result.exit_code == 2

    def test_no_value_source_fails(self, tmp_path):
        """No --value, --from-file, or --stdin should fail."""
        runner = CliRunner()
        with (
            patch(
                "strata.commands.deploy.set_values_deploy_command.SetValuesDeployCommand._initialize",
                return_value=True,
            ),
            patch(
                "strata.commands.deploy.set_values_deploy_command.SetValuesDeployCommand._before_execute",
                return_value=True,
            ),
        ):
            result = runner.invoke(
                values_group, ["set", "-f", "deploy.yaml", "--key", "FOO", "--work-path", str(tmp_path)]
            )
        assert result.exit_code != 0

    def test_value_flag_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.set_values_deploy_command.SetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                ["set", "-f", "deploy.yaml", "--key", "DB_HOST", "--value", "new-host", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_from_file_flag_mocked(self, tmp_path):
        cert_file = tmp_path / "cert.pem"
        cert_file.write_text("-----BEGIN CERTIFICATE-----\nfoo\n-----END CERTIFICATE-----\n")
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.set_values_deploy_command.SetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                [
                    "set",
                    "-f",
                    "deploy.yaml",
                    "--key",
                    "TLS_CERT",
                    "--from-file",
                    str(cert_file),
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_stdin_flag_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.set_values_deploy_command.SetValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                ["set", "-f", "deploy.yaml", "--key", "SSH_KEY", "--stdin", "--work-path", str(tmp_path)],
                input="ssh-rsa AAAA...\n",
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.set_values_deploy_command.SetValuesDeployCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                values_group,
                ["set", "-f", "deploy.yaml", "--key", "FOO", "--value", "bar", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0


class TestValuesSetCommand:
    """Unit tests for SetValuesDeployCommand internals."""

    def test_resolve_input_value_from_value(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand

        cmd = SetValuesDeployCommand(file="x.yaml", key="K", value="hello")
        result = cmd._resolve_input_value()
        assert result == "hello"

    def test_resolve_input_value_from_file(self, tmp_path):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand

        f = tmp_path / "data.txt"
        f.write_text("multiline\ncontent\n")
        cmd = SetValuesDeployCommand(file="x.yaml", key="K", from_file=str(f))
        result = cmd._resolve_input_value()
        assert result == "multiline\ncontent\n"

    def test_resolve_input_value_missing_file(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand

        cmd = SetValuesDeployCommand(file="x.yaml", key="K", from_file="/nonexistent/path.txt")
        result = cmd._resolve_input_value()
        assert result is None
        assert any("not found" in e.lower() for e in cmd._errors)

    def test_resolve_input_value_multiple_sources_fails(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand

        cmd = SetValuesDeployCommand(file="x.yaml", key="K", value="x", from_stdin=True)
        result = cmd._resolve_input_value()
        assert result is None
        assert any("multiple" in e.lower() for e in cmd._errors)

    def test_resolve_input_value_no_source_fails(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand

        cmd = SetValuesDeployCommand(file="x.yaml", key="K")
        result = cmd._resolve_input_value()
        assert result is None
        assert any("no value" in e.lower() for e in cmd._errors)

    def test_find_item_variable(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="DB_HOST", value="v")
        env_svc = MagicMock()
        env_svc.get_variables.return_value = [
            VariableStoreModel(key="DB_HOST", store=VariableStoreType.CONSTANT, value="old"),
        ]
        env_svc.get_secrets.return_value = []
        env_svc.get_features.return_value = []

        item, item_type = cmd._find_item(env_svc)
        assert item is not None
        assert item.key == "DB_HOST"
        assert item_type == "variable"

    def test_find_item_secret(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="API_KEY", value="v")
        env_svc = MagicMock()
        env_svc.get_variables.return_value = []
        env_svc.get_secrets.return_value = [
            SecretStoreModel(key="API_KEY", store=SecretStoreType.AZURE_KEYVAULT, value="my-secret-ref"),
        ]
        env_svc.get_features.return_value = []

        item, item_type = cmd._find_item(env_svc)
        assert item is not None
        assert item.key == "API_KEY"
        assert item_type == "secret"

    def test_find_item_feature(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import FeatureStoreModel, FeatureStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="DARK_MODE", value="true")
        env_svc = MagicMock()
        env_svc.get_variables.return_value = []
        env_svc.get_secrets.return_value = []
        env_svc.get_features.return_value = [
            FeatureStoreModel(key="DARK_MODE", store=FeatureStoreType.CONSTANT, value=False),
        ]

        item, item_type = cmd._find_item(env_svc)
        assert item is not None
        assert item.key == "DARK_MODE"
        assert item_type == "feature"

    def test_find_item_not_found(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand

        cmd = SetValuesDeployCommand(file="x.yaml", key="MISSING", value="v")
        env_svc = MagicMock()
        env_svc.get_variables.return_value = []
        env_svc.get_secrets.return_value = []
        env_svc.get_features.return_value = []

        item, item_type = cmd._find_item(env_svc)
        assert item is None

    def test_handle_constant_returns_instruction(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="WORKSPACE", value="new")
        cmd._deployment_service = MagicMock()
        env_svc = MagicMock()
        env_svc.path = "/path/to/env.yaml"
        cmd._deployment_service.get_environment_service.return_value = env_svc

        item = VariableStoreModel(key="WORKSPACE", store=VariableStoreType.CONSTANT, value="old")
        ok, msg = cmd._handle_constant(item, "variable")
        assert ok is True
        assert "constant" in msg
        assert "/path/to/env.yaml" in msg

    def test_handle_environment_returns_instruction(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="K", value="v")
        item = VariableStoreModel(key="K", store=VariableStoreType.ENVIRONMENT, value="MY_ENV_VAR")
        ok, msg = cmd._handle_environment(item, "new-value")
        assert ok is True
        assert "MY_ENV_VAR" in msg
        assert "export" in msg

    def test_handle_integration_success(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="SECRET", value="v")
        item = SecretStoreModel(key="SECRET", store=SecretStoreType.AZURE_KEYVAULT, value="my-ref")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (True, None)
        mock_integration.set_secret.return_value = True

        with (
            patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized"),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=mock_integration,
            ),
        ):
            ok, msg = cmd._handle_integration(item, "secret", "new-secret-value")

        assert ok is True
        assert "updated" in msg
        mock_integration.set_secret.assert_called_once_with("my-ref", "new-secret-value")

    def test_handle_integration_no_integration(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="SECRET", value="v")
        item = SecretStoreModel(key="SECRET", store=SecretStoreType.AZURE_KEYVAULT, value="my-ref")

        with (
            patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized"),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=None,
            ),
        ):
            ok, msg = cmd._handle_integration(item, "secret", "value")

        assert ok is False
        assert "no integration" in msg.lower()

    def test_handle_integration_not_available(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="K", value="v")
        item = VariableStoreModel(key="K", store=VariableStoreType.AZURE_APPCONFIG, value="my/key")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (False, "auth failed")

        with (
            patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized"),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=mock_integration,
            ),
        ):
            ok, msg = cmd._handle_integration(item, "variable", "new-val")

        assert ok is False
        assert "not available" in msg

    def test_handle_integration_set_returns_false(self):
        from strata.commands.deploy.set_values_deploy_command import SetValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = SetValuesDeployCommand(file="x.yaml", key="K", value="v")
        item = VariableStoreModel(key="K", store=VariableStoreType.HASHICORP_CONSUL, value="config/db_host")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (True, None)
        mock_integration.set_variable.return_value = False

        with (
            patch("strata.controllers.value_controller.ValueController._ensure_integrations_initialized"),
            patch(
                "strata.controllers.value_controller.ValueController._get_integration_by_type",
                return_value=mock_integration,
            ),
        ):
            ok, msg = cmd._handle_integration(item, "variable", "new-val")

        assert ok is False
        assert "failure" in msg.lower()


class TestValuesResolve:
    """CLI wiring tests for `strata values resolve`."""

    def test_missing_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(values_group, ["resolve", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_basic_resolve_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.resolve_values_deploy_command.ResolveValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(values_group, ["resolve", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_key_filter_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.resolve_values_deploy_command.ResolveValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                ["resolve", "-f", "deploy.yaml", "-k", "DB_HOST", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_probe_flag_mocked(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.resolve_values_deploy_command.ResolveValuesDeployCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                values_group,
                ["resolve", "-f", "deploy.yaml", "--probe", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.deploy.resolve_values_deploy_command.ResolveValuesDeployCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(values_group, ["resolve", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestValuesResolveCommand:
    """Unit tests for ResolveValuesDeployCommand internals."""

    def test_diagnose_constant_always_ok(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml")
        item = VariableStoreModel(key="K", store=VariableStoreType.CONSTANT, value="hello")
        diag = cmd._diagnose_item(item, "variable")
        assert diag["ok"] is True
        assert diag["store"] == "constant"
        assert any(c["check"] == "store" for c in diag["checks"])

    def test_diagnose_env_var_set(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml")
        item = VariableStoreModel(key="K", store=VariableStoreType.ENVIRONMENT, value="PATH")
        diag = cmd._diagnose_item(item, "variable")
        # PATH is always set
        assert diag["ok"] is True
        assert any(c["status"] == "ok" and "set" in c["detail"] for c in diag["checks"])

    def test_diagnose_env_var_not_set(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml")
        item = VariableStoreModel(key="K", store=VariableStoreType.ENVIRONMENT, value="STRATA_TEST_NONEXISTENT_VAR_XYZ")
        diag = cmd._diagnose_item(item, "variable")
        assert diag["ok"] is False
        assert any(c["status"] == "fail" for c in diag["checks"])

    def test_diagnose_github_not_in_ci(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml")
        item = SecretStoreModel(key="TOKEN", store=SecretStoreType.GITHUB, value="MY_TOKEN")
        with patch.dict("os.environ", {}, clear=False):
            # Ensure GITHUB_ACTIONS is not set
            os.environ.pop("GITHUB_ACTIONS", None)
            os.environ.pop("MY_TOKEN", None)
            diag = cmd._diagnose_item(item, "secret")
        assert diag["ok"] is False
        assert any(c["check"] == "context" and c["status"] == "warn" for c in diag["checks"])
        assert any(c["check"] == "env_var" and c["status"] == "fail" for c in diag["checks"])

    def test_diagnose_integration_not_registered(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml")
        item = SecretStoreModel(key="S", store=SecretStoreType.AZURE_KEYVAULT, value="my-ref")

        with patch(
            "strata.controllers.value_controller.ValueController._get_integration_by_type",
            return_value=None,
        ):
            diag = cmd._diagnose_item(item, "secret")

        assert diag["ok"] is False
        assert any(c["check"] == "integration" and c["status"] == "fail" for c in diag["checks"])

    def test_diagnose_integration_not_available(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml")
        item = SecretStoreModel(key="S", store=SecretStoreType.AZURE_KEYVAULT, value="my-ref")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (False, "az CLI not found")

        with patch(
            "strata.controllers.value_controller.ValueController._get_integration_by_type",
            return_value=mock_integration,
        ):
            diag = cmd._diagnose_item(item, "secret")

        assert diag["ok"] is False
        assert any(c["check"] == "available" and c["status"] == "fail" for c in diag["checks"])

    def test_diagnose_integration_available_no_probe(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml", probe=False)
        item = SecretStoreModel(key="S", store=SecretStoreType.AZURE_KEYVAULT, value="my-ref")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (True, "")
        mock_integration.get_info.return_value = {"version": "2.50.0"}

        with patch(
            "strata.controllers.value_controller.ValueController._get_integration_by_type",
            return_value=mock_integration,
        ):
            diag = cmd._diagnose_item(item, "secret")

        assert diag["ok"] is True
        assert not any(c["check"] == "probe" for c in diag["checks"])

    def test_diagnose_integration_with_probe_success(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import SecretStoreModel, SecretStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml", probe=True)
        item = SecretStoreModel(key="S", store=SecretStoreType.AZURE_KEYVAULT, value="my-ref")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (True, "")
        mock_integration.get_info.return_value = {"version": "2.50.0"}
        mock_integration.get_secret.return_value = "some-value"

        with patch(
            "strata.controllers.value_controller.ValueController._get_integration_by_type",
            return_value=mock_integration,
        ):
            diag = cmd._diagnose_item(item, "secret")

        assert diag["ok"] is True
        assert any(c["check"] == "probe" and c["status"] == "ok" for c in diag["checks"])

    def test_diagnose_integration_with_probe_failure(self):
        from strata.commands.deploy.resolve_values_deploy_command import ResolveValuesDeployCommand
        from strata.models.store_models import VariableStoreModel, VariableStoreType

        cmd = ResolveValuesDeployCommand(file="x.yaml", probe=True)
        item = VariableStoreModel(key="V", store=VariableStoreType.HASHICORP_CONSUL, value="config/missing")

        mock_integration = MagicMock()
        mock_integration.ensure_available.return_value = (True, "")
        mock_integration.get_info.return_value = {"version": "1.15.0"}
        mock_integration.get_variable.return_value = None

        with patch(
            "strata.controllers.value_controller.ValueController._get_integration_by_type",
            return_value=mock_integration,
        ):
            diag = cmd._diagnose_item(item, "variable")

        assert diag["ok"] is False
        assert any(c["check"] == "probe" and c["status"] == "fail" for c in diag["checks"])
