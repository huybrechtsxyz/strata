# Resolved-model cache for fleet-wide command performance

- Status: partially implemented
- Date: 2026-07-08

## Implementation status

Phases 1–3 (CacheService/SQLite backend, `strata cache` CLI group, VS Code background
warmer) are implemented. Command integration (originally "Phase 2") is intentionally
narrower than first planned — see "Implementation reality check" below for what's wired
and why the rest is deferred, not merely unfinished.

## Context and Problem Statement

Several strata commands need to traverse every registered deployment and fully resolve its
environment models before they can produce output. Today each command performs a complete
`EnvironmentService` load per deployment on every invocation — reading all referenced YAML
files, applying the merge chain, resolving overrides, and constructing the full model graph.

For a single deployment this is acceptable. For a fleet of 50+ deployments it is
prohibitively slow: a command like `promote matrix` that needs the effective version of
every deployment in every environment would trigger hundreds of file reads and model
constructions on every run.

This problem was first identified during the design of `strata promote matrix` (ADR-0011,
OQ-17), which was explicitly deferred until a caching strategy is in place. The same
limitation affects any future command that requires a fleet-wide resolved view.

### Commands that benefit from a cache

Every command that loads a deployment model can benefit. The table distinguishes
**single-deployment** commands (one `-f` file, cache saves repeated loading in CI
pipelines that run the same deployment through multiple steps) from **fleet-wide**
commands (no `-f`, scan all deployments, cache is essential for performance).

#### Fleet-wide commands — cache is essential

Without a cache these commands perform a full model load for every registered deployment.
On a 50-deployment fleet that means 50 × (workspace load + environment merge). These
commands must not be used uncached on any non-trivial fleet.

| Command                              | What it reads per deployment                 | Without cache |
| ------------------------------------ | -------------------------------------------- | ------------- |
| `strata promote matrix` *(future)*   | Effective version per environment            | Full load ×N  |
| `strata env status --all`            | Live infrastructure state summary            | Full load ×N  |
| `strata deploy drift run --all`      | Expected vs actual config per deployment     | Full load ×N  |
| `strata validate graph` *(no entry)* | Full workspace dependency graph              | Full load ×N  |
| `strata deploy list`                 | Deployment metadata for CI matrix generation | Full load ×N  |
| `strata manifest list`               | All deployment manifests                     | Full load ×N  |
| MCP workspace status queries         | Aggregate workspace state for IDE/copilot    | Full load ×N  |

#### Single-deployment commands — cache reduces per-step overhead in CI pipelines

Each command below loads the full deployment model independently. In a CI pipeline
that runs several commands against the same deployment (validate → build → deploy →
health check), without a cache every step pays the full load cost again. With a
pre-warmed cache each step after the first is near-instantaneous for the load phase.

| Command                      | What it loads                             | Cache benefit                                              |
| ---------------------------- | ----------------------------------------- | ---------------------------------------------------------- |
| `strata build run -f`        | Deployment + workspace + all envs         | Avoids reload when pipeline runs validate → build → deploy |
| `strata build plan -f`       | Same as build run                         | Same                                                       |
| `strata build sbom -f`       | Deployment + workspace (for context)      | Minor; sbom scan dominates                                 |
| `strata deploy run -f`       | Full resolved model before provisioning   | Avoids reload after build in same pipeline                 |
| `strata deploy destroy -f`   | Full resolved model                       | Same                                                       |
| `strata deploy show -f`      | Full resolved config for display          | Fast inspection without re-parse                           |
| `strata deploy output -f`    | Resolved deployment (for backend context) | Avoids reload in output scripts                            |
| `strata deploy drift run -f` | Resolved config for drift comparison      | Avoids reload after build                                  |
| `strata deploy plan -f`      | Resolved deployment (for plan context)    | Avoids reload                                              |
| `strata validate run -f`     | Full model for validation                 | Avoids reload in pre-build CI gates                        |
| `strata env show -f`         | Full resolved environment                 | Fast display in CI logs                                    |
| `strata env status -f`       | Resolved deployment + live backend        | Load phase avoidable                                       |
| `strata values list -f`      | Resolved variables/secrets/features       | Avoids reload in value-check scripts                       |
| `strata values get -f`       | Same                                      | Same                                                       |
| `strata values resolve -f`   | Same                                      | Same                                                       |
| `strata policy check -f`     | Resolved model for policy evaluation      | Avoids reload in policy gate step                          |

#### Commands that do NOT load a deployment model — no cache benefit

`sln`, `repo`, `profile`, `config`, `ref`, `vars`, `schema`, `secret`, `tools`,
`audit`, `log`, `guide`, `version`, `help`, `completion`, `console` — these operate
on solution state, CLI config, schemas, or external systems rather than on the
resolved deployment model. The cache is not consulted for these commands.

#### Implementation reality check (post-implementation survey)

The single-deployment table above was written before implementation and assumed every
listed command loads (or could load) a generic "resolved model" that the cache could
serve directly. A code survey done after Phase 1–3 shipped found this assumption does
not hold for most of them, and corrects the plan accordingly:

