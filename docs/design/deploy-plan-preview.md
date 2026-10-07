# `strata deploy run` — Plan Preview, Smoke Test, and Output Streaming

- Status: current
- Last updated: 2026-10-07

## Overview

`deploy run` has three distinct, mutually exclusive execution modes, plus
an orthogonal live-output flag:

- **`--smoke-test`** — zero tool contact. Confirms every filtered step's
  provisioner/tool resolves and is on `PATH`, reports which steps would
  run, and makes no `init`/`validate`/`plan`/`deploy` calls at all. No
  auth, no backend reachability needed — the cheapest possible check.
- **`--dry-run`** — a real preview. Runs `init` → `validate` → `plan` for
  real (saved to `<step>.tfplan`/the Helm-Compose equivalent) and stops —
  `deploy()` is never called. A tool with no meaningful plan-preview
  support is a warning here, not a hard failure.
- *(neither flag, `--force`)* — a real apply, unchanged from before this
  feature existed.
- **`--follow`/`-f`** — streams subprocess output live, tool-prefixed.
  Under `--output json`, lines reroute to stderr instead (stdout stays
  exactly one parseable document) — **not** silently dropped, correcting
  a real bug found in a later consumer report (see History). `--follow
  ndjson` streams newline-delimited JSON events to stdout instead
  (requires `--output json`) — a first cut, scoped to this command only.
  Orthogonal to the three modes above; works with any of them.

This closes a real gap: `--dry-run` used to mean the same thing
`--smoke-test` means today (report-only, zero tool contact) — a CI
pipeline mapping its "plan" stage to `--dry-run` got no Terraform output
at all. `--dry-run` now means what every comparable tool's "dry run"
already means (`terraform plan`, `rsync --dry-run`, `kubectl
--dry-run=server`): real contact, real computation, no mutation.

## Current Design

### Mode selection and control flow

`deploy_run()` (`deploy_controller.py`) takes `smoke_test: bool` and
`dry_run: bool` (mutually exclusive — `deploy_command.py` raises a
`UsageError` if both are passed). In the per-step loop:

- `if smoke_test: continue` — the only flag that short-circuits before
  any tool contact at all.
- Otherwise, `init`/`validate` always run (Terraform-shaped steps only —
  Helm/Compose have neither), then `plan` runs via `plan_or_warn()`
  (below). After a successful plan, `if dry_run: continue` — skips
  `deploy()` and the output-collection that follows a real apply.
- The container-capable branch (`Capability.CONTAINER in
  integration.CAPABILITIES` — Helm/Compose) dispatches to
  `deploy_namespace()` per targeted namespace, passing `dry_run` straight
  through; that method's own internal handling is described below.

**Known limitation, not a bug**: a step whose tokens consume
`${output:...}` from an upstream step that hasn't been *really* applied
yet cannot get a meaningful plan preview for that value — outputs are
only ever collected after a real `deploy()`, which `--dry-run` never
reaches. Inherent (a resource's real output can't be known before it's
created), not something this design solves.

### Graceful degradation — `InfraIntegration.plan_or_warn()`

Since `plan()` is `@abstractmethod` on `InfraIntegration`, every current
subclass must implement *something* — "not supported" can only mean a
future integration's `plan()` raising `IntegrationError`, or a real
`CommandResult` failure for a genuine, tool-specific reason. A single
non-abstract helper on the class (same "base-implemented, not abstract"
precedent as `prepare()`/`default_output()`) handles both uniformly,
called from both the top-level loop and Helm/Compose's `deploy_namespace()`:

```python
def plan_or_warn(
    self, path: Path, *, diagnostics: Diagnostics, location: str, **kwargs: Any,
) -> CommandResult | None:
    """Call `self.plan(path, **kwargs)`, folding an `IntegrationError` into
    a warning instead of propagating it. Returns `None` when `plan()`
    itself isn't meaningfully supported, or the real `CommandResult`
    otherwise — callers still check `.is_successful` themselves.
    """
    try:
        return self.plan(path, **kwargs)
    except IntegrationError as exc:
        diagnostics.warning(f"no plan preview available for '{self.name}' — {exc}", location=location)
        return None
```

**The severity of a `None` result depends on which mode is running** —
this is the one place `dry_run` and a real apply genuinely diverge in
severity, not just in which steps run:

- Under `--dry-run`: `None` → graceful skip, `continue` to the next step.
  Nothing real was going to happen anyway.
- On a real apply (`dry_run=False`): `None` → hard `diagnostics.error()` +
  abort. `plan()` is a required prerequisite there, not an optional
  preview — silently skipping `deploy()` would report the run as
  successful having never actually deployed the step.

Public (no leading underscore) despite being a thin wrapper, since it's
genuinely called across a module boundary (`deploy_controller.py`),
matching every other extension point on `InfraIntegration`.

