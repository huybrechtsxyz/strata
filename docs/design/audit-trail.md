# Audit Trail — v1 Capability Catalog and v2 Design Questions

- Status: draft — real-usage scoping done, Layer 1 shipped, and the
  remaining open questions resolved (2026-09-30); ready for a Layer 2
  Implementation Plan next
- Last updated: 2026-09-30

## Overview

v2 has no audit trail yet — `ConfigurationSpecModel` deliberately defers
`spec.audit` alongside `zones`/`remotes`/`policies`/`paths` (see that model's
own docstring, and gap #7 in [`docs/_gap_v1.md`](../_gap_v1.md)). Before
designing v2's version, this doc catalogs what v1 actually built, across
three rounds of iteration, so the v2 design starts from evidence rather than
from v1's documentation alone — consistent with this repo's "evidence over
assumption" convention
([`.github/copilot-instructions.md`](../../.github/copilot-instructions.md)).

v1's own audit subsystem was, by its own admission, largely broken: a
2026-08-06 measurement of a real 18,853-entry `.strata/audit.log` found it
was 95% VS Code polling and pytest noise, contained **zero** deploy or build
events, and half of its declared configuration (`policy.events`) was read by
nothing at all (v1 ADR-0066, "Context and Problem Statement"). v1 fixed this
in place with a substantial rewrite. This doc records **what v1 ended up
with after that rewrite** (the target design, not the broken first cut) —
that is the more useful baseline for v2 to react to. Where the earlier,
defective behavior is relevant as a lesson (not a capability to reproduce),
it's called out explicitly under "Lessons from v1's own defects" below.

The user asked for "a complete new way of working" for v2's audit trail —
this doc is deliberately scoped to the *capability catalog* (what v1 could
do, and why), not yet a v2 proposal. The "what should v2's audit provide"
question is captured as open design questions at the end, to be resolved in
a follow-up before implementation starts.

**Source (external, not part of this workspace):** all of this is read
directly from v1's installed source at `e:\SourcesXYZ\strata`
(`src/strata/models/audit_config_model.py`, `controllers/audit_controller.py`,
`commands/audit/*.py`, `logger/audit.py`, `models/deploy_log_model.py`,
`models/deployment_manifest_model.py`, `models/sbom_model.py`,
`models/deployment_outputs_model.py`, `models/change_reference_model.py`,
`controllers/repository_controller.py`, `integrations/git.py`) and v1's
`docs/decisions/0018-deployment-audit-traceability.md` and
`0066-audit-event-routing-policy-model.md`. Not part of the strata-v2
workspace, so not linked as workspace files.

## Current Design (v1) — What v1's Audit Trail Actually Provides

### Three subsystems, one configuration block

v1's final design (post ADR-0066) unifies what had been three unrelated
subsystems under a single `spec.audit` block on the **configuration**
document (never on environment — see "Configuration lives with governance,
not with the audited party" below):

| Subsystem                                              | Records                                            | Reaches sinks                  |
| ------------------------------------------------------ | -------------------------------------------------- | ------------------------------ |
| Journal (`logger/audit.py` → `.strata/audit.log`)      | Every mutating CLI command invocation              | No — local only                |
| Deploy-log (`AuditController` → `.strata/deploy-log/`) | Deployment outcomes, full detail                   | Yes, via `forward()`           |
| Policy + sinks (`AuditConfigModel`)                    | Declares which event types exist and where they go | N/A — the routing layer itself |

```
spec.audit
├── policy      → which event types are active (governance, class-aware defaults)
├── journal     → local record: path, rotation, retention
├── sinks       → outward destinations (routing only — no transport config)
├── structure / deploy_log_path / repository  → deploy-log disk layout + git push
```

### Compliance driver

The whole subsystem exists to answer four questions an ISO 27001 (A.12.1.2
Change Management) or ISAE 3402 Type II controls audit asks of every
infrastructure change (v1 ADR-0018):

1. **What changed** — specific values before/after, with evidence.
2. **Why it changed** — business justification / change ticket reference.
3. **Who approved** — identity of approver(s) and date.
4. **How it was applied** — mechanism, CLI version, commit SHA, timestamps.

v1's answer is a **three-layer evidence model**, deliberately not a single
mechanism, because no one layer alone satisfies an auditor:

| Layer                        | Question answered                     | Where it lives                                       | Survives a hostile repo admin?       |
| ---------------------------- | ------------------------------------- | ---------------------------------------------------- | ------------------------------------ |
| 1 — PR template + extraction | Why, who approved                     | Config repo's PR history (git, GitHub)               | No — git is mutable by repo admin    |
| 2 — Deploy-log               | What changed, how applied             | `.strata/deploy-log/` (local, optionally git-pushed) | No, unless pushed to a remote        |
| 3 — CLI reporting            | Cross-deployment queries over Layer 2 | `strata audit changes/diff`                          | N/A — a view, not a store            |
| 4 — SIEM/sink forwarding     | Long-term, tamper-resistant retention | External system (Splunk, Sentinel, webhook)          | Yes — that's the point of this layer |

v1 is explicit that Layers 1–3 alone do **not** meet a strict
non-repudiation bar (git is mutable by a repo admin); Layer 4 (or a future
external state service, v1 ADR-0065) is what closes that gap. This tiering
— "local record + queryable + tamper-resistant is a genuinely separate
concern from the other two" — is probably the single most transferable idea
in the whole design, independent of which mechanism does each job.

### Layer 1 — Process evidence (PR template + extraction)

Two parts, no code beyond a Jinja2-templated scaffold:

- **PR/issue templates**, scaffolded into every config repo by `strata sln
  init`/`sln update`: a GitHub issue form (`deployment-change-request.yml`
  — change ticket, target environment, risk level, what/why, rollback) and
  a PR template (before/after table, risk assessment, author/reviewer
  checklists). Idempotent — never overwrites an existing file.

  > **This half is no longer just a v1 capability — it's real in v2 too
  > (2026-09-30).** [`strata sln init`/`strata sln update`](solution-scaffolding.md)
  > are built and tested: they scaffold both files (matching this
  > description, with v2-specific CLI wording), idempotently, never
  > overwriting a `.github/` file a repo has since customized. The
  > `gh`-based extraction half below (`enrich_with_pr_data()`) remains
  > unbuilt — the design ported only the scaffolding mechanism, not the
  > later PR-enrichment step, which needs `deploy run` to write a real
  > deploy-log/manifest first (Layer 2, still open — see "v2 Design
  > Notes" below).

- **Automated PR extraction**, done by `AuditController.enrich_with_pr_data()`
  using the `gh` CLI: given the deploy's commit SHA, finds the merged PR
  that produced it and pulls `number`, `title`, `url`, `author`, `merged_by`,
  `merged_at`, `labels`, `files_changed` (approvers are modeled but not
  populated by the current extraction call). Embedded into the deploy-log
  entry as a `pull_request` sub-object. Best-effort: no `gh`, no network, or
  no matching PR (e.g. a direct push) all degrade to `pull_request: null`
  without failing the deploy.

### Layer 2 — Deploy-log (the core evidence artifact)

One `DeployLogModel` JSON record per deployment execution, written
automatically by the deploy command on completion (success **or**
failure) — this is the artifact everything else in the subsystem is built
around.

**Schema (`models/deploy_log_model.py`):**

| Field                                            | Purpose                                                                                                                                                                                                                                                             |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `execution_id`                                   | UUID4 — the correlation key shared with the journal and every sink payload                                                                                                                                                                                          |
| `timestamp`, `version`, `command`                | When, which CLI version, which command produced this (`deploy_run` / `deploy_destroy`)                                                                                                                                                                              |
| `commit_sha`, `commit_message`, `commit_author`  | Git HEAD context at deploy time — note this is who *wrote the code*, not who *ran the deploy* (a real defect, see below)                                                                                                                                            |
| `deployment`, `workspace`, `environment`, `file` | What was deployed and from where                                                                                                                                                                                                                                    |
| `force`, `dry_run`                               | Which flags were used                                                                                                                                                                                                                                               |
| `success`, `duration_seconds`                    | Overall outcome                                                                                                                                                                                                                                                     |
| `stages[]`                                       | Per-stage: `name`, `provisioner`, `topology`, `success`, `status` (`success`/`failed`/`skipped`, distinguishable per v1 ADR-0083), `skip_reason`, timestamps, `duration_seconds`, `steps[]` (each: `step`, `success`, `duration_seconds`), `errors[]`, `messages[]` |
| `pull_request`                                   | Layer 1 enrichment, optional                                                                                                                                                                                                                                        |
| `change_reference`                               | External ticket record justifying the deployment (v1 ADR-0074), optional                                                                                                                                                                                            |
| `errors[]`, `messages[]`, `metadata`             | Top-level catch-alls                                                                                                                                                                                                                                                |

**On-disk layout** — one `_execution.json` (always written) plus one
`{stage}.json` per stage (when `file_per_stage: true`), under a directory
resolved from a **named structure** or an inline Jinja2 template. Eight
built-ins: `flat`, `by-stage`, `by-execution` (default), `by-date`,
`by-environment`, `by-workspace`, `by-tenant`, `full` — e.g. `by-tenant`
resolves to `{{ tenant }}/{{ deployment }}/{{ timestamp }}`. Configurable
base path (default `.strata/deploy-log`).

**Durability (optional, `AuditConfigModel.repository`):** the deploy-log can
be copied into a *different* registered repo and git-pushed there —
`AuditController.push_to_remote()` copies files into the target repo's
working tree first (so the local write location and the push destination
never have to coincide), commits, and pushes, resolving the target branch
explicitly to handle a detached-HEAD checkout (the CI norm). This is the
"durable, shared shape" reused by every artifact kind that wants git-push
durability, not audit-specific (cost history, drift history, deployment
manifest all reuse `RepositoryPushModel`).

### The deploy-log's undocumented sibling — `DeploymentManifestModel`

The deploy-log above is not the only per-deploy artifact v1 writes —
`models/deployment_manifest_model.py` defines a second, separately-written
document (`kind: deployment-manifest`) that overlaps substantially with the
deploy-log and was not read for this doc's first pass. Catalogued now
because it is squarely what "the deployment manifest" in a v2 redesign must
replace, per the user's 2026-09-30 direction (see "v2 Design Notes" below).

Written by **both** `strata build run` (as `manifest.json` next to the
build artifacts) and `strata deploy run`/`destroy` (on completion, success
or failure) — `spec` carries:

| Group               | Fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Identity            | `deployment_name`, `workspace_name`, `environment`                                                                                                                                                                                                                                                                                                                                                                                                                           |
| Action/timing       | `action` (`build`/`deploy`/`destroy`), `started_at`, `completed_at`, `duration_seconds`, `status` (`success`/`partial`/`failed`), `dry_run`                                                                                                                                                                                                                                                                                                                                  |
| Actor               | `deployed_by`                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| **Artifacts (BOM)** | `artifacts.platform` (hash + path + **full embedded `platform.json` content**), `artifacts.repositories` (url/ref/resolved commit SHA, per repo name), `artifacts.images` (name/image ref/digest), `artifacts.providers` (name/type/state backend/type-specific details)                                                                                                                                                                                                     |
| Stages              | `stages[]` — name, provisioner, topology, status, timestamps, duration, `steps[]` (names only — coarser than the deploy-log's `DeployLogStepModel`, which also has per-step success/duration), `outputs` (non-sensitive), `outputs_artifact` (pointer to a separate `DeploymentOutputsModel` file), `error`, `skip_reason`, `warnings`                                                                                                                                       |
| Extension points    | `sbom` (`SbomReferenceModel`: path + format + sha256 + component_count — **referenced by path/hash, content NOT embedded**, unlike `artifacts.platform.content`), `signatures` (GPG signing/attestation, freeform dict), `policy_results[]`, `lock` (`ManifestLockReferenceModel` — state-lock audit trail: lock_id/backend/acquired_at/released_at/holder/hostname), `audit_log` (relative path pointer **back to** the deploy-log's `_execution.json`), `change_reference` |

**The overlap with deploy-log is real, not superficial.** Both carry
`deployment`/`workspace`/`environment`, both carry a `stages[]` list with
largely the same per-stage shape (name/provisioner/topology/status/timing/
steps/errors), and both carry `change_reference`. The manifest's
`audit_log` field is v1's own acknowledgement of this — it exists purely to
let a reader hop from one record to the other: two independently written
files describing the same execution, cross-linked after the fact rather
than designed as one artifact from the start.

**Two different content strategies coexist without a stated reason:**
`artifacts.platform.content` embeds the *entire* deployed `platform.json`
inline in the manifest, while `sbom` and `outputs_artifact` are bare
path+hash *references* to sibling files. Nothing in v1's source or ADRs
explains why the platform artifact gets the heavyweight treatment and
SBOM/outputs don't — worth deciding deliberately for v2 rather than
copying either choice by default.

`change_reference`/`ChangeReferenceModel` is the one sub-model v1 is
explicit about reusing verbatim across all three surfaces ("the same model
instance is reused, unmodified, by the deployment manifest, the deploy
log, and the forwarded audit event") — the closest thing v1 has to a
stated single-source-of-truth policy for a shared field, and worth
treating as precedent for how v2 should share ("author once, reference
from every surface" rather than "each surface gets its own copy of the
fields it wants").

### Layer 3 — CLI reporting

Four commands, all reading/writing the deploy-log (not the journal or a
sink):

- **`audit changes [--last N] [--since TS] [--stage NAME] [--ai]`** — lists
  recent deploy-log entries; recursive file discovery (`rglob
  "_execution.json"`), parsed, filtered, sorted by timestamp descending.
  `--ai` summarizes trends/anomalies/recurring failures.
- **`audit diff FROM_ID TO_ID`** — resolves both executions' `commit_sha`,
  runs `git diff <before> <after> -- <yaml_file>`; colourised in console
  output, raw diff string + `has_changes` in JSON. Exit code 3 when changes
  are detected (a scriptable drift signal).
- **`audit resend [--last N] [--since TS]`** — re-forwards deploy-log
  entries to configured sinks (recovery after a delivery outage).
- **`audit status`** — prints the fully resolved effective picture: journal
  path + where that setting came from, which event types the policy gate
  admits, which sinks are live, last delivery outcome per sink, and (per
  ADR-0066) which configuration documents contributed the effective
  `spec.audit` block. The "is my audit trail actually working?" command.

### Layer 3b — Journal (`logger/audit.py` → `.strata/audit.log`)

Separate from the deploy-log: an NDJSON log of **CLI invocations**, not
deployment outcomes — "who ran what, when". Configured from
`spec.audit.journal` (path, rotation strategy `size`/`daily`, `max_bytes`,
`backup_count`, `date_suffix`), with a two-phase bootstrap so the journal
still works before configuration loads:

1. **Phase 0 — bootstrap.** Opens with hardcoded defaults
   (`.strata/audit.log`, 5 MB × 3 backups) before any config is read — so a
   broken configuration file doesn't also lose the one audit entry that
   would explain why.
2. **Phase 1 — reconfigure.** Once configuration loads, reopens if
   `spec.audit.journal` differs.

Precedence: `spec.audit.journal` (shared, committed) is overridden by
`logging.yaml`'s `audit:` block (machine-local escape hatch, e.g. an
absolute path on a production host), which falls back to the built-in
defaults.

Emits `command.executed` **only for mutating commands** — read-only
commands (`*_list`, `*_show`, `*_status`, `schema_*`, `tools_status`,
`env_info`) produce no entry at all. This restriction is itself a fix for
a measured defect (see below) — applying it removed ~95% of a real
workspace's journal volume without losing a meaningful entry.

### Layer 4 — Policy gate + sinks (SIEM/webhook forwarding)

A single routing entrypoint, `AuditController.forward(event_type, payload)`,
called by every producer. Every admitted event is written to the journal
first, then fanned out to sinks. A sink receives an event when **all**
of: (1) the global policy gate admits the event type, (2) the sink is
`enabled`, (3) the sink's own `events` filter is `None` (everything the
gate admits) or names the type exactly.

**Event taxonomy — three classes, not a flat list:**

| Class      | Answers                       | Event types                                                                                                                                                                | Volume           | Default policy                                  |
| ---------- | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | ----------------------------------------------- |
| Invocation | *who* ran what                | `command.executed`                                                                                                                                                         | High             | Off (measured: 95% of volume was polling/tests) |
| Outcome    | *what did that run do*        | `deployment.completed`, `deployment.destroyed`, `deployment.measured`, `build.completed`, `validation.completed`, `workitem.*`                                             | One per run      | Mostly on                                       |
| Domain     | *what happened to the system* | `policy.violated`, `secret.accessed`, `lock.acquired`/`lock.released`, `drift.detected`, `cost.threshold_exceeded`, `cost.recorded`, `drift.recorded`, `manifest.recorded` | Rare, high value | Mostly on (locks off)                           |

Event types are a **closed enum** — an unrecognized key in
`policy.events` is a validation error at exit code 3 (with an explicit
rename table for pre-rewrite names, e.g. `deploy_audit` →
`deployment.completed`), not a silently-ignored typo. An event type
consulted by `forward()` at runtime that isn't in the closed set is never
gated off — the closed set only protects configured keys from typos, not
producers the model doesn't know about yet. A bare `bool` is shorthand for
`{enabled: <bool>}`; reserved-but-unread fields (`severity`, `sample`,
`retention_days`) are commented out in the model so `extra="forbid"`
rejects them until a real producer reads them, rather than shipping
aspirational config.

**Envelope — CloudEvents 1.0 + ECS.** Rather than invent a wire format, v1
reuses two existing standards: CloudEvents for the outer envelope
(`specversion`, `type`, `source`, `id`, `time`, `subject`, `data`) and
Elastic Common Schema for the fields inside `data` (`event.kind`/
`event.category`/`event.action`/`event.outcome`, `user.name` for the actor,
`labels` for correlation dimensions). `type` is reverse-DNS, dotted,
past-tense (`xyz.huybrechts.strata.deployment.completed`) — audit records
describe something that *has happened*. `event.kind: alert` for
`policy.violated`/`drift.detected`/`deployment.destroyed`/
`cost.threshold_exceeded` lets a SIEM route those differently by schema
alone, without a hand-written correlation rule.

**Every event carries an actor and a correlation key.** `actor` was added
(a real gap — `commit_author` is who wrote the code, not who ran the
deploy); `execution_id` is the contractual join key across the journal,
deploy-log, and every sink payload — letting "who did this" (invocation
record) and "what did it do" (outcome record) be joined after the fact.

**Sinks are pure routing references, never transport config.**
`AuditSinkModel` is just `{name, integration, enabled, events}` — no `url`/
`headers`/`type`/`format` on the sink itself. All transport (endpoint,
auth, headers, format) lives on a `spec.integrations[]` entry the sink
references by name (the same integration-layer pattern v2 already has from
its own ADR-0021-equivalent work) — meaning a sink *cannot* hold a
credential, by construction, rather than by convention. Two new
integration types (`webhook`, `syslog`) join the existing `splunk`/`elk`/
`otel`/`sentinel` SIEM integrations this way. Credential-bearing fields
require `${secret:KEY}`/`${var:KEY}` — a literal value in an
`authentication.*` field is a validation error, not a warning.

### Configuration lives with governance, not with the audited party

`spec.audit` exists **only** on the configuration document, deliberately
**not** on environment (v1 initially merged it there; ADR-0066 removes
that). Rationale: if audit configuration were overridable per-environment,
whoever edits the environment about to be deployed to could disable the
audit event for their own deployment, or reroute where it goes — an audit
control the audited party can switch off is not a control. Per-environment
differences (e.g. "ship to SIEM in prod, journal-only in dev") are
expressed by which **configuration document a profile activates**, not by
an environment-level override — keeping the differentiation inside the
same trust boundary. v1 is explicit this is defense-in-depth, not
tamper-proofing: anyone who can run the CLI can still edit the
configuration document too; genuine tamper-resistance needs the record to
leave the machine into an append-only external store (v1's separate,
not-yet-built state-service ADR).

### Lessons from v1's own defects (don't reproduce these)

v1 ADR-0066 catalogs eleven specific defects found by actually measuring a
production `.strata/audit.log`, not by design review. The ones most likely
to recur in a fresh v2 design if not deliberately guarded against:

1. **Declared-but-unread configuration is worse than no configuration** —
   `policy.events` existed and was read by nothing; an operator toggling it
   got silent no-ops in both directions.
2. **A hardcoded event-type string in the forwarding call** meant only one
   of eight declared event types could ever reach a sink at all.
3. **The invocation producer must apply its own stated filter criterion** —
   docstring said "side-effecting actions only"; code emitted for every
   command including polling reads. Measured cost: ~95% of volume.
4. **Tests must not write to the real audit log** — an in-process test
   suite with no audit-aware fixture silently populated a third of a real
   log with pytest's own argv.
5. **An outcome record needs an explicit actor field** — `commit_author`
   (who wrote the code) is not who ran the deploy; conflating them makes
   "who deployed to prod" unanswerable from the one record designed to
   answer it, and leaves a SOC-facing field (CEF `src=`) empty.
6. **A correlation key that already exists in two places must actually be
   documented/tested as the join key**, or two records that should compose
   never do.
7. **A recovery/replay command must go through the same sink-resolution
   path as the original send** — a bespoke construction path silently
   dropped a whole class of sinks in the one command whose entire purpose
   is recovering from a delivery outage.
8. **Sink resolution/filtering implemented more than once drifts** — three
   independent call sites had already diverged in what a filter matched.
9. **No field on a config model should be able to hold a literal
   credential** — make it structurally impossible (route through the
   integration/secret layer), not policy ("please use a secret reference").
10. **Default transport hardening matters for an audit channel
    specifically** — cleartext-by-default, unauthenticated, silently
    truncating transports undermine the "tamper-evident" value proposition
    they exist for.
11. **One configuration location, discoverable by one status command** —
    config split across multiple files (some commented out in every real
    workspace) is indistinguishable from "not configured" in practice.

## v2 Design Notes

Working notes toward a v2 ADR — analysis settled enough to record, but no
model or code exists yet in this repo.

### Durable push destination — why `SolutionRemoteModel` can't be reused as-is

v1's `push_to_remote()`/`RepositoryPushModel` (see "Durability" above) is
the mechanism a v2 deploy-log/manifest push would need to replicate. v2
already has git plumbing for a related but different job — fetching a
pinned artifact source
([`SolutionRemoteModel`](../../src/strata/models/solution_model.py) +
[`remote_resolution.py`](../../src/strata/controllers/remote_resolution.py))
— raising the question of whether the push destination can just reuse that
model. It can't, directly, without recreating a conflation v2 already
deliberately split apart:

- `SolutionRemoteModel.reference` is **required and validated as an
  immutable pin** for `type: git` (`validate_reference_for_type`) — "the
  artifact from this remote is always taken at this exact ref." A push
  destination needs the opposite: a **mutable branch tip**, refreshed
  immediately before every write so the push doesn't fail non-fast-forward
  against commits another deploy already pushed.
- `resolve_remote()` trusts an existing checkout **forever** once cloned —
  "this function's job is locating a checkout, not verifying one" — correct
  for a pinned read source, wrong for a repeatedly-pushed-to destination.
- No `add`/`commit`/`push` exists in v2 today — `remote_resolution.py` only
  clones + checks out once.

**What genuinely does reuse:** the git-via-subprocess transport pattern
(`run_command(["git", ...])`, timeout + `is_successful`/stderr handling)
already established there, and the `integration` credential-indirection
field (name of an `Integration`, never a literal credential) — both are
the right shape to extend rather than reinvent.

**Proposed shape** — a small, separate model (mirrors v1's
`RepositoryPushModel`, adapted), not a reuse of `SolutionRemoteModel`
itself:

```python
class RepositoryPushModel(PlatformBaseModel):
    remote: Annotated[PlatformName, RemoteReference()]  # resolves against strata.yaml's remotes
    branch: str                                          # mutable target — distinct from remote.reference
    path: str                                             # where inside that repo this artifact lands
```

Referencing an existing remote by name (via the same `RemoteReference`
annotation `reference_fields.py` already provides, so validation is free)
reuses the remote's `url`/`integration` for credentials without
redeclaring them, while keeping the mutable-branch concept — which does
not belong on every remote, only a push destination — out of the read-path
model entirely.

Transport-side, a **new**, push-specific resolution function is needed
alongside (not reusing) `resolve_remote()`: its own checkout directory
keyed by `(remote, branch)` rather than `(remote, reference)`, always
fetch + reset before writing, then copy-files-in → `git add` → `git commit`
→ `git push`. v1's `HEAD:<branch>` refspec trick (survives a detached-HEAD
checkout — the CI norm) is worth keeping; it is a real, tested fix, not
incidental complexity.

### The audit files — Deployment Manifest (+ Metrics); the Event is rendered, not written

**Revisited 2026-09-30 — `_event.json` as a standalone persisted file is
dropped.** Raised by the user ("do we still want the audit.json? seems
pointless") after the rest of this design was in place: once
`_manifest.json` exists, every field the CloudEvent envelope carries
(`data.event.outcome`/`duration`, `data.user.name`, `data.labels.*`) is
already sitting in the manifest, one directory-listing away. In the
zero-config default (no sinks configured — the common case per
"Streamlining the configuration surface" below), a persisted `_event.json`
would be written on every single run and read by *nobody* — pure I/O and
clutter for a file whose only real reason to exist is being a wire
payload for something that isn't there. The instinct was right.

**Resolution: the event becomes a pure rendering, computed on demand,
never written to disk.** `render_event(manifest) -> dict` (or
`render_event(metrics)` for `deployment.measured`) produces the same
CloudEvents 1.0 + ECS shape shown below, but only at the moment something
is actually being dispatched to an **integration** sink — initial send or
a later `audit resend`, both calling the identical function. This is
arguably *more* correct than persisting one fixed copy, not just simpler:
CloudEvents' own `id` is supposed to be unique per delivery attempt, so a
resend reusing a stale persisted `id` would have been slightly wrong
anyway. v1 defect #7 ("a recovery/replay command must go through the same
sink-resolution path as the original send") is satisfied by *code-path*
identity (the same rendering + dispatch function, both times, reading the
same immutable manifest), not by *byte* identity — which turns out to be
the more defensible guarantee of the two.

With the event no longer a file, **the manifest is the only substantive
persisted content**, with `_metrics.json` (below) as its lean, redacted,
more-shippable sibling. `execution_id` still links every rendering back to
exactly one manifest.

**File 1 — `_manifest.json` (the deployment manifest).** Everything
deploy-log and deployment-manifest each partially had, merged into one
record — keeping the *richer* side wherever the two disagreed on
granularity:

| Group                | Fields                                                                                                                                                                                                                                                  | Kept from                                                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Identity             | `execution_id`, `command`/`action` (`build`\|`deploy`\|`destroy`), `version` (CLI), `deployment`, `workspace`, `environment`, `file`                                                                                                                    | both (identical)                                                                                                                 |
| Timing               | `timestamp`/`started_at`, `completed_at`, `duration_seconds`                                                                                                                                                                                            | both (identical)                                                                                                                 |
| Outcome              | `status` (`success`\|`partial`\|`failed`)                                                                                                                                                                                                               | manifest's tri-state, not deploy-log's bare `success: bool` — `partial` is real signal a bool can't carry                        |
| Flags                | `force`, `dry_run`                                                                                                                                                                                                                                      | deploy-log                                                                                                                       |
| Actor + git context  | `deployed_by` (actor), `commit_sha`, `commit_message`, `commit_author`                                                                                                                                                                                  | both, actor field kept distinct from commit_author per lesson #5 ("commit_author is who wrote the code, not who ran the deploy") |
| Layer 1 evidence     | `pull_request` (PR enrichment), `change_reference` (ticket)                                                                                                                                                                                             | both — single shared `ChangeReferenceModel` instance, v1's own stated precedent                                                  |
| **Artifacts (BOM)**  | `artifacts.platform` (**hash + path only — reference, not embedded content**), `artifacts.repositories`, `artifacts.images`, `artifacts.providers`                                                                                                      | manifest, minus the content embed (see decision below)                                                                           |
| Stages               | `stages[]`: name, provisioner, topology, status (`success`\|`failed`\|`skipped`), skip_reason, started_at, completed_at, duration_seconds, `steps[]` **{step, success, duration_seconds}**, outputs, outputs_artifact, errors[], warnings[], messages[] | deploy-log's per-step success/duration — manifest's `steps[]` (names only) is strictly coarser and is dropped                    |
| Satellite references | `sbom` (path/format/sha256/component_count), `outputs` (path/sha256 — was `outputs_artifact`, promoted to top-level since it's per-execution not per-stage-only), `signatures`, `policy_results[]`, `lock`                                              | manifest, unchanged — this reference-only pattern is the one v1 got right, now applied consistently (see next decision)          |
| Catch-alls           | `errors[]`, `messages[]`, `metadata`                                                                                                                                                                                                                    | deploy-log                                                                                                                       |

**Decision — `artifacts.platform` becomes a reference, not an embed.**
Resolves the "two content strategies coexist without a stated reason"
question raised above: v1 never justified why the platform artifact got a
full inline copy while SBOM/outputs were left as path+hash pointers. A
reference is chosen for the manifest too — `{hash, path}`, no `content` —
keeping every satellite artifact the same shape (a hash the manifest
vouches for, a path a reader follows), and keeping the manifest file small
regardless of platform size. The full `platform.json` remains on disk as
its own build artifact; the manifest's hash is what makes it tamper-evident,
not a copy of its bytes.

**Rendering — `render_event(manifest)`, computed at dispatch time, never
written to disk.** Stays genuinely thin — no producer payload dumped in
wholesale (v1's actual defect: `data.strata` was the *entire* deploy-log
dict, making the envelope only nominally thin):

```json
{
  "specversion": "1.0",
  "type": "xyz.huybrechts.strata.deployment.completed",
  "source": "/strata/{workspace}/{deployment}",
  "id": "<fresh UUID4, per CloudEvents id+source uniqueness rule>",
  "time": "<ISO-8601>",
  "subject": "{deployment}",
  "datacontenttype": "application/json",
  "data": {
    "event": {"kind": "event", "category": ["configuration"], "action": "deployment-completed", "outcome": "success", "duration": 123000000000},
    "user": {"name": "<resolved actor>"},
    "labels": {"execution_id": "<uuid4>", "workspace": "...", "environment": "...", "deployment": "..."},
    "manifest": {"path": "<relative path to _manifest.json>", "sha256": "<hash of _manifest.json>"}
  }
}
```

**Decision — the rendered event references the manifest by path + hash,
never embeds it.** Resolves the other open question from above.
`data.manifest` replaces v1's `data.strata` (the full payload dump) with a
pointer — `execution_id` in `labels` still gives a SIEM/log system a
correlation key without opening the file; a reader who needs the
substance follows the path and can verify it against the hash (the
manifest is durable on disk and/or git-pushed, so the reference resolves
for as long as that copy is retained). This is the same reference
discipline just adopted for `artifacts.platform` inside the manifest,
applied consistently one level up.

**Not yet designed:** whether non-outcome domain events (`policy.violated`,
`secret.accessed`, etc. — raised inline by a validator/controller, not by
a deploy execution) have a manifest to render *from* at all, or need
their own minimal source record; and how `sbom`/`outputs`/future artifact
kinds (the user's "sbom etc. can be added later") register themselves as
new satellite reference fields without a manifest schema change each
time. The on-disk directory/naming convention itself is designed next,
below.

### Write once locally, then distribute — the end-of-run sequence

Sequencing, per the user's 2026-09-30 walkthrough:

1. **During the run** — `execution_id` and `started_at` are fixed at the
   top of the run; stage results accumulate in memory as each stage
   completes.
2. **At the end of the run** — the manifest (and metrics, below) are
   finalized (`completed_at`, `duration_seconds`, final `status`, PR/
   change-reference enrichment) and written **once**, locally, to the
   resolved local directory (path resolution below).
3. **Then**, one loop over every configured audit destination sends them
   there — an **integration** destination gets a CloudEvent rendered on
   the fly from the manifest (or metrics) content and forwarded; a
   **git** destination gets the manifest/metrics files themselves copied
   into the pushed repo at the same resolved relative path, then
   `add`/`commit`/`push` (see "Durable push destination" above).

Writing once, after everything (including best-effort PR enrichment) is
already known, also removes a real v1 defect: v1's own pipeline writes
`_execution.json`, *then* enriches with PR data, *then* re-writes
`_execution.json` a second time — two writes of the same file, with a
window between them where an on-disk record is known-incomplete. A single
finalize-then-write step has no such window.

**Unifying the destination list.** v1 has two separate mechanisms here —
a dedicated `AuditConfigModel.repository: RepositoryPushModel` field (git,
exactly one target, hardcoded to "deploy-log only") and a general
`sinks: list[AuditSinkModel]` (SIEM/webhook, `forward()`-only). v2 folds
these into **one list**: `git` becomes just another sink `type`, alongside
`siem`/`webhook`/`syslog`. v1 already established the precedent this
needs — ADR-0018 decision #7 requires a sink's type-specific fields to be
validated against its declared `type` (e.g. `type: ndjson` requires `path`,
rejects everything else). A `git` sink adds its own routing fields — see
"The endpoints" below, which also resolves an apparent conflict this
raises against ADR-0066 (a sink is supposed to carry no transport
configuration at all).

### The endpoints — the audit sink model

v1's real, current `AuditSinkModel` (post-ADR-0066, confirmed directly
from `models/audit_config_model.py`) is deliberately narrower than the
earlier "Layer 4" summary above implies: **`{name, integration, enabled,
events}` — nothing else.** There is no `type` field on the sink at all.
`stdout`/`ndjson` sink types from the original ADR-0018 design were
removed outright (the local record is now the journal); `syslog`/`webhook`
were not kept as sink types either — they were *promoted to Integration
types* (`syslog_siem_integration.py`/`webhook_siem_integration.py`) behind
the exact same bare `{integration: name}` reference every other SIEM
sink uses. A sink that still carries `type`/`path`/`address`/`url`/
`headers`/`format` directly is rejected with a validation error naming the
exact replacement shape — this is enforced in code
(`AuditSinkModel.reject_legacy_shape`), not just documented convention.

**This means "siem"/"webhook"/"syslog" were never really three sink
*types* — they are three *Integration* types that all plug into one
identical sink shape.** From the audit config's perspective there is only
one shape here. Worth stating precisely because it changes what adding
`git` as "just another sink type" actually requires: it is not a fourth
member of the same enum sitting next to `siem`/`webhook`/`syslog` — it is
a **second, structurally different sink shape**, because a git
destination has no `Integration` in the SIEM sense to reference (its
credentials live one level removed, on the *Remote* it points at — see
"Durable push destination" above).

**Two arms, exactly one set per sink — not a `type: git` sink with inline
transport fields (an earlier draft of this doc got this slightly wrong).**
`remote`/`branch`/`path` are **routing** information (which already-
authenticated destination, and where within it), not **transport**
configuration (an endpoint URL, a header, a credential) — the same
distinction `events`/`enabled` already make for the integration arm. The
git arm's *actual* transport (the repo URL, the credential) still lives
entirely behind `remote.url`/`remote.integration`, never inline on the
sink — so ADR-0066's real invariant ("no field on a sink can hold a
credential or an endpoint directly") holds for both arms identically; only
the *routing* shape differs.

```python
class AuditGitSinkTargetModel(PlatformBaseModel):
    """Git push destination — routing only. Transport (url, auth) lives on `remote`."""

    remote: Annotated[PlatformName, RemoteReference()]  # resolves against strata.yaml's remotes
    branch: str                                          # mutable target — distinct from remote.reference
    path: str                                             # root prefix under the repo; the shared
                                                           # structure/yyyymmdd-hhmmss relative_path is
                                                           # appended under this (see "Path resolution")


class AuditSinkModel(PlatformBaseModel):
    name: PlatformName
    enabled: bool = True
    events: list[str] | None = None          # filter — None means every policy-admitted event type

    integration: PlatformName | None = None  # arm 1 — siem/webhook/syslog, unchanged from v1
    git: AuditGitSinkTargetModel | None = None  # arm 2 — durable git push

    @model_validator(mode="after")
    def validate_exactly_one_target(self) -> "AuditSinkModel":
        if (self.integration is None) == (self.git is None):
            raise ValueError(f"sink '{self.name}': exactly one of 'integration' or 'git' must be set.")
        return self
```

```yaml
spec:
  audit:
    sinks:
      - name: prod-siem
        integration: splunk-prod          # arm 1
        events: [deployment.completed, policy.violated]
      - name: config-repo
        git:                                # arm 2
          remote: xyz-configuration
          branch: main
          path: audit
        events: [deployment.completed, deployment.destroyed]
```

**Admission mechanics are unchanged, and now apply identically to both
arms** — the same three-condition rule v1 already has (global policy gate
admits the event type → sink `enabled` → sink's own `events` filter is
`None` or names the type exactly) needs no modification to cover `git`;
it was already sink-shape-agnostic.

**Dispatch differs by arm — this is a replication vs. a forward, not the
same operation over two transports.** An **integration** sink triggers
rendering a fresh CloudEvent envelope from the manifest (or, for
`deployment.measured`, from the metrics record) and forwards that over
the network — nothing envelope-shaped is ever read from disk, since none
is written (see "The audit files" above). A **git** sink instead copies
whichever per-execution files exist (`_manifest.json`, `_metrics.json`)
into the resolved path and pushes — a durable byte-for-byte replication
of what was already written locally, not a notification. Treating both as
"a sink" is about sharing *admission* (policy/enabled/events) and
*sequencing* (one loop, end of run), not about pretending they do the
same thing on the wire.

**Resolved by dropping the persisted event file (see "The audit files"
above):** `deployment.measured` is a distinct entry in v1's closed
event-type enum (`AUDIT_EVENT_DEFAULTS`, defaulting enabled) — the
metrics record's own event type, separate from
`deployment.completed`/`destroyed`. Since every integration-sink dispatch
now renders its envelope fresh from source (manifest or metrics) rather
than reading a persisted file, there's no asymmetry left to resolve
between the two event types — both go through the identical
render-then-forward path, just from a different source record.

**Capability gating, reusing v2's existing mechanism rather than
inventing one.** v2's `Capability` enum (`models/integration_model.py`) is
a deliberately closed, minimal vocabulary, extended only when a concrete
feature needs a member (ADR-0021 D9) — no audit-forwarding capability
exists yet. Adding one (e.g. `Capability.AUDIT`) and requiring
`sink.integration` to resolve to an `Integration` declaring it is the same
mechanism every other capability-gated reference in v2 already uses, not
new plumbing — this resolves the "Reuse v2's own integration layer for
sinks" open question in Remaining Work below.

### Streamlining the configuration surface — `spec.audit` is fully optional

Raised by the user, reviewing `path`/`structure`/`metrics_path`/`journal`
together rather than one at a time: **the entire `spec.audit` block should
be omittable**, with `.strata/audit` as the default and every other
location derived from it — not four independent things a config author
has to learn before getting a working local audit trail.

**One base path, not three.** An earlier draft of the example below had
`path`, `journal.path`, and `metrics_path` as three independently
configurable locations. Streamlined to one — `spec.audit.path` (default
`.strata/audit`) — with everything else a fixed, derived sub-path under
it unless explicitly overridden:

| Artifact                                                             | Path (derived from `spec.audit.path`)   | Separately configurable?                                                                                                                                                                                |
| -------------------------------------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Journal (CLI invocations)                                            | `{path}/journal.ndjson`                 | Yes — `journal.path` remains an override, matching v1's existing "machine-local escape hatch" precedent (a developer wanting logs elsewhere without touching committed config)                          |
| Metrics append series                                                | `{path}/deployments.ndjson`             | **No longer** — dropped as its own top-level key (`metrics_path`); its whole value was being one well-known, always-the-same-relative-location path, which "derived, not configured" already guarantees |
| Per-execution files (`_manifest.json`/`_event.json`/`_metrics.json`) | `{path}/{structure}/{yyyymmdd-hhmmss}/` | Yes — `structure`, see next                                                                                                                                                                             |

**`structure` simplified from an 8-name enum to one optional Jinja2
string.** v1's `flat`/`by-stage`/`by-execution`/`by-date`/
`by-environment`/`by-workspace`/`by-tenant`/`full` were a lookup table of
named templates a config author had to pick from — real complexity partly
inherited from the fact that several of those names existed *only* to
smuggle a timestamp into the path (`by-execution`, `by-date`), which the
now-mandatory `yyyymmdd-hhmmss` leaf (see "Path resolution" below) makes
redundant. Streamlined to: `structure` accepts a plain Jinja2 template
string directly (default `{{ workspace }}/{{ deployment }}` — no name
lookup, no built-in table). Override it for `{{ tenant }}/{{ deployment
}}` or anything else, or omit it and get the default. One field, one
mechanism, no menu.

**`journal` stays optional with the same defaults v1 already had** (path
derived as above, `rotation: size`, `max_bytes: 5_000_000`,
`backup_count: 3`) — nothing to streamline here beyond making the whole
block omittable, which it already effectively was (every sub-field was
already `Optional` in v1's own model).

**`policy` and `sinks` were already zero-config.** Class-aware defaults
(`AUDIT_EVENT_DEFAULTS`) apply when `policy` is omitted; `sinks` defaults
to an empty list (fully local, nothing forwarded) when omitted. No change
needed — stating it here so "is `spec.audit` really all-optional" has one
answer covering all five fields, not four separate ones scattered across
this doc.

**Net result:** `spec.audit` can be omitted from `configuration.yaml`
entirely. A deploy still writes `.strata/audit/{{ workspace }}/{{
deployment }}/<yyyymmdd-hhmmss>/{_manifest,_event,_metrics}.json`, appends
to `.strata/audit/deployments.ndjson` and `.strata/audit/journal.ndjson`,
applies the built-in policy defaults, and forwards to nothing — a complete
local audit trail with zero YAML.

### Example configuration

This example shows the **override** case (SIEM forwarding + durable git
push) — the zero-config default from the previous section needs none of
this. Three real documents, matching this repo's own `config/`
conventions (`solution_model.py`'s `remotes`, an `integration` document
per `config/integrations/azure-keyvault.yaml`'s shape):

```yaml
# strata.yaml — the git sink's remote is a normal, already-declared remote.
# `reference: main` is required by SolutionRemoteModel for type: git (it
# pins *reads* via resolve_remote()) but is not consulted by the push path
# at all — the sink's own `branch` is what the push writes to. Reusing one
# remote for both purposes means declaring a reference that push ignores;
# not fully reconciled yet (see "Durable push destination" above).
spec:
  remotes:
    - name: xyz-configuration
      type: git
      url: git@github.com:xyz/xyz-configuration.git
      reference: main
      integration: github-deploy-key   # credentials — never inline on the remote
```

```yaml
# config/integrations/splunk.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: splunk-prod
spec:
  type: splunk
  capabilities: [audit]                # gates use as spec.audit.sinks[].integration
  required: false
  enabled: true
  endpoints:
    address: "${var:SPLUNK_HEC_URL}"
  authentication:
    method: token
    token: "${secret:SPLUNK_HEC_TOKEN}"  # never a literal value — validation error if not ${...}
```

```yaml
# config/configuration.yaml
apiVersion: strata.huybrechts.xyz/v2
kind: configuration
meta:
  name: prod-config
spec:
  audit:
    path: .strata/audit           # optional — this is already the default
    structure: "{{ tenant }}/{{ deployment }}"   # optional override of the default "{{ workspace }}/{{ deployment }}"
                                    # yyyymmdd-hhmmss leaf always appended, never part of this string
                                    # metrics/journal paths are derived from `path`, not separately configurable

    policy:
      events:
        secret.accessed: true
        policy.violated: true
        command.executed: false   # matches the class-aware default; explicit here for clarity

    journal:
      max_bytes: 5242880           # only overriding what differs from the default; path/rotation/backup_count omitted

    sinks:
      - name: prod-siem
        integration: splunk-prod   # arm 1 — integration reference, no transport fields here
        events: [deployment.completed, deployment.destroyed, deployment.measured, policy.violated]

      - name: config-repo
        git:                        # arm 2 — durable push, no Integration reference at all
          remote: xyz-configuration
          branch: main
          path: audit                # root prefix under the repo — relative_path is appended under this
        events: [deployment.completed, deployment.destroyed]
```

Reading the two sinks: `prod-siem` receives a CloudEvent rendered on the
fly from `_manifest.json` (and, since it also admits
`deployment.measured`, a second rendering from `_metrics.json`) sent to
Splunk over HEC, using credentials that live entirely on the
`splunk-prod` integration document — nothing sink-specific to leak, and
no envelope file ever touches disk. `config-repo` instead pushes copies
of whichever per-execution files exist (`_manifest.json`,
`_metrics.json`) under `audit/<tenant>/<deployment>/<yyyymmdd-hhmmss>/`
in the `xyz-configuration` repo's `main` branch, authenticated via
whatever `github-deploy-key` (the remote's own `integration`) provides —
two structurally different operations, admitted by the identical
policy/enabled/events rule, dispatched from the same end-of-run loop.

### Path resolution — shared by the local write and every push destination

One resolution function, reused everywhere a destination needs to know
"where do these two files go":

```
relative_path = render(structure, {deployment, workspace, environment, tenant, ...})
                 / "{yyyymmdd-hhmmss}"        # concrete leaf, always appended

# structure defaults to "{{ workspace }}/{{ deployment }}" — a plain Jinja2 string,
# not a named-template lookup (see "Streamlining the configuration surface" above)
```

- **Local write:** `local_base_path / relative_path / _manifest.json` (and `_metrics.json`) — `local_base_path` defaults to `.strata/audit` (`spec.audit.path`)
- **`git` sink:** `checked_out_repo_root / sink.git.path / relative_path / _manifest.json` (and `_metrics.json`) — the *same* `relative_path`, rooted under the sink's own `path` instead of the local base
- **`integration` sink:** no filesystem path at all — the CloudEvent is rendered fresh from the manifest/metrics content at dispatch time (see "The audit files" above); `relative_path` still travels as a field on the rendered payload (alongside `execution_id`), so a SIEM record and its on-disk/git counterpart can be correlated without a lookup

**The `yyyymmdd-hhmmss` leaf is unconditional — appended regardless of
what the structure template itself contains.** This is a deliberate fix,
not just a formatting choice. v1's own built-in structures include `flat:
"{{ deployment }}"` and `by-stage: "{{ deployment }}/{{ stage }}"` —
*neither has a timestamp component at all* — so under those two
structures, every subsequent run for the same deployment/stage **silently
overwrote** the previous `_execution.json`; picking the "wrong" structure
name was enough to lose audit history with no error. Making the timestamp
leaf mandatory and separate from the configurable structure closes that
class of bug structurally: the collision-avoiding segment is no longer
inside the part a config author can omit.

`yyyymmdd-hhmmss` (not ISO-8601 with colons) matches v1's own filesystem
sanitization (`fs_timestamp = payload.timestamp.replace(":", "-")` in
`_resolve_output_dir`) — Windows paths reject `:`, so the on-disk form has
always necessarily differed from the timestamp recorded *inside* the JSON
(which stays full ISO-8601). The timestamp used is the run's
`started_at` — fixed once at the top of the run — not `completed_at`, so
the resolved path is stable even if finalizing/writing needs to be retried.

### A third file — `_metrics.json`, adapting v1's never-built ADR-0064

Raised by the user (also asking specifically for runtime and
success/failure/error content) — v1 already designed this in detail:
`docs/decisions/0064-deployment-metrics-record.md` (external, not part of
this workspace — status **proposed, nothing in it was ever
implemented**), already listed under "Related Decisions" above. It is
directly the answer to "should we create a metrics.json" — evidence-based
and thorough enough to adapt rather than redesign from scratch.

**The self-containment invariant — the one rule everything else follows:**

> A deployment record may contain only facts observable from inside the
> deploying process.

Concretely: no scanning deployment history, no computing aggregates
(frequency, failure rate, MTTR) at deploy time. A CI runner starts from a
fresh checkout with no local history to scan, so any field needing history
either silently nulls out (every CI deploy looks like the first deploy
ever) or forces a network round-trip onto the deploy hot path. v1's ADR
rejected exactly this ("Option A — compute aggregate metrics during each
deployment") for that reason, plus a sharper one: change failure rate and
MTTR are **structurally uncomputable** at deploy time — MTTR needs a
*future* recovery event that hasn't happened yet. Metrics are emitted as
self-contained per-execution facts; aggregation is a downstream consumer's
job (deferred — same "wait for a concrete need" reasoning already applied
to Layer 3 CLI reporting above).

**Answers the user's "runtime and failure/success/errors" ask directly,**
via a **dimensions / measures / sections** split (not a flat bag of
fields):

- `dimensions` — what you slice by: `outcome` (execution-level
  success/failure — deliberately *not* named `status`, to keep it visibly
  distinct from "change failure rate", which strata cannot determine —
  see below), `action`, `environment`, `tenant`, `actor`, `commit_sha`
- `measures` — what you aggregate: `duration_seconds` (the "runtime" ask),
  `stages_total`/`stages_failed`, resource change counts, gate/policy
  counts
- `sections` — conditional groups (cost, SBOM/CVE, drift) that may simply
  not have run for a given deploy, each with an explicit `measured: bool`
  flag — a plain `null` cannot distinguish "not measured" from "measured
  as zero", and that ambiguity silently corrupts any average built on top

**Errors are redacted to a bounded enum, never raw text** — the "failure"
half of the user's ask is `error_category` (`auth`\|`quota`\|`timeout`\|
`conflict`\|`policy`\|`network`\|`state_lock`\|`unknown`), not `errors[]`.
Raw error text stays exclusively in `_manifest.json` (a failing Terraform
run will happily print connection strings) — this file is designed to be
shipped to third-party platforms (SIEM, Grafana, Datadog) more liberally
than the manifest, so it needs a stricter no-leak bar, not the same one.

**Cardinality discipline is explicit, not assumed.** `execution_id`,
`commit_sha`, `actor` are legitimate record attributes but unbounded —
promoting them to labels in a dimensional system (Prometheus, etc.)
detonates cardinality. A `label_safe` array names the bounded dimension
subset explicitly so a consumer doesn't have to guess.

**Scope — `deploy`/`destroy` only, never `build`, never dry-run.** A build
is a repeated development-loop action with no gates/approvals/lock/
resource changes — most of the record would be absent by construction, and
naive counting would conflate build noise with real delivery signal.
Dry-run writes nothing for the same reason `_manifest.json`'s own fields
would be hollow — nothing was actually changed.

**Distribution — identical to the manifest, nothing new to build.** v1's
own plan was to "hand the same payload to the existing
`AuditController.forward_to_siem()`" — i.e. reuse the transport, not
invent one. In v2's shape that means `_metrics.json` is a peer file to
`_manifest.json` (same directory, same `execution_id`, no nested
reference needed), finalized once at the end of the run and carried
through the same unified destination loop (local write + every
configured sink, git included; an integration sink renders its
`deployment.measured` CloudEvent from this file's content on the fly —
see "The audit files" above).

**Also worth adopting — a durable local append series.** v1's own
observation: per-execution build directories get cleaned, so without an
append-only series a future aggregation consumer has no offline corpus to
read at all. Alongside every per-execution `_metrics.json`, also append
one line to a fixed, never-cleaned path — `{spec.audit.path}/
deployments.ndjson`, **not its own separately configurable key** (see
"Streamlining the configuration surface" above; v1 had this as an
independent `metrics_path` setting, dropped here) — orthogonal to the
per-execution `structure`/`yyyymmdd-hhmmss` path resolution, since its
whole value is being one well-known path a bulk reader can open without
traversing a directory tree. Feeds a deferred future `strata metrics`/
`audit trends`-style command (Phase B in v1's own phasing) — not building
the command now, same reasoning already applied elsewhere in this doc.

**Deliberately excluded, per v1's ADR (still applies):**
`time_since_previous_deploy_seconds` (violates the self-containment
invariant — trivially derived downstream from timestamps across records
instead), output *values* (counts and key names only — outputs already
track `sensitive_keys` for exactly this reason), inline SBOM (a reference
by digest, not the content — same discipline already applied to
`artifacts.platform`/`sbom` in the manifest).

## Related Decisions

None yet in this repo — `docs/decisions/` has no v2 ADR for audit. The
source material is entirely v1's own (external, not part of this
workspace): v1's `docs/decisions/0018-deployment-audit-traceability.md`
(original three-layer model) and `0066-audit-event-routing-policy-model.md`
(the routing/policy/sink rewrite this doc mostly describes), plus
`0022-siem-integration-splunk-hec-cef.md`, `0064-deployment-metrics-record.md`,
`0065-strata-state-service.md`, and `0067-server-identity-authentication-authorization.md`
(v1's own noted dependency for a real `actor`/identity model, out of scope
there too).

Within this repo: [`docs/_gap_v1.md`](../_gap_v1.md) gap #7 tracks
`audit` as still fully open/unstarted, alongside `policies`/`promotions`.
[`docs/design/v2-schema-overview.md`](v2-schema-overview.md) has no audit
entry yet — add one once a v2 ADR exists.

## Remaining Work / Open Questions

This doc is capability catalog only — no v2 decisions are made here. Before
a v2 ADR/implementation:

- **Scope to real usage first, same as v1 should have.** **Answered
  2026-09-30**, per direct request ("lets look at what cfg-int-deployment
  actually needs from v2") — read the real, current
  `e:\sources\cfg-int-deployment\config\audit.yaml` and `.github/` directly:
  - **Layer 1 (PR template + extraction) — REAL, active.**
    `.github/pull_request_template.md` (before/after table, business
    justification, risk level, rollback plan, author/reviewer checklists)
    and `.github/ISSUE_TEMPLATE/deployment-change-request.yml` both exist
    and are populated, matching v1's design exactly.
  - **Layer 2 (deploy-log + manifest) — REAL, actively durable, not just
    local.** `spec.audit.deploy_log_path`/`structure: by-tenant` write
    locally, but `spec.audit.repository: {push: true, name: env-int, path:
    deploys}` git-pushes every deploy-log to a real remote
    (`env-int-deployment`) on every deploy — this is the opposite of "local
    only, unexercised"; it is the one piece of this whole subsystem that is
    both real and durable today. `spec.deployment.manifest` (resolved
    versions/resources/outputs) is pushed the same way, to the same remote,
    under `manifests/` instead of `deploys/`.
  - **Layer 3 (CLI reporting: `audit changes`/`diff`/`resend`/`status`) —
    NOT exercised in CI.** Grepped every real Azure Pipelines YAML
    (`.azure/**`) for any of the four subcommands — zero matches. May still
    be used interactively/manually by an operator (not something a repo
    search can rule out), but nothing automates it.
  - **Layer 4 (policy gate + sinks) — real config, but the one real sink is
    disabled.** `policy.events` has exactly 3 explicit overrides
    (`secret.accessed`/`policy.violated`/`workitem.rejected`); the one
    declared sink (`elk`) is `enabled: false` (no endpoint provisioned for
    this landscape yet) — confirms the earlier repo-memory finding, now
    with the full picture around it.
  - **Journal** — always on, local, gitignored; no per-landscape
    configuration beyond path/rotation.

  **Conclusion: build Layer 2 (deploy-log + manifest, with git-push
  durability) first — it is the one piece that is both real and already
  load-bearing for this specific consumer.** Layer 1 (PR template
  scaffolding) is cheap and real, a reasonable close second — **and is
  now done** (2026-09-30, see [solution-scaffolding.md](solution-scaffolding.md)):
  `strata sln init`/`update` scaffold both files, matching real
  `cfg-int-deployment` usage exactly; only the `gh`-based PR-extraction
  half remains unbuilt (needs Layer 2's deploy-log/manifest to exist
  first). Layers 3 and 4 have no CI evidence of use and should wait for
  a concrete need.
- **Does v2 need all four layers, or fewer? — Decided (2026-09-30).**
  Build Layer 1 (done) and Layer 2 (next) fully. Layer 2 itself only
  needs the **`git` sink arm** (durable push — the one real, active
  need `cfg-int-deployment` already exercises); the **`integration` sink
  arm** (siem/webhook/syslog, CloudEvents+ECS rendering) is modeled
  (cheap — the `AuditSinkModel` shape already supports it) but its
  dispatch is **not implemented in this first pass** — building a full
  CloudEvents renderer for an event type nothing currently forwards
  would be the exact mistake this doc's own "Lessons from v1's own
  defects" #1 describes (declared-but-unread machinery). Layer 3 (CLI
  reporting) and the rest of Layer 4 (policy gate, event taxonomy
  enforcement) stay deferred until a concrete consumer need appears —
  same discipline already applied to Layer 1's scope (ship the real
  PR/issue templates, skip v1's much larger `.github/` tree).
- **Actor/identity is an open dependency — Decided (2026-09-30): ship a
  weak, best-effort actor now, revisit when v2 has a real identity
  model.** v1 itself never solved this beyond `commit_author` (wrong) and
  a bare `user.name` string; v2 has no identity/AuthN model at all.
  Waiting for one would block Layer 2 entirely for a dependency with no
  committed timeline. Resolution order, each a plain env-var/OS read, no
  new plumbing: real CI's own identity var first (`cfg-int-deployment`'s
  actual pipeline is Azure Pipelines — `BUILD_REQUESTEDFOR`, falling back
  to `BUILD_REQUESTEDFOREMAIL`; a future GitHub Actions consumer would
  add `GITHUB_ACTOR` to the same chain), then the OS user
  (`getpass.getuser()`) for a local/manual run, then the literal string
  `"unknown"` — never an exception, since a missing actor must never
  block a deploy. Explicitly weaker than real identity/AuthN (anyone can
  set these env vars to anything), same as v1's own bare `user.name` was
  — acceptable for the same reason v1 shipped it anyway: a plausible
  actor is strictly better than `commit_author` silently standing in for
  one, and a real identity model is a separate, larger, not-yet-started
  effort (v1's own ADR-0067 was out of scope there too).
- **Where does config live? — Decided (2026-09-30): `ConfigurationSpecModel.audit`,
  matching v1 exactly, not Environment or Deployment.** v1's own
  rationale is sound and directly reusable: audit configuration living
  anywhere the audited party's own document can reach would let whoever
  edits the environment/deployment about to run disable or reroute their
  own audit trail — defense-in-depth requires the control to sit outside
  what it's watching. Also consistent with this repo's own precedent —
  `security`/`paths` (gap #7's other two resolved fields) both landed on
  `ConfigurationSpecModel` for the same "platform governance, not the
  thing being governed" reasoning.
- **Reuse v2's own integration layer for sinks — resolved 2026-09-30, see
  "The endpoints" above.** v2 already has `strata.integrations`
  (ADR-0021-equivalent) with a registry and capability ABCs — a new
  `Capability.AUDIT` member, required on whatever `sink.integration`
  resolves to, is the same mechanism every other capability-gated
  reference already uses. The "sink is a reference to an integration,
  never its own transport config" pattern is confirmed as v1's real,
  current design (not just an aspiration) — directly verified against
  `AuditSinkModel.reject_legacy_shape`, which rejects the old inline-
  transport shape in code, not just in a docstring.
- **Deploy-log is no longer unbuilt-on ground in v2 — corrected 2026-09-30.**
  This doc originally said v2 had no `deploy run` command yet; that's stale.
  `strata deploy run` is built and tested (ADR-0027,
  [`deploy-command.md`](deploy-command.md)) — per-stage results, timings,
  errors all already exist at runtime, just never persisted as a
  `DeployLogModel`-shaped record. A deploy-log design can proceed now; it
  does not need to wait on anything else.
- **Decide the event taxonomy fresh, or adopt CloudEvents+ECS wholesale?
  — Decided (2026-09-30): adopt it, but only for the `integration` sink
  arm, whose dispatch is itself deferred (see "Does v2 need all four
  layers" above).** "The audit files"/"The endpoints" sections already
  drafted `render_event()` against CloudEvents 1.0 + ECS; formalizing here
  that this stays the intended shape *whenever* the `integration` arm is
  eventually implemented, so that later work has a settled target rather
  than re-opening the question — but paying the envelope's real
  complexity cost happens then, not in this first Layer 2 pass, matching
  the same "real consumer need first" gate applied everywhere else in
  this doc.
- **User's 2026-09-30 v2 direction — first design drafted, see "The
  audit files" above.** Rather than v1's three overlapping per-deploy
  artifacts, v2 persists exactly two files: `_manifest.json` (merged
  deploy-log + deployment-manifest content, richer per-step timing kept,
  artifact BOM minus the platform content embed) and `_metrics.json`
  (below). **The CloudEvent is not a third file** — revisited 2026-09-30
  ("do we still want the audit.json? seems pointless") and resolved by
  rendering it on demand at dispatch time from the manifest/metrics
  content instead of ever persisting it (see "The audit files" above).
  **On-disk/distribution design also drafted, see "Write once locally,
  then distribute" and "Path resolution" above:** both files are
  finalized and written once at the end of the run, then one loop over a
  unified destination list (git now just another sink arm, alongside
  integration-backed siem/webhook) sends them everywhere configured —
  local and every push destination share one path-resolution function,
  with a mandatory `yyyymmdd-hhmmss` leaf that closes a real v1 silent-
  overwrite bug (`flat`/`by-stage` structures have no timestamp component
  at all). **A third artifact kind, `_metrics.json`, added — see "A third
  file" above:** answers the user's follow-up ask for runtime/success/
  failure/error content, adapting v1's own never-built ADR-0064
  (dimensions/measures/sections split, `error_category` enum instead of
  raw errors, `label_safe` cardinality list, deploy/destroy-only scope,
  plus a durable append series, now derived from `spec.audit.path` rather
  than its own config key — see "Streamlining the configuration
  surface"). Still open: whether non-outcome event types
  (`policy.violated`, etc.) have a manifest to render from at all, and
  how future satellite kinds (sbom already modeled; SBOM scan results,
  cost/drift snapshots, etc.) register as new reference fields.
- **Configuration surface streamlined 2026-09-30 — see "Streamlining the
  configuration surface" above.** `spec.audit` is fully optional
  end-to-end; `path`/`journal.path`/`metrics_path` (three independent
  keys) collapsed to one (`spec.audit.path`, default `.strata/audit`)
  with journal/metrics paths derived from it; `structure` simplified from
  v1's 8-name built-in template lookup to one plain, optional Jinja2
  string (default `{{ workspace }}/{{ deployment }}`).

## Changelog

- 2026-09-30: Created. Catalogs v1's post-ADR-0066 audit-trail design (three
  compliance layers, deploy-log schema, journal, policy gate + sinks,
  CloudEvents/ECS envelope, CLI surface) plus its own documented defects,
  as the evidence base for a future v2 audit-trail ADR. No v2 decisions
  made in this doc.
- 2026-09-30: Resolved Open Question #1, per request ("lets look at what
  cfg-int-deployment actually needs from v2"). Read the real, current
  `config/audit.yaml` and `.github/` directly: Layer 1 (PR template +
  extraction) and Layer 2 (deploy-log + manifest) are both real and
  actively load-bearing — Layer 2 specifically git-pushes to a real
  durable remote (`env-int-deployment`) on every deploy, not just local.
  Layer 3 (CLI reporting) has zero CI evidence; Layer 4 (policy + sinks)
  has real policy config but the one declared sink is disabled. Also
  corrected a stale blocker: this doc originally said v2 had no `deploy
  run` yet, gating any deploy-log design — that's no longer true (built,
  ADR-0027). Net effect: Layer 2 is both the most load-bearing piece for
  this real consumer and no longer blocked — the clear next build target.
- 2026-09-30: Added "v2 Design Notes" (durable push destination — why
  `SolutionRemoteModel` can't be reused as-is, proposed `RepositoryPushModel`
  referencing a remote by name plus a mutable `branch`). Catalogued v1's
  previously-unread `DeploymentManifestModel` and its `SbomReferenceModel`/
  `DeploymentOutputsModel`/`ChangeReferenceModel` satellites — flagging its
  substantial field overlap with the deploy-log as the concrete problem a
  v2 "one event file + one manifest file" design must resolve.
- 2026-09-30: Layer 1 (PR template + issue template scaffolding) design
  started, per request ("lets then first look at strata sln(solution)
  init command... lets start there") — see
  [solution-scaffolding.md](solution-scaffolding.md), which ports v1's
  `strata sln init`/`sln update` scaffolding mechanism as the delivery
  vehicle for this layer specifically (v2 has no scaffolding command
  surface at all today).
- 2026-09-30: Drafted the first concrete two-file design ("The two audit
  files — Deployment Manifest + Event"), per request ("lets create a first
  design for the audit files"). `_manifest.json` merges deploy-log +
  deployment-manifest content (keeping deploy-log's richer per-step
  success/duration, dropping the manifest's coarser duplicate); resolves
  the platform-artifact embed-vs-reference inconsistency by making it a
  hash+path reference like sbom/outputs. `_event.json` stays genuinely
  thin — references the manifest by path/hash instead of dumping the full
  payload in, unlike v1's `data.strata`. Left open: on-disk path
  convention, whether non-outcome event types persist a file too, and how
  future satellite kinds register as new reference fields.
- 2026-09-30: Drafted the end-of-run distribution sequence and shared path
  resolution ("Write once locally, then distribute" and "Path
  resolution"), per the user's walkthrough ("we create at the end the 2
  files, then loop each endpoint of the audit and send the data there
  (siem/git/webhook/...)"). Both files are finalized and written once,
  locally, after everything (including PR enrichment) is known — removing
  v1's own double-write-with-a-known-incomplete-window defect. Unified
  v1's two separate destination mechanisms (`AuditConfigModel.repository`
  single git target + `sinks: list[AuditSinkModel]` SIEM/webhook-only)
  into one destination list, `git` becoming just another sink `type`.
  Path resolution — base path + structure template + a mandatory,
  unconditionally-appended `yyyymmdd-hhmmss` leaf (using the run's
  `started_at`, fixed at run start) — is shared identically between the
  local write and every push destination; the mandatory leaf closes a
  real v1 bug where the `flat`/`by-stage` structures have no timestamp
  component and silently overwrite the prior run's record.
- 2026-09-30: Added a third file, `_metrics.json` ("A third file —
  `_metrics.json`"), per request ("also add runtime and
  failure/success/errors — should we create a metrics.json?"). Grounded in
  v1's own `docs/decisions/0064-deployment-metrics-record.md` (external,
  status proposed, never implemented) rather than designed from scratch:
  self-containment invariant (no history-scanning/aggregation at deploy
  time — that's a deferred downstream consumer's job), dimensions/measures/
  sections split with explicit `measured` flags, `error_category` enum
  instead of raw error text, `label_safe` cardinality list, deploy/
  destroy-only scope (never build, never dry-run), and a durable
  `.strata/metrics/deployments.ndjson` append series alongside the
  per-execution file so a future aggregation consumer has an offline
  corpus even after build directories are cleaned. Distributed through the
  same unified destination loop as the other two files — nothing new to
  build on the transport side.
- 2026-09-30: Drafted the audit sink model ("The endpoints — the audit
  sink model"), per request ("now lets describe the endpoints"). Verified
  directly against v1's real `AuditSinkModel` source rather than the
  earlier summary: v1 has exactly one sink shape (`{name, integration,
  enabled, events}`), not three — `siem`/`webhook`/`syslog` are Integration
  *types*, not sink types. This corrected an imprecision in this doc's own
  prior draft, which had described a `type: git` sink with inline
  `remote`/`branch`/`path` fields in a way that read like new transport
  config on the sink; restated as two structurally different arms
  (`integration` | `git`, exactly one set, enforced by a validator) where
  the git arm's fields are routing, not transport — actual git transport
  stays behind `remote.url`/`remote.integration`, preserving ADR-0066's
  real invariant for both arms. Also resolved the "reuse v2's integration
  layer" open question (a new `Capability.AUDIT` member gates
  `sink.integration`, same mechanism as every other capability-gated
  reference) and flagged one new open loose end: whether `_metrics.json`
  should itself be CloudEvent-shaped on disk for `deployment.measured`
  forwarding/resend symmetry.
- 2026-09-30: **Layer 1's scaffolding half is now built**, per
  [solution-scaffolding.md](solution-scaffolding.md)'s Phase 3
  (documentation pass, closing that design out). `strata sln init`/
  `update` are real, tested CLI commands — added a callout under "Layer
  1" itself (not just the Remaining Work summary) so a reader hits the
  "this part is done" note at the point where it's actually described,
  and updated the Remaining Work conclusion to stop describing it as a
  future "close second" candidate. The `gh`-based PR-extraction half, and
  all of Layer 2 (deploy-log/manifest — this doc's own "v2 Design Notes"
  section already designs it in detail), remain unbuilt.
- 2026-09-30: Streamlined the configuration surface ("Streamlining the
  configuration surface — `spec.audit` is fully optional"), per request
  ("do we still have a journal? metrics path? structure? ... i would like
  them streamlined"). Collapsed three independent path-ish keys (`path`,
  `journal.path`, `metrics_path`) into one (`spec.audit.path`, default
  `.strata/audit`), with the journal and metrics append-series paths now
  derived from it instead of separately configured; simplified
  `structure` from v1's 8-name built-in template lookup
  (`flat`/`by-stage`/`by-execution`/`by-date`/`by-environment`/
  `by-workspace`/`by-tenant`/`full`) to one plain, optional Jinja2 string
  (default `{{ workspace }}/{{ deployment }}`) — several of v1's named
  templates existed only to smuggle a timestamp into the path, which the
  now-mandatory `yyyymmdd-hhmmss` leaf already makes redundant. Confirmed
  `journal`/`policy`/`sinks` were already fully optional with sensible
  defaults; net result is `spec.audit` can be omitted from
  `configuration.yaml` entirely and still produce a complete local audit
  trail. Updated "Path resolution", "A third file", and "Example
  configuration" to match.
- 2026-09-30: Dropped `_event.json` as a persisted file ("The audit files
  — Deployment Manifest (+ Metrics); the Event is rendered, not
  written"), per request ("that is the question. do we still want the
  audit.json? seems pointless?"). Correct instinct: in the zero-config
  default (no sinks), every field the CloudEvent carried was already in
  `_manifest.json`, so the file was written on every run and read by
  nobody. Resolution: the event is now a pure rendering,
  `render_event(manifest | metrics)`, computed only at the moment of
  dispatch to an **integration** sink (initial send or `audit resend`,
  same function both times) — v1 defect #7 is satisfied by *code-path*
  identity, not *byte* identity, which is arguably the more correct
  guarantee anyway (CloudEvents' `id` is meant to be fresh per delivery
  attempt). Persisted content is now exactly two files, `_manifest.json`
  and `_metrics.json`; updated "The endpoints", "Path resolution", "A
  third file", and "Example configuration" to match, and resolved the
  standing `deployment.measured` enveloping question as a side effect —
  both event types now go through the same render-then-forward path.
- 2026-09-30: Resolved the remaining open questions, per request ("lets
  get back to the previous topic" -> "resolve remaining open questions
  first"). (1) Actor/identity: ship a weak, best-effort actor now (real
  CI identity env var — `BUILD_REQUESTEDFOR` for `cfg-int-deployment`'s
  actual Azure Pipelines CI — falling back to the OS user, falling back
  to `"unknown"`; never an exception), revisit once v2 has a real
  identity model. (2) Config location: `ConfigurationSpecModel.audit`,
  matching v1 and this repo's own `security`/`paths` precedent (gap #7).
  (3) Event taxonomy: CloudEvents+ECS confirmed as the eventual shape,
  but its cost is deferred along with the `integration` sink arm's
  dispatch itself (see next). (4) Layer scope: formalized that Layer 2's
  *first* implementation only needs the `git` sink arm (the one real,
  active need) — the `integration` arm stays modeled (cheap, already
  designed) but undispatched until a real consumer needs SIEM/webhook
  forwarding, matching this doc's own "Lessons from v1's own defects" #1
  (declared-but-unread machinery). All open questions are now resolved;
  ready for a Layer 2 Implementation Plan.



