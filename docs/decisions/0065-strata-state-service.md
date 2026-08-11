# Strata state service — a durable store for history that must survive a disposable runner

- Status: proposed
- Date: 2026-08-06 (revised 2026-08-09 — broadened from an audit-only ingest service to a general strata state service; see "Revision note" below)
- Related: ADR-0007 (deployment state locking — precedent for pluggable remote backends), ADR-0008 (infrastructure drift detection), ADR-0011 (promotion strategies), ADR-0018 (deployment audit & traceability), ADR-0022 (SIEM integration), ADR-0026 (resolved-model cache — rebuildability precedent), ADR-0031 (cost estimation & visibility), ADR-0057 (deployment workflow orchestration — precedent for pluggable remote backends), ADR-0062 (CLI consolidation — introduces `rollout`), ADR-0064 (deployment metrics record), ADR-0066 (audit event routing & policy model), ADR-0032 (approval gates)

## Revision note

This ADR originally proposed an "audit ingest service" scoped to three record kinds reachable through `spec.audit.sinks`: deploy-logs, deployment manifests, and (once ADR-0064 landed) metrics records. Re-reading it against the rest of the codebase surfaced a broader pattern: **most of strata's history is written to per-machine files that a disposable CI runner discards the moment the job ends**, and audit records are only one instance of that, not the whole problem. Drift history (`.strata/drift/*.drift.json`, ADR-0008) and cost history (`.strata/cost/*.cost-history.json`, ADR-0031) are both explicitly gitignored, per-deployment, append-only JSON files with exactly the same "invisible outside this machine" property as the pre-ADR-0018 deploy-log — and neither has any remote-forwarding story at all today, audit or otherwise. This revision keeps every mechanism the original ADR proposed (HTTP ingest over a queryable SQL store, the projection invariant, idempotency via a composite key, append-only semantics, best-effort bounded delivery) and generalises what it is *for*: not "audit events," but **strata's write-once local history in general** — deploy-logs and metrics included, but no longer the only tenants.

## Context and Problem Statement

Strata is designed to run statelessly, once per invocation, usually on a CI runner that is destroyed the moment the job ends. Every command that needs to remember something across invocations has had to solve that problem on its own, and it has produced two very different answers depending on *what* needs remembering:

**Coordination state** — a lock that must be visible to a second, concurrent runner, or an approval gate that must survive from the moment a pipeline pauses to the moment a human resolves it days later — already has a real answer. `BaseLockBackend` (ADR-0007) and `BaseWorkItemBackend` (ADR-0057) are both pluggable abstractions with remote implementations (S3, GCS, Azure Blob, Consul, Terraform Cloud, git tags) precisely because a lock or a pending approval sitting only on the runner that created it is useless the instant that runner disappears. This part of the problem is solved, and solved consistently — it is cited here as the precedent the rest of this ADR follows, not as something it needs to fix.

**History state** — a record of something that already happened, written once and never mutated again — has no such answer. It is written to a local file and left there:

| Record kind         | Local file                                    | Introduced by             |
| ------------------- | --------------------------------------------- | ------------------------- |
| Deploy-log          | `.strata/deploy-log/**/_execution.json`       | ADR-0018                  |
| Deployment manifest | `{build_path}/{deployment}/manifest.json`     | deployment-manifest model |
| Deployment metrics  | `.strata/metrics/deployments.ndjson`          | ADR-0064                  |
| Drift history       | `.strata/drift/{deployment}.drift.json`       | ADR-0008                  |
| Cost history        | `.strata/cost/{deployment}.cost-history.json` | ADR-0031                  |

All five are gitignored (or effectively per-machine — the deploy-log has an explicit, opt-in git-push escape hatch, but it is off by default). All five accumulate entries over time rather than getting overwritten. All five are read back later to answer a question — "how many deploys failed this quarter," "has this resource been drifting for a week," "what did this stack cost last month" — that only makes sense across many runs, most of which happened on runners that no longer exist. Only the first three (deploy-log, manifest, metrics) currently have *any* forwarding path at all, and that path — `spec.audit.sinks` — is one-way and lossy: it hands the record to a third-party SIEM/webhook and strata itself never sees it again.

ADR-0064 makes the gap sharper rather than solving it: its self-containment invariant deliberately pushes *all* aggregation downstream, so deployment frequency, change failure rate, and MTTR are explicitly not computable from a single record. That is the right call for a single record, but it means the value of the metrics work — and, by the same argument, of drift and cost history — is entirely gated on some consumer existing that accumulates many of them. Today, strata ships no such consumer, for any of the five.

Separately, there is a longer-term question hanging over the project: if strata ever grows a service — one that can hold a lock across process boundaries, wait days for an approval, or run scheduled drift detection — what does it stand on? Those capabilities all presuppose durable, queryable, cross-workspace state. Building that state store as a side effect of solving the history-aggregation problem is considerably cheaper than building it later as a greenfield control plane.

So: **strata needs a first-party destination — for its history in general, not for audit events specifically — that is durable, central, and queryable by whatever tooling the operator already owns.**

## Decision Drivers

- **This should extend an existing pattern, not invent a new one** — ADR-0007/0057 already establish "local by default, pluggable remote backend when cross-runner visibility matters"; this ADR applies that same idea to history, it does not introduce a competing philosophy
- **The emit side is already mostly built** — sinks, routing, event policy, redaction, and best-effort semantics all exist for audit records (ADR-0018/0022/0066); extending the same mechanism to drift and cost history should be additive, not a rewrite
- **Aggregation is worthless without a corpus** — ADR-0064 Phase B/C, and any future drift or cost trend command, all stall until something accumulates records
- **The operator's tooling is not ours to choose** — Grafana, Metabase, Power BI, `psql`, a Python notebook; SQL is the widest possible interface
- **The deploy path must stay safe** — ingest cannot become a dependency that slows or breaks deployments, builds, or drift/cost checks
- **Credentials on CI runners are a liability** — whatever we hand a pipeline will eventually leak; scope it accordingly
- **A future control plane should be incremental, not greenfield** — reuse this as its state store rather than designing a second one
- **Coordination state stays out of scope** — locks and work items already have a working, pluggable-backend answer (ADR-0007/0057); folding them into this service would solve an already-solved problem and blur two genuinely different consistency models (mutable/exclusive vs. append-only/immutable)

## Considered Options

**Option A — a `database` built-in sink; the CLI writes directly to a central SQL database**

Add `type: database` alongside `stdout`/`ndjson`/`syslog`/`webhook`, with a connection string, and have each history subsystem (audit, drift, cost) `INSERT` records itself.

Rejected on four independent grounds:

- **Credential blast radius.** Every CI runner and every developer laptop would hold database credentials. A connection that can `INSERT` can generally also `UPDATE`/`DELETE`, which is catastrophic for record series whose entire value rests on being immutable history. Restricting to insert-only grants is possible but must then be configured correctly by every operator, forever.
- **Schema ownership and version skew.** The CLI would encode table structure for five different record kinds. A workspace pinned to strata 1.6 and one pinned to 1.9 would write to the same tables with different expectations, and someone would need to run DDL — which means granting DDL rights somewhere on the deploy/build/drift/cost path.
- **Egress.** Outbound HTTPS from a CI runner is universally permitted. An outbound Postgres port generally is not, and getting it opened is an organisational project.
- **Dependency weight.** A database driver becomes a CLI dependency. Strata's existing sinks deliberately use `urllib` and stdlib sockets precisely to avoid this.

**Option B — an HTTP ingest service in front of a SQL event store** *(chosen)*

A small first-party server accepts records over HTTP and persists them, generalised across every write-once history kind strata produces. Workspaces reach it through the **existing** `webhook` sink (audit records today; drift and cost history can adopt the same delivery path without inventing a second one). Operators query the database directly with any SQL tooling.

**Option C — rely on third-party SIEM/observability platforms only**

Status quo for audit; the only option today for drift/cost. Rejected as a complete answer: it makes cross-workspace measurement conditional on owning and funding an observability platform, it only ever covers the record kinds someone bothers to wire a sink for, and it leaves strata unable to ship any aggregate command of its own for any of the five kinds. Retained as a parallel delivery channel — sinks are a list, and nothing here displaces them.

**Option D — build the full control plane now**

Skip the ingest-only step; build identity, authorization, run orchestration, approvals, and dashboards together.

Rejected as sequencing, not as a destination. It front-loads every hard problem (multi-tenancy, authz model, run isolation) before a single question has been answered, and it would launch against an empty database — so the first dashboard would show nothing for months. Option B reaches the same place with the hard parts deferred until there is evidence about what is actually needed.

## Decision Outcome

Chosen: **Option B — an HTTP ingest service over a queryable SQL event store, generalised to strata's history in general rather than scoped to audit records.**

The decisive property is unchanged from the original ADR: **the client side is already mostly built** for the record kind that matters most (audit). `AuditSinkModel` already supports `type: webhook` with `url` and `headers`, and `AuditController._send_webhook()` already POSTs the record as JSON over `urllib`. Pointing a workspace at the state service is configuration, not code, for deploy-logs and metrics today:

```yaml
spec:
  audit:
    policy:
      events:
        deployment.completed: true
    sinks:
      - name: strata-state
        integration: strata-state   # a webhook-type integration pointed at the state service
```

Drift history and cost history have no equivalent sink today — extending `DriftHistoryStore`/cost history's own `record_snapshot()` to optionally forward through the same `AuditController.forward()`/webhook path (rather than inventing separate delivery code) is in scope for this ADR's implementation, not a separate proposal; the wire format and server are identical either way, only the local writer gains an additional best-effort forward call, mirroring exactly how `deployment.completed` already works.

A second, independent durability option already exists for the deploy-log and is directly reusable, not something this ADR needs to invent: `AuditController.push_to_remote()` stages, commits, and pushes arbitrary local files to a git remote, and `spec.audit.repository` names *which* remote by looking up a **solution-level repo** (one registered with `strata repo add`, listed in `solution.json`, resolved via `SolutionController.get_repo_map()`) — the exact same repo registry `@repo_name/path` cross-repo references already use. This is worth being precise about because strata has two same-sounding but genuinely different "remote" concepts: `configuration.spec.remotes` (`RemoteModel` — named remote *endpoints*, used for `ref_convention` policy tag conventions, `gitops` provisioner backends, and environment remote overrides) is **not** this mechanism at all; `spec.audit.repository` redeclares nothing new, it points at the solution-level repo registry, which is a separate, pre-existing concept from `spec.remotes`. `push_to_remote()` itself is already generic — it takes any list of paths and any working directory — but as detailed in Phase 1 below, reusing it for cost/drift needs more than a commit-message parameter: it needs to actually place the file inside the target repo at a configured location, rather than assume the local write path already sits there — see Open Question 7.

#### Where else the durable-repository pattern helps

Checking every local artifact against the same test the command-by-command review above uses — a fact that happened once, invisible once the runner is gone, worth asking about later — surfaces more candidates than just cost, and one genuine finding: **the pattern already exists a second time, independently, and diverges from the audit implementation.**

