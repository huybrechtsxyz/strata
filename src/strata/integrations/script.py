#!/usr/bin/env python3
"""Script integration (docs/work/script-provisioner.md) — the first real
executor for `ProvisionerType.SCRIPT`, long reserved but, until now, dead:
`tool: script` passed `strata validate` cleanly and then failed at `build
run`/`deploy run` with a plain "no integration registered" error.

Unlike every other `InfraIntegration`, this class has no fixed binary
(`COMMAND`) — the interpreter needed depends on the bound provisioner's own
`script.file` extension (`.ps1`/`.sh`/`.bash`/`.py`/`.bat`), resolved via
`bind()` (`Integration.bind()`, base no-op elsewhere, docs/work/
script-provisioner.md Phase 2) rather than a class-level constant.

`init()` is deliberately not defined — there is no provider/backend/state
for a script to initialize (see the design doc's init/validate/plan/deploy
mapping table). `validate()` is real: a per-interpreter no-exec syntax
check, closer to Terraform's own "no state/credentials touched" contract
than a bare "not supported" stub. `plan()` is a safe, non-executing preview
— it cannot be a bare raise, since `deploy_controller.py`'s real-apply path
hard-errors when `plan()` returns no preview at all (`plan_or_warn()`'s own
docstring). `destroy()` raises; `strata deploy destroy` doesn't exist for
any tool yet (docs/work/deploy-destroy-command.md).

Value delivery (Phase 4): `default_output()` writes a build-time-only
`strata.auto.json` skeleton (`variables`/`flags`/`properties`/`custom`,
never secrets — unresolved until deploy, ADR-0022 D4), the same
build-time-safe subset `TerraformIntegration`'s own `_strata_variables()`/
`_strata_flags()` compute. Unlike Terraform, this file is never rewritten
at deploy time — found during implementation that Terraform's own
"variables"/"flags" categories in `strata.auto.tfvars.json` aren't
rewritten at deploy time either (only its *resources/namespaces* graph
portion is, via `rewrite_strata_payload()`, which script's file doesn't
carry); the real deploy-time delivery is instead a `STRATA_VALUES` env var
(JSON-encoded, the full resolved `tokens` map — vars, secrets, features,
upstream outputs alike) built fresh in `deploy()`/`plan()` from the
`resolved_values` kwarg every `InfraIntegration.plan()`/`.deploy()` call
already receives generically (`deploy_controller.py`'s existing
`step_resolved_values` plumbing, widened to cover `tool: script` alongside
GitOps). `script.args` entries get the same token resolution. The
generated/templated output file's path is auto-appended as the script's
final argument via the `output_path` kwarg, populated only when
`provisioner.output.template` is set (no magic-filename fallback for the
plain `strata.auto.json` case — that path is a fixed, documented
convention a script can reference directly, `cwd=path` either way).
"""

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.integration_model import Capability, IntegrationModel
from strata.models.provisioning_model import ProvisionerModel, ProvisionerScriptModel
from strata.utils.transport import CommandResult
from strata.utils.value_tokens import resolve_value_tokens


def _interpreter_argv(suffix: str, script_path: Path) -> list[str]:
    """A real binary resolution per extension, not a fixed class-level
    `COMMAND` — the interpreter needed varies per provisioner, not per
    class (see `ScriptIntegration.bind()`).

    Raises:
        IntegrationError: `suffix` has no interpreter dispatch — should not
            happen past `ProvisionerScriptModel.file`'s own extension
            validation; defensive, not primary validation.
    """
    if suffix in (".sh", ".bash"):
        exe = shutil.which("bash")
        if exe is None and os.name == "nt":
            # Git for Windows' installer frequently does not add Git\bin to
            # PATH (confirmed on this repo's own dev machine: bash.exe
            # exists on disk at both locations below, but shutil.which()
            # finds neither) - check its two well-known install locations
            # before giving up, same "known fallback locations" spirit as
            # the .py chain below.
            for candidate in (r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"):
                if Path(candidate).exists():
                    exe = candidate
                    break
        return [exe or "bash", str(script_path)]
    if suffix == ".py":
        # NOT sys.executable - that's strata's OWN interpreter (whichever
        # venv strata itself happens to run in), carrying only strata's own
        # dependencies, not whatever the script needs. Resolved from PATH
        # instead, same as every other interpreter here; sys.executable is
        # a last-resort fallback only, for an environment with neither
        # 'python3' nor 'python' on PATH at all.
        exe = shutil.which("python3") or shutil.which("python") or sys.executable
        return [exe, str(script_path)]
    if suffix == ".ps1":
        # Prefer Core (cross-platform, matches CI runners); fall back to Windows' bundled powershell.exe.
        exe = shutil.which("pwsh") or "powershell"
        return [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script_path)]
    if suffix == ".bat":
        return [str(script_path)]  # cmd.exe associates .bat natively - no wrapper
    raise IntegrationError(f"no interpreter dispatch for '{suffix}' scripts")


