"""Tests for ScriptPolicy — external script evaluation via subprocess.

The policy runs at any phase and delegates pass/fail to an external command.
Context is serialised to JSON and passed on stdin.  stdout/stderr lines from a
failing script become violations.
"""

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

try:
    from strata.models.policy_model import PolicyModel
    from strata.validators.policies.base_policy import PolicyContext, PolicyResult
    from strata.validators.policies.script_policy import ScriptPolicy

    IMPL_MISSING = False
except ImportError:
    ScriptPolicy = None  # type: ignore[assignment,misc]
    PolicyContext = None  # type: ignore[assignment,misc]
    PolicyResult = None  # type: ignore[assignment,misc]
    PolicyModel = None  # type: ignore[assignment,misc]
    IMPL_MISSING = True

pytestmark = pytest.mark.skipif(IMPL_MISSING, reason="ScriptPolicy not yet implemented")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_policy(configuration=None) -> "PolicyModel":
    cfg = configuration if configuration is not None else {"command": "scripts/check.sh", "timeout": 30}
    return PolicyModel.model_validate(
        {
            "name": "script-policy",
            "type": "script",
            "phase": "build",
            "enforcement": "deny",
            "configuration": cfg,
        }
    )


def make_context(phase: str = "build") -> "PolicyContext":
    return PolicyContext(phase=phase, work_path=None)


def _mock_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    r = MagicMock()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScriptPolicy:
    def test_passes_when_script_exits_zero(self):
        """Script exits 0 → passed=True, no violations."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context()

        with patch("subprocess.run", return_value=_mock_result(returncode=0)) as mock_run:
            result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.violations == []
        mock_run.assert_called_once()

    def test_fails_when_script_exits_nonzero_with_output(self):
        """Script exits 1 with stdout lines → passed=False, each non-empty line is a violation."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context()

        with patch(
            "subprocess.run", return_value=_mock_result(returncode=1, stdout="Rule A violated\nRule B violated")
        ):
            result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 2
        assert any("Rule A violated" in v for v in result.violations)
        assert any("Rule B violated" in v for v in result.violations)

    def test_fails_with_fallback_violation_when_no_output(self):
        """Script exits 2 with empty stdout and stderr → fallback violation with exit code."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context()

        with patch("subprocess.run", return_value=_mock_result(returncode=2, stdout="", stderr="")):
            result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "2" in result.violations[0]

    def test_skip_when_no_command_configured(self):
        """configuration={} (no 'command' key) → passed=True, details['skipped'] set."""
        policy = ScriptPolicy(make_policy(configuration={}))
        ctx = make_context()

        result = policy.evaluate(ctx)

        assert result.passed is True
        assert result.details is not None
        assert result.details.get("skipped")

    def test_script_not_found(self):
        """subprocess.run raises FileNotFoundError → passed=False, violation contains 'Script not found'."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context()

        with patch("subprocess.run", side_effect=FileNotFoundError("No such file")):
            result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "Script not found" in result.violations[0]
        assert "scripts/check.sh" in result.violations[0]

    def test_script_timeout(self):
        """subprocess.run raises TimeoutExpired → passed=False, violation mentions 'timed out'."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context()

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="scripts/check.sh", timeout=30),
        ):
            result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "timed out" in result.violations[0].lower()
        assert "30" in result.violations[0]

    def test_passes_violations_from_stderr(self):
        """Script exits 1, stdout empty but stderr has message → violation from stderr."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context()

        with patch("subprocess.run", return_value=_mock_result(returncode=1, stdout="", stderr="check failed")):
            result = policy.evaluate(ctx)

        assert result.passed is False
        assert len(result.violations) == 1
        assert "check failed" in result.violations[0]

    def test_context_json_sent_on_stdin(self):
        """subprocess.run must be called with ``input`` containing JSON that includes the phase key."""
        policy = ScriptPolicy(make_policy())
        ctx = make_context(phase="build")

        with patch("subprocess.run", return_value=_mock_result(returncode=0)) as mock_run:
            policy.evaluate(ctx)

        call_kwargs = mock_run.call_args
        # input can be a positional or keyword argument — normalise via call_args.kwargs
        stdin_data = call_kwargs.kwargs.get("input") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        assert stdin_data is not None, "subprocess.run must receive input on stdin"
        parsed = json.loads(stdin_data)
        assert "phase" in parsed
        assert parsed["phase"] == "build"
