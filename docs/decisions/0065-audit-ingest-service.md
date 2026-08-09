# Audit ingest service — an HTTP endpoint over a queryable event store

- Status: proposed
- Date: 2026-08-06
- Related: ADR-0018 (deployment audit & traceability), ADR-0022 (SIEM integration), ADR-0026 (resolved-model cache — rebuildability precedent), ADR-0064 (deployment metrics record), ADR-0007 (deployment state locking), ADR-0032 (approval gates)

## Context and Problem Statement

Strata already emits a rich stream of structured records — deploy-logs (ADR-0018), deployment manifests, and, once ADR-0064 lands, per-deployment metrics records. Each is written into the build output and optionally forwarded to configured sinks.

Every one of those destinations is either **local** or **someone else's product**:

- `stdout`, `ndjson`, and the build directory are per-machine. ADR-0018 already notes the deploy-log is readable "only on the machine (or checked-out repo) that produced the entry."
- `syslog`, `webhook`, and the `ISiemSink` integrations (Splunk HEC, ELK, OTLP, Sentinel DCR) all hand the record to an external platform.

This leaves a gap that neither side fills. A team without Splunk or Datadog has no way to answer *"how many production deploys failed this quarter, across all workspaces"* — the records exist, but they are scattered across ephemeral CI runners and developer laptops. A team *with* Splunk can answer it, but only inside Splunk, and only for as long as they keep paying for the retention window.

ADR-0064 makes this sharper rather than solving it. Its self-containment invariant deliberately pushes *all* aggregation downstream — deployment frequency, change failure rate, and MTTR are explicitly not computable from a single record. That is the right call, but it means the value of the metrics work is gated on some consumer existing that holds many records. Today, strata ships no such consumer.

Separately, there is a longer-term question hanging over the project: if strata ever grows a service — one that can hold a lock across process boundaries, wait days for an approval, or run scheduled drift detection — what does it stand on? Those capabilities all presuppose durable, queryable, cross-workspace state. Building that state store as a side effect of solving the aggregation problem is considerably cheaper than building it later as a greenfield control plane.

So: **strata needs a first-party destination that is durable, central, and queryable by whatever tooling the operator already owns.**

## Decision Drivers

- **The emit side is already built** — sinks, routing, event policy, redaction, and best-effort semantics all exist (ADR-0018/0022); this should require no new CLI capability
- **Aggregation is worthless without a corpus** — ADR-0064 Phase B and Phase C both stall until something accumulates records
- **The operator's tooling is not ours to choose** — Grafana, Metabase, Power BI, `psql`, a Python notebook; SQL is the widest possible interface
- **The deploy path must stay safe** — ingest cannot become a dependency that slows or breaks deployments
- **Credentials on CI runners are a liability** — whatever we hand a pipeline will eventually leak; scope it accordingly
- **A future control plane should be incremental, not greenfield** — reuse this as its state store rather than designing a second one

## Considered Options

**Option A — a `database` built-in sink; the CLI writes directly to a central SQL database**

Add `type: database` to `AuditSinkModel` alongside `stdout`/`ndjson`/`syslog`/`webhook`, with a connection string, and have `AuditController` INSERT records itself.

Rejected on four independent grounds:

- **Credential blast radius.** Every CI runner and every developer laptop would hold database credentials. A connection that can INSERT can generally also `UPDATE` and `DELETE`, which is catastrophic for a record series whose entire value rests on being an immutable audit trail. Restricting to INSERT-only grants is possible but must then be configured correctly by every operator, forever.
- **Schema ownership and version skew.** The CLI would encode table structure. A workspace pinned to strata 1.6 and one pinned to 1.9 would write to the same tables with different expectations, and someone would need to run DDL — which means granting DDL rights somewhere on the deploy path.
- **Egress.** Outbound HTTPS from a CI runner is universally permitted. An outbound Postgres port generally is not, and getting it opened is an organisational project.
- **Dependency weight.** A database driver becomes a CLI dependency. Strata's existing sinks deliberately use `urllib` and stdlib sockets precisely to avoid this.

**Option B — an HTTP ingest service in front of a SQL event store** *(chosen)*

A small first-party server accepts records over HTTP and persists them. Workspaces reach it through the **existing** `webhook` sink. Operators query the database directly with any SQL tooling.

**Option C — rely on third-party SIEM/observability platforms only**

