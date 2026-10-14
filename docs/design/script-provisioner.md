# `script` Provisioner Type — Design

- Status: current
- Last updated: 2026-10-14

## Overview

`ProvisionerType.SCRIPT` ("script") has been a decided, recognized
built-in tool since ADR-0009/ADR-0011, but had no executor at all: a real
`tool: script` provisioner passed `strata validate` cleanly and then
failed at `build run`/`deploy run` with a plain "no integration
registered for type 'script'" `UsageError`. This doc covers the
`ScriptIntegration` built to close that gap — a minimal, real executor
for `.ps1`/`.sh`/`.bash`/`.py`/`.bat` scripts, run as an ordinary
provisioning step (ordinary `execution:`/`depends_on` ordering already
gives "run this before/after that" for free, so this needed no separate
phase-hook subsystem — see [lifecycle.md](../work/lifecycle.md), the doc
this one was split out of, for that investigation's own
zero-real-demand finding).

**Deliberately out of scope:**

- `Module.spec.type: "script"` (the namespace/workload-deployer path,
  `resolve_module_integration()`/`prepare_namespace()`/`deploy_namespace()`)
  — `script` already being a `WORKLOAD_DEPLOYER_TYPES` member means this is
  schema-reachable today, but only the **Provisioner** path is built.
  `ScriptIntegration` doesn't implement `prepare_namespace()`/
  `deploy_namespace()`; the base class's "not supported" raise reports
  this safely. Revisit if a real namespace-attached script-module need
  shows up.
- `strata deploy destroy` — doesn't exist for any tool yet
  ([deploy-destroy-command.md](deploy-destroy-command.md));
  `ScriptIntegration.destroy()` raises `IntegrationError`, matching every
  other integration's "not supported" precedent.
