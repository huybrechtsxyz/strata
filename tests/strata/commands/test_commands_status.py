"""Tests for the `status` command."""

import json
from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_status import status_command


class TestStatusCommand:
    def test_basic_invocation_mocked(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_json_output_flag_accepted(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=False):
            result = runner.invoke(status_command, ["--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_json_output_contains_readiness_block(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "data" in data
        assert "readiness" in data["data"]
        readiness = data["data"]["readiness"]
        assert "phases_complete" in readiness
        assert "phases_total" in readiness
        assert "complete" in readiness
        assert "checklist" in readiness
        assert "next_step" in readiness

    def test_readiness_checklist_has_phases(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        checklist = data["data"]["readiness"]["checklist"]
        assert isinstance(checklist, list)
        assert len(checklist) > 0
        for item in checklist:
            assert "phase" in item
            assert "label" in item
            assert "status" in item


class TestSlnStatusCommand:
    def test_sln_status_basic(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_sln_status_json_output_flag_accepted(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=True):
            result = runner.invoke(sln_group, ["status", "--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0

    def test_sln_status_execute_false_returns_nonzero(self, tmp_path):
        from strata.commands.cli_sln import sln_group

        runner = CliRunner()
        with patch("strata.commands.status.show_status_command.StatusCommand.execute", return_value=False):
            result = runner.invoke(sln_group, ["status", "--work-path", str(tmp_path)])
        assert result.exit_code != 0


class TestStatusDeployments:
    """`data["deployments"]` — solution-wide registered deployments (not profile-scoped).

    Regression coverage: the VS Code extension's deployment picker/auto-select
    previously read the never-populated `profiles.paths['deployment']` instead
    of this field.
    """

    def test_deployments_empty_when_uninitialized(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["data"]["deployments"] == []

    def test_deployments_lists_registered_deployment(self, tmp_path):
        from strata.commands.cli_sln import sln_group
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
        (state_dir / SOLUTION_FILE).write_text(json.dumps(solution_json), encoding="utf-8")

        deploy_dir = tmp_path / "deploy"
        deploy_dir.mkdir()
        deploy_file = deploy_dir / "deploy-prd.yaml"
        deploy_file.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\nmeta:\n  name: my_deploy\n"
            "spec:\n  workspace:\n    name: ws\n    file: workspace.yaml\n  layers: {}\n  environments: []\n",
            encoding="utf-8",
        )

        runner = CliRunner()
        add_result = runner.invoke(
            sln_group,
            ["deployment", "add", str(deploy_file), "--work-path", str(tmp_path)],
        )
        assert add_result.exit_code == 0, add_result.output

        result = runner.invoke(status_command, ["--work-path", str(tmp_path), "--output", "json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        deployments = data["data"]["deployments"]
        assert len(deployments) == 1
        assert deployments[0]["name"] == "my_deploy"
