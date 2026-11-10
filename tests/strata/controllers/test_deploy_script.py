#!/usr/bin/env python3
"""`tool: script` through `deploy_run()` (docs/design/script-provisioner.md).

Most tests stub `run_command`, so no interpreter is needed. The last section runs a real Python script through
`build_run()` and `deploy_run()` with nothing stubbed.
"""

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from strata.controllers.build_controller import build_run
from strata.controllers.deploy_controller import deploy_run
from strata.controllers.solution_context import open_solution
from strata.integrations.gitops import ArgoCDIntegration
from strata.integrations.script import ScriptIntegration
from strata.integrations.terraform import TerraformIntegration
from strata.utils.transport import CommandResult

MANIFEST = "apiVersion: strata.huybrechts.xyz/v2\nkind: solution\nmeta:\n  name: test-solution\nspec: {}\n"
SECRET = "s3cr3t-value-123"
UNRELATED = "unrelated-db-pass-456"


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _context(root: Path):
    context = open_solution(root)
    assert context.ok, context.diagnostics.messages()
    return context


def _script_solution(
    tmp_path: Path,
    *,
    secret: bool = False,
    region: str = "westeurope",
    script: str = "print('hi')\n",
    plan_args: list[str] | None = None,
    destroy_args: list[str] | None = None,
    values: list[str] | None = None,
    unrelated_secret: bool = False,
) -> Path:
    """One script step: `deploy.py --environment ${var:REGION}`, plus `--token ${secret:API_TOKEN}` when asked.

    `plan_args`, `destroy_args` and `values`, when given, are written under `script:` as lists.
    `unrelated_secret` also declares a secret the script never names, `UNRELATED_DB_PASSWORD`.
    """
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "scripts/deploy.py", script)
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  type: local\n  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  provider_type: local\n  resource_type: server\n  category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    token_args = "          - --token\n          - ${secret:API_TOKEN}\n" if secret else ""
    phase_args = "".join(
        f"        {name}:{'' if items else ' []'}\n" + "".join(f"          - {item}\n" for item in items)
        for name, items in (("plan_args", plan_args), ("destroy_args", destroy_args), ("values", values))
        if items is not None
    )
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        "  providers:\n    - p1\n"
        "  provisioners:\n    - name: script_main\n      tool: script\n      source:\n        source_path: scripts\n"
        "      script:\n        file: deploy.py\n        args:\n          - --environment\n"
        "          - ${var:REGION}\n"
        f"{token_args}"
        f"{phase_args}"
        "  execution:\n    - name: run_script\n      provisioner: script_main\n      resources:\n        - r1\n"
        "  resources:\n    - name: r1\n      resource: r1\n",
    )
    secret_items = ""
    if secret:
        secret_items += f"    - key: API_TOKEN\n      store: constant\n      value: {SECRET}\n"
    if unrelated_secret:
        secret_items += f"    - key: UNRELATED_DB_PASSWORD\n      store: constant\n      value: {UNRELATED}\n"
    secrets = f"  secrets:\n{secret_items}" if secret_items else ""
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        f"  variables:\n    - key: REGION\n      store: constant\n      value: {region}\n{secrets}",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


@dataclass
class _Run:
    calls: list[list[str]] = field(default_factory=list)
    envs: list[dict[str, str] | None] = field(default_factory=list)
    responders: list[Any] = field(default_factory=list)

    def of_script(self) -> list[list[str]]:
        """The calls that executed the script itself (not the syntax check)."""
        return [c for c in self.calls if "py_compile" not in c]

    def script_envs(self) -> list[dict[str, str] | None]:
        return [e for c, e in zip(self.calls, self.envs, strict=True) if "py_compile" not in c]


