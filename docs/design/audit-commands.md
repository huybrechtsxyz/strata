# Audit read commands (`strata audit status`/`strata audit changes`) — Design

- Status: current — both `strata audit status` and `strata audit changes`
  are implemented (2026-10-05, all 7 Implementation Plan phases complete).
- Last updated: 2026-10-05

## Overview

This is Layer 3 of [audit-trail.md](audit-trail.md)'s four-layer model — the
one piece that doc's own MoSCoW records as genuinely missing: a way to read
back what Layer 2 already writes and Layer 4 already pushes out, so a human
or an auditor can ask "what deployed, and when" without knowing the on-disk
shape of a git checkout.

Spun into its own doc rather than growing further inside audit-trail.md, the
same way [audit-sink-dispatch.md](audit-sink-dispatch.md) was split out once
the dispatch design got detailed enough — audit-trail.md stays the index
("what exists, what's missing"); this doc owns "how the read side actually
works."

**Why this doc exists now, and why the earlier attempt was wrong.**
Audit-trail.md's "Layer 3 deferred" section records a first design (`strata
audit status`/`changes` reading local `.strata/audit/*.json` files) that was
rejected before any code was written: real deploys run in ephemeral CI
~90% of the time, so a local-only read would silently show nothing for most
real invocations — a false negative dressed up as a working command. The
fix is to read from the one durable, queryable destination that already
exists: **the `git` audit sink's pushed remote** ([audit_push.py](../../src/strata/controllers/audit_push.py)),
not local disk.

**Confirmed starting state (2026-10-05), not assumed:** the `git` sink is
fully built and tested, but is not configured in this repo's own dogfood
`config/` example, nor anywhere in the one real consumer's actual config —
grepped directly, zero hits. This design makes the mechanism read-ready;
turning it on for a real consumer is a separate, later step (configuring
`spec.audit.sinks[].git` + a `type: git` remote), same as the ADO gate's own
"most of the work is outside this repo" honest reservation.

## Current Design

Nothing here is implemented yet — this section describes the target shape,
not existing behaviour, flagged throughout as `[new]`. Kept in the
template's "Current Design" section anyway (rather than only under
"Remaining Work") because the shape itself — not just the backlog of tasks —
is the thing worth reviewing before code is written.

### Command surface

A new top-level group, `strata audit`, the second command group after
`sln` (same `@click.group` + subcommand shape as
[sln_command.py](../../src/strata/commands/sln_command.py)).

**Scoped to the minimum for the first build (2026-10-05):** only `status`
ships first. It alone exercises the whole read mechanism end to end
(checkout sync, glob, parse, render) and already answers the most common
real question ("is my last deploy healthy") — `changes` is strictly more
work on top of the same mechanism (range filtering, no grouping), not a
different mechanism, so it can follow once `status` is real and tested
rather than building both at once.

- **`strata audit status [DEPLOYMENT]`** — **Phase 1, build first.** The
  latest record per deployment (or just the named one). The "is my last
  deploy healthy" question.
- **`strata audit changes [DEPLOYMENT]`** — **designed (2026-10-05),
  not yet implemented** — see "`strata audit changes` — range
  listing" below for the full design. Every record in range, not just
  the latest — the ISAE 3402 Type II ask specifically (operating
  effectiveness *over a period*, not one good sample). Filters:
  `--since`/`--until` (ISO-8601 date/datetime, compared against each
  manifest's own `started_at`), `--status success|partial|failed` (the
  manifest's real tri-state outcome — corrected here from an earlier,
  inaccurate success/failed-only note).

`status` takes the same `--path`/`--output`/`--quiet`/`--verbose` options
every other command does. **No `--sink` flag in this first cut** — see
"Sink selection" below, now simplified to a single-sink assumption.

**Example invocations:**

```console
$ strata audit status
DEPLOYMENT        STATUS   VERSION  DEPLOYED BY        COMPLETED AT          CHANGE REF
web-frontend       success  2.4.1    svc-pipeline-ado   2026-10-05T14:02:11Z  OPS-1234
api-backend        failed   2.4.1    svc-pipeline-ado   2026-10-05T13:47:02Z  -
db-migrations      success  2.3.0    jsmith             2026-10-04T09:12:55Z  -

$ strata audit status api-backend
DEPLOYMENT    STATUS   VERSION  DEPLOYED BY       COMPLETED AT          CHANGE REF
api-backend   failed   2.4.1    svc-pipeline-ado  2026-10-05T13:47:02Z  -
```

`changes` is now fully designed (not merely deferred) — see "`strata audit
changes` — range listing" below for its own command surface, filter
validation, module contracts, and worked examples.

### Why read from the exact git-sink destination, not a second clone

[`layout.py`](../../src/strata/utils/layout.py) already keys every checkout
population by purpose — `remote_checkout_path()` (pinned reads),
`audit_push_checkout_path()` (the audit git sink's mutable push tip),
`gitops_push_checkout_path()` (same idea, a different producer) — each in
its own subdirectory specifically so two unrelated checkout populations of
the same remote can never collide. A read checkout is a fourth, distinct
population, not a reuse of the push one: running `audit status` concurrently
with a `deploy run` that's actively pushing to the same `(remote, branch)`
must never have two processes running git commands against the same working
tree. `[new]` `layout.audit_read_checkout_path(root, remote, branch)` →
`.strata/audit-read/<remote>/<branch>`, following the exact naming and
reasoning `audit_push_checkout_path`'s own docstring already states for
exactly this concern.

### Read mechanism — reuse, not reimplement, the push side's fetch+reset

`audit_push.py`'s private `_ensure_checkout()` already does exactly what a
read needs — clone if absent, else fetch + reset to `origin/<branch>` (or
the remote's default branch when it doesn't exist yet) — it just also
happens to live inside the push module. `[new]` Extract it to a small
shared function (e.g. `controllers/git_checkout.py::ensure_synced_checkout()`)
that both `audit_push.py` and the new `controllers/audit_read.py` call —
not duplicated logic, the same "fetch and push are one capability" instinct
[`remote_resolution.py`](../../src/strata/controllers/remote_resolution.py)'s
own docstring already states for credential handling. The read side never
calls `_configure_identity()`, `git add`, `git commit`, or `git push` —
those are push-only concerns.

Same ambient-only git auth as every other remote operation today
(`resolve_remote()`, `push_audit_files()`) — ambient SSH agent/credential
helper, nothing new invented for read either, matching this codebase's
existing documented gap rather than solving a different problem than the
one asked.

### Sink selection — single `git`-arm sink assumed for now

`spec.audit.sinks[]` can hold any mix of `git`-arm and `integration`-arm
sinks ([`AuditSinkModel`](../../src/strata/models/audit_model.py)). Only
`git`-arm sinks are readable at all — `integration` sinks
(webhook/OTel/Sentinel) are fire-and-forget by design, confirmed in
audit-trail.md's own Layer 3 deferral note ("no git destination to read
back from at all").

**Decided (2026-10-05): design for exactly one readable sink, not N.**
Multi-sink disambiguation (a `--sink NAME` flag, picking among several) is
real added surface with no confirmed need yet — neither the dogfood
`config/` example nor the one real consumer configures even a single audit
sink today (per this doc's own Overview), so designing a selection UI for
"more than one" is solving a problem nobody has hit. Selection logic,
`[new]`:

- No `spec.audit` configured, or no enabled `git`-arm sink among its
  `sinks[]` → a `UsageError`-class finding ("no readable audit sink
  configured") — **never a silently empty list.** This is the exact
  failure mode the local-file design was rejected for; the fix must not
  reintroduce it one layer up.
- Exactly one enabled `git`-arm sink → used automatically.
- **More than one → also a `UsageError`** ("multiple git-arm audit sinks
  configured; reading more than one is not supported yet"), not a
  `--sink` prompt. Honest about the gap rather than building a flag ahead
  of a real need — see "Deferred: multi-sink reads" under Remaining Work.

**Example — single sink, the only configuration this first cut supports:**

```yaml
# configuration.yaml
spec:
  audit:
    sinks:
      - name: compliance-archive
        git: { remote: audit-repo, branch: main, path: records }
```

```console
$ strata audit status
DEPLOYMENT    STATUS   VERSION  DEPLOYED BY  COMPLETED AT          CHANGE REF
web-frontend  success  2.4.1    jsmith       2026-10-05T14:02:11Z  OPS-1234
```

A solution with one `git`-arm sink and one `integration`-arm sink (the
documented dual-sink case — durable archive + SIEM fire-and-forget) still
works with no extra flag: only the `git`-arm one is ever a read candidate,
and there's exactly one of those.

**This check must run first, and must need zero network/git I/O to run.**
`open_solution(...).require_valid()` already resolved and loaded the
effective `ConfigurationModel` into memory as part of ordinary solution
loading — checking `configuration.spec.audit` and counting its `git`-arm
sinks is then pure in-memory inspection, nothing more. This ordering is
the literal fix for the "tell the user first" requirement: the very first
thing `audit status` does after loading the solution is this check, before
`sync_read_checkout()` (next section) ever runs a single git command — a
misconfigured solution fails in milliseconds with a message naming exactly
what to add, never after a slow, confusing clone/fetch attempt against
nothing. Exact message text, `[new]` — **corrected 2026-10-05**, shared
by both `status` and `changes` and no longer naming one of them
specifically (an earlier draft said "'strata audit status' reads
from..." unconditionally, actively misleading when raised for `strata
audit changes` instead):

- Zero readable sinks: *"No audit git sink is configured for this
  solution. Reading audit records ('strata audit status'/'strata audit
  changes') requires a configured 'git' audit sink, and none exists —
  add one under spec.audit.sinks in your Configuration document. See
  docs/design/audit-trail.md and docs/design/audit-commands.md."*
- More than one: *"Multiple git audit sinks are configured
  ('compliance-archive', 'team-mirror') — reading more than one is not
  supported yet. See docs/design/audit-commands.md."* — names the
  actual configured sinks, not a generic "ambiguous" message, so the fix
  is obvious without opening the config file.

**Example — nothing configured at all:**

```console
$ strata audit status
error: No audit git sink is configured for this solution. Reading audit records ('strata audit
status'/'strata audit changes') requires a configured 'git' audit sink, and none exists — add one
under spec.audit.sinks in your Configuration document. See docs/design/audit-trail.md and
docs/design/audit-commands.md.
$ echo $?
2
```

### Enumeration — glob and parse, don't reconstruct the write path

The write side's per-execution directory shape
([`resolve_audit_relative_path()`](../../src/strata/controllers/audit_path_resolution.py))
depends on a *deployment's own* `layers.segments`/`Configuration` at write
time — not something a read command can cheaply or reliably re-derive from
outside for every deployment that ever wrote a record, especially after a
solution's path conventions change over time. `[new]` Instead: recursively
glob `checkout_path / sink.path / "**/_manifest.json"`, parse each hit with
`DeploymentManifestModel.model_validate_json()`, and treat the manifest's
*own* fields (`deployment`, `started_at`, `status`, ...) as the source of
truth for filtering and sorting — never the directory path itself. A
manifest that fails to parse (corrupted file, pre-this-schema record from
an older strata version) produces a diagnostic warning and is skipped,
never aborts the whole command — one bad record must not hide every good
one, same "partial, honest result over a hard crash" instinct the sink
`required` flag already encodes elsewhere in this design.