**What the cache actually stores.** `CacheService`/`CacheController` cache a
`PlatformArtifactModel` — the same object `PlatformBuilder` assembles for
`build run`/`build plan` and writes to `platform.json`. It does **not** cache the live
`DeploymentService`/`EnvironmentService`/`WorkspaceService` object graph (variables,
secrets, features, stage config) that most of the commands below actually need — that
graph is not JSON-serialisable data, it's behaviour-bearing service objects.

**The real bottleneck is `load_deploy_services()`, not `PlatformBuilder`.** For every
command that needs the live service graph (to execute provisioners, read resolved
variables, evaluate stages, etc.), the expensive YAML-parse-and-merge step happens
*before* — and independently of — whether a `PlatformArtifactModel` is ever built.
Caching the platform.json-shaped output does nothing to avoid that step for these
commands; it only helps commands that were going to build a `PlatformArtifactModel`
anyway.

| Command                                                            | Loads `DeploymentService`?                                                                                | Builds/reads `PlatformArtifactModel`?                             | Cache wiring done?                                                                                                                                                                                                                                                                                                |
| ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `strata build run -f`                                              | Yes (via `BaseBuildCommand`)                                                                              | Builds one (`PlatformBuilder`)                                    | **Yes** — auto-warms from the model already built after a successful run. `--no-cache-warm` to opt out.                                                                                                                                                                                                           |
| `strata build plan -f`                                             | Yes                                                                                                       | Builds one (into a temp dir for diffing)                          | **Yes** — same auto-warm pattern, same flag.                                                                                                                                                                                                                                                                      |
| `strata policy check -f`                                           | Yes (own direct `DeploymentService.load()` call)                                                          | **Reads** an existing `platform.json` off disk (never builds one) | **Yes** — opportunistic warm using the artifact it already read. `--no-cache-warm` to opt out.                                                                                                                                                                                                                    |
| `strata deploy run -f`                                             | Yes                                                                                                       | No — executes provisioners directly off the live service graph    | **No.** Needs the live graph to provision infrastructure; a cached platform.json can't replace that, and there's no `PlatformArtifactModel` in memory to warm from.                                                                                                                                               |
| `strata deploy show -f`                                            | Yes                                                                                                       | No                                                                | **No.** Same reason — no model built here to warm the cache with; skipping `load_deploy_services()` to serve straight from a cached dict would require rewriting its data access from typed service accessors (`get_variables()`, `get_secrets()`, …) to reading a plain dict. Out of scope as a "quick wire-in". |
| `strata values list/get/resolve -f`                                | Yes                                                                                                       | No                                                                | **No.** Same reason as `deploy show`.                                                                                                                                                                                                                                                                             |
| `strata validate run -f`                                           | **No** — uses `PlatformValidator` directly over the raw YAML dict, never loads `DeploymentService` at all | No                                                                | **N/A.** The cache is irrelevant to this command's actual implementation.                                                                                                                                                                                                                                         |
| `strata deploy destroy/output/drift/plan -f`, `env show/status -f` | Not yet implemented as of this writing                                                                    | —                                                                 | Deferred until the commands exist.                                                                                                                                                                                                                                                                                |
| `strata deploy list`, `strata manifest list`                       | **No** — both do a cheap direct filesystem/JSON scan, no `DeploymentService` load at all                  | No                                                                | **N/A.** Already cheap; not the "Full load ×N" the fleet-wide table assumed.                                                                                                                                                                                                                                      |

**Revised conclusion:** of the originally-listed single-deployment commands, only the
ones that build or read a `PlatformArtifactModel` as part of their existing work
(`build run`, `build plan`, `policy check`) get a safe, real cache benefit today. Giving
the rest (`deploy run/show`, `values list/get/resolve`) genuine cache benefit would
require a second cache concept — a cached, serialisable "resolved deployment" (variables/
secrets/features/stage config) distinct from `PlatformArtifactModel` — plus rewriting
each command to read from it instead of the live service graph. Tracked as a follow-up,
not implemented.

## Decision Drivers

- **Performance** — Fleet-wide commands must complete in seconds, not minutes.
- **Correctness** — Stale cache must never silently produce wrong output. Staleness must
  be detectable; stale entries are auto-refreshed before use.
- **Cache as default, not opt-in** — Every command that can use the cache does so. The
  operator can bypass with `--no-cache`; forcing a refresh uses `--refresh-cache`.
- **Not in git** — Cache is derived from committed YAML files. Committing derived
  artifacts breaks DRY, creates PR noise, and each machine's local path context is
  different. `.strata/cache` is gitignored. CI pipelines always start cold (unless a
  CI artifact cache is configured separately — out of scope for this ADR).
- **Low infrastructure demands** — No external services, no network access, no additional
  persistent processes required. The cache must work with only the Python standard library.
- **VS Code background warmer** — The VS Code extension watches config files. On save,
  it should invalidate affected cache entries and re-warm in the background, keeping the
  cache perpetually fresh during active editing without any operator action.