class ScriptIntegration(InfraIntegration):
    """Runs a `.ps1`/`.sh`/`.bash`/`.py`/`.bat` script as a provisioning step."""

    TYPE = "script"
    CAPABILITIES = frozenset({Capability.INFRASTRUCTURE})
    TRANSPORTS = frozenset({"cli"})
    # No class-level COMMAND - resolved per-instance from the bound script's extension.

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._script: ProvisionerScriptModel | None = None

    def bind(self, provisioner: ProvisionerModel) -> None:
        """Stashes `provisioner.script` so `command`/`is_available()` can
        resolve the right interpreter (`Integration.bind()`, Phase 2)."""
        self._script = provisioner.script

    @property
    def command(self) -> str | None:
        if self._script is None:
            return None
        script_path = Path(self._script.file)
        return _interpreter_argv(script_path.suffix, script_path)[0]

    def is_available(self) -> bool:
        if self._script is None:
            return False
        suffix = Path(self._script.file).suffix
        if suffix == ".bat":
            return os.name == "nt"  # cmd.exe associates .bat natively; no PATH lookup applies
        try:
            command = self.command
        except IntegrationError:
            return False
        return command is not None and shutil.which(command) is not None

    def default_output(
        self,
        path: Path,
        resolved: ValueResolution,
        provisioner: ProvisionerModel,
        graph: ResolvedWorkspaceGraph,
    ) -> dict[str, str]:
        """Build-time `strata.auto.json` skeleton — `variables`/`flags`/
        `properties`/`custom` only, the same build-time-safe subset
        `TerraformIntegration`'s own `_strata_variables()`/`_strata_flags()`
        compute (`ref.value` is already set for constant/environment-store
        refs at build time; integration-backed/secret refs stay `None` and
        are filtered out, same as there). `resolved`/`provisioner` unused,
        same reasoning `TerraformIntegration.default_output()` already
        documents — these categories come from `graph`, not
        `ValueResolution`. Informational only: real deploy-time delivery is
        `STRATA_VALUES` (see `deploy()`), never a rewrite of this file.
        """
        del resolved
        del provisioner
        payload = {
            "variables": {ref.key: ref.value for ref in graph.variable_refs if ref.value is not None},
            "flags": {ref.key: ref.value for ref in graph.feature_refs if ref.value is not None},
            "properties": graph.properties,
            "custom": graph.custom,
        }
        return {"strata.auto.json": json.dumps(payload)}

    def _resolved_args(self, tokens: Mapping[str, str] | None) -> list[str]:
        """`script.args`, with `${var:}`/`${secret:}`/`${feature:}`/
        `${output:}` tokens resolved — same function `provisioner.backend.
        configuration` already uses. `tokens is None` (no `resolved_values`
        given, e.g. a bare unit-level call) leaves args unresolved rather
        than raising."""
        assert self._script is not None
        args = self._script.args or []
        return args if tokens is None else [resolve_value_tokens(a, dict(tokens)) for a in args]

    @staticmethod
    def _values_env(tokens: Mapping[str, str] | None) -> dict[str, str]:
        """`STRATA_VALUES` — the full resolved `tokens` map (vars, secrets,
        features, upstream outputs alike), JSON-encoded, delivered only via
        an env var, never written to a file — same reasoning Terraform's
        own `TF_VAR_*` delivery already accepts (a process's own
        environment, not disk). Not split secret-vs-non-secret: a flat
        `tokens` dict carries no such distinction by the time it reaches
        here (docs/work/script-provisioner.md Phase 4's own finding).
        `{}` when `tokens` is `None` — no mechanism to deliver, nothing
        silently injected."""
        return {} if tokens is None else {"STRATA_VALUES": json.dumps(dict(tokens))}

    def validate(
        self,
        path: Path,
        *,
        env: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """Syntax-check only — no execution, no side effects. Matches
        `terraform validate`'s own "no state/credentials touched" contract,
        one no-exec invocation per interpreter."""
        assert self._script is not None
        script_path = path / self._script.file
        suffix = script_path.suffix
        if suffix == ".bat":
            # cmd.exe has no syntax-check-only mode - nothing to validate without running it.
            return CommandResult(returncode=0, stdout="", stderr="")
        interpreter = _interpreter_argv(suffix, script_path)[0]
        if suffix == ".py":
            argv = [interpreter, "-m", "py_compile", str(script_path)]
        elif suffix in (".sh", ".bash"):
            argv = [interpreter, "-n", str(script_path)]
        else:  # .ps1 - parse only, via the AST parser, never invokes the script body
            parse = (
                "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile"
                f"('{script_path}',[ref]$null,[ref]$e); if($e){{$e|%{{Write-Error $_}};exit 1}}"
            )
            argv = [interpreter, "-NoProfile", "-Command", parse]
        return self.run(*argv[1:], cwd=path, env=env, line_callback=line_callback)

    def plan(
        self,
        path: Path,
        *,
        env: Mapping[str, str] | None = None,
        resolved_values: Mapping[str, str] | None = None,
        output_path: Path | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        **kwargs: Any,
    ) -> CommandResult:
        """Dry preview: confirms the script exists and its interpreter
        resolves — no execution. Closest real analog available, same
        honesty level as Compose's own `docker stack config` (renders,
        doesn't diff)."""
        assert self._script is not None
        script_path = path / self._script.file
        if not script_path.exists():
            return CommandResult(returncode=1, stdout="", stderr=f"script not found: {script_path}")
        argv = _interpreter_argv(script_path.suffix, script_path) + self._resolved_args(resolved_values)
        if output_path is not None:
            argv = [*argv, str(output_path)]
        return CommandResult(returncode=0, stdout=f"would run: {' '.join(argv)}", stderr="")

    def deploy(
        self,
        path: Path,
        *,
        env: Mapping[str, str] | None = None,
        resolved_values: Mapping[str, str] | None = None,
        output_path: Path | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> CommandResult:
        assert self._script is not None
        script_path = path / self._script.file
        argv = _interpreter_argv(script_path.suffix, script_path) + self._resolved_args(resolved_values)
        if output_path is not None:
            argv = [*argv, str(output_path)]
        run_env = {**(env or {}), **self._values_env(resolved_values)}
        return self.run(*argv[1:], cwd=path, env=run_env, timeout=timeout, line_callback=line_callback)
        # self.run() prepends self.command - argv[0] already equals that by construction

    def destroy(self, path: Path, **kwargs: Any) -> CommandResult:
        raise IntegrationError(f"{self.name} does not support destroy (docs/work/deploy-destroy-command.md)")