`_metrics.json` sits alongside `_manifest.json` in the same directory but is
out of scope for the first cut — `status`/`changes` answer Q2/Q3/Q4
(audit-trail.md's compliance questions), which live entirely in the
manifest; metrics are a separate, measurement-oriented concern with no
compliance question attached yet.

**Example checkout layout**, for `sink.path: records` pushed by two
different deployments (`web-frontend` uses a `layers`-derived shape;
`db-migrations` has no `layers` block, so it falls back to
`workspace/deployment`):

```text
.strata/audit-read/audit-repo/main/        # [new] — this command's own checkout
└── records/
    ├── prod/eu-west/web-frontend/20261005-140211/
    │   ├── _manifest.json
    │   └── _metrics.json
    └── customer-a/db-migrations/20261004-091255/
        ├── _manifest.json
        └── _metrics.json
```

`checkout_path / "records" / "**/_manifest.json"` finds both hits
regardless of the directory shape above them — `status`/`changes` never
need to know *why* `prod/eu-west/web-frontend/...` and
`customer-a/db-migrations/...` are shaped differently, only what each
found manifest's own JSON says.

### `status` — the only command this phase builds

Group the glob-and-parse result (above) by `deployment`, keep only the
max-`started_at` manifest per group (or just the named `DEPLOYMENT`'s
latest, when given). Console: one line per deployment (name, status,
version, `deployed_by`, `completed_at`, whether `change_reference` is
present). JSON: `run.reporter.data = {"deployments": [...]}"`, matching
`graph_command.py`'s existing `isinstance(run.reporter, JsonReporter)`
convention exactly — no new output convention invented.

`changes` (no grouping, every manifest in range, `--since`/`--until`/
`--status` filters) shares this same glob-and-parse step but is deferred —
see "Deferred: `changes`" under Remaining Work.

**Example `--output json` for `strata audit status`** (same envelope shape
`build_envelope()` already produces for every command — `data` is the only
part specific to this one):

```json
{
  "command": "audit status",
  "ok": true,
  "errors": 0,
  "warnings": 0,
  "info": 0,
  "data": {
    "deployments": [
      {
        "deployment": "web-frontend",
        "status": "success",
        "version": "2.4.1",
        "deployed_by": "svc-pipeline-ado",
        "completed_at": "2026-10-05T14:02:11Z",
        "change_reference": { "system": "azure_devops", "id": "OPS-1234" }
      },
      {
        "deployment": "api-backend",
        "status": "failed",
        "version": "2.4.1",
        "deployed_by": "svc-pipeline-ado",
        "completed_at": "2026-10-05T13:47:02Z",
        "change_reference": null
      }
    ]
  }
}
```

### Failure classification

- No readable sink configured → `UsageError` (exit 2) — a configuration/
  invocation problem, same class `validate_command` uses for "not inside a
  solution". Checked first, with zero I/O — see "This check must run
  first" above.
- The sink's remote exists but the fetch itself fails (network, auth, a
  non-existent branch with no commits ever pushed) → `[new]` `AuditReadError`
  (a `SystemError` subclass, exit 1), the same shape `RemoteResolutionError`
  already uses for an equivalent real-environment failure — the
  configuration is fine, the environment is not.
- An individual manifest fails to parse → a diagnostic warning on that one
  record, command still exits 0 if everything else is fine — not fatal, per
  "Enumeration" above.
- A specific `DEPLOYMENT` was named and the sink is configured and reachable,
  but genuinely has no record for it yet → **not an error.** An info-level
  diagnostic ("no audit records found for deployment 'x'") plus an empty
  table/empty JSON `deployments: []`, exit 0. Distinct from every case
  above: the solution and the sink are both fine, there's just nothing to
  show yet — conflating this with a configuration problem would make a
  brand-new deployment's first-ever run look like a setup mistake.

### Full command flow and module contracts

Putting every mechanism above together, in the exact order `strata audit
status` executes them — the ordering itself is the design, not just the
pieces:

1. `context = open_solution(resolve_work_path(path)).require_valid()` —
   identical to every other command (`build run`, `deploy run`). Raises
   `ValidationError` if the solution itself is invalid, before anything
   audit-specific is even looked at.