| Artifact                                                            | Git-push today?                                                                                                                                                                           | Fits the pattern?                                                                                                                                                                                                                                                     | Verdict                                                                                                                                             |
| ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Deploy-log                                                          | Yes — `AuditController.push_to_remote()`, via `spec.audit.repository` (a named solution repo)                                                                                             | Reference implementation                                                                                                                                                                                                                                              | **Already done**                                                                                                                                    |
| Deployment manifest                                                 | **Yes, but separately** — `ManifestController.push_to_remote()`, gated by `manifest_config.push_manifest` (a bool), pushes from the *workspace's own* `origin`, not a named solution repo | Same idea, independently reimplemented with a different configuration surface, a different working directory assumption, and a different commit message                                                                                                               | **Already done, but duplicated — worth unifying with the audit implementation rather than adding a third variant (see below)**                      |
| Cost history                                                        | No                                                                                                                                                                                        | Yes — identical shape to deploy-log                                                                                                                                                                                                                                   | **Candidate — this ADR's own drift/cost forwarding work should add this alongside state-service forwarding, not instead of it**                     |
| Drift history                                                       | No                                                                                                                                                                                        | Yes — identical shape to deploy-log                                                                                                                                                                                                                                   | **Candidate — same as cost history**                                                                                                                |
| Deployment metrics (`.strata/metrics/deployments.ndjson`, ADR-0064) | No                                                                                                                                                                                        | Yes — append-only, same shape                                                                                                                                                                                                                                         | **Candidate — same reasoning, not yet named as one before this pass**                                                                               |
| SBOM (`sbom.json`)                                                  | No                                                                                                                                                                                        | Partially — a historical diff of dependency changes over time is genuinely useful, but SBOMs are the one artifact Phase 2 explicitly excludes as a state-service payload (size), and a git repo has the same repo-bloat concern for anything committed on every build | **Weak candidate — plausible, but revisit only alongside the size/retention questions Phase 2 already has to answer for SBOMs, not as a quick add** |
| Version-lock files (`strata promote`)                               | Yes, but structurally different — the lock file's *primary* storage location is a git-backed `versions_path`; there is no separate local file with an optional push bolted on afterwards  | Already durable by construction, not an instance of this pattern at all                                                                                                                                                                                               | **Not a candidate — nothing to fix, it never had the gap the other rows do**                                                                        |
| Rollout report                                                      | N/A — no local artifact exists today; `rollout` orchestrates other commands live and persists nothing of its own                                                                          | Would become a candidate the moment `rollout` gains a persisted local artifact                                                                                                                                                                                        | **Not yet applicable — revisit if/when `rollout`'s own future ADR adds a local report file**                                                        |

The manifest finding is the one worth acting on directly, not just noting: two controllers (`AuditController`, `ManifestController`) each implement "stage, commit, push these files to git," with different configuration knobs (a named solution repo vs. a bool pushing to the workspace's own `origin`), different defaults, and different commit messages, and neither reuses the other. Extending the pattern to cost/drift/metrics by adding a *third* independent copy would make this worse, not better — the right move is consolidating on one implementation (almost certainly `AuditController.push_to_remote()`, since it is the more general of the two — it already accepts an arbitrary `working_dir`, which `ManifestController.push_to_remote()` does not) before cost/drift/metrics adopt it, so four record kinds converge on one mechanism instead of adding a fourth divergent one. This consolidation is now Phase 1, below — not deferred to an open question.

Everything new lives on the server, where it can be versioned, migrated, and secured independently of the ~dozens of runners that write to it. The one client-side change the original ADR required — raising sink-delivery failures from `debug` to `warning` — remains true and is already reflected in ADR-0066's implementation.

### The projection invariant

The rule governing the store, and the counterpart to ADR-0064's self-containment invariant:

> **The state service is a queryable projection, never the source of truth.**

Build artifacts, the local deploy-log, `.strata/metrics/deployments.ndjson`, `.strata/drift/*.drift.json`, `.strata/cost/*.cost-history.json`, and git remain authoritative — **but reconstructability is a property of which durable store the operator actually chose for the source record, not of the CI runner, and not of the state service.** It is tempting to read "the store must be fully reconstructable" as "as long as the disposable runner hasn't deleted its local files yet" — that is the wrong test. A disposable runner deletes everything the moment the job ends, local files included; whether a record survives that has nothing to do with the runner and everything to do with whether one of its configured sinks/stores is itself durable. Concretely:

- The deploy-log is reconstructable across runner churn precisely because, and only because, `spec.audit.repository` (ADR-0018) pushes it to a git remote. A workspace that leaves that unset has a deploy-log exactly as ephemeral as the runner that wrote it — gone the instant the job container is destroyed, with nothing left to replay from, `strata audit resend` included.
- Drift history and cost history have **no** durable-store option today, git-push or otherwise, until Phase 1 (below) extends `push_to_remote()` to them — on a disposable runner they do not outlive their own job regardless of anything this ADR does. Forwarding them to the state service (Phase 2) without also adopting Phase 1 does not fix that; it only means the state service now holds the *only* surviving copy, which inverts the invariant rather than satisfying it.

So the invariant should be read as conditional, not unconditional: **wherever the source record's own store is durable, the state service must be reconstructable from it; wherever it is not, that is a gap in the source record's durability, not a licence for the state service to become the source of truth by default.** This is exactly what `strata audit resend` already does for the one record kind (deploy-log) that has a durable store configured today, and it is why drift/cost history need the same kind of durable-store option before "reconstructable" is a meaningful claim about them, rather than a hopeful one.

This is the same discipline ADR-0026 applies to `cache.db` ("the database is fully rebuildable"), and it is load-bearing for the same reason: it is what preserves the freedom to change the schema. A store that cannot be rebuilt is a store whose schema is frozen the day the first row lands.

Note the difference from `cache.db` all the same — `cache.db` is rebuildable from state that is *always* present (the checked-out workspace itself). This database is only reconstructable where the operator has additionally chosen to make the source record durable (git-push today, for deploy-log only) — a strictly weaker guarantee that depends on configuration strata does not itself enforce.

And that reconstructability, where it exists, is a bonus — a defense-in-depth safety net — not the primary answer to "what happens if we lose this database." The primary answer is the same as for any other production database: backups, replication, and a tested restore path, operated with the same rigor as the rest of the production estate. This matters more, not less, as Phase 4 approaches — the moment a control plane's approvals, run history, or authorization decisions are built on top of this store, its rows stop being a nice-to-have measurement trail and become data whose loss is a real incident. "We could theoretically rebuild the deploy-log slice from git" is a genuinely useful property to have in reserve; it is not a substitute for running the database itself like it matters, and it must never be used to justify skipping backups on the theory that everything is reconstructable — most of it, today, is not (see the drift/cost gap above). It is therefore backed up like real data, not discarded like a cache, and it must never live under `.strata/cache/`.

### Phase 1 — unify durable git-push storage ✅ Done

Implemented as designed below: `RepositoryPushModel` (`push`/`name`/`path`) added to `strata.models.audit_config_model`, reused by `spec.audit.repository`, `spec.deployment.manifest.repository`, and the new `spec.cost.history.repository`/`spec.drift.history.repository`. `ConfigurationManifestModel`'s dead `type`/`repository`(str)/`branch`/`tag` fields and `ManifestStoreType` are removed. `AuditController.push_to_remote()` now copies into the resolved repo at `{remote_path}/{workspace}/...` before committing (no longer assumes co-location); `ManifestController` is deleted, its one call site now goes through `AuditController`. `CostController`/`DriftController` gained best-effort push wiring right after their existing local writes, resolving `spec.cost.history.repository`/`spec.drift.history.repository` via `ConfigurationService`. Verified via full `Check.ps1` (5517 tests, ruff, mypy).

Before any new service exists, close the gap that's cheapest to close and already half-solved: consolidate the two existing, divergent git-push implementations into one, and extend it to the record kinds that don't have it yet — cost history and drift history (metrics has no store implementation yet at all; adopt this once it does). Version-lock files are deliberately excluded: they are already stored directly in a git-backed repo as their primary location, not a local file with an optional push bolted on afterwards, so there is nothing to unify for them.

#### Today: two divergent implementations, and one that's dead on arrival

|                         | `AuditController.push_to_remote()`                                                                                                         | `ManifestController.push_to_remote()`                                |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| Configured by           | `spec.audit.repository` (a **named** solution repo)                                                                                        | `manifest_config.push_manifest` (a **bool**)                         |
| Pushes from             | The resolved solution repo's own working tree (`SolutionController.get_repo_map()`)                                                        | The current workspace's own working tree (`self._work_path`)         |
| Pushes to               | That repo's configured remote                                                                                                              | The workspace's own `origin`, hardcoded                              |
| Where the file lands    | Wherever `spec.audit.deploy_log_path` happens to already point — must be manually kept inside that repo's checkout for this to work at all | Wherever `manifest_config.path` already points, inside the workspace |
| Commit message          | `"chore(audit): deploy-log update [skip ci]"`                                                                                              | `"chore(manifest): deployment manifest update [skip ci]"`            |
| Reused by anything else | No                                                                                                                                         | No                                                                   |

Both do the same three things — `git add`, `git commit` (tolerating "nothing to commit"), `git push` — with the same shape of error handling (log a warning, return `False`, never raise). But neither actually solves "where does this land inside the target repo": both assume the local write path *already* sits inside the repo they're pushing from, and compute each file's path relative to that repo's root (`p.relative_to(base)`) to stage it. If the local path and the push target don't happen to coincide — which nothing today enforces or even checks — that computation raises, falls back to the file's raw absolute path, and `git add <absolute-path>` from the wrong working tree fails silently (logged at `warning`, not surfaced). This is a real, load-bearing gap, not a style difference between the two implementations.

`ConfigurationManifestModel` makes it worse: `type: gitops` requires `repository` and `branch` to be set (a validator enforces it), and the docstring describes them as resolving against `spec.remotes` — but neither field is read anywhere at write or push time. `resolve_output_dir()` never inspects `type`/`repository`/`branch`; the push call site (`manifest_ctrl.push_to_remote([path])`) passes none of them either. They are validated as required together, then never consulted. In practice `type: gitops` + `repository: state-repo` behaves identically to `push_manifest: true` alone — always the workspace's own repo, always `origin`. This is a bug to fix as part of the same pass, not a design constraint to preserve.

#### Unified design

One shared implementation, kept where the more general of the two already lives (`AuditController.push_to_remote()`), given an explicit job neither current version does — placing the file at a *configured* location inside the target repo, not assuming it's already there:

```python
def push_to_remote(
    self,
    local_paths: List[Path],
    *,
    repo_name: Optional[str] = None,     # named solution repo; None = this workspace's own repo
    remote_path: str,                    # where inside that repo — see "Distinguishing artifacts", below
    remote_name: str = "origin",
    commit_message: str,                 # now required, not hardcoded — caller states its own intent
) -> bool:
    """Copy local_paths into {resolved_repo}/{remote_path}/..., then git add/commit/push from there."""
    ...
```

The behaviour change from today: this **copies** each file from its local, fixed location into the resolved repo's working tree at `remote_path`, then commits and pushes from there — it no longer assumes the two locations already coincide. That copy step is what actually closes the "silently misconfigured, push quietly fails" gap identified above; alignment is no longer something the operator has to get right by hand.

- `repo_name=None` preserves both `ManifestController`'s and `AuditController`'s existing default reachability (push to the workspace's own repo) — unchanged for existing configs.
- `commit_message` moves from hardcoded to caller-supplied, so each record kind's commit history stays self-describing (`chore(cost): history update`, `chore(drift): history update`, ...) without near-identical copies of the method.

`ManifestController.push_to_remote()` and its dead `type`/`repository`/`branch` fields are deleted; `base_deploy_command.py`'s manifest call site switches to `AuditController.push_to_remote()`. `manifest_config.push_manifest: true` continues to mean exactly what it means today (push to the workspace's own repo) — the fix removes dead configuration, it doesn't change working behaviour.

#### Configuration surface

**Local paths stay exactly as they are — fixed for cost/drift, unchanged where already configurable for audit/manifest.** Cost and drift have no local path configuration today (`get_cost_dir()`/`get_drift_dir()` are hardcoded) and this phase does not add any — `.strata/cost`, `.strata/drift`, `.strata/deploy-log`, and the manifest's own path stay exactly as predictable as they are today. Only the remote side is new configuration surface, and it is the same shape for every record kind:

```yaml
spec:
  audit:
    repository:
      push: true          # states: absent/false = don't push; true = push
      name: config        # named solution repo; omit = push to this workspace's own repo
      path: history/deploy-log   # where inside that repo — optional, see below
  deployment:
    manifest:
      push_manifest: true   # unchanged — existing field, existing meaning
      repository:
        push: true
        name: config
        path: history/manifest
  cost:
    history:
      repository:
        push: true
        name: config
        path: history/cost   # new — cost has no configuration surface at all today
  drift:
    history:
      repository:
        push: true
        name: config
        path: history/drift  # new — same as cost
```

`name` (not `repository`, to avoid `repository.repository`) is the only field naming *which* repo — a plain string, resolved the same way `spec.audit.repository` already resolves today (`SolutionController.get_repo_map()`). It deliberately does **not** use `@repo_name/...` syntax the way cross-repo YAML references do elsewhere in strata: that convention exists for when there is no separate field disambiguating the repo; here `name` already is that field, so a `@` prefix on `path` would be redundant and could disagree with `name` in a way nothing would catch. `path` is optional — see below for its default and its (deliberately limited) Jinja2 support.

#### Distinguishing artifacts within a shared repo

Once `name` can point multiple record kinds — or multiple workspaces — at the *same* repo, three separate questions need three separate, non-overlapping answers:

1. **Which artifact kind is this?** Answered by `path` itself. Default, when omitted, is the kind's own name (`deploy-log`, `manifest`, `cost`, `drift`) as a subdirectory at the repo root — so kinds sharing a repo don't collide by default, and `path` only needs to be set explicitly for a custom layout. `path` supports the same Jinja2 templating `spec.audit.structure` already does — operators who know `{{ deployment }}/{{ timestamp }}` from `structure` will expect the same `{{ }}` syntax here, and a plain, non-templated `path` sitting right next to a fully-templated `structure` would be a real, needless inconsistency. But the available variables are deliberately narrower than `structure`'s full set: only `{{ tenant }}` and `{{ environment }}` — classification variables that answer *where this kind of artifact lives*, not *which record it is*. `{{ deployment }}`, `{{ timestamp }}`, `{{ date }}`, and `{{ stage }}` are excluded from `path` on purpose: those are exactly what question 2 (below) already provides via the preserved local-relative layout, and allowing them in `path` too would reopen "two structures, no clear precedence" — the problem this design is meant to close, not reproduce one level up. `{{ workspace }}` is excluded from `path` for a second, more concrete reason: it is already inserted automatically and unconditionally (question 3, below); allowing it as a `path` variable too would let an operator duplicate that segment (`history/myws/cost/myws/...`) by writing `{{ workspace }}` into `path` on top of the automatic insertion.
2. **Which record, within a kind, over time?** Not a new setting — the copy preserves the artifact's *existing* local relative layout verbatim. For deploy-log, that's whatever `spec.audit.structure`'s Jinja2 template already produced locally (e.g. `{deployment}/{timestamp}/_execution.json`); for cost/drift, it's their existing per-deployment filename (`{deployment}.cost-history.json`). One structure decision (the existing local one), not two — there is no separate "remote structure" templating language to configure.
3. **Which workspace, when several share one repo?** This is a genuinely new question — it has no equivalent for purely local storage, because a single workspace's own `.strata/` never collides with itself. But `name` pointing multiple workspaces at one shared repo is exactly the scenario this phase is meant to support, and cost/drift's filenames carry no workspace or tenant identity at all today — two unrelated workspaces with a deployment named `prod` would silently overwrite each other's history. Rather than rely on the operator picking a collision-avoiding `spec.audit.structure` variant (`by-workspace`/`by-tenant`/`full` exist, but nothing requires their use, and cost/drift don't have the option at all), the remote copy path always inserts the workspace name, unconditionally:

   ```
   {resolved_repo}/{path}/{workspace}/{local_relative_path}
   ```

   This is a deliberate asymmetry from local storage, which is not workspace-scoped and stays exactly as unconfigured as it is today — the workspace segment exists only on the remote side, because sharing a repo is the one thing that introduces this collision risk in the first place.