- **Version-safe** — CLI upgrades that change the resolved model schema must invalidate
  all cache entries gracefully.

## Considered Options

### Storage backend comparison

Three storage approaches are viable within the zero-external-dependency constraint:

| Approach             | Bulk read (N deployments) | Single read          | Atomic write           | Human-readable | Dependencies       |
| -------------------- | ------------------------- | -------------------- | ---------------------- | -------------- | ------------------ |
| **SQLite**           | 1 query                   | 1 query              | Yes (WAL)              | No (binary)    | `sqlite3` — stdlib |
| **Per-file JSON**    | N file opens              | 1 file open          | No (write-then-rename) | Yes            | None               |
| **Single JSON file** | 1 file open               | 1 file open + filter | No (full rewrite)      | Yes            | None               |

**SQLite wins for this use case.** The primary consumers (`promote matrix`, bulk
validation, drift detection) always need all entries. A single `SELECT` vs N
individual file opens is the deciding factor. Python's built-in `sqlite3` means no
new dependencies. WAL mode allows concurrent readers without blocking writers.

Debuggability (the main argument for JSON) is addressed by `strata cache export`
which writes the current cache to JSON on demand.

**Single JSON file is the runner-up** — one file open for all entries, human-readable.
Weakness is atomic write: the entire file must be rewritten on any update, and a crash
during write corrupts the whole cache. Acceptable for small fleets, fragile for large ones.

**Per-file JSON** (original proposal) has the worst bulk read performance and atomic
write characteristics of the three. Ruled out.

### Option A — SQLite database at `.strata/cache.db` (recommended)

A single SQLite database file stores all resolved cache entries.

```
.strata/
  cache.db          # SQLite — gitignored via .strata/.gitignore
```

**Schema:**

Two tables. `cache` stores one row per cached artifact. `cache_inputs` records every
file that contributed to that row — needed for efficient remote-sync invalidation (OQ-2).

```sql
CREATE TABLE cache (
    name            TEXT PRIMARY KEY,  -- meta.name from YAML; natural lookup key
    kind            TEXT NOT NULL,     -- YAML kind; index target for future non-deployment caching
    cache_version   INTEGER NOT NULL,  -- schema version; bump triggers full table rebuild
    strata_version  TEXT NOT NULL,     -- CLI version that wrote this row
    cache_key       TEXT NOT NULL,     -- sha256 of all input file contents; staleness check
    written_at      TEXT NOT NULL,     -- ISO 8601 UTC; shown by strata cache status
    resolved        TEXT NOT NULL      -- full resolved model; zlib-compressed JSON
);

-- Enables O(1) invalidation when a remote is synced (see OQ-2)
CREATE TABLE cache_inputs (
    name        TEXT NOT NULL REFERENCES cache(name) ON DELETE CASCADE,
    file_path   TEXT NOT NULL,         -- absolute resolved local path (not @remote/path)
    PRIMARY KEY (name, file_path)
);

CREATE INDEX idx_cache_kind ON cache (kind);
```

**Why `name` as primary key (not a generated ID)?** `name` is already a unique, stable
identifier (`meta.name` in the YAML). A surrogate `id` would add a column that is never
queried. `INSERT OR REPLACE` on `name` gives upsert semantics for free.

**Why `kind`?** Cache is initially deployment-only, but environment and tenant models
may be cached later (e.g., for `promote matrix` per-environment version lookup). Having
`kind` from day one means the table schema doesn't need to change.

**Why `cache_key` as a column (not derived from `resolved`)?** Staleness check reads
only `cache_key` — a few bytes — before deciding whether to deserialise `resolved`
(potentially hundreds of KB). Two-step read path avoids unnecessary JSON + zlib work.

**Why `zlib` on `resolved`?** Python `zlib` is stdlib. Typical resolved model JSON
(environments, overrides, labels) compresses 60–80%. A 200-deployment fleet with
~50KB average JSON per entry becomes ~2MB compressed vs ~10MB uncompressed.
Compression is always applied; no flag needed.

**The database is fully rebuildable.** `cache.db` is derived from the YAML files in
the config repos. If the file is deleted, corrupted, or schema-migrated away,
`strata cache warm` regenerates every entry from scratch. No data is lost; only the
performance benefit is temporarily unavailable.

**Cache key computation:**

```python
key = sha256(
    deployment_file_content
    + workspace_file_content                      # ← must be included: workspace changes affect all deployments that share it
    + b"".join(sorted(env_file_contents))         # sorted for determinism
    + b"".join(sorted(workspace_input_contents))  # provider files, resource files, module files referenced by the workspace
)
```

All input file contents are hashed at their **resolved local paths** (not `@remote/path`
strings — see OQ-2). This means every file that contributes to the resolved model is
covered: the deployment file, the workspace blueprint it references, the environment
files, and all the provider/resource/module files the workspace pulls in.

**Implementation status (post-implementation).** `CacheController._collect_input_paths`
implements the above plus the two items originally flagged as "not yet covered":

