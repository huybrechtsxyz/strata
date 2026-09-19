"""Tests for `deploy run`'s AI plan gate (`--ai` / `--strict-ai-review`).

This gate shipped complete but **unreachable**: `cli_deploy.py` accepted `ai` and
`strict_ai_review` as function parameters and forwarded them to the command, but
never declared the corresponding `@click.option`s — so Click never supplied them
and both were permanently `False`/`None`. `docs/help/ai_agent.md` documented the
flags as working. Roughly a hundred lines of deployment-gating logic sat behind a
switch that could not be flipped, and no test touched it.

The flags are now declared. These tests exist because turning safety-critical
logic on without exercising it first would just move the problem: previously it
could not block, now it can block wrongly.

Gating contract:
- `--strict-ai-review` blocks non-interactively; `--force` does **not** override it
- `--ai` alone is advisory: prompt on a TTY, block off one, `--force` overrides
- a missing/broken AI provider is fatal only under `--strict-ai-review`
"""

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from strata.commands.cli_deploy import deploy
from strata.commands.deploy.run_deploy_command import RunDeployCommand, _parse_ai_risk


def _make_cmd(*, ai=False, strict=None, force=False, console=True):
    cmd = RunDeployCommand.__new__(RunDeployCommand)
    cmd._ai = ai
    cmd._strict_ai_review = strict
    cmd._force = force
    cmd._errors = []
    cmd._messages = []
    cmd._work_path = "/w"
    cmd._configuration_service = MagicMock()
    cmd._deployment_service = MagicMock()
    cmd._deployment_service.model.meta.name = "demo"
    cmd._output_format = "console" if console else "json"
    cmd._output_quiet = not console
    return cmd


def _stage(name="core"):
    stage = MagicMock()
    stage.name = name
    return stage


def _integration(risk="low", *, available=True, raises=None):
    integration = MagicMock()
    integration.integration_name = "fake-ai"
    integration.ensure_available.return_value = (available, "" if available else "provider down")
    if raises is not None:
        integration.analyse_plan.side_effect = raises
    else:
        response = MagicMock()
        response.content = f'{{"risk": "{risk}", "summary": "s", "concerns": [], "recommendations": []}}'
        integration.analyse_plan.return_value = response
    return integration


def _run(cmd, integration, *, tty=True):
    with (
        patch("strata.integrations.ai.find_ai_integration", return_value=integration),
        patch("sys.stdin.isatty", return_value=tty),
    ):
        return cmd._check_ai_plan_gate(_stage(), ["plan output"])


class TestStrictAiReviewBlocks:
    def test_risk_at_threshold_blocks(self):
        cmd = _make_cmd(strict="high")

        assert _run(cmd, _integration("high")) is False
        assert any("blocked deployment" in e for e in cmd._errors)

    def test_risk_above_threshold_blocks(self):
        cmd = _make_cmd(strict="high")

        assert _run(cmd, _integration("critical")) is False

    def test_risk_below_threshold_proceeds(self):
        cmd = _make_cmd(strict="high")

        assert _run(cmd, _integration("medium")) is True
        assert cmd._errors == []

    def test_force_does_not_override_strict(self):
        """The whole point of `--strict`: a pipeline cannot shrug it off with --force."""
        cmd = _make_cmd(strict="high", force=True)

        assert _run(cmd, _integration("critical")) is False

    def test_threshold_is_honoured(self):
        cmd = _make_cmd(strict="critical")

        assert _run(cmd, _integration("high")) is True

    def test_error_names_risk_and_threshold(self):
        cmd = _make_cmd(strict="medium")

        _run(cmd, _integration("high"))

        message = " ".join(cmd._errors)
        assert "HIGH" in message
        assert "MEDIUM" in message


class TestAdvisoryAiMode:
    """`--ai` without `--strict` — prompt a human, never silently proceed."""

    def test_high_risk_blocks_when_not_a_tty(self):
        cmd = _make_cmd(ai=True)

        assert _run(cmd, _integration("high"), tty=False) is False
        assert any("not a TTY" in e for e in cmd._errors)

    def test_force_overrides_in_advisory_mode(self):
        cmd = _make_cmd(ai=True, force=True)

        assert _run(cmd, _integration("critical"), tty=False) is True

    def test_low_risk_never_prompts(self):
        cmd = _make_cmd(ai=True)

        with patch("click.confirm") as confirm:
            assert _run(cmd, _integration("low")) is True
        confirm.assert_not_called()

    def test_tty_prompt_accepted(self):
        cmd = _make_cmd(ai=True)

        with patch("click.confirm", return_value=True):
            assert _run(cmd, _integration("high")) is True

    def test_tty_prompt_declined_records_the_reason(self):
        cmd = _make_cmd(ai=True)

        with patch("click.confirm", return_value=False):
            assert _run(cmd, _integration("high")) is False
        assert any("cancelled by operator" in e for e in cmd._errors)


