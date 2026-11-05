#!/usr/bin/env python3
"""Tests for `ScriptIntegration` (docs/design/script-provisioner.md)."""

import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from strata.integrations.errors import IntegrationError
from strata.integrations.registry import get
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueReference, ValueResolution
from strata.integrations.script import ScriptIntegration, _interpreter_argv
from strata.models.common_models import SourceModel
from strata.models.provisioning_model import ProvisionerModel, ProvisionerScriptModel
from strata.models.workspace_model import WorkspaceMetaModel, WorkspaceModel, WorkspaceSpecModel
from strata.utils.transport import CommandResult


def _capture(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        captured["args"] = args
        captured["cwd"] = cwd
        captured["env"] = env
        return CommandResult(returncode=0, stdout="ok", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    # Default: every command "resolves" at its own literal name (e.g.
    # "bash" -> "bash") — a test that cares about a specific resolved path
    # (e.g. "/usr/bin/python3") overrides this afterward. Without this,
    # tests using a bare "deploy.sh" would otherwise depend on whether the
    # host machine running them happens to have Git Bash installed at a
    # known Windows fallback location (found on this repo's own dev
    # machine, Phase 6 graduation review, 2026-10-14) — non-deterministic
    # across environments for a test that isn't exercising that fallback.
    monkeypatch.setattr(shutil, "which", lambda name: name)
    return captured


def _platform(monkeypatch, os_name: str) -> None:
    # Never patch the real os.name: pathlib (and pytest plugins) would then build PosixPath on Windows.
    import strata.integrations.script as script_module

    monkeypatch.setattr(script_module, "os", SimpleNamespace(name=os_name))


def _bound(
    file: str,
    args: list[str] | None = None,
    plan_args: list[str] | None = None,
    destroy_args: list[str] | None = None,
    values: list[str] | None = None,
) -> ScriptIntegration:
    provisioner = ProvisionerModel(
        name="script-step",
        tool="script",
        source=SourceModel(source_path="scripts"),
        script=ProvisionerScriptModel(
            file=file, args=args, plan_args=plan_args, destroy_args=destroy_args, values=values
        ),
    )
    integration = ScriptIntegration()
    integration.bind(provisioner)
    return integration


# ---------------------------------------------------------------------------
# class contract / registration
# ---------------------------------------------------------------------------


def test_class_declares_its_contract():
    assert ScriptIntegration.TYPE == "script"
    assert ScriptIntegration.CAPABILITIES == {"infrastructure"}
    assert ScriptIntegration.TRANSPORTS == {"cli"}
    assert ScriptIntegration.COMMAND is None
    assert ScriptIntegration.ENV_VAR_PREFIX is None


def test_registered_in_the_registry():
    assert isinstance(get("script"), ScriptIntegration)


# ---------------------------------------------------------------------------
# _interpreter_argv() — per-extension dispatch
# ---------------------------------------------------------------------------


def test_interpreter_argv_sh_uses_bash_from_path_when_available(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/bash" if name == "bash" else None)
    assert _interpreter_argv(".sh", Path("deploy.sh")) == ["/bin/bash", "deploy.sh"]


def test_interpreter_argv_bash_uses_bash_from_path_when_available(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/bash" if name == "bash" else None)
    assert _interpreter_argv(".bash", Path("deploy.bash")) == ["/bin/bash", "deploy.bash"]


def test_interpreter_argv_sh_falls_back_to_git_bash_on_windows(monkeypatch):
    """Regression (found during Phase 6 graduation verification,
    2026-10-14) — Git for Windows' installer frequently does not add
    Git\\bin to PATH, confirmed on this repo's own dev machine
    (bash.exe exists on disk, shutil.which('bash') found neither)."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _platform(monkeypatch, "nt")
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == r"C:\Program Files\Git\bin\bash.exe")

    assert _interpreter_argv(".sh", Path("deploy.sh")) == [r"C:\Program Files\Git\bin\bash.exe", "deploy.sh"]


def test_interpreter_argv_sh_falls_back_to_bash_literal_when_nothing_resolves(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _platform(monkeypatch, "posix")  # no Windows fallback paths checked
    assert _interpreter_argv(".sh", Path("deploy.sh")) == ["bash", "deploy.sh"]


def test_interpreter_argv_sh_falls_back_to_bash_literal_on_windows_with_no_git_install(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    _platform(monkeypatch, "nt")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    assert _interpreter_argv(".sh", Path("deploy.sh")) == ["bash", "deploy.sh"]


def test_interpreter_argv_py_prefers_python3_on_path(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    assert _interpreter_argv(".py", Path("deploy.py")) == ["/usr/bin/python3", "deploy.py"]


def test_interpreter_argv_py_falls_back_to_python_when_python3_missing(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python" if name == "python" else None)
    assert _interpreter_argv(".py", Path("deploy.py")) == ["/usr/bin/python", "deploy.py"]


def test_interpreter_argv_py_falls_back_to_sys_executable_as_last_resort(monkeypatch):
    """Regression (found on design review, 2026-10-14) — sys.executable is
    only a last-resort fallback, never the first choice."""
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _interpreter_argv(".py", Path("deploy.py")) == [sys.executable, "deploy.py"]


def test_interpreter_argv_ps1_prefers_pwsh(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/pwsh" if name == "pwsh" else None)
    argv = _interpreter_argv(".ps1", Path("deploy.ps1"))
    assert argv == [
        "/usr/bin/pwsh",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        "deploy.ps1",
    ]


def test_interpreter_argv_ps1_falls_back_to_powershell(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    argv = _interpreter_argv(".ps1", Path("deploy.ps1"))
    assert argv[0] == "powershell"


def test_interpreter_argv_bat():
    """cmd.exe associates .bat natively - no interpreter wrapper."""
    assert _interpreter_argv(".bat", Path("deploy.bat")) == ["deploy.bat"]


def test_interpreter_argv_unsupported_extension_raises():
    with pytest.raises(IntegrationError, match="no interpreter dispatch"):
        _interpreter_argv(".exe", Path("deploy.exe"))


# ---------------------------------------------------------------------------
# bind() / command / is_available()
# ---------------------------------------------------------------------------


def test_command_is_none_when_unbound():
    assert ScriptIntegration().command is None


def test_is_available_is_false_when_unbound():
    assert ScriptIntegration().is_available() is False


def test_command_resolves_from_the_bound_script(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    assert _bound("deploy.py").command == "/usr/bin/python3"


def test_is_available_true_when_the_interpreter_resolves(monkeypatch):
    # `command` resolves "bash" -> "/bin/bash" via `_interpreter_argv()`,
    # then `is_available()` re-queries that already-resolved absolute path
    # — real `shutil.which()` returns an absolute input unchanged when it
    # exists, so the fake must be idempotent over both forms too.
    monkeypatch.setattr(shutil, "which", lambda name: "/bin/bash" if name in ("bash", "/bin/bash") else None)
    assert _bound("deploy.sh").is_available() is True


def test_is_available_false_when_the_interpreter_does_not_resolve(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert _bound("deploy.sh").is_available() is False


@pytest.mark.parametrize("os_name,expected", [("nt", True), ("posix", False)])
def test_is_available_bat_checks_the_platform_not_path(monkeypatch, os_name, expected):
    """.bat has no PATH lookup - cmd.exe associates it natively, Windows-only."""
    _platform(monkeypatch, os_name)
    assert _bound("deploy.bat").is_available() is expected


def test_is_available_false_for_an_unsupported_extension_defensively():
    """Unreachable via normal construction (schema already restricts the
    extension set) - `is_available()`'s own `except IntegrationError` guard,
    exercised directly via `model_construct()` to bypass validation."""
    integration = ScriptIntegration()
    integration._script = ProvisionerScriptModel.model_construct(file="deploy.exe", args=None)
    assert integration.is_available() is False


# ---------------------------------------------------------------------------
# validate() — real per-interpreter no-exec syntax check
# ---------------------------------------------------------------------------


def test_validate_py_runs_py_compile(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    _bound("deploy.py").validate(tmp_path)
    assert captured["args"] == ["/usr/bin/python3", "-m", "py_compile", str(tmp_path / "deploy.py")]


def test_validate_sh_runs_bash_dash_n(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _bound("deploy.sh").validate(tmp_path)
    assert captured["args"] == ["bash", "-n", str(tmp_path / "deploy.sh")]


def test_validate_ps1_parses_without_executing(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "pwsh" if name == "pwsh" else None)
    _bound("deploy.ps1").validate(tmp_path)
    args = captured["args"]
    assert args[0] == "pwsh"
    assert args[1] == "-NoProfile"
    assert "ParseFile" in args[3]


def test_validate_ps1_escapes_single_quotes_in_the_path(monkeypatch, tmp_path: Path):
    """Regression (found on code review, 2026-10-15) — `script.file` isn't
    restricted to quote-free names by schema validation (only path
    traversal/extension are checked); an unescaped single quote would break
    out of the single-quoted PowerShell string this builds and let the rest
    of the filename run as arbitrary PowerShell code."""
    captured = _capture(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "pwsh" if name == "pwsh" else None)
    _bound("it's-evil.ps1").validate(tmp_path)
    parse_arg = captured["args"][3]
    expected_path = str(tmp_path / "it's-evil.ps1").replace("'", "''")
    assert f"ParseFile('{expected_path}'," in parse_arg


def test_validate_bat_is_a_no_op_success_without_running_anything(monkeypatch, tmp_path: Path):
    def _fail_if_called(*a, **k):
        raise AssertionError("run_command should not be called for .bat validate()")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fail_if_called)

    result = _bound("deploy.bat").validate(tmp_path)

    assert result.is_successful


# ---------------------------------------------------------------------------
# plan() — safe, non-executing preview
# ---------------------------------------------------------------------------


def test_plan_reports_a_missing_script_without_executing_anything(monkeypatch, tmp_path: Path):
    def _fail_if_called(*a, **k):
        raise AssertionError("run_command should not be called by plan()")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fail_if_called)

    result = _bound("deploy.py").plan(tmp_path)

    assert not result.is_successful
    assert "not found" in result.stderr


def test_plan_reports_what_would_run_when_the_script_exists(monkeypatch, tmp_path: Path):
    (tmp_path / "deploy.py").write_text("print('hi')")
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)

    result = _bound("deploy.py", args=["--env", "prod"]).plan(tmp_path)

    assert result.is_successful
    assert "would run:" in result.stdout
    assert "--env prod" in result.stdout


# ---------------------------------------------------------------------------
# deploy() — real execution
# ---------------------------------------------------------------------------


def test_deploy_invokes_the_resolved_argv_with_args(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)

    _bound("deploy.py", args=["--env", "prod"]).deploy(tmp_path)

    assert captured["args"] == ["/usr/bin/python3", str(tmp_path / "deploy.py"), "--env", "prod"]
    assert captured["cwd"] == tmp_path


def test_deploy_without_args(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _bound("deploy.sh").deploy(tmp_path)
    assert captured["args"] == ["bash", str(tmp_path / "deploy.sh")]


# ---------------------------------------------------------------------------
# destroy() — deferred (docs/design/deploy-command.md)
# ---------------------------------------------------------------------------


def test_destroy_raises():
    with pytest.raises(IntegrationError, match="does not support destroy"):
        _bound("deploy.py").destroy(Path("/work"))


# ---------------------------------------------------------------------------
# default_output() — build-time strata.auto.json skeleton (Phase 4)
# ---------------------------------------------------------------------------


def _graph(*, variable_refs=None, feature_refs=None, properties=None, custom=None) -> ResolvedWorkspaceGraph:
    workspace = WorkspaceModel(
        meta=WorkspaceMetaModel(name="ws"),
        spec=WorkspaceSpecModel(
            providers=["p"],
            provisioners=[ProvisionerModel(name="prov", tool="terraform", source=SourceModel(source_path="infra"))],
        ),
    )
    return ResolvedWorkspaceGraph(
        workspace=workspace,
        variable_refs=variable_refs or [],
        feature_refs=feature_refs or [],
        properties=properties or {},
        custom=custom or {},
    )


def test_default_output_writes_the_build_time_safe_subset(tmp_path: Path):
    graph = _graph(
        variable_refs=[
            ValueReference(key="REGION", store="constant", value="westeurope"),
            # Integration-backed/secret ref, unresolved at build time (value is None) - excluded.
            ValueReference(key="db_password", store="infisical", value=None),
        ],
        feature_refs=[ValueReference(key="beta", store="constant", value=True)],
        properties={"team": "platform"},
        custom={"vm_count": 3},
    )
    import json as json_module

    provisioner = ProvisionerModel(
        name="script-step",
        tool="script",
        source=SourceModel(source_path="scripts"),
        script=ProvisionerScriptModel(file="deploy.py"),
    )
    output = ScriptIntegration().default_output(tmp_path, ValueResolution(deployment="d"), provisioner, graph)

    assert set(output) == {"strata.auto.json"}
    payload = json_module.loads(output["strata.auto.json"])
    assert payload == {
        "variables": {"REGION": "westeurope"},
        "flags": {"beta": True},
        "properties": {"team": "platform"},
        "custom": {"vm_count": 3},
    }
    assert "secrets" not in payload
    assert "db_password" not in payload["variables"]


# ---------------------------------------------------------------------------
# _resolved_args() / _values_env() — the two resolved_values consumers
# ---------------------------------------------------------------------------


def test_resolved_args_passthrough_when_tokens_is_none():
    integration = _bound("deploy.py", args=["--env", "${var:environment}"])
    assert integration._resolved_args(None) == ["--env", "${var:environment}"]


def test_resolved_args_resolves_tokens():
    integration = _bound("deploy.py", args=["--env", "${var:environment}", "--password", "${secret:db_password}"])
    resolved = integration._resolved_args({"environment": "prod", "db_password": "hunter2"})
    assert resolved == ["--env", "prod", "--password", "hunter2"]


def test_values_env_empty_when_tokens_is_none():
    assert ScriptIntegration._values_env(None) == {}


def test_values_env_json_encodes_every_resolved_token():
    import json as json_module

    env = ScriptIntegration._values_env({"environment": "prod", "db_password": "hunter2"})
    assert set(env) == {"STRATA_VALUES"}
    assert json_module.loads(env["STRATA_VALUES"]) == {"environment": "prod", "db_password": "hunter2"}


# ---------------------------------------------------------------------------
# plan()/deploy() with resolved_values/output_path (Phase 4)
# ---------------------------------------------------------------------------


def test_plan_resolves_args_from_resolved_values(tmp_path: Path):
    (tmp_path / "deploy.py").write_text("print('hi')")
    result = _bound("deploy.py", args=["--env", "${var:environment}"]).plan(
        tmp_path, resolved_values={"environment": "prod"}
    )
    assert result.is_successful
    assert "--env prod" in result.stdout
    assert "${var:environment}" not in result.stdout


def test_plan_appends_output_path_to_the_preview(tmp_path: Path):
    (tmp_path / "deploy.py").write_text("print('hi')")
    output_path = tmp_path / "strata.auto.json"
    result = _bound("deploy.py").plan(tmp_path, output_path=output_path)
    assert result.stdout.endswith(str(output_path))


def test_deploy_resolves_args_and_injects_strata_values_env(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    integration = _bound("deploy.sh", args=["--env", "${var:environment}"])

    integration.deploy(tmp_path, resolved_values={"environment": "prod"})

    assert captured["args"] == ["bash", str(tmp_path / "deploy.sh"), "--env", "prod"]
    import json as json_module

    assert json_module.loads(captured["env"]["STRATA_VALUES"]) == {"environment": "prod"}


def test_deploy_merges_strata_values_into_the_caller_supplied_env(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    integration = _bound("deploy.sh")

    integration.deploy(tmp_path, env={"EXISTING": "1"}, resolved_values={"k": "v"})

    assert captured["env"]["EXISTING"] == "1"
    assert "STRATA_VALUES" in captured["env"]


def test_deploy_appends_output_path_as_the_final_argument(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    integration = _bound("deploy.sh", args=["--env", "prod"])
    output_path = tmp_path / "strata.auto.json"

    integration.deploy(tmp_path, output_path=output_path)

    assert captured["args"] == ["bash", str(tmp_path / "deploy.sh"), "--env", "prod", str(output_path)]


def test_deploy_without_resolved_values_has_no_strata_values_env(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _bound("deploy.sh").deploy(tmp_path)
    assert "STRATA_VALUES" not in captured["env"]
    assert captured["env"]["STRATA_PHASE"] == "apply"


# ---------------------------------------------------------------------------
# plan_args / destroy_args / STRATA_PHASE (docs/design/script-provisioner.md)
# ---------------------------------------------------------------------------


def _script_in(tmp_path: Path, name: str = "deploy.py") -> None:
    (tmp_path / name).write_text("print('hi')")


def test_plan_runs_the_script_with_plan_args_when_previewing(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", args=["--apply"], plan_args=["--action", "${var:mode}"])

    result = integration.plan(tmp_path, dry_run=True, resolved_values={"mode": "plan"})

    assert result.is_successful
    assert captured["args"] == ["bash", str(tmp_path / "deploy.sh"), "--action", "plan"]
    assert captured["env"]["STRATA_PHASE"] == "plan"
    assert "STRATA_VALUES" in captured["env"]


def test_plan_streams_and_honours_the_timeout_when_it_runs_the_script(monkeypatch, tmp_path: Path):
    seen: dict[str, object] = {}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        seen["timeout"] = timeout
        seen["line_callback"] = line_callback
        return CommandResult(returncode=0, stdout="", stderr="")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    _script_in(tmp_path, "deploy.sh")
    callback = lambda stream, text: None  # noqa: E731

    _bound("deploy.sh", plan_args=["--check"]).plan(tmp_path, dry_run=True, timeout=7, line_callback=callback)

    assert seen == {"timeout": 7, "line_callback": callback}


def test_plan_appends_the_output_path_to_plan_args_too(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")
    output_path = tmp_path / "strata.auto.json"

    _bound("deploy.sh", plan_args=["--check"]).plan(tmp_path, dry_run=True, output_path=output_path)

    assert captured["args"][-2:] == ["--check", str(output_path)]


def test_plan_does_not_run_plan_args_unless_previewing(monkeypatch, tmp_path: Path):
    def _fail_if_called(*a, **k):
        raise AssertionError("a real run must not execute plan_args")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fail_if_called)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    _script_in(tmp_path, "deploy.sh")

    result = _bound("deploy.sh", args=["--apply"], plan_args=["--check"]).plan(tmp_path)

    assert result.is_successful
    assert result.stdout.startswith("would run:")
    assert "--apply" in result.stdout


def test_plan_without_plan_args_never_executes_even_when_previewing(monkeypatch, tmp_path: Path):
    def _fail_if_called(*a, **k):
        raise AssertionError("no plan_args, nothing to execute")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fail_if_called)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    _script_in(tmp_path, "deploy.sh")

    result = _bound("deploy.sh", args=["--apply"]).plan(tmp_path, dry_run=True)

    assert result.stdout.startswith("would run:")


def test_plan_with_plan_args_still_reports_a_missing_script(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)

    result = _bound("deploy.sh", plan_args=["--check"]).plan(tmp_path, dry_run=True)

    assert not result.is_successful
    assert "not found" in result.stderr
    assert "args" not in captured


def test_deploy_sets_the_apply_phase_next_to_strata_values(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)

    _bound("deploy.sh").deploy(tmp_path, resolved_values={"k": "v"})

    assert captured["env"]["STRATA_PHASE"] == "apply"
    assert "STRATA_VALUES" in captured["env"]


def test_destroy_runs_destroy_args_in_the_destroy_phase(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", args=["--apply"], destroy_args=["--action", "${var:mode}"])

    result = integration.destroy(tmp_path, resolved_values={"mode": "revoke"})

    assert result.is_successful
    assert captured["args"] == ["bash", str(tmp_path / "deploy.sh"), "--action", "revoke"]
    assert captured["env"]["STRATA_PHASE"] == "destroy"
    assert captured["cwd"] == tmp_path


def test_destroy_does_not_append_the_output_path(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")

    _bound("deploy.sh", destroy_args=["--action", "revoke"]).destroy(tmp_path, output_path=tmp_path / "out.json")

    assert captured["args"][-1] == "revoke"


def test_plan_destroy_reports_what_would_run_and_never_executes(monkeypatch, tmp_path: Path):
    def _fail_if_called(*a, **k):
        raise AssertionError("a destroy preview must not execute the script")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fail_if_called)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", destroy_args=["--action", "${var:mode}"])

    result = integration.plan_destroy(tmp_path, resolved_values={"mode": "revoke"})

    assert result.is_successful
    assert result.stdout == f"would run: bash {tmp_path / 'deploy.sh'} --action revoke"


def test_plan_destroy_without_destroy_args_raises(tmp_path: Path):
    with pytest.raises(IntegrationError, match="does not support destroy"):
        _bound("deploy.sh").plan_destroy(tmp_path)


def test_plan_destroy_reports_a_missing_script(tmp_path: Path):
    result = _bound("deploy.sh", destroy_args=["--action", "revoke"]).plan_destroy(tmp_path)

    assert not result.is_successful
    assert "not found" in result.stderr


def test_supports_destroy_follows_destroy_args():
    assert _bound("deploy.sh").supports_destroy is False
    assert _bound("deploy.sh", plan_args=["--check"]).supports_destroy is False
    assert _bound("deploy.sh", destroy_args=["--action", "revoke"]).supports_destroy is True
    assert ScriptIntegration().supports_destroy is False  # nothing bound yet


def test_the_class_constant_stays_false_so_an_unbound_script_cannot_destroy():
    assert ScriptIntegration.SUPPORTS_DESTROY is False


@pytest.mark.parametrize("method", ["plan", "deploy", "destroy", "plan_destroy"])
def test_an_unresolvable_token_fails_the_call_instead_of_raising(monkeypatch, tmp_path: Path, method):
    _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound(
        "deploy.sh",
        args=["${output:ghost.key}"],
        plan_args=["${output:ghost.key}"],
        destroy_args=["${output:ghost.key}"],
    )
    kwargs = {"dry_run": True} if method == "plan" else {}

    result = getattr(integration, method)(tmp_path, resolved_values={"x": "y"}, **kwargs)

    assert not result.is_successful
    assert "ghost.key" in result.stderr


# ---------------------------------------------------------------------------
# script.values: which resolved values reach STRATA_VALUES (docs/design/script-provisioner.md)
# ---------------------------------------------------------------------------

_TOKENS = {
    "API_TOKEN": "tok-1",
    "UNRELATED_DB_PASSWORD": "hunter2",
    "REGION": "westeurope",
    "build.url": "https://example.test",
    "deployment:name": "app",
    "deployment:tenant": "acme",
}


def _delivered(env: dict[str, str]) -> dict[str, str]:
    import json as json_module

    return json_module.loads(env["STRATA_VALUES"])


def _phase_calls(integration: ScriptIntegration, tmp_path: Path, phase: str, tokens):
    """Run `phase` and return the (result)."""
    if phase == "plan":
        return integration.plan(tmp_path, dry_run=True, resolved_values=tokens)
    if phase == "apply":
        return integration.deploy(tmp_path, resolved_values=tokens)
    return integration.destroy(tmp_path, resolved_values=tokens)


@pytest.mark.parametrize("phase", ["plan", "apply", "destroy"])
def test_only_the_listed_values_and_the_deployment_entries_are_delivered(monkeypatch, tmp_path: Path, phase):
    captured = _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", plan_args=["x"], destroy_args=["x"], values=["API_TOKEN", "build.url"])

    result = _phase_calls(integration, tmp_path, phase, _TOKENS)

    assert result.is_successful
    assert _delivered(captured["env"]) == {
        "API_TOKEN": "tok-1",
        "build.url": "https://example.test",
        "deployment:name": "app",
        "deployment:tenant": "acme",
    }


def test_an_empty_allow_list_delivers_only_the_deployment_entries(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _bound("deploy.sh", values=[]).deploy(tmp_path, resolved_values=_TOKENS)
    assert _delivered(captured["env"]) == {"deployment:name": "app", "deployment:tenant": "acme"}


def test_without_an_allow_list_every_value_is_delivered(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    _bound("deploy.sh").deploy(tmp_path, resolved_values=_TOKENS)
    assert _delivered(captured["env"]) == _TOKENS


def test_the_allow_list_does_not_limit_what_args_can_use(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    integration = _bound("deploy.sh", args=["--token", "${secret:API_TOKEN}"], values=[])

    integration.deploy(tmp_path, resolved_values=_TOKENS)

    assert captured["args"][-2:] == ["--token", "tok-1"]
    assert "API_TOKEN" not in _delivered(captured["env"])


def test_an_allow_list_with_nothing_resolved_delivers_no_values_and_does_not_fail(monkeypatch, tmp_path: Path):
    captured = _capture(monkeypatch)
    result = _bound("deploy.sh", values=["API_TOKEN"]).deploy(tmp_path)
    assert result.is_successful
    assert "STRATA_VALUES" not in captured["env"]


@pytest.mark.parametrize("method", ["plan", "deploy", "destroy", "plan_destroy"])
def test_a_listed_value_that_did_not_resolve_fails_the_call_and_names_it(monkeypatch, tmp_path: Path, method):
    captured = _capture(monkeypatch)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", plan_args=["x"], destroy_args=["x"], values=["API_TOKEN", "TYPO", "ALSO_GONE"])
    kwargs = {"dry_run": True} if method == "plan" else {}

    result = getattr(integration, method)(tmp_path, resolved_values={"API_TOKEN": "t"}, **kwargs)

    assert not result.is_successful
    assert "'TYPO'" in result.stderr
    assert "'ALSO_GONE'" in result.stderr
    assert "script.values" in result.stderr
    assert "args" not in captured  # nothing ran


def test_a_plain_preview_also_catches_a_listed_value_that_did_not_resolve(tmp_path: Path):
    """A real `deploy run` only previews before the apply; the typo must not wait for the apply to surface."""
    _script_in(tmp_path, "deploy.sh")
    result = _bound("deploy.sh", values=["TYPO"]).plan(tmp_path, resolved_values={"API_TOKEN": "t"})
    assert not result.is_successful
    assert "'TYPO'" in result.stderr


# ---------------------------------------------------------------------------
# Outputs: STRATA_OUTPUT_FILE in, output() out (docs/design/script-provisioner.md)
# ---------------------------------------------------------------------------


def _writing(monkeypatch, content: str | None, *, returncode: int = 0) -> dict[str, object]:
    """Stub the process: it writes `content` to `STRATA_OUTPUT_FILE` (nothing when `content` is None)."""
    seen: dict[str, object] = {}

    def _fake_run_command(args, *, cwd=None, env=None, timeout=60, input=None, line_callback=None):
        target = Path(env["STRATA_OUTPUT_FILE"])
        seen["file"] = target
        seen["directory_existed"] = target.parent.is_dir()
        if content is not None:
            target.write_text(content, encoding="utf-8")
        return CommandResult(returncode=returncode, stdout="", stderr="boom" if returncode else "")

    import strata.integrations.base as base_module

    monkeypatch.setattr(base_module, "run_command", _fake_run_command)
    monkeypatch.setattr(shutil, "which", lambda name: name)
    return seen


def _collected(integration: ScriptIntegration, tmp_path: Path) -> dict[str, dict[str, object]]:
    import json as json_module

    result = integration.output(tmp_path, json_format=True)
    assert result.is_successful, result.stderr
    return json_module.loads(result.stdout)


def test_deploy_gives_the_script_a_private_output_file_outside_the_build_directory(monkeypatch, tmp_path: Path):
    seen = _writing(monkeypatch, None)

    _bound("deploy.sh").deploy(tmp_path)

    target = seen["file"]
    assert isinstance(target, Path)
    assert seen["directory_existed"] is True
    assert tmp_path not in target.parents
    assert not target.exists()  # nothing is pre-created: the script writes it or does not


def test_the_scratch_directory_is_removed_after_the_run(monkeypatch, tmp_path: Path):
    seen = _writing(monkeypatch, '{"ip": "10.0.0.5"}')

    _bound("deploy.sh").deploy(tmp_path)

    target = seen["file"]
    assert isinstance(target, Path)
    assert not target.parent.exists()


@pytest.mark.parametrize("phase", ["plan", "apply", "destroy"])
def test_the_output_file_is_set_in_every_executing_phase(monkeypatch, tmp_path: Path, phase):
    """A script that writes outputs unconditionally must not need to branch on the phase."""
    seen = _writing(monkeypatch, None)
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", plan_args=["x"], destroy_args=["x"])

    result = _phase_calls(integration, tmp_path, phase, {"k": "v"})

    assert result.is_successful
    assert isinstance(seen["file"], Path)


def test_output_returns_what_the_script_wrote_as_strings(monkeypatch, tmp_path: Path):
    _writing(monkeypatch, '{"ip": "10.0.0.5", "count": 3, "on": true, "tags": ["a", "b"], "nested": {"k": 1}}')
    integration = _bound("deploy.sh")

    integration.deploy(tmp_path)

    assert _collected(integration, tmp_path) == {
        "ip": {"value": "10.0.0.5", "sensitive": False},
        "count": {"value": "3", "sensitive": False},
        "on": {"value": "true", "sensitive": False},
        "tags": {"value": '["a", "b"]', "sensitive": False},
        "nested": {"value": '{"k": 1}', "sensitive": False},
    }


def test_output_marks_the_listed_keys_sensitive_and_hides_the_marker(monkeypatch, tmp_path: Path):
    _writing(monkeypatch, '{"ip": "10.0.0.5", "token": "abc", "_sensitive": ["token", "not-written"]}')
    integration = _bound("deploy.sh")

    integration.deploy(tmp_path)

    assert _collected(integration, tmp_path) == {
        "ip": {"value": "10.0.0.5", "sensitive": False},
        "token": {"value": "abc", "sensitive": True},
    }


@pytest.mark.parametrize("content", [None, "", "  \n", "{}"])
def test_output_is_empty_when_the_script_wrote_nothing(monkeypatch, tmp_path: Path, content):
    _writing(monkeypatch, content)
    integration = _bound("deploy.sh")

    integration.deploy(tmp_path)

    assert _collected(integration, tmp_path) == {}


def test_output_is_empty_before_any_deploy(tmp_path: Path):
    assert _collected(_bound("deploy.sh"), tmp_path) == {}


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1, 2]",
        '"text"',
        '{"a": 1, "_sensitive": "a"}',
        '{"a": 1, "_sensitive": [1]}',
        '{"bad key": 1}',
        '{"a\\"b": 1}',
    ],
)
def test_a_malformed_output_file_is_reported_and_none_of_it_is_used(monkeypatch, tmp_path: Path, content):
    """Fail closed: a half-read file could drop the `_sensitive` marker and let a secret into the store."""
    _writing(monkeypatch, content)
    integration = _bound("deploy.sh")

    deployed = integration.deploy(tmp_path)
    collected = integration.output(tmp_path, json_format=True)

    assert deployed.is_successful  # the step itself still succeeded
    assert not collected.is_successful
    assert collected.stdout == ""
    assert "STRATA_OUTPUT_FILE" in collected.stderr


def test_a_failed_script_leaves_no_outputs(monkeypatch, tmp_path: Path):
    _writing(monkeypatch, '{"ip": "10.0.0.5"}', returncode=1)
    integration = _bound("deploy.sh")

    assert not integration.deploy(tmp_path).is_successful

    assert _collected(integration, tmp_path) == {}


def test_a_second_deploy_does_not_keep_the_first_ones_outputs(monkeypatch, tmp_path: Path):
    integration = _bound("deploy.sh", args=["${var:ABSENT}"])
    _writing(monkeypatch, '{"ip": "10.0.0.5"}')
    assert integration.deploy(tmp_path, resolved_values={"ABSENT": "x"}).is_successful
    assert _collected(integration, tmp_path) != {}

    failed = integration.deploy(tmp_path, resolved_values={})  # the token no longer resolves: nothing runs

    assert not failed.is_successful
    assert _collected(integration, tmp_path) == {}


def test_a_previews_or_a_destroys_outputs_are_not_collected(monkeypatch, tmp_path: Path):
    _writing(monkeypatch, '{"ip": "10.0.0.5"}')
    _script_in(tmp_path, "deploy.sh")
    integration = _bound("deploy.sh", plan_args=["x"], destroy_args=["x"])

    integration.plan(tmp_path, dry_run=True)
    integration.destroy(tmp_path)

    assert _collected(integration, tmp_path) == {}


def test_output_accepts_what_collect_step_outputs_passes(monkeypatch, tmp_path: Path):
    """The controller passes `auth` and `resolved_values` for a script step too."""
    _writing(monkeypatch, '{"ip": "10.0.0.5"}')
    integration = _bound("deploy.sh")
    integration.deploy(tmp_path)

    result = integration.output(tmp_path, json_format=True, env={}, auth=None, resolved_values={"k": "v"})

    assert result.is_successful