Status quo. Rejected as a complete answer: it makes cross-workspace measurement conditional on owning and funding an observability platform, and it leaves strata unable to ship any aggregate command of its own. Retained as a parallel delivery channel — sinks are a list, and nothing here displaces them.

**Option D — build the full control plane now**

Skip the ingest-only step; build identity, authorization, run orchestration, approvals, and dashboards together.

Rejected as sequencing, not as a destination. It front-loads every hard problem (multi-tenancy, authz model, run isolation) before a single question has been answered, and it would launch against an empty database — so the first dashboard would show nothing for months. Option B reaches the same place with the hard parts deferred until there is evidence about what is actually needed.

## Decision Outcome

Chosen: **Option B — an HTTP ingest service over a queryable SQL event store.**

The decisive property is that **the client side is already built.** `AuditSinkModel` already supports `type: webhook` with `url` and `headers`, and `AuditController._send_webhook()` already POSTs the record as JSON over `urllib`. Pointing a workspace at the ingest service is configuration, not code:

```yaml
spec:
  audit:
    policy:
      events:
        deploy_audit: true
    sinks:
      - name: strata-ingest
        type: webhook
        url: https://ingest.internal/v1/events
        headers:
          Authorization: "Bearer ${STRATA_INGEST_TOKEN}"
```

Everything new lives on the server, where it can be versioned, migrated, and secured independently of the ~dozens of runners that write to it. The one client-side change this ADR does require is raising sink-delivery failures from `debug` to `warning` (see Risks) — a one-line fix, not a new capability.

### The projection invariant

The rule governing the store, and the counterpart to ADR-0064's self-containment invariant:

> **The event store is a queryable projection, never the source of truth.**

Build artifacts, the local deploy-log, `.strata/metrics/deployments.ndjson`, and git remain authoritative. The store must be fully reconstructable by replaying those records — which is exactly what `strata audit resend` already does.

This is the same discipline ADR-0026 applies to `cache.db` ("the database is fully rebuildable"), and it is load-bearing for the same reason: it is what preserves the freedom to change the schema. A store that cannot be rebuilt is a store whose schema is frozen the day the first row lands.

Note the difference from `cache.db` all the same — this database is *reconstructable*, but only from records that live on many machines. It is therefore backed up like real data, not discarded like a cache, and it must never live under `.strata/cache/`.

### Phase 1 — ingest endpoint and event store

A single service exposing one write route:

```
POST /v1/events          → 202 Accepted
GET  /healthz            → 200
```

The body is whatever the sink sent — a `DeployLogModel`, a deployment manifest, or an ADR-0064 metrics record. Record type is discriminated by the payload's `kind` (or inferred for legacy deploy-log payloads, which predate a `kind` field).

#### Storage schema — typed dimensions, JSON payload

One table, deliberately:

