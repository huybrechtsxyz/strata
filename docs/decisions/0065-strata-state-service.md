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

### Phase 1 — unify durable git-push storage

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

### Phase 2 — ingest endpoint and event store

A single service exposing one write route, shared by every record kind:

```
POST /v1/events          → 202 Accepted
GET  /healthz             → 200
```

The body is whatever the sink sent — a `DeployLogModel`, a deployment manifest, an ADR-0064 metrics record, a drift-history snapshot, or a cost-history snapshot. Record type is discriminated by the payload's `kind` (or inferred for legacy deploy-log payloads, which predate a `kind` field).

#### Storage schema — typed dimensions, JSON payload

One table, deliberately, covering every record kind:

```sql
CREATE TABLE events (
    execution_id    TEXT        NOT NULL,
    record_type     TEXT        NOT NULL,   -- deploy-log | deployment-manifest | deployment-metrics
                                             -- | drift-history | cost-history
    recorded_at     TIMESTAMPTZ NOT NULL,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- promoted dimensions: bounded cardinality, indexed, safe as labels
    deployment      TEXT,
    workspace       TEXT,
    environment     TEXT,
    tenant          TEXT,
    ring            TEXT,
    action          TEXT,
    outcome         TEXT,

    strata_version  TEXT,
    payload         JSONB       NOT NULL,   -- the complete record, verbatim

    PRIMARY KEY (execution_id, record_type)
);

CREATE INDEX idx_events_recorded_at ON events (recorded_at DESC);
CREATE INDEX idx_events_slice       ON events (deployment, environment, recorded_at DESC);
```

The promoted columns are precisely ADR-0064's `label_safe` set, and they apply unchanged to drift and cost history too — a resource address or a monthly total lives in `payload`, never as its own column, for exactly the same bounded-cardinality reasoning. That is not a coincidence: the same discipline that keeps `commit_sha` out of Prometheus labels keeps it out of indexed columns here, regardless of which of the five record kinds it comes from.

Storing the **complete record verbatim in `payload`** is what neutralises schema-churn risk across all five kinds at once. A new field added to any of them lands in `payload` and is immediately queryable via a JSON path expression, with no migration and no coordination between runner and server versions. Promotion to a typed column happens later and only when a query needs an index — and it can be backfilled from `payload` at that point, because the data was never discarded.

#### Idempotency is mandatory

Duplicate delivery is **certain**, not hypothetical, for two concrete reasons: best-effort forwarding has no delivery confirmation, and a resend/replay path (`strata audit resend` today; the equivalent for drift/cost once they adopt forwarding) exists specifically to re-forward records that failed the first time.

The primary key on `(execution_id, record_type)` with insert-on-conflict-ignore makes replay a no-op for every record kind uniformly. Without it, one resend after an ingest outage would silently inflate deployment frequency (or drift-check counts, or cost snapshots) and corrupt every ratio derived from it — the kind of defect that is invisible in the data and only discovered when someone questions a dashboard months later.

`execution_id` is already unique per run for audit records (`AuditController.generate_execution_id()`); drift and cost history would need an equivalent stable identifier per snapshot (e.g. `deployment` + `recorded_at`, or a generated UUID at snapshot time) — a small addition to each store, not a new concept.

#### Append-only

There are no update or delete routes, for any record kind. Records are immutable facts about events that have already happened. Corrections, if ever needed, are new records — never mutations of old ones. Retention enforcement is an operator-run job against the database, not an API surface.

#### Delivery semantics — protecting the command path

The more fundamental point first, because everything else in this section is just consequences of it: **whether the state service accepted the record is not, and must never become, a question about whether the underlying action happened.** A terraform apply that succeeds has changed real infrastructure whether or not the forwarded record made it to the state service afterwards; a drift check ran and produced a real answer whether or not that answer got ingested. Ingestion failure is a gap in *our observability of the fact*, never a gap in the fact itself, and no future version of this service should be allowed to blur that — e.g. by making ingestion a precondition, a gate, or a required step of the command it is merely reporting on.

