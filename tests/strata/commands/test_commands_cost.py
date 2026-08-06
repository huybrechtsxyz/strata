#!/usr/bin/env python3
"""Tests for the `cost` command group (show / diff)."""

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from strata.commands.cli_cost import cost_group

# ---------------------------------------------------------------------------
# cost show
# ---------------------------------------------------------------------------


class TestCostShow:
    _SHOW_EXECUTE = "strata.commands.cost.show_cost_command.ShowCostCommand.execute"

    def test_missing_file_returns_nonzero(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cost_group, ["show", "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_file_option_accepted_success(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=True):
            result = runner.invoke(cost_group, ["show", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 0

    def test_file_env_var_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                ["show", "--work-path", str(tmp_path)],
                env={"STRATA_FILE": "deploy.yaml"},
            )
        assert result.exit_code == 0

    def test_execute_false_returns_exit_1(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=False):
            result = runner.invoke(cost_group, ["show", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 1

    def test_currency_option_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                ["show", "-f", "deploy.yaml", "--currency", "EUR", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_provisioner_option_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                ["show", "-f", "deploy.yaml", "--provisioner", "infra", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_output_json_accepted(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                ["show", "-f", "deploy.yaml", "--output", "json", "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 0

    def test_invalid_output_format_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(
            cost_group,
            ["show", "-f", "deploy.yaml", "--output", "badformat", "--work-path", str(tmp_path)],
        )
        assert result.exit_code == 2

    def test_currency_and_provisioner_together(self, tmp_path):
        runner = CliRunner()
        with patch(self._SHOW_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                [
                    "show",
                    "-f",
                    "deploy.yaml",
                    "--currency",
                    "GBP",
                    "--provisioner",
                    "terraform",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_command_passed_correct_params(self, tmp_path):
        """Verify ShowCostCommand receives the correct constructor arguments."""
        from strata.commands.cost.show_cost_command import ShowCostCommand

        captured = {}

        def _fake_init(self, **kwargs):
            captured.update(kwargs)
            # Minimal setup to avoid AttributeError
            self._currency = kwargs.get("currency")
            self._provisioner_filter = kwargs.get("provisioner")

        with (
            patch.object(ShowCostCommand, "__init__", _fake_init),
            patch.object(ShowCostCommand, "execute", return_value=True),
        ):
            runner = CliRunner()
            runner.invoke(
                cost_group,
                [
                    "show",
                    "-f",
                    "deploy.yaml",
                    "--currency",
                    "EUR",
                    "--provisioner",
                    "infra",
                    "--work-path",
                    str(tmp_path),
                ],
            )

        assert captured.get("currency") == "EUR"
        assert captured.get("provisioner") == "infra"


# ---------------------------------------------------------------------------
# cost diff
# ---------------------------------------------------------------------------


class TestCostDiff:
    _DIFF_EXECUTE = "strata.commands.cost.diff_cost_command.DiffCostCommand.execute"

    def test_missing_file_returns_nonzero(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        runner = CliRunner()
        result = runner.invoke(cost_group, ["diff", "--plan-file", str(plan), "--work-path", str(tmp_path)])
        assert result.exit_code != 0

    def test_missing_plan_file_option_returns_exit_2(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(cost_group, ["diff", "-f", "deploy.yaml", "--work-path", str(tmp_path)])
        assert result.exit_code == 2

    def test_both_required_options_accepted(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        runner = CliRunner()
        with patch(self._DIFF_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                [
                    "diff",
                    "-f",
                    "deploy.yaml",
                    "--plan-file",
                    str(plan),
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_execute_false_returns_exit_1(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        runner = CliRunner()
        with patch(self._DIFF_EXECUTE, return_value=False):
            result = runner.invoke(
                cost_group,
                ["diff", "-f", "deploy.yaml", "--plan-file", str(plan), "--work-path", str(tmp_path)],
            )
        assert result.exit_code == 1

    def test_currency_option_accepted(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        runner = CliRunner()
        with patch(self._DIFF_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                [
                    "diff",
                    "-f",
                    "deploy.yaml",
                    "--plan-file",
                    str(plan),
                    "--currency",
                    "EUR",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_provisioner_option_accepted(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        runner = CliRunner()
        with patch(self._DIFF_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                [
                    "diff",
                    "-f",
                    "deploy.yaml",
                    "--plan-file",
                    str(plan),
                    "--provisioner",
                    "terraform",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_output_json_accepted(self, tmp_path):
        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        runner = CliRunner()
        with patch(self._DIFF_EXECUTE, return_value=True):
            result = runner.invoke(
                cost_group,
                [
                    "diff",
                    "-f",
                    "deploy.yaml",
                    "--plan-file",
                    str(plan),
                    "--output",
                    "json",
                    "--work-path",
                    str(tmp_path),
                ],
            )
        assert result.exit_code == 0

    def test_command_passed_correct_params(self, tmp_path):
        """Verify DiffCostCommand receives the correct constructor arguments."""
        from strata.commands.cost.diff_cost_command import DiffCostCommand

        plan = tmp_path / "plan.json"
        plan.write_text("{}")
        captured = {}

        def _fake_init(self, **kwargs):
            captured.update(kwargs)
            self._plan_file = kwargs.get("plan_file")
            self._currency = kwargs.get("currency")
            self._provisioner_filter = kwargs.get("provisioner")

        with (
            patch.object(DiffCostCommand, "__init__", _fake_init),
            patch.object(DiffCostCommand, "execute", return_value=True),
        ):
            runner = CliRunner()
            runner.invoke(
                cost_group,
                [
                    "diff",
                    "-f",
                    "deploy.yaml",
                    "--plan-file",
                    str(plan),
                    "--currency",
                    "EUR",
                    "--provisioner",
                    "infra",
                    "--work-path",
                    str(tmp_path),
                ],
            )

        assert captured.get("plan_file") == str(plan)
        assert captured.get("currency") == "EUR"
        assert captured.get("provisioner") == "infra"


# ---------------------------------------------------------------------------
# cost diff — internal wiring (regression: work_path must reach CostController)
# ---------------------------------------------------------------------------


class TestDiffCostCommandControllerWiring:
    def test_diff_passes_work_path_to_cost_controller(self, tmp_path):
        """Regression test: DiffCostCommand previously constructed CostController()
        without work_path, unlike ShowCostCommand — harmless today (diff() doesn't
        use cache/history) but inconsistent and a latent bug if that ever changes."""
        from strata.commands.cost.diff_cost_command import DiffCostCommand
        from strata.commands.deploy.base_deploy_command import BaseDeployCommand

        with patch.object(BaseDeployCommand, "_initialize", return_value=None):
            cmd = DiffCostCommand(work_path=str(tmp_path), file="deploy.yaml", plan_file="plan.json")
        cmd._work_path = tmp_path
        cmd._deployment_service = MagicMock()
        cmd._build_path = tmp_path / "build"
        cmd._solution_controller = MagicMock()

        mock_instance = MagicMock()
        mock_instance.diff.return_value = (True, {})
        mock_instance.get_messages.return_value = []
        mock_instance.get_errors.return_value = []

        with patch(
            "strata.commands.cost.diff_cost_command.CostController", return_value=mock_instance
        ) as mock_controller_cls:
            cmd._execute()

        mock_controller_cls.assert_called_once_with(work_path=tmp_path)


# ---------------------------------------------------------------------------
# cost group help
# ---------------------------------------------------------------------------


class TestCostGroupHelp:
    def test_group_help_exits_0(self):
        runner = CliRunner()
        result = runner.invoke(cost_group, ["--help"])
        assert result.exit_code == 0
        assert "show" in result.output
        assert "diff" in result.output

    def test_show_help_exits_0(self):
        runner = CliRunner()
        result = runner.invoke(cost_group, ["show", "--help"])
        assert result.exit_code == 0
        assert "--currency" in result.output
        assert "--provisioner" in result.output

    def test_diff_help_exits_0(self):
        runner = CliRunner()
        result = runner.invoke(cost_group, ["diff", "--help"])
        assert result.exit_code == 0
        assert "--plan-file" in result.output
        assert "--currency" in result.output
        assert "--provisioner" in result.output