- Deployment-level `spec.configurations[].file` references
- The deployment's tenant file (`tenants/<code>.yaml`, resolved by convention — not
  via `repo_map` — when `spec.tenant` is set and the file exists on disk)
- One level of recursion into namespace files' own `spec.modules[].file` references —
  the only genuine "file referenced by a referenced file" case found in the schema
  (provider/resource/module/firewall/DNS/network models have no further nested file
  references of their own)

Deliberately still out of scope: module `source` paths (app code/templates — often a
directory or glob, e.g. `@infra/services/traefik/*`) are not hashed. Hashing an entire
app source tree on every cache-key computation would be expensive and is arguably a
different concern (app code changes are tracked by the module's own version/digest
mechanism elsewhere, not this cache). `--refresh-cache` remains the escape hatch for
this and any other gap.

The `cache_inputs` table stores every resolved local path that contributed to a given
entry. This enables efficient bulk invalidation: when `strata repo sync` fetches a new
version of a remote, every deployment whose workspace or environment files came from that
remote is invalidated in one SQL query (see OQ-2).

**Why the workspace file must be in the hash (and `cache_inputs`):**

A single workspace file is typically shared by many deployments. If `workspace1.yaml`
changes — new provider, updated provisioner source, modified resource list — every
deployment referencing it has a stale resolved model. Without the workspace content in
the hash, all those entries appear fresh and return wrong data silently.

Because workspace files appear in `cache_inputs` for every deployment that references
them, a single workspace edit invalidates exactly the right set of entries. The
`cache_inputs`-based invalidation path handles this efficiently:

```sql
-- When workspace1.yaml changes (local path known):
DELETE FROM cache
WHERE name IN (
    SELECT name FROM cache_inputs
    WHERE file_path = '/absolute/path/to/workspace1.yaml'
);
```

**Read path (every command that can use the cache):**

Two steps: cheap staleness check first, full deserialisation only if fresh.

```sql
-- Step 1: staleness check (fast — reads only cache_key, no JSON deserialisation)
SELECT cache_key, cache_version, strata_version FROM cache WHERE name = ?

-- Step 2: full read (only reached if step 1 confirms the entry is fresh)
SELECT resolved FROM cache WHERE name = ?

-- Fleet-wide bulk read (promote matrix, drift detection, etc.)
SELECT name, kind, resolved FROM cache WHERE kind = 'deployment'
```

Auto-warm on stale or cold:
```
1. Recompute cache_key for this deployment (hash of input file contents)
2. Run step-1 query; compare cache_key + versions
3. Match → run step-2, decompress + deserialise, return (fast path)
4. No match / no row → full EnvironmentService load
                    → INSERT OR REPLACE into cache + INSERT into cache_inputs
                    → return result
```

Step 4 is transparent to callers — the cache auto-warms on first use or on stale hit.
Commands never need to check whether the cache is warm; they call `CacheService.get()`.

**`--no-cache` and `--refresh-cache` flags** (on every command that uses the cache):

| Flag              | Behaviour                                                               |
| ----------------- | ----------------------------------------------------------------------- |
| *(none)*          | Use cache if fresh; auto-warm if stale/cold                             |
| `--no-cache`      | Bypass cache entirely; do not read or write for this invocation         |
| `--refresh-cache` | Force re-warm even if cache_key matches; useful after manual YAML edits |

**WAL mode** is enabled on open (`PRAGMA journal_mode=WAL`). This allows the VS Code
extension and CLI commands to read concurrently without blocking each other.

**Pros:**
- Single file — simple to backup, delete, or inspect
- Bulk read is one SQL query regardless of fleet size
- WAL mode: concurrent readers + writer without lock contention
- Atomic writes: crash during `INSERT OR REPLACE` never corrupts other entries
- Python `sqlite3` built-in — zero new dependencies
- Schema version column: one `DELETE FROM cache WHERE cache_version != CURRENT` on upgrade

**Cons:**
- Binary format — not directly human-readable (mitigated by `strata cache export`)
- Schema migrations needed when `cache_version` bumps
- SQLite WAL leaves two extra files (`.db-wal`, `.db-shm`) during active writes —
  all three must be gitignored

### Option B — Single JSON file at `.strata/cache.json`

All entries in one JSON object keyed by deployment name. Human-readable, zero
dependencies, single file open for bulk reads.

- Pro: Trivial implementation. No schema. Directly editable.
- Con: Every write rewrites the entire file. On a 200-deployment fleet a single cache
  warm touches megabytes of JSON for one entry change.
- Con: A crash mid-write corrupts all entries — not just the one being updated.
- Con: No concurrent write safety without external locking.

Acceptable for personal use or very small fleets. Not suitable as the default for
production config repos.

### Option C — Per-file JSON in `.strata/cache/`

The original proposal: one JSON file per deployment.

- Pro: Human-readable. Partial corruption (one bad file) doesn't affect others.
- Con: Bulk read requires N file opens — the primary performance problem is not solved.
- Con: No atomic write guarantee per file without write-then-rename.
- Con: Managing N files adds filesystem overhead (inode usage, directory listing).