#### What Phase 1 deliberately does not do

- **No new record kinds.** This phase only consolidates and extends the *push* mechanism; deciding whether cost, drift, and metrics *should* forward to the HTTP state service at all remains with whichever future ADR wires each one.
- **No local path configuration for cost/drift.** Deliberately not added — local storage stays fixed and predictable, matching today's behaviour; only the remote destination is new configuration surface.
- **No new "remote structure" templating language.** The remote layout is a strict function of the existing local one (see "Distinguishing artifacts," above) — not a second thing to configure.
- **No change to the deploy-log's or manifest's existing local-write behaviour.** Both keep writing exactly where they do today; only the push mechanism underneath changes, and only when `repository.push`/`push_manifest` is actually enabled.
- **No change to version-lock files.** They are already stored directly in a git-backed repo, not a local file with a push bolted on — there is nothing here for them to adopt.
- **Does not touch the HTTP state service.** Phase 2 (below) is independent of this — git-push buys durability, the state service buys queryability, and a record kind can adopt either, both, or neither.

### Phase 2 — ingest endpoint and event store ✅ Done

The end state is a single service exposing one write route, shared by every record kind:

```
POST /v1/events          → 202 Accepted
GET  /healthz             → 200
```

But that end state is not one unit of work, and treating it as one hides a real ordering dependency: there is no such thing as "add the ingest route" before there is a process that runs, listens, and can be reached, and no such thing as "make ingestion idempotent" before there is a database to hold the primary key that idempotency depends on. Phase 2 is therefore seven sequential steps, each a prerequisite for the next, not a single delivery — the first five deliver the core service; the last two (2.6/2.7) are a later, narrower addition once a concrete consumer (the VS Code extension) needed something from it:

