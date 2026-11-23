#!/usr/bin/env python3
"""`deploy_run()` running a stage's `verify` checks after its step and the deployment's `spec.verify` after every
step, and `deploy_verify()` running them on their own (docs/design/post-deploy-verification.md, D2, D3, D5).

A real build of a small Terraform solution, then `deploy_run()` with `terraform` faked (as in
`test_deploy_controller.py`) and the check's command faked separately, so each test scripts the check's answer.
"""

from pathlib import Path

import pytest

from strata.controllers import verification
from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run, deploy_verify
from strata.controllers.deploy_stage_log import StageLog
from strata.controllers.solution_context import open_solution
from strata.utils.transport import CommandResult

pytestmark = pytest.mark.slow

MANIFEST = "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec: {}\n"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _solution(tmp_path: Path, stages: str, spec: str = "") -> Path:
    """One Terraform step `apply_web`, a dependent `apply_api`, the deployment's `stages` as given, then `spec`."""
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "infra/main.tf", "# root module\n")
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n  type: local\n  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  provider_type: local\n  resource_type: server\n  category: compute\n",
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: tf_main\n      tool: terraform\n      source:\n        source_path: infra\n"
        "  execution:\n"
        "    - name: apply_web\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "    - name: apply_api\n      provisioner: tf_main\n      resources:\n        - r1\n"
        "      depends_on:\n        - apply_web\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec: {}\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n  stages:\n" + stages + spec,
    )
    return root


#: A stage `apply_web` with one check, no waiting.
_WEB_CHECK = (
    "    - step: apply_web\n      verify:\n        initial_delay: 0\n        failure_threshold: 2\n        period: 1\n"
    "{extra}"
    "        checks:\n          - name: web-answers\n            run: [check-web, '${{deployment:name}}']\n"
)


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


