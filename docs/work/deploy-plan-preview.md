# `strata deploy run` Plan Preview & Streaming Output — Work

- Status: draft — design finalized below (naming, control flow, graceful
  degradation, streaming all decided with no open questions remaining);
  implementation not yet started
- Last updated: 2026-10-06

## Overview

Filed from a real finding in a fresh v2 solution's first CI pipeline run
(`cfg-int-deployment`, xyz-strata 2.0.0a6, CI vocabulary `mode: smoke / plan
/ apply`): the `mode: plan` CI step mapped to `strata deploy run --force
--dry-run --verbose` gets **zero Terraform output** — no `init`, no `plan`,
no diff. `--dry-run`'s own help text already says this ("Report which steps
would run without calling init/validate/plan/deploy at all"), so the
behaviour matches the docs — but it leaves no way to get a real plan
preview before apply through this command at all. Confirmed directly
against source
([deploy_controller.py](../../src/strata/controllers/deploy_controller.py)):
`dry_run` short-circuits the step loop with a bare `continue` before
`init`/`validate`/`plan` are ever called, both in the Terraform-style
top-level loop and in the Helm/Compose `deploy_namespace()` path.

This doc designs a real plan-preview mode and the subprocess output
streaming needed to make it (and real deploys) usable interactively,
grounded in what v1 actually shipped
(`e:\SourcesXYZ\strata\src\strata\deployers\*.py`,
`commands\deploy\run_deploy_command.py`) rather than assumption.

**Naming, decided up front** (see "Proposed Design" for the full
reasoning): rather than bolt a new `--plan-only` flag on next to
`--dry-run` (too easy to confuse — "plan-only" and "dry-run" read as
near-synonyms), `--dry-run` itself is **redefined** to do what every other
tool calls a dry run (`terraform plan`, `rsync --dry-run`, `kubectl
--dry-run=server` — real contact, real computation, no mutation), and
today's existing narrower behaviour (zero tool contact at all) moves to a
newly-named `--smoke-test` flag — chosen because the reporting CI's own
pipeline already has a real `mode: smoke` stage, so this maps 1:1 with no
translation needed, and because it carries no competing meaning in the
IaC-tooling space (unlike `--check`/`--noop`, which Ansible/Puppet already
use for the *real-contact* tier — reusing either here would mislead anyone
familiar with those tools).

## Real evidence — what v1 actually did

- **v1's own `--dry-run` already ran a real plan.** Per
  `run_deploy_command.py::_execute_stage_provisioning()`'s own docstring and
  code:
  ```
  dry-run  : setup → check → plan
  normal   : setup → check → plan → apply
  ```
  `--dry-run` only ever skipped `apply` — `setup`/`check`/`plan` always ran
  for real, against the real backend, producing a real saved plan file
  (`<stage>.tfplan`) and a real plan JSON artifact
  (`deployer.save_plan_json()`). v2's `--dry-run` is a different, narrower
  thing today (report-only, zero subprocess calls) — this is the actual gap.
- **Every v1 deployer implemented a `plan` step, at varying fidelity** (all
  four header docstrings confirmed directly):
  - `TerraformDeployer.plan()` — real `terraform plan -detailed-exitcode
    -out=<stage>.tfplan`.
  - `HelmDeployer.plan()` — real `helm upgrade --dry-run --install` per
    module.
  - `ComposeDeployer.plan()` — `docker stack config` (v1's own comment:
    *"list namespaces and service counts... no true dry-run"* — closest real
    analog for Swarm, not a true diff).
  - `AnsibleDeployer.plan()` — real `ansible-playbook --check --diff`.
- **`get_supported_steps()` + a hard fail for anything missing.** Before
  running a step, v1 checks `if step_name not in deployer.get_supported_steps():
  self._errors.append(...); return False` — a deployer that doesn't declare
  a step aborts the whole stage. In practice every real deployer declares
  `plan`, so this never actually triggers for `plan` — but it is a hard
  failure, not a graceful skip, and is **not** a pattern this doc proposes
  copying (see Proposed Design below).