@pytest.fixture
def run(monkeypatch):
    recorder = _Run()

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        recorder.calls.append(list(args))
        recorder.envs.append(dict(env) if env is not None else None)
        for responder in recorder.responders:
            result = responder(list(args))
            if result is not None:
                return result
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(base_module.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    return recorder


def _built(tmp_path: Path, root: Path) -> Path:
    build_path = tmp_path / "build"
    diagnostics = build_run(_context(root), "app", build_path)
    assert diagnostics.ok, diagnostics.messages()
    return build_path


def _deploy_fails(result: CommandResult):
    """Fail only the call that executes the script, leaving the syntax check alone."""
    return lambda args: result if "py_compile" not in args else None


# ---------------------------------------------------------------------------
# G1: the preview of a dry run is shown
# ---------------------------------------------------------------------------


def test_a_dry_run_shows_what_the_script_would_run(tmp_path: Path, run):
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert diagnostics.ok, diagnostics.messages()
    preview = next(s for s in steps if s.startswith("would run:"))
    assert "--environment westeurope" in preview
    assert run.of_script() == []  # a preview never executes the script


def test_the_preview_redacts_resolved_secrets(tmp_path: Path, run):
    root = _script_solution(tmp_path, secret=True)
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert diagnostics.ok, diagnostics.messages()
    preview = next(s for s in steps if s.startswith("would run:"))
    assert SECRET not in preview
    assert "--token ***" in preview


def test_a_real_run_does_not_print_a_would_run_line(tmp_path: Path, run):
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    diagnostics = deploy_run(_context(root), "app", build_path, on_step=steps.append)

    assert diagnostics.ok, diagnostics.messages()
    assert not any(s.startswith("would run:") for s in steps)
    assert len(run.of_script()) == 1


def test_a_preview_the_tool_already_streamed_is_not_shown_twice(tmp_path: Path, run, monkeypatch):
    def _streaming_plan(self, path, *, line_callback=None, **kwargs):
        assert line_callback is not None
        line_callback("stdout", "streamed preview line")
        return CommandResult(returncode=0, stdout="streamed preview line", stderr="")

    monkeypatch.setattr(ScriptIntegration, "plan", _streaming_plan)
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    deploy_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert "streamed preview line" not in steps


# ---------------------------------------------------------------------------
# G3: what a failure message carries
# ---------------------------------------------------------------------------


def test_a_failing_script_reports_its_exit_code_and_what_it_printed_to_stdout(tmp_path: Path, run):
    run.responders.append(_deploy_fails(CommandResult(returncode=4, stdout="ERROR: the vault is sealed", stderr="")))
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    message = diagnostics.errors[0].message
    assert "deploy failed" in message
    assert "exit code 4" in message
    assert "ERROR: the vault is sealed" in message


def test_a_failure_with_both_streams_shows_both(tmp_path: Path, run):
    run.responders.append(_deploy_fails(CommandResult(returncode=1, stdout="progress line", stderr="fatal: no")))
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    message = diagnostics.errors[0].message
    assert "fatal: no" in message
    assert "progress line" in message


def test_a_timeout_is_reported_as_a_timeout_not_an_exit_code(tmp_path: Path, run):
    run.responders.append(
        _deploy_fails(CommandResult(returncode=124, stdout="", stderr="still working", timed_out=True))
    )
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    message = diagnostics.errors[0].message
    assert "timed out" in message
    assert "exit code" not in message
    assert "still working" in message


def test_a_failure_message_keeps_only_the_last_lines_of_a_long_output(tmp_path: Path, run):
    long_output = "\n".join(f"line {i}" for i in range(50))
    run.responders.append(_deploy_fails(CommandResult(returncode=1, stdout=long_output, stderr="")))
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    message = diagnostics.errors[0].message
    assert "line 49" in message
    assert "line 0\n" not in message
    assert "earlier lines omitted" in message


def test_a_failure_message_redacts_resolved_secrets(tmp_path: Path, run):
    run.responders.append(_deploy_fails(CommandResult(returncode=1, stdout=f"token was {SECRET}", stderr="")))
    root = _script_solution(tmp_path, secret=True)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    message = diagnostics.errors[0].message
    assert SECRET not in message
    assert "token was ***" in message


def test_a_failed_syntax_check_reports_its_exit_code_too(tmp_path: Path, run):
    run.responders.append(
        lambda args: CommandResult(returncode=1, stdout="", stderr="SyntaxError") if "py_compile" in args else None
    )
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    message = diagnostics.errors[0].message
    assert "validate failed" in message
    assert "exit code 1" in message
    assert "SyntaxError" in message


# ---------------------------------------------------------------------------
# G4: tools that write no saved plan cannot be applied from one
# ---------------------------------------------------------------------------


def test_the_flag_is_false_for_the_tools_that_write_no_plan_file():
    assert ScriptIntegration.SUPPORTS_SAVED_PLAN is False
    assert ArgoCDIntegration.SUPPORTS_SAVED_PLAN is False
    assert TerraformIntegration.SUPPORTS_SAVED_PLAN is True


def test_apply_refuses_a_script_step_before_anything_runs(tmp_path: Path, run):
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, use_saved_plan=True)

    assert not diagnostics.ok
    error = diagnostics.errors[0]
    assert error.code == "saved_plan_unsupported"
    assert "run_script" in error.message
    assert "deploy run" in error.message
    assert "--stage" in error.message
    assert run.calls == []