@pytest.fixture
def tools(monkeypatch):
    """Fake `terraform` (always succeeds) and the check's command, whose exit codes a test sets."""
    import strata.integrations.base as base_module

    state: dict = {"check_codes": [0], "check_calls": [], "check_env": [], "sleeps": []}

    def _terraform(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        return CommandResult(returncode=0, stdout="ok", stderr="")

    def _check(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        state["check_calls"].append(list(args))
        state["check_env"].append(dict(env or {}))
        codes = state["check_codes"]
        return CommandResult(returncode=codes.pop(0) if len(codes) > 1 else codes[0], stdout="HTTP 503", stderr="")

    monkeypatch.setattr(base_module, "run_command", _terraform)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    monkeypatch.setattr(verification, "run_command", _check)
    monkeypatch.setattr(verification.time, "sleep", state["sleeps"].append)
    return state


def _deploy(tmp_path: Path, stage_yaml: str, spec: str = "", **kwargs):
    diagnostics, log, steps = _deploy_with_log(tmp_path, stage_yaml, spec, **kwargs)
    return diagnostics, log.stages(diagnostics), steps


def _deploy_with_log(tmp_path: Path, stage_yaml: str, spec: str = "", **kwargs):
    root = _solution(tmp_path, stage_yaml, spec)
    build_path = tmp_path / "build"
    assert build_run(_context(root), "app", build_path).ok
    log = StageLog()
    steps: list[str] = []
    kwargs.setdefault("force", True)
    diagnostics = deploy_run(_context(root), "app", build_path, on_step=steps.append, stage_log=log, **kwargs)
    return diagnostics, log, steps


def test_a_check_that_passes_is_recorded_as_a_verify_phase(tmp_path, tools):
    diagnostics, stages, _ = _deploy(tmp_path, _WEB_CHECK.format(extra=""))

    assert diagnostics.ok, diagnostics.messages()
    web, api = stages
    assert [(phase.step, phase.success) for phase in web.steps][-2:] == [("apply", True), ("verify", True)]
    assert [(check.name, check.answer, check.attempts) for check in web.checks] == [("web-answers", "pass", 1)]
    assert "verify" not in [phase.step for phase in api.steps]  # no checks declared for it
    assert tools["check_calls"] == [["/usr/bin/check-web", "app"]]


def test_a_failing_check_fails_its_stage_and_stops_the_run(tmp_path, tools):
    tools["check_codes"] = [1]

    diagnostics, stages, _ = _deploy(tmp_path, _WEB_CHECK.format(extra=""))

    [error] = diagnostics.errors
    assert (error.code, error.location) == ("verify_check_failed", "apply_web")
    assert "HTTP 503" in error.message
    web, api = stages
    assert web.status == "failed"
    assert [(phase.step, phase.success) for phase in web.steps][-2:] == [("apply", True), ("verify", False)]
    assert [(check.answer, check.attempts) for check in web.checks] == [("fail", 2)]
    assert (api.status, api.skip_reason) == ("skipped", "an earlier step failed")


def test_enforcement_warn_reports_a_failure_and_the_run_goes_on(tmp_path, tools):
    tools["check_codes"] = [1]

    diagnostics, stages, _ = _deploy(tmp_path, _WEB_CHECK.format(extra="        enforcement: warn\n"))

    assert diagnostics.ok, diagnostics.messages()
    assert [w.code for w in diagnostics.warnings if w.code.startswith("verify")] == ["verify_check_failed"]
    assert [stage.status for stage in stages] == ["success", "success"]


def test_no_verify_skips_the_checks(tmp_path, tools):
    tools["check_codes"] = [1]

    diagnostics, stages, _ = _deploy(tmp_path, _WEB_CHECK.format(extra=""), verify=False)

    assert diagnostics.ok, diagnostics.messages()
    assert tools["check_calls"] == []
    assert stages[0].checks is None


def test_a_disabled_block_is_skipped(tmp_path, tools):
    diagnostics, _, _ = _deploy(tmp_path, _WEB_CHECK.format(extra="        enabled: false\n"))

    assert diagnostics.ok, diagnostics.messages()
    assert tools["check_calls"] == []


@pytest.mark.parametrize("flag", ["dry_run", "smoke_test"])
def test_a_preview_names_the_checks_and_runs_none(tmp_path, tools, flag):
    diagnostics, _, steps = _deploy(tmp_path, _WEB_CHECK.format(extra=""), **{flag: True})

    assert diagnostics.ok, diagnostics.messages()
    assert "would verify step 'apply_web': check 'web-answers'" in steps
    assert tools["check_calls"] == []


def test_a_destroy_runs_no_apply_checks(tmp_path, tools):
    diagnostics, _, _ = _deploy(tmp_path, _WEB_CHECK.format(extra=""), destroy=True)

    assert diagnostics.ok, diagnostics.messages()
    assert tools["check_calls"] == []


# ---------------------------------------------------------------------------
# The deployment's own checks, `spec.verify` (phase 2)
# ---------------------------------------------------------------------------

#: The deployment's `spec.verify` with one check, no waiting.
_E2E = (
    "  verify:\n    initial_delay: 0\n    failure_threshold: 2\n    period: 1\n"
    "{extra}"
    "    checks:\n      - name: e2e\n        run: [check-e2e]\n"
)


def _executables(tools) -> list[str]:
    return [call[0] for call in tools["check_calls"]]


def test_the_deployment_checks_run_once_after_every_step(tmp_path, tools):
    diagnostics, log, _ = _deploy_with_log(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""))

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-web", "/usr/bin/check-e2e"]
    env = tools["check_env"][-1]
    assert (env["STRATA_STEP"], env["STRATA_CHECK"], env["STRATA_ACTION"]) == ("", "e2e", "apply")
    assert [(check.name, check.answer) for check in log.deployment_checks()] == [("e2e", "pass")]
    assert log.verified() is True


def test_a_failing_deployment_check_fails_the_run_but_no_stage(tmp_path, tools):
    tools["check_codes"] = [0, 1]  # the stage check passes, the deployment check fails

    diagnostics, log, _ = _deploy_with_log(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""))

    [error] = diagnostics.errors
    assert (error.code, error.location) == ("verify_check_failed", None)
    assert error.message.startswith("Deployment: verify check 'e2e' failed after 2 attempt(s): HTTP 503")
    assert [stage.status for stage in log.stages(diagnostics)] == ["success", "success"]
    assert log.verified() is False


