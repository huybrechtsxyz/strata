# Audit Trail — v1 Capability Catalog and v2 Design Questions

- Status: draft — real-usage scoping done (2026-09-30); ready for a v2
  design/ADR pass next
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
`commands/audit/*.py`, `logger/audit.py`, `models/deploy_log_model.py`) and
v1's `docs/decisions/0018-deployment-audit-traceability.md` and
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
  scaffolding) is cheap and real, a reasonable close second. Layers 3
  and 4 have no CI evidence of use and should wait for a concrete need.
- **Does v2 need all four layers, or fewer?** The tiering idea (local +
  queryable + tamper-resistant are different concerns) is the most
  transferable part; the specific mechanisms (PR-template scaffolding,
  CloudEvents+ECS envelope, `gh` CLI PR enrichment) are each independently
  adoptable or droppable.
- **Actor/identity is an open dependency.** v1 itself never solved "who is
  the actor" beyond `commit_author` (wrong) and a bare `user.name` string —
  real identity/AuthN is out of scope in v1's own ADR too (their ADR-0067).
  v2 has no identity model yet; decide whether v2's first audit cut can
  ship with a weaker actor (e.g. OS user / CI identity env var) or must
  wait.
- **Where does config live?** v1 settled on configuration-document-only,
  explicitly not environment-level, for separation-of-duties reasons — v2
  should decide this deliberately rather than default to "wherever's
  convenient", since `ConfigurationSpecModel` is already the deferred home
  for `audit` per gap #7.
- **Reuse v2's own integration layer for sinks, if built at all.** v2
  already has `strata.integrations` (ADR-0021-equivalent) with a registry
  and capability ABCs — the "sink is a reference to an integration, never
  its own transport config" pattern maps directly onto that, if/when v2
  builds sinks.
- **Deploy-log is no longer unbuilt-on ground in v2 — corrected 2026-09-30.**
  This doc originally said v2 had no `deploy run` command yet; that's stale.
  `strata deploy run` is built and tested (ADR-0027,
  [`deploy-command.md`](deploy-command.md)) — per-stage results, timings,
  errors all already exist at runtime, just never persisted as a
  `DeployLogModel`-shaped record. A deploy-log design can proceed now; it
  does not need to wait on anything else.
- **Decide the event taxonomy fresh, or adopt CloudEvents+ECS wholesale?**
  v1's case for reuse (SIEM field-mapping is then a no-op) is strong, but
  v2 should confirm a real consumer actually needs SIEM-facing output
  before paying the CloudEvents envelope's complexity cost — v1 explicitly
  says that cost is real, not negligible.

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