- **Streaming is a first-class, already-proven mechanism**, not new
  invention: every step method takes an optional
  `line_callback: Callable[[str, str], None]` (first arg `"stdout"`/
  `"stderr"`, second the raw line), which the base `run_command` plumbs
  through. `run_deploy_command.py` builds one of two callbacks per step:
  - **Tier 1 (console, `--verbose`)**: prints each line live, tool-prefixed
    with a gutter — `click.secho(f"      {tool} │ {text}", fg=...)`,
    yellow for stderr, cyan for stdout.
  - **Tier 2 (NDJSON)**: `self.emit_ndjson({"event": "step_start"/"step_end",
    ...})` around each step, plus a per-line NDJSON event from
    `make_ndjson_line_callback()`.
  Neither tier is a separate code path per deployer — the callback is built
  once in the orchestrator and passed uniformly to whichever `step_fn` is
  about to run.
- **A separate, fully offline `strata deploy plan` command** existed
  alongside `deploy run --dry-run`: `plan_deploy_command.py` reads back the
  `.tfplan` file `deploy run --dry-run` already saved (`deployer.show_plan()`
  → `terraform show -json <plan>`) and prints a human change summary
  (counts by action, `--verbose` lists every changed address). Zero backend
  calls — pure artifact read. Not part of this doc's core ask, noted as a
  reusable follow-on idea (see Remaining Work).

## Current Design / Progress — confirmed v2 state today

Read directly from source, not assumed:

1. **`deploy_run()`'s top-level step loop** (Terraform/GitOps-style steps,
   `deploy_controller.py`):
   ```python
   if dry_run:
       _step(f"would deploy step '{step.name}' via {integration_type}")
       continue
   ```
   This `continue` fires *before* `init`/`validate`/`plan`/`deploy` are
   reached later in the same loop body — confirmed by reading the rest of
   the function, where the real `integration.plan(path, out_file=...)` call
   happens unconditionally further down, immediately before
   `integration.deploy(...)`.
