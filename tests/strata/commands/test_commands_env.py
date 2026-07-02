"""Tests for the ``env`` command group."""

import json
from pathlib import Path
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from strata.commands.cli_env import env_group
from strata.commands.envs.status_env_command import StatusEnvCommand

# ---------------------------------------------------------------------------
# CLI wiring — env status
# ---------------------------------------------------------------------------


class TestEnvStatusCli:
    def test_status_all_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.envs.status_env_command.StatusEnvCommand.execute", return_value=True):
            result = runner.invoke(env_group, ["status", "--all", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_path_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.envs.status_env_command.StatusEnvCommand.execute", return_value=True):
            result = runner.invoke(env_group, ["status", "--path", str(tmp_path), "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_with_file(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.envs.status_env_command.StatusEnvCommand.execute", return_value=True):
            result = runner.invoke(env_group, ["status", "--file", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_offline_flag(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.envs.status_env_command.StatusEnvCommand.execute", return_value=True):
            result = runner.invoke(env_group, ["status", "--offline", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_stage_option(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.envs.status_env_command.StatusEnvCommand.execute", return_value=True):
            result = runner.invoke(env_group, ["status", "--stage", "production", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_status_execute_false_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        with patch("strata.commands.envs.status_env_command.StatusEnvCommand.execute", return_value=False):
            result = runner.invoke(env_group, ["status", "--all", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_status_help(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(env_group, ["status", "--help"])
        assert result.exit_code == 0
        assert "--all" in result.output
        assert "--path" in result.output
        assert "--offline" in result.output
        assert "--stage" in result.output


# ---------------------------------------------------------------------------
# Unit tests — _extract_deployment_status
# ---------------------------------------------------------------------------


class TestExtractDeploymentStatus:
    def _make_cmd(self, tmp_path: Path) -> StatusEnvCommand:
        return StatusEnvCommand(work_path=str(tmp_path), all_deployments=True)

    def test_valid_deployment_yaml(self, tmp_path):
        yaml_file = tmp_path / "deploy.yaml"
        yaml_file.write_text(
            "apiVersion: strata.huybrechts.xyz/v1\n"
            "kind: deployment\n"
            "meta:\n"
            "  name: my_deploy\n"
            "spec:\n"
            "  stages:\n"
            "    - name: infra\n"
            "      provisioner: terraform\n"
            "    - name: services\n"
            "      provisioner: helm\n"
        )
        cmd = self._make_cmd(tmp_path)
        entry = cmd._extract_deployment_status(yaml_file)
        assert entry is not None
        assert entry["name"] == "my_deploy"
        assert entry["stage_count"] == 2
        assert entry["cached_count"] == 0
        assert len(entry["stages"]) == 2
        assert entry["stages"][0]["name"] == "infra"
        assert entry["stages"][1]["name"] == "services"

    def test_non_deployment_yaml_returns_none(self, tmp_path):
        yaml_file = tmp_path / "workspace.yaml"
        yaml_file.write_text("kind: workspace\nmeta:\n  name: ws\n")
        cmd = self._make_cmd(tmp_path)
        assert cmd._extract_deployment_status(yaml_file) is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        yaml_file = tmp_path / "broken.yaml"
        yaml_file.write_text(": invalid: yaml: [\n")
        cmd = self._make_cmd(tmp_path)
        assert cmd._extract_deployment_status(yaml_file) is None

    def test_non_dict_yaml_returns_none(self, tmp_path):
        yaml_file = tmp_path / "list.yaml"
        yaml_file.write_text("- item1\n- item2\n")
        cmd = self._make_cmd(tmp_path)
        assert cmd._extract_deployment_status(yaml_file) is None

    def test_empty_stages_list(self, tmp_path):
        yaml_file = tmp_path / "deploy.yaml"
        yaml_file.write_text("kind: deployment\nmeta:\n  name: empty_deploy\nspec:\n  stages: []\n")
        cmd = self._make_cmd(tmp_path)
        entry = cmd._extract_deployment_status(yaml_file)
        assert entry is not None
        assert entry["stage_count"] == 0
        assert entry["cached_count"] == 0

    def test_cache_detected_when_output_file_present(self, tmp_path):
        yaml_file = tmp_path / "deploy.yaml"
        yaml_file.write_text(
            "kind: deployment\n"
            "meta:\n"
            "  name: cached_deploy\n"
            "spec:\n"
            "  stages:\n"
            "    - name: infra\n"
            "      provisioner: terraform\n"
        )
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "infra.tf-outputs.json").write_text(
            json.dumps(
                {
                    "refreshed_at": "2026-07-01T10:00:00",
                    "outputs": {"endpoint": "https://example.com", "ip": "1.2.3.4"},
                }
            )
        )
        cmd = self._make_cmd(tmp_path)
        entry = cmd._extract_deployment_status(yaml_file)
        assert entry is not None
        assert entry["cached_count"] == 1
        assert entry["stages"][0]["cached"] is True
        assert entry["stages"][0]["cache"]["output_count"] == 2

    def test_partial_cache(self, tmp_path):
        yaml_file = tmp_path / "deploy.yaml"
        yaml_file.write_text(
            "kind: deployment\n"
            "meta:\n"
            "  name: partial_deploy\n"
            "spec:\n"
            "  stages:\n"
            "    - name: infra\n"
            "      provisioner: terraform\n"
            "    - name: services\n"
            "      provisioner: helm\n"
        )
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "infra.tf-outputs.json").write_text(json.dumps({"refreshed_at": "2026-07-01", "outputs": {}}))
        # services has no cache
        cmd = self._make_cmd(tmp_path)
        entry = cmd._extract_deployment_status(yaml_file)
        assert entry is not None
        assert entry["cached_count"] == 1
        assert entry["stages"][0]["cached"] is True
        assert entry["stages"][1]["cached"] is False


# ---------------------------------------------------------------------------
# Unit tests — _read_output_cache_by_name
# ---------------------------------------------------------------------------


class TestReadOutputCacheByName:
    def _make_cmd(self, tmp_path: Path) -> StatusEnvCommand:
        return StatusEnvCommand(work_path=str(tmp_path))

    def test_returns_none_when_file_missing(self, tmp_path):
        cmd = self._make_cmd(tmp_path)
        assert cmd._read_output_cache_by_name("missing") is None

    def test_returns_cache_info(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "infra.tf-outputs.json").write_text(
            json.dumps({"refreshed_at": "2026-07-01T12:00:00", "outputs": {"k": "v"}})
        )
        cmd = self._make_cmd(tmp_path)
        info = cmd._read_output_cache_by_name("infra")
        assert info is not None
        assert info["refreshed_at"] == "2026-07-01T12:00:00"
        assert info["output_count"] == 1
        assert "k" in info["output_keys"]

    def test_returns_none_on_corrupt_json(self, tmp_path):
        build_dir = tmp_path / "build"
        build_dir.mkdir()
        (build_dir / "infra.tf-outputs.json").write_text("not valid json{")
        cmd = self._make_cmd(tmp_path)
        assert cmd._read_output_cache_by_name("infra") is None


# ---------------------------------------------------------------------------
# Unit tests — _run_multi
# ---------------------------------------------------------------------------


class TestRunMulti:
    def _make_deploy_yaml(self, path: Path, name: str, stages: list) -> Path:
        content = {
            "apiVersion": "strata.huybrechts.xyz/v1",
            "kind": "deployment",
            "meta": {"name": name},
            "spec": {"stages": [{"name": s, "provisioner": "terraform"} for s in stages]},
        }
        f = path / f"{name}.yaml"
        f.write_text(yaml.dump(content))
        return f

    def test_run_multi_empty_dir(self, tmp_path):
        cmd = StatusEnvCommand(work_path=str(tmp_path), all_deployments=True)
        ok = cmd._run_multi()
        assert ok is True
        assert cmd._output_data["deployments"] == []

    def test_run_multi_finds_deployments(self, tmp_path):
        self._make_deploy_yaml(tmp_path, "deploy_prd", ["infra", "services"])
        self._make_deploy_yaml(tmp_path, "deploy_stg", ["infra"])
        (tmp_path / "workspace.yaml").write_text("kind: workspace\nmeta:\n  name: ws\n")

        cmd = StatusEnvCommand(work_path=str(tmp_path), all_deployments=True)
        ok = cmd._run_multi()
        assert ok is True
        deployments = cmd._output_data["deployments"]
        assert len(deployments) == 2
        names = {d["name"] for d in deployments}
        assert names == {"deploy_prd", "deploy_stg"}

    def test_run_multi_with_path(self, tmp_path):
        sub = tmp_path / "deploy"
        sub.mkdir()
        self._make_deploy_yaml(sub, "deploy_prd", ["infra"])

        cmd = StatusEnvCommand(work_path=str(tmp_path), path=str(sub))
        ok = cmd._run_multi()
        assert ok is True
        assert len(cmd._output_data["deployments"]) == 1

    def test_run_multi_invalid_path_returns_false(self, tmp_path):
        cmd = StatusEnvCommand(work_path=str(tmp_path), path=str(tmp_path / "nonexistent"))
        ok = cmd._run_multi()
        assert ok is False
        assert any("does not exist" in e for e in cmd._errors)

    def test_run_multi_scans_subdirectories(self, tmp_path):
        sub = tmp_path / "sub" / "deep"
        sub.mkdir(parents=True)
        self._make_deploy_yaml(sub, "nested_deploy", ["infra"])

        cmd = StatusEnvCommand(work_path=str(tmp_path), all_deployments=True)
        ok = cmd._run_multi()
        assert ok is True
        names = [d["name"] for d in cmd._output_data["deployments"]]
        assert "nested_deploy" in names

    def test_run_multi_output_data_has_scan_path(self, tmp_path):
        cmd = StatusEnvCommand(work_path=str(tmp_path), all_deployments=True)
        cmd._run_multi()
        assert "scan_path" in cmd._output_data
        assert str(tmp_path) in cmd._output_data["scan_path"]

    def test_run_multi_get_required_integrations_empty(self, tmp_path):
        cmd = StatusEnvCommand(work_path=str(tmp_path), all_deployments=True)
        assert cmd.get_required_integrations() == {}

    def test_run_multi_offline_get_required_integrations_empty(self, tmp_path):
        cmd = StatusEnvCommand(work_path=str(tmp_path), offline=True)
        assert cmd.get_required_integrations() == {}

    def test_run_single_get_required_integrations_requires_terraform(self, tmp_path):
        cmd = StatusEnvCommand(work_path=str(tmp_path))
        assert "terraform" in cmd.get_required_integrations()