def test_a_failed_stage_check_leaves_the_deployment_checks_unrun(tmp_path, tools):
    tools["check_codes"] = [1]

    diagnostics, log, _ = _deploy_with_log(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""))

    assert [error.code for error in diagnostics.errors] == ["verify_check_failed"]
    assert "/usr/bin/check-e2e" not in _executables(tools)
    assert log.deployment_checks() == []
    assert log.verified() is False  # the stage check that ran failed


def test_a_run_of_some_steps_skips_the_deployment_checks(tmp_path, tools):
    diagnostics, log, _ = _deploy_with_log(
        tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""), stages=["apply_web"]
    )

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-web"]
    [info] = [item for item in diagnostics.infos if item.code == "verify_deployment_skipped"]
    assert "strata deploy verify app" in info.message
    assert log.deployment_checks() == []


@pytest.mark.parametrize("flag", ["dry_run", "smoke_test"])
def test_a_preview_names_the_deployment_checks_and_runs_none(tmp_path, tools, flag):
    diagnostics, _, steps = _deploy(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""), **{flag: True})

    assert diagnostics.ok, diagnostics.messages()
    assert "would verify the deployment: check 'e2e'" in steps
    assert tools["check_calls"] == []


def test_no_verify_skips_the_deployment_checks_and_records_nothing(tmp_path, tools):
    diagnostics, log, _ = _deploy_with_log(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""), verify=False)

    assert diagnostics.ok, diagnostics.messages()
    assert tools["check_calls"] == []
    assert log.verified() is None


def test_a_disabled_deployment_block_is_skipped(tmp_path, tools):
    diagnostics, log, _ = _deploy_with_log(
        tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra="    enabled: false\n")
    )

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-web"]
    assert log.deployment_checks() == []


# ---------------------------------------------------------------------------
# `deploy_verify()`: the checks on their own, against what is deployed
# ---------------------------------------------------------------------------


def _verify(tmp_path: Path, stage_yaml: str, spec: str = "", **kwargs):
    root = _solution(tmp_path, stage_yaml, spec)
    steps: list[str] = []
    diagnostics, report = deploy_verify(_context(root), "app", on_step=steps.append, **kwargs)
    return diagnostics, report, steps


def test_deploy_verify_runs_every_block_without_waiting(tmp_path, tools):
    spec = "  verify:\n    initial_delay: 30\n    checks:\n      - name: e2e\n        run: [check-e2e]\n"

    diagnostics, report, _ = _verify(tmp_path, _WEB_CHECK.format(extra=""), spec)

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-web", "/usr/bin/check-e2e"]
    assert [env["STRATA_ACTION"] for env in tools["check_env"]] == ["verify", "verify"]
    assert tools["sleeps"] == []  # nothing was just started
    assert report.verified is True
    assert [(step, [check.answer for check in checks]) for step, checks in report.stages.items()] == [
        ("apply_web", ["pass"])
    ]
    assert [check.name for check in report.checks] == ["e2e"]


def test_deploy_verify_reports_every_failure_without_stopping(tmp_path, tools):
    tools["check_codes"] = [1]

    diagnostics, report, _ = _verify(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""))

    assert [(error.code, error.location) for error in diagnostics.errors] == [
        ("verify_check_failed", "apply_web"),
        ("verify_check_failed", None),
    ]
    assert report.verified is False


def test_deploy_verify_of_some_steps_skips_the_deployment_checks(tmp_path, tools):
    diagnostics, report, _ = _verify(tmp_path, _WEB_CHECK.format(extra=""), _E2E.format(extra=""), stages=["apply_web"])

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-web"]
    assert "verify_deployment_skipped" in [item.code for item in diagnostics.infos]
    assert report.checks == []


def test_deploy_verify_with_nothing_declared_warns_and_verifies_nothing(tmp_path, tools):
    diagnostics, report, _ = _verify(tmp_path, "    - step: apply_web\n")

    assert diagnostics.ok
    [warning] = diagnostics.warnings
    assert (warning.code, warning.hint is not None) == ("verify_nothing_declared", True)
    assert report.verified is None
    assert tools["check_calls"] == []


