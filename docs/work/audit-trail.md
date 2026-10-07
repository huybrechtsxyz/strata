# Audit Trail — v1 Capability Catalog and v2 Design Questions

- Status: draft — real-usage scoping done, Layer 1 shipped, the design
  fully walked end-to-end with a worked example, and the full 5-phase
  Layer 2 Implementation Plan implemented (2026-09-30). `deploy run` now
  writes a real, local audit manifest + metrics record on every
  invocation, and distributes them to configured `git` sinks. Layer 2 is
  live. `strata audit status` (Layer 3 reads) is also now implemented
  (2026-10-05) — see [audit-commands.md](audit-commands.md). The manifest's
  `sbom` reference is now also wired to the real build-time SBOM
  (2026-10-03). Layer 4's actual dispatch is designed and implemented
  separately in
  [audit-sink-dispatch.md](audit-sink-dispatch.md) (2026-10-03) — the
  `integration` sink arm this doc left as a stub now really sends, via a
  generic `webhook` integration. A NIS2/ISO 27001/ISAE 3402 compliance
  gap analysis + MoSCoW (2026-10-03) found Q2 ("why")/Q3 ("who approved")
  fully unpopulated in the shipped manifest — see "Compliance Gap
  Analysis" — not yet implemented. An ADO-approval-gate addendum
  (2026-10-04) found v1 already designed this exact idea
  (`DeploymentGateModel`'s `declare` mode), unused by any real consumer —
  **promoted to Should-have (2026-10-04)**: pipeline development is in
  progress and an ADO approval step will be created once this stack
  reaches production. M1 (commit identity) was pressure-tested
  (2026-10-04) and its scope corrected: it is a reproducibility anchor
  ("exact repo state used"), not a claim about who authored the
  meaningful change — see "What `commit_sha` can and can't claim." M1
  was then re-prioritized from Must-have to Could-have (2026-10-05, no
  confirmed demand, shrunk value). Its proposed replacement, Layer 3
  reporting, was designed (new `strata audit` command group) then
  **deferred (2026-10-05)** before any code was written — the design
  read local `.strata/audit/` files, the wrong data source given ~90% of
  real deploys are ephemeral CI (same reasoning that already removed
  `deployments.ndjson` earlier in this doc). The Must-have category is
  genuinely empty — see "Layer 3 deferred." **`change_reference` CLI
  wiring is now implemented (2026-10-05)**: six `deploy run --change-*`
  flags, closing Q2 ("why") for any deploy that supplies them — see the
  Should-have list. **`strata audit status` AND `strata audit changes`
  are now both implemented (2026-10-05)** — see
  [audit-commands.md](audit-commands.md)'s Phases 1-7, all complete:
  `status` reads the configured `git` sink's latest record per
  deployment, `changes` lists every record in a range — the full ISAE
  3402 "enumerable sample over a period" ask this Must-have exists for.
  A full review pass (2026-10-05) found and fixed three real bugs
  (a command-name-specific error message, a non-inclusive `--until`
  bound, a misleading empty-result message) — see audit-commands.md's
  own changelog for the detail. **`DeploymentManifestModel.
  policy_results` population is now implemented (2026-10-07)** — `build
  run` writes an optional `build_path/policy_results.json` sidecar for
  `cve_policy`'s own evaluation result, read back and folded into
  `_manifest.json` by `finalize_and_distribute_deploy_audit()`. See
  "Implementation Plan"'s own entry below for the full detail.
- Last updated: 2026-10-07

## Overview

v2 has no audit trail yet — `ConfigurationSpecModel` deliberately defers
`spec.audit` alongside `zones`/`remotes`/`policies`/`paths` (see that model's
own docstring, and gap #7 in [`docs/work/gap_fit_v1.md`](gap_fit_v1.md)). Before
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
    events: list[str] | None = None          # filter — None means every gate-admitted event type

    # Annotated with References(PlatformKind.INTEGRATION) — same as every other
    # "names an Integration document" field in v2 (artifact_model.py,
    # provisioning_model.py) — so the generic reference-walker (ADR-0015)
    # structurally checks this resolves, instead of only failing at dispatch time.
    integration: Annotated[PlatformName, References(PlatformKind.INTEGRATION)] | None = None  # arm 1
    git: AuditGitSinkTargetModel | None = None  # arm 2 — durable git push

    @model_validator(mode="after")
    def validate_exactly_one_target(self) -> "AuditSinkModel":
        if (self.integration is None) == (self.git is None):
            raise ValueError(f"sink '{self.name}': exactly one of 'integration' or 'git' must be set.")
        return self
```

**Sink names must be unique, same discipline as every other named list on
`ConfigurationSpecModel`.** `providers`/`topologies`/`paths` each get a
`check_unique_names()` model validator (`configuration_model.py`) — two
sinks sharing a name is the same class of silent footgun, and matters more
once something like `audit resend --sink <name>` exists. `AuditConfigModel`
gains the identical validator over `sinks`, not a new pattern.

**`events: list[str] | None` stays a plain string list — not upgraded to
an enum, despite the vocabulary being closed.** Considered matching
`Capability`'s own `StrEnum` typo-safety rationale, but the two cases
differ: `Capability` gates a static, in-code dispatch site
(`if Capability.X in ...`) where a typo is a silent no-op forever. An
event *name* here is checked against the runtime event gate's own known
vocabulary at validation time regardless, so a typo surfaces immediately
as a validation error either way — an enum would add a second place event
names must be kept in sync (the model and the gate) for no extra
safety. Revisit only if event names start being referenced from more than
one place the way `Capability` is.

```yaml
spec:
  audit:
    sinks:
      - name: prod-siem
        integration: splunk-prod          # arm 1
        events: [deployment.completed, deployment.measured]
      - name: config-repo
        git:                                # arm 2
          remote: xyz-configuration
          branch: main
          path: audit
        events: [deployment.completed, deployment.destroyed]
```

**Admission mechanics are unchanged, and now apply identically to both
arms** — the same three-condition rule v1 already has (global event gate
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
"a sink" is about sharing *admission* (gate/enabled/events) and
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

**Capability gating — deferred alongside the integration arm's dispatch,
not added in this first pass.** An earlier draft of this section proposed
adding `Capability.AUDIT` now, gating `sink.integration` the same way every
other capability-gated reference in v2 works. Caught on review: that would
be dead on arrival — `Capability` (`models/integration_model.py`) is a
deliberately closed, minimal vocabulary, its own docstring stating it is
"extended only when a concrete v2 feature needs it" (ADR-0021 D9), and
nothing dispatches through `Capability.AUDIT` in this pass, since the
"Does v2 need all four layers, or fewer?" decision above explicitly defers
the `integration` arm's *dispatch* to a later pass. Adding the enum member
now, before any code checks it, is exactly the "declared-but-unread
machinery" this doc's own "Lessons from v1's own defects" #1 warns
against — so `Capability.AUDIT` is added together with the integration
arm's dispatch itself, not before. `AuditSinkModel.integration` is still
annotated with `References(PlatformKind.INTEGRATION)` today (see above) —
that only checks the name resolves to *some* `Integration` document, which
is correct and cheap in either case; the capability check is the part
that's deferred.

### Dispatch failure — a `required` flag, not a binary silent-vs-fail choice

Raised by the user, walking the full end-to-end loop: a `git` push (or,
later, an integration send) can fail — network down, a non-fast-forward
conflict, bad credentials. Neither extreme is right: always failing
`deploy run` over a durability push means an infrastructure change that
already succeeded gets reported as a failure; always swallowing it
silently reintroduces exactly the "declared real, but doesn't actually
work" problem this whole Layer 2 effort exists to fix (the real,
load-bearing `env-int-deployment` push).

**Resolution — reuse `Diagnostics`' existing three-tier `Severity`, plus
one field v2 already has a name and default for.** `Integration.required:
bool = False` ("whether this integration is required for platform
operation", `integration_model.py`) is the same shape this needs, so
`AuditSinkModel` gains an identically-named field rather than inventing
new vocabulary:

```python
class AuditSinkModel(PlatformBaseModel):
    ...
    required: bool = False  # same name/default as Integration.required
```

A dispatch failure **always** produces a `Severity.WARNING` diagnostic —
visible in `deploy run`'s report every time, never truly silent. Only when
`required: true` does that same failure escalate to `Severity.ERROR`,
which fails `require_valid()`'s downstream check and blocks the command
at exit code 3 (`command_run`'s existing `ValidationError` mapping —
nothing new needed there). Default `False` matches the common case; an
operator who has decided durable audit is non-negotiable (a compliance
sink, say) opts in per-sink. Composes cleanly across multiple sinks too —
a SIEM sink can be `required: true` while a `git` durability sink stays
`required: false`, or the reverse; not an all-or-nothing switch.

### Streamlining the configuration surface — `spec.audit` is fully optional, and `path`/`structure`/`journal` are dropped, not just defaulted

Raised by the user, reviewing `path`/`structure`/`metrics_path`/`journal`
together rather than one at a time — then revisited once more on a second
pass (2026-09-30): three of those turned out not to be configuration at
all, once checked against `layout.py`'s own rule and real usage evidence.

**`path` is dropped outright — `.strata/audit` is a layout fact, not a
choice.** `layout.py`'s own docstring: *"Every derived path belongs here.
Nothing outside this module should join a path segment, hardcode a
directory name, or decide where something lands on disk."* `.strata/audit`
is the same tier as `REMOTES_DIRNAME` — a new `layout.audit_dir(root)`
(`root / STRATA_DIR / "audit"`) replaces `spec.audit.path` entirely; a
config author never declares it, the same way `.strata/remotes` is never
declared either.

**`structure` is dropped too — derived from `DeploymentLayersModel.segments`
when a deployment already has one, not a second templating mechanism.**
The original proposal was a plain Jinja2 `structure` string (default
`"{{ workspace }}/{{ deployment }}"`). Caught on review:
`path_conventions.py` is match-only (a real path against a pattern, never
the reverse), so that field would have been a disconnected second way to
describe a directory shape the config may already declare once, via
`spec.paths` + a deployment's own `layers` block. Where a deployment
declares `layers.follows`/`layers.segments` (already validated against a
`spec.paths` convention by `semantic_checks.py`), the audit directory is
built by joining those *resolved* segment values, in the order that
convention's own `PathConventionModel.segments[]` list declares them —
e.g. `layers.segments == {hub: z00, spoke: s01, customer: c0224, ring:
dev}` under a convention ordering `[hub, spoke, customer, ring]` produces
`z00/s01/c0224/dev`, matching the deployment's real hierarchy for free.
Where no `layers` block exists, fall back to a fixed `f"{workspace}/
{deployment}"`. No template engine, no config field, either way.

**`journal` is deferred out of this pass entirely — no evidenced
consumer.** Checked against the same real-usage bar that justified
building Layer 2 next: in `config-deploy` it runs "always on, local,
gitignored," but nothing reads it, nothing forwards it, and it played no
part in the finding that justified this work (deploy-log + manifest
git-push durability). Same "declared-but-unread machinery" caution this
doc already applied to `Capability.AUDIT` above — designed and added in
its own pass once something needs it, not bundled into Layer 2 by
default. Dropped from `AuditConfigModel` for now, not merely optional.

**`policy`/event-overrides resolved — see "Event admission" below,
immediately after this section.** `sinks` needed no further changes.

**Net result:** `spec.audit` can be omitted from `configuration.yaml`
entirely. A deploy still writes to `.strata/audit/<resolved
structure>/<yyyymmdd-hhmmss>/{_manifest,_metrics}.json` and forwards to
nothing — a complete local audit trail with zero YAML. (No
`deployments.ndjson` — dropped, see "A third file" below.)

### Event admission — a closed set gate, not a policy framework

Raised by the user ("do we need policies (a place/framework first?)") —
checked directly against v1's real `models/audit_config_model.py`, not
just the earlier prose catalog. Once distilled to what's actually used,
it is a lookup table with a validator, not the deny/warn/multi-type
engine `PathConventionModel.enforcement` deliberately declined to port —
no separate framework needs to exist first.

**What v1 really has, stripped of what's unused:** a closed set of event
types, each with a class-aware default (set by measuring an 18,853-entry
real audit.log sample, not guessed — `command.executed` defaulting off
alone removed ~95% of volume); a flat per-type bool override merged over
those defaults; a cross-check that a sink can't filter on an event type
the gate has disabled or one outside the closed set (`ValueError`, exit
code 3 — not a silent no-op); and a fail-open rule for any event type a
future producer emits that isn't in the closed set at all (never gated
off by a model that doesn't know about it yet).

**Two simplifications from v1's real shape, not a straight port:**

1. **Drop the `Union[bool, AuditEventPolicyModel]` object shape.** v1's
   own comment on the reserved `severity`/`sample`/`retention_days` fields:
   "not read by any producer yet" — deliberately commented out to keep the
   surface honest rather than aspirational. Same instinct, one step
   further: don't port the wrapper object at all. Flatten to `dict[str,
   bool] | None` directly.
2. **Scope the closed set to what v2 actually produces, not v1's 20
   types.** v1's set includes `workitem.*`, `policy.violated`,
   `secret.accessed`, `lock.*`, `drift.*`, `cost.*` — none of which have a
   producer anywhere in v2 yet (no workitem controller, no policy engine,
   no lock/drift/cost tracking). Importing that whole list now would be
   the same "declared-but-unread machinery" this doc already flagged for
   `Capability.AUDIT`, seventeen times over. v2's closed set for this pass
   is exactly the three events Layer 2 itself emits, all defaulting
   enabled (matches v1's own real defaults for these three): `deployment.
   completed`, `deployment.destroyed`, `deployment.measured`. Grows only
   when a new producer lands, same discipline as `Capability`.

**Real-usage tension, named rather than hidden:** `config-deploy`'s
actual overrides are `secret.accessed`/`policy.violated`/
`workitem.rejected` — all three **outside** this scoped set. Expected,
not a problem: none of those producers exist in v2 yet, so that config
simply wouldn't carry those overrides until the corresponding producer is
built — the same incremental-porting story as every other gap closed so
far.

**Also dropped: the word "policy" itself.** No field or model is named
`policy`/`AuditPolicyModel` — v2 has no other "policy" concept, and the
word implies the multi-type engine this isn't.

```python
# Directly on AuditConfigModel, no nested model:
event_overrides: dict[str, bool] | None = None  # overrides over the built-in per-type defaults

# Module-level, not configurable — grows only when a new producer lands:
_EVENT_DEFAULTS: dict[str, bool] = {
    "deployment.completed": True,
    "deployment.destroyed": True,
    "deployment.measured": True,
}
```

Both of v1's real validators are kept, ported against this smaller table:
an unknown key in `event_overrides` is a `ValueError`; and
`AuditSinkModel.events` entries must be in the closed set *and* resolve
enabled — the same cross-check `AuditConfigModel.
validate_sink_filters_against_gate` already does in v1, just scoped down.

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
    # no `path`, no `structure` — .strata/audit is a fixed layout location
    # (layout.audit_dir()), and the per-execution directory shape is
    # derived from this deployment's own `layers.segments` when declared,
    # falling back to workspace/deployment otherwise (see above). Neither
    # is a field on this model.

    event_overrides:                 # flat, no nested "policy" model — see "Event admission" above
      deployment.measured: false     # example override; all three default to true otherwise

    sinks:
      - name: prod-siem
        integration: splunk-prod   # arm 1 — integration reference, no transport fields here
        events: [deployment.completed, deployment.destroyed]

      - name: config-repo
        git:                        # arm 2 — durable push, no Integration reference at all
          remote: xyz-configuration
          branch: main
          path: audit                # root prefix under the repo — relative_path is appended under this
        events: [deployment.completed, deployment.destroyed]
```

Reading the config: `event_overrides` disables `deployment.measured`
entirely for this landscape (no `_metrics.json` dispatch anywhere, though
the file is still written locally — the override gates *forwarding*, not
local persistence), so neither sink lists it. `prod-siem` receives a
CloudEvent rendered on the fly from `_manifest.json` for
`deployment.completed`/`destroyed`, sent to Splunk over HEC using
credentials that live entirely on the `splunk-prod` integration document
— nothing sink-specific to leak, and no envelope file ever touches disk.
`config-repo` instead pushes copies of whichever per-execution files
exist (`_manifest.json`, `_metrics.json` — the metrics file is still
written and still pushed here, since the git sink replicates files
wholesale rather than rendering individual event types) under
`audit/<tenant>/<deployment>/<yyyymmdd-hhmmss>/` in the
`xyz-configuration` repo's `main` branch, authenticated via whatever
`github-deploy-key` (the remote's own `integration`) provides — two
structurally different operations, admitted by the identical
gate/enabled/events rule, dispatched from the same end-of-run loop.

### Path resolution — shared by the local write and every push destination

One resolution function, reused everywhere a destination needs to know
"where do these two files go":

```
if deployment.spec.layers and deployment.spec.layers.segments:
    convention = lookup(configuration.spec.paths, deployment.spec.layers.follows)
    ordered_values = [deployment.spec.layers.segments[s.name] for s in convention.segments]
    relative_path = Path(*ordered_values)
else:
    relative_path = Path(workspace, deployment)

relative_path = relative_path / "{yyyymmdd-hhmmss}"   # concrete leaf, always appended

# No template string, no `structure` field — this is a plain function of
# already-resolved model data (see "Streamlining the configuration
# surface" above).
```

- **Local write:** `local_base_path / relative_path / _manifest.json` (and `_metrics.json`) — `local_base_path` is `layout.audit_dir(root)` (`.strata/audit`, fixed — see "Streamlining the configuration surface" above)
- **`git` sink:** `checked_out_repo_root / sink.git.path / relative_path / _manifest.json` (and `_metrics.json`) — the *same* `relative_path`, rooted under the sink's own `path` instead of the local base
- **`integration` sink:** no filesystem path at all — the CloudEvent is rendered fresh from the manifest/metrics content at dispatch time (see "The audit files" above); `relative_path` still travels as a field on the rendered payload (alongside `execution_id`), so a SIEM record and its on-disk/git counterpart can be correlated without a lookup

**The `yyyymmdd-hhmmss` leaf is unconditional — appended regardless of
what the derived directory shape itself contains.** This is a deliberate
fix, not just a formatting choice. v1's own built-in structures include
`flat: "{{ deployment }}"` and `by-stage: "{{ deployment }}/{{ stage }}"`
— *neither has a timestamp component at all* — so under those two
structures, every subsequent run for the same deployment/stage **silently
overwrote** the previous `_execution.json`; picking the "wrong" structure
name was enough to lose audit history with no error. Making the timestamp
leaf mandatory and separate from the derived directory shape closes that
class of bug structurally: the collision-avoiding segment is never
something a config author's `layers.segments` (or its absence) can omit.

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

**Dropped — the durable local append series, per real-usage evidence
(2026-09-30).** Originally proposed: alongside every per-execution
`_metrics.json`, also append one line to a fixed, never-cleaned local path
so a future aggregation consumer has an offline corpus to read, since
per-execution build directories get cleaned. Caught on review, walking the
full end-to-end loop with real usage in mind: **~90% of real deploys run
in ephemeral CI** (`config-deploy`'s own is Azure Pipelines) — a
runner checks out fresh, writes exactly one line to a local-only file,
then the runner and that file are both destroyed. The series would never
actually accumulate where most runs happen, which is backwards from its
own stated purpose. A local-only append series is real value only for the
minority long-lived/manual-run case — not enough to justify building it
now, especially since nothing reads it yet either (`strata metrics`/
`audit trends` doesn't exist). Dropped entirely from this pass, same
treatment as `journal` — revisit properly (the real fix is having the
`git` sink also append to a *remote*-tracked series inside its existing
push transaction, so it's durable exactly where the majority of runs
happen) once `strata metrics` is actually being designed, not bundled in
here speculatively.

**Deliberately excluded, per v1's ADR (still applies):**
`time_since_previous_deploy_seconds` (violates the self-containment
invariant — trivially derived downstream from timestamps across records
instead), output *values* (counts and key names only — outputs already
track `sensitive_keys` for exactly this reason), inline SBOM (a reference
by digest, not the content — same discipline already applied to
`artifacts.platform`/`sbom` in the manifest).

## Implementation Plan

Everything below is settled design (this whole "v2 Design Notes" section,
its open questions resolved, the full end-to-end loop walked once with a
worked example). Phased the same way [solution-scaffolding.md](solution-scaffolding.md)
was — one phase, one focused change, full check suite, then the next.

**Explicitly out of scope for every phase below** (already decided
above, restated here so a phase reviewer doesn't have to re-derive it):
the `integration` sink arm's dispatch and `Capability.AUDIT` (modeled,
never called); the CLI-invocation `journal`; the local
`deployments.ndjson` append series; Layer 3 CLI reporting
(`audit changes`/`diff`/`resend`/`status`); the generic policy engine.

### Phase 1 — Config models — ~~IMPLEMENTED (2026-09-30)~~

- New `src/strata/models/audit_model.py`: `AuditGitSinkTargetModel`
  (`remote`/`branch`/`path`), `AuditSinkModel` (`name`, `enabled`,
  `required`, `events`, `integration` w/ `References(PlatformKind.
  INTEGRATION)`, `git`, exactly-one-arm validator), `AuditConfigModel`
  (`event_overrides: dict[str, bool] | None`, `sinks: list[AuditSinkModel]
  | None`, unique-sink-names validator via `check_unique_names()`).
  `EVENT_DEFAULTS` is the module-level closed set (named plainly, not
  `_EVENT_DEFAULTS` — it's read by tests and will be read by Phase 5).
- `ConfigurationSpecModel.audit: AuditConfigModel | None` field added in
  `configuration_model.py`, matching `security`/`paths`'s exact style.
- `layout.audit_dir(root) -> Path` added in `utils/layout.py` (`root /
  STRATA_DIR / "audit"`) — no `path` field anywhere, per "Streamlining
  the configuration surface".
- The unknown-key validator and the sink-filters-against-gate cross-check
  are both ported from v1's real `AuditConfigModel`/`AuditPolicyModel`,
  scoped down to the 3-entry closed set — see "Event admission".
- Tests (`tests/strata/models/test_models_audit.py`, 17 new): exactly-one-
  arm validator (both directions), `required` defaults `False`, unknown
  event type on a sink filter, `spec.audit` fully optional end-to-end,
  unknown `event_overrides` key rejected, unique sink names, sink
  filtering on a gate-disabled event rejected, `ConfigurationSpecModel.
  audit` wiring, and reference discoverability confirmed directly via
  `extract_references()` — both in isolation (`AuditSinkModel.integration`,
  `AuditGitSinkTargetModel.remote`) and transitively from the root
  `ConfigurationModel` (`spec.audit.sinks[].integration`/`.git.remote`).
  Full check suite green: mypy (113 files), ruff, import-linter (1 kept,
  0 broken), pytest (1403 passed — same pre-existing, unrelated `config/`
  example-solution drift as the sole failure).

### Phase 2 — Manifest and metrics models — ~~IMPLEMENTED (2026-09-30)~~

- New `src/strata/models/audit_manifest_model.py`: the merged manifest
  shape from "The audit files" table — identity, timing, outcome (tri-
  state `status`), flags, actor + git context, PR/change-reference
  evidence (`ChangeReferenceModel`, `ManifestPullRequestModel`),
  `artifacts.platform` as hash+path reference (not embedded), `stages[]`
  (deploy-log's richer per-step shape via `ManifestStepModel`), satellite
  references (`sbom`/`outputs`/`signatures`/`policy_results`/`lock`),
  catch-alls. No `apiVersion`/`kind`/`meta` envelope — a written artifact
  under `.strata/`, never a discoverable `(kind, name)` document.
- New `src/strata/models/audit_metrics_model.py`: `MetricsDimensionsModel`/
  `MetricsMeasuresModel`/`MetricsSectionModel` split, `ErrorCategory`
  `StrEnum`, `label_safe` defaulting to the bounded dimension subset
  (`outcome`/`action`/`environment`/`tenant`), a validator confirming
  `error_category` is only set alongside a `failed` outcome.
- New `src/strata/controllers/audit_event_rendering.py`:
  `render_manifest_event()`/`render_metrics_event()` (CloudEvents 1.0 +
  ECS shape) — modeled and unit-tested now even though nothing calls it
  yet in this pass (only the `integration` arm would call it, and that's
  deferred). Lives in `controllers/`, not `utils/`, since it depends on
  concrete model types (ADR-0003 — `utils/` never depends on `models/`).
- Tests (23 new — 10 manifest, 8 metrics, 5 event-rendering): tri-state `status` values, `artifacts.platform` rejects
  an embedded `content` field outright (`extra="forbid"`), stage
  steps/outputs-artifact shapes, `error_category`/`outcome` cross-
  validation, `label_safe` excludes unbounded fields by default, rendered
  envelope matches the documented JSON shape field-for-field (including
  the destroy→`deployment.destroyed` type switch, ECS `duration` in
  nanoseconds, a fresh `id` on every call, and no wholesale payload dump
  in `data`). Full check suite green: mypy (116 files), ruff,
  import-linter (1 kept, 0 broken), pytest (1426 passed — same
  pre-existing, unrelated `config/` example-solution drift as the sole
  failure).

### Phase 3 — Resolution helpers — ~~IMPLEMENTED (2026-09-30)~~

- `utils/actor.py::resolve_actor() -> str` — pure function, no
  dependency on other layers: `BUILD_REQUESTEDFOR` ->
  `BUILD_REQUESTEDFOREMAIL` -> `getpass.getuser()` -> `"unknown"`, never
  raises. Lives in `utils/` (not `controllers/`), matching
  `path_conventions.py`'s own placement rationale — pure, no
  cross-document lookups.
- `controllers/audit_path_resolution.py::resolve_audit_relative_path(...)
  -> Path` — the derivation from "Streamlining the configuration
  surface"/"Path resolution": join a deployment's `layers.segments` in
  its convention's declared segment order when present, else fall back to
  `Path(workspace, deployment)`; always appends the mandatory
  `yyyymmdd-hhmmss` leaf from the run's `started_at`. Lives in
  `controllers/` (not `utils/`) because it needs the resolved
  `PathConventionModel` alongside the `DeploymentModel` — a
  cross-document lookup, same tier as `semantic_checks.py`. Reuses the
  identical `{c.name: c for c in configuration.spec.paths}` lookup
  `semantic_checks.py::_check_deployment_layers()` already uses, rather
  than inventing a second convention-lookup shape — never raises on a
  missing/mismatched declaration, since reporting that mismatch is
  `semantic_checks.py`'s job, not this function's; it just falls back.
- Tests (18 new — 5 actor, 13 path resolution): CI-env-var precedence
  order, OS-user fallback, `getpass` failure still resolves to
  `"unknown"`, never raises; segment-order-derived path for a fixture
  `layers` block, fallback path when no `layers` block/no configuration/
  unknown convention name/incomplete segments, `yyyymmdd-hhmmss` leaf
  always present and colon-free in every case. Full check suite green
  (mypy 118 files, ruff, import-linter, pytest 1439 passed — same
  pre-existing, unrelated `config/` example-solution drift as the sole
  failure).

### Phase 4 — Git push destination — ~~IMPLEMENTED (2026-09-30)~~

- New `controllers/audit_push.py::push_audit_files(root, sink, solution,
  files, relative_path, actor) -> PushResult` — resolution keyed by
  `(remote, branch)` via new `layout.audit_push_checkout_path()`, not
  `(remote, reference)` like `resolve_remote()`; always fetch + reset
  before writing (never trusts an existing checkout indefinitely, unlike
  `resolve_remote()`'s read-path caching); copies the given files in,
  configures a local (not global) git identity from the resolved `actor`
  so `git commit` never fails on a CI runner with no identity configured,
  then `git add`/`commit`/`push` using the `HEAD:<branch>` refspec trick.
  Reuses `utils.transport.run_command` and the established
  `{remote.name: remote for remote in solution.spec.remotes}` lookup
  pattern (`deploy_controller.py`/`build_controller.py`/`version_pins.py`
  all already do this inline; matched rather than inventing a shared
  helper). `PushResult(success, detail)` never raises for an ordinary
  push failure — Phase 5 decides `required`/`Severity` from it.
  Deliberately not resolving `remote.integration` for real credentials —
  matches `remote_resolution.py`'s own current, documented state (relies
  on git's own ambient auth; no `SourceIntegration` ABC exists yet).
- **Real-testing found and fixed a genuine edge case not in the original
  plan**: resetting to `origin/HEAD` unconditionally fails outright
  against a brand-new, completely empty remote (`fatal: ambiguous
  argument 'origin/HEAD'` — no commits exist upstream yet, so there is no
  default branch to point to). Fixed by falling back further: target
  branch exists upstream → reset to it; else the remote has *some*
  default branch → reset to that; else (a genuinely empty remote) nothing
  to reset to at all — proceed from the fresh, empty clone as-is. Found
  by a real end-to-end test against a real empty bare repo, not assumed
  from reading git's docs.
- Tests (11 new): 6 with a mocked `run_command` (matching
  `test_remote_resolution.py`'s own established style — unknown remote,
  non-git remote type, no solution, identity configured before commit,
  clone failure short-circuits, the full clone→fetch→reset→push command
  sequence); 3 real end-to-end round trips against a real local bare git
  repository (first push to a brand-new empty remote; a second push
  reusing the same checkout directory, confirming fetch+reset against a
  now-real `origin/<branch>`; the checkout path matches
  `layout.audit_push_checkout_path()`). Full check suite green (mypy 119
  files, ruff, import-linter, pytest 1447 passed — same pre-existing,
  unrelated `config/` example-solution drift as the sole failure).

### Phase 5 — Wiring into the run — ~~IMPLEMENTED (2026-09-30)~~

- **Scope correction found by checking real code, not assumed**: no
  `destroy` command exists in v2 yet — "and the equivalent destroy path"
  in the original plan had nothing to wire. Deploy-only for this phase;
  extend when `deploy destroy` is built.
- **New module `controllers/audit_run.py::finalize_and_distribute_deploy_audit()`,
  not a change to `deploy_controller.py::deploy_run()` itself** — a
  deliberate departure from the original plan. That function's existing
  fail-fast, single-return-diagnostics shape is complex, heavily tested,
  and load-bearing; it does not expose per-step stage/timing data today.
  Wrapping its *result* was chosen over restructuring its internals to
  fabricate that granularity — `stages[]` is therefore omitted from the
  manifest in this pass (coarser than "The audit files"' full design),
  revisited only if/when `deploy_run()` itself is refactored to expose
  that data (separate, larger work).
- **`artifacts.platform` references `build_path/resolved.yaml`**
  (`build_controller.write_resolved_manifest()`) — v2's closest real
  equivalent to v1's `platform.json`; v2 does not produce that exact file
  today. An honest adaptation, found by checking what `build_run()`
  actually writes rather than assuming a `platform.json` exists.
  `deploy_command.py::deploy_run_command` now generates `execution_id`/
  `started_at` once, before calling `deploy_run()`, and calls this new
  function after, merging its findings into the same `Diagnostics` the
  command already reports.
- One loop over `spec.audit.sinks` (empty/omitted is a no-op): admission
  (`EVENT_DEFAULTS` merged with `event_overrides` → sink `enabled` →
  sink's own `events` filter); `git` arm dispatches via Phase 4;
  `integration` arm is **not dispatched** — produces one `Severity.INFO`
  finding per configured integration sink instead, naming it explicitly.
  Dispatch failure escalates to `Severity.WARNING` by default, or
  `Severity.ERROR` (failing the command at exit 3) when `sink.required`
  is `true` — exactly as designed.
- Tests (13 new): zero-config writes both files locally with no dispatch
  attempted; the manifest reflects a failed run's `status`; a dry run
  writes nothing at all; a missing `resolved.yaml` warns (not errors) and
  skips cleanly; `event_overrides` disabling `deployment.measured`
  suppresses only that file's *dispatch*, never its local write; a
  configured `git` sink pushes to a real local bare repository; a forced
  push failure warns without failing when `required: false`, and fails
  the command when `required: true`; a configured `integration` sink
  produces the expected info finding and calls `push_audit_files` zero
  times. **A real test-isolation bug found and fixed along the way**: a
  fixture borrowed from `test_deploy_controller.py` patched the shared
  `shutil` module's `which` globally (to fake `terraform`), which broke
  this file's *real* `git` subprocess calls too, since both modules
  import the same `shutil` object — every push silently "succeeded" with
  0 files landed until the test asserted on `diagnostics.warnings`
  explicitly rather than trusting `.ok` alone (a warning never affects
  `.ok`). Fixed by making the fake `which` pass through to the real one
  for anything other than `terraform`. Full check suite green (mypy 121
  files, ruff, import-linter, pytest 1484 passed — same pre-existing,
  unrelated `config/` example-solution drift as the sole failure).

**Layer 2 Implementation Plan complete — all 5 phases shipped.**

### Follow-up — SBOM reference wired into the manifest (2026-10-03)

- **Gap found while comparing v2's audit capabilities against v1
  directly**: `DeploymentManifestModel.sbom` (`ManifestSbomReferenceModel`
  — path/format/sha256/component_count) was already modeled, but
  `finalize_and_distribute_deploy_audit()`'s `DeploymentManifestModel(...)`
  construction never passed `sbom=` — always `None`. Separately,
  `sbom_controller.write_sbom()` (gap #18 Phase 1) already computed this
  exact reference shape internally, but only to log it before discarding
  it — the real `sbom.json` and the deploy-time manifest were two
  unconnected artifacts.
- **Fixed by re-deriving, not threading through** — new
  `audit_run._sbom_reference(build_path)`, mirroring the already-proven
  `_platform_reference()` pattern exactly: re-hashes `build_path/sbom.json`
  fresh at deploy time rather than trying to carry an in-memory value from
  the earlier, separate `build run` process (the same reason
  `_platform_reference()` re-hashes `resolved.yaml` instead of doing the
  same). Unlike `_platform_reference()`, a missing SBOM raises no
  diagnostic — SBOM generation is itself optional.
- Promoted `sbom_controller.py`'s private `_SBOM_FORMAT` to a public
  `SBOM_FORMAT` constant so `audit_run.py` reuses the one source of truth
  instead of hardcoding a second copy of `"cyclonedx-1.6"`.
- **Tests (2 new)**: the manifest's `sbom` reference is populated
  correctly when `build run` already wrote a real `sbom.json` (using the
  real `write_sbom()` via the existing fixture, not a hand-crafted file);
  a missing SBOM produces a manifest with no `sbom` key and no warning.
  Full check suite green (mypy 133 files, ruff, ruff format, import-linter
  1/0, pytest 1836 passed).

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

Within this repo: [`docs/work/gap_fit_v1.md`](gap_fit_v1.md) gap #7 tracks
`audit` as still fully open/unstarted, alongside `policies`/`promotions`.
[`docs/design/v2-schema-overview.md`](v2-schema-overview.md) has no audit
entry yet — add one once a v2 ADR exists.
[`docs/design/audit-sink-dispatch.md`](audit-sink-dispatch.md) (2026-10-03)
is the child design for Layer 4's actual dispatch — the `integration` sink
arm this doc deliberately left as a stub, starting with a generic webhook.

## Remaining Work / Open Questions

This doc is capability catalog only — no v2 decisions are made here. Before
a v2 ADR/implementation:

- **Scope to real usage first, same as v1 should have.** **Answered
  2026-09-30**, per direct request ("lets look at what config-deploy
  actually needs from v2") — read the real, current
  `e:\sources\config-deploy\config\audit.yaml` and `.github/` directly:
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
  `config-deploy` usage exactly; only the `gh`-based PR-extraction
  half remains unbuilt (needs Layer 2's deploy-log/manifest to exist
  first). Layers 3 and 4 have no CI evidence of use and should wait for
  a concrete need.
- **Does v2 need all four layers, or fewer? — Decided (2026-09-30).**
  Build Layer 1 (done) and Layer 2 (next) fully. Layer 2 itself only
  needs the **`git` sink arm** (durable push — the one real, active
  need `config-deploy` already exercises); the **`integration` sink
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
  new plumbing: real CI's own identity var first (`config-deploy`'s
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
  Verified directly against v1's real `actor_controller.py::resolve_actor()`
  (ADR-0066/ADR-0067): its full chain has two steps *above* the two
  adopted here — a control-plane session (`IdentityController`, gated on
  a whole OIDC login/RBAC server v2 doesn't have) and the signed-in cloud
  CLI identity (az/aws/gcloud, whichever is configured — checked in that
  fixed order). The cloud-CLI step is a **future enhancement, not a gap**:
  `config-deploy` runs on Azure (every provider declares
  `authentication.method: managed_identity`) but authenticates through
  Azure Pipelines' own service connection, not a strata-owned `azure_cli`
  integration — v2 has no `azure_cli`/`aws_cli`/`gcloud_cli` integrations
  at all yet, and no real consumer configures the identity-provider/
  control-plane layer either. Revisit this step only once v2 grows its
  own cloud-CLI integrations for other reasons (e.g. deployer auth); it
  slots into the same precedence chain above the CI-env-var step without
  changing anything already decided here.
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
  raw errors, `label_safe` cardinality list, deploy/destroy-only scope —
  a local durable append series was considered but dropped, see "A third
  file"). Still open: whether non-outcome event types (`policy.violated`,
  etc.) have a manifest to render from at all, and how future satellite
  kinds (sbom already modeled; SBOM scan results, cost/drift snapshots,
  etc.) register as new reference fields.
- **Configuration surface streamlined 2026-09-30, then simplified further
  2026-09-30 — see "Streamlining the configuration surface" above.**
  `spec.audit` is fully optional end-to-end. `path` is dropped outright
  (`.strata/audit` is a fixed `layout.py` location, not a config field, same
  tier as `REMOTES_DIRNAME`). `structure` is dropped too — no Jinja2
  field at all — derived instead from a deployment's own
  `layers.follows`/`layers.segments` (already validated against
  `spec.paths` by `semantic_checks.py`) when present, falling back to a
  fixed `workspace/deployment` otherwise. `journal` is deferred out of
  this pass entirely (no evidenced consumer, same discipline already
  applied to `Capability.AUDIT`). `policy` is resolved too — see "Event
  admission — a closed set gate, not a policy framework": no framework,
  no nested model, no word "policy" at all — a flat `event_overrides:
  dict[str, bool] | None`, validated against a closed set scoped to only
  the three events v2 actually produces (`deployment.completed`/
  `destroyed`/`measured`), not v1's full 20-type list.
- **`DeploymentManifestModel.policy_results` population — ~~IMPLEMENTED
  (2026-10-07)~~.** Raised from
  [cve-scanner-integration.md](cve-scanner-integration.md)'s own
  "Remaining Work" tracking (`cve_policy`'s own evaluation result is the
  first real, concrete case needing this) — confirmed by direct grep:
  `ManifestPolicyResultModel` is instantiated nowhere in `src/` today,
  for any policy type.

  **The real architectural wrinkle, found by reading
  `finalize_and_distribute_deploy_audit()` directly, not assumed:**
  `DeploymentManifestModel` is constructed in exactly one place, at the
  end of a `deploy run` invocation. Every policy that exists today
  evaluates somewhere else entirely — `cve_policy` inline in `build
  run` (`sbom_controller.evaluate_cve_policy()`), `tenant_zone`/
  `path_convention` during `strata validate`
  (`semantic_checks.py`). These are three **separate CLI invocations**,
  not one atomic call — a policy's `Diagnostics` result does not
  naturally survive from the command that produced it to the later
  command that builds the manifest. Naively calling
  `evaluate_cve_policy()` a second time from inside
  `finalize_and_distribute_deploy_audit()` would re-run the scan
  pointlessly (and need the same `index`/`workspace` already resolved
  once); it also still wouldn't reach `tenant_zone`/`path_convention` at
  all, since those run during `validate`, a command with no necessary
  temporal relationship to any one later `deploy run` invocation.

  **The precedent already established, reused exactly, not reinvented:**
  `build_path` is already the real hand-off artifact directory between
  `build run` and `deploy run` — `resolved.yaml` and `sbom.json` are both
  written once by `build run`, then re-read and re-hashed by
  `finalize_and_distribute_deploy_audit()`'s own `_platform_reference()`/
  `_sbom_reference()` (both already optional — a missing file is fine,
  no diagnostic, matching `_sbom_reference()`'s own stated reasoning).
  **Decision: policy results follow the identical pattern** — a new,
  optional `build_path/policy_results.json` sidecar, written by `build
  run` itself right after each inline policy evaluator runs, read back
  by a new `_policy_results_reference(build_path)` helper in
  `audit_run.py` modeled 1:1 on `_sbom_reference()`'s own shape.

  **Scope, decided explicitly, not implied:** Phase 1 covers **only
  build-phase policies** (`cve_policy` today; any future build-phase
  policy the same way) — the one case this precedent cleanly solves.
  **Validate-phase policies (`tenant_zone`/`path_convention`) are
  explicitly out of scope for this pass** — `strata validate` has no
  `build_path` concept to write a sidecar into, and critically, is not
  necessarily run immediately before any one specific `deploy run`
  invocation at all (a solution can be validated once, deployed many
  times later; the two commands have no enforced 1:1 temporal
  relationship the way `build run` → `deploy run` does via a shared
  `build_path`). Wiring those in is a genuinely harder, separate
  problem — tracked here as still open, not solved by this design.

  **Concrete shape — no new model needed:** reuse
  `ManifestPolicyResultModel` exactly as it already exists
  (`policy_name`/`policy_type`/`phase`/`enforcement`/`passed`/
  `violations`); the sidecar is simply `{"policy_results":
  [ManifestPolicyResultModel, ...]}`. `build_controller.build_run()`
  collects a `list[ManifestPolicyResultModel]` across every inline
  policy evaluator it calls (today: one entry, from `cve_policy`, when
  `workspace.spec.cve_policy` is set — `None`/unset produces no entry,
  matching every other "nothing declared, nothing recorded" convention
  already used throughout this feature), and writes the file once at
  the end of the run — skipped entirely when the list is empty, the
  same "optional, omit rather than write an empty artifact" convention
  `write_sbom()`'s own SBOM-is-optional behavior already established.

  A small new helper builds each entry from a policy's own `Diagnostics`
  result — e.g. `_cve_policy_result(workspace, diagnostics) ->
  ManifestPolicyResultModel | None` in `build_controller.py` (not
  `sbom_controller.py` — `evaluate_cve_policy()`'s own signature/
  behavior stays completely unchanged, matching the established
  "policy function reports `Diagnostics` only, the orchestrator decides
  what to do with it" separation of concerns `build_run()` already
  uses for every other inline check):
  - `passed = diagnostics.ok` — strictly "no errors," matching
    `Diagnostics.ok`'s own existing semantics unchanged. An
    `enforcement: warn` breach still reports `passed=True` here — the
    build itself wasn't blocked — but see the next point for why the
    finding itself isn't lost.
  - `violations` folds in **both** error and warning messages, not
    errors only — an `enforcement: warn` breach is still a real,
    substantive finding worth an auditable record, even though it
    didn't fail the build; recording only `passed=True` with an empty
    `violations` list would silently discard exactly the information a
    "warn, don't block, but still tell someone" policy exists to
    surface in the first place.
  - **A real, open naming gap, surfaced by this design, not
    glossed over:** `ManifestPolicyResultModel.policy_name` is
    documented as "policy name as declared in configuration" — a
    concept that assumes v1's own `policies: [{name: ..., type:
    ...}]` named-list shape. v2's `cve_policy` (and `tenant_zone`) are
    **unnamed**, single, bespoke fields — there is no operator-chosen
    name to record at all (unlike `path_convention`, which **is** a
    named list, `PathConventionModel.name`, and so has a real name to
    use here whenever its own turn comes). **Decision: fall back to the
    field name itself** (`policy_name="cve_policy"`) whenever no
    operator-assigned name exists — an honest, if slightly awkward,
    consequence of the "small dedicated field, not a generic `policies:`
    list" decision [cve-scanner-integration.md](
    cve-scanner-integration.md)'s own Design section already made for
    `cve_policy` specifically; not a reason to revisit that decision now.

  **A real call-site wrinkle this design must not gloss over:**
  `build_run()`'s current call site immediately discards
  `evaluate_cve_policy()`'s own return value into the shared,
  function-wide `diagnostics` accumulator
  (`diagnostics.extend(evaluate_cve_policy(...))`) — once merged, there
  is no way to tell which items in `diagnostics` came from `cve_policy`
  specifically versus everything else `build_run()` already
  accumulated (SBOM warnings, provisioner errors, ...). `_cve_policy_result()`
  needs `evaluate_cve_policy()`'s own, *unmerged* `Diagnostics` object,
  so the call site must capture it in its own local variable first,
  then extend the shared one — a small, real, two-line reordering, not
  just "call a new function," which is why it's spelled out here rather
  than left implicit:

  ```python
  # build_controller.py, inside build_run(), replacing the existing
  # single line `diagnostics.extend(evaluate_cve_policy(build_path, graph.workspace, index))`:
  cve_diagnostics = evaluate_cve_policy(build_path, graph.workspace, index)
  diagnostics.extend(cve_diagnostics)
  policy_result = _cve_policy_result(graph.workspace, cve_diagnostics)
  if policy_result is not None:
      write_policy_results(build_path, [policy_result])


  def _cve_policy_result(workspace: WorkspaceModel, diagnostics: Diagnostics) -> ManifestPolicyResultModel | None:
      policy = workspace.spec.cve_policy
      if policy is None:
          return None
      return ManifestPolicyResultModel(
          policy_name="cve_policy",
          policy_type="cve_max_severity",
          phase="build",
          enforcement=policy.enforcement,
          passed=diagnostics.ok,
          violations=[item.message for item in diagnostics.items],
      )
  ```

  No filtering by `code` is needed in `violations` — `diagnostics` here
  is already `evaluate_cve_policy()`'s own, unmerged result (every item
  in it genuinely came from this one policy), unlike the shared
  `build_run()` accumulator this is deliberately kept separate from.

  **The write/read pair, in full — a new, small, shared module** (not
  folded into `build_controller.py`/`audit_run.py` directly: the write
  half is called from `build run`, the read half from `deploy run`'s
  audit finalize step — two different commands, so the shared file-format
  contract between them deserves one file neither owns outright):

  ```python
  # strata/controllers/policy_results.py
  """Policy evaluation results sidecar (`build_path/policy_results.json`)
  — docs/work/audit-trail.md's "DeploymentManifestModel.policy_results
  population" design. The one shared file-format contract between
  `build run` (writes) and `deploy run`'s audit finalize step (reads) —
  matches the existing `resolved.yaml`/`sbom.json` hand-off precedent.
  """

  import json
  from pathlib import Path

  from strata.models.audit_manifest_model import ManifestPolicyResultModel

  POLICY_RESULTS_FILENAME = "policy_results.json"


  def write_policy_results(build_path: Path, results: list[ManifestPolicyResultModel]) -> None:
      """Write `build_path/policy_results.json`, or do nothing when `results`
      is empty — optional artifact, matching `write_sbom()`'s own "nothing
      relevant, no file" precedent."""
      if not results:
          return
      path = build_path / POLICY_RESULTS_FILENAME
      payload = {"policy_results": [r.model_dump(mode="json") for r in results]}
      path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


  def read_policy_results(build_path: Path) -> list[ManifestPolicyResultModel] | None:
      """Read `build_path/policy_results.json` back, or `None` when it
      doesn't exist — same optional, no-diagnostic shape as
      `audit_run.py`'s own `_sbom_reference()`; a build with no configured
      build-phase policy writes none, and that must not be an error here."""
      path = build_path / POLICY_RESULTS_FILENAME
      if not path.exists():
          return None
      payload = json.loads(path.read_text(encoding="utf-8"))
      return [ManifestPolicyResultModel.model_validate(item) for item in payload["policy_results"]]
  ```

  `audit_run.py` then just calls `read_policy_results(build_path)`
  directly and wires its result into
  `DeploymentManifestModel(..., policy_results=...)` — no separate
  `_policy_results_reference()` wrapper needed after all, since
  `read_policy_results()` already has exactly the right optional
  signature; the earlier sketch of a second, audit_run-local wrapper
  function was unnecessary indirection, corrected here.

  **Implementation plan:**
  1. `audit_manifest_model.py`: no change — `ManifestPolicyResultModel`
     already has the right shape.
  2. New `strata/controllers/policy_results.py`: `write_policy_results()`/
     `read_policy_results()`, exactly as sketched above.
  3. `build_controller.py`: add `_cve_policy_result()`; restructure the
     `evaluate_cve_policy()` call site to capture its own `Diagnostics`
     before merging (the two-line reordering above); call
     `write_policy_results(build_path, [policy_result])` when non-`None`.
  4. `audit_run.py`: import `read_policy_results` from the new module;
     call it once, pass the result straight into
     `DeploymentManifestModel(..., policy_results=...)`.
  5. Tests, named explicitly (not just scenarios):
     - `test_build_run_writes_policy_results_when_cve_policy_breaches`
       — `enforcement: deny` breach produces a `policy_results.json`
       with one entry, `passed=False`, non-empty `violations`.
     - `test_build_run_records_a_warn_enforcement_breach_as_passed_with_violations`
       — `enforcement: warn` still writes `passed=True` but a non-empty
       `violations` (the "don't silently discard a warn-mode finding"
       decision above, specifically exercised).
     - `test_build_run_writes_no_policy_results_file_when_cve_policy_unset`
       — the common case; no file at all, not an empty one.
     - `test_write_policy_results_skips_writing_when_results_is_empty`
       — unit test on the new module directly.
     - `test_read_policy_results_round_trips_a_written_file` and
       `test_read_policy_results_is_none_when_file_is_missing` — the
       read half, in isolation.
     - `test_finalize_and_distribute_deploy_audit_includes_policy_results_in_the_manifest`
       — the real end-to-end case this whole design exists to serve:
       a prior `build run`'s `policy_results.json` ends up in the
       `deploy run`-produced `_manifest.json`.
  6. Full check suite clean before considering this done, same
     discipline as every other feature in this repo.

  **Shipped as designed (2026-10-07), all 6 steps above, exactly as
  sketched — no changes needed during implementation.** New
  `src/strata/controllers/policy_results.py` (`write_policy_results()`/
  `read_policy_results()`); `build_controller.py` gained
  `_cve_policy_result()` and the two-line call-site reordering;
  `audit_run.py` wires `policy_results=read_policy_results(build_path)`
  into its one `DeploymentManifestModel(...)` construction. 7 new tests
  (not 6 — the design's own step 5 bullet list names two tests in one
  bullet): 3 in `test_build_controller.py`
  (`test_build_run_writes_policy_results_when_cve_policy_breaches`,
  `test_build_run_records_a_warn_enforcement_breach_as_passed_with_violations`,
  `test_build_run_writes_no_policy_results_file_when_cve_policy_unset`),
  3 in new `test_policy_results.py`
  (`test_write_policy_results_skips_writing_when_results_is_empty`,
  `test_read_policy_results_round_trips_a_written_file`,
  `test_read_policy_results_is_none_when_file_is_missing`), 1 in
  `test_audit_run.py`
  (`test_finalize_and_distribute_deploy_audit_includes_policy_results_in_the_manifest`).
  Full check suite green: mypy (148 files), ruff, ruff format,
  import-linter (1 kept, 0 broken), pytest (2249 passed, up from 2242).

## Compliance Gap Analysis (NIS2 / ISO 27001 / ISAE 3402) — 2026-10-03

Prompted by a direct question ("PR extraction — useful? is there
justification for this?") that led to checking, field by field, what the
four compliance questions this whole doc opens with ("What changed", "Why",
"Who approved", "How applied") actually have *populated* behind them today
— not what's modeled, what's real. Checked directly against
`DeploymentManifestModel`'s only construction site
(`controllers/audit_run.py::finalize_and_distribute_deploy_audit()`), not
assumed from the model's field list.

### What's actually populated today, per question

| Question                    | Modeled                                                                                           | Populated today                                                                                                                                                                                                                                                                                                                                                                                                                                                  | Evidence                                                                                                                        |
| --------------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **Q1 — What changed**       | `artifacts.{platform,repositories,images,providers}`, `sbom`                                      | **Partial.** `artifacts.platform` and `sbom` real (hash+path); `repositories`/`images`/`providers` have **zero construction call sites anywhere in `src/`** — modeled, never built. Full before/after value diffing is Layer 3's `audit diff`, not built.                                                                                                                                                                                                        | Grepped `ManifestRepositoryModel(`/`ManifestImageModel(`/`ManifestProviderModel(` — each matches only its own class definition. |
| **Q2 — Why it changed**     | `ChangeReferenceModel` (`reason`/`classification`/ticket `id`/`system`/`url`)                     | **None.** Grepping the whole codebase for `change_reference` finds exactly one hit beyond the field definition itself — nothing ever constructs one. No CLI flag threads a ticket reference into `deploy run`. The PR template's own "business justification" text is real and human-populated, but lives only in GitHub, never linked into the manifest.                                                                                                        | `grep_search` for `change_reference` across `src/strata/**`.                                                                    |
| **Q3 — Who approved**       | `ManifestPullRequestModel.approvers`                                                              | **None.** Confirmed by reading v1's real `enrich_with_pr_data()` directly: its `gh pr list --json` call requests `author`/`mergedBy`/`mergedAt`/`labels`/`files` — never `reviews`/`reviewDecision` — so even v1's own "finished" implementation never populated this field. v2 hasn't built the extraction step at all yet. `deployed_by` (real, populated) answers *who ran the deploy*, a different question from *who approved it*.                          | Read `e:\SourcesXYZ\strata\src\strata\controllers\audit_controller.py`'s real `enrich_with_pr_data()` body directly.            |
| **Q4 — How it was applied** | `action`, `version`, timestamps, `commit_sha`/`commit_message`/`commit_author`, `force`/`dry_run` | **Mostly real.** `action`/`version`/`started_at`/`completed_at`/`duration_seconds` are genuinely populated. `commit_sha`/`commit_message`/`commit_author` have **zero construction call sites** despite `git rev-parse HEAD` already being a used pattern elsewhere in this codebase (`integrations/gitops.py`, `controllers/audit_push.py`) for an unrelated purpose. `force` isn't even threaded through `finalize_and_distribute_deploy_audit()`'s signature. | Read `audit_run.py`'s only `DeploymentManifestModel(...)` construction site directly.                                           |

**Net:** only Q4's timing/version fields and the artifact-hash half of Q1
had real, populated answers as of this table's own date (2026-10-03).

**Update (2026-10-05) — Q2 and part of Q3 are no longer unpopulated.**
This table is a historical snapshot, kept as-is rather than rewritten —
what's changed since:

- **Q2 ("why")**: `change_reference` CLI wiring shipped the same day this
  table was written up as a gap — six `deploy run --change-*` flags,
  opt-in (nothing forces a deploy to supply one). See the Should-have
  list's `change_reference` entry.
- **Q3 ("who approved")**: still **not** solved by PR-approver extraction
  (that remains exactly as this table found it — v1 never requested
  `reviews`/`reviewDecision`, v2 never built the extraction step, and it's
  a Could-have, not pursued). Instead, `ChangeReferenceModel` grew
  `approved_by`/`approved_at` (2026-10-05) — operator-supplied via
  `deploy run --change-approved-by`/`--change-approved-at`, distinct from
  `supplied_by` (whoever typed the reference) and `deployed_by` (whoever
  ran the deploy). **Honest limit, stated directly:** this is
  operator-attested, not independently verified against the tracker —
  strictly better than conflating approver with executor, but not the
  same as a real tracker-integration lookup (not built; no real
  consumer's tracker integration exists yet to build it against). See
  `audit_manifest_model.py`'s own docstring for the full reasoning.
have real, populated answers today. Q2 and Q3 are fully unanswered — not
degraded-gracefully like a best-effort PR lookup would be, just never wired
at all.

### Reading this against NIS2 / ISO 27001 (A.12.1.2, A.12.4) / ISAE 3402 Type II

The three frameworks converge on the same three asks, in the order an
auditor actually tests them:

1. **Accountability** — NIS2 Art. 20 holds management bodies accountable;
   ISO 27001 A.12.1.2 requires authorization *before* implementation. The
   first question is never "show me one record," it's "show me who could
   change this, and prove someone with authority signed off."
2. **Completeness over a period, not a sample of one** — ISAE 3402 Type II
   tests *operating effectiveness over time*. A single well-formed
   manifest proves nothing about *consistency* if there's no way to
   enumerate every change in a period — that's Layer 3, not built.
3. **Non-repudiation / tamper resistance** — already the explicit reason
   Layer 4 exists (git is mutable by a repo admin). Solved
   ([audit-sink-dispatch.md](audit-sink-dispatch.md)).

### MoSCoW

**Must have**

- **Implemented in full (2026-10-05)** — see
  [audit-commands.md](audit-commands.md)'s Phases 1-7, all complete:
  `strata audit status` reads the configured `git` sink's latest record
  per deployment; `strata audit changes` lists every record in a
  `--since`/`--until`/`--status`-filtered range, oldest to newest — the
  ISAE 3402 "operating effectiveness over a period" ask this Must-have
  existed for. Both fail fast, before any network access, if no (or more
  than one) readable sink is configured. This is the one item this
  entire MoSCoW ever marked Must-have, and it is now the only one fully
  closed out — every other open item below is Should-have or lower.

**Should have**

- **`change_reference` CLI wiring** — **implemented (2026-10-05)**. Six
  flags on `deploy run` (`--change-system`/`--change-id`/`--change-reason`/
  `--change-classification`/`--change-title`/`--change-url`), one per
  `ChangeReferenceModel` field — not a single packed `--change-ticket
  SYSTEM:ID`, matching this codebase's existing one-flag-per-field style.
  `system`/`id`/`reason` (the model's required fields) must be supplied
  together or not at all — a partial combo is a `UsageError` raised in
  `deploy_command.py` **before** `deploy_run()` executes, so a malformed
  flag combination never lets real infrastructure work happen first.
  `supplied_by`/`supplied_at` are never flags — auto-derived in
  `audit_run.py` (`resolve_actor()`/the run's own clock), the same pattern
  `deployed_by`/`started_at` already use. Matches the ADO-gate finding
  below: both `--change-reason` and `--change-id` end up fed from
  queue-time pipeline parameters (not from the approval event itself —
  see the 2026-10-05 correction in the addendum) — no further plumbing
  needed to bridge the two designs once this shipped.
  New tests in `test_audit_run.py` (4 cases: absent by default, populated
  when all three required fields given, `supplied_by`/`supplied_at`
  presence without asserting environment-dependent literals) and
  `test_commands_deploy.py` (2 cases: full flow via the real CLI reading
  the written manifest, partial combo exits 2 before `deploy_run()` is
  ever called). Full check suite green: mypy (136 files), ruff, ruff
  format, import-linter 1/0, pytest 1879 passed.
- **`audit diff`** (full before/after value diffing) — valuable for
  incident root-cause and change-detail review; commit SHA already gives
  a weaker "which version" substitute, so this is an enhancement, not a
  blocker.
- **`force` flag captured in the manifest** — cheap, and a deploy that
  bypassed normal gates is exactly what a change-management auditor
  flags, but low volume relative to the Must-haves.
- **An ADO Environment approval gate feeding `ChangeReferenceModel`** —
  **promoted from Could-have (2026-10-04)**: pipeline development is
  actively in progress, and an ADO approval step will be created once
  this stack reaches production — a confirmed, concrete, planned need,
  not a speculative one (same evidence bar the OTel/Sentinel sinks were
  held to in [audit-sink-dispatch.md](audit-sink-dispatch.md)). See
  "Addendum — formalizing approval via an ADO gate" below for the
  right-sized shape (populate the existing `ChangeReferenceModel`, don't
  build a new document kind or re-implement enforcement).

**Could have**

- **Commit identity** (`commit_sha`/`commit_author`/`commit_message`) +
  **PR number recovered from `commit_message`** — **demoted from
  Must-have (2026-10-05)**, see "M1 re-prioritized to Could-have" below.
  Design remains fully worked out and ready; just no longer the next
  thing to build.
- **Full `gh`-based PR *approver* extraction** (fixing v1's own
  never-populated `approvers`) — only worth it once a real pipeline
  actually has `gh` available (confirmed not true for `config-deploy`
  today: zero evidence of `gh` CLI/`GITHUB_TOKEN` anywhere in its real
  Azure Pipelines YAML). The commit-message PR-number recovery above
  covers most of the compliance value at none of this cost.
- **`artifacts.repositories`/`images`/`providers` full population** —
  completeness nice-to-have; the platform+SBOM hash already anchors "what
  was deployed" for audit purposes.
- **A v1-style global policy gate** layered over the per-sink `events`
  filter — noise/volume control, not a compliance gap; per-sink filtering
  already exists.

**Resolved — no longer deferred (2026-10-05)**

- **Layer 3 reporting** (`audit status`/`audit changes`) — moved out of
  this category entirely: `audit status` is implemented (see the
  Must-have entry above), so this is no longer "not validly designed
  yet," the reasoning that put it here. `audit changes` remains
  unbuilt, tracked as a scope cut in
  [audit-commands.md](audit-commands.md), not as a design gap.

**Won't have (now)**

- **Journal** (Layer 3b, `.strata/audit.log`) — v1's own measured defect
  (95% noise, zero deploy events), no justification then or now.
- **Full OIDC/cloud-CLI identity chain** for `deployed_by` — needs
  infrastructure (login server, cloud-CLI integrations) that doesn't
  exist and no real consumer configures yet, per `actor.py`'s own
  documented scope cut.
- **Porting v1's `DeploymentGateModel`/`mode: "enforce"` work-item
  pause-and-resume mechanism** — duplicates what an ADO Environment
  approval gate already does natively; see addendum below.

### M1 re-prioritized to Could-have (2026-10-05)

Asked directly for a short status check on M1, which surfaced the
question plainly: "seems like something we skip?" Correct call, for
reasons that didn't exist when M1 was first proposed:

1. **Its value shrank once honestly scoped.** The previous section
   ("What `commit_sha` can and can't claim") already walked this back to
   a reproducibility anchor ("exact repo state used"), not an
   authorship/"who caused this" claim — a more modest result than first
   framed.
2. **No concrete demand, unlike everything else promoted this session.**
   OTel/Sentinel ([audit-sink-dispatch.md](audit-sink-dispatch.md)) and
   the ADO gate below were promoted specifically because of a named real
   signal ("our ELK stack," "pipeline development is in progress"). M1
   was chosen because it was *cheap* (reuses an existing `git rev-parse`
   pattern), not because any real consumer asked for it — a weaker
   justification than this project's own evidence bar normally requires.
3. **The free PR-number recovery is shakier than it first looked.** Given
   deploys are manually queued with nothing pinning a ref/commit at queue
   time (established earlier in this doc), HEAD is just as likely to be
   an unrelated commit as a real merge commit — the same "typo fix"
   problem that undermined the authorship claim also undermines the
   regex's hit rate.

**Decision:** demoted to Could-have. Design stays fully worked out and
ready to implement later — nothing here invalidates the design itself,
only its priority. Layer 3 (`audit status`/`audit changes`) was proposed
as the next Must-have in its place — **since deferred, not built; see
"Layer 3 deferred" immediately below.**

### Layer 3 deferred — local-file reporting is wrong for the real case (2026-10-05)

A full design for `audit status`/`audit changes` was produced (new
`strata audit` command group, `controllers/audit_report.py`, both
commands reading local `_manifest.json` files under
`layout.audit_dir(root)`). Challenged directly before any code was
written: *"no data will be local. all the data will be remote."*
Correct, and this doc had already established exactly why, earlier,
for a different piece — the `deployments.ndjson` removal recorded above
in "Walked the full end-to-end loop": *"90 percent deployed in CI/CD so
will be not saved"* — a local-only file in an ephemeral CI runner gets
written once, then the runner (and the file with it) is destroyed. The
exact same reasoning applies here: a command that walks local
`.strata/audit/` would find real data only for the minority of runs that
happen to execute somewhere persistent (a developer's own machine), and
nothing at all for the ~90% real case — the command would *look* like it
answers "is my audit trail working," and answer wrong, silently, for most
real invocations. Shipping that would be worse than not shipping anything
— a false negative dressed up as a working command.

**What a correct version needs instead:** read from wherever the data
actually survives — the `git` sink's pushed remote destination (the one
real, durable mechanism already confirmed load-bearing for
`cfg-int-deployment`), not the local filesystem. That is a materially
bigger design than "walk a directory": it needs a fetch/checkout step
(plausibly reusing `audit_push_checkout_path()`'s existing layout
convention in reverse, as a pull instead of a push), a decision about
which sink to read from when several are configured, and handling for
the sink being `git`-only vs. `integration`-only (an `integration`-only
solution has no git destination to read back from at all — Layer 4's
SIEM sinks are pure fire-and-forget, not queryable by strata itself).

**Decision (2026-10-05):** deferred, not demoted to Could-have — this was
not "lower priority," it was "not validly designed yet." **Update,
same day:** a remote-read design was produced, then **implemented in
full** — see [audit-commands.md](audit-commands.md) for the full design
and its Phases 1-7, all complete (a dedicated `audit-read/` checkout
namespace, glob-and-parse manifest discovery rather than reconstructing
the write path, both the `status` and `changes` commands, failure
classification) — `strata audit status` and `strata audit changes` are
both real, working commands as of 2026-10-05. Spun into its own doc
rather than grown here further, the same way audit-sink-dispatch.md was
split out once its own design got detailed enough. This section stays as
the record of *why* the first (local-file) attempt was wrong, which the
new doc's own Overview links back to rather than re-deriving.

### What `commit_sha` can and can't claim (2026-10-04)

Pressure-tested M1 directly: "what commit would we be looking at — the
last commit could also just say 'fixed typo in workspace description'."
Correct, and worth recording precisely rather than letting the earlier
worked example's framing stand uncorrected.

**The problem.** `git log -1` on `context.root` returns whichever commit
is HEAD on whatever's checked out at that moment — nothing more. Given
the confirmed reality one addendum up (every real pipeline is `trigger:
none`/`pr: none`, manually queued, no ref/commit pinned at queue time),
that HEAD commit can be **completely unrelated** to whatever motivated
this deploy: a typo fix, a docs change, a different stack's commit in the
same monorepo, landed by someone else, at any point before the queue
action. The earlier worked example (two sections up) implicitly treated
`commit_author` as "who wrote the infrastructure change" — that framing
is wrong and is corrected here, not silently fixed.

**What the field is honestly worth.** A reproducibility anchor: *"here is
the exact repo state this deployment used — `git show <sha>` to see
precisely what was checked out."* Genuinely valuable for incident
root-cause (NIS2) even when the commit itself turns out to be unrelated
to intent. **Not** a claim about who authored the meaningful change
behind the deploy — that is a different question this field does not
answer.

**Considered and rejected: scoping the query to the deployment's own
file** (`git log -1 -- <deployment.file>`, a field the manifest already
tracks, also currently unpopulated). Better signal for the common case
(someone edits the deployment YAML directly), but trades one blind spot
for another — it misses a real, relevant change landed in a *different*
file this deployment references (a module version bump in a separate
file, a workspace-level change). Neither scope (whole-repo vs.
single-file) is actually correct; **there is no git-log-based fix for
this.** The only mechanism that genuinely answers "what changed" is
Layer 3's `audit diff` (diffing two manifests' actual resolved content),
already a Should-have — M1 cannot and should not try to substitute for
it.

**Decision:** ship M1 as originally scoped (unscoped `git log -1`), with
the claim stated honestly in code/docstrings as a reproducibility anchor,
not an authorship claim. No file-scoping added. One unplanned upside
worth keeping in mind: once this ships, a manifest showing
`commit_message: "Fixed typo in workspace description"` next to a real
infra-affecting deploy is itself a visible, honest signal to a human
reader that "the commit trail doesn't explain this deploy" — not a
resolution, but better than the current silence.

### Addendum — formalizing approval via an ADO gate (2026-10-04)

Prompted by a direct proposal: OMP has a Change Advisory Board; could an
Azure DevOps Environment approval gate stand in for it, with a script
turning the approval event into a document pushed to the repo/manifest/
SIEM? Investigated rather than assumed, including "did v1 already have a
workflow/gate system" (yes).

**v1 already designed this, unused.** `models/gate_model.py` (ADR-0057/
ADR-0059) has `DeploymentGateModel` with `type: approval | cost_review |
security_review | verify | scheduled | incident | cab` — `cab` is a
literal gate type — and two modes: `enforce` (strata itself creates a
`WorkItem`, pauses the deploy, exits code 5, requires `--resume`) and
**`declare`**, whose own field description says, verbatim: *"strata only
records this gate in the audit trail; it never blocks. Use 'declare' when
enforcement already happens externally (e.g. Azure DevOps environment
approvals, GitHub Actions protection rules)."* v1's authors had already
reasoned through exactly this proposal.

**Real-usage check, same discipline as every other feature in this doc:**
grepped every real YAML in `cfg-int-deployment` for `gates:`/`type:
cab`/`type: approval` — one hit, a different and unrelated field
(`promotions.yaml`'s rollout-ordering `gates:`, itself commented out,
"not adopted yet"). **Zero evidence this system is used anywhere.** Same
"declared but unread machinery" pattern as the SIEM sinks, the journal,
and PR extraction — a third instance of it.

**Update (2026-10-04) — this is a real, planned need, not a dead idea.**
Two things changed the read: (1) the likely reason v1's gate system went
unused isn't lack of value — it wasn't obvious *how* to use it (a
discoverability/UX problem with a 7-type, 2-mode, condition-expression
configuration surface, not evidence nobody needed approval gating); (2)
pipeline development for this stack is actively in progress, and an ADO
approval step **will** be created once it reaches production — a
confirmed, concrete, near-term consumer, the same evidence bar that
promoted OTel/Sentinel in
[audit-sink-dispatch.md](audit-sink-dispatch.md) from hypothetical to
built. Promoted from Could-have to Should-have in the MoSCoW above. The
discoverability lesson matters for the right-sized version too: whatever
ships here should be small and obvious (populate one existing field from
one REST call) rather than v1's broad, generalized gate-configuration
surface — a plausible reason *that* went unused in the first place.

**If this is ever built, the right-sized version is narrower than the
original proposal:**

- **No new `kind: changerequest/approval` document.** `ChangeReferenceModel`
  (`system`/`id`/`reason`/`classification`/`supplied_by`/`supplied_at`)
  already exists on the manifest, confirmed unpopulated in the Compliance
  Gap Analysis above — the ADO approval event is data to populate a field
  that already has a home, not reason to invent a new document kind with
  its own discovery/reference-wiring/lifecycle.
- **No porting v1's `mode: "enforce"` work-item pause-and-resume
  mechanism.** It duplicates a stop-and-wait control ADO's own Environment
  approval gate already provides natively. v1's own `declare` mode — record,
  don't re-implement — is the right instinct and avoids reviving a
  substantial (388-line `gate_controller.py`) but never-production-exercised
  mechanism.
- **"Push to SIEM" needs no new step.** Once `change_reference` is
  populated, it rides inside the manifest, which Layer 2 (git) and Layer 4
  (webhook/OTel/Sentinel) already push everywhere automatically.
- The actual shape: a small script/step run after an ADO Environment
  approval passes, calling ADO's REST API for the approval's
  identity/timestamp/comment, feeding a populated `ChangeReferenceModel`
  into `deploy run` (new flag, or a pre-supplied file) — not a new
  enforcement engine.

**Verified directly against Microsoft's real docs (2026-10-05) — what the
approval step can and can't supply.** Asked plainly: can an ADO approval
gate request parameters, like a ticket number? Checked the conceptual
docs and the real `ApprovalUpdateParameters` REST schema rather than
assuming. The answer is no, and it splits `ChangeReferenceModel` cleanly
in two:

- **`reason` → the approval's `comment` field.** `ApprovalUpdateParameters`
  (what an approver actually submits: `approvalId`/`status`/`comment`/
  `reassignTo`) has exactly one free-text field, filled in at the moment
  of approve/reject. Clean 1:1 mapping, no parsing needed.
- **`id`/`system`/`classification` have no equivalent in the approval
  step at all.** The check's own `instructions` field is static (set once
  by the resource owner, identical for every run) — not a per-run
  parameter, and not approver-supplied either way. Recovering a
  structured ticket reference by parsing free text out of `comment`
  (e.g. expecting `"JIRA-1234: approved"`) would repeat the same
  fragile-regex-over-prose pattern D2 already rejected once (and the
  same honest limit already recorded against the PR-number regex above)
  — not the right mechanism for structured data.
- **The right mechanism for the structured half is a queue-time pipeline
  parameter, not the approval step.** `cfg-int-deployment`'s real
  pipelines already use YAML `parameters:` (confirmed: `stack`,
  `deploymentPath`, `dryRun`, ...) — a `changeTicket` parameter, typed by
  whoever *queues* the run, is a genuinely structured, already-proven ADO
  mechanism, unlike squeezing structure out of an approver's prose.
  `supplied_by`/`supplied_at` come from the approval event itself either
  way (`actualApprover`/`lastModifiedOn` on the real `ApprovalStep`
  schema), not from either input source.

**Correction (2026-10-05) — the `comment`-based plan above doesn't work;
verified two further real constraints that rule it out rather than just
complicate it.**

- **Only `jobs.deployment` has an `environment:` property — `jobs.job`
  has none at all.** Confirmed by reading the real YAML schema reference
  for both side by side: `jobs.job`'s full property list
  (`job`/`displayName`/`dependsOn`/`condition`/`continueOnError`/
  `timeoutInMinutes`/`variables`/`strategy`/`pool`/`container`/
  `services`/`workspace`/`uses`/`steps`/`templateContext`) has no
  `environment` field; `jobs.deployment` does, plus a different steps
  shape (`strategy.runOnce.deploy.steps`, not a flat `steps:` list).
  Environment approval checks can only gate deployment jobs. This makes
  the "most of the work is outside strata's own repo" reservation below
  concrete and larger than first scoped: adopting this gate means
  restructuring whichever job currently calls `strata deploy run` into a
  deployment job, a real pipeline-shape change in `cfg-int-deployment`,
  not just adding a check to an existing job.
- **There is no way for a script to correlate "the approval that just
  passed" back to that approval's own `comment`.** Checked both real
  candidate mechanisms and found neither exposes it: the documented
  Deployment job variables are only `Environment.Name`/`Environment.Id`/
  `Environment.ResourceName`/`Environment.ResourceId`/`Strategy.Name`/
  `Strategy.CycleName` — no approval ID. The Approvals Query REST API
  (`GET .../_apis/pipelines/approvals`) filters only by `approvalIds`
  (a list you'd already need to know), `state`, `userIds`, `top` — no
  run/build-ID filter exists. A step in the deployment job has no
  documented way to ask "which approval record gated *this* run" —
  the only fallback (`state=approved&top=1` sorted by recency) is a
  race under any concurrent runs targeting the same environment, not a
  real mechanism to build on.
- **Revised recommendation: drop the `comment`-sourced `reason` entirely.
  Make both `--change-id` and `--change-reason` queue-time pipeline
  parameters**, typed by whoever queues the run — the same proven
  mechanism, applied to both fields instead of split across two sources.
  This sidesteps the correlation gap completely rather than working
  around it. It also simplifies the gate's job down to what ADO actually
  reliably provides: a stop-and-wait control, not a data-entry form.
  `supplied_by`/`supplied_at` still auto-derive from the run itself
  (`resolve_actor()`/the run's own clock) exactly as already implemented
  for the CLI flags above — this correction only changes where `id` and
  `reason` come from, not the rest of the already-shipped wiring.

**Honest reservations, still worth recording even after the promotion:**

- **Most of the work is outside strata's own repo, and is now known to
  include a pipeline-shape change, not just a new check**: creating the
  ADO Environment, restructuring the calling job into a deployment job,
  and defining who's actually authorized to approve (a real
  CAB-equivalent group, agreed with real humans) are governance and
  pipeline-authoring decisions, not code strata can produce on its own —
  confirmed as planned, but not yet built on the ADO side either.
- **A technical ADO gate is not automatically an organizational CAB** —
  if an ISMS scope statement commits to a literal CAB with minutes/quorum,
  an automated approval click doesn't necessarily satisfy that; a question
  for whoever owns the ISMS scope, not something resolvable in this repo.

**Verdict:** promoted to Should-have (2026-10-04) — a confirmed, planned
consumer, not a speculative one. Still behind M1 (commit identity) as the
next concrete step, since M1 needs no external/operational buy-in and the
ADO-side infrastructure (the Environment, the named approvers, and now a
deployment-job restructure) isn't built yet either — but no longer gated
on "needs a named owner to materialize" the way the Could-have framing
implied; worth a proper design pass once the ADO approval step itself
exists to integrate against. Strata-side scope, once that exists, is now
smaller than first designed: both `id` and `reason` arrive as queue-time
parameters strata already knows how to accept (the six `--change-*`
flags, shipped 2026-10-05) — no REST-polling script to write at all.

## Changelog

- 2026-09-30: Created. Catalogs v1's post-ADR-0066 audit-trail design (three
  compliance layers, deploy-log schema, journal, policy gate + sinks,
  CloudEvents/ECS envelope, CLI surface) plus its own documented defects,
  as the evidence base for a future v2 audit-trail ADR. No v2 decisions
  made in this doc.
- 2026-09-30: Resolved Open Question #1, per request ("lets look at what
  config-deploy actually needs from v2"). Read the real, current
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
  CI identity env var — `BUILD_REQUESTEDFOR` for `config-deploy`'s
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
- 2026-09-30: Investigated v1's real `azure`/`aws`/`gcloud`/generic-OIDC
  identity system, per request ("lets look at the v1 auth model first").
  Found two distinct systems: `AuthenticationModel` (declarative
  per-integration credential shape — already ported to v2, unrelated to
  actor identity) and a much larger identity-provider/OIDC control-plane
  login system (ADR-0067, `IdentityController` + `server/auth/*` —
  sessions, RBAC, M2M) gated on a control-plane server v2 doesn't have.
  Confirmed via v1's real `actor_controller.py::resolve_actor()` that the
  already-adopted CI-env-var/OS-user chain is exactly v1's own bottom two
  precedence steps, not a gap — the two steps above it (control-plane
  session; signed-in cloud CLI identity) need infrastructure v2 lacks.
  Verified no real consumer need: `config-deploy` runs on Azure
  (`authentication.method: managed_identity` on every provider) but
  authenticates via Azure Pipelines' own service connection, not a
  strata-owned `azure_cli` integration, and configures no identity-
  provider/control-plane integration at all. Added a note to "Actor/
  identity" recording the cloud-CLI step as a future enhancement once v2
  grows its own `azure_cli`/`aws_cli`/`gcloud_cli` integrations for other
  reasons — not a blocker for Layer 2.
- 2026-09-30: Reviewed the "v2 Design Notes" section against real v2 code,
  per request ("lets look further into the design and see if it matches
  the v2 spirit/redesign"). Confirmed several sketches already match real
  precedent exactly (`RemoteReference()` on `AuditGitSinkTargetModel.remote`
  matches `common_models.py`'s real `SourceModel.remote`; new git-push
  logic belonging in `controllers/` over `utils.transport.run_command()`
  matches `remote_resolution.py`'s real shape; `ConfigurationSpecModel.audit`
  matches `security`/`paths`'s exact field style). Found and fixed four
  gaps in "The endpoints": (1) `AuditSinkModel.integration` was a bare
  `PlatformName`, not annotated `References(PlatformKind.INTEGRATION)` like
  every other "names an Integration" field in v2 (`artifact_model.py`,
  `provisioning_model.py`) — fixed. (2) No uniqueness validator was
  sketched for `sinks[].name`, unlike every other named list on
  `ConfigurationSpecModel` — added, matching `check_unique_names()`. (3)
  Adding `Capability.AUDIT` now was a real inconsistency with this doc's
  own already-settled "Layer scope" decision (the `integration` arm's
  dispatch is deferred) — the exact "declared-but-unread machinery"
  pattern this doc calls out elsewhere; deferred `Capability.AUDIT` to
  land together with that dispatch instead. (4) Considered, and kept,
  `events: list[str] | None` as a plain string list rather than an enum —
  unlike `Capability`, it has only one consulting site (the runtime policy
  gate), so an enum would add a sync burden with no added typo-safety.
- 2026-09-30: Began an implementation walkthrough ("lets go over the
  design step by step"), Step 1 (config models) — surfaced three fields
  that turned out not to be configuration at all, per the user's own
  challenge ("`path`... is fixed. `.strata` is our folder we choose in
  there what and where", plus questioning `structure`/`journal`/`policy`
  before any code was written. Resolved: (1) `path` dropped outright —
  `.strata/audit` becomes a fixed `layout.audit_dir(root)` location, same
  tier as `REMOTES_DIRNAME`, never a config field. (2) `structure`
  dropped too, replaced by deriving the per-execution directory from a
  deployment's own `layers.follows`/`layers.segments` when declared
  (already validated against `spec.paths` by `semantic_checks.py`) —
  found by checking `path_conventions.py` is match-only, so a Jinja
  `structure` field would have been a disconnected second way to
  describe a directory shape `spec.paths` may already declare once —
  falls back to a fixed `workspace/deployment` when no `layers` block
  exists. (3) `journal` deferred out of this pass entirely — checked
  against real usage and found no evidenced consumer (`config-deploy`
  runs it "always on, local, gitignored" but nothing reads or forwards
  it), the same "declared-but-unread machinery" caution already applied
  to `Capability.AUDIT`. (4) `policy`/event-overrides intentionally left
  unresolved — flagged by the user as "a big one" needing its own
  discussion (is this a policy framework, or a plain override table?) —
  to be picked up next, separately from this update. Updated
  "Streamlining the configuration surface", "Path resolution", "A third
  file", and the Remaining Work summary bullets to match.
- 2026-09-30: Resolved "policy" ("lets dig into the policies"), checked
  directly against v1's real `models/audit_config_model.py` rather than
  the earlier prose catalog. Verdict: a closed-set event gate with a
  validator, not the deny/warn/multi-type engine
  `PathConventionModel.enforcement` already declined to port — no
  framework needed first. Two simplifications from v1's real shape:
  dropped the `Union[bool, AuditEventPolicyModel]` object wrapper (its
  extra fields were v1's own "reserved, not read by any producer yet")
  for a flat `event_overrides: dict[str, bool] | None`; and scoped the
  closed set to only the three events v2 actually produces
  (`deployment.completed`/`destroyed`/`measured`), not v1's full 20-type
  list spanning workitem/lock/drift/cost/secret producers v2 doesn't have
  — named explicitly that `config-deploy`'s real overrides
  (`secret.accessed`/`policy.violated`/`workitem.rejected`) all fall
  outside this scoped set, expected given none of those producers exist
  in v2 yet. Also dropped the word "policy" itself — no field or model
  is named that. Added "Event admission — a closed set gate, not a policy
  framework"; updated the sink code comment, its `events` YAML example,
  and "Example configuration"'s `policy:` block (now `event_overrides:`)
  to match.
- 2026-09-30: Walked the full end-to-end loop with a worked example
  ("give the full design example loop again"), which surfaced two real
  gaps rather than just confirming the design. (1) **Dropped the durable
  local append series (`deployments.ndjson`) entirely**, per direct
  challenge ("90 percent deployed in CI/CD so will be not saved") — a
  local-only file in an ephemeral CI runner gets one line appended then
  destroyed, never accumulating where most real runs happen; the real fix
  (the `git` sink also appending to a *remote*-tracked series) is real
  work belonging to a future `strata metrics` design, not bundled in here
  speculatively. Updated "A third file", the "Net result" paragraph, and
  the Remaining Work summary bullet to match. (2) **Added `AuditSinkModel.
  required: bool = False`** for dispatch-failure semantics, per direct
  challenge that "fail silently or failure" was a false binary — reused
  `Integration.required`'s existing name/default rather than inventing new
  vocabulary, and `Diagnostics`' existing `Severity.WARNING`/`ERROR` tiers
  rather than a new mechanism: a dispatch failure always warns (visible,
  never silent), escalating to a hard failure (blocking `deploy run` at
  exit 3) only when the sink opts in via `required: true`. New "Dispatch
  failure — a `required` flag, not a binary silent-vs-fail choice"
  section.
- 2026-09-30: Added a 5-phase Layer 2 Implementation Plan ("create the
  implementation plan"), phased like [solution-scaffolding.md](solution-scaffolding.md)'s
  own Phase 1/2/3 structure: (1) config models (`audit_model.py`,
  `ConfigurationSpecModel.audit`, `layout.audit_dir()`); (2) manifest +
  metrics models (`audit_manifest_model.py`/`audit_metrics_model.py`,
  `render_event()`); (3) resolution helpers (`utils/actor.py`,
  `controllers/audit_path_resolution.py`); (4) the git push destination
  (`controllers/audit_push.py`, fetch+reset before every write, the
  `HEAD:<branch>` refspec trick); (5) wiring into `deploy_controller.
  deploy_run()`'s end-of-run sequencing, including the `integration` arm's
  explicit `Severity.INFO` "not dispatched yet" finding (chosen so a
  configured-but-inert sink is visibly known, not silently doing nothing)
  and the `required`/`Severity` dispatch-failure handling. Restated what's
  explicitly out of scope for every phase (integration dispatch +
  `Capability.AUDIT`, `journal`, `deployments.ndjson`, Layer 3 CLI
  reporting, the generic policy engine) once, at the top of the plan,
  rather than repeating it per phase. Updated the doc's top status line.
- 2026-09-30: **Phase 1 (config models) implemented** ("design, plan,
  show, and implement phase 1"). New `src/strata/models/audit_model.py`
  (`AuditGitSinkTargetModel`, `AuditSinkModel`, `AuditConfigModel`,
  `EVENT_DEFAULTS`); `ConfigurationSpecModel.audit` wired in
  `configuration_model.py`; `layout.audit_dir()` added. Ported both real
  v1 validators (unknown event-type key, sink-filters-against-gate),
  scoped to the 3-entry closed set. 17 new tests, including reference
  discoverability confirmed directly via `extract_references()` rather
  than assumed. Full check suite green (mypy 113 files, ruff,
  import-linter, pytest 1403 passed — same pre-existing unrelated
  `config/` drift as the sole failure). Sphinx rebuilt clean. Updated
  Phase 1's own checklist and the top status line.
- 2026-09-30: **Phase 2 (manifest + metrics models) implemented**
  ("design, plan, show, and implement phase 2"). New
  `src/strata/models/audit_manifest_model.py` (`DeploymentManifestModel`
  and its satellite sub-models — `ChangeReferenceModel`,
  `ManifestPullRequestModel`, `ManifestArtifactsModel`, `ManifestStageModel`/
  `ManifestStepModel`, `ManifestSbomReferenceModel`,
  `ManifestOutputsReferenceModel`, `ManifestPolicyResultModel`,
  `ManifestLockReferenceModel`); new
  `src/strata/models/audit_metrics_model.py` (`DeploymentMetricsModel`,
  `MetricsDimensionsModel`/`MetricsMeasuresModel`/`MetricsSectionModel`,
  `ErrorCategory`); new `src/strata/controllers/audit_event_rendering.py`
  (`render_manifest_event()`/`render_metrics_event()`). Grounded the exact
  field shapes in v1's real `models/deployment_manifest_model.py`/
  `change_reference_model.py` (read directly, not re-derived from prose)
  where the design doc's own table didn't spell out a sub-model shape —
  e.g. `ManifestPullRequestModel`'s exact field set came from "Layer 1"'s
  own `enrich_with_pr_data()` description (`number`/`title`/`url`/
  `author`/`merged_by`/`merged_at`/`labels`/`files_changed`/`approvers`),
  not invented fresh. 23 new tests. Full check suite green (mypy 116
  files, ruff, import-linter, pytest 1426 passed — same pre-existing
  unrelated `config/` drift as the sole failure). Sphinx rebuilt clean.
  Updated Phase 2's own checklist and the top status line.
- 2026-09-30: **Phase 3 (resolution helpers) implemented** ("design,
  plan, show, and implement phase 3"). New `src/strata/utils/actor.py`
  (`resolve_actor()`) and new `src/strata/controllers/
  audit_path_resolution.py` (`resolve_audit_relative_path()`). The path
  resolver reuses `semantic_checks.py::_check_deployment_layers()`'s own
  `{c.name: c for c in configuration.spec.paths}` lookup pattern rather
  than inventing a second one, and never raises on a missing/mismatched
  `layers` declaration — falls back to `workspace/deployment` instead,
  since reporting that mismatch is `semantic_checks.py`'s job. 18 new
  tests (5 actor, 13 path resolution — including convention-order
  derivation, every fallback branch, and Windows-safe timestamp
  formatting). Full check suite green (mypy 118 files, ruff,
  import-linter, pytest 1439 passed — same pre-existing unrelated
  `config/` drift as the sole failure). Sphinx rebuilt clean. Updated
  Phase 3's own checklist and the top status line.
- 2026-09-30: **Phase 4 (git push destination) implemented** ("design,
  plan, show, and implement phase 4"). New
  `src/strata/controllers/audit_push.py` (`push_audit_files()`,
  `PushResult`) and new `layout.audit_push_checkout_path()`. Found and
  fixed a real edge case via a real end-to-end test (not assumed): reset
  falls back through target-branch → remote's default branch → nothing-
  to-reset-to-at-all, since a brand-new empty remote has no `origin/HEAD`
  either — `git`'s own error, not a guess. 11 new tests (6 mocked,
  matching `test_remote_resolution.py`'s established style; 3 real,
  against a real local bare git repository, including a second push
  reusing the same checkout to confirm fetch+reset against a now-real
  branch). Full check suite green (mypy 119 files, ruff, import-linter,
  pytest 1447 passed — same pre-existing unrelated `config/` drift as the
  sole failure). Sphinx rebuilt clean. Updated Phase 4's own checklist
  and the top status line.
- 2026-09-30: **Phase 5 (wiring into the run) implemented — Layer 2
  Implementation Plan complete** ("design, plan, show, and implement
  phase 5"). New `src/strata/controllers/audit_run.py`
  (`finalize_and_distribute_deploy_audit()`), wired into
  `commands/deploy_command.py::deploy_run_command`. Two scope
  corrections found by checking real code rather than the original
  plan text: (1) no `destroy` command exists in v2 yet, so this phase is
  deploy-only. (2) `deploy_run()`'s own internals were left untouched —
  wrapping its result was chosen over restructuring its complex,
  heavily-tested fail-fast shape to expose per-step stage timing it
  doesn't have today, so the manifest's `stages[]` is omitted in this
  pass (coarser than the full design), revisited only alongside a
  separate, larger `deploy_run()` refactor. `artifacts.platform`
  references `build_path/resolved.yaml` — checked what `build_run()`
  actually writes rather than assuming a `platform.json` exists; v2 has
  no such file. 13 new tests, including a real local bare git repository
  for the sink-dispatch tests. **Found and fixed a real test-isolation
  bug along the way**: a fixture pattern borrowed from
  `test_deploy_controller.py` patched the shared `shutil` module's
  `which` globally to fake `terraform`, which silently broke this file's
  *real* `git` subprocess calls too (both modules import the same
  `shutil` object) — every push "succeeded" with 0 files actually landed,
  caught only once the test asserted on `diagnostics.warnings` directly
  instead of trusting `.ok` alone (a warning never affects `.ok`). Fixed
  by making the fake `which` pass through to the real one for anything
  other than `terraform`. Full check suite green (mypy 121 files, ruff,
  import-linter, pytest 1484 passed — same pre-existing unrelated
  `config/` drift as the sole failure). Sphinx rebuilt clean. Updated
  Phase 5's own checklist and the top status line — Layer 2 is live.
- 2026-10-03: Added "Compliance Gap Analysis (NIS2 / ISO 27001 / ISAE
  3402)", prompted by a direct challenge to PR extraction's
  justification ("useful? is there justification for this?"), followed
  by "look at the questions it needs to answer and see what is currently
  shipped," then a request to frame the findings from "the senior nis2
  isae iso27k perspective" with a MoSCoW list. Checked field-by-field,
  against `audit_run.py`'s only `DeploymentManifestModel(...)`
  construction site (not the model's field list), what each of the four
  compliance questions actually has populated today: Q4 (timestamps/
  version) and half of Q1 (artifact/SBOM hashes) are real; Q2
  (`change_reference`) and Q3 (`approvers`) are fully unpopulated, not
  merely best-effort-degraded. Confirmed, by reading v1's real
  `enrich_with_pr_data()` directly, that even v1's own implementation
  never requested `reviews`/`reviewDecision` from `gh` — so `approvers`
  was never populated there either, undermining a straight port as a fix.
  MoSCoW'd the gaps: Must (commit identity via the `git rev-parse`
  pattern already used elsewhere in this codebase; free PR-number
  recovery from `commit_message`, no `gh` dependency; minimum-viable
  Layer 3 reporting, since ISAE 3402 Type II needs an enumerable sample
  set, not one good record); Should (`change_reference` CLI wiring,
  `audit diff`, `force` capture); Could (full `gh`-based approver
  extraction, once a real `gh`-capable pipeline exists; `artifacts.
  {repositories,images,providers}` population); Won't (the journal;
  full OIDC/cloud-CLI identity). No code changed in this pass — analysis
  only, recorded ahead of implementation.
- 2026-10-04: Added "Addendum — formalizing approval via an ADO gate",
  prompted by a direct proposal to "fake" OMP's Change Advisory Board
  using an Azure DevOps Environment approval gate, with a script turning
  the approval event into a document pushed to repo/manifest/SIEM, plus
  a direct question ("strata v1 had a workflow type system?"). Confirmed
  v1 already designed exactly this: `models/gate_model.py`'s
  `DeploymentGateModel` (ADR-0057/ADR-0059) has a literal `type: "cab"`
  gate and a `mode: "declare"` whose own field description names "Azure
  DevOps environment approvals" as the intended external-enforcement
  case — record, don't re-implement. Checked real usage the same way as
  every other feature in this doc: zero evidence anywhere in
  `cfg-int-deployment` (`gates:` has exactly one unrelated, commented-out
  hit in `promotions.yaml`) — a third "declared but unread machinery"
  instance, alongside the SIEM sinks and the journal. Right-sized the
  idea rather than taking it as proposed: no new `kind: changerequest`
  document (`ChangeReferenceModel` already exists and is already
  unpopulated, confirmed in the Compliance Gap Analysis above — this is
  data to populate an existing field, not reason for a new document
  kind); no porting v1's `mode: "enforce"` work-item pause/resume
  mechanism (duplicates what an ADO Environment gate already does
  natively); no new SIEM-push step (Layers 2/4 already push the whole
  manifest once `change_reference` is populated). Recorded as a
  Could-have, with honest reservations (zero confirmed real demand; most
  of the work — ADO Environments, naming real approvers — is governance
  work outside this repo; a technical gate isn't automatically an
  organizational CAB for ISMS-scope purposes) rather than silently
  promoted. No code changed — analysis only.
- 2026-10-04: **Promoted the ADO-approval-gate item to Should-have**,
  per new information: pipeline development for this stack is actively
  in progress, and an ADO approval step will be created once it reaches
  production — a confirmed, concrete, near-term consumer, not a
  speculative one (same evidence bar that promoted the OTel/Sentinel
  sinks from hypothetical to built in
  [audit-sink-dispatch.md](audit-sink-dispatch.md)). Also recorded a
  plausible diagnosis for *why* v1's gate system went unused: likely not
  obvious how to use (a 7-type, 2-mode, condition-expression
  configuration surface), not evidence nobody needed approval gating —
  which argues for the right-sized version staying small and obvious
  (populate one existing field from one REST call) rather than
  reproducing v1's broad, generalized gate-configuration surface. No
  code changed — analysis only; still behind M1 since the ADO-side
  infrastructure (the Environment, the named approvers) isn't built yet
  either.
- 2026-10-04: Added "What `commit_sha` can and can't claim", per a
  direct pressure-test of M1 ("what commit would we be looking at — the
  last commit could also just say 'fixed typo in workspace
  description'?"). Correct: `git log -1` on whatever's checked out
  returns whatever HEAD happens to be, with no guarantee of relevance to
  the actual change behind a deploy — confirmed especially sharp given
  the earlier finding that every real pipeline is manually queued with
  nothing pinning a ref/commit at queue time. Corrected the earlier
  worked example's implicit framing (treating `commit_author` as "who
  wrote the infrastructure change") rather than leaving it stand:
  `commit_sha`/`commit_author`/`commit_message` are a reproducibility
  anchor ("exact repo state used"), not an authorship claim. Considered
  and rejected scoping the git query to the deployment's own file
  (better for the common case, but trades one blind spot — misses
  changes in separately-referenced files — for another, and is still not
  actually correct); concluded the only real fix is Layer 3's `audit
  diff`, already a Should-have, not something M1 can substitute for.
  Decision: ship M1 as originally scoped, with the claim stated honestly
  in code/docstrings. No code changed — analysis only, folded into the
  MoSCoW's M1 bullet and a new dedicated section ahead of implementation.
- 2026-10-05: **Re-prioritized M1 from Must-have to Could-have**, per a
  direct status check ("status of m1 in short. seems like something we
  skip?"). Agreed: its value shrank once honestly scoped (a
  reproducibility anchor, not an authorship claim, per the section
  above), it was never backed by a named real consumer the way
  OTel/Sentinel and the ADO gate were, and the free PR-number recovery's
  hit rate is undermined by the same "unrelated HEAD commit" problem.
  Design stays fully worked out, just deprioritized — not invalidated.
  Layer 3 (`audit status`/`audit changes`) is now the sole Must-have and
  the active next design target. Added "M1 re-prioritized to Could-have"
  recording the reasoning. No code changed.
- 2026-10-05: **Designed, then deferred, Layer 3 reporting**, per a
  request to look into the next Must-have, followed immediately by a
  direct correction before any code was written: "no data will be local.
  all the data will be remote." A full design was produced (new `strata
  audit` command group with `status`/`changes` subcommands, a new
  `controllers/audit_report.py` reading local `_manifest.json` files
  under `layout.audit_dir(root)`) — then deferred on the correction,
  since this doc had already established, for a different piece, exactly
  why that data source is wrong: the `deployments.ndjson` removal
  ("90 percent deployed in CI/CD so will be not saved") applies
  identically here. A command reading local files would silently show
  nothing for the ~90% real case rather than failing loudly — worse than
  not shipping it. Recorded what a correct version actually needs (read
  from the `git` sink's pushed remote, not local disk — a materially
  bigger design involving a fetch/checkout step and a sink-selection
  decision) and moved Layer 3 to a new "Deferred — blocked on a real
  redesign" MoSCoW category, distinct from Could-have (lower priority)
  and Won't-have (no value) — this is neither; it just isn't validly
  designed yet. The Must-have category is now recorded as genuinely
  empty rather than backfilled with a weaker substitute. No code
  changed.
- 2026-10-05: Verified, against Microsoft's real docs rather than
  assumption, whether an ADO approval gate can request structured
  parameters (e.g. a ticket number) from an approver. Checked the
  conceptual approvals page and the real `ApprovalUpdateParameters` REST
  schema directly: the answer is no — an approver can only submit
  `status` + one free-text `comment`; the check's own `instructions`
  field is static, set once by the resource owner, not a per-run input.
  This splits `ChangeReferenceModel` cleanly: `reason` maps 1:1 onto
  `comment`; `id`/`system`/`classification` have no equivalent in the
  approval step and would need a queue-time pipeline `parameters:` entry
  instead (confirmed as a real, already-used mechanism in
  `cfg-int-deployment`'s actual pipelines) rather than parsing structure
  out of an approver's free text. Folded into the ADO-gate addendum. No
  code changed.
- 2026-10-05: **Implemented `change_reference` CLI wiring.** Six new
  `deploy run` flags (`--change-system`/`--change-id`/`--change-reason`/
  `--change-classification`/`--change-title`/`--change-url`), one per
  `ChangeReferenceModel` field, per direct confirmation ("we use CLI
  params not a file, store those values, and then apply them to the
  manifest"). `system`/`id`/`reason` validated as all-or-nothing in
  `deploy_command.py` (`UsageError` before `deploy_run()` executes);
  `audit_run.py`'s new `_build_change_reference()` helper builds the
  model (or `None`) and auto-derives `supplied_by`/`supplied_at`, trusting
  the CLI layer's validation rather than re-checking it — same "one place
  owns the invariant" discipline as `AuditSinkModel`'s own exactly-one-arm
  validator. 6 new tests total (4 in `test_audit_run.py`, 2 in
  `test_commands_deploy.py`, including a partial-combo case asserting
  `deploy_run()` is never reached). Full check suite green: mypy (136
  files), ruff, ruff format, import-linter 1/0, pytest 1879 passed.
- 2026-10-05: **Corrected the ADO-gate addendum's `comment`-based
  `reason` plan** after verifying two further real constraints against
  Microsoft's docs rather than assuming the design was complete. (1)
  Compared the real `jobs.job` and `jobs.deployment` YAML schema
  references side by side: only `jobs.deployment` has an `environment:`
  property at all, so Environment approval checks can only gate
  deployment jobs — adopting this gate requires restructuring whichever
  job in `cfg-int-deployment` currently calls `strata deploy run` into a
  deployment job, a real pipeline-shape change, not just adding a check.
  (2) Checked both candidate mechanisms for a script to read back "the
  approval that just passed" and found neither exists: the documented
  Deployment job variables (`Environment.Name`/`.Id`/`.ResourceName`/
  `.ResourceId`, `Strategy.Name`/`.CycleName`) include no approval ID,
  and the Approvals Query REST API filters only by `approvalIds`/
  `state`/`userIds`/`top` — no run/build-ID filter. There is no reliable
  way to correlate a run to its own approval's `comment`. Revised
  recommendation: drop the `comment`-sourced `reason` entirely and make
  both `--change-id` and `--change-reason` queue-time pipeline
  parameters, sidestepping the correlation gap rather than working
  around it — simpler than the prior split-source design, and the
  strata-side flags to accept them already shipped (2026-10-05). No code
  changed — doc correction only.
- 2026-10-05: **Designed the Layer 3 read path, in a new dedicated doc**
  ([audit-commands.md](audit-commands.md)), per a direct request to design
  `strata audit` commands "for this purpose" (git as the read basis) and
  keep the design in a separate file rather than growing this one further
  — same split already done for audit-sink-dispatch.md. Grounded directly
  in real code read first, not assumed: confirmed `spec.audit` actually
  lives on `ConfigurationModel`, not the solution manifest
  (`configuration_model.py`'s `ConfigurationSpecModel.audit`); confirmed
  `audit_push.py`'s real `_ensure_checkout()`/`push_audit_files()` shape
  (clone-if-absent, always fetch+reset before write); confirmed `layout.py`'s
  established convention of a dedicated subdirectory per checkout
  population (`audit_push_checkout_path()` vs `gitops_push_checkout_path()`)
  and applied the same pattern to a new read-only checkout rather than
  reusing the push destination (avoids a concurrent-git-command race with
  an in-flight push); confirmed the `graph_command.py` precedent for
  returning structured JSON data (`isinstance(run.reporter, JsonReporter)`
  + `run.reporter.data = {...}`) rather than inventing a new output
  convention. Core design decision: enumerate by recursively globbing for
  `_manifest.json` and trusting each manifest's own fields for
  filtering/sorting, rather than reconstructing `resolve_audit_relative_path()`'s
  layers-dependent write-time directory shape from outside — confirmed
  that function's real dependency on a deployment's own
  `layers.segments`/`Configuration` state first, rather than assuming a
  path-based reconstruction would be reliable. Updated this doc's Must-have
  and "Layer 3 deferred" sections to point at the new doc instead of
  restating the design here. No code changed — design only, nothing built.
- 2026-10-05: **`strata audit status` implemented** (audit-commands.md's
  Phases 1-4: `layout.audit_read_checkout_path()`, the shared
  `git_checkout.ensure_synced_checkout()` extraction,
  `controllers/audit_read.py`, `commands/audit_command.py`). Phase 5 of
  that doc's own Implementation Plan — updating this doc's cross-references
  from "a design exists" to "implemented": the Must-have entry, the
  former "Deferred — blocked on a real redesign" MoSCoW category (removed;
  no longer applies — `audit status` is built, so "not validly designed
  yet" is no longer the reason anything here waits), the "Layer 3
  deferred" section's own Decision paragraph, and the top status line.
  `strata audit changes` (range listing — the full ISAE 3402 "enumerable
  sample over a period" ask) remains a deliberate, tracked scope cut in
  audit-commands.md, not a design gap — the Must-have category here stays
  open until that ships too, not papered over as fully done. No code
  changed in this pass — documentation cross-reference update only, per
  audit-commands.md's own Phase 5.
- 2026-10-05: **Caught this doc up to `strata audit changes` also
  shipping** (audit-commands.md's Phases 6-7, implemented the same day
  as the entry above but under separate design/plan/implement requests —
  this doc's own cross-references had gone stale in the meantime, still
  saying "a deliberate scope cut, not yet built"). Corrected, per a
  direct "is this a valid first audit layer?" question that prompted
  re-checking the current state rather than answering from memory: the
  top status line, the Must-have MoSCoW entry (now closed out — this is
  the one item this whole MoSCoW ever marked Must-have, and the only one
  now fully done), and the "Layer 3 deferred" section's Decision
  paragraph. Also noted audit-commands.md's own full-review pass (same
  day) that found and fixed three real bugs in the shipped commands. No
  code changed — doc catch-up only.
- 2026-10-05: **Added `approved_by`/`approved_at` to `ChangeReferenceModel`**,
  per a direct question ("differentiation between who executes and who
  approves?"). Closes part of Q3 honestly, not fully: a third distinct
  identity alongside `deployed_by` (who ran the deploy) and `supplied_by`
  (who typed the change reference) — who *approved* it. Deliberately
  **not** a replacement for PR-approver extraction (still not pursued,
  per the update above) — a different, complementary mechanism. Design
  decisions worth restating: both-or-neither validated on the model
  itself (mirrors the existing `system`/`id`/`reason` discipline);
  `deploy_command.py` additionally rejects `--change-approved-by`/`--at`
  given without the base `--change-system`/`--change-id`/`--change-reason`
  trio (an approval needs a change reference to approve — silently
  dropping them would be worse than rejecting); `approved_at` deliberately
  does **not** auto-derive from the run's own clock the way `supplied_at`
  does, since the approval happened earlier, in the tracker. Stated
  directly, not left implied: these fields are operator-supplied, not
  independently verified — a future tracker-integration lookup would be
  strictly stronger, not built since no real consumer's tracker
  integration exists yet. Two new CLI flags on `deploy run`
  (`--change-approved-by`/`--change-approved-at`), 15 new tests across
  `test_models_audit_manifest.py`/`test_audit_run.py`/
  `test_commands_deploy.py`. Full check suite green: mypy (139 files),
  ruff check, ruff format, import-linter (1 kept, 0 broken), pytest
  (1958 passed, up from 1948).
- 2026-10-07: **Fully designed `policy_results` population**, raised from
  [cve-scanner-integration.md](cve-scanner-integration.md)'s own
  "Remaining Work" tracking item ("create the design for it"). Found the
  real architectural wrinkle by reading `finalize_and_distribute_deploy_
  audit()` directly: the manifest is built exactly once, at the end of a
  `deploy run` invocation, but every policy that exists today evaluates
  in an earlier, separate CLI invocation (`cve_policy` in `build run`,
  `tenant_zone`/`path_convention` in `strata validate`) — a `Diagnostics`
  result does not naturally survive from the command that produced it to
  the later command that builds the manifest. Resolved by reusing the
  exact precedent already established for `resolved.yaml`/`sbom.json`:
  `build_path` as the real hand-off artifact directory, with a new,
  optional `build_path/policy_results.json` sidecar written by `build
  run` and read back by a new `_policy_results_reference()` helper,
  modeled 1:1 on `_sbom_reference()`'s own shape. Scoped deliberately to
  build-phase policies only for this pass (`cve_policy`); validate-phase
  policies (`tenant_zone`/`path_convention`) are explicitly deferred as a
  harder, separate problem — `strata validate` has no `build_path` and no
  enforced 1:1 relationship to any one later `deploy run`. Surfaced one
  real, un-glossed-over naming gap: `ManifestPolicyResultModel.policy_name`
  assumes v1's named-policy-list shape, which `cve_policy`/`tenant_zone`
  (v2's unnamed, bespoke-field policies) don't have — resolved by falling
  back to the field name itself (`"cve_policy"`) rather than revisiting
  that earlier design decision. Full implementation plan (5 steps) and
  test plan recorded; no code written this pass — design only, ready to
  implement.
- 2026-10-07: Brought the `policy_results` design up to the same rigor as
  `cve-scanner-integration.md`'s own Phase 3 design, per a direct
  completeness check ("is the design for this complete with
  implementation plan?") — honest answer at the time: no, not quite.
  Added the actual code (not just named functions): `_cve_policy_result()`'s
  real body, a new shared `strata/controllers/policy_results.py`
  (`write_policy_results()`/`read_policy_results()`, replacing the
  earlier sketch's separate `audit_run`-local wrapper — unnecessary
  indirection, corrected), and the real two-line `build_run()` call-site
  change this design's first pass had glossed over: `evaluate_cve_policy()`'s
  own `Diagnostics` must be captured in its own local variable before
  merging into the shared accumulator, or there is no way to tell which
  findings came from this one policy. Replaced the prose test scenarios
  with 6 explicitly named test functions. No code written yet — still
  design only, now genuinely implementation-ready.