2. Resolve the single effective `ConfigurationModel` (same "one declared
   source of truth" rule `audit_run.py`'s own `_single_configuration()`
   already applies for the write side).
3. **`resolve_readable_sink(configuration.spec.audit)`** — zero I/O,
   raises `UsageError` for zero or >1 enabled `git`-arm sinks. Always the
   first audit-specific thing that runs.
4. **`sync_read_checkout(context.root, remote, sink)`** — the only network
   operation in the whole command. Raises `[new]` `AuditReadError` on
   failure.
5. **`list_latest_per_deployment(checkout_path, sink, deployment=...)`** —
   glob, parse (skip + warn on a corrupt file), optional name filter,
   group-and-keep-latest.
6. Render: console table, or `run.reporter.data = {"deployments": [...]}"`
   for `--output json`.

`[new]` module contracts (signatures, not full bodies — exact
implementations are Implementation Plan work):

```python
# src/strata/controllers/audit_read.py

class AuditReadError(SystemError):
    """The configured audit sink could not be reached — fetch/clone failure.
    Exit 1, not UsageError: the configuration is fine, the environment isn't.
    """


@dataclass(frozen=True)
class DeploymentStatusRecord:
    deployment: str
    status: str
    version: str
    deployed_by: str
    completed_at: str
    change_reference: ChangeReferenceModel | None


def resolve_readable_sink(audit_config: AuditConfigModel | None) -> AuditGitSinkTargetModel:
    """Zero I/O. Raises UsageError for zero or >1 enabled git-arm sinks —
    the one check guaranteed to run before any git/network operation."""


def sync_read_checkout(root: Path, remote: SolutionRemoteModel, sink: AuditGitSinkTargetModel) -> Path:
    """Resolve `layout.audit_read_checkout_path(...)`, then clone/fetch/reset
    via the shared `ensure_synced_checkout()`. Raises AuditReadError."""


def list_latest_per_deployment(
    checkout_path: Path,
    sink: AuditGitSinkTargetModel,
    *,
    deployment: str | None,
    diagnostics: Diagnostics,
) -> list[DeploymentStatusRecord]:
    """Glob + parse + optional name filter + group-keep-latest-per-deployment."""


def audit_status(
    context: SolutionContext, deployment: str | None
) -> tuple[list[DeploymentStatusRecord], Diagnostics]:
    """Top-level orchestration the command calls — steps 2-5 above, in order."""
```

```python
# src/strata/commands/audit_command.py

@click.group("audit")
def audit_command() -> None:
    """Read back audit trail records from a configured durable sink."""


@audit_command.command("status")
@click.argument("deployment", required=False)
@click.option("--path", type=click.Path(file_okay=False, path_type=Path), default=None, help="...")
@output_option
@quiet_option
@verbose_option
def status_command(deployment: str | None, path: Path | None, output: str, quiet: bool, verbose: bool) -> None:
    """Show the latest audit record per deployment (or just DEPLOYMENT)."""
    with command_run("audit status", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        run.describe(solution=context.controller.solution.meta.name, root=context.root)
        records, diagnostics = audit_status(context, deployment)
        if output == "console":
            _print_status_table(records)
        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"deployments": [_to_json(r) for r in records]}
        run.ok = True
```

Note what the command body does **not** do: no `try`/`except` around
`audit_status()`. Both `UsageError` (step 3) and `AuditReadError` (step 4)
are `StrataError` subclasses, and `command_run()`'s own context manager
(`commands/run.py`) already catches every `StrataError` and converts it to
the right exit code — the "tell the user first" requirement falls directly
out of where `resolve_readable_sink()` is called in `audit_status()`, not
out of anything the command body has to handle itself.

### `strata audit changes` — range listing (designed 2026-10-05)

Everything `status` already established (read from the exact git-sink
destination, the single-sink assumption, glob-and-parse enumeration
instead of reconstructing the write path, fail-fast-before-any-I/O) stays
unchanged — `changes` is the same mechanism with a different final step
(list every record in range, not just the latest per deployment) and one
genuinely new failure mode (`--since`/`--until` can be malformed, which
`status` has no equivalent of).

**Command surface:**

- **`strata audit changes [DEPLOYMENT]`** — every audit record in range,
  sorted oldest to newest. DEPLOYMENT narrows to one, same as `status`.
- **`--since TEXT`** / **`--until TEXT`** — ISO-8601 date or datetime,
  inclusive bounds compared against each manifest's own `started_at`.
  Both optional; omit either/both for an unbounded range on that side.
- **`--status [success|partial|failed]`** — filters by the manifest's
  real outcome field. `click.Choice` enforces this set directly, so an
  invalid value is rejected by Click itself (its own usage error, exit 2)
  before any of this command's own code runs at all — correcting an
  earlier draft of this doc, written before `DeploymentManifestModel` was
  checked directly: `status` is a **tri-state** `Literal["success",
  "partial", "failed"]`, not a success/failed-only bool, and `partial` is
  real, intentional signal (a deploy that got through some stages but not
  all) a filter that only knew "success|failed" would silently misclassify.
- Same `--path`/`--output`/`--quiet`/`--verbose` as `status`. No `--sink`
  either, for the identical single-sink reasoning already decided above.

**Example invocations:**

```console
$ strata audit changes --since 2026-10-01 --status failed
DEPLOYMENT    STATUS   STARTED AT                 EXECUTION ID
api-backend   failed   2026-10-03T08:15:40+00:00  7e2f1234-5678-4abc-9def-0123456789ab
api-backend   failed   2026-10-05T13:44:50+00:00  a81d5678-1234-4abc-9def-0123456789cd

$ strata audit changes web-frontend --since 2026-10-01 --until 2026-10-04
  (no audit records found)
```

**Date-filter validation is a third zero-I/O "tell the user first" check,
not an afterthought.** `status` only ever had one way to fail before
touching git (`resolve_readable_sink()`); `changes` introduces a second,
independent one — a malformed `--since`/`--until` value — and it must be
checked at the exact same point in the sequence, before
`sync_read_checkout()` runs, for the identical reason: a typo in a date
flag should never cost a slow, confusing clone/fetch attempt before the
user finds out it was rejected.

```console
$ strata audit changes --since not-a-date
error: '--since' is not a valid ISO-8601 date or datetime: 'not-a-date'. Examples: 2026-10-01, 2026-10-01T00:00:00+00:00.
$ echo $?
2
```

**Parsing detail worth recording precisely, not glossed over:** every
`started_at` this command compares against is written by `audit_run.py`
as `datetime.now(timezone.utc).isoformat()` — always timezone-*aware*
UTC. A user's `--since 2026-10-01` parses via `datetime.fromisoformat()`
into a *naive* datetime (no `tzinfo`), and comparing a naive and an
aware datetime raises `TypeError` in Python, not a wrong-but-silent
answer — this would surface as an ugly crash, not a clean `UsageError`,
if not handled explicitly. `[new]` `_parse_range_bound()`: parse via
`datetime.fromisoformat()`, and if the result has no `tzinfo`, attach
`timezone.utc` explicitly (matching the one timezone convention every
real `started_at` in this system already uses) — raising `UsageError`
with the exact message shown above when parsing itself fails outright.

**Correction, found during a full review (2026-10-05): `--until` with a
bare date wasn't actually inclusive of that whole day.** The help text
claimed "inclusive", but `datetime.fromisoformat('2026-10-04')` parses to
that day's *midnight* — a record at `2026-10-04T18:00:00` would have been
silently excluded by `--until 2026-10-04`, the opposite of what "inclusive"
promises. Fixed: `_parse_range_bound()` takes an `end_of_day: bool = False`
parameter, passed `True` only for `--until` (never `--since`, where
"since the start of this day" is already the correct, intuitive reading of
a bare date) — when the given value has no time component, the bound is
pushed to that day's last microsecond instead of its first. An explicit
time component (`--until 2026-10-04T08:00:00`) is left untouched; the user
already named the exact moment they meant.