# ---------------------------------------------------------------------------
# A real interpreter, nothing stubbed
# ---------------------------------------------------------------------------

_REAL_SCRIPT = (
    "import json, os, sys, time\n"
    "region = sys.argv[sys.argv.index('--environment') + 1]\n"
    "values = json.loads(os.environ['STRATA_VALUES'])\n"
    "with open('result.json', 'w') as handle:\n"
    "    json.dump({'args': sys.argv[1:], 'region': values['REGION'], 'phase': os.environ.get('STRATA_PHASE')}, handle)\n"
    "if region == 'boom':\n"
    "    print('it broke: boom')\n"
    "    sys.exit(3)\n"
    "if region == 'sleep':\n"
    "    time.sleep(30)\n"
)


@pytest.fixture
def real_python(monkeypatch):
    """Resolve the interpreter to this one: a bare `python3` on PATH may be a Windows Store stub."""
    import strata.integrations.script as script_module

    real_which = script_module.shutil.which
    monkeypatch.setattr(
        script_module.shutil,
        "which",
        lambda cmd, *a, **kw: (
            cmd if cmd == sys.executable else (None if cmd in ("python3", "python") else real_which(cmd, *a, **kw))
        ),
    )


def test_a_real_script_runs_with_resolved_arguments_and_values(tmp_path: Path, real_python):
    root = _script_solution(tmp_path, script=_REAL_SCRIPT)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    result = json.loads((build_path / "scripts" / "result.json").read_text())
    assert result == {"args": ["--environment", "westeurope"], "region": "westeurope", "phase": "apply"}


def test_a_real_failing_script_reports_its_exit_code_and_stdout(tmp_path: Path, real_python):
    root = _script_solution(tmp_path, script=_REAL_SCRIPT, region="boom")
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    message = diagnostics.errors[0].message
    assert "exit code 3" in message
    assert "it broke: boom" in message


def test_a_real_script_that_overruns_its_timeout_is_reported_as_a_timeout(tmp_path: Path, real_python, monkeypatch):
    monkeypatch.setenv("STRATA_TIMEOUT_RUN_SCRIPT_APPLY", "1")
    root = _script_solution(tmp_path, script=_REAL_SCRIPT, region="sleep")
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    assert "timed out" in diagnostics.errors[0].message


# ---------------------------------------------------------------------------
# Phase 2: plan_args, destroy_args and STRATA_PHASE (docs/design/script-provisioner.md)
# ---------------------------------------------------------------------------