Two properties matter, one on each side, and both generalise unchanged from the original ADR:

- **Server: respond immediately.** Validate shallowly (well-formed JSON, required identity fields present, size limit), insert, return `202`. No aggregation, no enrichment, no fan-out on the request path.
- **Client: already correct for audit, and the same discipline applies to drift/cost — best-effort, and bounded, but not needlessly lossy.** `_send_webhook()` uses a 10-second `urllib` timeout and swallows failures; today that is a single attempt with no retry at all. Worth tightening, not loosening: a **small, bounded retry — one immediate retry, purely for a clearly transient failure** (connection reset, a single dropped packet) **and only that** — costs almost nothing and turns a network blip into a delivered record instead of a silently missing one. This is different from resilience against a sustained outage, which is explicitly not the goal (see below) — the retry is for the failure mode that is over by the time you'd notice it, not for the failure mode that needs `resend`. So an ingest outage still costs a deploy, build, drift check, or cost check at most ~10–20 seconds and nothing else — no failed command, no changed exit code, ever.

That handful of seconds is nonetheless a real cost worth stating plainly, and now applies to more command types: with the state service hard-down (connection hanging rather than refused), every deploy *and* every drift check *and* every cost check pays the full timeout (times up to two attempts) if forwarding is enabled for all three. This is acceptable, but it is the reason ingest must never be given a real retry *loop*, or backoff, on any of those paths — one bounded extra attempt for a transient blip is the entire concession. Recovery from anything longer than that is a resend command, run after the fact, which already exists for audit and is already idempotent under the primary key above.

This is also a second, independent reason to reach the state service through the **built-in `webhook` sink rather than as a new `ISiemSink` integration**, for every record kind. Integration-backed sinks share `SiemBaseIntegration`'s transport, which uses `requests` with `_REQUESTS_TIMEOUT = 15`, `_MAX_RETRIES = 3`, and `_RETRY_BACKOFF = 1.0` doubling per attempt — roughly 45 seconds of timeout plus ~7 seconds of backoff against a hard-down endpoint, on every command that forwards. That retry behaviour is correct for a third-party SIEM whose delivery we cannot replay, but wrong for a first-party store that has resend/replay as a first-class recovery path. Cheap-and-lossy plus explicit replay beats expensive-and-persistent on the command path.

#### Authentication

Bearer token via the sink's existing `headers` map, issued **per workspace** so that tokens can be attributed and revoked individually. Tokens grant append-only ingest and nothing else — they cannot read, cannot query, and cannot delete. A leaked runner token lets an attacker write junk records, which is bad but bounded and detectable; it does not expose deployment, drift, or cost history.

TLS is required. The service must refuse to start on a non-loopback bind without it.

#### What Phase 2 deliberately excludes

- **No read API.** Operators query the database directly. This is the point — SQL is a better and more widely supported interface than any REST API we would design, and shipping one now would freeze a query model before we know the questions, for any of the five record kinds.
- **No UI.** Grafana, Metabase, and Power BI all speak SQL already.
- **No aggregation.** Deployment frequency, change failure rate, MTTR, drift duration, and cost trend are all `GROUP BY` queries, not endpoints.
- **No large blobs.** Terraform plan JSON and SBOM documents are referenced by digest, not inlined. Inlining them turns the state service into an artifact store — a different product with different retention economics and different security review. Payloads are size-capped, and oversized records are rejected with a clear error rather than silently truncated.
- **No execution.** The service receives records. It does not run strata.
- **No coordination state.** Locks and work items are explicitly out of scope (see Decision Drivers) — they already have a pluggable-backend answer via ADR-0007/0057 and have different consistency requirements (mutable, exclusive) than this service's append-only model.

#### Command-by-command review — where else this could help

