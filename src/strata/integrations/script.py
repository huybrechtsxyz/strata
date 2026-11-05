#!/usr/bin/env python3
"""Script integration (docs/design/script-provisioner.md) — the first real
executor for `ProvisionerType.SCRIPT`, long reserved but, until now, dead:
`tool: script` passed `strata validate` cleanly and then failed at `build
run`/`deploy run` with a plain "no integration registered" error.

Unlike every other `InfraIntegration`, this class has no fixed binary
(`COMMAND`) — the interpreter needed depends on the bound provisioner's own
`script.file` extension (`.ps1`/`.sh`/`.bash`/`.py`/`.bat`), resolved via
`bind()` (`Integration.bind()`, base no-op elsewhere, docs/design/
script-provisioner.md) rather than a class-level constant.

`init()` is deliberately not defined — there is no provider/backend/state
for a script to initialize (see the design doc's init/validate/plan/deploy
mapping table). `validate()` is real: a per-interpreter no-exec syntax
check, closer to Terraform's own "no state/credentials touched" contract
than a bare "not supported" stub. `plan()` is a non-executing `would run:`
preview unless the provisioner sets `plan_args` and a preview was requested
(`dry_run`); it cannot be a bare raise, since `deploy_controller.py`'s real-apply
path hard-errors when `plan()` returns no preview at all (`plan_or_warn()`'s own
docstring). `destroy()` runs `destroy_args`; without them `supports_destroy` is
false and `strata deploy destroy` refuses the step before anything is destroyed
(docs/design/deploy-destroy.md, D5). Every run sets `STRATA_PHASE` and `STRATA_OUTPUT_FILE`; `output()` returns
what the apply wrote to the latter, so later steps can use `${output:step.key}`.

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
upstream outputs alike — or only the keys `script.values` lists) built fresh in `deploy()`/`plan()` from the
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
import re
import shutil
import sys
import tempfile
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from strata.integrations.capabilities import InfraIntegration
from strata.integrations.errors import IntegrationError
from strata.integrations.resolved_context import ResolvedWorkspaceGraph, ValueResolution
from strata.models.integration_model import Capability, IntegrationModel
from strata.models.provisioning_model import ProvisionerModel, ProvisionerScriptModel
from strata.utils.transport import CommandResult
from strata.utils.value_tokens import is_deployment_entry, resolve_value_tokens

#: Reserved key of the file a script writes to `STRATA_OUTPUT_FILE`: a list of the output keys that are secret.
_SENSITIVE_KEY = "_sensitive"

#: The characters a `${output:step.key}` token key may hold (utils/value_tokens.py).
_OUTPUT_KEY = re.compile(r"[A-Za-z0-9_.-]+")


@contextmanager
def _scratch_output_file() -> Iterator[Path]:
    """A path in a private directory outside the build tree, removed afterwards, so a secret output never lingers."""
    directory = Path(tempfile.mkdtemp(prefix="strata-script-"))
    try:
        yield directory / "outputs.json"
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _read_outputs(output_file: Path) -> CommandResult:
    """What `output()` reports for the file a script wrote: Terraform's `output -json` shape, or a failure.

    Fails closed: a file that cannot be read in full yields no outputs at all, because a half-read file could lose the
    `_sensitive` marker and let a secret into the output store.
    """

    def _problem(reason: str) -> CommandResult:
        return CommandResult(
            returncode=1, stdout="", stderr=f"the file the script wrote to STRATA_OUTPUT_FILE {reason}; ignored"
        )

    if not output_file.exists():
        return CommandResult(returncode=0, stdout="{}", stderr="")
    try:
        text = output_file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _problem(f"cannot be read ({exc})")
    if not text.strip():
        return CommandResult(returncode=0, stdout="{}", stderr="")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return _problem(f"is not valid JSON ({exc.msg})")
    if not isinstance(parsed, dict):
        return _problem("is not a JSON object")
    marked = parsed.pop(_SENSITIVE_KEY, [])
    if not isinstance(marked, list) or not all(isinstance(item, str) for item in marked):
        return _problem(f"has a '{_SENSITIVE_KEY}' that is not a list of key names")
    invalid = [key for key in parsed if not _OUTPUT_KEY.fullmatch(key)]
    if invalid:
        return _problem(f"has keys a token cannot name: {invalid}")
    outputs = {
        key: {"value": value if isinstance(value, str) else json.dumps(value), "sensitive": key in marked}
        for key, value in parsed.items()
    }
    return CommandResult(returncode=0, stdout=json.dumps(outputs), stderr="")


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
    SUPPORTS_DESTROY = False
    SUPPORTS_SAVED_PLAN = False
    # No class-level COMMAND - resolved per-instance from the bound script's extension.

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._script: ProvisionerScriptModel | None = None
        self._outputs: CommandResult | None = None  # what the last apply wrote, for `output()`

    def bind(self, provisioner: ProvisionerModel) -> None:
        """Stashes `provisioner.script` so `command`/`is_available()` can
        resolve the right interpreter (`Integration.bind()`, Phase 2)."""
        self._script = provisioner.script

    @property
    def supports_destroy(self) -> bool:
        """A script can be destroyed only when its provisioner sets `destroy_args`; without them there is nothing to
        run (docs/design/script-provisioner.md)."""
        return self._script is not None and self._script.destroy_args is not None

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

    def _resolved_args(self, tokens: Mapping[str, str] | None, phase_args: list[str] | None = None) -> list[str]:
        """`phase_args` (default `script.args`), with `${var:}`/`${secret:}`/`${feature:}`/
        `${output:}` tokens resolved — same function `provisioner.backend.
        configuration` already uses. `tokens is None` (no `resolved_values`
        given, e.g. a bare unit-level call) leaves args unresolved rather
        than raising.

        Raises:
            ValueError: a token names a value `tokens` does not have.
        """
        assert self._script is not None
        chosen = (self._script.args if phase_args is None else phase_args) or []
        if tokens is None:
            return list(chosen)
        values = dict(tokens)  # built once, not once per arg
        return [resolve_value_tokens(a, values) for a in chosen]

    def _argv(
        self,
        script_path: Path,
        tokens: Mapping[str, str] | None,
        phase_args: list[str] | None = None,
        output_path: Path | None = None,
    ) -> list[str]:
        """Interpreter, script, the phase's resolved arguments, and the rendered output path when there is one.

        Raises:
            ValueError: see `_resolved_args()`.
        """
        argv = _interpreter_argv(script_path.suffix, script_path) + self._resolved_args(tokens, phase_args)
        return argv if output_path is None else [*argv, str(output_path)]

    def _delivered(self, tokens: Mapping[str, str] | None) -> Mapping[str, str] | None:
        """The part of `tokens` that reaches `STRATA_VALUES`: all of it, or with `script.values` only the listed keys
        and the `deployment:` entries.

        Raises:
            ValueError: a listed key is not in `tokens`.
        """
        assert self._script is not None
        listed = self._script.values
        if tokens is None or listed is None:
            return tokens
        missing = [key for key in listed if key not in tokens]
        if missing:
            names = ", ".join(f"'{key}'" for key in missing)
            raise ValueError(f"script.values lists {names}, which did not resolve to a value.")
        return {key: value for key, value in tokens.items() if key in listed or is_deployment_entry(key)}

    @staticmethod
    def _run_env(
        env: Mapping[str, str] | None, tokens: Mapping[str, str] | None, phase: str, output_file: Path
    ) -> dict[str, str]:
        """The caller's environment, `STRATA_VALUES`, `STRATA_PHASE` and `STRATA_OUTPUT_FILE`.

        `STRATA_PHASE` (`plan`, `apply` or `destroy`) is independent of the arguments, so a script that changes
        things can refuse an action that does not fit the phase it was started in, whatever `plan_args` says.
        `STRATA_OUTPUT_FILE` is set in every phase so a script can write its outputs without branching; only the
        apply's file is read.
        """
        return {
            **(env or {}),
            **ScriptIntegration._values_env(tokens),
            "STRATA_PHASE": phase,
            "STRATA_OUTPUT_FILE": str(output_file),
        }

    @staticmethod
    def _values_env(tokens: Mapping[str, str] | None) -> dict[str, str]:
        """`STRATA_VALUES` — the `tokens` it is given (`_delivered()` has already applied `script.values`; vars, secrets,
        features, upstream outputs alike), JSON-encoded, delivered only via
        an env var, never written to a file — same reasoning Terraform's
        own `TF_VAR_*` delivery already accepts (a process's own
        environment, not disk). Not split secret-vs-non-secret: a flat
        `tokens` dict carries no such distinction by the time it reaches
        here (docs/design/script-provisioner.md's own finding).
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
            # Single quotes doubled - a script.file containing one (schema
            # validation only checks path traversal/extension, not this)
            # would otherwise break out of the single-quoted PowerShell
            # string and inject arbitrary code into this -Command string.
            escaped_path = str(script_path).replace("'", "''")
            parse = (
                "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile"
                f"('{escaped_path}',[ref]$null,[ref]$e); if($e){{$e|%{{Write-Error $_}};exit 1}}"
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
        dry_run: bool = False,
        timeout: int = 300,
        **kwargs: Any,
    ) -> CommandResult:
        """A preview. Runs the script with `plan_args` when a preview was asked for (`dry_run`) and `plan_args` is
        set; otherwise it only confirms the script exists and prints what would run, executing nothing — the closest
        real analog available, same honesty level as Compose's own `docker stack config`.

        A real `deploy run` calls this too, as the step before the apply, and never runs `plan_args` there: that
        would execute the script twice on every run.
        """
        assert self._script is not None
        script_path = path / self._script.file
        if not script_path.exists():
            return CommandResult(returncode=1, stdout="", stderr=f"script not found: {script_path}")
        executes = dry_run and self._script.plan_args is not None
        try:
            argv = self._argv(script_path, resolved_values, self._script.plan_args if executes else None, output_path)
            delivered = self._delivered(resolved_values)
        except ValueError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        if not executes:
            return CommandResult(returncode=0, stdout=f"would run: {' '.join(argv)}", stderr="")
        with _scratch_output_file() as output_file:
            return self.run(
                *argv[1:],
                cwd=path,
                env=self._run_env(env, delivered, "plan", output_file),
                timeout=timeout,
                line_callback=line_callback,
            )

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
        self._outputs = None
        script_path = path / self._script.file
        try:
            argv = self._argv(script_path, resolved_values, None, output_path)
            delivered = self._delivered(resolved_values)
        except ValueError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        with _scratch_output_file() as output_file:
            result = self.run(
                *argv[1:],
                cwd=path,
                env=self._run_env(env, delivered, "apply", output_file),
                timeout=timeout,
                line_callback=line_callback,
            )
            if result.is_successful:
                self._outputs = _read_outputs(output_file)
        return result
        # self.run() prepends self.command - argv[0] already equals that by construction

    def output(self, path: Path, **kwargs: Any) -> CommandResult:
        """What the last `deploy()` wrote to `STRATA_OUTPUT_FILE`, in the `terraform output -json` shape the
        controller reads (`{key: {"value": ..., "sensitive": ...}}`), so a later step can use `${output:step.key}`.

        Nothing the script wrote is `{}`. A file that is not valid fails the call, and the controller turns that into a
        warning: the step itself already succeeded.
        """
        return CommandResult(returncode=0, stdout="{}", stderr="") if self._outputs is None else self._outputs

    def _cannot_destroy(self) -> IntegrationError:
        return IntegrationError(
            f"{self.name} does not support destroy: set 'destroy_args' on the provisioner's script "
            "(docs/design/script-provisioner.md)"
        )

    def destroy(
        self,
        path: Path,
        *,
        env: Mapping[str, str] | None = None,
        resolved_values: Mapping[str, str] | None = None,
        line_callback: Callable[[str, str], None] | None = None,
        timeout: int = 300,
        **kwargs: Any,
    ) -> CommandResult:
        """Run the script with `destroy_args` and `STRATA_PHASE=destroy`. The rendered output path is not appended:
        a destroy renders nothing.

        Raises:
            IntegrationError: the provisioner sets no `destroy_args`.
        """
        if not self.supports_destroy:
            raise self._cannot_destroy()
        assert self._script is not None and self._script.destroy_args is not None
        script_path = path / self._script.file
        if not script_path.exists():
            return CommandResult(returncode=1, stdout="", stderr=f"script not found: {script_path}")
        try:
            argv = self._argv(script_path, resolved_values, self._script.destroy_args)
            delivered = self._delivered(resolved_values)
        except ValueError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        with _scratch_output_file() as output_file:
            return self.run(
                *argv[1:],
                cwd=path,
                env=self._run_env(env, delivered, "destroy", output_file),
                timeout=timeout,
                line_callback=line_callback,
            )

    def plan_destroy(
        self, path: Path, *, resolved_values: Mapping[str, str] | None = None, **kwargs: Any
    ) -> CommandResult:
        """What `destroy()` would run. Never executes: `plan_args` previews an apply, not a removal, so there is no
        mode of the script that shows what a destroy would do.

        Raises:
            IntegrationError: the provisioner sets no `destroy_args`.
        """
        if not self.supports_destroy:
            raise self._cannot_destroy()
        assert self._script is not None and self._script.destroy_args is not None
        script_path = path / self._script.file
        if not script_path.exists():
            return CommandResult(returncode=1, stdout="", stderr=f"script not found: {script_path}")
        try:
            argv = self._argv(script_path, resolved_values, self._script.destroy_args)
            self._delivered(resolved_values)
        except ValueError as exc:
            return CommandResult(returncode=1, stdout="", stderr=str(exc))
        return CommandResult(returncode=0, stdout=f"would run: {' '.join(argv)}", stderr="")
