"""Tests for the `sln deployment` command group (add, remove, list, scan)."""

import json
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from strata.commands.cli_sln import sln_group

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path: Path) -> Path:
    """Create a minimal initialized workspace with solution.json."""
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
    return tmp_path


def _write_deployment_yaml(path: Path, name: str = "my_deploy") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"apiVersion: strata.huybrechts.xyz/v1\nkind: deployment\nmeta:\n  name: {name}\nspec:\n  workspace:\n    name: ws\n    file: workspace.yaml\n  layers: {{}}\n  environments: []\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# sln deployment add
# ---------------------------------------------------------------------------


class TestSlnDeploymentAdd:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(sln_group, ["deployment", "add", "--help"])
        assert result.exit_code == 0

    def test_execute_success(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "add", str(deploy_file), "--work-path", str(work_path)],
        )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.add_deployment_command.AddDeploymentCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                sln_group,
                ["deployment", "add", "deploy/deploy-prd.yaml", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0

    def test_output_json_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.add_deployment_command.AddDeploymentCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                sln_group,
                ["deployment", "add", "deploy/deploy-prd.yaml", "--output", "json", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_file_not_found_fails(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "add", "nonexistent.yaml", "--work-path", str(work_path)],
        )
        assert result.exit_code != 0

    def test_wrong_kind_fails(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        bad_file = work_path / "workspace.yaml"
        bad_file.write_text("apiVersion: strata.huybrechts.xyz/v1\nkind: workspace\nmeta:\n  name: ws\n", encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "add", str(bad_file), "--work-path", str(work_path)],
        )
        assert result.exit_code != 0

    def test_duplicate_add_fails(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        result = runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# sln deployment remove
# ---------------------------------------------------------------------------


class TestSlnDeploymentRemove:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(sln_group, ["deployment", "remove", "--help"])
        assert result.exit_code == 0

    def test_execute_success(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        result = runner.invoke(
            sln_group,
            ["deployment", "remove", "my_deploy", "--work-path", str(work_path)],
        )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.remove_deployment_command.RemoveDeploymentCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                sln_group,
                ["deployment", "remove", "my_deploy", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0

    def test_remove_nonexistent_fails(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "remove", "ghost_deploy", "--work-path", str(work_path)],
        )
        assert result.exit_code != 0

    def test_output_json_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.remove_deployment_command.RemoveDeploymentCommand.execute",
            return_value=True,
        ):
            result = runner.invoke(
                sln_group,
                ["deployment", "remove", "my_deploy", "--output", "json", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# sln deployment list
# ---------------------------------------------------------------------------


class TestSlnDeploymentList:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(sln_group, ["deployment", "list", "--help"])
        assert result.exit_code == 0

    def test_empty_list_exits_zero(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "list", "--work-path", str(work_path)],
        )
        assert result.exit_code == 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.list_deployments_command.ListDeploymentsCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                sln_group,
                ["deployment", "list", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0

    def test_lists_registered_deployment(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        result = runner.invoke(
            sln_group,
            ["deployment", "list", "--work-path", str(work_path)],
        )
        assert result.exit_code == 0
        assert "my_deploy" in result.output

    def test_name_filter_accepted(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        result = runner.invoke(
            sln_group,
            ["deployment", "list", "--name", "my_deploy", "--work-path", str(work_path)],
        )
        assert result.exit_code == 0

    def test_output_json_returns_json(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        result = runner.invoke(
            sln_group,
            ["deployment", "list", "--output", "json", "--work-path", str(work_path)],
        )
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "deployments" in parsed["data"]


# ---------------------------------------------------------------------------
# sln deployment scan
# ---------------------------------------------------------------------------


class TestSlnDeploymentScan:
    def test_help_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(sln_group, ["deployment", "scan", "--help"])
        assert result.exit_code == 0

    def test_scan_empty_dir_exits_zero(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "scan", str(work_path), "--work-path", str(work_path)],
        )
        assert result.exit_code == 0

    def test_scan_finds_and_registers_deployment(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "scan", str(work_path / "deploy"), "--work-path", str(work_path)],
        )
        assert result.exit_code == 0
        # Verify it's now listed
        list_result = runner.invoke(
            sln_group,
            ["deployment", "list", "--output", "json", "--work-path", str(work_path)],
        )
        parsed = json.loads(list_result.output)
        names = [d["name"] for d in parsed["data"]["deployments"]]
        assert "my_deploy" in names

    def test_scan_skips_already_registered(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        deploy_file = _write_deployment_yaml(work_path / "deploy" / "deploy-prd.yaml")
        runner = CliRunner()
        runner.invoke(sln_group, ["deployment", "add", str(deploy_file), "--work-path", str(work_path)])
        # Scanning again should succeed (skip, not error)
        result = runner.invoke(
            sln_group,
            ["deployment", "scan", str(work_path / "deploy"), "--work-path", str(work_path)],
        )
        assert result.exit_code == 0

    def test_scan_nonexistent_dir_fails(self, tmp_path):
        work_path = _make_workspace(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            sln_group,
            ["deployment", "scan", str(tmp_path / "ghost"), "--work-path", str(work_path)],
        )
        assert result.exit_code != 0

    def test_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch(
            "strata.commands.sln.scan_deployments_command.ScanDeploymentsCommand.execute",
            return_value=False,
        ):
            result = runner.invoke(
                sln_group,
                ["deployment", "scan", ".", "--work-path", str(tmp_path)],
            )
        assert result.exit_code != 0
