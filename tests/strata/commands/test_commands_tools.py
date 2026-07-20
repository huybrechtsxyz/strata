"""Tests for the ``tools`` command group."""

from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_tools import tools_group

_SAMPLE_ROWS = [
    {
        "name": "git",
        "available": True,
        "version": "2.40.0",
        "capabilities": ["IRepositoryTool"],
        "command": "git",
        "requirement": None,
    },
    {
        "name": "docker",
        "available": False,
        "version": None,
        "capabilities": ["IContainerTool"],
        "command": "docker",
        "requirement": None,
    },
    {
        "name": "terraform",
        "available": True,
        "version": "1.9.2",
        "capabilities": ["IInfrastructureTool"],
        "command": "terraform",
        "requirement": None,
    },
    {
        "name": "cve_scanner",
        "available": False,
        "version": None,
        "capabilities": ["ICveScanner"],
        "command": "trivy",
        "requirement": None,
    },
]

_SAMPLE_ROWS_WITH_DEPLOYMENT = [
    {
        "name": "git",
        "available": True,
        "version": "2.40.0",
        "capabilities": ["IRepositoryTool"],
        "command": "git",
        "requirement": None,
    },
    {
        "name": "terraform",
        "available": True,
        "version": "1.9.2",
        "capabilities": ["IInfrastructureTool"],
        "command": "terraform",
        "requirement": "required",
    },
    {
        "name": "bitwarden",
        "available": False,
        "version": None,
        "capabilities": ["ISecretStore"],
        "command": "bws",
        "requirement": "required",
    },
    {
        "name": "hashicorp_vault",
        "available": False,
        "version": None,
        "capabilities": [],
        "command": "vault",
        "requirement": "optional",
    },
]

_SAMPLE_DETAIL = {
    "name": "git",
    "command": "git",
    "install_url": "https://git-scm.com/downloads",
    "env_vars": [],
    "auth_methods": [
        {"method": "SSH keys", "description": "Add SSH key to remote."},
    ],
    "yaml_example": None,
    "available": True,
    "version": "2.40.0",
    "capabilities": ["IRepositoryTool"],
}