- `.js`/`.mjs`/`.go` (in the general `SCRIPT_EXTENSIONS` constant, used by
  lifecycle hooks' `ScriptPathModel`) — a narrower, dedicated extension
  set for this tool (`ps1`/`sh`/`bash`/`py`/`bat`), not the shared one.
- Resolved-value delivery into `script.args`/env does not distinguish a
  secret from a non-secret value (see Value delivery below) — accepted,
  not a gap: the same risk profile Terraform's own `TF_VAR_*` delivery
  already carries.
- A `strata validate`-time existence check for `script.file` — deliberately
  not checked, matching `ScriptPathModel`'s own "may live in a remote not
  yet materialised" precedent.

## Current Design

### Schema — `ProvisionerModel.script`

One new field, following the established one-field-per-tool-concern
pattern already used for `backend` (terraform-only), `properties`
(ansible-only), `gitops` (sync-only) — never a shared/overloaded field:

```python
#: Narrower than the general SCRIPT_EXTENSIONS (common_models.py) — this
#: tool's own supported interpreters only (ps1/sh/bash/py/bat), not the
#: lifecycle-hook set's .js/.mjs/.go.
SCRIPT_PROVISIONER_EXTENSIONS = frozenset({".ps1", ".sh", ".bash", ".py", ".bat"})


class ProvisionerScriptModel(PlatformBaseModel):
    """Only valid when a Provisioner's `tool` is 'script'."""

    file: str = Field(description="Script path, relative to this provisioner's 'source' directory")
    args: list[str] | None = Field(None, description="Arguments passed to the script")

    @field_validator("file")
    @classmethod
    def validate_file(cls, v: str) -> str:
        """Solution-relative containment + a supported extension — reuses
        `validate_script_file()` (common_models.py), same function
        `ScriptPathModel.file` already calls, narrowed to this tool's own
        extension set. Filesystem existence is deliberately not checked —
        same precedent as `ScriptPathModel`: the file may live in a remote
        not yet materialised."""
        return validate_script_file(v, allowed=SCRIPT_PROVISIONER_EXTENSIONS)
```

On `ProvisionerModel`:

```python
script: ProvisionerScriptModel | None = Field(
    None, description="Script file + args to run. Required when tool is 'script'."
)

@model_validator(mode="after")
def validate_script_required_and_scoped(self) -> "ProvisionerModel":
    """`script` is required for tool 'script' (nothing else says what to
    run) and rejected for every other *recognized* built-in — same
    recognized-vs-custom leniency as `validate_backend_only_for_terraform`/
    `validate_properties_only_for_ansible` above."""
    try:
        known = ProvisionerType(self.tool)
    except ValueError:
        return self  # unrecognized — plugin's own code decides
    if known == ProvisionerType.SCRIPT and self.script is None:
        raise ValueError(f"Provisioner '{self.name}': 'script' is required for tool 'script'.")
    if known != ProvisionerType.SCRIPT and self.script is not None:
        raise ValueError(f"Provisioner '{self.name}': 'script' is only valid for tool 'script'.")
    return self
```

`source` stays required and unchanged — `validate_source_required_unless_sync()`
already requires it for any recognized non-sync tool, `script` included
(it's not in `SYNC_PROVISIONER_TYPES`). `source.source_path` names the
directory `sync_source()` materialises; `script.file` is relative to that
materialised directory, exactly how Ansible's `properties.playbook` already
works relative to its own synced source.

### `bind()` — a new `Integration` hook for provisioner-specific context

Every other `InfraIntegration` resolves `is_available()` from a single
fixed class-level `COMMAND` (e.g. Compose's `COMMAND = "docker"`). Script
has no such fixed binary — which interpreter is needed depends on the
specific provisioner's `script.file` extension, and `resolve_integration()`
constructs every integration uniformly (`config: IntegrationModel | None`
only, no provisioner). Rather than changing that shared construction
signature for every integration, `Integration` (`base.py`) gained one
small, optional, additive hook:

```python
def bind(self, provisioner: ProvisionerModel) -> None:
    """Optional: give an integration instance provisioner-specific context
    it needs before `is_available()`/`command` can resolve (script's own
    use case — which interpreter a specific script file needs). Base
    no-op; every existing integration is unaffected."""
    del provisioner
```

`resolve_integration()` (`integration_resolution.py`) calls it once,
unconditionally, right before returning — the same `getattr`-optional-hook
spirit `deploy_controller.py` already uses for `init`/`validate`, just
promoted to a real base-class no-op method instead of a `getattr` at the
call site.

### Interpreter dispatch

A real binary resolution per extension, not a fixed class-level `COMMAND`
— the interpreter needed varies per provisioner, not per class:

```python
def _interpreter_argv(suffix: str, script_path: Path) -> list[str]:
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
```

`.bat` is Windows-only by nature (no interpreter exists on POSIX) — its
availability check is "are we on Windows," not "is some binary on PATH."
`pwsh`/`powershell` and the Git-Bash-location fallback were both verified
against a real environment (see History).

```python
class ScriptIntegration(InfraIntegration):
    TYPE = "script"
    CAPABILITIES = frozenset({Capability.INFRASTRUCTURE})
    TRANSPORTS = frozenset({"cli"})
    # No class-level COMMAND — resolved per-instance from the bound script's extension.

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._script: ProvisionerScriptModel | None = None

    def bind(self, provisioner: ProvisionerModel) -> None:
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
            return os.name == "nt"
        try:
            command = self.command
        except IntegrationError:
            return False
        return command is not None and shutil.which(command) is not None
```

### `init()`/`validate()`/`plan()`/`deploy()`/`destroy()` — what each stage means for a script

The per-step loop (`deploy_controller.py`) runs every step through up to
four stages in order — `init` (optional), `validate` (optional), `plan`
(mandatory-shaped: a real apply hard-errors if it returns `None`),
`deploy` — plus a fifth, `is_available()`, called once per step as a
**preflight** before the loop even starts. Mapped onto Terraform's own
vocabulary, to be explicit about which are real and which are genuinely
not applicable rather than silently absent:

| Stage      | Terraform does                                     | Script does                                                                                                                                                                                             |
| ---------- | --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| preflight  | `which terraform`                                   | `is_available()` — already generic, resolves via the bound script's extension                                                                                                                         |
| `init`     | download providers, set up backend/state            | **N/A — omitted.** No providers/backend/state exist for a script; there is nothing to initialize                                                                                                       |
| `validate` | syntax-check the HCL, no state/credentials touched   | **Real** — a per-interpreter no-exec syntax check (below)                                                                                                                                               |
| `plan`     | preview the diff, no apply                           | **Real** — confirms the file exists and the interpreter resolves; no true diff is possible for an imperative script, same honesty level as Compose's own `docker stack config` (renders, doesn't diff) |
| `deploy`   | apply the plan                                       | **Real** — actually executes the script                                                                                                                                                                 |
| `destroy`  | tear down                                            | Deferred — raises (out of scope above)                                                                                                                                                                  |

`plan()` cannot be a bare "not supported" raise — a missing plan preview
is a **hard error on a real (non-dry-run) apply**, not a graceful skip
(`plan_or_warn()`'s skip-on-`None` branch only applies under `--dry-run`).
`validate()` is optional (`getattr`-based) but, unlike `init()`, has a
cheap, real no-exec analog per interpreter, so it's implemented rather
than left out — verified against a real `pwsh` (see History) that the
`.ps1` parse-only check correctly reports a non-zero exit on real invalid
syntax and passes silently on valid syntax:

```python
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

def destroy(self, path: Path, **kwargs: Any) -> CommandResult:
    raise IntegrationError(f"{self.name} does not support destroy (docs/work/deploy-destroy-command.md)")
```

`plan()`/`deploy()` are covered together with value delivery below, since
both accept the same two value-delivery kwargs.

### Value delivery — build-time skeleton + `STRATA_VALUES` env var + `args` resolution

A file reads identically from every interpreter (`json.load`/
`ConvertFrom-Json`/`jq`), unlike env vars, which also force a key-name
sanitisation decision strata's dotted/hyphenated keys don't naturally
survive — so a generated JSON file is the right shape for the build-time
skeleton:

- **`default_output()`** (base-implemented hook every `InfraIntegration`
  gets for free, `TerraformIntegration`'s own precedent) — overridden to
  write `strata.auto.json` at **build time** with the build-time-safe
  subset only (`variables`/`flags`/`properties`/`custom` — never secrets,
  unresolved until deploy, ADR-0022 D4). **Never rewritten at deploy
  time** — Terraform's own `strata.auto.tfvars.json` isn't rewritten at
  deploy time in this shape either; only its *resources/namespaces* graph
  portion is (`rewrite_strata_payload()`), which script's file doesn't
  carry at all. This file is informational only; the real deploy-time
  channel is `STRATA_VALUES` below.
- **`output.template`/`render_output_template()`** (docs/design/
  value-token-resolution.md Mechanism C) — already fully generic, zero new
  code needed. A provisioner that sets `output.template` gets a
  custom-shaped rendered file instead (same mutual exclusivity
  `prepare()`'s own docstring establishes), for a script author who wants
  a different shape/subset than the default.

At **deploy time**, every `InfraIntegration.plan()`/`.deploy()` call
already receives a generic `resolved_values` kwarg (`deploy_controller.py`
threads it through `plan_or_warn()`/`deploy()` unconditionally for every
tool — previously only ever populated for GitOps). Widening that existing
gate to also cover `provisioner.tool == ProvisionerType.SCRIPT` (using
`tokens`, not bare `resolved.values` — script wants upstream step outputs
too) was the entire controller change needed for this half.

`ScriptIntegration` uses `resolved_values` for two things:

- **`STRATA_VALUES`** — an env var carrying the *entire* resolved `tokens`
  map (vars, secrets, features, upstream outputs alike), JSON-encoded,
  built fresh in `deploy()`/`plan()`. A flat `tokens` dict carries no
  secret-vs-non-secret distinction by the time it reaches an integration
  (that knowledge lives in `graph.secret_refs`, never passed to
  `deploy()`), so delivering the whole map via one env var — never a file
  — is both simpler and the only buildable option, at the same risk
  profile Terraform's own `TF_VAR_*` delivery already accepts.
- **`script.args` token resolution** — `${var:}`/`${secret:}`/`${feature:}`/
  `${output:}` tokens inside each `args` entry, resolved via
  `resolve_value_tokens()` (the same function `provisioner.backend.
  configuration` already uses) — explicit, visible, CLI-shaped parameters
  as an alternative/complement to reading `STRATA_VALUES`.

The `output.template` render's path is auto-appended as the script's
final argument, via an `output_path` kwarg populated **only** when
`provisioner.output.template` is set (`deploy_controller.py` captures
`render_output_template()`'s return value) — no magic-filename fallback
for the plain `strata.auto.json` case; that filename is a fixed,
documented convention a script can reference directly (`deploy()` always
runs with `cwd=path`).

```python
def _resolved_args(self, tokens: Mapping[str, str] | None) -> list[str]:
    """`tokens is None` (no `resolved_values` given, e.g. a bare
    unit-level call) leaves args unresolved rather than raising."""
    assert self._script is not None
    args = self._script.args or []
    return args if tokens is None else [resolve_value_tokens(a, dict(tokens)) for a in args]

@staticmethod
def _values_env(tokens: Mapping[str, str] | None) -> dict[str, str]:
    """`{}` when `tokens` is `None` — no mechanism to deliver, nothing
    silently injected."""
    return {} if tokens is None else {"STRATA_VALUES": json.dumps(dict(tokens))}

def default_output(
    self,
    path: Path,
    resolved: ValueResolution,
    provisioner: ProvisionerModel,
    graph: ResolvedWorkspaceGraph,
) -> dict[str, str]:
    """`resolved`/`provisioner` unused, same reasoning `TerraformIntegration.
    default_output()` documents — these categories come from `graph`."""
    del resolved
    del provisioner
    payload = {
        "variables": {ref.key: ref.value for ref in graph.variable_refs if ref.value is not None},
        "flags": {ref.key: ref.value for ref in graph.feature_refs if ref.value is not None},
        "properties": graph.properties,
        "custom": graph.custom,
    }
    return {"strata.auto.json": json.dumps(payload)}

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
    resolves — no execution. Closest real analog available, same honesty
    level as Compose's own `docker stack config` (renders, doesn't diff)."""
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
```

### Registration

One entry in `registry.py`'s `_KNOWN`:

```python
"script": ("strata.integrations.script", "ScriptIntegration"),
```

### `build run`

`script`'s `source.source_path` materialises through the existing generic
`sync_source()` path, identical to every other provisioner. `prepare()`
itself is unchanged (base dispatch already calls `default_output()` for
every tool); only `default_output()` is overridden, to write the
build-time `strata.auto.json` skeleton described above (matches
`TerraformIntegration`'s own override, not a new pattern).

### `deploy run`

The per-step loop:

- Skips `init` via `getattr(..., None)` — not defined, matching Compose/
  Helm exactly.
- Calls `validate()` (real for script, still `getattr`-optional for every
  other integration that doesn't define it).
- Calls `plan_or_warn()` then `deploy()` generically for any non-container
  `InfraIntegration` — script's own `plan()`/`deploy()` slot in exactly
  like Terraform/Ansible/Bicep would. `resolved_values`/`auth` are already
  threaded into both calls unconditionally for every tool.
- Skips the `ENV_VAR_PREFIX`-gated `TF_VAR_*`/`strata.auto.tfvars.json`
  delivery block automatically (`ENV_VAR_PREFIX = None`) — script's own
  payload/env delivery is a fully separate mechanism, not that block
  reused.

Two small, additive controller changes:

- Widen the `step_resolved_values` gate (`if provisioner.gitops is not
  None: ... elif provisioner.tool == ProvisionerType.SCRIPT.value: ...`)
  to set it to `tokens` (not bare `resolved.values`) for a script step.
- Capture `render_output_template()`'s previously-discarded return value
  into `rendered_output_path`, threaded through as `output_path` to both
  `plan_or_warn()` and `deploy()` — harmless for every other integration
  via `**kwargs`.

## Related Decisions

- [ADR-0009](../decisions/0009-module-model-design-decisions.md) /
  [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) —
  `ProvisionerType.SCRIPT`'s original decision, `WORKLOAD_DEPLOYER_TYPES`
  membership (the still-out-of-scope module/namespace path).
- [ADR-0021](../decisions/0021-integration-layer.md) — the `Integration`/
  `InfraIntegration` contract this class implements; D11's interpreter
  map (`.py`-only for `Integration.lifecycle`) is a narrower, unrelated
  case (that's a *hook*, not a provisioner).
- [lifecycle.md](../work/lifecycle.md) — the doc this one was split out
  of; its gate-question finding (zero real lifecycle-hook demand) is why
  this doc doesn't port v1's phase-hook executor, only the plain "run a
  script" primitive.
- [deploy-destroy-command.md](deploy-destroy-command.md) — why
  `destroy()` raises rather than attempting anything.

## History

- `.py`'s interpreter must not default to `sys.executable` — that's
  strata's own interpreter (whatever venv/install strata itself happens to
  run under), carrying only strata's own dependencies, not whatever the
  script needs. Resolved via `PATH` (`python3`/`python`) instead, same as
  every other interpreter here; `sys.executable` is only a last-resort
  fallback for an environment with neither on PATH at all.
- Value delivery was originally drafted as a deploy-time rewrite of the
  build-time file plus a secrets-only `STRATA_SECRETS` env var split. Both
  turned out unbuildable once actually implemented: Terraform's own
  `strata.auto.tfvars.json` isn't rewritten at deploy time in this shape
  either (only its *resources/namespaces* graph portion is, which script's
  file doesn't carry), and a flat `resolved_values`/`tokens` dict carries
  no secret-vs-non-secret distinction by the time it reaches an
  integration (that knowledge lives in `graph.secret_refs`, never passed
  to `deploy()`). Revised to the simpler, buildable design actually
  shipped: the build-time file stays build-time-only and informational;
  `STRATA_VALUES` delivers the *entire* resolved map via one env var,
  never a file.
- `pwsh`/`powershell` availability and the `.ps1` `validate()` parse-only
  `[System.Management.Automation.Language.Parser]::ParseFile` one-liner
  were both verified against a real Windows environment (not just assumed
  from CI conventions): `pwsh` resolves at its default install location,
  `powershell` at its always-present bundled one, and the parse-only check
  correctly detects a real invalid-syntax script (non-zero exit, a useful
  message) while passing silently on valid syntax.
- Windows `bash` resolution for `.sh`/`.bash` needed a real fallback, not
  just a researched assumption: confirmed directly that Git for Windows'
  installer frequently does not add `Git\bin` to `PATH` even when Git
  itself is installed (reproduced on this repo's own dev machine —
  `bash.exe` exists on disk at the standard install location, but
  `shutil.which("bash")` found neither it nor the alternate `Git\usr\bin`
  location). `_interpreter_argv()` now checks both well-known install
  locations before falling back to the bare `"bash"` literal.
