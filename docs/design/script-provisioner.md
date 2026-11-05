# `script` Provisioner Type — Design

- Status: current
- Last updated: 2026-11-05

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

Beyond running the file, a script step has a phase per command (`args`, `plan_args`, `destroy_args`, with
`STRATA_PHASE`), can be limited to the values it needs (`values`), and can hand values to later steps
(`STRATA_OUTPUT_FILE`). Each is described below. A walkthrough for users is
[how-to/script-provisioner.md](../how-to/script-provisioner.md).

**Deliberately out of scope:**

- `Module.spec.type: "script"` (the namespace/workload-deployer path,
  `resolve_module_integration()`/`prepare_namespace()`/`deploy_namespace()`)
  — `script` already being a `WORKLOAD_DEPLOYER_TYPES` member means this is
  schema-reachable today, but only the **Provisioner** path is built.
  `ScriptIntegration` doesn't implement `prepare_namespace()`/
  `deploy_namespace()`; the base class's "not supported" raise reports
  this safely. Revisit if a real namespace-attached script-module need
  shows up.
- `.js`/`.mjs`/`.go` (in the general `SCRIPT_EXTENSIONS` constant, used by
  lifecycle hooks' `ScriptPathModel`) — a narrower, dedicated extension
  set for this tool (`ps1`/`sh`/`bash`/`py`/`bat`), not the shared one.
- Resolved-value delivery into `script.args`/env does not distinguish a
  secret from a non-secret value (see Value delivery below) — accepted,
  not a gap: the same risk profile Terraform's own `TF_VAR_*` delivery
  already carries. `values` narrows what is delivered; it is not a sandbox.
- A `strata validate`-time existence check for `script.file` — deliberately
  not checked, matching `ScriptPathModel`'s own "may live in a remote not
  yet materialised" precedent. `build run` checks it once the source is
  materialised (see Reporting).
- A richer `script:` block: an executable or interpreter of the author's choice (`ansible-playbook`, `kubectl`, a
  compiled program), a per-step `env`, a working directory. No real step needs one yet; add it when one does.
  Until then `.py` runs with the first `python3`/`python` on `PATH`.
- Idempotence or change detection: a script owns its own state, and strata does not guess whether it changed
  anything.
- Destroying through a saved plan: a script writes none.
- `--follow` on a non-UTF-8 pipe on Windows loses the streamed lines because the box-drawing prefix cannot be
  encoded. It affects every tool's `--follow`, not only scripts.

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
    args: list[str] | None = Field(None, description="Arguments for `deploy run` (the apply)")
    plan_args: list[str] | None = Field(None, description="Arguments for a requested preview")
    destroy_args: list[str] | None = Field(None, description="Arguments for `deploy destroy --force`")
    values: list[str] | None = Field(None, description="Keys `STRATA_VALUES` carries; unset means all")

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
    SUPPORTS_DESTROY = False  # the class default; the instance answers from `destroy_args`
    SUPPORTS_SAVED_PLAN = False
    # No class-level COMMAND — resolved per-instance from the bound script's extension.

    def __init__(self, config: IntegrationModel | None = None) -> None:
        super().__init__(config)
        self._script: ProvisionerScriptModel | None = None
        self._outputs: CommandResult | None = None  # what the last apply wrote, for `output()`

    def bind(self, provisioner: ProvisionerModel) -> None:
        self._script = provisioner.script

    @property
    def supports_destroy(self) -> bool:
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