Ruled out: per-file JSON has the worst performance for the primary use case and no
meaningful advantage over SQLite for single-entry reads.

### Option D — In-process memoization only

Use `functools.lru_cache` on `EnvironmentService.load()`. Models loaded once within
a single process invocation are not re-loaded.

- Pro: Zero disk I/O, trivial to implement.
- Con: Cache is lost when the process exits. Every `strata` invocation starts cold.
- Con: Does not solve the fleet-wide problem — only reduces within-run redundancy.

In-process memoization is a **complementary** optimization. It should be implemented
alongside Option A to eliminate redundant loads within a single invocation (e.g., the
same `production.yaml` referenced by 50 deployments is loaded once per run).

## Decision Outcome

**Proposed: Option A** — SQLite database at `.strata/cache.db`.

In-process memoization (Option D) is implemented as a complementary optimization. It
already exists in the codebase today as `strata.utils.service_cache` (a process-lifetime
dict cache keyed by service class + path, used by `BaseService.load()`) — this ADR does
not introduce L1, only L2.

### Flag convention

Every strata command that can use the cache exposes two flags:

```
--no-cache        Skip cache for this invocation: no read, no write, no warm
--refresh-cache   Force re-warm even if cache_key is fresh (useful after manual edits)
```

No flag is needed for normal use: the cache is consulted automatically, stale entries
are auto-refreshed transparently. These flags are escape hatches, not normal workflow.

`--no-cache` suppresses both directions. The command reads live YAML files and does
not write the result back to the cache. The next invocation without `--no-cache` will
auto-warm the entry as usual.

**Persistent defaults follow the existing CLI config pattern:**

| Scope             | How to set                                                          | Precedence |
| ----------------- | ------------------------------------------------------------------- | ---------- |
| Single invocation | `--no-cache` flag                                                   | Highest    |
| Shell session     | `STRATA_NO_CACHE=1` env var                                         | Middle     |
| Workspace default | `strata config set cache.enabled false` → writes `.strata/cli.yaml` | Lowest     |

`strata config set cache.enabled false` is the escape hatch for operators who want
cache-less behaviour by default for a specific workspace (e.g., a workspace used only
in CI where the cache never survives between runs anyway). The flag and env var still
override it per-invocation.

### VS Code extension integration

The VS Code extension gains a background cache warmer:

1. **File watcher** — watches all `*.yaml` files under the registered remote paths
2. **On save** — debounced 500ms: identifies which registered deployments reference the
   changed file, calls `strata cache warm {deployment}` for each
3. **On extension start** — calls `strata cache status` to detect cold/stale entries;
   warms all stale entries in the background (non-blocking)
4. **Setting**: `strata.cache.backgroundWarm: true` (default) / `false`

This keeps the cache perpetually fresh during active editing. By the time an operator
runs `promote matrix` in the terminal, the cache is already warm from the last save.

### Implementation approach

**Phase 1 — CacheService + SQLite backend:**
- `CacheService` class: `get(deployment)` (read + auto-warm), `warm(deployment)`,
  `invalidate(name)`, `invalidate_all()`
- Opens `.strata/cache.db` with WAL mode on first use
- `strata cache warm [deployment]` — warm one or all registered deployments
- `strata cache status` — show cache hit/stale/cold state per registered deployment
- `strata cache clear` — remove all cache entries (truncates table)
- `strata cache export [path]` — write current cache to JSON for debugging
- Integration with `strata build`: auto-warm after each successful build
- `.strata/cache.db`, `.strata/cache.db-wal`, `.strata/cache.db-shm` added to
  `.strata/.gitignore`

**Phase 2 — Command integration:**
- `strata promote matrix` — first consumer; always uses `CacheService.get()`;
  prints `(cached)` / `(refreshed)` / `(no-cache)` indicator per row
- Subsequent commands integrated as they are implemented; each gets `--no-cache` +
  `--refresh-cache` flags via a shared Click option group

**Phase 3 — VS Code extension:**
- File watcher + background warm on save
- `strata.cache.backgroundWarm` setting
- Cache status indicator in the status bar (green = warm, yellow = stale, grey = cold)

### Consequences

- Good: Fleet-wide commands become viable at any fleet size — cache read is one SQL query
  regardless of fleet size.
- Good: Cache is the default for every command that can use it; `--no-cache` is an escape
  hatch, not a required flag for fast operation.
- Good: SQLite WAL mode allows the VS Code extension to warm entries concurrently with CLI
  commands reading them — no lock contention in normal use.
- Good: Auto-warm on stale hit means operators never see a "cache miss" error; the command
  just takes slightly longer on first run or after a config change.
- Good: `strata build` integration means the cache is warm after every build without any
  extra operator action.
- Good: Gitignored, single file — never pollutes the config repo; trivial to clear.
- Good: Zero external dependencies — `sqlite3` is Python built-in.
- Neutral: Cache entries can be stale if config files are edited without `strata build`
  (e.g., direct YAML edits). Hash check detects this and auto-refreshes; VS Code extension
  proactively invalidates on save, eliminating staleness in interactive use.
