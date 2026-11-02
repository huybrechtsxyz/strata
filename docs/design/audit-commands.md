# Audit read commands (`strata audit status`/`strata audit changes`) — Design

- Status: current — both `strata audit status` and `strata audit changes`
  are implemented (2026-10-05, all 7 Implementation Plan phases complete).
- Last updated: 2026-10-05

## Overview

This is Layer 3 of [audit-trail.md](../work/audit-trail.md)'s four-layer model — the
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
  docs/work/audit-trail.md and docs/design/audit-commands.md."*
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
under spec.audit.sinks in your Configuration document. See docs/work/audit-trail.md and
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
[audit-trail.md](../work/audit-trail.md) (itself grounded in ADR-0003's layering and
ADR-0015's identity-not-paths/`.strata/` discovery exclusion) rather than
introducing a new architectural decision. Reuses
[audit-sink-dispatch.md](audit-sink-dispatch.md)'s existing sink model
unchanged.


## History

- Multi-sink reads (a `--sink NAME` flag, merge+dedup across more than one `git`-arm sink) are deliberately out of scope - no real config (this repo's dogfood example or the one real consumer) configures even a single audit sink today, so zero or multiple configured sinks both fail loudly rather than building a disambiguation UI for a need nothing has hit yet.
- No retention/pruning - a sink's remote branch only grows; revisit once a real remote has actually accumulated enough history to be a problem, not a hypothetical one.
- `audit changes` deliberately shares `audit status`'s entire mechanism and was built as a second phase of the same design rather than a separate one.
- Still genuinely open: whether `audit changes` should grow into `audit diff` (full before/after value diffing) - likely a natural extension since `changes` already has every manifest in hand to diff consecutive pairs, but deliberately not bundled in to keep each phase's scope small and obvious.