| Stage      | Terraform does                                     | Script does                                                                                                                                                                                     |
| ---------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| preflight  | `which terraform`                                  | `is_available()` — already generic, resolves via the bound script's extension                                                                                                                   |
| `init`     | download providers, set up backend/state           | **N/A — omitted.** No providers/backend/state exist for a script; there is nothing to initialize                                                                                                |
| `validate` | syntax-check the HCL, no state/credentials touched | **Real** — a per-interpreter no-exec syntax check (below)                                                                                                                                       |
| `plan`     | preview the diff, no apply                         | **Real, opt-in** - runs the script with `plan_args` when a preview was asked for and `plan_args` is set; otherwise only confirms the file exists and prints `would run: ...` (see Phases below) |
| `deploy`   | apply the plan                                     | **Real** — actually executes the script and collects its outputs (below)                                                                                                                        |
| `destroy`  | tear down                                          | **Real, opt-in** - runs the script with `destroy_args`; a script without them cannot be destroyed (see Phases below)                                                                            |

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
        # Single quotes doubled - schema validation only checks path
        # traversal/extension, not quote characters in the filename.
        escaped_path = str(script_path).replace("'", "''")
        parse = (
            "$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile"
            f"('{escaped_path}',[ref]$null,[ref]$e); if($e){{$e|%{{Write-Error $_}};exit 1}}"
        )
        argv = [interpreter, "-NoProfile", "-Command", parse]
    return self.run(*argv[1:], cwd=path, env=env, line_callback=line_callback)
```

`plan()`/`deploy()`/`destroy()` are covered under Phases and Value delivery
below, since they share the value-delivery kwargs.

### Phases - `plan_args`, `destroy_args` and `STRATA_PHASE`

A script has one file and no idea which strata command started it, so each phase it can run in gets its own
arguments, and an environment variable names the phase. `args` stays what it always was: the apply.

| Command                                        | Method           | What runs                                                                | `STRATA_PHASE` |
| ---------------------------------------------- | ---------------- | ------------------------------------------------------------------------ | -------------- |
| `deploy run`                                   | `deploy()`       | the script with `args`                                                   | `apply`        |
| `deploy run --dry-run`, `deploy plan`          | `plan()`         | the script with `plan_args`, if set; else only `would run: ...`          | `plan`         |
| `deploy run` (real), the step before the apply | `plan()`         | nothing: only the `would run: ...` line, which a real run does not print | -              |
| `deploy destroy --force`                       | `destroy()`      | the script with `destroy_args`                                           | `destroy`      |
| `deploy destroy --dry-run`                     | `plan_destroy()` | nothing: `would run: <destroy_args>`                                     | -              |
| any command with `--smoke-test`                | -                | nothing: no tool is contacted                                            | -              |

- **`plan_args` run only when a preview was requested.** A real `deploy run` always calls `plan()` before `deploy()`.
  If `plan()` executed the script there, every real run would execute it twice. The controller passes `dry_run` to
  `plan()`; the script integration executes only when it is true and `plan_args` is set. `deploy apply` is refused for
  a script (it writes no saved plan), so `deploy run` is the one real command.
- **`destroy_args` makes a step destroyable.** `SUPPORTS_DESTROY` is a class constant, so an integration whose answer
  depends on its provisioner exposes the `supports_destroy` property instead (the class constant is its default).
  `ScriptIntegration` answers `destroy_args is not None`. The destroy pre-flight reads the property, and also scans
  `destroy_args` for `${output:}` references, which it serves from the output store like a Terraform backend's.
- **`deploy destroy --dry-run` never executes the script.** `plan_args` previews an apply, not a removal, and there
  is no mode of an arbitrary script that shows what a destroy would remove. The honest preview is the command line.
- **The output path.** `args` and `plan_args` get the rendered `output.template` path appended as the last argument,
  as before. `destroy_args` do not: a destroy renders nothing.
- **A token that cannot be resolved** (an `${output:}` nobody produced) fails the call with a message naming it,
  instead of raising.
- **Visibility.** A preview that executes the script streams its output through `--follow`, which `deploy plan` turns
  on by default and `deploy run --dry-run` does not. The `would run: ...` lines print without `--follow`.

**What strata cannot verify.** That a script is free of side effects when run with `plan_args` rests on its author,
as Ansible's `--check` rests on its modules. strata makes the mistake harder to make and easier to see:

1. `plan_args` is opt-in. Unset, a preview never executes anything.
2. `STRATA_PHASE` tells the script which phase it is in, independently of its arguments, so a script that changes
   things can refuse an action that does not fit. For example:

   ```python
   import os, sys
   if os.environ.get("STRATA_PHASE") == "plan" and action != "plan":
       sys.exit(f"refusing '{action}' during a preview")
   ```

3. `--smoke-test` runs nothing, so a pipeline can check a script's wiring before it is trusted.
4. The exact command line is shown (resolved secrets redacted) before a preview executes.

A new script should go through `--smoke-test`, then `--dry-run` in a test environment, before it is scheduled.

**Example** - one script, three actions, chosen by the phase and not by a variable:

```yaml
script:
  file: secrets.py
  args:         [--action, reload, --records, "${var:secrets_records}"]
  plan_args:    [--action, plan,   --records, "${var:secrets_records}"]
  destroy_args: [--action, revoke, --records, "${var:secrets_records}"]
