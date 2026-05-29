"""Tests for the `sln update` command."""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_sln import sln_group


class TestSlnUpdateCommand:
    """CLI surface tests — option parsing and exit codes."""

    def test_no_options_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.update_solution_command.UpdateSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(sln_group, ["update", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.update_solution_command.UpdateSolutionCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(sln_group, ["update", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_output_json_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.update_solution_command.UpdateSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                sln_group,
                ["update", "--output", "json", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_verbose_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.update_solution_command.UpdateSolutionCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                sln_group,
                ["update", "--verbose", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0


class TestSolutionControllerUpdate:
    """Unit tests for SolutionController.update() and helpers."""

    def _make_initialized_workspace(self, tmp_path: Path) -> Path:
        """Create a minimal initialized workspace for testing."""
        from strata.utils.config import SOLUTION_DIR, SOLUTION_FILE
        from strata.utils.system import generate_uuid

        state_dir = tmp_path / SOLUTION_DIR
        state_dir.mkdir()
        (state_dir / "logs").mkdir()

        solution_json = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "solution",
            "meta": {"name": "test-solution"},
            "spec": {"solution_id": generate_uuid()},
        }
        import json

        (state_dir / SOLUTION_FILE).write_text(json.dumps(solution_json), encoding="utf-8")
        return tmp_path

    def test_update_fails_without_strata_dir(self, tmp_path):
        from strata.controllers.solution_controller import SolutionController

        ctrl = SolutionController(tmp_path)
        ok, errors = ctrl.update()
        assert not ok
        assert any("not initialised" in e.lower() or ".strata" in e for e in errors)

    def test_update_succeeds_on_initialized_workspace(self, tmp_path):
        work_path = self._make_initialized_workspace(tmp_path)

        from strata.controllers.solution_controller import SolutionController

        ctrl = SolutionController(work_path)

        with (
            patch.object(ctrl, "_update_scaffold_files", return_value=(True, [])) as mock_scaffold,
            patch.object(ctrl, "_generate_schemas", return_value=(True, [])) as mock_schemas,
        ):
            ok, errors = ctrl.update()

        assert ok
        assert not errors
        mock_scaffold.assert_called_once()
        mock_schemas.assert_called_once()

    def test_update_propagates_scaffold_failure(self, tmp_path):
        work_path = self._make_initialized_workspace(tmp_path)

        from strata.controllers.solution_controller import SolutionController

        ctrl = SolutionController(work_path)

        with patch.object(ctrl, "_update_scaffold_files", return_value=(False, ["scaffold error"])):
            ok, errors = ctrl.update()

        assert not ok
        assert "scaffold error" in errors

    def test_update_propagates_schema_failure(self, tmp_path):
        work_path = self._make_initialized_workspace(tmp_path)

        from strata.controllers.solution_controller import SolutionController

        ctrl = SolutionController(work_path)

        with (
            patch.object(ctrl, "_update_scaffold_files", return_value=(True, [])),
            patch.object(ctrl, "_generate_schemas", return_value=(False, ["schema error"])),
        ):
            ok, errors = ctrl.update()

        assert not ok
        assert "schema error" in errors


class TestPackageOwnedClassification:
    """Unit tests for the package-owned / user-owned classification logic."""

    def setup_method(self):
        from strata.controllers.solution_controller import SolutionController

        self.ctrl_cls = SolutionController

    def test_package_owned_strata_readme(self):
        assert self.ctrl_cls._is_package_owned(".strata/README.md")

    def test_package_owned_strata_gitignore(self):
        assert self.ctrl_cls._is_package_owned(".strata/.gitignore")

    def test_package_owned_devcontainer(self):
        assert self.ctrl_cls._is_package_owned(".devcontainer/devcontainer.json")
        assert self.ctrl_cls._is_package_owned(".devcontainer/post-create.sh")

    def test_package_owned_github_workflows(self):
        assert self.ctrl_cls._is_package_owned(".github/workflows/deploy.yml")
        assert self.ctrl_cls._is_package_owned(".github/strata.instructions.md")

    def test_package_owned_strata_templates(self):
        assert self.ctrl_cls._is_package_owned(".strata/templates/workspace.yaml")
        assert self.ctrl_cls._is_package_owned(".strata/templates/configuration.yaml")

    def test_package_owned_strata_integrations(self):
        assert self.ctrl_cls._is_package_owned(".strata/integrations/my_integration.py")

    def test_package_owned_root_gitignore(self):
        assert self.ctrl_cls._is_package_owned(".gitignore")

    def test_user_owned_cli_yaml(self):
        assert not self.ctrl_cls._is_package_owned(".strata/cli.yaml")

    def test_user_owned_logging_yaml(self):
        assert not self.ctrl_cls._is_package_owned(".strata/logging.yaml")

    def test_user_owned_vscode(self):
        assert not self.ctrl_cls._is_package_owned(".vscode/settings.json")
        assert not self.ctrl_cls._is_package_owned(".vscode/extensions.json")

    def test_user_owned_root_readme(self):
        assert not self.ctrl_cls._is_package_owned("README.md")

    def test_user_owned_code_workspace(self):
        assert not self.ctrl_cls._is_package_owned("my-solution.code-workspace")


class TestGenerateWorkspaceIdempotent:
    """Verify that generate_workspace skips .code-workspace if it already exists."""

    def test_existing_workspace_file_not_overwritten(self, tmp_path):
        import json

        from strata.controllers.solution_controller import SolutionController
        from strata.utils.config import SOLUTION_DIR, SOLUTION_FILE, SOLUTION_WORKSPACE_SUFFIX
        from strata.utils.system import generate_uuid

        # Set up minimal workspace
        state_dir = tmp_path / SOLUTION_DIR
        state_dir.mkdir()
        solution_json = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "solution",
            "meta": {"name": "my-ws"},
            "spec": {"solution_id": generate_uuid()},
        }
        (state_dir / SOLUTION_FILE).write_text(json.dumps(solution_json), encoding="utf-8")

        # Pre-create the workspace file with custom content
        workspace_path = tmp_path / f"my-ws{SOLUTION_WORKSPACE_SUFFIX}"
        original_content = '{"folders": [{"path": "custom"}], "settings": {"my.key": true}}'
        workspace_path.write_text(original_content, encoding="utf-8")

        ctrl = SolutionController(tmp_path)
        ctrl.load()
        ctrl.generate_workspace()

        # Content must be unchanged
        assert workspace_path.read_text(encoding="utf-8") == original_content

    def test_missing_workspace_file_is_created(self, tmp_path):
        import json

        from strata.controllers.solution_controller import SolutionController
        from strata.utils.config import SOLUTION_DIR, SOLUTION_FILE, SOLUTION_WORKSPACE_SUFFIX
        from strata.utils.system import generate_uuid

        state_dir = tmp_path / SOLUTION_DIR
        state_dir.mkdir()
        solution_json = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "solution",
            "meta": {"name": "new-ws"},
            "spec": {"solution_id": generate_uuid()},
        }
        (state_dir / SOLUTION_FILE).write_text(json.dumps(solution_json), encoding="utf-8")

        ctrl = SolutionController(tmp_path)
        ctrl.load()
        ctrl.generate_workspace()

        workspace_path = tmp_path / f"new-ws{SOLUTION_WORKSPACE_SUFFIX}"
        assert workspace_path.exists()
        data = json.loads(workspace_path.read_text(encoding="utf-8"))
        assert "folders" in data