The scope of this ADR is deliberately narrow: **the list below, the unified git-push mechanism (Phase 1) and HTTP ingest server (Phase 2), above, and audit records as the first tenant.** Every other row is a candidate, not a commitment — each one that gets picked up gets its own ADR to work out the record shape, the forward-call site, and the volume question, the same way this ADR itself only found drift/cost history by going looking. What follows is that search, done once, systematically, across every command group, so the candidates are enumerated in one place instead of being rediscovered piecemeal.

Three questions decide each row: does the command produce a **fact that already happened** (a candidate); is that fact currently **invisible once the runner that produced it is gone** (the actual gap); and is it something **a second runner, or an operator later, would plausibly want to ask about across many runs** (worth the corpus). A "no" to any of the three is a reason to leave a command out, not an oversight.

| Command group                                                                   | What happens today                                                                                                                                                                                                                                         | Could the state service help?                                                                                                                                                                                              | Verdict                                                                                                                                                   |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `audit` (`changes`/`resend`/`status`/`export`/`diff`)                           | Deploy-log read/write/forward, already local + optionally git-pushed                                                                                                                                                                                       | This is the seed tenant                                                                                                                                                                                                    | **Already covered — Phase 1 (git-push) and Phase 2 (state-service forwarding) both already wired**                                                        |
| `cost` (`show`/`diff`/`history`)                                                | `cost show`/`diff` are live estimator calls; `history` reads `.strata/cost/*.cost-history.json`, local-only, no remote option                                                                                                                              | Same shape as deploy-log: a snapshot that happened once, needed across runners for trend questions ("is this stack getting more expensive")                                                                                | **In scope — named in this ADR already, needs a Phase 1 git-push call site and a Phase 2 `forward()` call site**                                          |
| `deploy drift`                                                                  | Reads/writes `.strata/drift/*.drift.json`, local-only, no remote option                                                                                                                                                                                    | Same shape again: "has this resource been drifting for a week" is unanswerable once the runner that detected it is gone                                                                                                    | **In scope — named in this ADR already, needs a Phase 1 git-push call site and a Phase 2 `forward()` call site**                                          |
| `deploy run` / `deploy destroy`                                                 | Already the deploy-log's two producers (`deployment.completed`/`deployment.destroyed`)                                                                                                                                                                     | Already covered                                                                                                                                                                                                            | **In scope — Phase 2, already wired via `AuditController.forward()`**                                                                                     |
| `manifest` (query/export)                                                       | Reads `{build_path}/{deployment}/manifest.json`; the record kind is already named in Phase 2's schema (`deployment-manifest`); already has Phase 1 git-push via `ManifestController` (soon to be unified) but nothing forwards it to the state service yet | Same shape as deploy-log/cost/drift: a fact worth correlating across runs (which artifact hash actually shipped, when)                                                                                                     | **In scope for Phase 2 — schema already accounts for it, just needs a `forward()` producer, same as drift/cost**                                          |
| `promote` (rings/waves)                                                         | A promotion decision is recorded only in the version-lock file it writes to a git-backed `versions_path`, and in whatever local log the runner kept                                                                                                        | A promotion is exactly a deploy-log-shaped fact — who promoted what, from which ring, to which version, when — currently answerable only by walking git history by hand                                                    | **Strong future candidate — new record kind, own ADR**                                                                                                    |
| `rollout` (fleet-wide, multi-deployment)                                        | Coordinates many deployments from a single invocation or pipeline; overall progress ("14 of 20 done, 2 failed") lives only in that one runner's process memory/output                                                                                      | A rollout is the single strongest case in this whole review — its own defining feature is spanning more than one deployment, so its status is structurally unavailable to any other runner without a shared store          | **Strongest future candidate — own ADR, probably before promote**                                                                                         |
| `policy` (`check`)                                                              | Standalone policy evaluation; when run via `validate`/`build`/`deploy` it already produces `policy.violated` (ADR-0066)                                                                                                                                    | A standalone `policy check` run reuses the same event shape — no new record kind needed, only a question of whether the standalone command should also call `forward_policy_violation()`                                   | **Likely already covered by `policy.violated` — confirm the standalone command forwards too, no new ADR needed**                                          |
| `workitem` (approve/reject/complete/cancel/list/show/expire)                    | Resolution *events* already forward through `AuditController.forward()` (ADR-0066 gap A); the *live pending item* itself is stored in a pluggable remote backend (S3/Blob/GCS/git-tag, ADR-0057)                                                           | The history side is already covered by the audit mechanism; the live/pending side is coordination state with an existing, working answer                                                                                   | **Events in scope (already wired); live state explicitly out of scope — already solved, don't duplicate**                                                 |
| `deploy lock`                                                                   | Coordination state via a pluggable remote lock backend (S3/GCS/Azure RM/Consul/TFC, ADR-0007)                                                                                                                                                              | None — this is a mutable, exclusive resource; an append-only store cannot represent "who holds the lock right now"                                                                                                         | **Out of scope — already solved, different consistency model (see Decision Drivers)**                                                                     |
| `log` (`list`/`config`)                                                         | Reads the per-invocation journal, `.strata/audit.log`, local-only NDJSON                                                                                                                                                                                   | Same local-only shape as the other five, but `command.executed` already defaults **off** in the closed enum specifically because of dev-loop/CI-polling volume (ADR-0066)                                                  | **Low value as-is — would need the same class-aware volume discipline ADR-0066 already established before it's worth a record kind, not a new mechanism** |
| `sln status` / `sln doctor`-style health checks                                 | Point-in-time, local-workspace-only                                                                                                                                                                                                                        | A fleet inventory ("which deployments exist, across every workspace this org runs") is a real, currently-unanswerable question, but it is a **read** question over the corpus this ADR already builds, not a new **write** | **Phase 3 read-API consumer, not a new Phase 2 producer**                                                                                                 |
| `status` (top-level quick status)                                               | Local-workspace-only snapshot                                                                                                                                                                                                                              | Same as `sln status` — becomes more useful once Phase 3's read API exists, no new record kind required                                                                                                                     | **Phase 3 read-API consumer, not a new Phase 2 producer**                                                                                                 |
| `cache` (`warm`/`status`/`clear`/`export`)                                      | The resolved-model cache (ADR-0026) — explicitly local and fully rebuildable by design                                                                                                                                                                     | None — this is the precedent this ADR's own projection invariant is modelled on; forwarding cache entries centrally would be actively wrong, not merely unnecessary                                                        | **Explicitly out of scope**                                                                                                                               |
| `secret` / `values` (inspect/generate/resolve)                                  | Secret stores already produce their own, more rigorous native audit trails (Vault, Key Vault, Bitwarden); `secret.accessed` is deliberately unwired for this exact reason (ADR-0066)                                                                       | None beyond what ADR-0066 already declined for the same reasons — the store already audits access better than strata could                                                                                                 | **Out of scope — same reasoning as `secret.accessed`'s existing decision**                                                                                |
| `vars` / `versions` (team-shared vars, version locks)                           | Already committed to git (`solution.json`, version-lock files) — durable by construction                                                                                                                                                                   | None — there is no gap to fill; git already is the durable store for these                                                                                                                                                 | **Out of scope — already durable**                                                                                                                        |
| `validate`                                                                      | No side effect — produces no artifact, nothing changes on disk or in infrastructure                                                                                                                                                                        | None — matches `validation.completed`'s existing "not needed" conclusion (ADR-0066) for the identical reason                                                                                                               | **Out of scope — already decided, same logic applies here**                                                                                               |
| `repo` / `profile` / `ref` / `config` / `tools`                                 | Workspace setup and inspection ergonomics (add a repo, activate a profile, check a tool is installed)                                                                                                                                                      | None identified — no recurring "did this happen, how often, what changed" question worth accumulating a corpus for                                                                                                         | **Out of scope — no history worth keeping**                                                                                                               |
| `new` / `init` / `schema` / `help` / `completion` / `guide` / `mcp` / `console` | One-off scaffolding, developer ergonomics, or purely local interactive tooling                                                                                                                                                                             | None                                                                                                                                                                                                                       | **Out of scope**                                                                                                                                          |

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