```

### Reporting — previews, failures and `deploy apply`

What a user sees when a script step runs, and where it differs from a tool with a native preview:

- **A preview is shown.** A tool that does not stream its preview has its stdout printed through the step callback
  under `--dry-run` and `deploy plan`: for a script that is `would run: <argv>`, with resolved secrets redacted. A
  preview that was streamed is not printed twice. A real `deploy run` prints no `would run:` line.
- **A successful script is silent without `--follow`**, like every tool: the run prints "deployed step" and nothing
  else. Making scripts the exception was rejected; `--follow` shows the output, and a failure shows its tail.
- **A failure says how it ended.** For every tool, a failed `init`, `validate`, `plan`, `deploy` or `destroy` reports
  `exit code N`, or `timed out and was killed`, then the last 10 lines of stderr and stdout, redacted like streamed
  lines. A script prints its error to stdout as often as to stderr, and a timeout looked like any other failure.
  The limit that was hit is not named: the defaults live in each integration, and the controller only knows a
  configured override.
- **`deploy apply` needs a saved plan.** `InfraIntegration.SUPPORTS_SAVED_PLAN` is true by default and false for a
  script and for GitOps, whose `plan()` writes no file. `deploy apply` refuses such a step in pre-flight, with code
  `saved_plan_unsupported`, naming the step and pointing at `deploy run`. Container steps are exempt, as before.
- **A missing script file** fails `build run`, once the source is materialised, with a usage error naming the
  provisioner and the path. It is not a `validate` check because the source may live in a remote that is not
  materialised yet, and a dry run materialises nothing.

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

- **`STRATA_VALUES`** — an env var carrying the resolved `tokens` map
  (vars, secrets, features, upstream outputs alike; all of it, or the
  subset `script.values` lists, see Value scoping below), JSON-encoded,
  built fresh in `deploy()`/`plan()`/`destroy()`. A flat `tokens` dict carries no
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

def _delivered(self, tokens: Mapping[str, str] | None) -> Mapping[str, str] | None:
    """All of `tokens`, or with `script.values` only the listed keys and the `deployment:` entries.
    Raises ValueError for a listed key that is not in `tokens`."""

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
```

`plan()`, `deploy()` and `destroy()` are covered under Phases above; their
source is `integrations/script.py`.

### Value scoping - `script.values`

By default `STRATA_VALUES` carries every resolved value, so a script that
needs one API token also receives every unrelated secret of the deployment.
`values` is an allow-list that lets a script take only what it needs.

- **Unset** (the default): everything, as before. Nothing existing changes.
- **Set**: `STRATA_VALUES` holds the listed keys, plus the `deployment:name`
  and `deployment:tenant` entries, which identify the run and carry nothing
  secret. They are not listed (the model rejects a key with a colon), they
  are always there. An empty list is valid: only those entries.
- **Keys** are the names in the flat token map: a variable, secret or feature
  key, or `<step>.<key>` for an output of an earlier step. A key follows the
  same character set as a token key.
- **It limits `STRATA_VALUES` only.** `args`, `plan_args` and `destroy_args`
  still resolve any token, so a script can be given one secret as an argument
  and still receive none through the environment. An argument is visible in
  the process list to every user of the machine, an environment variable
  generally only to the same user; prefer `STRATA_VALUES` for a secret.