```sql
CREATE TABLE events (
    execution_id    TEXT        NOT NULL,
    record_type     TEXT        NOT NULL,   -- deploy-log | deployment-manifest | deployment-metrics
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

The promoted columns are precisely ADR-0064's `label_safe` set. That is not a coincidence — the same bounded-cardinality reasoning that keeps `commit_sha` out of Prometheus labels keeps it out of indexed columns here. It stays queryable inside `payload`, where an unbounded value costs nothing.

Storing the **complete record verbatim in `payload`** is what neutralises the schema-churn risk ADR-0064 flags. A new measure added in strata 1.8 lands in `payload` and is immediately queryable via a JSON path expression, with no migration and no coordination between runner and server versions. Promotion to a typed column happens later and only when a query needs an index — and it can be backfilled from `payload` at that point, because the data was never discarded.

#### Idempotency is mandatory

Duplicate delivery is **certain**, not hypothetical, for two concrete reasons: `forward_to_siem()` is best-effort with no delivery confirmation, and `strata audit resend` exists specifically to re-forward records that failed the first time.

The primary key on `(execution_id, record_type)` with insert-on-conflict-ignore makes replay a no-op. Without it, one `audit resend` after an ingest outage would silently inflate deployment frequency and corrupt every ratio derived from it — the kind of defect that is invisible in the data and only discovered when someone questions a dashboard months later.

`execution_id` is generated by `AuditController.generate_execution_id()` and is already unique per run, so no client change is needed to support this.

#### Append-only

There are no update or delete routes. Records are immutable facts about events that have already happened. Corrections, if ever needed, are new records — never mutations of old ones. Retention enforcement is an operator-run job against the database, not an API surface.

#### Delivery semantics — protecting the deploy path

Two properties matter, one on each side:

- **Server: respond immediately.** Validate shallowly (well-formed JSON, required identity fields present, size limit), insert, return `202`. No aggregation, no enrichment, no fan-out on the request path.
- **Client: already correct, but bounded.** `_send_webhook()` uses a 10-second `urllib` timeout, a single attempt, and no retry; `forward_to_siem()` swallows failures. So an ingest outage costs a deployment up to 10 seconds and nothing else — no failed deploy, no changed exit code.

That 10 seconds is nonetheless a real cost worth stating plainly: with the ingest service hard-down (connection hanging rather than refused), every deployment pays the full timeout. This is acceptable, but it is the reason ingest must never be given a retry loop on the deploy path. Recovery is `strata audit resend`, run after the fact, which already exists and is already idempotent under the primary key above.

This is also a second, independent reason to reach the ingest service through the **built-in `webhook` sink rather than as a new `ISiemSink` integration**. Integration-backed sinks share `SiemBaseIntegration`'s transport, which uses `requests` with `_REQUESTS_TIMEOUT = 15`, `_MAX_RETRIES = 3`, and `_RETRY_BACKOFF = 1.0` doubling per attempt — roughly 45 seconds of timeout plus ~7 seconds of backoff against a hard-down endpoint, on every deployment. That retry behaviour is correct for a third-party SIEM whose delivery we cannot replay, but wrong for a first-party store that has `audit resend` as a first-class recovery path. Cheap-and-lossy plus explicit replay beats expensive-and-persistent on the deploy path.

#### Authentication

Bearer token via the sink's existing `headers` map, issued **per workspace** so that tokens can be attributed and revoked individually. Tokens grant append-only ingest and nothing else — they cannot read, cannot query, and cannot delete. A leaked runner token lets an attacker write junk records, which is bad but bounded and detectable; it does not expose deployment history.

TLS is required. The service must refuse to start on a non-loopback bind without it.

#### What Phase 1 deliberately excludes

- **No read API.** Operators query the database directly. This is the point — SQL is a better and more widely supported interface than any REST API we would design, and shipping one now would freeze a query model before we know the questions.
- **No UI.** Grafana, Metabase, and Power BI all speak SQL already.
- **No aggregation.** Deployment frequency, change failure rate, and MTTR are `GROUP BY` queries, not endpoints.
- **No large blobs.** Terraform plan JSON and SBOM documents are referenced by digest, not inlined. Inlining them turns the ingest service into an artifact store — a different product with different retention economics and different security review. Payloads are size-capped, and oversized records are rejected with a clear error rather than silently truncated.
- **No execution.** The service receives records. It does not run strata.

### Phase 2 — read API and first-party queries ⏳ deferred

Once real query patterns emerge from operators using SQL directly, promote the recurring ones into a read API and into `strata metrics dora` / `strata metrics show` (ADR-0064 Phase B), pointed at the store instead of local NDJSON.

Deferred deliberately: designing the read model before observing the queries is how APIs acquire endpoints nobody uses.

### Phase 3 — control plane ⏳ future

The capabilities a CLI structurally cannot provide — approvals that outlive a process (ADR-0032), locks held across machines (ADR-0007), scheduled drift detection, and an authorization model for who may deploy where — all require durable central state plus a long-lived process. Phase 1 provides the first; Phase 3 adds the second.

If that service ever executes deployments, it should **spawn the strata CLI as a subprocess** rather than importing strata as a library. The codebase is built around one-process-one-workspace assumptions — process-scoped token caches, cwd-relative work-path discovery, a global logger, and lifecycle scripts that read `STRATA_PHASE` from the environment. Process exit is also what bounds secret lifetime: the SSH-key pattern writes a `chmod 600` temp file and deletes it when the subprocess ends, whereas an in-process runner would accumulate resolved secrets from every workspace in one long-lived heap. Version pinning follows for free — different workspaces on different strata versions are just different images.

Worth stating plainly, because it is easy to assume otherwise: a subprocess is **not** a security boundary. Same UID, same filesystem, same network. If Phase 3 is ever multi-tenant across trust boundaries, the real isolation unit is a container or VM per run; the process split buys lifecycle hygiene, not isolation.

## Consequences

### Good

- **Near-zero CLI change to adopt** — the `webhook` sink already exists; onboarding a workspace is a YAML edit, and the only code change is a sink-failure log level
- **Unblocks ADR-0064** — Phase B and Phase C both need a corpus; this is the corpus, and it accumulates from the day it is switched on
- **Widest possible consumer interface** — SQL works with Grafana, Metabase, Power BI, notebooks, and `psql`, with no API to learn or version
- **Small credential surface** — append-only bearer tokens on runners; no database credentials leave the server
- **Schema churn absorbed** — verbatim JSON payload means new fields need no migration and no runner/server version coordination
- **The control plane becomes incremental** — Phase 3 inherits a populated store with real history instead of launching against an empty database
- **Composes rather than replaces** — sinks are a list; Splunk and OTLP forwarding continue unchanged alongside it

### Neutral

- **Another service to operate** — a process, a database, TLS, and backups. Justified only for teams that actually want cross-workspace history; single-workspace users should keep using the local NDJSON series
- **Eventually consistent by construction** — best-effort delivery means the store may lag or miss records until an `audit resend`; it is a measurement system, not an accounting ledger
- **Direct SQL access is the API in Phase 1** — deliberate, but it does mean the physical schema is visible to consumers earlier than a REST design would expose it

### Risk

- **The store gets mistaken for the source of truth** — someone builds a compliance process that assumes every deployment is present
  - Mitigation: the projection invariant is documented here and must be repeated wherever the store is described; `audit resend` is the reconciliation path, and gaps are detectable by comparing against local deploy-logs
- **Ingest outage silently loses records** — deployments succeed, records vanish, and nobody notices until a dashboard looks wrong. This is worse than it first appears: `forward_to_siem()` logs sink failures at **`debug`** level (`forward_to_siem_sink_failed`), so under any normal log configuration the loss is entirely invisible
  - Mitigation: raise sink-delivery failures to `warning` so they are observable by default — a prerequisite for this ADR, not an optional follow-up. Beyond that, gaps are detectable by reconciling the store against local deploy-logs, and closed with `strata audit resend`
- **Schema becomes a de-facto public API** — Phase 1 hands operators direct SQL, so any column rename breaks their dashboards
  - Mitigation: promoted columns are restricted to the stable `label_safe` set; everything volatile stays in `payload`; a versioned view layer can be added if churn materialises
- **Duplicate records corrupt aggregates** — the highest-impact failure mode, because it is invisible
  - Mitigation: the composite primary key makes replay a no-op; this is a correctness requirement, not an optimisation
- **Scope creep into an artifact store** — pressure to inline plan JSON and SBOMs will recur
  - Mitigation: hard payload size limit with explicit rejection; blobs referenced by digest only

## Open questions

1. **Database engine for the reference implementation** — SQLite is sufficient for a single-team fleet and matches the ADR-0026 precedent of stdlib `sqlite3` with no new dependency; Postgres is the obvious answer for concurrent writers and JSONB indexing. Shipping SQLite-first with a documented Postgres path is likely right, but the write-concurrency ceiling of SQLite under many simultaneous CI runners needs measuring before committing.
2. **Does the ingest service ship inside the strata package, or as a separate deployable?** In-package means `strata serve ingest` and zero extra distribution; separate means the CLI does not carry a web framework it never uses in the common case.
3. **Retention and rotation policy** — inherited unresolved from ADR-0064 OQ-3, and now a server-side concern: per-record-type retention, partition-by-month, or operator-run purge.
4. **Should `build_event` records be ingested?** ADR-0064 excludes builds from the metrics series for good reasons (dev-loop volume, nothing changed outside the output directory). The audit policy already has a `build_event` flag, so the store *could* accept them — but the same volume argument applies, and mixing them into `events` re-imposes the filtering burden ADR-0064 was avoiding.
5. **Multi-workspace token model** — per-workspace tokens are proposed above, but issuance, rotation, and revocation have no home yet; that likely arrives with Phase 3's identity model rather than Phase 1. That identity model — OIDC/OAuth2 login, session/token issuance, and an authorization model for who may deploy where (both named in Phase 3 above) — is settled separately in [ADR-0067](0067-server-identity-authentication-authorization.md), not an extension of this one's bearer-token design: a CI runner authenticating with a static append-only token and a human authenticating to a control-plane UI are different problems with different threat models. ADR-0066's `actor` resolution (cloud CLI identity → CI actor → OS login) is CLI-side and unaffected either way; ADR-0067 settles that a control-plane session outranks it when one exists.