def test_deploy_verify_of_an_unknown_step_is_an_error(tmp_path, tools):
    diagnostics, _, _ = _verify(tmp_path, _WEB_CHECK.format(extra=""), stages=["nope"])

    assert "verify_no_steps" in [error.code for error in diagnostics.errors]
    assert tools["check_calls"] == []


def test_deploy_verify_skips_destroy_checks(tmp_path, tools):
    diagnostics, report, _ = _verify(
        tmp_path, _WEB_CHECK.format(extra="").replace("run:", "when: [destroy]\n            run:")
    )

    assert [warning.code for warning in diagnostics.warnings] == ["verify_nothing_declared"]
    assert tools["check_calls"] == []
    assert report.verified is None


# ---------------------------------------------------------------------------
# After a destroy: `when: [destroy]` checks (phase 3)
# ---------------------------------------------------------------------------

#: Stage `apply_web` with an apply check and a destroy check; the deployment's own destroy check.
_DESTROY_CHECKS = (
    "    - step: apply_web\n      verify:\n        initial_delay: 0\n        failure_threshold: 2\n        period: 1\n"
    "        checks:\n          - name: web-answers\n            run: [check-web]\n"
    "          - name: web-gone\n            when: [destroy]\n            run: [check-gone]\n"
)
_E2E_GONE = (
    "  verify:\n    initial_delay: 0\n    failure_threshold: 2\n    period: 1\n"
    "    checks:\n      - name: all-gone\n        when: [apply, destroy]\n        run: [check-all-gone]\n"
)


def test_a_destroy_runs_the_destroy_checks_after_the_step(tmp_path, tools):
    diagnostics, log, _ = _deploy_with_log(tmp_path, _DESTROY_CHECKS, _E2E_GONE, destroy=True)

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-gone", "/usr/bin/check-all-gone"]
    assert [env["STRATA_ACTION"] for env in tools["check_env"]] == ["destroy", "destroy"]
    web = next(stage for stage in log.stages(diagnostics) if stage.name == "apply_web")
    assert [(phase.step, phase.success) for phase in web.steps][-2:] == [("destroy", True), ("verify", True)]
    assert [(check.name, check.answer) for check in web.checks] == [("web-gone", "pass")]
    assert [check.name for check in log.deployment_checks()] == ["all-gone"]
    assert log.verified() is True


def test_a_failing_destroy_check_fails_the_run_and_says_it_is_not_gone(tmp_path, tools):
    tools["check_codes"] = [1]

    diagnostics, log, _ = _deploy_with_log(tmp_path, _DESTROY_CHECKS, destroy=True)

    [error] = diagnostics.errors
    assert (error.code, error.location) == ("verify_check_failed", "apply_web")
    assert "The step was destroyed, but the check says what it removed is not gone." in error.message
    assert "left behind" in (error.hint or "")
    assert log.verified() is False


def test_a_destroy_check_also_runs_after_an_apply_when_it_says_both(tmp_path, tools):
    diagnostics, _, _ = _deploy_with_log(tmp_path, _DESTROY_CHECKS, _E2E_GONE)

    assert diagnostics.ok, diagnostics.messages()
    assert _executables(tools) == ["/usr/bin/check-web", "/usr/bin/check-all-gone"]  # not check-gone
    assert [env["STRATA_ACTION"] for env in tools["check_env"]] == ["apply", "apply"]


def test_no_verify_skips_the_destroy_checks(tmp_path, tools):
    diagnostics, _, _ = _deploy_with_log(tmp_path, _DESTROY_CHECKS, _E2E_GONE, destroy=True, verify=False)

    assert diagnostics.ok, diagnostics.messages()
    assert tools["check_calls"] == []


@pytest.mark.parametrize("flag", ["dry_run", "smoke_test"])
def test_a_destroy_preview_names_the_destroy_checks(tmp_path, tools, flag):
    diagnostics, _, steps = _deploy(tmp_path, _DESTROY_CHECKS, _E2E_GONE, destroy=True, force=False, **{flag: True})

    assert diagnostics.ok, diagnostics.messages()
    assert "would verify step 'apply_web': check 'web-gone'" in steps
    assert "would verify step 'apply_web': check 'web-answers'" not in steps
    assert "would verify the deployment: check 'all-gone'" in steps
    assert tools["check_calls"] == []