- **Every phase** gets the same subset, including a preview that executes the
  script and a destroy.
- **A listed key that does not resolve fails the call**, naming the key and
  the step, in `plan()` too, so a real `deploy run` stops before the apply.
  Nothing is delivered partially. An output key of a step the run did not
  reach (a destroy with no `output_store`, say) fails the same way.
- **`strata validate`** checks each listed key per deployment: it must be
  declared by one of the deployment's environments, or be `<step>.<key>`
  where `<step>` is an execution step of the workspace (an output only exists
  at deploy time, so only its step can be checked). Without a resolvable
  environment there is nothing to check against, as for `${var:}` tokens.
  Code: `undeclared_script_value`.
- **What it does not do.** The process still inherits the pipeline's own
  environment, and a script can read any file it can reach. It narrows what
  strata hands over; it is not a sandbox.

### Outputs - `STRATA_OUTPUT_FILE`

A script step can hand values to the steps after it, as a Terraform step does: they become `${output:<step>.<key>}`
for every step that depends on it, and are recorded in the workspace's `output_store` for later invocations.

The script writes one JSON object to the file named by the environment variable `STRATA_OUTPUT_FILE`:

```json
{ "address": "10.0.0.5", "count": 3, "token": "abc", "_sensitive": ["token"] }
```

- **Values** may be any JSON. A string is used as it is; anything else is stored as its JSON text (`3` becomes
  `"3"`, a list becomes `'["a", "b"]'`), exactly as for Terraform outputs.
- **`_sensitive`** is a reserved key listing the keys that are secret. A sensitive output can be used by the steps
  after it in the same run, as a `${secret:}` can, and is never recorded in the output store (ADR-0036 D4). Unlisted
  keys are not sensitive, as in Terraform. The marker is a key inside the file, not a second file, so the two
  cannot get out of step. A key that is not in the file is ignored.
- **Keys** use the characters of a token key (`A-Z a-z 0-9 _ . -`).
- **Nothing written** (no file, an empty file, `{}`) means no outputs and no warning.
- **A file that is not valid** (not JSON, not an object, a `_sensitive` that is not a list of strings, a key a token
  cannot name) yields no outputs at all and a warning naming the step. The step does not fail: it already succeeded,
  as when `terraform output` fails after an apply. It fails closed because a half-read file could lose the
  `_sensitive` marker and put a secret in the store.
- **A script that exits non-zero** produces no outputs.
- **The file is private.** It lives in a new directory made by the operating system for the run, outside the build
  directory, and is removed when the script ends, so a secret output is never left on disk.
- **Every phase sets the variable**, so a script can write its outputs without checking `STRATA_PHASE`. Only the
  apply's file is read: a preview and a destroy collect nothing (a destroy collects no outputs for any tool,
  [deploy-destroy.md](deploy-destroy.md)).
- **How it is wired.** `ScriptIntegration.deploy()` reads the file after a successful run and keeps the result;
  `output()` returns it in the `terraform output -json` shape that `collect_step_outputs()` already reads, so the
  controller, the output store and the token resolution needed no change for scripts. The one controller change is
  that a failed `output()` call now produces a warning with its stderr, for every tool: before, a Terraform or GitOps
  step that lost its outputs said nothing, and a later `${output:}` failed with no explanation.
- **Who can produce outputs.** Only an integration that defines `output()`: Terraform, GitOps and script. Helm and
  Compose steps have none, and a `${output:}` on them never resolves.
- **The pairing of `deploy()` and `output()`.** `output()` returns what the last `deploy()` of the same instance
  wrote; the instance holds it. The controller makes one integration per step and calls the two back to back, so
  nothing else can come between them. An instance that is reused for another step, or asked for `output()` before
  `deploy()`, reports no outputs.
- **Limits.** The script cannot read another step's outputs from the file; it receives them through
  `STRATA_VALUES` (listed in `values` if scoped) or `args`.

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
- [deploy-destroy.md](deploy-destroy.md) — `SUPPORTS_DESTROY` and the destroy preview rules that `destroy_args`
  and `plan_destroy()` follow.