**Module contracts** (extending `controllers/audit_read.py` and
`commands/audit_command.py` — signatures, not full bodies):

```python
# src/strata/controllers/audit_read.py (additions)

@dataclass(frozen=True)
class DeploymentChangeRecord:
    """One audit record, ungrouped — `changes`'s own result shape, a
    sibling of `DeploymentStatusRecord` (status's group-keep-latest view)
    rather than a reuse of it: `changes` needs `execution_id`/`started_at`
    to distinguish multiple runs of the same deployment, which `status`'s
    one-row-per-deployment view has no use for."""

    execution_id: str
    deployment: str
    status: str
    started_at: str
    completed_at: str
    deployed_by: str
    version: str
    change_reference: ChangeReferenceModel | None


def _iter_manifests(checkout_path: Path, sink: AuditGitSinkTargetModel, diagnostics: Diagnostics):
    """The glob + parse + skip-corrupt-with-warning loop, extracted out of
    `list_latest_per_deployment()`'s own inline version once a second
    caller (`list_manifests_in_range()`) needed the identical enumeration
    — shared, not duplicated, the same DRY instinct Phase 2 already
    applied to `ensure_synced_checkout()`. Yields parsed
    `DeploymentManifestModel`s only; grouping/filtering stays with each
    caller."""


def list_manifests_in_range(
    checkout_path: Path,
    sink: AuditGitSinkTargetModel,
    *,
    deployment: str | None,
    since: datetime | None,
    until: datetime | None,
    status: str | None,
    diagnostics: Diagnostics,
) -> list[DeploymentChangeRecord]:
    """Every manifest matching all given filters, sorted oldest to newest
    by `started_at` — no grouping, unlike `list_latest_per_deployment()`."""


def audit_changes(
    context: SolutionContext,
    deployment: str | None,
    *,
    since: str | None,
    until: str | None,
    status: str | None,
) -> tuple[list[DeploymentChangeRecord], Diagnostics]:
    """Same fail-fast order as `audit_status()`, with one extra zero-I/O
    step first: parse/validate `since`/`until` (raises UsageError), then
    resolve_readable_sink() (raises UsageError), then sync_read_checkout()
    (raises AuditReadError), then list_manifests_in_range()."""
```

```python
# src/strata/commands/audit_command.py (additions)

@audit_command.command("changes")
@click.argument("deployment", required=False)
@click.option("--since", default=None, metavar="DATE", help="ISO-8601 date/datetime lower bound, inclusive.")
@click.option("--until", default=None, metavar="DATE", help="ISO-8601 date/datetime upper bound, inclusive.")
@click.option("--status", "status_filter", type=click.Choice(["success", "partial", "failed"]), default=None)
@click.option("--path", type=click.Path(file_okay=False, path_type=Path), default=None, help="...")
@output_option
@quiet_option
@verbose_option
def changes_command(...) -> None:
    """List every audit record in range, oldest to newest."""
    with command_run("audit changes", output=output, quiet=quiet, verbose=verbose) as run:
        context = open_solution(resolve_work_path(path)).require_valid()
        run.describe(...)
        records, diagnostics = audit_changes(context, deployment, since=since, until=until, status=status_filter)
        if output == "console":
            _print_changes_table(records)
        run.report(diagnostics, root=context.root)
        if isinstance(run.reporter, JsonReporter):
            run.reporter.data = {"changes": [_change_to_json(r) for r in records]}
        run.ok = True
```

**Failure classification — `changes`-specific additions** (everything
`status` already has — no/multiple sinks, unreachable remote, corrupted
manifest skipped with a warning, named-deployment-with-nothing-yet is not
an error — applies identically):

- A malformed `--since`/`--until` value → `UsageError` (exit 2), checked
  before `resolve_readable_sink()` even runs — the first thing this
  command checks, zero I/O, per "Date-filter validation" above.
- `--status` given an unrecognised value → Click's own usage error
  (exit 2), before this command's own code runs at all — not this
  command's responsibility to validate, `click.Choice` already owns it.
- A filter combination matching zero records (a real deployment, but
  nothing in the given date range, or no record with the given status)
  → **not an error**, same reasoning `status` already applies to a
  named deployment with nothing recorded yet: the sink is configured and
  reachable, there is just nothing matching. An info diagnostic, empty
  list, exit 0.

**Correction, found during a full review (2026-10-05): the empty-result
message must not claim the sink is empty when filters, not an empty sink,
are why nothing matched.** The first implementation reused
`list_latest_per_deployment()`'s own "no audit records found in this sink
yet" message unconditionally whenever `deployment` was `None` — but
`changes` has three more filters (`since`/`until`/`status`) that can
legitimately exclude every real record in a genuinely non-empty sink, and
claiming "nothing in this sink" in that case is actively wrong, not merely
vague (a real finding caught by actually checking message *content*, not
only the diagnostic *code*, against a sink with real records in it).
Fixed: `list_manifests_in_range()` now checks whether `since`/`until`/
`status` were given before falling back to the sink-is-empty message —
if any were, a new, distinct `audit_read_no_records_for_filters` code and
"no audit records match the given filters" message is used instead. The
true "nothing in this sink at all" message/code is now only reached when
`deployment` and every other filter are `None`, where it is guaranteed
accurate.

## Related Decisions