2. **The Helm/Compose `deploy_namespace()` path carries a second, currently
   *unreachable* copy of the same `dry_run: bool` parameter** — re-checked
   precisely against source (not assumed): the outer loop's `if dry_run:
   continue` (item 1 above) fires *before* `deploy_namespace()` is ever
   called at all, so in practice `deploy_namespace(..., dry_run=...)` is
   only ever invoked with `dry_run=False` today — its own internal `if
   dry_run: continue`/`return diagnostics` is dead code, not an
   independently-triggered second bug. Both `HelmIntegration.
   deploy_namespace()` and `ComposeIntegration.deploy_namespace()` already
   implement a real `self.plan()` (`helm upgrade --dry-run --install`,
   `docker stack config`) that this dead branch could call instead of
   skipping — once item 1's outer short-circuit is changed so real-plan
   mode reaches this far (Proposed Design §2), this parameter's own
   internal behaviour also needs to change from "skip" to "call
   `self.plan()` per module" (§2a).
3. **Every current `InfraIntegration` subclass already implements a real,
   tool-appropriate `plan()`** — `plan` is `@abstractmethod` on the ABC
   (`capabilities.py`), so this isn't a gap to build, just a call that's
   never made on the dry-run path:
   - `TerraformIntegration.plan()` — `terraform plan -out=<step>.tfplan
     [-detailed-exitcode]`.
   - `HelmIntegration.plan()` — `helm upgrade --dry-run --install`.
   - `ComposeIntegration.plan()` — `docker stack config -c path` (own
     docstring: *"No true dry-run exists for Swarm... closest real
     analog"* — same honest caveat v1 carried).
   - `GitOpsIntegration.plan()` — real `git diff --no-index` between the
     already-pushed file and the freshly-rendered one.
4. **Streaming is not wired end-to-end, despite the low-level machinery
   already existing.** `strata.utils.transport.run_command()` already
   accepts and honours `line_callback` (`_run_streaming()`), but
   `Integration.run()` — the thin wrapper every integration method calls
   through (`TerraformIntegration.plan()` → `self.run(*args, ...)`) — has no
   `line_callback` parameter at all and never forwards one:
   ```python
   def run(self, *args: str, cwd: Path | None = None,
           env: Mapping[str, str] | None = None, timeout: int = 300) -> CommandResult:
       ...
       return run_command([command, *args], cwd=cwd, env=env, timeout=timeout)
   ```
   No current call path in v2 can stream subprocess output, regardless of
   any CLI flag — this is a real, separate gap from the dry-run/plan one.
5. **`--verbose` today is purely a log-level flag, not a streaming one.**
   Confirmed from its own docstring (`commands/options.py`):
   > "Add `--verbose/-v`, raising the log level... Affects logging only,
   > which goes to stderr."
   `deploy_run_command()` accepts `verbose` but never passes it into
   `deploy_run()` at all — only `on_step=run.step` (one line per step) is
   wired through. The original bug report's claim that "`--verbose` is
   documented to stream each terraform subprocess line live" does not match
   any current v2 documentation found — it matches v1's real behaviour
   (Tier 1 above) and was a reasonable but incorrect carry-over assumption.
6. **No real-plan flag and no `strata deploy plan` command exist in v2
   today.** Neither is on the documented roadmap
   ([deploy-command.md](deploy-command.md)'s own deferred-items list only
   names locking, SIEM/audit, whole-run timeout, and `--force`'s advisory-gate
   semantics — no plan-preview item).

## Proposed Design

### 0. Naming survey (why `--smoke-test` + redefined `--dry-run`, not a third flag)

No single industry standard exists for "no-contact check" vs. "real-contact
 preview" vs. "apply" — tools split this differently:

| Tool             | No-contact / local-only           | Real-contact preview (no mutation) | Apply          |
| ---------------- | --------------------------------- | ---------------------------------- | -------------- |
| Terraform itself | `validate`                        | `plan`                             | `apply`        |
| Ansible          | `--syntax-check` / `--list-tasks` | `--check --diff`                   | *(normal run)* |
| Puppet / Chef    | —                                 | `--noop` / `--why-run`             | *(normal run)* |
| kubectl          | `--dry-run=client`                | `--dry-run=server`                 | *(normal run)* |
| AWS CDK          | `cdk synth`                       | `cdk diff`                         | `cdk deploy`   |

Two findings from this survey settled the scheme below:

- **`--check`/`--noop` already mean the *real-contact* tier elsewhere**
  (Ansible, Puppet) — reusing either for strata's no-contact tier would
  mislead anyone who knows those tools. Ruled out.
- **Most mature tools use separate verbs** (`validate`/`plan`/`apply`,
  `synth`/`diff`/`deploy`), not one flag with a value — kubectl's
  `--dry-run=client|server` is the one real precedent for the latter, but
  strata already has an unrelated top-level `strata validate` command, so
  borrowing Terraform's own three-verb vocabulary directly would collide.
- **Deciding factor**: the real CI that filed this report already has a
  production `mode: smoke` stage. Per this repo's own "evidence over
  assumption" convention, matching that existing, real vocabulary beats an
  abstract industry term — `--smoke-test` needs no translation on the CI
  side. (`--preflight` was the runner-up — a more generic DevOps term with
  no real precedent in *this* codebase to justify it over the CI's own
  word.)

### 1. `--smoke-test` (new) + `--dry-run` (redefined), `--force` unchanged

- **`--smoke-test`** *(new flag)* takes over today's existing v2 `--dry-run`
  behaviour exactly, unchanged: confirms every filtered step's provisioner/
  tool resolves and is on `PATH` (already happens today, before the dry-run
  check — see `deploy_controller.py`'s `resolved_steps` build-up), reports
  which steps would run, and makes **zero** `init`/`validate`/`plan`/`deploy`
  calls. Needs no auth, no backend reachability — the cheapest possible
  check. Maps to the CI's own `mode: smoke` stage.
- **`--dry-run`** *(same flag name, redefined behaviour)* becomes the real
  preview: runs `init` → `validate` → `plan` for every filtered step
  (mirrors v1's own dry-run sequence exactly) and stops — `deploy()`/
  `apply()` is never called. For container-capable (Helm/Compose) steps,
  `deploy_namespace()` gets the equivalent change: a real preview path that
  calls `integration.plan(...)` per module/namespace instead of
  unconditionally skipping. Maps to the CI's own `mode: plan` stage —
  **no change needed to the CI's invocation**, since it already passes
  `--dry-run`; it just finally gets the behaviour it always expected.
- **`--force` + no dry-run/smoke-test flag** stays apply, unchanged. Maps
  to the CI's own `mode: apply` stage.
- Honest tradeoff: this is a **behaviour-breaking change** for `--dry-run`
  (same flag, different effect) — real consumers who relied on today's
  zero-contact meaning under that name need to move to `--smoke-test`
  instead. Given this is what the bug report itself demonstrates consumers
  already expect `--dry-run` to do, and v2 is still alpha (2.0.0a6), this
  is judged acceptable — flagged explicitly rather than silently decided.

### 2. Orchestrator control-flow change

Precisely, in `deploy_run()`'s step loop (`deploy_controller.py`):

- The parameter gating today's existing zero-contact behaviour is renamed
  `smoke_test: bool = False` — its `if smoke_test: continue` stays
  structurally identical to today's `if dry_run: continue` (same position,
  same effect, same progress message), just renamed.
- `dry_run: bool = False` becomes the real-preview flag and **no longer
  short-circuits at the top**. Instead:
  - `init`/`validate` always run (for both `dry_run` and a normal
    execution — only `smoke_test`'s earlier `continue` skips them).
  - The existing unconditional `integration.plan(...)` call is replaced by
    the new `_plan_or_warn()` helper (§3).
  - Immediately after a successful plan: `if dry_run: _step(f"planned step
    '{step.name}' via {integration_type} (dry-run, no apply)"); continue`
    — `deploy()`/`collect_step_outputs()` are skipped, matching v1's
    "dry-run stops before apply" sequence exactly.
  - The container-capable branch (`Capability.CONTAINER`) is reached
    normally under `dry_run=True` now (no longer pre-empted) and forwards
    `dry_run` into `deploy_namespace(..., dry_run=dry_run, ...)` exactly as
    today's call shape already does — only `deploy_namespace()`'s own
    internal handling changes (§2a).
- `smoke_test` and `dry_run` are mutually exclusive — `deploy_command.py`
  raises `UsageError` if both are passed (one pipeline step should mean
  one thing).
- **Known limitation, not a bug**: a step whose `provisioner.backend`/
  tokens consume `${output:...}` from an upstream step that hasn't been
  *really* applied yet cannot get a meaningful plan preview for that
  value — `step_outputs` is only ever populated after a real `deploy()`
  (§2's new code never reaches that call under `dry_run`). This is
  inherent (you cannot know a resource's real output before creating it)
  and is not something this design attempts to solve — noted so it isn't
  mistaken for a regression later.

### 2a. Container-capable (Helm/Compose) alignment

Both `HelmIntegration.deploy_namespace()` and `ComposeIntegration.
deploy_namespace()` keep their existing `dry_run: bool` parameter and
signature — only the internal `if dry_run:` branch's body changes, from
skip-everything to call-plan-and-report:

- **Helm**: still resolves `resolved_values`/`secrets` and (when non-empty)
  writes `values.yaml` exactly as the real-deploy path already does — a
  preview needs the real rendered file on disk to run `helm upgrade
  --dry-run` against, same input the real `deploy()` call would use, just
  not followed by one. Then calls `self._plan_or_warn(values_file,
  diagnostics=diagnostics, location=item.reference.name, release=release,
  namespace=release_namespace, chart=chart, set_string=set_string,
  env=env, line_callback=line_callback)` instead of `continue`; a failed
  (but supported) plan becomes `diagnostics.error(...)`, matching the real
  `deploy()` failure branch immediately below it.
- **Compose**: same shape — writes the resolved compose file, then
  `self._plan_or_warn(compose_file, diagnostics=diagnostics,
  location=str(namespace.meta.name), env={**(env or {}), **secrets},
  line_callback=line_callback)` instead of `return diagnostics`.
- Neither integration does anything new under `smoke_test` —
  `deploy_namespace()` is never reached at all in that mode (§2's outer
  `continue` still applies before the container branch), so no "skip"
  logic needs to exist inside `deploy_namespace()` at all going forward.

### 3. Graceful degradation — one shared `_plan_or_warn()` helper

v1's `if step_name not in deployer.get_supported_steps(): ... return False`
is a hard abort for the whole stage. This design deliberately does **not**
copy that: `plan()` is `@abstractmethod` on `InfraIntegration`, so every
current subclass must implement *something* — "not supported" can
therefore only mean a future integration's `plan()` raising
`IntegrationError`, or a real `CommandResult` failure for a genuine,
tool-specific reason.

New, non-abstract helper directly on `InfraIntegration` (`capabilities.py`,
same "base-implemented, not abstract" precedent already used for
`prepare()`/`default_output()`, ADR-0023 D5):

```python
def _plan_or_warn(
    self, path: Path, *, diagnostics: Diagnostics, location: str, **kwargs: Any,
) -> CommandResult | None:
    """Call `self.plan(path, **kwargs)`, folding an `IntegrationError` into
    a warning instead of propagating it — "preview if the tool supports
    it, otherwise just say so." Returns `None` when `plan()` itself isn't
    meaningfully supported (caller treats `None` as "skip, a diagnostic
    was already recorded") or the real `CommandResult` otherwise — callers
    still check `.is_successful` themselves; a `plan()` that runs but
    fails for a real tool-specific reason (bad backend config, invalid
    HCL, unreachable remote) stays a hard error, same severity as today's
    real `plan()` failure on the apply path — only a missing *capability*
    is graceful.
    """
    try:
        return self.plan(path, **kwargs)
    except IntegrationError as exc:
        diagnostics.warning(f"no plan preview available for '{self.name}' — {exc}", location=location)
        return None
```

Used uniformly at both call sites: the top-level loop (replacing today's
unconditional `integration.plan(...)` call) and both Helm/Compose
`deploy_namespace()` bodies (§2a) — one implementation, no per-integration
duplication. None of the four current integrations (Terraform/Helm/
Compose/GitOps) are expected to actually raise `IntegrationError` from
`plan()` today — this helper exists for the *next* integration that might,
and so today's behaviour for all four is unchanged by its existence.

### 4. Streaming, decided end-to-end

No more open question — researched against real precedent (`tail -f`,
`kubectl logs -f`, `docker logs -f`): the de facto standard verb for
"don't buffer, show lines as they arrive" is **follow**, not "verbose."
`--verbose` keeps its existing, shared, log-level-only contract
(`options.py`'s own docstring, used identically by every other command)
— widening it just for `deploy run` would make the same flag mean
different things on different commands, which is worse than adding one
new flag. New: **`--follow`/`-f`** (`deploy run` only — no CLI collision,
`-f` is unused on this command today).

Concrete changes:

1. `Integration.run()` (`base.py`) gains `line_callback: Callable[[str,
   str], None] | None = None`, forwarded to `run_command(...,
   line_callback=line_callback)`.
2. Every method that builds argv and calls `self.run(...)` needs an
   explicit `line_callback` parameter added and threaded through —
   confirmed these methods' existing `**kwargs: Any` catch-alls do **not**
   currently reach `self.run()` (the body calls `self.run(*args,
   cwd=path, env=env, timeout=timeout)` with no `**kwargs` spread), so
   this is a real, necessary signature change, not just plumbing a value
   that already flows:
   - `TerraformIntegration`: `init`, `validate`, `plan`, `deploy`,
     `destroy`, `output`, `show`.
   - `HelmIntegration`: `plan`, `deploy`.
   - `ComposeIntegration`: `plan`, `deploy`.
   - `GitOpsIntegration`: `plan`, `deploy` — verify during implementation
     whether these call `self.run()` or `run_command()` directly (its
     `plan()` was seen calling `run_command(["git", "diff", ...])`
     directly in one spot) and wire whichever is the real call site.
3. `deploy_run()` gains `on_line: Callable[[str, str], None] | None =
   None`, passed to every `init`/`validate`/`_plan_or_warn`/`deploy` call
   and into `deploy_namespace(..., line_callback=on_line)`.
4. CLI layer (`deploy_command.py`) builds the real callback only when
   `--follow` is set **and** output is console (not `--output json`) —
   mirrors `--verbose`'s own "can never make JSON output unparseable"
   precedent:
   ```python
   def _make_follow_callback(tool: str) -> Callable[[str, str], None]:
       def _cb(stream: str, text: str) -> None:
           if stream == "stderr":
               click.secho(f"      {tool} │ {text}", fg="yellow", err=True)
           else:
               click.secho(f"      {tool} │ {text}", fg="cyan")
       return _cb
   ```
   (direct port of v1's own `_make_verbose_cb`, renamed) — built once per
   step, keyed off `type(integration).__name__`, passed as `on_line` into
   `deploy_run()`.
5. NDJSON streaming (v1's Tier 2) stays explicitly **out of scope** —
   `--output ndjson` doesn't exist in v2 yet (`json_output.py`'s own
   comments mark it future work). Revisit once that output mode is built
   — the same `on_line` plumbing this phase adds is exactly what a future
   NDJSON writer would also consume, so nothing here needs redoing later.

### 5. Audit/manifest treatment

`finalize_and_distribute_deploy_audit()`'s existing `dry_run: bool`
parameter already no-ops the whole manifest/metrics write ("nothing was
actually changed") — that reasoning holds identically for the redefined
`--dry-run` (a plan still changes nothing real) and for the new
`--smoke-test`. No change needed inside `audit_run.py` itself —
`deploy_command.py` just calls it with `dry_run=dry_run or smoke_test`.

### 6. Saved plan artifact

Terraform's `_plan_or_warn(..., out_file=f"{step.name}.tfplan", ...)` call
already writes a real `.tfplan` file under `--dry-run`, for free — the
same convention the real-apply path already uses, no new design needed. A
future `strata deploy plan` (v1's offline saved-plan reader, `terraform
show -json <plan>`) could read this back directly; building that reader
command is explicitly **out of scope** for this phase (below), but this
phase's artifact naming is chosen so that follow-on needs no rename.

### Not in this phase

- `strata deploy plan` (v1's separate, fully-offline saved-plan reader) —
  real, low-cost follow-on now that `--dry-run` writes a saved plan
  artifact to read back (§6), but is a distinct command and not required
  to close the original report's gap. Noted for later, not designed here.
- Infracost-style cost-diff-after-plan (v1's `_run_cost_diff_for_stage`) —
  no evidence of a cost estimator integration in v2 yet; out of scope.
- NDJSON (Tier 2) streaming — see §4 point 5.

## Implementation Plan

Phased the same way `deploy-command.md`'s own Implementation Plan was —
small, independently-testable, full check suite after each.

1. **`--smoke-test` flag, additive only — no behaviour change yet.** Add
   `smoke_test: bool = False` to `deploy_run()` and `deploy run
   --smoke-test`; the orchestrator's early continue becomes `if smoke_test
   or dry_run: continue` temporarily (both flags still gate the exact
   same zero-contact path) so this phase is a pure rename/addition with
   nothing behaviourally different yet. Add the mutual-exclusivity
   `UsageError` now too. Update existing tests that pass `dry_run=True`
   expecting zero `run_command` calls to also cover `smoke_test=True`
   identically; add a new test asserting both flags together raise
   `UsageError`.
2. **Redefine `--dry-run`'s real behaviour.** Remove `dry_run` from the
   early-continue condition (only `smoke_test` remains). Add
   `InfraIntegration._plan_or_warn()` (§3) to `capabilities.py`. Replace
   the top loop's unconditional `integration.plan(...)` call with
   `_plan_or_warn(...)`, add the post-plan `if dry_run: ...; continue`
   before the existing `deploy(...)` call. Update `deploy_command.py`'s
   `--dry-run` help text to describe the new real-preview behaviour, and
   its `finalize_and_distribute_deploy_audit(...)` call to pass
   `dry_run=dry_run or smoke_test` (§5). Update the existing
   `test_commands_deploy.py` assertion that `--dry-run` makes zero
   `run_command` calls — move that exact assertion onto `--smoke-test`
   instead — and add new tests: `--dry-run` calls `init`/`validate`/`plan`
   but never `deploy`, a failed plan under `--dry-run` is a hard error, an
   `IntegrationError` from `plan()` becomes a warning and the run
   continues to the next step.
3. **Container-capable (`deploy_namespace()`) alignment (§2a).** Update
   `HelmIntegration.deploy_namespace()` and `ComposeIntegration.
   deploy_namespace()`'s `if dry_run:` branches to call `self._plan_or_warn(...)`
   instead of skipping, reusing phase 2's helper. New tests per
   integration: dry-run writes the rendered file and calls `plan()` (not
   `deploy()`), a failed plan is an error, `smoke_test` never reaches
   `deploy_namespace()` at all (no new assertion needed beyond
   phase 1's, confirms no regression).
4. **Streaming (§4).** Add `line_callback` to `Integration.run()` and to
   every method listed in §4 point 2. Add `on_line` to `deploy_run()`,
   threaded to every call site. Add `--follow`/`-f` to `deploy_command.py`
   plus the callback builder, gated on console (non-JSON) output. New
   tests: a stubbed `run_command` that synchronously invokes its
   `line_callback` with a couple of fake lines, asserting those lines
   reach `deploy_run()`'s `on_line` unchanged; CLI-level test asserting
   `--follow` without `--output json` produces the tool-prefixed console
   lines, and that `--follow --output json` is accepted but produces no
   extra console noise (silently inert, matching `--verbose`'s own
   precedent).
5. **Docs/cross-link cleanup.** Update [deploy-command.md](deploy-command.md)'s
   CLI signature section (`--force [--smoke-test] [--dry-run] [--follow]
   [--stage NAME] [--scope LABEL]`) and fold this doc's `## Overview`/
   `## Proposed Design` findings into it per that doc's own graduation
   convention once every phase above is done and nothing remains open;
   update [v2-schema-overview.md](../design/v2-schema-overview.md)'s
   `deploy run` status row.
6. **Consumer coordination.** Confirm with haven/cfg-int-deployment
   maintainers before merging/releasing — their `mode: smoke` CI stage
   needs to switch from `--dry-run` to `--smoke-test` to keep its current
   zero-contact meaning; their `mode: plan` stage needs no change (already
   passes `--dry-run`, now gets the behaviour it always expected). Given
   v2 is still alpha (2.0.0a6), this ships as a direct behaviour change
   with a clear release note, not a deprecation cycle (docs/work/
   release-strategy.md's existing process otherwise unaffected — this is
   a code change, not itself a version-bump procedure).

## Related Decisions

- [ADR-0027](../decisions/0027-strata-deploy-run.md) — `strata deploy run`'s
  original design decisions (render/execute split, `--dry-run`'s meaning
  discussed only in the `build_run()` context, not this gap).
- [deploy-command.md](deploy-command.md) — the main `deploy run` work doc;
  this doc should be folded back into it once built, rather than staying
  permanently separate.

## Remaining Work / Open Questions

Everything design-level is decided (§0–§6 above) — nothing left open at
the design level. What remains is purely execution, tracked as the
Implementation Plan's six phases above. The only genuinely open item:

- [ ] **Timing of the consumer-visible `--dry-run` behaviour change**
      (Implementation Plan phase 6) — whether to land all six phases in
      one release or coordinate the CLI flag rename with haven/
      cfg-int-deployment maintainers first. Not a design question, an
      execution/release-sequencing one; revisit once phases 1–5 are built
      and ready to ship.

## Changelog

- 2026-10-06: Created, from a real CI pipeline finding
  (`cfg-int-deployment`, xyz-strata 2.0.0a6) that `deploy run --dry-run`
  produces no Terraform output at all, confirmed against both v1's real
  source (`e:\SourcesXYZ\strata`) and v2's current `deploy_controller.py`/
  `capabilities.py`/`base.py`/`options.py`.
- 2026-10-06: Naming revised after discussion — rejected the initial
  `--plan-only` (too similar to `--dry-run`) in favour of redefining
  `--dry-run` itself to run a real plan (matching v1 and external
  convention) plus a new `--smoke-test` flag for today's existing
  zero-contact behaviour, chosen to match the reporting CI's own real
  `mode: smoke` stage name over a generic industry term.
- 2026-10-06: Full design finalized — resolved every prior open question:
  `_plan_or_warn()` as the single graceful-degradation helper (§3),
  `deploy_namespace()`'s `dry_run` parameter repurposed rather than
  replaced (§2a), `--follow`/`-f` decided over widening `--verbose`'s
  existing shared contract (§4), audit/manifest treatment (§5), and the
  saved-plan-artifact question (§6, already free via the existing
  `out_file` convention). Added a six-phase Implementation Plan ready for
  execution. Also corrected an earlier imprecision: the Helm/Compose
  `deploy_namespace()` "second bug" is actually a currently-unreachable
  vestigial parameter, not an independently-triggered second short-circuit
  — re-verified directly against source before writing the fix.