1. **Database engine for the reference implementation** — SQLite is sufficient for a single-team fleet and matches the ADR-0026 precedent of stdlib `sqlite3` with no new dependency; Postgres is the obvious answer for concurrent writers and JSONB indexing. Shipping SQLite-first with a documented Postgres path is likely right, but the write-concurrency ceiling of SQLite under many simultaneous CI runners (now writing up to five record kinds instead of three) needs measuring before committing.
2. **Does the state service ship inside the strata package, or as a separate deployable?** In-package means `strata serve state` and zero extra distribution; separate means the CLI does not carry a web framework it never uses in the common case.
3. **Retention and rotation policy** — inherited unresolved from ADR-0064 OQ-3, and now a server-side concern spanning all five record kinds: per-record-type retention, partition-by-month, or operator-run purge. Drift and cost history may reasonably want different retention windows than deploy-logs.
4. **Should `build.completed` records be ingested?** ADR-0064 excludes builds from the metrics series for good reasons (dev-loop volume, nothing changed outside the output directory). The audit policy already has a `build.completed` flag (ADR-0066), so the store *could* accept them — but the same volume argument applies, and mixing them into `events` re-imposes the filtering burden ADR-0064 was avoiding.
5. **Multi-workspace token model** — per-workspace tokens are proposed above, but issuance, rotation, and revocation have no home yet; that likely arrives with Phase 4's identity model rather than Phase 2. That identity model — OIDC/OAuth2 login, session/token issuance, and an authorization model for who may deploy where (both named in Phase 4 above) — is settled separately in [ADR-0067](0067-server-identity-authentication-authorization.md), not an extension of this one's bearer-token design: a CI runner authenticating with a static append-only token and a human authenticating to a control-plane UI are different problems with different threat models. ADR-0066's `actor` resolution (cloud CLI identity → CI actor → OS login) is CLI-side and unaffected either way; ADR-0067 settles that a control-plane session outranks it when one exists.
6. **Does `DriftHistoryStore`/cost history need their own `forward()` call sites, or a shared helper?** `AuditController.forward()` already exists and is call-site agnostic — it takes an `event_type` and a payload dict. The smallest change is almost certainly a best-effort forward call added directly inside `record_snapshot()`/the drift equivalent, mirroring the pattern `_forward_lock_audit_event()`/`_forward_drift_audit_event()` already established for lock and drift *events* (as opposed to drift/cost *history snapshots*, which is what this ADR is actually about) — this needs a short implementation pass once the state service itself exists, not a design decision this ADR needs to pre-resolve.
7. **Do drift/cost history need their own durable-store option before forwarding them to the state service is meaningful?** Resolved — this is now Phase 1, above: one unified `AuditController.push_to_remote()`, given a `repo_name`/`remote_path` shape that actually places files inside the target repo instead of assuming they're already there, with `ManifestController.push_to_remote()` and its dead `type`/`repository`/`branch` fields retired in favour of it.
8. **Should `spec.audit` be renamed now that it is not audit-only?** This ADR deliberately keeps reusing `spec.audit.sinks`/`AuditController` as the delivery mechanism for drift and cost history, which means a section named "audit" ends up routing non-audit history too. Renaming `spec.audit` to something broader (e.g. `spec.telemetry` or `spec.state`) is a real naming question this revision surfaces but does not resolve — it touches a large, recently-stabilised surface (ADR-0066) and is better decided once drift/cost forwarding is actually implemented and the awkwardness is concrete rather than hypothetical.
