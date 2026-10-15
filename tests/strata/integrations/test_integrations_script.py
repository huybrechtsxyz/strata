#!/usr/bin/env python3
"""Tests for `ScriptIntegration` (docs/design/script-provisioner.md)."""

import shutil
import sys
from pathlib import Path

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


def _bound(file: str, args: list[str] | None = None) -> ScriptIntegration:
    provisioner = ProvisionerModel(
        name="script-step",
        tool="script",
        source=SourceModel(source_path="scripts"),
        script=ProvisionerScriptModel(file=file, args=args),
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
    import strata.integrations.script as script_module

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(script_module.os, "name", "nt")
    monkeypatch.setattr(Path, "exists", lambda self: str(self) == r"C:\Program Files\Git\bin\bash.exe")

    assert _interpreter_argv(".sh", Path("deploy.sh")) == [r"C:\Program Files\Git\bin\bash.exe", "deploy.sh"]


def test_interpreter_argv_sh_falls_back_to_bash_literal_when_nothing_resolves(monkeypatch):
    import strata.integrations.script as script_module

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(script_module.os, "name", "posix")  # no Windows fallback paths checked
    assert _interpreter_argv(".sh", Path("deploy.sh")) == ["bash", "deploy.sh"]


def test_interpreter_argv_sh_falls_back_to_bash_literal_on_windows_with_no_git_install(monkeypatch):
    import strata.integrations.script as script_module

    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(script_module.os, "name", "nt")
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
    import strata.integrations.script as script_module

    monkeypatch.setattr(script_module.os, "name", os_name)
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
# destroy() — deferred (docs/work/deploy-destroy-command.md)
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
    assert captured["env"] == {}