| Step | Delivers                                                                                                     | New CLI                                                                                             | Depends on                                                        |
| ---- | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| 2.1  | `serve` command + server skeleton (`/healthz` only, TLS-enforced bind, graceful shutdown)                    | `strata serve run`, `strata serve health <url>`                                                     | Phase 1 (nothing to ingest yet, but nothing here needs it either) |
| 2.2  | Event store — `events` table, schema/migration, DB connection at startup                                     | `strata serve migrate`                                                                              | 2.1 (a process to hold the connection)                            |
| 2.3  | `POST /v1/events` — idempotent, append-only ingest                                                           | none — server-side only                                                                             | 2.2 (the primary key idempotency relies on)                       |
| 2.4  | Authentication — admin-token-protected `/v1/tokens` routes + per-workspace bearer tokens on the ingest route | `strata serve token create\|list\|revoke --url ... --admin-token ...` (HTTP clients, not direct-DB) | 2.3 (a route to protect)                                          |
| 2.5  | Client-side delivery — webhook sink pointed at the endpoint, bounded retry tightening                        | none new — reuses existing `spec.audit.sinks` config                                                | 2.4 (a real, authenticated endpoint to point a sink at)           |
| 2.6  | Minimal `/v1/events/tail` read-only endpoint — last N rows, no filters beyond workspace                      | `strata serve tail <url> --limit 100`                                                               | 2.4 (reuses the same tokens for read scope)                       |
| 2.7  | VS Code extension integration — status bar, token management, tools row, guide step, tail view               | none new (extension only)                                                                           | 2.6 (the tail view's data source)                                 |

The more fundamental point first, because it governs every step below: **whether the state service accepted a record is not, and must never become, a question about whether the underlying action happened.** A terraform apply that succeeds has changed real infrastructure whether or not the forwarded record made it to the state service afterwards; a drift check ran and produced a real answer whether or not that answer got ingested. Ingestion failure is a gap in *our observability of the fact*, never a gap in the fact itself, and no step below is allowed to blur that — e.g. by making ingestion a precondition, a gate, or a required step of the command it is merely reporting on.

#### Step 2.1 — `serve` command and server skeleton ✅ Done

A new command, `strata serve`, following the exact precedent `strata mcp serve` already established: an optional dependency extra (`pip install xyz-strata[server]`), lazily imported so the base install stays free of a web framework and a DB driver, and a clear `ImportError` with the install hint when it's missing.

At this step the server does exactly one thing: bind, respond `200` on `GET /healthz`, and shut down cleanly on `SIGTERM`/`SIGINT`. No `/v1/events` route exists yet, and there is no database — deliberately, so that "does the process start, bind, and stop correctly" can be verified in complete isolation from every other concern below.

TLS enforcement belongs here, not in step 2.4, because it is a property of the process's bind, not of any one route: **the service must refuse to start on a non-loopback bind without TLS.** Bearer-token verification (step 2.4) protects a route; refusing an insecure bind protects the process before any route exists to protect.

`serve` is a new top-level group with exactly two subcommands at this step: `serve run` (the foreground server itself) and `serve health <url>` — a thin CLI wrapper around `GET /healthz`, the same kind of remote-state convenience `deploy lock status` already provides instead of making operators hand-craft a request. **Deliberately no `serve stop`.** The server runs in the foreground, exactly like `strata mcp serve` — lifecycle (start, stop, restart) belongs to whatever launched it (systemd, a container runtime's restart policy, `Ctrl+C`), not to a second CLI invocation tracking a separate process by PID. Adding `stop` would imply a daemon/PID-file model this design does not use, and should not gain by accident later.

**Implemented as designed.** New `server` optional extra (`fastapi>=0.115`, `uvicorn>=0.30`) in `pyproject.toml`, absent from the `dev` group — same as `mcp`, genuinely not installed in the dev/test environment; tests fake the modules instead. `strata/server/config.py` holds `ServerRuntimeConfig`/`validate_bind()`, deliberately framework-free (no `fastapi`/`uvicorn` import) so the loopback-or-TLS rule is unit-testable without the optional dependency. `strata/server/app.py`'s `create_app()` imports `fastapi` only inside the function body and registers exactly `GET /healthz`. `strata serve run` (`commands/cli_serve.py`) is a bare Click function — not a `BaseCommand` subclass, matching `mcp_serve()`'s precedent for a blocking foreground process — validates the bind before ever importing `uvicorn`, then calls `uvicorn.run(app, host=..., port=..., ssl_certfile=..., ssl_keyfile=...)`. `strata serve health` is a real `HealthServeCommand(BaseCommand)`, workspace-optional via `_initialize_session()` (same pattern as `CheckToolsCommand`), using the already-base `requests` dependency — no new dependency needed for it. Verified via full `Check.ps1`: 5554 tests passed (26 new), ruff/mypy/Sphinx all green.

**Implementation decisions, settled:**

- **Framework: FastAPI + uvicorn**, added as `server = ["fastapi>=0.115", "uvicorn>=0.30"]`, following `mcp`'s exact extras precedent (not even installed in the dev venv today — `test_commands_mcp.py` injects a fake module instead — the new `server` extra's tests do the same rather than requiring the real packages). FastAPI is pydantic-native, reusing the dependency strata already carries everywhere, and uvicorn handles graceful `SIGTERM`/`SIGINT` shutdown and TLS termination (`ssl_certfile`/`ssl_keyfile`) itself — nothing hand-rolled for either. Route handlers stay plain `def`, not `async def` — FastAPI runs them in a threadpool, so this does not force the rest of the (synchronous) codebase toward async.
- **Config surface: CLI flags + `STRATA_SERVE_*` env vars, deliberately *not* `spec.state_service`.** The server is consumed by many workspaces (Phase 1's own per-workspace path-insertion design already assumes this), so its bind/TLS config is process/operational config, not one workspace's deployment config — `--host`/`--port`/`--tls-cert`/`--tls-key`, each with an `envvar=` fallback (`STRATA_SERVE_HOST` etc.), matching strata's existing `STRATA_*` convention. Consequence: `serve run`/`serve health` do not require `solution.json` — same workspace-optional `_initialize_session()` override `CheckToolsCommand` already uses, not the full `_initialize()`.
- **Package layout:** `src/strata/server/` (`app.py`'s `create_app()`, `config.py`'s `ServerRuntimeConfig`), `src/strata/commands/serve/` (`RunServeCommand`, `HealthServeCommand`), `src/strata/commands/cli_serve.py` (the `serve` group), registered in `cli.py` next to `mcp_group`.
- **`GET /healthz` payload:** `{"status": "ok"}` on success, or a `503` with `HTTPException(detail=...)` describing the connection failure (added in step 2.2 once there is a database to check). No `version` field — verified against the live response; keeping the payload minimal avoids it becoming a dumping ground for diagnostic fields unrelated to liveness.
- **FastAPI's auto-generated `/docs`, `/redoc`, `/openapi.json` are left enabled** (default behaviour, not disabled) — harmless at this step with only `/healthz` mounted, and useful once step 2.3 adds a real route.
- **`serve health` needs zero new dependencies** — it's a `requests` GET (already a base dependency) against a *remote* server; it works without `pip install xyz-strata[server]` installed locally at all.
- **TLS/loopback rule, mechanically:** `ServerRuntimeConfig.validate_bind()` refuses when `host` is not in `{127.0.0.1, ::1, localhost}` and either `tls_cert` or `tls_key` is missing. `RunServeCommand._execute()` returns `False` with the reason in `self._errors` — falls through to the existing default exit code **1** (system error) in `handle_command_exit`, no new exit-code plumbing needed.


**Done when:** `strata serve run` starts, `curl http://127.0.0.1:.../healthz` (or `strata serve health http://...`) returns `200` for the default zero-config loopback bind — TLS is not required for `127.0.0.1`/`::1`/`localhost`, only enforced for any other bind address, in which case the URL (and flag set) becomes `https://` — the process exits cleanly on `Ctrl+C`, and attempting a non-loopback, non-TLS bind fails fast with a clear error instead of starting insecurely.

#### Step 2.2 — event store: schema and connection ✅ Done

**Three backends, one schema.** SQLite is the zero-config default — friction-free local/dev use, no external service, no driver beyond Python's own stdlib `sqlite3`. PostgreSQL and SQL Server are supported, opt-in production backends. Maintaining three hand-written dialect-specific SQL files for one logical schema is exactly the kind of drift risk this ADR's own "payload verbatim, promote later" philosophy argues against — so the schema is defined once, in **SQLAlchemy Core** (`Table`/`MetaData`, not the ORM layer), and rendered per-dialect by SQLAlchemy itself:

```python
events = Table(
    "events", metadata,
    Column("execution_id", String, nullable=False),
    Column("record_type", String, nullable=False),   # the envelope's CloudEvents `type` string, verbatim
                                                       # (ADR-0066's full event-type enum, e.g.
                                                       # "xyz.huybrechts.strata.deployment.completed") —
                                                       # see step 2.3's correction below
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),

    # promoted dimensions: bounded cardinality, indexed, safe as labels
    Column("deployment", String), Column("workspace", String), Column("environment", String),
    Column("tenant", String), Column("ring", String), Column("action", String), Column("outcome", String),

    Column("strata_version", String),
    Column("payload", JSON, nullable=False),          # the complete record, verbatim

    PrimaryKeyConstraint("execution_id", "record_type"),
)
Index("idx_events_recorded_at", events.c.recorded_at.desc())
Index("idx_events_slice", events.c.deployment, events.c.environment, events.c.recorded_at.desc())
```

`payload` uses SQLAlchemy's generic `JSON` type — renders JSONB-equivalent storage on Postgres, `NVARCHAR(MAX)` with automatic (de)serialization on SQL Server, `TEXT` on SQLite. No JSON-path querying is needed at this step (that's Phase 3's read API); the type only needs to round-trip a dict faithfully, which all three do. The promoted columns are still precisely ADR-0064's `label_safe` set, for the same bounded-cardinality reasoning as before — a resource address or a monthly total lives in `payload`, never as its own column, regardless of which of the five record kinds or three backends it comes from.

Storing the **complete record verbatim in `payload`** is still what neutralises schema-churn risk across all five record kinds at once, unchanged from the original design. A new field added to any of them lands in `payload` and is immediately available, with no migration and no coordination between runner and server versions. Promotion to a typed column happens later and only when a query needs an index — and it can be backfilled from `payload` at that point, because the data was never discarded.

**Idempotency: insert-then-catch-duplicate, not dialect-specific upsert SQL.** `ON CONFLICT DO NOTHING` (Postgres, SQLite) has no equivalent on SQL Server without a `MERGE` statement per insert — three different SQL shapes for one concept. Simpler and fully portable: attempt a plain `INSERT`; catch the resulting integrity/duplicate-key error (SQLAlchemy normalises this to one `IntegrityError` across all three dialects); treat it as a no-op. Idempotency here exists for correctness on retry/replay, not hot-path throughput, so the dialect-free approach wins outright — there is no performance case for the more complex per-dialect upsert yet, and if one ever appears, it can be optimised later without changing the schema.

**Connection config:** `--db-url` / `STRATA_SERVE_DB_URL`, same CLI-flag-plus-envvar shape as `--host`/`--port`/`--tls-*` (ADR's own "operational config for a standalone process, not workspace config" reasoning from step 2.1 applies unchanged). Default `sqlite:///./strata-state.db` — zero configuration needed to start. Production backends select via SQLAlchemy's own URL scheme: `postgresql+psycopg://...`, `mssql+pyodbc://...`.

**Dependency layout — sqlite is free, postgres/mssql are opt-in extras:**

```toml
server          = ["fastapi>=0.115", "uvicorn>=0.30", "sqlalchemy>=2.0"]
server-postgres = ["psycopg[binary]>=3.1"]
server-mssql    = ["pyodbc>=5.0"]
```

`pip install xyz-strata[server]` alone is enough to run against SQLite. Postgres/SQL Server each need their own compiled driver installed via a second extra — no reason to force a C extension or an ODBC driver on someone who only wants to try the server locally. Unlike `fastapi`/`uvicorn` (kept genuinely absent from the dev/test venv, per step 2.1's own precedent), `sqlalchemy` **is** added to the `dev` dependency-group — it needs no external service or compiled driver to exercise against SQLite, so real tests against a real (temp-file or in-memory) SQLite database are strictly better than hand-faking an entire query-building API. Postgres/SQL Server connection paths get structural tests only (URL scheme → correct dialect/engine), the same "can't hit the real service in CI" treatment this codebase already gives cloud integrations (Azure Key Vault, AWS, etc.) — mocked, not connected.

Schema application gets its own command, `strata serve migrate`, run separately from `serve run` — not folded into server startup. This is a deliberate privilege split, not just convenience: `migrate` is the one place anything needs `CREATE TABLE`/`ALTER TABLE` rights, run once by an operator or a CI step with elevated DB credentials, while `serve run`'s own long-lived connection only ever needs `INSERT`/`SELECT` on an already-existing table. An internet-facing process holding schema-modification privileges for its entire lifetime is exactly the kind of standing-privilege footprint worth avoiding by construction, not by later hardening. `metadata.create_all(engine, checkfirst=True)` implements "create if not exists" identically across all three backends — no dialect-specific migration files to maintain even at this step.

`/healthz` now also runs `SELECT 1` through the configured engine, so a database outage is visible the same way a process-down outage already was in step 2.1 — the handler raises a `503 HTTPException` with the failure detail, never a silent `200`.

**Implemented as designed.** New `src/strata/server/db/` package: `schema.py` (the `MetaData`/`Table` above, raising a clear `ImportError` install hint if `sqlalchemy` is missing, same pattern as `strata.mcp.server`), `engine.py` (`create_engine_from_url()` — validates the URL's backend name against the three supported dialects before constructing the engine; `check_connection()` — the `SELECT 1` liveness check, catching broadly since it must never raise past `/healthz`), `store.py` (`insert_event()` — the insert-then-catch-`IntegrityError` idempotency helper). `strata serve migrate` (`commands/serve/migrate_serve_command.py`, a real `BaseCommand`, workspace-optional like `serve health`) calls `metadata.create_all(engine, checkfirst=True)` and disposes its own connection afterward — short-lived, unlike `serve run`'s. `serve run` gained `--db-url`/`STRATA_SERVE_DB_URL` (default `sqlite:///./strata-state.db`), builds the engine before `create_app(engine)`, and `/healthz` now raises `HTTPException(503, detail=...)` on a failed `SELECT 1`. `sqlalchemy` added to the `dev` dependency-group (test-only, real SQLite in tests — `psycopg`/`pyodbc` deliberately are not, so the postgresql/mssql dialect-selection tests patch `sqlalchemy.create_engine` itself rather than requiring either driver); `server`/`server-postgres`/`server-mssql` extras added to `pyproject.toml`. Verified via full `Check.ps1`: 5573 tests passed (19 new), ruff/mypy/Sphinx all green.

**Done when:** `strata serve migrate --db-url sqlite:///./x.db` creates the `events` table and exits `0`, and running it again against the same file is a no-op; `strata serve run` started afterward connects successfully and only ever issues `INSERT`/`SELECT`; `strata serve migrate --db-url postgresql+psycopg://...` and `--db-url mssql+pyodbc://...` build a correctly-dialected engine (verified structurally, not against a live server in CI); taking the database away makes `/healthz` return `503` rather than the server silently accepting requests it has nowhere to put.

#### Step 2.3 — ingest endpoint ✅ Done

**Correction to step 2.2's schema: `record_type` is not the five artifact kinds.** Tracing the actual delivery path (`AuditController.forward()` → `_build_envelope()` → any sink, including the webhook sink step 2.5 points at this endpoint) shows the body arriving here is **always the CloudEvents 1.0 + ECS envelope** `_build_envelope()` builds for every event — never a raw artifact dump. Its `type` field is `"xyz.huybrechts.strata.<event_type>"`, where `event_type` is ADR-0066's full closed enum (~20 values: `deployment.completed`, `workitem.approved`, `policy.violated`, `cost.threshold_exceeded`, ...) — finer-grained than, and not a clean many-to-one mapping onto, the five-artifact-kind list step 2.2 originally assumed (`workitem.*`/`lock.*`/`policy.violated` don't correspond to any of the five at all). Forcing that mapping would invent structure this ADR argues against having. **Resolution: `record_type` = the envelope's own `type` string, verbatim** — simpler, no mapping table to maintain, and already the exact granularity ADR-0066 settled on.

Adds the actual write route: `POST /v1/events`. Idempotency (`(execution_id, record_type)`, from step 2.2) needs both fields pulled out of the envelope, not read top-level:

| Column                                                | Source in the envelope           | Required?                                                                                                                                                         |
| ----------------------------------------------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `execution_id`                                        | `data.labels.execution_id`       | Yes — `400` if missing                                                                                                                                            |
| `record_type`                                         | `type`                           | Yes — `400` if missing                                                                                                                                            |
| `recorded_at`                                         | `time` (ISO 8601)                | No — falls back to server-received time if absent/unparseable                                                                                                     |
| `deployment` / `workspace` / `environment` / `tenant` | `data.labels.*`                  | No                                                                                                                                                                |
| `action` / `outcome`                                  | `data.event.*`                   | No                                                                                                                                                                |
| `ring`, `strata_version`                              | —                                | **Not populated** — `_build_envelope()` doesn't emit either today; columns stay `NULL` until a future producer-side change adds them (out of scope for this step) |
| `payload`                                             | the **whole envelope**, verbatim | —                                                                                                                                                                 |

Storing the full envelope (not just the inner `data.strata` payload) in `payload` is deliberate — it's the only place `user.name` (who did it) and `event.kind`/`category` (alert vs. plain event) exist; discarding them for a slimmer `payload` would throw away exactly the audit value this service exists to keep.

**Idempotency is mandatory, not optional, from this step's first line of code.** Duplicate delivery is **certain**, not hypothetical, for two concrete reasons: best-effort forwarding has no delivery confirmation, and a resend/replay path (`strata audit resend` today; the equivalent for drift/cost once they adopt forwarding) exists specifically to re-forward records that failed the first time. The primary key on `(execution_id, record_type)` from step 2.2, used with step 2.2's insert-then-catch-duplicate helper, makes replay a no-op for every record kind uniformly. Without it, one resend after an ingest outage would silently inflate deployment frequency (or drift-check counts, or cost snapshots) and corrupt every ratio derived from it — the kind of defect that is invisible in the data and only discovered when someone questions a dashboard months later. `execution_id` is already unique per run for audit records (`AuditController.generate_execution_id()`); drift and cost history would need an equivalent stable identifier per snapshot (e.g. `deployment` + `recorded_at`, or a generated UUID at snapshot time) — a small addition to each store, not a new concept, and still a producer-side gap (see the `ring`/`strata_version` note above) rather than something this step can fix from the server side.

**Append-only.** There are no update or delete routes, for any record kind. Records are immutable facts about events that have already happened. Corrections, if ever needed, are new records — never mutations of old ones. Retention enforcement is an operator-run job against the database, not an API surface.

**Route shape: raw bytes, not a pydantic body model — stays a plain `def`, matching step 2.1's no-`async def` precedent.**

```python
@app.post("/v1/events", status_code=202)
def ingest_event(request: Request, body: bytes = Body(...)) -> Dict[str, Any]:
    if _content_too_large(request, body):
        raise HTTPException(413, "Payload too large")
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"Malformed JSON: {exc}") from exc
    if not isinstance(envelope, dict):
        raise HTTPException(400, "Body must be a JSON object")
    try:
        row = extract_row(envelope)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    try:
        insert_event(engine, row)
    except Exception as exc:
        raise HTTPException(503, f"Insert failed: {exc}") from exc
    return {"status": "accepted"}
```

`body: bytes = Body(...)` (not a pydantic model) is what keeps the handler a plain `def` — FastAPI reads the body for you asynchronously and hands the already-read bytes to your (possibly synchronous) function; no `await` needed in the handler itself. Content-length is checked against a cap (256 KiB — matches Phase 2's own "no large blobs" exclusion) before `json.loads` ever runs, so an oversized body is never even parsed.

**Status codes chosen to match the webhook client's own, already-existing retry logic** (`base_siem_integration.py`'s `_post_json()`: `resp.status_code < 500` → no retry, confirmed in code, not assumed): `202` on success, including duplicate no-ops — idempotency is invisible to the caller, per step 2.2. `400`/`413` for anything the client itself sent wrong — correctly never retried, since retrying a malformed payload would never help. `503` only if the *database* write itself fails — so a real outage **is** retried by the existing webhook mechanism, the same convention `/healthz` already established in step 2.2.

**Server-side delivery semantics: respond immediately.** Validate shallowly (well-formed JSON, required identity fields present, size limit), insert, return `202`. No aggregation, no enrichment, no fan-out on the request path — that discipline is what keeps this step's contribution to command latency bounded regardless of how many record kinds eventually flow through it.

**Implemented as designed.** New `src/strata/server/db/ingest.py`: `extract_row(envelope)` — the mapping table above, raising `ValueError` for the two required identity fields, letting the route translate that into `400`. `app.py`'s `create_app(engine)` gained the `/v1/events` route exactly as shown, plus a small `_content_too_large()` helper checking both the `Content-Length` header and the actual body length (defends against a missing/incorrect header).

**One implementation correction found along the way: `fastapi` moved from a lazy in-function import (step 2.1/2.2's pattern) to a real module-scope import in `app.py`, guarded by the same try/except-with-install-hint `schema.py` already uses.** FastAPI resolves route parameter annotations (`Request`, `Body(...)`) via the handler function's own `__globals__` — for a nested route function defined inside `create_app()`, that's always the *module's* globals, never the enclosing factory function's locals. A lazy `from fastapi import Request, Body` inside `create_app()` would leave those names unresolvable to FastAPI's real signature inspection at request-handling time, even though the fake-module tests (which call route handlers directly, bypassing FastAPI's own dependency injection) couldn't have caught it. `/healthz` never exercised this risk (no parameter annotations reference FastAPI types), which is why step 2.1/2.2 didn't need to fix it — step 2.3's `Request`/`Body(...)` parameters are the first to.

Verified via full `Check.ps1`: 5591 tests passed (18 new), ruff/mypy/Sphinx all green.

**Done when:** a hand-crafted `POST` with a valid envelope-shaped payload gets a `202` and a row in `events`; a byte-for-byte duplicate (same `execution_id` + `record_type`) gets a `202` and no second row; a payload missing `type` or `data.labels.execution_id` gets a `400`; an oversized payload gets a `413`; a database failure during insert gets a `503`, never a silent `202` or an unhandled `500`.

#### Step 2.4 — authentication ✅ Done

Bearer token via the sink's existing `headers` map, issued **per workspace** so that tokens can be attributed and revoked individually. Tokens grant append-only ingest and nothing else — they cannot read, cannot query, and cannot delete. A leaked runner token lets an attacker write junk records, which is bad but bounded and detectable; it does not expose deployment, drift, or cost history. Verification happens before the route's body is even parsed — an unauthenticated request never reaches step 2.3's idempotency or validation logic.

**Token management goes through the running server's own HTTP API, not direct database access — a correction made during design review.** The first version of this step put `serve token create/list/revoke` on the same footing as `serve migrate`: a direct-DB CLI command. That reasoning didn't actually transfer: `migrate` needs genuinely elevated `CREATE TABLE`/`ALTER TABLE` rights the long-lived server must never hold — that privilege split is real. Issuing a token is just `INSERT`/`SELECT`/`UPDATE` on one table, the *same* privilege level `serve run` already has. Keeping it as a direct-DB command would mean every operator managing tokens needs two separate connections/credentials (DB *and* HTTP) — exactly the fragmentation this whole phase exists to remove — and creates a bootstrap problem where getting a workspace its (lower-privilege) ingest token first requires a *higher*-privilege raw DB grant.

**Two credential classes, deliberately separate — an admin credential and per-workspace ingest tokens:**
- **Admin token** — `--admin-token` / `STRATA_SERVE_ADMIN_TOKEN`, configured at `serve run` startup, same CLI-flag-plus-envvar shape as everything else in step 2.1. Guards new `/v1/tokens` routes: `POST /v1/tokens` (create), `GET /v1/tokens` (list), `DELETE /v1/tokens/{token_id}` (revoke). **If not provided, these routes are not registered at all** — no accidental unauthenticated admin surface by default. Compared via `hmac.compare_digest` (a genuine raw string comparison this time, unlike ingest-token verification below, so constant-time comparison actually matters here).
- **Per-workspace ingest tokens** — created via the admin routes above, persisted (as a SHA-256 hash only) in a new `tokens` table, guard `POST /v1/events` via the same bearer-token mechanism. Verification is a DB *equality lookup* on the hash, not a raw string compare, so there is no timing-attack surface to add `hmac.compare_digest` for.

`serve migrate` remains the one genuine direct-DB exception — it must run before any table (including `tokens`) exists at all, so it structurally cannot go through an HTTP API that has nothing to talk to yet. Everything else, including token issuance, now goes through the same interface every other client uses.

`serve token create --url <server> --admin-token ... --workspace <name>` (prints the token once, never stored or retrievable in plaintext again — the server keeps only a hash), `serve token list --url ... --admin-token ...` (identifiers/creation dates, never the token itself), and `serve token revoke <id> --url ... --admin-token ...` — all thin HTTP clients, the same shape as `serve health`, not `serve migrate`.

**New table: `tokens`** (`token_id`, `token_hash`, `workspace`, `created_at`, `revoked_at`) — deliberately mutable (revocation), unlike `events`. This doesn't violate step 2.3's append-only principle: that principle is specifically about the audit-fact table, not about access-control housekeeping data, which is a different concept entirely. Token secrets are generated via the existing `strata.utils.secret_generator.generate_secret("urlsafe", 32)` (~256 bits of entropy) and hashed with `hashlib.sha256` — the same hashing convention already used elsewhere in this codebase (cache keys, content digests) — not bcrypt/argon2, which exist specifically to slow down brute-forcing *low-entropy, human-chosen* secrets; that doesn't apply to a machine-generated token.

**Implemented as designed.** New `src/strata/server/db/tokens.py`: `create_token()`, `list_tokens()`, `revoke_token()`, `verify_token()`. `app.py` gained `verify_ingest_token`/`verify_admin_token` (nested closures, same pattern as `healthz`/`ingest_event`, wired via FastAPI's `dependencies=[Depends(...)]` so an auth failure never reaches the route's own body), and the three `/v1/tokens*` routes (registered only when `admin_token` is passed to `create_app()`). `cli_serve.py` gained `--admin-token` on `serve run` and a new `serve token` subgroup (`create`/`list`/`revoke`) as HTTP clients. New command classes `CreateTokenServeCommand`/`ListTokensServeCommand`/`RevokeTokenServeCommand` (`commands/serve/`).

Verified via full `Check.ps1`: 5625 tests passed (34 new), ruff/mypy/Sphinx all green.

**Done when:** an unauthenticated or wrong-token `POST /v1/events` is rejected (`401`/`403`) before touching the database, a correctly-authenticated `POST` succeeds exactly as step 2.3 already verified, and a token revoked via `serve token revoke` is rejected on its very next use.

#### Step 2.5 — client-side delivery ✅ Done

**Correction: the ADR's original framing of this step is stale.** It describes `_send_webhook()` as "a single attempt with no retry at all," worth "tightening." That function no longer exists — ADR-0066 already replaced it with `WebhookSiemIntegration`, which inherits `SiemBaseIntegration._post_json()`: a real retry loop (`_MAX_RETRIES = 3`, `_RETRY_BACKOFF = 1.0` doubling per attempt, retrying only 5xx/network errors, never 4xx). So the actual gap isn't "add retry" — retry already exists — it's that those constants are **hardcoded module globals**, identical for every sink, when the right amount of retry is genuinely different for a third-party SIEM (no replay path, worth retrying harder) versus this first-party state service (resend/replay already exists, so retrying hard just delays the command for no real recovery benefit).

**Fix: make retry configurable per-sink, via the config surface every SIEM integration already has** — `config.properties`, read through the existing `self._prop(key, default)` helper (already used for `headers`/`allow_insecure`; no schema change needed):

```python
max_retries = max(1, int(self._prop("max_retries", _MAX_RETRIES)))
backoff = float(self._prop("retry_backoff_seconds", _RETRY_BACKOFF))
```

**And the defaults themselves change, for every sink, not just newly-configured ones — `max_retries: 1` (no retry at all by default), `retry_backoff_seconds: 10`.** This is a deliberate, broader simplification, not a state-service-only carve-out: the same "resend already exists as the real recovery path" argument that motivates this for the state service has always applied equally to Sentinel/Splunk/ELK/OTel — `strata audit resend` (ADR-0066) already covers re-forwarding a record that failed to reach *any* sink, so the aggressive 3-attempt/45-second-worst-case retry was never actually buying anything a real outage couldn't already recover from via resend, for any sink. `retry_backoff_seconds: 10` still matters even with `max_retries: 1` as the *default* — it's what a sink gets automatically the moment an operator raises `max_retries` above 1, without also having to remember to set a sane backoff. This is a real, existing-behaviour-affecting change: any test asserting a specific retry count against the *old* defaults needs to configure `properties.max_retries` explicitly to keep testing what it was actually testing (retry-loop correctness), not the old default value.

**A second, independent correctness detail found while wiring the actual example: use `authentication.method: oauth2`, not `api_key`, to reach the ingest token from step 2.4.** `_build_auth_headers()` has two branches: `api_key` sends `{header_name: value}` **verbatim** — reaching `Authorization: Bearer <token>` that way would require the operator to store the literal string `"Bearer <token>"` as the secret value, an easy-to-forget footgun. `oauth2` already builds `{"Authorization": f"Bearer {token}"}` **automatically** from `oauth2.client_secret` (an env-var/secret reference holding just the raw token) — exactly the shape step 2.4's ingest-token verification expects, with zero new auth code. (The field name "client_secret" is a minor semantic mismatch — this isn't a real OAuth2 client-credentials flow, just a convenient reuse of the one existing auth branch that already auto-prefixes `Bearer` — worth a one-line callout in the worked example, not worth adding a new auth method for.)

**Worked example:**

```yaml
integrations:
  - name: strata-ingest
    type: webhook
    capabilities: [audit]
    endpoints:
      address: https://state-service.internal/v1/events
    authentication:
      method: oauth2
      oauth2:
        client_secret: "${secret:strata_ingest_token}"   # from `strata serve token create`
    properties:
      max_retries: 1              # the new default — no retry; resend is the real recovery path
      retry_backoff_seconds: 10   # only relevant if an operator raises max_retries above 1

spec:
  audit:
    sinks:
      - name: state-service
        integration: strata-ingest
```

With the new defaults, a hard-down state service (connection hanging, not refused) costs one sink's worth of `_REQUESTS_TIMEOUT` (15s) per forwarding command — down from the old worst case of ~45s of timeout plus ~3s of backoff across 3 attempts — and exactly the same for every other sink type unless an operator explicitly opts back into more attempts.

**Implemented as designed.** `SiemBaseIntegration._post_json()` (and `SplunkSiemIntegration._post_raw()`, which had its own separate, previously-hardcoded retry loop — found and fixed identically) now read `max_retries`/`retry_backoff_seconds` via `self._prop(...)`, floored at 1 attempt minimum. Module defaults changed from `_MAX_RETRIES = 3`/`_RETRY_BACKOFF = 1.0` to `_MAX_RETRIES = 1`/`_RETRY_BACKOFF = 10.0`. Three existing tests (`test_base_siem_integration.py`, two in `test_splunk_siem_integration.py`) that asserted the old 3-attempt default now explicitly configure `properties.max_retries` to keep testing retry-loop *mechanics*, not the changed default value. New tests added covering the no-retry default, the floor-at-1 guard, and backoff-value honouring. Docs updated: `docs/guides/siem-audit-forwarding.md`'s "Retry Behavior" section (was describing the old hardcoded 3-attempt behaviour) and a new "Webhook / strata state-service Reference" section with the `oauth2`-not-`api_key` worked example.

Verified via full `Check.ps1`: 5628 tests passed (3 new), ruff/mypy/Sphinx all green.

**Done when:** a workspace configured with this sink, run against a live server, produces a row in `events` from a real `deploy run`; killing the server mid-command costs at most one `_REQUESTS_TIMEOUT` window, never a failed command; `properties.max_retries`/`retry_backoff_seconds` are honoured when explicitly configured higher, and every existing retry-loop test is updated to configure them explicitly rather than relying on the now-changed defaults.

#### Step 2.6 — minimal read-tail endpoint ✅ Done

Phase 3's fuller read API is still deliberately deferred — real query patterns haven't emerged yet. But there is one already-known, narrow need, driven by the VS Code extension (step 2.7): a lightweight, human-facing "tail" of recent activity — the same shape `tail -f`/`kubectl logs -f` already provide, not a query surface. Scoping it precisely as "the last N rows, no filters beyond workspace, no date ranges, no aggregation" keeps it out of Phase 3's territory: it answers exactly one question ("what happened most recently"), not the open-ended "what happened when, for which resource" Phase 3 exists to answer once real usage reveals the actual query shapes needed.

**New route:** `GET /v1/events/tail?limit=100&workspace=<name>`.

**Auth reuses the same two credential classes step 2.4 already established, with read access scoped exactly like write access already is:** a per-workspace ingest token can only tail its own workspace — a `workspace` query param, if present, is ignored/overridden to the token's own scope, so there is no cross-workspace leakage through a "read" door that write access doesn't have. The admin token can tail any workspace, or omit the filter for the full cross-workspace tail. No new credential type — same `tokens` table, same bearer-token mechanism, one new `verify_read_access()` dependency alongside the existing `verify_ingest_token`/`verify_admin_token`, returning either a workspace scope (ingest token) or `None` (admin, unrestricted).

`limit` defaults to 100 (matching the ask) and is capped server-side at a fixed maximum regardless of what the client requests — the same size-discipline reasoning `_MAX_BODY_BYTES` already applies to ingest, applied here to response size instead.

**Response is a lean projection, not the full stored payload** — `execution_id`, `record_type`, `recorded_at`, `received_at`, `workspace`, `deployment`, `environment`, `action`, `outcome`. The full `payload` JSON blob stays available (once Phase 3 exists) for anyone who needs to inspect a specific record in full; a tail view showing 100 rows needs a log-line summary, not 100 complete envelopes.

**Ordering: `received_at` ascending** (oldest of the N first) — the same order a real `tail` shows lines in, and the order a UI can safely append to the bottom of a scrolling list without re-sorting. `received_at`, not `recorded_at`, because it's insertion order — the property a "what just happened" tail actually needs, and it stays stable even when two records share the same `recorded_at`.

Worked example:

```
GET /v1/events/tail?limit=100
Authorization: Bearer <ingest-or-admin-token>

200 OK
{
  "events": [
    {"execution_id": "...", "record_type": "xyz.huybrechts.strata.deployment.completed",
     "recorded_at": "...", "received_at": "...", "workspace": "my-workspace",
     "deployment": "my-deploy", "environment": "prd", "action": "deploy-run", "outcome": "success"}
  ]
}
```

New code: `src/strata/server/db/query.py` (`list_recent_events(engine, workspace=None, limit=100)`), a `resolve_read_scope()` helper in `app.py` (called directly from the route body, not via `dependencies=[]`, since its return value — the workspace scope — is needed by the handler, unlike the fire-and-forget `verify_ingest_token`/`verify_admin_token` checks), and the `GET /v1/events/tail` route — registered unconditionally, same as `/v1/events`, guarded per-request rather than gated on `admin_token` presence like `/v1/tokens`. A new CLI passthrough, `strata serve tail <url> --token ... --limit 100 --output json` (`TailServeCommand`), gives step 2.7's extension integration a CLI-shaped way to call it, matching `serve health`'s existing thin-HTTP-client precedent rather than the extension talking to the server directly.

**Implemented as designed.** `list_recent_events()` orders by `received_at` descending under `LIMIT` (to actually get the most recent rows), then reverses to ascending order for the response. `resolve_read_scope()` checks the admin token first (if configured), falling through to an ingest-token lookup via the existing `verify_token()` — exactly the same two-credential-class shape step 2.4 established. `_MAX_TAIL_LIMIT = 500` caps the response regardless of the requested `limit`.

**This is a narrow, deliberate exception to "What Phase 2 deliberately excludes: No read API" below** — one fixed-shape endpoint for one known consumer, not a general query surface. Phase 3's fuller read API (filters, date ranges, aggregation, first-party `metrics`/`trends` commands) remains deferred exactly as before.

Verified via full `Check.ps1`: 5660 tests passed (21 new), ruff/mypy/Sphinx all green.

**Done when:** `GET /v1/events/tail` returns the most recent `limit` (default 100, capped) events for the caller's authorized scope, ordered oldest-to-newest by `received_at`, excluding the full payload; an ingest token cannot see another workspace's events via this route even if a `workspace` query param requests it; and the admin token can tail across all workspaces.

#### Step 2.7 — VS Code extension integration ✅ Done

**Architecture, unchanged from every other extension feature:** the extension talks to the server exclusively through the CLI — `strata serve health`/`serve token create|list|revoke`/`serve tail` — via `StrataClient._run()`'s existing spawn-and-parse-JSON pattern, never a direct HTTP call from Node. This keeps exactly one code path (the CLI) responsible for talking to the server, consistent with the rest of the extension's design (every other feature, including the closest existing precedent, `deploy health`, already works this way).

**Status bar — a second, independent item, shown only when configured.** New `StateServiceStatusBarProvider`, mirroring `StatusBarProvider`'s existing loading/healthy/degraded/broken state machine, gated on a new `strata.stateService.url` setting (empty by default — same gating pattern `showStatusBar` already uses). Polls `serve health` on an interval (`strata.stateService.pollIntervalSeconds`, default 60, minimum 10 — same shape as the existing `workItemPollIntervalSeconds`). States: `$(sync~spin)` loading, `$(radio-tower)` ok, `$(warning)` unreachable, `$(error)` explicit failure (e.g. a `503` from a database outage). Click opens the tail view below — the natural next action after "is it up" is "what is it seeing."

**Token management**, via three command-palette actions (`strata.stateService.createToken`/`.listTokens`/`.revokeToken`), Quick Pick-driven the same way `strata.manageRefs` already is. **The admin token is never written to `settings.json`** — first use prompts via `vscode.window.showInputBox({ password: true })` and stores it in `context.secrets` (VS Code's `SecretStorage` API — a new pattern for this extension, nothing uses it today, but the only correct place for a credential this sensitive). Two housekeeping commands, `strata.stateService.setAdminToken`/`.clearAdminToken`, cover updating/removing it, since there is no settings UI for a secret.

**Tools view row — correction during implementation: there is no separate "Tools view".** `toolsViewProvider.ts` turned out to be unused, superseded, dead code — the real Tools section is a collapsible group inside `WorkspaceHealthProvider._buildTools()` (the `strataWorkspace` tree view). The state-service row was added there instead: one extra row, appended after the CLI-provided tool rows, shown only when a URL is configured, reusing the status bar item's own health check via a new `setStateServiceStatus(url, reachable)` setter — rather than polling independently a second time, exactly as designed.

**Guide view step** — a new step in `guideViewProvider.ts`'s existing walkthrough, appearing once a workspace's `deploy-*.yaml` is detected forwarding to a state-service-shaped webhook sink (step 2.5's worked example) — nudging the operator to set `strata.stateService.url` so the indicator/tail view has something to point at, the same "detect config, suggest the next step" pattern the guide already uses elsewhere.

**Tail view**, `strata.stateService.showTail`, opens (or reveals) a dedicated VS Code `OutputChannel` ("Strata: State Service Tail") and polls `serve tail` on the same interval as the status bar item, appending only rows newer than the last-seen `received_at` (tracked client-side) — a real `tail -f`-like experience without the server needing any streaming/websocket support, matching step 2.6's deliberately simple, poll-friendly design. An `OutputChannel`, not a webview, because it's a plain scrolling log of one-line summaries — no richer surface than the data shape needs.

**Command palette actions, in full:** `Strata: Check State Service Health`, `Strata: Show State Service Tail`, `Strata: Create Ingest Token`, `Strata: List Ingest Tokens`, `Strata: Revoke Ingest Token`, `Strata: Set State Service Admin Token`, `Strata: Clear State Service Admin Token`.

**New settings:**

```jsonc
"strata.stateService.url": { "type": "string", "default": "", "description": "Base URL of a running strata state-service server (ADR-0065). Leave empty to disable the status bar indicator, tools row, and tail view." },
"strata.stateService.pollIntervalSeconds": { "type": "number", "default": 60, "minimum": 10, "description": "How often the state-service status bar item and tail view poll the server." }
```

**Scoping limit, explicit:** Phase 3 (read API) is still deferred, so nothing here can browse deployment/cost/drift history beyond the last-100-rows tail step 2.6 provides — no filtering by date range, no per-resource drill-down, no aggregation. That is intentional; a fuller history browser is a Phase 3 consumer, not part of this step.

**Implemented as designed** (with the tools-view correction above). New `providers/stateServiceStatusBarProvider.ts` (second status bar item, `onStatus()` callback so `WorkspaceHealthProvider` and the guide hint reuse one poll) and `providers/stateServiceTailProvider.ts` (`OutputChannel`-backed poller, client-side de-dup via last-seen `received_at`). `strataClient.ts` gained `getServerHealth()`/`getServerTail()`/`createIngestToken()`/`listIngestTokens()`/`revokeIngestToken()`, each a thin `strata serve ...` CLI passthrough. Seven new commands registered in `extension.ts`; the admin token is read/written exclusively through `context.secrets` (`_getStateServiceAdminToken()`), never a setting. The guide-view nudge does a best-effort plain-text scan of `deploy*.yaml` files for `/v1/events` (not full YAML parsing — matches this step's deliberately simple scope) to decide whether to show the setup hint. Config changes to `strata.stateService.url`/`pollIntervalSeconds` restart the status bar's polling live, same pattern as the existing `strata.cliPath` change handler.

Verified via `tsc -p ./` (clean compile) — the extension has no existing per-provider unit test suite to extend (only a broad smoke test), consistent with the rest of the codebase's test coverage for this package.

**Done when:** the second status bar item reflects a running server's real health and is hidden entirely when `strata.stateService.url` is empty; token management round-trips create → list → revoke entirely through Quick Picks with the admin token stored only in `SecretStorage`, never in a setting; the Tools row in the workspace health view shows the same reachability the status bar does; the guide view surfaces the setup nudge when a state-service webhook sink is detected; and the tail view shows new rows appearing within one poll interval of a live `deploy run` against a configured server.

#### What Phase 2 deliberately excludes

- **No general-purpose read API.** Operators query the database directly. This is the point — SQL is a better and more widely supported interface than any REST API we would design, and shipping one now would freeze a query model before we know the questions, for any of the five record kinds. **Step 2.6's `/v1/events/tail` is a narrow, deliberate exception** — one fixed-shape, size-capped, non-queryable endpoint built for one known consumer (the VS Code tail view), not a general query surface; it does not change this conclusion for anything beyond "the last N rows."
- **No UI.** Grafana, Metabase, and Power BI all speak SQL already.
- **No aggregation.** Deployment frequency, change failure rate, MTTR, drift duration, and cost trend are all `GROUP BY` queries, not endpoints.
- **No large blobs.** Terraform plan JSON and SBOM documents are referenced by digest, not inlined. Inlining them turns the state service into an artifact store — a different product with different retention economics and different security review. Payloads are size-capped, and oversized records are rejected with a clear error rather than silently truncated.
- **No execution.** The service receives records. It does not run strata.
- **No coordination state.** Locks and work items are explicitly out of scope (see Decision Drivers) — they already have a pluggable-backend answer via ADR-0007/0057 and have different consistency requirements (mutable, exclusive) than this service's append-only model.

#### Command-by-command review — where else this could help

The scope of this ADR is deliberately narrow: **the list below, the unified git-push mechanism (Phase 1) and HTTP ingest server (Phase 2), above, and audit records as the first tenant.** Every other row is a candidate, not a commitment — each one that gets picked up gets its own ADR to work out the record shape, the forward-call site, and the volume question, the same way this ADR itself only found drift/cost history by going looking. What follows is that search, done once, systematically, across every command group, so the candidates are enumerated in one place instead of being rediscovered piecemeal.

Three questions decide each row: does the command produce a **fact that already happened** (a candidate); is that fact currently **invisible once the runner that produced it is gone** (the actual gap); and is it something **a second runner, or an operator later, would plausibly want to ask about across many runs** (worth the corpus). A "no" to any of the three is a reason to leave a command out, not an oversight.

| Command group                                                                   | What happens today                                                                                                                                                                                          | Could the state service help?                                                                                                                                                                                              | Verdict                                                                                                                                                                                                  |
| ------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `audit` (`changes`/`resend`/`status`/`export`/`diff`)                           | Deploy-log read/write/forward, already local + optionally git-pushed                                                                                                                                        | This is the seed tenant                                                                                                                                                                                                    | **Already covered — Phase 1 (git-push) and Phase 2 (state-service forwarding) both already wired**                                                                                                       |
| `cost` (`show`/`diff`/`history`)                                                | `cost show`/`diff` are live estimator calls; `history` reads `.strata/cost/*.cost-history.json`, local-only, no remote option                                                                               | Same shape as deploy-log: a snapshot that happened once, needed across runners for trend questions ("is this stack getting more expensive")                                                                                | **Fully wired — Phase 1 git-push, `cost.threshold_exceeded` alert (ADR-0066 follow-up), and `cost.recorded` (the full snapshot, forwarded unconditionally on every `cost show`) all forward now**        |
| `deploy drift`                                                                  | Reads/writes `.strata/drift/*.drift.json`, local-only, no remote option                                                                                                                                     | Same shape again: "has this resource been drifting for a week" is unanswerable once the runner that detected it is gone                                                                                                    | **Fully wired — Phase 1 git-push, `drift.detected` alert (ADR-0066 follow-up), and `drift.recorded` (the full snapshot, forwarded unconditionally on every `deploy drift` check) all forward now**       |
| `deploy run` / `deploy destroy`                                                 | Already the deploy-log's two producers (`deployment.completed`/`deployment.destroyed`)                                                                                                                      | Already covered                                                                                                                                                                                                            | **In scope — Phase 2, already wired via `AuditController.forward()`**                                                                                                                                    |
| `manifest` (query/export)                                                       | Reads `{build_path}/{deployment}/manifest.json`; the record kind is already named in Phase 2's schema (`deployment-manifest`); Phase 1 git-push goes through the unified `AuditController.push_to_remote()` | Same shape as deploy-log/cost/drift: a fact worth correlating across runs (which artifact hash actually shipped, when)                                                                                                     | **Fully wired — `manifest.recorded` forwards unconditionally once per deploy/destroy from `BaseDeployCommand._forward_manifest_recorded_event()`, alongside (and independent of) the existing git-push** |
| `promote` (rings/waves)                                                         | A promotion decision is recorded only in the version-lock file it writes to a git-backed `versions_path`, and in whatever local log the runner kept                                                         | A promotion is exactly a deploy-log-shaped fact — who promoted what, from which ring, to which version, when — currently answerable only by walking git history by hand                                                    | **Strong future candidate — new record kind, own ADR**                                                                                                                                                   |
| `rollout` (fleet-wide, multi-deployment)                                        | Coordinates many deployments from a single invocation or pipeline; overall progress ("14 of 20 done, 2 failed") lives only in that one runner's process memory/output                                       | A rollout is the single strongest case in this whole review — its own defining feature is spanning more than one deployment, so its status is structurally unavailable to any other runner without a shared store          | **Strongest future candidate — own ADR, probably before promote**                                                                                                                                        |
| `policy` (`check`)                                                              | Standalone policy evaluation; when run via `validate`/`build`/`deploy` it already produces `policy.violated` (ADR-0066)                                                                                     | A standalone `policy check` run reuses the same event shape — no new record kind needed                                                                                                                                    | **Confirmed already covered — `CheckPolicyCommand` calls the same shared `BaseCommand._forward_policy_violation_audit_event()` every other policy-evaluating command uses**                              |
| `workitem` (approve/reject/complete/cancel/list/show/expire)                    | Resolution *events* already forward through `AuditController.forward()` (ADR-0066 gap A); the *live pending item* itself is stored in a pluggable remote backend (S3/Blob/GCS/git-tag, ADR-0057)            | The history side is already covered by the audit mechanism; the live/pending side is coordination state with an existing, working answer                                                                                   | **Events in scope (already wired); live state explicitly out of scope — already solved, don't duplicate**                                                                                                |
| `deploy lock`                                                                   | Coordination state via a pluggable remote lock backend (S3/GCS/Azure RM/Consul/TFC, ADR-0007)                                                                                                               | None — this is a mutable, exclusive resource; an append-only store cannot represent "who holds the lock right now"                                                                                                         | **Out of scope — already solved, different consistency model (see Decision Drivers)**                                                                                                                    |
| `log` (`list`/`config`)                                                         | Reads the per-invocation journal, `.strata/audit.log`, local-only NDJSON                                                                                                                                    | Same local-only shape as the other five, but `command.executed` already defaults **off** in the closed enum specifically because of dev-loop/CI-polling volume (ADR-0066)                                                  | **Low value as-is — would need the same class-aware volume discipline ADR-0066 already established before it's worth a record kind, not a new mechanism**                                                |
| `sln status` / `sln doctor`-style health checks                                 | Point-in-time, local-workspace-only                                                                                                                                                                         | A fleet inventory ("which deployments exist, across every workspace this org runs") is a real, currently-unanswerable question, but it is a **read** question over the corpus this ADR already builds, not a new **write** | **Phase 3 read-API consumer, not a new Phase 2 producer**                                                                                                                                                |
| `status` (top-level quick status)                                               | Local-workspace-only snapshot                                                                                                                                                                               | Same as `sln status` — becomes more useful once Phase 3's read API exists, no new record kind required                                                                                                                     | **Phase 3 read-API consumer, not a new Phase 2 producer**                                                                                                                                                |
| `cache` (`warm`/`status`/`clear`/`export`)                                      | The resolved-model cache (ADR-0026) — explicitly local and fully rebuildable by design                                                                                                                      | None — this is the precedent this ADR's own projection invariant is modelled on; forwarding cache entries centrally would be actively wrong, not merely unnecessary                                                        | **Explicitly out of scope**                                                                                                                                                                              |
| `secret` / `values` (inspect/generate/resolve)                                  | Secret stores already produce their own, more rigorous native audit trails (Vault, Key Vault, Bitwarden); `secret.accessed` is deliberately unwired for this exact reason (ADR-0066)                        | None beyond what ADR-0066 already declined for the same reasons — the store already audits access better than strata could                                                                                                 | **Out of scope — same reasoning as `secret.accessed`'s existing decision**                                                                                                                               |
| `vars` / `versions` (team-shared vars, version locks)                           | Already committed to git (`solution.json`, version-lock files) — durable by construction                                                                                                                    | None — there is no gap to fill; git already is the durable store for these                                                                                                                                                 | **Out of scope — already durable**                                                                                                                                                                       |
| `validate`                                                                      | No side effect — produces no artifact, nothing changes on disk or in infrastructure                                                                                                                         | None — matches `validation.completed`'s existing "not needed" conclusion (ADR-0066) for the identical reason                                                                                                               | **Out of scope — already decided, same logic applies here**                                                                                                                                              |
| `repo` / `profile` / `ref` / `config` / `tools`                                 | Workspace setup and inspection ergonomics (add a repo, activate a profile, check a tool is installed)                                                                                                       | None identified — no recurring "did this happen, how often, what changed" question worth accumulating a corpus for                                                                                                         | **Out of scope — no history worth keeping**                                                                                                                                                              |
| `new` / `init` / `schema` / `help` / `completion` / `guide` / `mcp` / `console` | One-off scaffolding, developer ergonomics, or purely local interactive tooling                                                                                                                              | None                                                                                                                                                                                                                       | **Out of scope**                                                                                                                                                                                         |

**`cost.recorded`/`drift.recorded` implemented — closing the two gaps this review itself found.** Two new closed-enum event types (`event` class, default on), forwarded unconditionally on every snapshot — unlike the existing `cost.threshold_exceeded`/`drift.detected` alerts, which only fire on a threshold breach or detected drift, these fire every time, since they *are* the history record Step 2.2's schema was built for, not an anomaly signal. `CostController._forward_cost_recorded_event()` (called from `_record_history_snapshot()`, alongside the existing alert forward) and `DriftDeployCommand._forward_drift_recorded_event()` (called unconditionally in `_run_drift_detection()`, where the alert forward is conditional on `report.has_drift`) both use a **deterministic** `execution_id` — `sha256(deployment:recorded_at)` for cost, `sha256(deployment:checked_at)` for drift — rather than a fresh UUID or (for drift) the command's own `self._execution_id`: the record's identity is the snapshot itself, so a resend of the same snapshot must be a no-op under Step 2.3's idempotent primary key, regardless of which invocation re-sends it.

**`manifest.recorded` implemented too — the last open gap from this table is now closed.** `BaseDeployCommand._forward_manifest_recorded_event()`, called unconditionally right after `_write_deployment_manifest()` writes (and optionally git-pushes) the manifest, independent of whether git-push is configured — the two delivery mechanisms are orthogonal, same as cost/drift. Unlike cost/drift's recorded events, this one uses the command's own `self._execution_id` directly, not a derived hash: a manifest is written exactly once per deploy/destroy invocation, so it's the same correlation key `deployment.completed`/`deployment.destroyed` already use for events from the same run, and a resend of the persisted record keeps that same id. Since `DeploymentManifestModel` is a nested (`meta`/`spec`) model, unlike `DeployLogModel`'s flat one, the payload is built as `manifest.model_dump(mode="json")` with `deployment`/`workspace`/`environment` promoted to the top level afterward — the envelope's field lookups (`_build_envelope()`) need those flat, the rest of the manifest stays nested and complete underneath.

Verified via full `Check.ps1`: 5639 tests passed (2 new), ruff/mypy/Sphinx all green. Every producer gap this review found is now closed.

### Phase 3 — read API and first-party queries ⏳ deferred

Once real query patterns emerge from operators using SQL directly — across any of the five record kinds — promote the recurring ones into a read API and into first-party commands (`strata metrics dora`/`strata metrics show` per ADR-0064 Phase B; equally plausible future commands like `strata drift trends` or `strata cost trends`), pointed at the store instead of local files.

Deferred deliberately: designing the read model before observing the queries is how APIs acquire endpoints nobody uses.

### Phase 4 — control plane ⏳ future

The capabilities a CLI structurally cannot provide — approvals that outlive a process (ADR-0032), locks held across machines (ADR-0007), scheduled drift detection, and an authorization model for who may deploy where — all require durable central state plus a long-lived process. Phase 2 provides the first (for history); note that locks and work items already have their own durable backends today and do not wait on this ADR. Phase 4 adds the long-lived process that could, for example, run scheduled drift checks and write their results through the same ingest path Phase 2 defines.

If that service ever executes deployments, it should **spawn the strata CLI as a subprocess** rather than importing strata as a library. The codebase is built around one-process-one-workspace assumptions — process-scoped token caches, cwd-relative work-path discovery, a global logger, and lifecycle scripts that read `STRATA_PHASE` from the environment. Process exit is also what bounds secret lifetime: the SSH-key pattern writes a `chmod 600` temp file and deletes it when the subprocess ends, whereas an in-process runner would accumulate resolved secrets from every workspace in one long-lived heap. Version pinning follows for free — different workspaces on different strata versions are just different images.

Worth stating plainly, because it is easy to assume otherwise: a subprocess is **not** a security boundary. Same UID, same filesystem, same network. If Phase 4 is ever multi-tenant across trust boundaries, the real isolation unit is a container or VM per run; the process split buys lifecycle hygiene, not isolation.

## Consequences

### Good

- **Near-zero CLI change to adopt for audit** — the `webhook` sink already exists; onboarding a workspace is a YAML edit, and the only code change is a sink-failure log level (already done, ADR-0066)
- **Small, well-understood extension for drift/cost** — both already have a local append-only store with an identical shape problem; adopting the same forward-on-write call `AuditController.forward()` already provides is additive, not a new subsystem
- **Unblocks ADR-0064, and the equivalent question for drift/cost** — Phase B/C both need a corpus; this is the corpus, and it accumulates from the day it is switched on, for every record kind that adopts it
- **Widest possible consumer interface** — SQL works with Grafana, Metabase, Power BI, notebooks, and `psql`, with no API to learn or version, regardless of record kind
- **Small credential surface** — append-only bearer tokens on runners; no database credentials leave the server
- **Schema churn absorbed uniformly** — verbatim JSON payload means new fields, in any of the five record kinds, need no migration and no runner/server version coordination
- **The control plane becomes incremental** — Phase 4 inherits a populated store with real history instead of launching against an empty database
- **Composes rather than replaces** — sinks are a list; Splunk and OTLP forwarding continue unchanged alongside it, and coordination state (locks, work items) keeps its own, already-solved backends untouched

### Neutral

- **Another service to operate** — a process, a database, TLS, and backups. Justified only for teams that actually want cross-workspace history; single-workspace users should keep using the local files for all five kinds
- **Eventually consistent by construction** — best-effort delivery means the store may lag or miss records until a resend; it is a measurement system, not an accounting ledger, for any record kind it holds
- **Direct SQL access is the API in Phase 2** — deliberate, but it does mean the physical schema is visible to consumers earlier than a REST design would expose it
- **Drift and cost history gain a new optional dependency on `AuditController`'s forwarding path** — reasonable, since it is already the shared, tested mechanism, but it does mean those two local-only stores are no longer fully independent of the audit subsystem's configuration surface once they opt in

### Risk

- **The store gets mistaken for the source of truth** — someone builds a compliance or cost-reporting process that assumes every deployment/drift-check/cost-check is present
  - Mitigation: the projection invariant is documented here and must be repeated wherever the store is described; a resend/replay path is the reconciliation mechanism for every record kind — but only for record kinds whose source has a durable store configured (see below); for the rest, reconciliation against "local files" is reconciliation against nothing, because the runner deleted them
- **Reconstructability is assumed for a record kind that has no durable source** — it is easy to read the projection invariant as an unconditional guarantee ("the state service can always be rebuilt") rather than the conditional one it actually is. Deploy-log is only durable-by-replay because `spec.audit.repository` exists and is configured; drift and cost history have no equivalent option yet, so forwarding them to the state service today creates a projection with no verifiable source to project *from* — the exact inversion the invariant is meant to prevent
  - Mitigation: this ADR states the conditionality explicitly (see "The projection invariant"); drift/cost forwarding should be treated as provisional, and documented as such, until they gain their own durable-store option (Open Questions)
- **Ingest outage silently loses records** — commands succeed, records vanish, and nobody notices until a dashboard looks wrong. This is worse than it first appears: sink failures were historically logged at **`debug`** level, so under any normal log configuration the loss was entirely invisible
  - Mitigation: sink-delivery failures are raised to `warning` (ADR-0066) so they are observable by default. Beyond that, gaps are detectable by reconciling the store against the local files, and closed with a resend command
- **Schema becomes a de-facto public API** — Phase 2 hands operators direct SQL, so any column rename breaks their dashboards, across all five record kinds
  - Mitigation: promoted columns are restricted to the stable `label_safe` set; everything volatile stays in `payload`; a versioned view layer can be added if churn materialises
- **Duplicate records corrupt aggregates** — the highest-impact failure mode, because it is invisible, and now applies to drift/cost aggregates too, not only deployment frequency
  - Mitigation: the composite primary key makes replay a no-op; this is a correctness requirement, not an optimisation, for every record kind
- **Scope creep into an artifact store** — pressure to inline plan JSON and SBOMs will recur
  - Mitigation: hard payload size limit with explicit rejection; blobs referenced by digest only
- **Scope creep into coordination state** — pressure to also route locks or work-item approvals through this same service will recur, since "it's already there"
  - Mitigation: this ADR explicitly excludes coordination state (see Decision Drivers and Phase 2 exclusions); locks and work items keep their existing, purpose-built pluggable backends (ADR-0007/0057), which have mutation and exclusivity semantics this append-only store does not provide

## Open questions

1. **Database engine for the reference implementation** — Resolved: **start with `sqlite3`**, matching the ADR-0026 precedent (stdlib, no new dependency) and sufficient for a single-team fleet. Postgres and SQL Server are both kept in mind as documented upgrade paths, not implemented alongside SQLite from day one — `JSONB`/native `JSON` support and concurrent-writer behaviour differ enough between the two that the storage layer must stay behind an interface (mirroring `BaseLockBackend`/`BaseWorkItemBackend`'s pluggable-backend precedent) rather than assume SQLite's semantics leak through. The write-concurrency ceiling of SQLite under many simultaneous CI runners (now writing up to five record kinds instead of three) still needs measuring before it's trusted as the default for anything beyond a single-team fleet; that measurement, not a hypothetical, is what should trigger the Postgres/SQL Server path rather than switching pre-emptively.
2. **Does the state service ship inside the strata package, or as a separate deployable?** Resolved: **in-package, behind an optional extra** — the same pattern strata already uses for `mcp` (`[project.optional-dependencies] mcp = ["mcp>=1.0"]`, `pyproject.toml`). A `state` extra (`xyz-strata[state]`) carries FastAPI/uvicorn/the DB driver; `strata serve state` is a real command in the same package, but the module lazily imports the web-framework dependencies so a plain `pip install xyz-strata` never pulls them in and CLI startup is unaffected. Running the command without the extra installed fails with a clear "install `xyz-strata[state]`" message rather than an import error at CLI load time. This was chosen over both a fully separate package (Terraform CLI vs. Terraform Cloud/Enterprise is the industry example, but that split tracks a commercial-tier boundary strata doesn't have) and an unconditional in-package dependency (would force the web framework on every install, including CI runners that only ever emit records). Same repo, same version, same release cadence as the CLI — no second distribution to keep in sync.
3. **Retention and rotation policy** — narrowed from ADR-0064 OQ-3: retention is not a uniform concern across all three storage tiers this ADR touches, and only one of them is actually open.
   - **Local files** (`.strata/cost/`, `.strata/drift/`, `.strata/metrics/deployments.ndjson`, `.strata/audit.log`, `.strata/deploy-log/`) — not a gap this ADR needs to close. On the disposable runner this whole ADR is framed around, the files never accumulate long enough to matter. Unbounded growth is only a real risk on a persistent workspace (a long-lived dev checkout, a self-hosted runner that's never recycled), and that's a pre-existing, independent question for each local store to answer on its own — orthogonal to whether the state service exists at all.
   - **Git-backed durable copy** (Phase 1) — not a gap either. Git is already the retention mechanism: content-addressed, compressed, and repo hygiene (squash, shallow clone, archive) is the operator's existing lever. This mirrors the deploy-log's git-push (ADR-0018), which has never needed a retention story of its own.
   - **State-service database** — this is the tier where retention is actually load-bearing, and it's the only one left open. Unlike the other two, it's *centralized* (aggregates every workspace in the fleet, not one workspace's local disk) and *queryable* (Phase 2's entire point is `GROUP BY` performance, which degrades as the table grows unbounded). "No retention" isn't neutral here the way it is for git. The mechanism is already decided — an operator-run purge job against the database, no delete API, consistent with the append-only/immutable-history invariant — what remains open is the *window* per record type (deploy-log vs. cost vs. drift may reasonably differ), which is a business/compliance question the reference implementation should leave configurable rather than hardcode.
4. **Should `build.completed` records be ingested?** Resolved, narrower than a flat yes/no: **ingest, but gated on CI-resolved actor, not on profile.** Profile only answers "which environment" — it doesn't touch the actual volume driver ADR-0064 flagged, which is dev-loop iteration (a developer re-running `strata build run` while debugging a module has nothing to do with which profile is active). The filter that actually controls volume is CI vs. local, and it's already computed: ADR-0066's actor resolution (cloud CLI identity → CI actor → OS login) already distinguishes a CI-triggered run from an interactive one — gate on "actor resolved as CI," not on a new field. Once filtered that way, there is one piece of knowledge this record type uniquely provides: deploy-log only ever records a deploy *attempt*, so if a build fails, no deploy attempt happens and deploy-log has a structural blind spot upstream of itself — "how often do CI builds fail before a deploy is even attempted" is otherwise unanswerable from the corpus at all. Build duration trend (is the build getting slower as state/module count grows) is a secondary, weaker benefit. Artifact provenance ("which build produced this artifact hash") is deliberately *not* this record's job — that's what `deployment-manifest` forwarding already answers (see the command-by-command review above) — so the payload should stay lean (outcome, duration, actor, environment) rather than duplicate manifest content.
5. **Multi-workspace token model** — per-workspace tokens are proposed above, but issuance, rotation, and revocation have no home yet; that likely arrives with Phase 4's identity model rather than Phase 2. That identity model — OIDC/OAuth2 login, session/token issuance, and an authorization model for who may deploy where (both named in Phase 4 above) — is settled separately in [ADR-0067](0067-server-identity-authentication-authorization.md), not an extension of this one's bearer-token design: a CI runner authenticating with a static append-only token and a human authenticating to a control-plane UI are different problems with different threat models. ADR-0066's `actor` resolution (cloud CLI identity → CI actor → OS login) is CLI-side and unaffected either way; ADR-0067 settles that a control-plane session outranks it when one exists.
6. **Does `DriftHistoryStore`/cost history need their own `forward()` call sites, or a shared helper?** `AuditController.forward()` already exists and is call-site agnostic — it takes an `event_type` and a payload dict. The smallest change is almost certainly a best-effort forward call added directly inside `record_snapshot()`/the drift equivalent, mirroring the pattern `_forward_lock_audit_event()`/`_forward_drift_audit_event()` already established for lock and drift *events* (as opposed to drift/cost *history snapshots*, which is what this ADR is actually about) — this needs a short implementation pass once the state service itself exists, not a design decision this ADR needs to pre-resolve.
7. **Do drift/cost history need their own durable-store option before forwarding them to the state service is meaningful?** Resolved — this is now Phase 1, above: one unified `AuditController.push_to_remote()`, given a `repo_name`/`remote_path` shape that actually places files inside the target repo instead of assuming they're already there, with `ManifestController.push_to_remote()` and its dead `type`/`repository`/`branch` fields retired in favour of it.
8. **Should `spec.audit` be renamed now that it is not audit-only?** This ADR deliberately keeps reusing `spec.audit.sinks`/`AuditController` as the delivery mechanism for drift and cost history, which means a section named "audit" ends up routing non-audit history too. Renaming `spec.audit` to something broader (e.g. `spec.telemetry` or `spec.state`) is a real naming question this revision surfaces but does not resolve — it touches a large, recently-stabilised surface (ADR-0066) and is better decided once drift/cost forwarding is actually implemented and the awkwardness is concrete rather than hypothetical.
