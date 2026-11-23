#!/usr/bin/env python3
"""Tests for `verification.py`: post-deploy verify checks (docs/design/post-deploy-verification.md).

Most tests fake `run_command` and the clock, so the timing (initial delay, period, thresholds, phase timeout) is
checked exactly and fast; two run a real Python script for the environment, the message and a real timeout.
"""

import sys
from pathlib import Path

import pytest

from strata.controllers import verification
from strata.controllers.verification import (
    CheckResult,
    VerifyReport,
    all_verified,
    output_steps,
    run_verify_block,
    verify_diagnostics,
    would_verify,
)
from strata.models.audit_manifest_model import ManifestCheckModel
from strata.models.deployment_model import DeploymentVerifyModel
from strata.utils.transport import CommandResult


def _block(**fields) -> DeploymentVerifyModel:
    checks = fields.pop("checks", [{"name": "web", "run": ["check"]}])
    return DeploymentVerifyModel.model_validate({"initial_delay": 0, "checks": checks, **fields})


class _Clock:
    """A clock that only moves when the runner sleeps, so every wait is visible and deterministic."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def fake(monkeypatch):
    """`run_command` answering from a script of exit codes; records every call."""
    state: dict = {"codes": [0], "calls": [], "stdout": "", "timed_out": False}

    def _run(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        state["calls"].append({"args": list(args), "cwd": cwd, "env": dict(env or {}), "timeout": timeout})
        codes = state["codes"]
        code = codes.pop(0) if len(codes) > 1 else codes[0]
        return CommandResult(returncode=code, stdout=state["stdout"], stderr="", timed_out=state["timed_out"])

    monkeypatch.setattr(verification, "run_command", _run)
    monkeypatch.setattr(verification.shutil, "which", lambda name: f"/bin/{Path(name).name}")
    return state


def _run(block, clock=None, **kwargs) -> list[CheckResult]:
    clock = clock or _Clock()
    return run_verify_block(
        block,
        deployment="app",
        step="apply_web",
        tokens=kwargs.pop("tokens", {}),
        root=Path("/solution"),
        sleep=clock.sleep,
        clock=clock,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


def test_a_check_that_exits_zero_passes_at_once(fake):
    [result] = _run(_block())
    assert (result.answer, result.attempts, result.message) == ("pass", 1, None)


def test_binary_reads_every_nonzero_code_as_a_failure_and_retries_it(fake):
    fake["codes"] = [1]
    clock = _Clock()
    [result] = _run(_block(failure_threshold=3, period=10), clock)
    assert (result.answer, result.attempts, result.message) == ("fail", 3, "exit code 1")
    assert clock.sleeps == [10, 10]  # between attempts, none after the last


@pytest.mark.parametrize(
    ("code", "answer", "attempts"), [(0, "pass", 1), (1, "warn", 1), (2, "fail", 2), (3, "unknown", 2), (7, "fail", 2)]
)
def test_monitoring_reads_the_four_plugin_codes(fake, code, answer, attempts):
    fake["codes"] = [code]
    checks = [{"name": "web", "run": ["check"], "exit_codes": "monitoring"}]
    [result] = _run(_block(checks=checks, failure_threshold=2))
    assert (result.answer, result.attempts) == (answer, attempts)


def test_a_workload_that_comes_up_passes_on_a_later_attempt(fake):
    fake["codes"] = [1, 1, 0]
    [result] = _run(_block(failure_threshold=30))
    assert (result.answer, result.attempts) == ("pass", 3)


def test_success_threshold_needs_consecutive_passes(fake):
    fake["codes"] = [0, 1, 0, 0]
    [result] = _run(_block(success_threshold=2))
    assert (result.answer, result.attempts) == ("pass", 4)


def test_a_check_that_keeps_flipping_stops_at_the_attempt_cap_without_a_deadline(fake):
    """Pass, fail, pass, ... resets both counts, so neither threshold is reached: the attempts stop at
    `failure_threshold + success_threshold - 1`, and a last pass reads as fail (the workload did not stay up)."""
    fake["codes"] = [0, 1] * 20 + [0]
    [result] = _run(_block(success_threshold=2, failure_threshold=5))
    assert (result.answer, result.attempts) == ("fail", 6)
    assert result.message is not None and "the answer keeps changing" in result.message


def test_the_attempt_cap_leaves_a_slow_start_that_then_passes_alone(fake):
    fake["codes"] = [1, 1, 1, 1, 0, 0]  # failure_threshold - 1 fails, then success_threshold passes
    [result] = _run(_block(success_threshold=2, failure_threshold=5))
    assert (result.answer, result.attempts) == ("pass", 6)


def test_an_attempt_that_times_out_is_unknown_and_retried(fake):
    fake["timed_out"] = True
    fake["codes"] = [124]
    [result] = _run(_block(failure_threshold=2, timeout=5))
    assert (result.answer, result.attempts) == ("unknown", 2)
    assert result.message == "timed out after 5s"
    assert [call["timeout"] for call in fake["calls"]] == [5, 5]


def test_a_command_that_cannot_start_is_unknown_and_not_retried(fake, monkeypatch):
    monkeypatch.setattr(verification.shutil, "which", lambda name: None)
    [result] = _run(_block(failure_threshold=30))
    assert (result.answer, result.attempts) == ("unknown", 1)
    assert "'check' cannot be run" in result.message
    assert fake["calls"] == []


def test_a_token_that_does_not_resolve_is_unknown_and_not_retried(fake):
    checks = [{"name": "web", "run": ["check", "${output:apply_web.url}"]}]
    [result] = _run(_block(checks=checks))
    assert (result.answer, result.attempts) == ("unknown", 1)
    assert "apply_web.url" in result.message
    assert fake["calls"] == []


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------


def test_the_block_initial_delay_is_waited_once_before_the_first_check(fake):
    clock = _Clock()
    checks = [{"name": "a", "run": ["check"]}, {"name": "b", "run": ["check"]}]
    _run(_block(checks=checks, initial_delay=10), clock)
    assert clock.sleeps == [10]


def test_the_defaults_wait_about_five_minutes(fake):
    """10s initial delay, then 30 attempts 10s apart (docs/design/post-deploy-verification.md, D4)."""
    fake["codes"] = [1]
    clock = _Clock()
    block = DeploymentVerifyModel.model_validate({"checks": [{"name": "web", "run": ["check"]}]})
    [result] = _run(block, clock)
    assert (result.attempts, sum(clock.sleeps)) == (30, 10 + 29 * 10)


def test_a_check_overrides_the_block_timing(fake):
    fake["codes"] = [1]
    clock = _Clock()
    checks = [{"name": "web", "run": ["check"], "failure_threshold": 2, "period": 3, "initial_delay": 5}]
    [result] = _run(_block(checks=checks, failure_threshold=30, period=10), clock)
    assert result.attempts == 2
    assert clock.sleeps == [5, 3]


def test_the_phase_timeout_stops_the_attempts(fake):
    fake["codes"] = [1]
    clock = _Clock()
    [result] = _run(_block(failure_threshold=100, period=10), clock, phase_timeout=25)
    assert (result.answer, result.attempts) == ("fail", 3)
    assert clock.now <= 25


def test_no_time_left_for_a_check_is_unknown(fake):
    checks = [{"name": "slow", "run": ["check"], "initial_delay": 60}]
    [result] = _run(_block(checks=checks), phase_timeout=30)
    assert (result.answer, result.attempts) == ("unknown", 0)


# ---------------------------------------------------------------------------
# What the command gets, and what it says
# ---------------------------------------------------------------------------


def test_tokens_and_strata_variables_reach_the_command(fake):
    checks = [{"name": "web", "run": ["check", "${output:apply_web.host}"], "env": {"TOKEN": "${secret:TOKEN}"}}]
    _run(_block(checks=checks), tokens={"apply_web.host": "web.example.net", "TOKEN": "s3cr3t-value"})
    [call] = fake["calls"]
    assert call["args"] == ["/bin/check", "web.example.net"]
    assert call["cwd"] == Path("/solution")
    assert call["env"]["TOKEN"] == "s3cr3t-value"
    assert {key: call["env"][key] for key in ("STRATA_DEPLOYMENT", "STRATA_STEP", "STRATA_CHECK", "STRATA_ACTION")} == {
        "STRATA_DEPLOYMENT": "app",
        "STRATA_STEP": "apply_web",
        "STRATA_CHECK": "web",
        "STRATA_ACTION": "apply",
    }
    assert call["env"]["STRATA_ATTEMPT"] == "1"


def test_the_message_is_the_first_line_masked_and_cut(fake):
    fake["codes"] = [1]
    fake["stdout"] = "\n  HTTP 500 from https://web?key=s3cr3t-value\n" + "details\n"
    [result] = _run(_block(failure_threshold=1), redact=lambda text: text.replace("s3cr3t-value", "***"))
    assert result.message == "HTTP 500 from https://web?key=***"

    fake["stdout"] = "x" * 1000
    [result] = _run(_block(failure_threshold=1))
    assert len(result.message) == 300


def test_progress_names_each_failed_attempt_and_the_answer(fake):
    fake["codes"] = [1, 0]
    steps: list[str] = []
    _run(_block(), on_step=steps.append)
    assert steps == [
        "verify step 'apply_web': check 'web' attempt 1 fail: exit code 1",
        "verify step 'apply_web': check 'web' pass after 2 attempt(s)",
    ]


@pytest.mark.slow
def test_a_real_script_reads_its_attempt_and_its_first_line_is_the_message(tmp_path):
    script = tmp_path / "check.py"
    script.write_text(
        "import os, sys\n"
        "attempt = int(os.environ['STRATA_ATTEMPT'])\n"
        "print(f'attempt {attempt}: {os.environ[\"TARGET\"]}')\n"
        "sys.exit(0 if attempt >= 2 else 1)\n",
        encoding="utf-8",
    )
    block = DeploymentVerifyModel.model_validate(
        {
            "initial_delay": 0,
            "period": 1,
            "checks": [{"name": "web", "run": [sys.executable, "check.py"], "env": {"TARGET": "${deployment:name}"}}],
        }
    )
    [result] = run_verify_block(
        block, deployment="app", step="s", tokens={"deployment:name": "app"}, root=tmp_path, sleep=lambda s: None
    )
    assert (result.answer, result.attempts, result.message) == ("pass", 2, "attempt 2: app")


@pytest.mark.slow
def test_a_real_script_that_hangs_is_stopped_and_unknown(tmp_path):
    block = DeploymentVerifyModel.model_validate(
        {
            "initial_delay": 0,
            "timeout": 1,
            "failure_threshold": 1,
            "checks": [{"name": "hang", "run": [sys.executable, "-c", "import time; time.sleep(30)"]}],
        }
    )
    [result] = run_verify_block(block, deployment="app", step="s", tokens={}, root=tmp_path)
    assert (result.answer, result.attempts) == ("unknown", 1)


# ---------------------------------------------------------------------------
# The reaction: policy semantics (D1, D7)
# ---------------------------------------------------------------------------


def _result(answer: str) -> CheckResult:
    return CheckResult(name="web", answer=answer, attempts=3, duration_seconds=1.0, message="HTTP 503")


@pytest.mark.parametrize(
    ("fields", "answer", "severity", "code"),
    [
        ({}, "pass", None, None),
        ({}, "warn", "warning", "verify_check_warning"),
        ({}, "fail", "error", "verify_check_failed"),
        ({"enforcement": "warn"}, "fail", "warning", "verify_check_failed"),
        ({}, "unknown", "error", "verify_check_unknown"),
        ({"on_unknown": "warn"}, "unknown", "warning", "verify_check_unknown"),
        ({"on_unknown": "skip"}, "unknown", "info", "verify_check_unknown"),
    ],
)
def test_the_answer_becomes_a_finding_by_enforcement_and_on_unknown(fields, answer, severity, code):
    diagnostics = verify_diagnostics(_block(**fields), [_result(answer)], step="apply_web")
    if severity is None:
        assert len(diagnostics) == 0
        return
    [finding] = diagnostics.items
    assert (finding.severity.value, finding.code, finding.location) == (severity, code, "apply_web")
    assert "verify check 'web'" in finding.message and "HTTP 503" in finding.message


def test_a_failure_that_stops_the_stage_says_what_to_do():
    [finding] = verify_diagnostics(_block(), [_result("fail")], step="apply_web").items
    assert finding.hint is not None and "enforcement: warn" in finding.hint


def test_a_dry_run_names_the_checks_it_would_run():
    checks = [{"name": "a", "run": ["check"]}, {"name": "b", "run": ["check"]}]
    assert would_verify(_block(checks=checks), "apply_web") == [
        "would verify step 'apply_web': check 'a'",
        "would verify step 'apply_web': check 'b'",
    ]


# ---------------------------------------------------------------------------
# Deployment checks and `deploy verify` (phase 2)
# ---------------------------------------------------------------------------


def test_a_deployment_check_has_no_step(fake):
    clock = _Clock()
    steps: list[str] = []
    run_verify_block(
        _block(checks=[{"name": "e2e", "run": ["check"]}]),
        deployment="app",
        step=None,
        tokens={},
        root=Path("/solution"),
        on_step=steps.append,
        sleep=clock.sleep,
        clock=clock,
    )
    env = fake["calls"][0]["env"]
    assert (env["STRATA_STEP"], env["STRATA_CHECK"], env["STRATA_ACTION"]) == ("", "e2e", "apply")
    assert steps == ["verify the deployment: check 'e2e' pass after 1 attempt(s)"]


def test_deploy_verify_waits_no_initial_delay_and_says_verify(fake):
    clock = _Clock()
    checks = [{"name": "web", "run": ["check"], "initial_delay": 5}]
    _run(_block(initial_delay=30, checks=checks), clock, action="verify", wait=False)
    assert clock.sleeps == []
    assert fake["calls"][0]["env"]["STRATA_ACTION"] == "verify"


def test_a_disabled_check_does_not_run(fake):
    checks = [{"name": "old", "run": ["old"], "enabled": False}, {"name": "new", "run": ["new"]}]
    results = _run(_block(checks=checks))
    assert [result.name for result in results] == ["new"]
    assert would_verify(_block(checks=checks), None) == ["would verify the deployment: check 'new'"]


def test_a_block_whose_checks_are_all_disabled_waits_for_nothing(fake):
    clock = _Clock()
    assert _run(_block(initial_delay=30, checks=[{"name": "old", "run": ["old"], "enabled": False}]), clock) == []
    assert clock.sleeps == []


def test_a_deployment_finding_has_no_location_so_no_stage_fails():
    [finding] = verify_diagnostics(_block(), [_result("fail")], step=None).items
    assert finding.location is None
    assert finding.message.startswith("Deployment: verify check 'web' failed")


@pytest.mark.parametrize(
    ("answers", "verified"),
    [([], None), (["pass", "warn"], True), (["pass", "fail"], False), (["unknown"], False)],
)
def test_verified_means_every_check_that_ran_said_so(answers, verified):
    checks = [ManifestCheckModel(name=f"c{i}", answer=answer, attempts=1) for i, answer in enumerate(answers)]
    assert all_verified(checks) is verified


def test_the_report_lists_each_steps_checks_then_the_deployments():
    report = VerifyReport(
        stages={"apply_web": [ManifestCheckModel(name="web", answer="pass", attempts=1)]},
        checks=[ManifestCheckModel(name="e2e", answer="fail", attempts=3, message="HTTP 503")],
    )
    assert report.to_dict() == {
        "verified": False,
        "stages": [{"name": "apply_web", "checks": [{"name": "web", "answer": "pass", "attempts": 1}]}],
        "checks": [{"name": "e2e", "answer": "fail", "attempts": 3, "message": "HTTP 503"}],
    }


def test_output_steps_are_the_steps_the_enabled_checks_read():
    checks = [
        {"name": "a", "run": ["check", "${output:apply_web.url}"], "env": {"DB": "${output:apply_db.host}"}},
        {"name": "b", "run": ["check", "${output:apply_old.url}"], "enabled": False},
        {"name": "c", "run": ["check", "${var:region}", "${output:nokey}"]},
    ]
    assert output_steps([_block(checks=checks)]) == {"apply_web", "apply_db"}


def test_a_disabled_block_reads_no_outputs():
    """A destroy reads these records before it starts; a block that is off must not make it read (or fail on) one."""
    checks = [{"name": "a", "run": ["check", "${output:apply_web.url}"], "when": ["destroy"]}]
    assert output_steps([_block(checks=checks, enabled=False)], "destroy") == set()