class TestProviderProblems:
    """A gate that cannot run must not silently pass under --strict."""

    def test_missing_integration_is_fatal_under_strict(self):
        cmd = _make_cmd(strict="high")

        with patch("strata.integrations.ai.find_ai_integration", return_value=None):
            ok = cmd._check_ai_plan_gate(_stage(), [])

        assert ok is False
        assert any("no reachable ai_agent integration" in e for e in cmd._errors)

    def test_missing_integration_is_advisory_only_without_strict(self):
        cmd = _make_cmd(ai=True)

        with patch("strata.integrations.ai.find_ai_integration", return_value=None):
            assert cmd._check_ai_plan_gate(_stage(), []) is True

    def test_unavailable_provider_is_fatal_under_strict(self):
        cmd = _make_cmd(strict="high")

        assert _run(cmd, _integration(available=False)) is False

    def test_analysis_exception_is_fatal_under_strict(self):
        cmd = _make_cmd(strict="high")

        assert _run(cmd, _integration(raises=RuntimeError("boom"))) is False
        assert any("boom" in e for e in cmd._errors)

    def test_analysis_exception_does_not_block_advisory_mode(self):
        cmd = _make_cmd(ai=True)

        assert _run(cmd, _integration(raises=RuntimeError("boom"))) is True


class TestParseAiRisk:
    @pytest.mark.parametrize(
        "content,expected",
        [
            ('{"risk": "critical"}', "critical"),
            ('{"risk": "HIGH"}', "high"),
            ('{"summary": "no risk key"}', "low"),
            ("not json at all, but mentions critical", "critical"),
            ("plain prose with no verdict", "low"),
        ],
    )
    def test_risk_extraction(self, content, expected):
        assert _parse_ai_risk(content)[0] == expected

    def test_unparseable_content_defaults_to_low_not_a_crash(self):
        """Advisory mode must survive a provider that returns prose."""
        risk, parsed = _parse_ai_risk("")
        assert risk == "low"
        assert parsed == {}


class TestDeployRunAiFlagsAreReachable:
    """Regression: the flags existed as parameters but not as CLI options, so the
    entire gate above was dead code. `--help` is the check that catches that."""

    def test_ai_flag_is_declared(self):
        result = CliRunner().invoke(deploy, ["run", "--help"])

        assert "--ai" in result.output

    def test_strict_ai_review_flag_is_declared(self):
        result = CliRunner().invoke(deploy, ["run", "--help"])

        assert "--strict-ai-review" in result.output

    def test_ai_flag_reaches_the_command(self, tmp_path):
        with patch.object(RunDeployCommand, "__init__", return_value=None) as init:
            with patch.object(RunDeployCommand, "execute", return_value=True):
                with patch("strata.commands.cli_deploy.handle_command_exit"):
                    CliRunner().invoke(deploy, ["run", "--ai", "--work-path", str(tmp_path)])

        assert init.call_args.kwargs["ai"] is True

    def test_strict_ai_review_reaches_the_command(self, tmp_path):
        with patch.object(RunDeployCommand, "__init__", return_value=None) as init:
            with patch.object(RunDeployCommand, "execute", return_value=True):
                with patch("strata.commands.cli_deploy.handle_command_exit"):
                    CliRunner().invoke(deploy, ["run", "--strict-ai-review", "critical", "--work-path", str(tmp_path)])

        assert init.call_args.kwargs["strict_ai_review"] == "critical"

    def test_threshold_is_lowercased_by_the_command(self, tmp_path):
        """`--strict-ai-review CRITICAL` must match the lowercase risk levels."""
        with patch.object(RunDeployCommand, "_initialize", return_value=None):
            cmd = RunDeployCommand(work_path=str(tmp_path), strict_ai_review="CRITICAL")

        assert cmd._strict_ai_review == "critical"

    def test_flags_default_off(self, tmp_path):
        with patch.object(RunDeployCommand, "__init__", return_value=None) as init:
            with patch.object(RunDeployCommand, "execute", return_value=True):
                with patch("strata.commands.cli_deploy.handle_command_exit"):
                    CliRunner().invoke(deploy, ["run", "--work-path", str(tmp_path)])

        assert init.call_args.kwargs["ai"] is False
        assert init.call_args.kwargs["strict_ai_review"] is None