- [deploy-pipeline-stage-commands.md](deploy-pipeline-stage-commands.md) — why `deploy apply` needs a saved plan.
- [ADR-0032](../decisions/0032-deploy-time-step-timeout-overrides.md) — the per-phase timeouts (`plan`, `apply`,
  `destroy`) a script honours.
- [ADR-0036](../decisions/0036-cross-run-key-value-output-store.md) — the output store, and why a sensitive output
  is never recorded.

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
- `validate()`'s `.ps1` parse-only `-Command` string interpolates
  `script_path` directly into a single-quoted PowerShell string literal —
  schema validation only checks path traversal and extension, never quote
  characters, so an unescaped single quote in `script.file` would break
  out of that literal and let the rest of the filename run as arbitrary
  PowerShell. Found on code review (not exploited, not reported) — fixed
  by doubling embedded single quotes before interpolating, PowerShell's
  own escaping convention for a single-quoted string.
- A script has no way to show what it would do, so `plan()` could only ever print the command line. Rather than make
  every dry run execute the script, execution is opt-in through `plan_args` and happens only when a preview was
  requested: a real `deploy run` calls `plan()` first, and would have run the script twice. The three alternatives
  considered were an `actions` map (a second way to say what `args` says) and a `${phase}` token in `args` (tooling
  cannot see that a preview executes the script); both were rejected.
- A destroy dry run does not execute the script either. `plan_args` shows an apply, and no mode of an arbitrary script
  shows a removal, so the only honest preview is the command line.
- Whether a script is safe in `plan` cannot be checked, so `STRATA_PHASE` exists as a second signal that does not
  depend on the arguments being right. It makes a wrong `plan_args` fail inside the script instead of changing things.
- Every script got every resolved value, and a script that needs one token had no way to ask for less. An allow-list
  (`values`) was chosen over flipping the default to none, which would break every existing script. The `deployment:`
  entries stay in even when a list is set: they are not secret and a script rarely works without knowing which
  deployment it runs for. The check runs in `plan()` as well as the apply, because a real run only previews before the
  apply and a typo should stop it there.
- Outputs follow the shape Terraform already established rather than a new mechanism: the integration's `output()`
  returns `terraform output -json` JSON, which `collect_step_outputs()` reads for any tool, so the controller, the
  output store and `${output:}` resolution needed nothing new. A file (read after the run) was chosen over parsing
  stdout, which breaks with `--follow` and with a script that prints anything else. The sensitive marker is a reserved
  `_sensitive` key in the same file, not a sibling file: one write, one read, no way for the two to disagree. The
  default is not sensitive, as in Terraform, because failing closed would make every output unusable for a dependent
  step; the cost of a wrong default is the author's, as for `plan_args`.
- The gaps behind the preview, failure and `deploy apply` behavior were found by running a real script step through
  `build run`, `deploy run` (`--dry-run`, `--follow`, `--smoke-test`, a stage timeout), `deploy plan` and `deploy apply`,
  not by reading this doc: the preview was computed and then dropped by the controller, a timeout looked like any
  other failure, and `deploy apply` told the user to run `deploy plan` forever. Unit tests that stub `run_command` had
  passed all along. A new tool should be driven through the CLI modes the same way, and the script now has tests that
  run a real interpreter (pinned to the test's own, because a bare `python3` on a Windows `PATH` can be a Store stub).
- `SUPPORTS_SAVED_PLAN` is a class flag, the same device as `SUPPORTS_DESTROY`, rather than a check on the tool's name,
  so the controller stays tool-agnostic. GitOps turned out to need it too.
- `SUPPORTS_DESTROY` could not say "this script can be destroyed and that one cannot", so an integration may answer
  per bound provisioner through the `supports_destroy` property, which defaults to the class constant.
- Every script was found to receive every secret of the deployment (a probe with an unrelated secret showed it in a
  script that needed one token), which is why `values` exists.
- A richer `script:` block was not built: nothing real needs an interpreter choice or a per-step environment, and
  every other gap here had a real symptom behind it.