class TestToolsStatus:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["status", "--help"])
        assert result.exit_code == 0

    def test_status_exit_0(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_shows_integration_names(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert "git" in result.output
        assert "docker" in result.output
        assert "terraform" in result.output

    def test_status_shows_availability_icons(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert "✓" in result.output
        assert "✗" in result.output

    def test_status_empty_rows(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, [], [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_filter_available_shows_only_available(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--available", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "git" in result.output
        assert "terraform" in result.output
        assert "docker" not in result.output

    def test_filter_missing_shows_only_unavailable(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--missing", "--work-path", str(tmp_path)])
        assert result.exit_code == 0  # no required integrations → exit 0
        assert "docker" in result.output
        assert "git" not in result.output

    def test_filter_missing_required_exits_3(self, tmp_path):
        """--missing with a required unavailable integration → exit 3."""
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS_WITH_DEPLOYMENT, [])
            result = runner.invoke(
                tools_group,
                ["status", "--file", "deploy.yaml", "--missing", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 3

    def test_filter_required_shows_only_required(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS_WITH_DEPLOYMENT, [])
            result = runner.invoke(
                tools_group,
                ["status", "--file", "deploy.yaml", "--required", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "terraform" in result.output
        assert "bitwarden" in result.output
        assert "git" not in result.output
        assert "hashicorp_vault" not in result.output

    def test_file_mode_shows_requirement_column(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS_WITH_DEPLOYMENT, [])
            result = runner.invoke(
                tools_group,
                ["status", "--file", "deploy.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "Requirement" in result.output
        assert "required" in result.output
        assert "optional" in result.output
        # git has requirement=None — filtered out when --file is given
        assert "git" not in result.output

    def test_required_without_file_warns(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(
                tools_group,
                ["status", "--required", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "--required/--optional require --file" in result.output

    def test_optional_without_file_warns(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(
                tools_group,
                ["status", "--optional", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "--required/--optional require --file" in result.output

    def test_cve_scanner_shown_in_status(self, tmp_path):
        """cve_scanner must appear in tools status output with ICveScanner capability."""
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS, [])
            result = runner.invoke(tools_group, ["status", "--work-path", str(tmp_path)])
        assert "cve_scanner" in result.output

    def test_required_and_optional_combined(self, tmp_path):
        """--required --optional together shows both required and optional integrations."""
        runner = CliRunner()
        with patch("strata.commands.tools.status_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.status.return_value = (True, _SAMPLE_ROWS_WITH_DEPLOYMENT, [])
            result = runner.invoke(
                tools_group,
                ["status", "--file", "deploy.yaml", "--required", "--optional", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        assert "terraform" in result.output
        assert "bitwarden" in result.output
        assert "hashicorp_vault" in result.output
        assert "git" not in result.output


class TestToolsCheck:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["check", "--help"])
        assert result.exit_code == 0
        assert "NAME" in result.output

    def test_check_known_integration_exit_0(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (True, _SAMPLE_DETAIL, [])
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_check_shows_integration_name(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (True, _SAMPLE_DETAIL, [])
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert "git" in result.output

    def test_check_shows_install_url(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (True, _SAMPLE_DETAIL, [])
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert "git-scm.com" in result.output

    def test_check_unavailable_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (
                False,
                {**_SAMPLE_DETAIL, "available": False, "version": None},
                ["Integration 'git' is not available."],
            )
            result = runner.invoke(tools_group, ["check", "git", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_check_unknown_integration_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.check_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.check.return_value = (
                False,
                {},
                ["Unknown integration: 'STRATA_unknown'. Known: git, terraform"],
            )
            result = runner.invoke(tools_group, ["check", "STRATA_unknown", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_check_missing_name_arg_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["check"])
        assert result.exit_code == 2


_SAMPLE_INSTALL_INFO = {
    "name": "terraform",
    "command": "terraform",
    "install_url": "https://developer.hashicorp.com/terraform/install",
    "env_vars": [
        {
            "name": "TERRAFORM_API_TOKEN",
            "purpose": "API token for Terraform Cloud authentication",
            "required": False,
        },
    ],
    "auth_methods": [
        {"method": "Environment variable", "description": "Set TERRAFORM_API_TOKEN."},
    ],
    "yaml_example": "type: terraform\nspec:\n  source: path/to/module",
}


class TestToolsInstall:
    def test_help(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["install", "--help"])
        assert result.exit_code == 0
        assert "NAME" in result.output

    def test_install_shows_download_url(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.install_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.install_info.return_value = (True, _SAMPLE_INSTALL_INFO, [])
            result = runner.invoke(tools_group, ["install", "terraform", "--work-path", str(tmp_path)])
        assert result.exit_code == 0
        assert "developer.hashicorp.com/terraform/install" in result.output

    def test_install_shows_env_vars(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.install_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.install_info.return_value = (True, _SAMPLE_INSTALL_INFO, [])
            result = runner.invoke(tools_group, ["install", "terraform", "--work-path", str(tmp_path)])
        assert "TERRAFORM_API_TOKEN" in result.output

    def test_install_shows_auth_methods(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.install_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.install_info.return_value = (True, _SAMPLE_INSTALL_INFO, [])
            result = runner.invoke(tools_group, ["install", "terraform", "--work-path", str(tmp_path)])
        assert "Environment variable" in result.output

    def test_install_env_file_written(self, tmp_path):
        env_path = str(tmp_path / "terraform.env")
        runner = CliRunner()
        with patch("strata.commands.tools.install_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.install_info.return_value = (True, _SAMPLE_INSTALL_INFO, [])
            result = runner.invoke(
                tools_group,
                ["install", "terraform", "--env-file", env_path, "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0
        content = open(env_path).read()
        assert "TERRAFORM_API_TOKEN" in content
        assert "developer.hashicorp.com/terraform/install" in content

    def test_install_env_file_is_commented(self, tmp_path):
        env_path = str(tmp_path / "terraform.env")
        runner = CliRunner()
        with patch("strata.commands.tools.install_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.install_info.return_value = (True, _SAMPLE_INSTALL_INFO, [])
            runner.invoke(
                tools_group,
                ["install", "terraform", "--env-file", env_path, "--work-path", str(tmp_path)],
            )
        content = open(env_path).read()
        # Every non-blank line must be a comment (no executable assignments)
        for line in content.splitlines():
            if line.strip():
                assert line.startswith("#"), f"Unexpected non-comment line: {line!r}"

    def test_install_unknown_integration_exits_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.tools.install_tools_command.ToolsController") as mock_ctrl:
            mock_ctrl.return_value.install_info.return_value = (
                False,
                {},
                ["Unknown integration: 'no-such-tool'. Known: git, terraform"],
            )
            result = runner.invoke(tools_group, ["install", "no-such-tool", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_install_missing_name_arg_exits_2(self):
        runner = CliRunner()
        result = runner.invoke(tools_group, ["install"])
        assert result.exit_code == 2


class TestToolsStatusProvisionerPlugins:
    """Tests for provisioner plugin rows in strata tools status."""

    def setup_method(self):
        from strata.deployers.factory import DeployerFactory

        DeployerFactory.reset()

    def teardown_method(self):
        from strata.deployers.factory import DeployerFactory

        DeployerFactory.reset()

    def test_provisioner_plugin_row_available(self, tmp_path):
        """Plugin with all required binaries present shows available=True."""
        import shutil

        from strata.controllers.tools_controller import ToolsController
        from strata.deployers.factory import DeployerFactory
        from strata.models.provisioner_manifest_model import ProvisionerManifestModel

        manifest = ProvisionerManifestModel(name="myplugin", version="1.0.0", requires=["python"])
        DeployerFactory._manifests["myplugin"] = manifest

        ctrl = ToolsController()
        rows = ctrl._provisioner_plugin_rows()

        assert len(rows) == 1
        row = rows[0]
        assert row["name"] == "provisioner:myplugin"
        assert row["version"] == "1.0.0"
        # python is always on PATH in test env
        assert row["available"] is (shutil.which("python") is not None)

    def test_provisioner_plugin_row_missing_binary(self, tmp_path):
        """Plugin with missing required binary shows available=False and lists missing."""
        from strata.controllers.tools_controller import ToolsController
        from strata.deployers.factory import DeployerFactory
        from strata.models.provisioner_manifest_model import ProvisionerManifestModel

        manifest = ProvisionerManifestModel(
            name="ghost_tool",
            version="0.1.0",
            requires=["strata_nonexistent_binary_xyz"],
        )
        DeployerFactory._manifests["ghost_tool"] = manifest

        ctrl = ToolsController()
        rows = ctrl._provisioner_plugin_rows()

        assert len(rows) == 1
        row = rows[0]
        assert row["available"] is False
        assert "strata_nonexistent_binary_xyz" in row["missing_binaries"]

    def test_no_manifests_returns_empty(self, tmp_path):
        """No manifests loaded → _provisioner_plugin_rows returns []."""
        from strata.controllers.tools_controller import ToolsController

        ctrl = ToolsController()
        assert ctrl._provisioner_plugin_rows() == []

    def test_status_includes_plugin_rows(self, tmp_path):
        """ToolsController.status() includes provisioner plugin rows."""
        from strata.deployers.factory import DeployerFactory
        from strata.models.provisioner_manifest_model import ProvisionerManifestModel

        manifest = ProvisionerManifestModel(name="pulumi", version="3.0.0", requires=[])
        DeployerFactory._manifests["pulumi"] = manifest

        with patch("strata.controllers.tools_controller.IntegrationFactory") as mock_factory:
            mock_factory.get_known_types.return_value = []
            from strata.controllers.tools_controller import ToolsController

            ctrl = ToolsController()
            _, rows, _ = ctrl.status()

        names = [r["name"] for r in rows]
        assert "provisioner:pulumi" in names