def test_a_destroy_of_some_steps_skips_the_deployment_destroy_checks(tmp_path, tools):
    diagnostics, _, _ = _deploy_with_log(tmp_path, _DESTROY_CHECKS, _E2E_GONE, destroy=True, stages=["apply_web"])

    assert _executables(tools) == ["/usr/bin/check-gone"]
    [info] = [item for item in diagnostics.infos if item.code == "verify_deployment_skipped"]
    assert "deploy verify" not in info.message  # nothing to verify later after a destroy


def test_a_destroy_check_that_reads_an_output_without_an_output_store_cannot_answer(tmp_path, tools):
    stage = (
        "    - step: apply_web\n      verify:\n        initial_delay: 0\n        checks:\n"
        "          - name: web-gone\n            when: [destroy]\n            run: [check-gone, '${output:apply_web.url}']\n"
    )

    diagnostics, _, _ = _deploy_with_log(tmp_path, stage, destroy=True)

    assert "verify_outputs_unavailable" in [warning.code for warning in diagnostics.warnings]
    assert [(error.code, error.location) for error in diagnostics.errors] == [("verify_check_unknown", "apply_web")]
    assert tools["check_calls"] == []


def test_deploy_verify_without_an_output_store_says_why_an_output_check_cannot_answer(tmp_path, tools):
    stage = (
        "    - step: apply_web\n      verify:\n        checks:\n"
        "          - name: web-answers\n            run: [check-web, '${output:apply_web.url}']\n"
    )

    diagnostics, report, _ = _verify(tmp_path, stage)

    assert [warning.code for warning in diagnostics.warnings] == ["verify_outputs_unavailable"]
    [error] = diagnostics.errors
    assert (error.code, error.location) == ("verify_check_unknown", "apply_web")
    assert tools["check_calls"] == []  # the token did not resolve, so the command never ran
    assert report.verified is False


# ---------------------------------------------------------------------------
# Reading the outputs a check needs when no step ran before it
# ---------------------------------------------------------------------------


class _Store:
    """An output store whose `read()` answers from `records`, raising an exception value as is."""

    def __init__(self, records):
        self.records = records
        self.reads: list[str] = []

    def read(self, step):
        self.reads.append(step)
        record = self.records[step]
        if isinstance(record, Exception):
            raise record
        return record


def test_a_destroy_reuses_the_records_its_preflight_read_and_a_stale_one_does_not_stop_it():
    """The preflight read `apply_db`; reading it again is a second round trip. A record only a check needs that
    is stale makes that check unknown (its `on_unknown` decides), instead of stopping the destroy before it starts."""
    from strata.controllers.deploy_controller import _read_check_outputs
    from strata.controllers.deployment_output_store import OutputRecordStaleError
    from strata.utils.diagnostics import Diagnostics

    store = _Store({"apply_web": OutputRecordStaleError("older than 24h")})
    diagnostics = Diagnostics()

    outputs = _read_check_outputs(
        store,
        "main",
        {"apply_db", "apply_web"},
        diagnostics=diagnostics,
        already_read={"apply_db": {"host": "db"}},
        unusable_blocks=False,
    )

    assert outputs == {"apply_db": {"host": "db"}}
    assert store.reads == ["apply_web"]
    assert diagnostics.ok
    [warning] = diagnostics.warnings
    assert warning.code == "verify_outputs_unusable" and "cannot answer" in warning.message


def test_deploy_verify_still_stops_on_a_stale_record():
    from strata.controllers.deploy_controller import _read_check_outputs
    from strata.controllers.deployment_output_store import OutputRecordStaleError
    from strata.utils.diagnostics import Diagnostics

    diagnostics = Diagnostics()
    _read_check_outputs(
        _Store({"apply_web": OutputRecordStaleError("old")}), "main", {"apply_web"}, diagnostics=diagnostics
    )

    assert [error.code for error in diagnostics.errors] == ["verify_outputs_unusable"]