def test_a_dry_run_runs_the_script_with_plan_args_in_the_plan_phase(tmp_path: Path, run):
    root = _script_solution(tmp_path, plan_args=["--action", "plan", "${var:REGION}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    [call] = run.of_script()
    assert call[2:] == ["--action", "plan", "westeurope"]
    [env] = run.script_envs()
    assert env is not None
    assert env["STRATA_PHASE"] == "plan"
    assert "STRATA_VALUES" in env


def test_a_real_run_applies_without_running_plan_args(tmp_path: Path, run):
    root = _script_solution(tmp_path, plan_args=["--action", "plan"])
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    diagnostics = deploy_run(_context(root), "app", build_path, on_step=steps.append)

    assert diagnostics.ok, diagnostics.messages()
    [call] = run.of_script()  # one execution, not a plan followed by an apply
    assert call[2:] == ["--environment", "westeurope"]
    [env] = run.script_envs()
    assert env is not None
    assert env["STRATA_PHASE"] == "apply"


def test_a_dry_run_without_plan_args_executes_nothing_and_shows_the_preview(tmp_path: Path, run):
    root = _script_solution(tmp_path)
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    deploy_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert run.of_script() == []
    assert any(s.startswith("would run:") for s in steps)


def test_destroy_runs_destroy_args_in_the_destroy_phase(tmp_path: Path, run):
    root = _script_solution(tmp_path, destroy_args=["--action", "revoke", "${var:REGION}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, force=True)

    assert diagnostics.ok, diagnostics.messages()
    assert len(run.calls) == 1  # no syntax check on a destroy
    assert run.calls[0][2:] == ["--action", "revoke", "westeurope"]
    [env] = run.script_envs()
    assert env is not None
    assert env["STRATA_PHASE"] == "destroy"


def test_destroy_is_still_refused_for_a_script_without_destroy_args(tmp_path: Path, run):
    root = _script_solution(tmp_path, plan_args=["--action", "plan"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, force=True)

    assert [e.code for e in diagnostics.errors] == ["destroy_unsupported"]
    assert run.calls == []


def test_a_destroy_dry_run_shows_what_would_run_and_executes_nothing(tmp_path: Path, run):
    root = _script_solution(
        tmp_path, secret=True, destroy_args=["--action", "revoke", "--token", "${secret:API_TOKEN}"]
    )
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, dry_run=True, on_step=steps.append)

    assert diagnostics.ok, diagnostics.messages()
    assert run.calls == []
    preview = next(s for s in steps if s.startswith("would run:"))
    assert "--action revoke" in preview
    assert SECRET not in preview
    assert "--token ***" in preview


def test_a_destroy_argument_that_needs_an_output_nothing_can_serve_stops_the_run_first(tmp_path: Path, run):
    root = _script_solution(tmp_path, destroy_args=["--ip", "${output:run_script.ip}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, force=True)

    assert "destroy_output_unavailable" in [e.code for e in diagnostics.errors]
    assert run.calls == []


def test_a_real_dry_run_runs_plan_args_in_the_plan_phase(tmp_path: Path, real_python):
    root = _script_solution(tmp_path, script=_REAL_SCRIPT, plan_args=["--environment", "previewed"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True)

    assert diagnostics.ok, diagnostics.messages()
    result = json.loads((build_path / "scripts" / "result.json").read_text())
    assert result["phase"] == "plan"
    assert result["args"] == ["--environment", "previewed"]


def test_a_real_run_never_executes_plan_args(tmp_path: Path, real_python):
    """`boom` makes the script exit 3: if plan_args ran before the apply, this run would fail."""
    root = _script_solution(tmp_path, script=_REAL_SCRIPT, plan_args=["--environment", "boom"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    assert json.loads((build_path / "scripts" / "result.json").read_text())["phase"] == "apply"


def test_a_real_destroy_runs_destroy_args_in_the_destroy_phase(tmp_path: Path, real_python):
    root = _script_solution(tmp_path, script=_REAL_SCRIPT, destroy_args=["--environment", "${var:REGION}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, force=True)

    assert diagnostics.ok, diagnostics.messages()
    result = json.loads((build_path / "scripts" / "result.json").read_text())
    assert result["phase"] == "destroy"
    assert result["args"] == ["--environment", "westeurope"]


# ---------------------------------------------------------------------------
# script.values: the script receives only what it lists
# ---------------------------------------------------------------------------


def _delivered_values(env: dict[str, str] | None) -> dict[str, str]:
    assert env is not None
    return json.loads(env["STRATA_VALUES"])


def test_the_script_receives_only_the_values_it_lists(tmp_path: Path, run):
    root = _script_solution(tmp_path, secret=True, unrelated_secret=True, values=["REGION"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    [env] = run.script_envs()
    assert _delivered_values(env) == {"REGION": "westeurope", "deployment:name": "app"}
    assert UNRELATED not in env["STRATA_VALUES"]
    assert SECRET not in env["STRATA_VALUES"]


def test_listing_values_does_not_stop_args_resolving_them(tmp_path: Path, run):
    root = _script_solution(tmp_path, secret=True, values=[])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    [call] = run.of_script()
    assert call[-2:] == ["--token", SECRET]
    [env] = run.script_envs()
    assert _delivered_values(env) == {"deployment:name": "app"}


def test_without_a_list_the_script_still_receives_every_value(tmp_path: Path, run):
    root = _script_solution(tmp_path, secret=True, unrelated_secret=True)
    build_path = _built(tmp_path, root)

    deploy_run(_context(root), "app", build_path)

    [env] = run.script_envs()
    assert set(_delivered_values(env)) == {"REGION", "API_TOKEN", "UNRELATED_DB_PASSWORD", "deployment:name"}


def test_a_destroy_delivers_only_the_listed_values_too(tmp_path: Path, run):
    root = _script_solution(
        tmp_path, secret=True, unrelated_secret=True, destroy_args=["--action", "revoke"], values=["REGION"]
    )
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, force=True)

    assert diagnostics.ok, diagnostics.messages()
    [env] = run.script_envs()
    assert _delivered_values(env) == {"REGION": "westeurope", "deployment:name": "app"}


def test_a_listed_value_that_does_not_resolve_stops_the_step_before_it_runs(tmp_path: Path, run):
    root = _script_solution(tmp_path, values=["REGION", "TYPO"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    message = " ".join(diagnostics.messages())
    assert "run_script" in message
    assert "'TYPO'" in message
    assert run.of_script() == []


def test_a_dry_run_catches_a_listed_value_that_does_not_resolve(tmp_path: Path, run):
    root = _script_solution(tmp_path, values=["TYPO"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True)

    assert not diagnostics.ok
    assert "'TYPO'" in " ".join(diagnostics.messages())


_KEYS_SCRIPT = (
    "import json, os\n"
    "with open('result.json', 'w') as handle:\n"
    "    json.dump(sorted(json.loads(os.environ['STRATA_VALUES'])), handle)\n"
)


def test_a_real_script_does_not_see_a_secret_it_did_not_list(tmp_path: Path, real_python):
    root = _script_solution(tmp_path, script=_KEYS_SCRIPT, secret=True, unrelated_secret=True, values=["API_TOKEN"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    assert json.loads((build_path / "scripts" / "result.json").read_text()) == ["API_TOKEN", "deployment:name"]


# ---------------------------------------------------------------------------
# Outputs: a script's STRATA_OUTPUT_FILE becomes ${output:step.key} for the steps after it
# ---------------------------------------------------------------------------

TOKEN_OUTPUT = "tok-secret-789"

_PRODUCER = (
    "import json, os\n"
    "with open(os.environ['STRATA_OUTPUT_FILE'], 'w') as handle:\n"
    f"    json.dump({{'address': '10.0.0.5', 'token': '{TOKEN_OUTPUT}', '_sensitive': ['token']}}, handle)\n"
)
_BAD_PRODUCER = "import os\nwith open(os.environ['STRATA_OUTPUT_FILE'], 'w') as handle:\n    handle.write('not json')\n"
_CONSUMER = "import json, sys\nwith open('result.json', 'w') as handle:\n    json.dump(sys.argv[1:], handle)\n"


def _chained_solution(
    tmp_path: Path,
    *,
    producer: str,
    consumer_args: list[str],
    output_store: str | None = None,
    consumer: str = _CONSUMER,
    consumer_extra: str = "",
) -> Path:
    """`run_script` (runs `producer`), then `use_output` (depends on it, runs `consumer` with `consumer_args`).

    `consumer_extra` is YAML appended to the consumer's `script:` block, indented eight spaces.
    """
    root = tmp_path / "sln"
    _write(root, "strata.yaml", MANIFEST)
    _write(root, "scripts/deploy.py", producer)
    _write(root, "scripts_next/next.py", consumer)
    _write(
        root,
        "provider.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: provider\nmeta:\n  name: p1\nspec:\n"
        "  type: local\n  region: local\n",
    )
    _write(
        root,
        "resource.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: resource\nmeta:\n  name: r1\nspec:\n"
        "  provider_type: local\n  resource_type: server\n  category: compute\n"
        "  default_tags:\n    managed-by: strata\n",
    )
    items = "".join(f"          - {arg}\n" for arg in consumer_args)
    store = f"  output_store: {output_store}\n" if output_store else ""
    _write(
        root,
        "workspace.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: workspace\nmeta:\n  name: main\nspec:\n"
        f"{store}"
        "  providers:\n    - p1\n"
        "  provisioners:\n"
        "    - name: script_main\n      tool: script\n      source:\n        source_path: scripts\n"
        "      script:\n        file: deploy.py\n"
        "    - name: script_next\n      tool: script\n      source:\n        source_path: scripts_next\n"
        f"      script:\n        file: next.py\n        args:\n{items}{consumer_extra}"
        "  execution:\n"
        "    - name: run_script\n      provisioner: script_main\n      resources:\n        - r1\n"
        "    - name: use_output\n      provisioner: script_next\n      depends_on:\n        - run_script\n"
        "      resources:\n        - r2\n"
        "  resources:\n    - name: r1\n      resource: r1\n    - name: r2\n      resource: r1\n",
    )
    _write(
        root,
        "environment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: environment\nmeta:\n  name: prd\nspec:\n"
        "  variables:\n    - key: REGION\n      store: constant\n      value: westeurope\n",
    )
    _write(
        root,
        "deployment.yaml",
        "apiVersion: strata.huybrechts.xyz/v2\nkind: deployment\nmeta:\n  name: app\nspec:\n"
        "  workspace: main\n  environments:\n    - prd\n",
    )
    return root


def test_a_real_script_output_reaches_the_step_that_depends_on_it(tmp_path: Path, real_python):
    args = ["--address", "${output:run_script.address}", "--token", "${output:run_script.token}"]
    root = _chained_solution(tmp_path, producer=_PRODUCER, consumer_args=args)
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    result = json.loads((build_path / "scripts_next" / "result.json").read_text())
    assert result == ["--address", "10.0.0.5", "--token", TOKEN_OUTPUT]


def test_a_malformed_output_file_warns_and_does_not_fail_the_run(tmp_path: Path, real_python):
    root = _chained_solution(tmp_path, producer=_BAD_PRODUCER, consumer_args=["--region", "${var:REGION}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    [warning] = diagnostics.warnings
    assert "run_script" in warning.message
    assert "STRATA_OUTPUT_FILE" in warning.message
    assert (build_path / "scripts_next" / "result.json").exists()  # the next step still ran


def test_a_step_needing_an_output_the_script_never_wrote_fails_naming_it(tmp_path: Path, real_python):
    root = _chained_solution(tmp_path, producer="pass\n", consumer_args=["${output:run_script.address}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    assert "run_script.address" in " ".join(diagnostics.messages())


def _fake_output_store(monkeypatch) -> dict[str, tuple[bytes, str]]:
    """Register a `fake-output-store` backend; returns its backing dict."""
    from dataclasses import dataclass, field

    from strata.integrations import registry as registry_module
    from strata.integrations.capabilities import KeyValueStoreIntegration
    from strata.integrations.resolved_context import KeyValueEntry

    data: dict[str, tuple[bytes, str]] = {}

    class _Backend(KeyValueStoreIntegration):
        TYPE = "fake-output-store"
        CAPABILITIES = frozenset({"key_value"})
        TRANSPORTS = frozenset({"sdk"})

        def get(self, key: str) -> KeyValueEntry | None:
            if key not in data:
                return None
            value, version = data[key]
            return KeyValueEntry(value=value, version=version)

        def put(self, key: str, value: bytes, *, if_version: str | None = None) -> str:
            data[key] = (value, str(len(data) + 1))
            return data[key][1]

    @dataclass
    class _EntryPoint:
        name: str
        value: str
        _target: Any = field(default=_Backend)

        def load(self) -> Any:
            return self._target

    monkeypatch.setattr(
        registry_module, "entry_points", lambda group: [_EntryPoint(name="fake-output-store", value="x:Y")]
    )
    return data


def test_a_sensitive_script_output_is_never_recorded_to_the_output_store(tmp_path: Path, real_python, monkeypatch):
    data = _fake_output_store(monkeypatch)
    args = ["--address", "${output:run_script.address}", "--token", "${output:run_script.token}"]
    root = _chained_solution(tmp_path, producer=_PRODUCER, consumer_args=args, output_store="fake-output-store")
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert diagnostics.ok, diagnostics.messages()
    record_bytes, _version = data["strata/step-outputs/app/run_script"]
    record = json.loads(record_bytes)
    assert record["values"] == {"address": "10.0.0.5"}
    assert TOKEN_OUTPUT not in record_bytes.decode("utf-8")


_NOISY_CONSUMER = "import sys\nprint('using ' + ' '.join(sys.argv[1:]))\nsys.exit(3)\n"


def test_a_sensitive_output_is_redacted_from_a_later_steps_failure(tmp_path: Path, real_python):
    """The consumer echoes the token it was given and fails: the error must not carry the real value."""
    root = _chained_solution(
        tmp_path,
        producer=_PRODUCER,
        consumer_args=["--token", "${output:run_script.token}", "--address", "${output:run_script.address}"],
        consumer=_NOISY_CONSUMER,
    )
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    message = " ".join(diagnostics.messages())
    assert TOKEN_OUTPUT not in message
    assert "--token ***" in message
    assert "10.0.0.5" in message  # an output that is not sensitive is left alone


def test_a_destroy_listing_an_output_in_values_stops_before_anything_runs(tmp_path: Path, run):
    root = _chained_solution(
        tmp_path,
        producer="pass\n",
        consumer_args=["--x", "one"],
        consumer_extra="        destroy_args: [--x, one]\n        values: [run_script.address]\n",
    )
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, destroy=True, force=True, stage="use_output")

    assert "destroy_output_unavailable" in [e.code for e in diagnostics.errors]
    assert "run_script.address" in " ".join(diagnostics.messages())
    assert run.calls == []


def test_a_preview_needing_an_output_nobody_recorded_says_a_preview_applies_nothing(tmp_path: Path, run):
    root = _chained_solution(tmp_path, producer=_PRODUCER, consumer_args=["--address", "${output:run_script.address}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True)

    assert not diagnostics.ok
    message = " ".join(diagnostics.messages())
    assert "run_script.address" in message
    assert "A preview does not apply 'run_script'" in message
    assert "output_store" in message
    assert run.of_script() == []


def test_a_real_run_missing_an_output_does_not_blame_the_preview(tmp_path: Path, run):
    """The producer's (stubbed) run wrote no file, so the consumer's output is missing, and the run says so plainly."""
    root = _chained_solution(tmp_path, producer=_PRODUCER, consumer_args=["--address", "${output:run_script.address}"])
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path)

    assert not diagnostics.ok
    message = " ".join(diagnostics.messages())
    assert "run_script.address" in message
    assert "preview" not in message.lower()


def test_a_preview_reads_the_output_an_earlier_real_run_recorded(tmp_path: Path, run, monkeypatch):
    """The remedy the message gives: with an `output_store`, a recorded output resolves in a preview."""
    data = _fake_output_store(monkeypatch)
    data["strata/deployment-outputs/app"] = (
        json.dumps(
            {"run_script": {"collected_at": datetime.now(timezone.utc).isoformat(), "values": {"address": "10.0.0.5"}}}
        ).encode("utf-8"),
        "1",
    )
    root = _chained_solution(
        tmp_path,
        producer=_PRODUCER,
        consumer_args=["--address", "${output:run_script.address}"],
        output_store="fake-output-store",
    )
    build_path = _built(tmp_path, root)
    steps: list[str] = []

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True, on_step=steps.append)

    assert diagnostics.ok, diagnostics.messages()
    assert any(s.startswith("would run:") and "--address 10.0.0.5" in s for s in steps)


def test_a_preview_also_checks_the_outputs_a_script_lists_in_values(tmp_path: Path, run):
    root = _chained_solution(
        tmp_path,
        producer=_PRODUCER,
        consumer_args=["--x", "one"],
        consumer_extra="        values: [run_script.address]\n",
    )
    build_path = _built(tmp_path, root)

    diagnostics = deploy_run(_context(root), "app", build_path, dry_run=True)

    assert not diagnostics.ok
    message = " ".join(diagnostics.messages())
    assert "run_script.address" in message
    assert "was not produced by it" in message  # caught before the script is asked to run, not by its failure


def test_a_preview_checks_plan_args_but_a_real_run_does_not(tmp_path: Path, run):
    """Only the arguments of the phase that runs need their outputs: a real run never uses `plan_args`."""
    root = _chained_solution(
        tmp_path,
        producer="pass\n",
        consumer_args=["--x", "one"],
        consumer_extra='        plan_args: ["--a", "${output:run_script.address}"]\n',
    )
    build_path = _built(tmp_path, root)

    preview = deploy_run(_context(root), "app", build_path, dry_run=True)
    real = deploy_run(_context(root), "app", build_path)

    assert not preview.ok
    assert "was not produced by it" in " ".join(preview.messages())
    assert real.ok, real.messages()