No new ADR — this extends the existing Layer 2/4 design in
[audit-trail.md](audit-trail.md) (itself grounded in ADR-0003's layering and
ADR-0015's identity-not-paths/`.strata/` discovery exclusion) rather than
introducing a new architectural decision. Reuses
[audit-sink-dispatch.md](audit-sink-dispatch.md)'s existing sink model
unchanged.

## Implementation Plan

Scoped to the minimum (2026-10-05) — `status` only, single sink only, no
retention. Phased the same way audit-trail.md's own Layer 2 Implementation
Plan was: one phase, one focused change, full check suite, then the next.
Nothing below is implemented yet.

**Explicitly out of scope for every phase below** (already decided above,
restated so a phase reviewer doesn't have to re-derive it): `--sink`/
multi-sink disambiguation, the `changes` subcommand, retention/pruning,
`audit diff`.

### Phase 1 — `layout.audit_read_checkout_path()` — ~~IMPLEMENTED (2026-10-05)~~

- Added `audit_read_checkout_path(root, remote, branch) -> Path` to
  `utils/layout.py`, returning `.strata/audit-read/<remote>/<branch>` —
  pure function, mirrors `audit_push_checkout_path()`'s exact shape and
  docstring style (same "why its own subdirectory" reasoning). Also
  updated `gitops_push_checkout_path()`'s own docstring to name the new
  population among the ones it can never collide with.
- Tests added to `tests/strata/utils/test_utils_layout.py` (not
  `test_layout.py` — corrected from this doc's earlier, inaccurate file
  name): keyed-by-`(remote, branch)`, lives under its own `audit-read/`
  subdirectory, and three explicit non-collision checks against
  `audit_push_checkout_path()`, `gitops_push_checkout_path()`, and
  `remote_checkout_path()` for the identical `(remote, branch)` pair —
  plus the same filesystem-purity check every other layout function gets.
  Full check suite green: mypy (136 files), ruff check, ruff format,
  import-linter (1 kept, 0 broken), pytest (1885 passed).

### Phase 2 — extract the shared checkout-sync helper — ~~IMPLEMENTED (2026-10-05)~~

- Extracted `audit_push.py`'s private `_ensure_checkout()` into a new
  `controllers/git_checkout.py::ensure_synced_checkout(url, branch,
  checkout_path) -> SyncResult` (clone-if-absent, else fetch + reset to
  `origin/<branch>` or the remote's default) — a refactor of existing,
  already-tested behaviour, not new logic. `audit_push.py` now calls the
  shared function and maps its `SyncResult` to its own `PushResult`.
- **Correction to this phase's original plan, found while implementing,
  not assumed:** "existing tests must stay green unmodified" turned out
  to be wrong once actually checked. `test_audit_push.py`'s `_capture()`
  helper monkeypatches `run_command` only on the `audit_push` module
  object — after the extraction, the clone/fetch/reset calls execute
  inside `git_checkout`'s own module namespace instead, so the old patch
  silently stopped observing them (and `test_clone_failure_short_circuits`
  would have silently exercised the *real* `git_checkout.run_command`,
  not the mock). Fixed by patching `run_command` on **both** module
  objects in `_capture()` and in that one inline fake — the tests
  themselves needed a small, necessary update, not zero changes.
- Added `tests/strata/controllers/test_git_checkout.py`: the extracted
  function's first-ever direct unit tests (it was previously only
  exercised indirectly through `push_audit_files()`) — fresh clone,
  already-checked-out skip-clone, branch-not-yet-on-remote fallback to
  `origin/HEAD`, completely-empty-remote (no reset, still success),
  clone/fetch/reset failure cases, and a real end-to-end test against a
  real local bare repo confirming a second sync picks up a commit landed
  independently in between.
  Full check suite green: mypy (137 files), ruff check, ruff format,
  import-linter (1 kept, 0 broken), pytest (1893 passed, up from 1885).

### Phase 3 — `controllers/audit_read.py` — ~~IMPLEMENTED (2026-10-05)~~

- `AuditReadError(SystemError)`, `DeploymentStatusRecord` (dataclass),
  `resolve_readable_sink()`, `sync_read_checkout()`,
  `list_latest_per_deployment()`, `audit_status()` — implemented as
  designed in "Full command flow and module contracts" above.
- Registered `AuditReadError` in `commands/exit_codes.py`'s
  `EXIT_CODE_BY_ERROR` (→ `EXIT_FAILURE`) — confirmed, not assumed, that
  `test_every_error_type_is_mapped()` really does walk every live
  `StrataError.__subclasses__()` and would have failed without this entry.
- **Found and fixed one real duplication while implementing, not
  pre-planned:** `audit_run.py`'s manifest filename constant was
  `_MANIFEST_FILENAME` — private, underscore-prefixed, the exact "don't
  import this elsewhere" signal `layout.py`'s own docstring warns about
  for path literals. Promoted it to a public `MANIFEST_FILENAME` (same
  module) rather than either duplicating the literal in `audit_read.py`
  or reaching past the underscore — `audit_read.py` now imports the one
  constant both the write and read sides glob/write against.
- **`sync_read_checkout()`'s scope absorbed one case the design draft
  hadn't explicitly bucketed:** a sink's remote resolving to the wrong
  `type` (not `git`) isn't caught by `require_valid()`'s reference check
  (`RemoteReference()` only confirms the *name* resolves, not which type
  it is — the identical gap `audit_push.py`'s own `push_audit_files()`
  already guards against). Folded into `AuditReadError` alongside a
  real sync failure, matching how `push_audit_files()` already treats the
  equivalent case as the same kind of outcome, rather than inventing a
  third failure bucket beyond the two "Failure classification" names.
- Tests (`tests/strata/controllers/test_audit_read.py`, 15 new): zero-sink/
  integration-only/disabled-sink/multiple-sink `UsageError` cases for
  `resolve_readable_sink()` (pure, no mocking needed — the function does
  no I/O at all); undeclared-remote and wrong-remote-type cases for
  `sync_read_checkout()` with hand-built models (mirrors
  `test_audit_push.py`'s own style); six pure-filesystem tests for
  `list_latest_per_deployment()` (nested glob, keep-latest, name filter,
  corrupted-file-skipped-with-warning, named-deployment-empty,
  nothing-at-all-empty) needing no git at all; one real end-to-end test
  against a real local bare repo pushing three manifests (two for one
  deployment at different times, one for another) via the existing
  `push_audit_files()`, then reading them all back through the full
  `audit_status()` orchestration — confirms latest-wins, multi-deployment
  grouping, and the named-empty-deployment case together, wired end to
  end rather than only unit-level.
  Full check suite green: mypy (138 files), ruff check, ruff format,
  import-linter (1 kept, 0 broken), pytest (1908 passed, up from 1893).


### Phase 4 — `commands/audit_command.py` — ~~IMPLEMENTED (2026-10-05)~~

- The `audit` group with only `status` registered, `_print_status_table()`
  console renderer (dynamic per-column width via `ljust()`, mirroring
  `values_command.py`'s `_print_declared_list()` rather than inventing a
  second table layout), `--output json` via `run.reporter.data`,
  registered in `cli.py` alongside the other command groups.
- `_to_json()` renders `DeploymentStatusRecord` → a plain dict rather than
  `dataclasses.asdict()` (`graph_command.py`'s own convention for its
  all-`str`-field `GraphEdge`) — `change_reference` is a pydantic model,
  not a nested dataclass, so `asdict()` would leave it as a live object
  `json.dumps()` cannot serialise rather than recursing into it.
  `.model_dump(exclude_none=True)` when present, `None` otherwise.
- Tests (`tests/strata/commands/test_commands_audit.py`, 10 new): full CLI
  round-trip (console and JSON, including the empty-sink case), the
  zero-sink and multiple-sink error messages and exit code 2 asserted
  end-to-end through the real CLI (not just at the controller layer, per
  this phase's own explicit ask), the named-`DEPLOYMENT` argument path and
  its empty-result case, an unreachable-remote exit-1 case, and "not
  inside a solution" exit 2.
  Full check suite green: mypy (139 files), ruff check, ruff format,
  import-linter (1 kept, 0 broken), pytest (1918 passed, up from 1908).

### Phase 5 — docs — ~~IMPLEMENTED (2026-10-05)~~

- Updated audit-trail.md's cross-references from "a remote-read design
  now exists" to "implemented": the Must-have MoSCoW entry, the former
  "Deferred — blocked on a real redesign" category (removed — no longer
  applies, since `audit status` is built), the "Layer 3 deferred"
  section's own Decision paragraph, the top status line, and a new
  changelog entry. `strata audit changes` stays a tracked scope cut there
  too, not silently implied as done.

### Phase 6 — `controllers/audit_read.py` additions for `changes` — ~~IMPLEMENTED (2026-10-05)~~

Grounded directly against the real, currently-shipped `list_latest_per_deployment()`
body (not re-derived from memory) — its inline loop is exactly:

```python
for manifest_path in sorted(search_root.rglob(MANIFEST_FILENAME)):
    try:
        manifest = DeploymentManifestModel.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, PydanticValidationError, ValueError) as exc:
        diagnostics.warning(f"could not read audit record: {exc}", source=str(manifest_path),
                             code="audit_read_manifest_unparseable")
        continue
    ...
```

Concrete steps, in order:

1. **Extract `_iter_manifests(search_root: Path, diagnostics: Diagnostics) -> Iterator[DeploymentManifestModel]`**
   — the `sorted(search_root.rglob(...))` + try/except parse/warn/skip
   block above, verbatim, yielding each successfully-parsed manifest.
   Refactor of already-shipped, already-tested Phase 3 code — same
   "extract once a second real caller needs it" discipline Phase 2
   applied to `ensure_synced_checkout()`, not a speculative abstraction
   built ahead of need.
2. **Rewrite `list_latest_per_deployment()` to consume `_iter_manifests()`**
   instead of its own inline loop — behaviour must stay byte-identical;
   this is the step `test_audit_read.py`'s existing 15 tests (unchanged)
   prove didn't regress.
3. **Add `_parse_range_bound(value: str | None, flag_name: str) -> datetime | None`**
   — `None` in, `None` out (an omitted `--since`/`--until`). Otherwise
   `datetime.fromisoformat(value)`; if the result's `tzinfo is None`,
   `.replace(tzinfo=timezone.utc)`. Catches `ValueError` from
   `fromisoformat()` and re-raises as `UsageError` with the exact message
   from "`strata audit changes` — range listing" above, naming
   `flag_name` (`--since` or `--until`) so one function serves both
   flags without duplicating the message text per-flag.
4. **Add `DeploymentChangeRecord`** (frozen dataclass): `execution_id`,
   `deployment`, `status`, `started_at`, `completed_at`, `deployed_by`,
   `version`, `change_reference` — exactly the fields in the design's
   module contract.
5. **Add `list_manifests_in_range(checkout_path, sink, *, deployment, since, until, status, diagnostics) -> list[DeploymentChangeRecord]`**
   — calls `_iter_manifests(checkout_path / sink.path, diagnostics)`,
   applies (in order) the `deployment` name filter, the `since`/`until`
   bounds against `manifest.started_at` (parsed once per manifest via
   `datetime.fromisoformat()` — `started_at` is always aware UTC on a
   real record, per `audit_run.py`, so no naive-handling needed on this
   side, only on the user-supplied bound), the `status` filter: builds
   one `DeploymentChangeRecord` per surviving manifest (same
   `or "unknown"`/`or manifest.started_at` fallbacks
   `list_latest_per_deployment()` already uses for optional fields), then
   `sorted(..., key=lambda r: r.started_at)` (oldest to newest — the
   opposite order from `status`'s alphabetical-by-deployment). Empty
   result → reuse the exact same `diagnostics.info(...)` codes
   `list_latest_per_deployment()` already emits
   (`audit_read_no_records_for_deployment` / `audit_read_no_records`) —
   one concept ("nothing matched"), one code, regardless of which
   command asked, not a duplicate pair of near-identical codes.
6. **Add `audit_changes(context, deployment, *, since, until, status) -> tuple[list[DeploymentChangeRecord], Diagnostics]`**
   — `_parse_range_bound()` for both bounds first (zero I/O, raises
   `UsageError` before anything else runs), then the identical
   `_single_configuration()` → `resolve_readable_sink()` →
   `sync_read_checkout()` sequence `audit_status()` already uses, then
   `list_manifests_in_range()`.

**Tests** (`tests/strata/controllers/test_audit_read.py`, extended, not a
new file — 16 new, 31 total in the file): the existing 15 `status`-era
tests pass completely unchanged, confirming the `_iter_manifests()`
extraction really is behaviour-preserving; 5 direct `_parse_range_bound()`
unit tests (omitted-flag, already-aware passthrough, naive-promoted-to-UTC
— the exact bug this design calls out, asserted directly rather than
"it works" — malformed value, and that the flag name appears in the
message); 9 pure-filesystem `list_manifests_in_range()` tests
(returns-every-record not just latest, oldest-to-newest sort,
since/until/status/deployment filters individually and combined,
corrupted-manifest-skipped, both zero-match cases); 2 zero-I/O
`audit_changes()` ordering tests, each passing `None` as `context` itself
and confirming the date error (not an `AttributeError` on `context`) is
what surfaces — the starkest possible proof the parse genuinely runs
before `context` is ever touched. Extended (not duplicated) the existing
real end-to-end bare-repo test: reused the same three already-pushed
manifests (gave the `older` one `status: failed` to make the status
filter meaningful) and added `audit_changes()` assertions for the
unfiltered list, a `status` filter, a combined `since`/`until` range, the
malformed-date `UsageError`, and the zero-match case — no second bare
repo stood up.
Full check suite green: mypy (139 files), ruff check, ruff format,
import-linter (1 kept, 0 broken), pytest (1934 passed, up from 1918).

### Phase 7 — `commands/audit_command.py` additions for `changes` — ~~IMPLEMENTED (2026-10-05)~~

Grounded against the real, currently-shipped `status_command`/
`_print_status_table`/`_to_json` shapes — `changes` mirrors each one-for-one:

1. **Add the `changes` subcommand**: `@audit_command.command("changes")`,
   `deployment` positional (optional, same as `status`), `--since`/
   `--until` (`default=None, metavar="DATE"`), `--status` as
   `"status_filter"` (avoids shadowing the `click.option` name against
   Python's own `status` — `type=click.Choice(["success", "partial",
   "failed"])`, `default=None`), the same `--path`/`output_option`/
   `quiet_option`/`verbose_option` as `status_command`. Body follows
   `status_command`'s exact shape: `open_solution(...).require_valid()`,
   `run.describe(...)`, call `audit_changes(context, deployment,
   since=since, until=until, status=status_filter)`, console-print if
   `output == "console"`, `run.report(diagnostics, root=context.root)`,
   `isinstance(run.reporter, JsonReporter)` → `run.reporter.data =
   {"changes": [...]}"`, `run.ok = True`. No `try`/`except` here either —
   identical reasoning to `status_command`'s own (every raised error is a
   `StrataError`, already caught by `command_run()`).
2. **Add `_print_changes_table(records: list[DeploymentChangeRecord]) -> None`**
   — same dynamic-`ljust()` convention as `_print_status_table()`, same
   `"  (no audit records found)"` empty placeholder, columns `DEPLOYMENT`/
   `STATUS`/`STARTED AT`/`EXECUTION ID` (per the worked example above —
   deliberately narrower than `status`'s column set; `version`/
   `deployed_by`/`change_reference` are JSON-only detail for `changes`,
   not console columns, to keep an oldest-to-newest list of many rows
   scannable).
3. **Add `_change_to_json(record: DeploymentChangeRecord) -> dict[str, Any]`**
   — distinct name from `status_command`'s own `_to_json()` (not a
   reused/overloaded name in the same module): same
   `change_reference.model_dump(exclude_none=True) if ... else None`
   handling `_to_json()` already established, plus `execution_id` and
   `started_at`.
4. No change needed in `cli.py` — `audit_command` (the group) is already
   registered; `changes` is a subcommand of it, same as `status`.

**Tests** (`tests/strata/commands/test_commands_audit.py`, extended, not
a new file — 16 total: 10 existing + 6 new): full CLI round-trip (console
and JSON, oldest-to-newest ordering asserted on real output) using the
same `_solution_with_sink()`/`_push_manifest()` helpers already in the
file (both extended with an optional `status` kwarg, default `"success"`,
so `changes`'s filter tests could push `failed`/`success` records without
touching any existing call site); the malformed-`--since` case asserted
end-to-end through the real CLI, exit code 2, with an explicit assertion
that the "No audit git sink is configured" message does *not* appear —
proof the date check really did run first, not just that the right code
came back; the `--status` invalid-choice case (Click's own rejection,
confirmed distinct from this command's own code); `DEPLOYMENT`/`--since`/
`--until`/`--status` combined via the real CLI against three pushed
manifests across two deployments; the zero-match case reports info not
error, matching `status`'s own standard.
Full check suite green: mypy (139 files), ruff check, ruff format,
import-linter (1 kept, 0 broken), pytest (1940 passed, up from 1934).
Smoke-tested the real installed `strata audit changes --help` output
directly, matching Phase 4's own standard for `status`.

`strata audit changes` is now a real, working command — both commands
this doc designs are fully implemented.

## Remaining Work / Open Questions

All 7 implementation phases are complete — `strata audit status` and
`strata audit changes` are both real, working commands as of 2026-10-05.
What's left here is the deliberate scope cuts recorded during design, not
a task list for either command's design itself.


- **Multi-sink reads** (a `--sink NAME` flag, merge + dedup by
  `execution_id` across more than one `git`-arm sink) — decided out of
  scope for now, not merely unconfirmed: neither real config (this repo's
  dogfood example nor the one real consumer) configures even a single
  audit sink today, so designing a disambiguation UI ahead of that is
  solving a problem nobody has hit yet. Zero or multiple configured
  sinks both fail loudly (`UsageError`) rather than guessing.
- **No retention/pruning.** A sink's remote branch only grows —
  `AuditConfigModel`'s own reserved-but-unread `retention_days` field
  (commented out, per that model's docstring) hints v1 considered this and
  didn't finish it either. Decided out of scope for this phase, not an
  open question — revisit only once a real remote has accumulated enough
  history that it's an actual problem, not a hypothetical one.

**Still a genuinely open question:**

- **Relationship to `audit diff`** (audit-trail.md's separate Should-have,
  full before/after value diffing) — `changes` lists records (design
  complete, see above); it does not diff two of them against each other.
  Likely a natural extension once `changes` ships (it already has every
  manifest in hand to diff consecutive pairs), but deliberately not
  bundled into this design to keep each phase's scope small and obvious,
  matching the discoverability lesson already recorded against v1's gate
  system in audit-trail.md.

## Changelog

- 2026-10-05: Created. Drafted the full read-path design — git-sink-backed
  enumeration (not local files, per audit-trail.md's rejected first
  attempt), a dedicated `audit-read/` checkout namespace distinct from the
  push side, glob-and-parse manifest discovery instead of reconstructing
  the write path, `status`/`changes` command shapes, and failure
  classification. No code written yet.
- 2026-10-05: **Scoped down to the minimum**, per direct feedback: (1)
  single `git`-arm sink assumed for now — no `--sink` flag; zero or
  multiple configured sinks both fail loudly rather than building a
  disambiguation UI for a need nothing today actually has; (2) no
  retention/pruning in this phase — moved from an open question to a
  decided scope cut; (3) `status` is the only command this phase builds —
  `changes` shares its entire mechanism and is explicitly deferred rather
  than built alongside it. No code changed — scope correction only.
- 2026-10-05: **Designed the full `audit status` command end to end**, per
  a direct request, with explicit emphasis on "tell the user first" when
  nothing is configured. Added: the exact ordering guarantee (the
  zero-I/O sink check runs before `sync_read_checkout()` ever touches
  git, confirmed against `require_valid()`'s own real behavior — it
  already loads `ConfigurationModel` into memory, so the check needs no
  extra loading step) and the exact message text for the zero-sink and
  multiple-sink cases; a full step-by-step command flow; concrete module
  contracts for `controllers/audit_read.py` and
  `commands/audit_command.py` (including a `[new]` `AuditReadError`
  distinct from the config-time `UsageError`); a fourth failure case
  (`DEPLOYMENT` named but genuinely has no records yet — not an error,
  confirmed distinct from a configuration problem). Replaced the
  numbered phase list with a dedicated "Implementation Plan" section
  (5 phases, matching audit-trail.md's own Layer 2 Implementation Plan
  style), confirming along the way that raising `UsageError`/
  `AuditReadError` from inside the controller needs no try/except in the
  command body — `command_run()`'s existing context manager
  (`commands/run.py`) already catches every `StrataError`. No code
  written yet — design only.
- 2026-10-05: **Implemented Phase 1** of the Implementation Plan:
  `layout.audit_read_checkout_path()`, plus six new tests in
  `tests/strata/utils/test_utils_layout.py` (the real file — this doc had
  named it `test_layout.py` until now, corrected while implementing).
  Confirmed, rather than assumed, that no direct unit tests for
  `audit_push_checkout_path()`/`gitops_push_checkout_path()` actually live
  in that file today (they're exercised indirectly via real-checkout
  round-trip tests in `test_audit_push.py`/`test_integrations_git_push.py`
  instead) — added the first direct collision tests for all three
  push/read/pinned populations rather than only for the new function in
  isolation. Full check suite green: mypy (136 files), ruff check, ruff
  format, import-linter (1 kept, 0 broken), pytest (1885 passed, up from
  1879). Phases 2-5 (shared checkout-sync extraction, `audit_read.py`,
  `audit_command.py`, docs) remain unbuilt.
- 2026-10-05: **Implemented Phase 2** of the Implementation Plan: extracted
  `audit_push.py`'s private `_ensure_checkout()` into
  `controllers/git_checkout.py::ensure_synced_checkout()`, re-pointed
  `audit_push.py` at it, and added its first-ever direct unit tests
  (`test_git_checkout.py`) — previously only exercised indirectly through
  `push_audit_files()`. Found and fixed a real gap in this phase's own
  original plan while implementing it, not assumed away: the claim that
  "existing tests must stay green unmodified" was wrong —
  `test_audit_push.py`'s `_capture()` helper only monkeypatched
  `run_command` on the `audit_push` module object, which stopped
  observing the clone/fetch/reset calls once they moved to
  `git_checkout`'s own namespace; fixed by patching both module objects.
  Full check suite green: mypy (137 files), ruff check, ruff format,
  import-linter (1 kept, 0 broken), pytest (1893 passed, up from 1885).
  Phases 3-5 (`audit_read.py`, `audit_command.py`, docs) remain unbuilt.
- 2026-10-05: **Implemented Phase 3** of the Implementation Plan:
  `controllers/audit_read.py` (`resolve_readable_sink()`,
  `sync_read_checkout()`, `list_latest_per_deployment()`,
  `audit_status()`, `AuditReadError`), registered in `exit_codes.py`.
  Two real findings while implementing, folded into the design rather
  than left as a silent deviation: (1) promoted `audit_run.py`'s private
  `_MANIFEST_FILENAME` to a public `MANIFEST_FILENAME` instead of
  duplicating the literal, once a second module genuinely needed it; (2)
  a sink's remote resolving to the wrong `type` isn't caught by
  `require_valid()`'s reference check (name-only, not type-aware) — folded
  into `AuditReadError` alongside a real sync failure rather than adding a
  third failure bucket, matching how `audit_push.py` already treats the
  identical case. 15 new tests in `test_audit_read.py`: pure config-check
  unit tests for `resolve_readable_sink()` (zero I/O), hand-built-model
  tests for `sync_read_checkout()`'s defensive branches, pure-filesystem
  tests for `list_latest_per_deployment()` (no git needed), and one real
  end-to-end test pushing three manifests across two deployments via the
  existing `push_audit_files()` and reading them all back through the
  full `audit_status()` orchestration. Full check suite green: mypy (138
  files), ruff check, ruff format, import-linter (1 kept, 0 broken),
  pytest (1908 passed, up from 1893). Phases 4-5 (`audit_command.py`,
  docs cross-link) remain unbuilt.
- 2026-10-05: **Implemented Phase 4** of the Implementation Plan:
  `commands/audit_command.py` — the `audit status` command (`audit`
  group registered in `cli.py`, second group after `sln`), a
  `_print_status_table()` console renderer, and `--output json` via
  `run.reporter.data`. Confirmed directly against real code rather than
  assumed: `dataclasses.asdict()` (`graph_command.py`'s own convention)
  would leave `DeploymentStatusRecord.change_reference` — a pydantic
  model, not a dataclass — as a live object `json.dumps()` can't
  serialise, so `_to_json()` handles it explicitly via
  `.model_dump(exclude_none=True)`. 10 new tests in
  `test_commands_audit.py`, confirming end to end through the real CLI
  (not just at the controller layer, per this phase's own explicit ask):
  console and JSON round-trips, the zero-sink/multiple-sink error
  messages and exit code 2, an unreachable-remote exit 1, "not inside a
  solution" exit 2, the named-`DEPLOYMENT` filter and its empty-result
  case. Smoke-tested the real installed `strata audit status --help`
  output directly, not just the test suite. Full check suite green: mypy
  (139 files), ruff check, ruff format, import-linter (1 kept, 0 broken),
  pytest (1918 passed, up from 1908). Only Phase 5 (updating
  audit-trail.md's cross-reference) remains.
- 2026-10-05: **Implemented Phase 5** of the Implementation Plan — the
  final phase, docs only, no code. Updated audit-trail.md's cross-references
  from "a design exists" to "implemented": the Must-have MoSCoW entry (now
  names `strata audit status` as shipped, while keeping the category open
  since `audit changes` — the actual ISAE 3402 "enumerable sample over a
  period" ask — isn't built yet, not papering over the gap); removed the
  "Deferred — blocked on a real redesign" category entirely (the reasoning
  that put Layer 3 there, "not validly designed yet," no longer applies);
  updated the "Layer 3 deferred" section's own Decision paragraph and the
  doc's top status line. Updated this doc's own header `Status:` from
  `draft` to `current` and closed out "Remaining Work" accordingly — all
  5 phases are now complete.
- 2026-10-05: **Designed `strata audit changes` end to end**, per a
  direct request, reusing every mechanism `status` already established
  (single-sink resolution, glob-and-parse enumeration, fail-fast-before-
  any-I/O) rather than redesigning from scratch. Corrected one inaccuracy
  found while grounding the design in real code rather than the earlier
  draft's assumption: `DeploymentManifestModel.status` is a **tri-state**
  `Literal["success", "partial", "failed"]`, not success/failed-only —
  `--status` uses `click.Choice` over the real three values, so `partial`
  (a real, intentional outcome) can't be silently excluded by a filter
  that only knew two. Identified and specified a genuinely new failure
  mode `status` never had: a malformed `--since`/`--until` value, handled
  as a third zero-I/O "tell the user first" check (parsed before
  `resolve_readable_sink()`), plus the concrete naive-vs-aware-datetime
  comparison bug this needs to avoid (`TypeError` if a user's
  `--since 2026-10-01` parses naive while every real `started_at` is
  timezone-aware UTC — `_parse_range_bound()` attaches UTC explicitly to
  a naive parse rather than letting the comparison crash). Added module
  contracts (`DeploymentChangeRecord`, a shared `_iter_manifests()`
  generator extracted from `status`'s already-shipped
  `list_latest_per_deployment()` rather than duplicated,
  `list_manifests_in_range()`, `audit_changes()`, the `changes` CLI
  subcommand) and two new Implementation Plan phases (6-7). Updated this
  doc's header status line and Remaining Work to reflect "designed, not
  yet implemented" rather than "deferred." No code changed — design only.
- 2026-10-05: **Fleshed out Phases 6-7 into a concrete, ready-to-implement
  checklist**, per a direct request for an implementation plan. Grounded
  directly against the real, currently-shipped body of
  `list_latest_per_deployment()` (quoted verbatim) rather than
  re-describing the refactor from memory, so the `_iter_manifests()`
  extraction step names the exact code being moved. Spelled out
  `_parse_range_bound()`'s signature precisely (`flag_name` parameter, so
  one function serves both `--since` and `--until` without duplicating
  the error message per flag) and `list_manifests_in_range()`'s filter
  order. Decided, rather than leaving implicit, that the empty-result
  info diagnostics reuse `list_latest_per_deployment()`'s existing codes
  (`audit_read_no_records_for_deployment`/`audit_read_no_records`) rather
  than minting a near-duplicate pair for `changes` — one concept, one
  code, regardless of which command asked. Specified that both phases
  extend the existing `test_audit_read.py`/`test_commands_audit.py` files
  rather than new ones, reusing already-established fixtures. No code
  changed — plan only, ready for implementation.
- 2026-10-05: **Implemented Phase 6**: `_iter_manifests()` extracted from
  `list_latest_per_deployment()`'s own inline loop (its existing 15 tests
  pass completely unchanged, confirming the extraction preserved
  behaviour exactly); `_parse_range_bound()`, `DeploymentChangeRecord`,
  `list_manifests_in_range()`, `audit_changes()` added as designed. 16
  new tests in `test_audit_read.py` (31 total in the file): direct
  `_parse_range_bound()` unit tests including the naive-promoted-to-UTC
  case asserted explicitly; pure-filesystem `list_manifests_in_range()`
  tests for every filter individually and combined, sort order, and both
  empty-result cases; two zero-I/O `audit_changes()` tests that pass
  `None` as `context` itself and still get the date error, not an
  `AttributeError` — the starkest possible proof the parse runs before
  `context` is ever touched; extended the existing real end-to-end
  bare-repo test to also exercise `audit_changes()` against the same
  three already-pushed manifests, rather than standing up a second bare
  repo. Full check suite green: mypy (139 files), ruff check, ruff
  format, import-linter (1 kept, 0 broken), pytest (1934 passed, up from
  1918). Only Phase 7 (`commands/audit_command.py`'s `changes`
  subcommand) remains before `strata audit changes` is a real, working
  command.
- 2026-10-05: **Implemented Phase 7** — the final phase. `commands/
  audit_command.py`'s `changes` subcommand (mirroring `status_command`'s
  exact shape one-for-one), `_print_changes_table()` (narrower console
  columns than `status` by design — `version`/`deployed_by`/
  `change_reference` are JSON-only), `_change_to_json()`. 6 new tests in
  `test_commands_audit.py` (16 total: 10 existing + 6 new), extending
  `_manifest_json()`/`_push_manifest()` with an optional `status` kwarg
  (default `"success"`, no existing call site touched) so the new filter
  tests could push `failed` records: the malformed-`--since` case
  explicitly asserts the "No audit git sink is configured" message does
  *not* appear, proving the date check ran first rather than merely
  returning the right exit code; the `--status` invalid-choice case;
  `DEPLOYMENT`/`--since`/`--until`/`--status` combined through the real
  CLI; the zero-match case. Smoke-tested the real installed `strata audit
  changes --help` output directly, matching Phase 4's own standard. Full
  check suite green: mypy (139 files), ruff check, ruff format,
  import-linter (1 kept, 0 broken), pytest (1940 passed, up from 1934).
  All 7 Implementation Plan phases are now complete — `strata audit
  status` and `strata audit changes` are both real, working commands.
- 2026-10-05: **Full review of both commands, per a direct request** — a
  line-by-line pass over `audit_read.py`/`audit_command.py` and every
  test, not a re-read of the design. Found and fixed three real bugs,
  none caught by the existing test suite because each needed checking
  message *content* or a specific boundary case the original tests
  happened not to exercise:
  1. **`resolve_readable_sink()`'s error messages hardcoded "'strata
     audit status' reads from..."** even though the function is shared
     by `audit_changes()` too — misleading when the same check failed for
     `strata audit changes`. Fixed to name both commands neutrally.
  2. **`--until` with a bare date wasn't actually inclusive of that
     whole day** — `--until 2026-10-04` parsed to midnight, silently
     excluding anything later that day despite the help text's own
     "inclusive" claim. Fixed via `_parse_range_bound(..., end_of_day=True)`
     for `--until` only.
  3. **`list_manifests_in_range()`'s empty-result message claimed "nothing
     in this sink"** even when real records existed and `since`/`until`/
     `status` filters were the actual reason nothing matched — found by
     checking a real end-to-end test's diagnostic *message*, not just its
     *code*. Fixed with a new, distinct `audit_read_no_records_for_filters`
     code/message, reserving the "nothing in this sink" message for when
     it's actually true.
  Also minor: `changes` records now sort by the already-parsed `datetime`
  rather than the raw `started_at` string (string-sort happened to agree
  with datetime-sort for every real UTC-isoformatted record, but didn't
  need to rely on that assumption once the parsed value was already in
  hand). 8 new regression tests added across `test_audit_read.py` and
  `test_commands_audit.py`, each named for the bug it guards against,
  plus updated the one existing assertion the fix itself changed the
  behaviour of. Updated this doc's documented exact message text and
  added correction notes in both the "Sink selection" and `changes`
  design sections rather than silently editing them. Full check suite
  green: mypy (139 files), ruff check, ruff format, import-linter (1
  kept, 0 broken), pytest (1948 passed, up from 1940).