### Container-capable (Helm/Compose) `deploy_namespace()`

Both `HelmIntegration.deploy_namespace()` and `ComposeIntegration.
deploy_namespace()` keep one `dry_run: bool` parameter; only the internal
`if dry_run:` branch differs from a real deploy:

- **Helm**: resolves `resolved_values`/`secrets` and (when non-empty)
  writes `values.yaml` — a preview needs the same real rendered file on
  disk a real `helm upgrade` would use, just not followed by one. Calls
  `self.plan_or_warn(values_file, ..., release=..., namespace=...,
  chart=..., set_string=..., line_callback=...)` instead of `deploy()`.
- **Compose**: same shape — writes the resolved compose file, then
  `self.plan_or_warn(compose_file, ..., env={**(env or {}), **secrets},
  line_callback=...)` instead of `deploy()`.
- Neither integration does anything under `smoke_test` —
  `deploy_namespace()` is never reached at all in that mode.

**Operator-facing caveat**: because of the above, `--dry-run` is **not**
filesystem-read-only for a Helm/Compose step — it permanently rewrites
that step's `values.yaml`/`docker-compose.yml` under
`build/<deployment>/...` with resolved (never secret) values, even though
nothing is deployed. `helm upgrade --dry-run`/`docker stack config` both
take a real file path, not stdin — there's no way to preview without
writing first. **Practical implication**: a build-output reproducibility/
diff check must compare one `build run` invocation's output against
another `build run` invocation's — never against the same directory
after a `--dry-run` ran against it, which always shows this expected,
harmless diff.

### Streaming — `--follow`/`-f`

Researched against real precedent (`tail -f`, `kubectl logs -f`, `docker
logs -f`): the de facto verb for "don't buffer, show lines as they
arrive" is **follow**, not "verbose" — `--verbose` keeps its existing,
shared, log-level-only contract (used identically by every other
command); widening it just for this command would make the same flag
mean different things elsewhere.

- `Integration.run()` (`base.py`) accepts `line_callback: Callable[[str,
  str], None] | None`, forwarded to `run_command()` (which already
  implements the real streaming via two reader threads, one per
  stdout/stderr — pre-existing infrastructure, newly connected here).
  `line_callback` is **never invoked concurrently**: `_run_streaming()`
  serializes every call with an internal lock, so a caller writing to a
  shared resource (console, a log buffer) needs no synchronization of its
  own — only the *ordering* between stdout/stderr lines is unspecified,
  expected for live streaming.
- Every method that calls `self.run()` threads this through explicitly
  (their `**kwargs: Any` catch-alls do **not** reach `self.run()`, so this
  is real plumbing, not free): `TerraformIntegration` (`init`/`validate`/
  `plan`/`deploy`/`destroy`/`output`/`show`), `HelmIntegration`/
  `ComposeIntegration` (`plan`/`deploy`). `GitOpsIntegration.plan()` also
  streams (its one direct `run_command()` call, `git diff`) — its
  `deploy()`/`destroy()` do not yet, since both delegate to `git_push.py`
  helpers that make several internal `run_command()` calls needing their
  own threading first (deferred; GitOps is not a proven Tier-1
  critical-path integration).
- `deploy_run()` takes `on_line: Callable[[str, str, str], None] | None`
  — `(tool, stream, text)`. The orchestrator itself builds a small
  per-step closure enriching the plain `(stream, text)` every integration
  method expects with the step's own `integration.TYPE`, then calls the
  richer 3-arg `on_line` — necessary because v2's entire step loop lives
  inside one `deploy_run()` call (unlike v1, whose CLI drove the loop
  itself and could rebuild a tool-prefixed callback each iteration).
- `deploy_command.py` builds the console callback when `--follow` is
  bare (`click.secho`, tool-prefixed, yellow stderr/cyan stdout) —
  automatically rerouted to stderr whenever `--output json` is also
  active, so stdout always stays exactly one document. **Corrected**:
  this used to be silently inert under `--output json` instead (no
  warning, lines simply dropped) — a real consumer-reported bug, fixed
  by matching `--verbose`'s own actual stderr-reroute precedent instead
  of merely citing it (see History, [docs/work/
  cli-output-mode-interactions.md](../work/cli-output-mode-interactions.md)).
- `--follow ndjson` (requires `--output json`) emits one JSON event per
  subprocess line (`{"event": "line", "tool", "stream", "text", "ts"}`)
  and per progress message (`{"event": "progress", "message", "ts"}"`) to
  stdout instead, ending with the normal `build_envelope()` result as the
  final line (forced single-line via a new `compact` parameter on
  `make_reporter()`/`command_run()`, so it stays consistent with the
  per-line events ahead of it) — shipped as a first cut, scoped to
  `deploy run` only; full design and remaining open questions (a
  `repo sync`-style "data" event shape for non-subprocess commands,
  whether `build run` ever gains an analogous need) tracked in
  [docs/work/ndjson-output.md](../work/ndjson-output.md).