- Neutral: `.db-wal` and `.db-shm` sidecar files appear during active writes. All three
  must be in `.strata/.gitignore`. No user-visible impact.
- Neutral: Schema migration on `cache_version` bump is a `DELETE FROM cache` — all entries
  regenerate on next use. No data loss risk; transient performance impact on first run
  after a CLI upgrade.
- Neutral: Design is forward-compatible with a future long-running strata server without
  any rework — see "Future compatibility" below.
- Bad: Binary format reduces direct inspectability. Mitigated by `strata cache export`.

### Future compatibility: a long-running strata server

This ADR was reviewed against a hypothetical future strata server (a long-running daemon,
as opposed to today's one-process-per-invocation CLI). Conclusion: **no change needed now
(YAGNI)** — the design already generalizes.

The cache is two layers, and only one of them is process-scoped:

| Layer          | What it is                                               | Lifetime                                       | Introduced by                      |
| -------------- | -------------------------------------------------------- | ---------------------------------------------- | ---------------------------------- |
| L1 — ephemeral | `strata.utils.service_cache` in-process dict memoization | One CLI invocation                             | Already shipped, predates this ADR |
| L2 — fixed     | SQLite `.strata/cache.db` (this ADR)                     | Survives process exit; shared across processes | This ADR                           |

A future server does not change L2 at all — it is simply another client calling
`CacheService.get()`, exactly like the CLI and the VS Code extension already do
concurrently today (which is why WAL mode is in this design in the first place). The only
difference a server introduces is that its **L1** keeps living for the server's uptime
instead of dying at the end of one command — that is a longer-lived instance of the same
in-process memoization pattern, not a new mechanism. On server restart, L1 is empty and
it falls back to L2, same cold-start path a CLI process hits today.

The one shift a server would motivate is push-based invalidation (a file watcher
proactively invalidating/re-warming rows) instead of today's pull-based lazy hash-check on
read. That is not new either — it is exactly what Phase 3 (VS Code extension background
warmer) already does. A future daemon is effectively "the VS Code extension's warmer,
extracted to run standalone," reusing the same `cache_inputs`-driven invalidation query.

No action item follows from this — it is recorded here so a future server ADR does not
need to revisit or re-justify the L2 cache design.

## Open Questions

1. **Cache warming in CI**

   CI pipelines start cold: `cache.db` does not exist on a fresh checkout. There are
   three patterns, each suited to a different pipeline shape:

   **Pattern A — Suppress (single-deployment, simple pipeline)**

   A pipeline that validates and deploys exactly one deployment file has nothing to gain
   from the cache. Suppress auto-warming to avoid wasted I/O:

   ```yaml
   # GitHub Actions example
   - run: strata build run -f deploy/deploy-prd.yaml
     env:
       STRATA_NO_CACHE_WARM: "1"
   ```

   - Flag: `strata build --no-cache-warm` (per-command)
   - Env var: `STRATA_NO_CACHE_WARM=1` (job-wide)

   **Pattern B — Explicit pre-warm (multi-step single-deployment pipeline)**

   A pipeline that runs several commands against the same deployment
   (validate → build → deploy → policy check → health check) benefits from warming
   once at the start so every subsequent step hits the cache instead of re-parsing:

   ```yaml
   steps:
     - run: strata cache warm -f deploy/deploy-prd.yaml   # warm once
     - run: strata validate run -f deploy/deploy-prd.yaml  # cache hit
     - run: strata build run -f deploy/deploy-prd.yaml     # cache hit
     - run: strata deploy run -f deploy/deploy-prd.yaml    # cache hit
     - run: strata policy check -f deploy/deploy-prd.yaml  # cache hit
   ```

   **Pattern C — Fleet pre-warm (matrix pipeline)**

   A pipeline that fans out to N deployments in parallel (CI matrix) should warm all
   entries in a single serial step before the matrix starts. Without this, each matrix
   job independently loads the same shared workspace file, multiplying I/O:

   ```yaml
   jobs:
     warm:
       steps:
         - run: strata cache warm --all          # warm entire fleet once
         - uses: actions/upload-artifact@v4
           with: { name: strata-cache, path: .strata/cache.db }

     deploy:
       needs: warm
       strategy:
         matrix:
           deployment: [deploy-eu, deploy-us, deploy-staging]
       steps:
         - uses: actions/download-artifact@v4
           with: { name: strata-cache, path: .strata/ }  # inject warm cache
         - run: strata deploy run -f deploy/${{ matrix.deployment }}.yaml  # cache hit
   ```

   The key insight: `workspace1.yaml` is shared by all deployments. Warming once loads
   it once. Each matrix job then gets a warm cache via the CI artifact and pays only the
   deploy cost, not the model-load cost.

   **Do NOT auto-detect CI from `CI=true`.** Some pipelines intentionally warm and use
   the cache across steps. Make suppression an explicit opt-out, not a heuristic.

   **CI artifact cache (optional, out of scope):** The patterns above use CI job
   artifacts to pass `cache.db` between jobs. If the CI system supports persistent
   artifact caching between pipeline runs (GitHub Actions `cache`, GitLab CI `cache:`),
   the operator can also cache `.strata/cache.db` across runs for even faster cold starts.
   This is a CI pipeline concern — strata just reads/writes the file wherever it finds it.

2. **Cross-remote cache keys**

   `@remote/path` references are resolved to absolute local paths before hashing.
   The cache key is computed from file *contents*, not path strings, so a remote sync
   that fetches new file versions automatically makes the key stale on next read.

   Efficient invalidation when `strata repo sync` runs:

   ```sql
   -- Invalidate all entries that referenced any file from the synced remote
   DELETE FROM cache
   WHERE name IN (
       SELECT name FROM cache_inputs
       WHERE file_path LIKE '/absolute/path/to/remote/%'
   );
   ```

   `RepoSyncCommand` calls `CacheService.invalidate_by_path_prefix(local_remote_root)`
   on completion. The `cache_inputs` table (populated during warm) makes this O(matched
   entries) rather than requiring a full cache scan or re-hash of every entry.

   Next command that needs an invalidated entry auto-re-warms it transparently.

3. **Cache size and compression**

   `resolved` is stored as `zlib.compress(json.dumps(model).encode())` — Python stdlib,
   no new dependencies. Decompression is `json.loads(zlib.decompress(blob))`.

   Typical numbers:

   | Fleet size        | Avg JSON/entry | Compressed | Total `.db` size |
   | ----------------- | -------------- | ---------- | ---------------- |
   | 50 deployments    | ~50 KB         | ~12 KB     | ~0.6 MB          |
   | 200 deployments   | ~50 KB         | ~12 KB     | ~2.4 MB          |
   | 1 000 deployments | ~100 KB        | ~20 KB     | ~20 MB           |

   20 MB is well within SQLite's comfortable operating range (tested to TB scale).
   No hard limits, no eviction policy, no `max_entry_size` config needed at these sizes.

   If an individual entry is anomalously large (e.g., a deployment that merges dozens
   of environment files), `strata cache status --verbose` will show per-entry sizes.
   The operator can use `--no-cache` for that specific deployment if latency matters more
   than convenience.

4. **Store value caching (variables, secrets, feature flags)**

   The model cache stores the resolved *structure* of a deployment — YAML files merged
   into a `PlatformArtifactModel`. It does not store the *values* resolved from external
   stores (Bitwarden, HashiCorp Vault, environment variables). The question is whether
   those resolved values should also be cached locally.

   **Primary driver:** offline / degraded availability. If Bitwarden or Vault is
   unreachable, a warm value cache lets `build run` succeed using last-known values
   rather than failing. Secondary driver: CI pipelines that resolve the same secrets
   across multiple steps currently hit the store on every step.

   **Benefits in full:**

   | Benefit                          | Description                                                                                                                                                                                                                                                                            |
   | -------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | **Resilience to store outages**  | `build run` and `deploy run` succeed even when Bitwarden / Vault is unreachable. Without a value cache, an unavailable secret store blocks every deployment, including ones whose secrets haven't changed.                                                                             |
   | **CI pipeline speed**            | A pipeline that runs validate → build → deploy → policy check resolves the same secrets four times. With a warm value cache, each step after the first is a local read. On a fleet of 50 deployments running in a matrix this compounds: 50 × 4 = 200 store round-trips reduced to 50. |
   | **Rate-limit protection**        | Bitwarden, Azure Key Vault, and Vault all have API rate limits. Fleet-wide commands (`promote matrix`, `drift run --all`) that resolve values for every deployment can hit these limits on larger fleets. A value cache collapses N resolutions to 1 per TTL window.                   |
   | **Predictable build times**      | External store latency is variable (network conditions, Vault lease renewal, MFA prompts). Caching removes the store from the critical path of routine builds, making CI timings consistent.                                                                                           |
   | **Air-gapped / offline deploys** | Once values are cached, a deployment can be built and applied without any network path to the secret store. Useful for regulated environments where the deployment target is network-isolated but the operator has previously fetched the required values.                             |
   | **Audit snapshot**               | A cached value entry records *what value was used at what time* — a lightweight audit trail for variables and features that complements the build artifact. Useful for drift analysis: "did this variable's effective value change between the last two builds?"                       |

   The benefits are real and meaningful. The reason store value caching is deferred is
   not that the benefits are weak — it is that **the two problems below make a correct
   implementation significantly harder than model caching**, and a wrong implementation
   (stale secrets used silently, or secrets stored in plaintext) is worse than no cache.

   **Two hard problems distinguish this from model caching:**

   **Problem 1 — Cache validity cannot be determined by file hash.**

   The model cache uses a SHA-256 of the input YAML file contents. If no file changed,
   the model is fresh. Store values have no equivalent signal — a secret can rotate in
   Bitwarden without any YAML file changing. The only validity mechanism available is a
   **TTL**. This changes the entire correctness contract:

   - Model cache: *provably fresh* (hash matches → identical result guaranteed)
   - Store value cache: *probably fresh* (TTL not expired → value *likely* unchanged)

   A stale model cache entry is detected and auto-refreshed before use. A stale store
   value cache entry is *silently used* — there is no way to detect that the upstream
   value changed without querying the store, which defeats the purpose of caching.

   The acceptable TTL depends on the store type:

   | Source           | Typical rotation cadence    | Suggested max TTL                                            |
   | ---------------- | --------------------------- | ------------------------------------------------------------ |
   | `constant`       | Never                       | Indefinite (but YAML hash covers it — no store cache needed) |
   | `env`            | Session / pipeline          | 0 — do not cache; always resolve live                        |
   | `bitwarden`      | Hours–weeks                 | 1–4 hours                                                    |
   | `vault`          | Minutes–hours (lease-based) | Match Vault lease TTL; do not exceed                         |
   | `azure_keyvault` | Hours–days                  | 1–4 hours                                                    |

   `env` source values must never be cached: they are pipeline-scoped and can differ
   between invocations of the same deployment in the same build system.

   **Problem 2 — Secrets stored at rest require encryption or access controls.**

   The model cache (`cache.db`) stores no sensitive data — resolved model structure is
   safe to write in compressed-JSON form. Secret values are a different category.
   Storing them in the same `cache.db` file means a read of `.strata/cache.db` yields
   plaintext-equivalent secret values. Options:

   - **Separate file with `chmod 600`** — `cache_secrets.db` with filesystem-level
     access control. Provides OS-level protection on Linux/macOS; weaker on Windows.
     Simple, no new dependencies. Does not protect against an attacker with filesystem
     access (same risk as the secrets being on disk at all).
   - **Encryption at rest with an auto-generated ephemeral key** — strata generates a
     random key on first use (`os.urandom(32)`) and writes it to
     `.strata/.cache.key` (chmod 600, gitignored). The `STRATA_CACHE_KEY` env var
     overrides the file (useful in CI: generate once at pipeline start, export for the
     run, discard at the end). Encryption uses that key; decryption requires it.
     **Key lost → cache undecryptable → cold start → resolve live.** This is not a
     problem: the store value cache is a performance optimisation, not an authoritative
     source. Losing the key costs one round-trip to the store, not data loss. No key
     management infrastructure is needed; no root secret is required beyond what the OS
     filesystem already provides. The `cryptography` package (`Fernet`) handles
     encrypt/decrypt; it is not stdlib but is already a likely transitive dependency.
   - **Cache non-secrets only** — cache `variable` and `feature` values freely
     (non-sensitive). Never cache `secret` values; always resolve them live. This is
     the safest default — it eliminates the at-rest risk entirely and still provides
     offline capability for the non-sensitive majority of values.

   **Operator responsibility model:**

   Unlike the model cache — where staleness is *detectable* (hash mismatch) and
   auto-refresh is *safe* (re-reading YAML costs nothing) — the store value cache places
   responsibility on the engineer or DevOps profile to keep values fresh. The cache is
   valid for as long as the operator accepts the TTL risk. This is a deliberate trade-off:
   the system does not silently use a stale secret, but it also does not force a live
   fetch on every command. The operator chooses the freshness window.

   The VS Code extension and a manual CLI trigger carry this responsibility in practice:

   | Mechanism                        | How it works                                                                                                                                                                                                                                                                                                                                                                 |
   | -------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
   | **Extension background refresh** | The extension's background job (already warming the model cache on save) also refreshes store values on a TTL-aware schedule. Each source type has a configured refresh interval (see TTL table above). The extension calls `strata cache warm --values` in the background; the operator sees a status indicator (green = fresh, yellow = expiring soon, grey = stale/cold). |
   | **Manual refresh trigger**       | A VS Code command palette action (`Strata: Refresh store values`) and `strata cache warm --values [-f deployment]` let the operator force a live fetch at any time — before a deploy, after a secret rotation, or when the extension indicator shows stale.                                                                                                                  |
   | **Key auto-generation**          | On first use, strata generates `.strata/.cache.key`. As long as this file exists and `STRATA_CACHE_KEY` is not overridden, the same key is used across sessions. The operator does not need to manage the key manually.                                                                                                                                                      |

   In CI the pattern is the same as the model cache (OQ-1 Pattern B/C): warm values
   explicitly at pipeline start, use them across steps. The pipeline is responsible for
   having a valid `STRATA_CACHE_KEY` or a pre-populated `.strata/.cache.key`.

   **Proposed position (not yet decided):**

   Store value caching is a distinct feature from model caching. It should be designed
   and implemented as a separate ADR. The model cache (this ADR) is not extended with
   store values. The remaining blocking questions before a store value cache ADR:

   - What is the acceptable TTL policy per store type? (draft table above is a starting point)
   - Is caching secret values in scope, or is the scope limited to variables and features?
   - How does `--refresh-cache` interact with store TTLs — does it force a live store
     fetch, or only re-warm the model?
   - What happens when the store is unreachable and no cached value exists (hard error
     vs deployment blocked vs degraded mode)?