### Secret redaction for streamed output

Some tools echo their own rendered input verbatim regardless of any
streaming flag — concretely, `helm upgrade --dry-run` prints the full
rendered manifest, which can embed a `--set-string`-substituted secret
value anywhere a chart template places it. `--follow` would otherwise
display this live; before streaming existed this text was only ever
captured in `CommandResult.stdout` and never printed anywhere.

`merge_environment_models(environments)` already returns `variables,
secrets, features` as three distinct dicts keyed by declared name —
`secrets.keys()` is exactly the set of secret key names, even though the
later flat `resolved.values` deliberately drops that distinction
(`ValueResolution`'s own docstring: "the store a value came from is not
preserved on the result"). `deploy_run()` reconstructs the set of actual
resolved secret *values* from this:

```python
secret_values = {v for k, v in resolved.values.items() if k in secrets and len(v) >= _MIN_REDACT_LENGTH}
```

This single set also covers Helm/Compose's own per-module `--set-string`
secrets for free — they're derived from `${secret:KEY}` tokens resolved
against this same `resolved.values` dict, so no second mechanism is
needed. Redaction happens centrally in `deploy_run()`'s existing per-step
`_line` closure, **not** in the CLI — it's the one place secret values
are actually known, and it protects every current/future consumer of
`on_line` (today's `--follow` writer, a future NDJSON writer)
automatically:

```python
def _redact(text: str) -> str:
    for value in secret_values:
        text = text.replace(value, "***")
    return text
```

`_MIN_REDACT_LENGTH = 6` avoids a short secret turning into a scattergun
replace that corrupts unrelated output — same accepted tradeoff tools
like GitHub Actions' own log masking make.

**Known, honest limitations** (this mitigates the common case, not a
complete guarantee):

- Only catches plaintext, same-line occurrences — a secret rendered
  base64-encoded (Kubernetes `Secret.data`, which `helm upgrade --dry-run`
  *does* render that way) won't string-match.
- A value split across two streamed lines won't be caught.
- Only covers `store: secret`-declared values — a `variable`/`feature` a
  user considers sensitive isn't redacted; strata can't guess intent
  beyond the schema's own explicit classification.

### Saved plan artifact

Terraform's `plan_or_warn(..., out_file=f"{step.name}.tfplan", ...)` call
writes a real `.tfplan` file under `--dry-run` for free — the same
convention the real-apply path already uses. A future `strata deploy
plan` (an offline saved-plan reader, `terraform show -json <plan>`,
matching v1's own precedent) could read this back directly; not built
here, but this artifact naming means that follow-on needs no rename.

### Audit/manifest treatment

`finalize_and_distribute_deploy_audit()`'s existing `dry_run: bool`
parameter no-ops the whole manifest/metrics write ("nothing was actually
changed") — holds identically for `--dry-run` (a plan changes nothing
real) and `--smoke-test`. `deploy_command.py` calls it with `dry_run=
dry_run or smoke_test`.

## Not Built Here (deliberately out of scope)

- `strata deploy plan` — the offline saved-plan reader described above.
- Infracost-style cost-diff-after-plan (v1 precedent) — no cost estimator
  integration exists in v2 yet.
- A `repo sync`-style structured "data" NDJSON event shape (for commands
  with no subprocess at all) — `--follow ndjson` here only ever emits
  `line`/`progress` events; the other shape remains undesigned, no
  command exists yet that would need it ([docs/work/
  ndjson-output.md](../work/ndjson-output.md)).
- Extending `--follow`/NDJSON to `build run` — confirmed `build run` has
  no subprocess/line-callback plumbing at all today, so there is nothing
  to stream; revisit only if that changes.

## Related Decisions

- [ADR-0027](../decisions/0027-strata-deploy-run.md) — the original
  `deploy run` orchestrator this extends (render/execute split, saved-
  plan-file apply, cross-step outputs).
- [ADR-0030](../decisions/0030-deploy-run-plan-preview-and-streaming.md) —
  the decision this doc documents the design for (naming scheme,
  graceful-degradation severity split, streaming/redaction approach).
  Its own "silently inert with `--output json`" line records what was
  decided/believed *at the time* (2026-10-06) — left unedited as the
  historical record; the real behavior was corrected the next day (see
  History below), with the correction documented here, in the living
  design doc, rather than by editing the ADR itself.
- [deploy-command.md](../work/deploy-command.md) — the main `deploy run`
  work doc, still in progress for unrelated concerns (locking, SIEM,
  whole-run timeout).
- [cli-output-mode-interactions.md](../work/cli-output-mode-interactions.md) —
  the real cfgint bug report and fix (`--follow` silently inert under
  `--output json`), plus the general `--output`/`--quiet`/`--verbose`
  interaction survey that motivated it.
- [ndjson-output.md](../work/ndjson-output.md) — the full NDJSON design
  (v1 precedent, event shapes, scope) this doc's "Streaming" section
  summarizes the shipped first cut of.

## History

- Filed from a real CI pipeline finding (`cfg-int-deployment`, xyz-strata
  2.0.0a6): a `mode: plan` CI stage mapped to `deploy run --dry-run`
  produced zero Terraform output, since `--dry-run` short-circuited the
  whole step loop before `init`/`validate`/`plan` were ever called.
- v1's own `--dry-run` (`e:\SourcesXYZ\strata`) already ran a real plan
  (`setup → check → plan`, only skipping `apply`) — confirmed directly
  from its real source before designing this, not assumed. v1's hard
  failure for an unsupported step (`get_supported_steps()`) was
  deliberately not copied — see `plan_or_warn()`'s graceful-degradation
  design instead, a genuine improvement over v1's behaviour.
- Naming survey across Terraform/Ansible/Puppet/kubectl/AWS CDK found no
  single industry standard for "no-contact check" vs. "real-contact
  preview" vs. "apply." `--check`/`--noop` were ruled out (Ansible/Puppet
  already use them for the *real-contact* tier, which would mislead
  anyone who knows those tools). `--smoke-test` was chosen over a more
  generic `--preflight` specifically because the reporting CI already had
  a real `mode: smoke` stage — matching existing, real vocabulary over an
  abstract term, per this repo's "evidence over assumption" convention.
- Original design sketch named the graceful-degradation helper
  `_plan_or_warn()` (leading underscore); implementation corrected this
  to `plan_or_warn()` (public) since it's genuinely called across a
  module boundary, matching every other extension point on
  `InfraIntegration`.
- Post-implementation code review (full pass over every changed file)
  found and fixed one real correctness bug before it could ship: the
  top-level loop originally treated `plan_or_warn()`'s `None` result as a
  graceful skip *regardless* of `dry_run`, which would have silently
  skipped `deploy()` on a real apply while still reporting success. Fixed
  by gating the graceful skip on `dry_run`, matching Helm/Compose's
  `deploy_namespace()`, which only ever treated a missing plan capability
  as a graceful skip inside its own `dry_run` branch from the start.
- The same review also found `line_callback` could be invoked
  concurrently by the stdout/stderr reader threads (pre-existing
  infrastructure, newly exercised once `--follow` connected real console
  I/O to it) — fixed with an internal lock, verified as a real fix by
  temporarily removing it and confirming the regression test actually
  failed before trusting it.
- `init`/`validate`'s own `CommandResult`s were discovered to be silently
  discarded (pre-existing, predates this feature) — now checked for
  `.is_successful`, each a hard failure on error, matching `plan`/
  `deploy`'s existing pattern.
- 2026-10-07: A real external bug report (cfgint team, `xyz-strata`
  2.0.0a8): `--follow` really was "silently inert with `--output json`"
  exactly as documented above — but silent, with zero warning, caused an
  11-minute real `terraform apply` to look completely hung with no
  progress signal at all. Investigated and fixed
  ([cli-output-mode-interactions.md](../work/cli-output-mode-interactions.md)):
  `--follow` now reroutes every line to stderr under `--output json`
  instead of dropping it, actually matching the `--verbose` precedent
  this doc's own "Streaming" section already (incorrectly, at the time)
  claimed it followed. Same investigation surveyed every other
  console-only flag (`--quiet`) for the same shape — found independent,
  no change needed there.
- 2026-10-07: Shipped `--follow ndjson` as a first cut of the NDJSON
  streaming this doc's "Streaming" section had deferred
  ([ndjson-output.md](../work/ndjson-output.md)) — grounded directly in
  v1's own real NDJSON implementation (`git show main:...`, not assumed),
  which turned out to be used far more broadly than `deploy run` alone
  (8 v1 command files, two patterns: subprocess-line streaming and
  structured per-item "data" events with no subprocess at all). Chose
  `--follow ndjson` over a new top-level `--output ndjson` specifically
  to reuse the one flag this whole investigation is about rather than
  invent a second trigger mechanism. Found and fixed one real gap not
  anticipated in the design: `JsonReporter`'s default pretty-print
  (`indent=2`) broke NDJSON's one-object-per-line contract for the final
  envelope — fixed with an additive `compact` parameter. A follow-up code
  review then found and fixed a second real bug: the new events' own
  `json.dumps(...)` calls didn't match `format_json()`'s `ensure_ascii=
  False` policy, which would have escaped non-ASCII subprocess text
  inconsistently depending on which line of the stream it appeared in.
