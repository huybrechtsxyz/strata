# Deployment audit and traceability for compliance (ISO 27001 / ISAE 3402)

- Status: accepted
- Date: 2026-06-24

## Context and Problem Statement

Organizations subject to ISO 27001 (A.12.1.2 Change Management) or ISAE 3402 (Type II controls) audits must prove that every infrastructure or application configuration change was:

1. **What changed** — specific YAML values before and after, with evidence of the change
2. **Why it changed** — business justification and change request reference
3. **Who approved** — identity of approver(s) and date of approval
4. **How it was applied** — deployment mechanism, CLI version, exact commit SHA, timestamps

Without this audit trail, configuration management changes **fail a controls walkthrough** — a critical compliance gap. Today, strata has no mechanism to capture and report this evidence.

Related: GitHub Issue #28.

## Decision Drivers

- **Compliance requirement** — ISO 27001 and ISAE 3402 auditors expect immutable audit records for configuration changes.
- **Non-repudiation** — auditors need proof that a change occurred, by whom, and when — independent of git (which is mutable by repo admin).
- **Auditability** — must support tooling integration (SIEM, audit platforms) via structured output formats.
- **Minimal friction** — solution must fit into existing `strata deploy run` workflow without requiring new commands for capture (manifest writing is automatic).
- **Separation of concerns** — audit log (who ran CLI commands) is different from deployment manifest (proof of configuration changes).

## Considered Options

**Option A: Three-layer compliance evidence system**
- Layer 1: PR template in config repos (captures the "why" and "who approved" in source control)
- Layer 2: Deployment manifest JSON (captures "what changed" and "how it was applied" at deployment time)
- Layer 3: CLI query tool (`strata audit changes`) for reporting across multiple deployments

**Option B: Single deployment manifest only**
- Write a comprehensive manifest at deploy time, skip PR template + query tool
- Simpler to implement, but harder for auditors to correlate with source changes

**Option C: External SIEM integration**
- Stream all audit data to an external immutable store (Splunk, Azure Sentinel, AWS CloudTrail)
- Removes requirement to maintain audit trail in git/filesystem, but increases operational complexity
- Suitable as a **future layer**, not the initial implementation

**Option D: Git-based audit only**
- Commit all changes to a separate audit branch, rely on git log and `git blame`
- Auditors consider git mutable if repo admin has force-push rights — insufficient for compliance

## Decision Outcome

Chosen:

**Option A — Three-layer compliance evidence system**, because it provides multiple levels of evidence (process, artifact, reporting), integrates naturally into existing deployment workflow, and aligns with how auditors review configuration management controls.

**Option C — External SIEM integration**, initial support for Azure Sentinel (Log Analytics). Others (Splunk, AWS CloudTrail) deferred to future implementations.

### Architecture: AuditController

All audit orchestration flows through an `AuditController` (extends `BaseController`). The controller is the single point of coordination between:
- Layer 2 (deploy-log JSON writing)
- Layer 3 (audit changes reporting)
- Layer 4 (SIEM forwarding)

**Responsibilities:**
- Assemble the deploy-log JSON payload from git context + deployment results
- Write the deploy-log JSON to disk (Layer 2)
- Commit + push deploy-log to configured remotes (Layer 4) — same remotes as deploy artifacts
- Forward the payload to configured SIEM sinks (Layer 4) — fire-and-forget, non-blocking
- Optionally embed deploy-log reference in the deployment manifest artifacts
- Query and filter deploy-log entries for reporting (Layer 3)
- Accumulate errors/messages via `BaseController` pattern

**Design Pattern:** Mirrors existing controllers (e.g., `ValueController` orchestrates multiple store integrations). The `AuditController` orchestrates multiple SIEM integrations without `RunDeployCommand` knowing which sinks are configured.

```python
class AuditController(BaseController):
    def __init__(self, work_path: str, siem_sinks: List[SiemBaseIntegration] | None = None):
        ...

    def write_deploy_log(self, payload: DeployLogModel) -> Path:
        """Write deploy-log JSON to .strata/deploy-log/, push to remote, and forward to SIEM sinks."""
        ...

    def enrich_with_pr_data(self, payload: DeployLogModel) -> DeployLogModel:
        """Query GitHub for the PR that produced commit_sha; embed PR metadata in payload."""
        ...

    def push_to_remote(self, log_path: Path) -> bool:
        """Commit and push deploy-log entry to configured remote repository."""
        ...

    def query_deploy_logs(self, since=None, stage=None, last=None) -> List[DeployLogModel]:
        """Query deploy-log entries for audit changes reporting."""
        ...
```

### Layer 1: Process + PR Data Extraction (configuration repo)

**Purpose:** Capture the _why_ and _who approved_ — both via structured PR templates and via automated extraction from GitHub PR metadata.

**Part A — PR Template:**

**Artifact:** `.github/pull_request_template.md` in configuration repository (e.g., `xyz-configuration`).

**Form Fields:**
- Change ticket / work item reference (mandatory)
- What changed (free text — YAML paths, new vs old values)
- Why (business justification)
- Risk level (low / medium / high)
- Rollback plan
- Approver sign-off checklist (e.g., `- [ ] @infra-lead reviewed`)

**Part B — PR Data Extraction (automated by `AuditController`):**

The `commit_sha` captured in the deploy-log (Layer 2) is the merge commit — this is the bridge that links a deployment back to its source PR. The `AuditController` can query GitHub for the PR that produced that commit and extract:

| PR Field                   | Audit Evidence                           | How                             |
| -------------------------- | ---------------------------------------- | ------------------------------- |
| `body`                     | Change ticket, justification, risk level | Parsed from template sections   |
| `reviews[].user` + `state` | Who approved, approval date              | Directly available              |
| `mergeCommit.oid`          | Links PR ↔ deployment                    | Matches deploy-log `commit_sha` |
| `files[].filename`         | Which YAML files changed                 | File diff list                  |
| `labels[]`                 | Risk level, environment tags             | Label names                     |
| `closingIssuesReferences`  | Work item / ticket reference             | Linked issues                   |
| `mergedBy`                 | Who triggered the merge                  | User identity                   |
| `headRefName`              | Source branch (feature context)          | Branch name                     |
| `createdAt` / `mergedAt`   | Timeline for auditors                    | ISO timestamps                  |

**Retrieval method:** Uses the existing `GitIntegration` (which wraps `git` CLI) to resolve the merge commit, then extends with a PR lookup via `git log --merges --grep` or the GitHub API. For GitHub-hosted repos, the `AuditController` shells out via `GitIntegration._run_integration()` to query PR data — keeping all subprocess calls within the integration layer (no direct `subprocess` calls in the controller).

**Enriched deploy-log entry:** When PR data is available, the `AuditController` enriches the deploy-log JSON with a `pull_request` section:

```json
{
  "execution_id": "...",
  "commit_sha": "abc123def456...",
  "pull_request": {
    "number": 42,
    "title": "feat: increase replica count for production",
    "url": "https://github.com/org/xyz-configuration/pull/42",
    "author": "jane@example.com",
    "merged_by": "lead@example.com",
    "merged_at": "2026-06-24T14:30:00Z",
    "approvers": ["lead@example.com", "security@example.com"],
    "labels": ["risk:low", "env:production"],
    "linked_issues": ["ORG-1234"],
    "files_changed": ["deploy/deploy-prd.yaml", "environments/prd.yaml"]
  },
  "...remaining fields..."
}
```

**Graceful degradation:** If `gh` CLI is not available, GitHub is unreachable, or the commit doesn't map to a PR (e.g., direct push to main), the `pull_request` field is `null` — the deploy-log is still valid without it.

**Auditor Evidence:** Pull request history on `main` branch shows every merged change with full rationale, approvals, and discussion thread. Stored immutably in GitHub. Additionally, PR metadata is embedded in the deploy-log for offline audit access without querying GitHub.

**Implementation:**
- PR template: documentation and template file in configuration repo (no code)
- PR extraction: new method on `AuditController` using `gh` CLI or GitHub API, called during deploy-log assembly

### Layer 2: Deployment Manifest (written by `xyz deploy run`)

**Purpose:** Capture the _what changed_ and _how it was applied_ with cryptographic certainty at deployment time.

**Artifact:** `.strata/deploy-log/{resolved_structure}/<stage>.json` — path determined by `audit.structure` template (committed to config repo by CI/CD post-deployment).

**Schema:**

```json
{
  "execution_id": "unique-identifier",
  "timestamp": "2026-06-24T14:32:00Z",
  "command": "deploy_run",
  "version": "0.13.0",
  "commit_sha": "abc123def456...",
  "commit_message": "feat: increase replica count for production",
  "commit_author": "jane@example.com",
  "file": "deploy/deploy-prd.yaml",
  "stage": "production",
  "force": false,
  "dry_run": false,
  "success": true,
  "duration_seconds": 164,
  "steps": [
    { "step": "setup", "success": true, "duration_seconds": 8 },
    { "step": "check", "success": true, "duration_seconds": 2 },
    { "step": "plan", "success": true, "duration_seconds": 45 },
    { "step": "apply", "success": true, "duration_seconds": 109 }
  ],
  "errors": [],
  "messages": []
}
```

**Key Fields:**
- `commit_sha`, `commit_message`, `commit_author` — retrieved from git at deploy time via `GitIntegration`
- `file` — the deployment YAML file that was executed
- `stage` — target environment (production, staging, etc.)
- `steps` — each step of the deployment pipeline with success/duration
- `errors` — deployment errors, if any (for audit trail completeness)

**Location in Repo:** `.strata/deploy-log/` is **committed to the configuration repo** by the CI/CD pipeline immediately after deployment, creating an immutable version-controlled audit trail.

**Path Resolution:**

The deploy-log directory path is defined as a constant in `utils/config.py` (already exists: `SOLUTION_DEPLOY_LOG_DIR = "deploy-log"`) and exposed via `ConfigurationService`:

```python
# utils/config.py — already defined
SOLUTION_DEPLOY_LOG_DIR: str = "deploy-log"

# services/configuration_service.py — new method (follows get_default_build_path pattern)
def get_deploy_log_path(self, work_path: Path, create_path: bool = True) -> Path:
    """Get the deploy-log output path from configuration or fallback to constant.
    
    Resolution order:
    1. spec.deployment.audit.path (from configuration YAML)
    2. config.SOLUTION_DEPLOY_LOG_DIR (constant: "deploy-log", under .strata/)
    """
    ...
```

This follows the established pattern where `ConfigurationService.get_default_build_path()`, `get_default_dist_path()`, etc. resolve paths from configuration with a constant fallback. The `AuditController` calls `configuration_service.get_deploy_log_path(work_path)` — it never hardcodes the path.

**Auditor Evidence:** Immutable deploy-log entries in version control, one per deployment, with git history showing who triggered each deployment and when.

**Implementation:** Modify `RunDeployCommand` to write this JSON file after successful (or failed) deployment.

### Layer 3: Audit Reporting (`strata audit changes` CLI command)

**Purpose:** Provide auditors and operators with an on-demand report combining all deployment evidence.

**Commands:**

```bash
strata audit changes                       # list all deploy-log entries (console format)
strata audit changes --since 2026-01-01    # filter by date
strata audit changes --stage production    # filter by stage
strata audit changes --last 10             # last N deployments
strata audit changes --output json         # machine-readable JSON array
strata audit changes --output ndjson       # streaming NDJSON (one record per line)
strata audit changes --output text         # plain text (one line per deployment)
```

**Output Format:**

Uses the standard `@click_output_format` decorator from `cli_common.py`, supporting the existing `OUTPUT_FORMATS`: `console`, `text`, `json`, `ndjson`.

| Format    | Use Case                                                                    |
| --------- | --------------------------------------------------------------------------- |
| `console` | Human-readable table (default) — timestamp, stage, status, duration, commit |
| `text`    | Plain text — one line per deployment, suitable for piping                   |
| `json`    | Machine-readable JSON array — for SIEM integration, audit tooling, jq       |
| `ndjson`  | Newline-delimited JSON — one record per line, streaming-friendly            |

Per-deployment record includes:
- Timestamp, stage, success, duration
- Commit SHA + message + author
- List of YAML files changed (via `git diff` between consecutive deployments)
- Link to the deploy manifest JSON

**Future Extension:** `strata audit diff <sha1> <sha2>` for side-by-side YAML value diffs.

**Auditor Evidence:** Structured report of all configuration changes, their source (git commit), and deployment evidence, suitable for export to SIEM/audit tooling.

**Implementation:** New CLI command group and `ChangesAuditCommand` class.

### Layer 4: SIEM Integration + Remote Persistence (Azure Sentinel)

**Purpose:** Provide a platform-wide immutable event sink for all security, audit, and compliance events — not just deployment logs. The `ISiemSink` capability is reusable across the entire strata platform.

#### Platform Event Source Matrix

The `ISiemSink` protocol serves as the universal forwarding interface for all audit-relevant events in strata:

| Event Source                       | `log_type`          | Producer                 | Sink Destinations          | Priority                        |
| ---------------------------------- | ------------------- | ------------------------ | -------------------------- | ------------------------------- |
| **Deployment evidence** (this ADR) | `deploy_audit`      | `AuditController`        | SIEM + remote + local disk | P0 — compliance-critical        |
| **CLI user actions** (Issue #43)   | `cli_action`        | Application audit logger | SIEM + local NDJSON        | P0 — who did what               |
| **Policy violations**              | `policy_violation`  | `PolicyController`       | SIEM + local disk          | P1 — guardrail failures         |
| **Secret access**                  | `secret_access`     | `ValueController`        | SIEM                       | P1 — who resolved which secrets |
| **State lock events**              | `lock_event`        | `RunDeployCommand`       | SIEM + local disk          | P2 — concurrency audit          |
| **Validation results**             | `validation_result` | `ValidateCommand`        | SIEM + local disk          | P2 — pre-deploy evidence        |
| **Drift detection** (future)       | `drift_alert`       | `DriftController`        | SIEM                       | P3 — infrastructure drift       |
| **Build events**                   | `build_event`       | `BuildCommand`           | SIEM + local disk          | P3 — artifact provenance        |

**Design principle:** Each producer calls `sink.send_event(log_type, payload)`. The sink doesn't care what the event is — it forwards structured JSON. The `log_type` field enables filtering, routing, and retention policies at the SIEM level (e.g., different DCR streams per event type in Sentinel).

**Phased adoption:** Only `deploy_audit` and `cli_action` are in scope for this ADR. Other event sources adopt `ISiemSink` incrementally as their features are built — no big-bang migration required.

**Architecture:**

```
ISiemSink (capability protocol)
    │
    ▼
SiemBaseIntegration (extends BaseIntegration)
    │
    ├── SentinelIntegration (Azure Log Analytics / Sentinel)
    ├── ElkSiemIntegration (Elasticsearch / Logstash — structured audit events)
    ├── OtelSiemIntegration (OTLP exporter — any OTel-compatible backend)
    ├── (future) SplunkIntegration
    └── (future) CloudTrailIntegration
```

**Relationship to operational logging:**

The existing `LogstashHandler` in `src/strata/logger/handlers.py` ships *operational logs* (structlog output) to ELK via TCP — configured in `logging.yaml`. The `ElkSiemIntegration` and `OtelSiemIntegration` ship *structured audit events* (compliance records) via the `ISiemSink` protocol. Both can target the same ELK stack but carry different data:

| Channel                         | Data                                                     | Format                           | Configured In                     |
| ------------------------------- | -------------------------------------------------------- | -------------------------------- | --------------------------------- |
| `LogstashHandler` (operational) | Application logs, debug, warnings                        | structlog JSON lines             | `logging.yaml`                    |
| `ElkSiemIntegration` (audit)    | Compliance events (deploy_audit, policy_violation, etc.) | Structured JSON per event schema | `configuration.spec.integrations` |
| `OtelSiemIntegration` (audit)   | Same compliance events via OTLP                          | OTel Log Records                 | `configuration.spec.integrations` |

**New Capability Protocol — `ISiemSink`:**

A **platform-wide** capability protocol — not specific to deployment audit. Any component that produces audit-relevant events can use this interface to forward them to external immutable stores.

```python
@runtime_checkable
class ISiemSink(Protocol):
    """Capability: Integration supports forwarding structured events to an immutable audit store.
    
    Used by: AuditController (deploy logs), application audit logger (CLI actions),
    PolicyController (violations), ValueController (secret access), and future producers.
    """

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        """Send a single structured event to the sink.
        
        Args:
            log_type: Event category (deploy_audit, cli_action, policy_violation, etc.)
            payload: Structured JSON payload for the event
        """
        ...

    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        """Send a batch of structured events to the sink."""
        ...
```

**`SiemBaseIntegration`** (extends `BaseIntegration`):
- Provides shared HTTP transport (retry, timeout, auth header construction)
- Implements `ISiemSink` protocol with abstract `send_event` / `send_batch`
- Handles failure gracefully — SIEM unavailability must NOT block deployment
- Logs warnings on failed delivery; never raises

**`SentinelIntegration`** (extends `SiemBaseIntegration`):
- Uses Azure Monitor Logs Ingestion API (DCR-based, replaces deprecated Data Collector API)
- Authentication via Azure Identity (DefaultAzureCredential — supports managed identity, service principal, CLI login)
- Configuration via environment YAML (see Audit Policy & Sink Configuration section below)

**Effort Estimate:**
- `ISiemSink` capability + registration: ~1 hour (follows existing `ISecretStore` pattern)
- `SiemBaseIntegration` base class: ~2–4 hours (HTTP transport, retry, auth)
- `SentinelIntegration`: ~4–6 hours (Azure Identity auth, DCR API, payload shaping)
- `ElkSiemIntegration`: ~2–4 hours (TCP/HTTP transport, index templating — leverages existing `LogstashHandler` patterns)
- `OtelSiemIntegration`: ~2–3 hours (OTel Logs SDK already installed — emit Log Records via OTLP exporter)
- `AuditController` SIEM forwarding: ~2 hours (iterate sinks, fire-and-forget)
- Tests: ~6 hours (mock HTTP/TCP/gRPC responses, auth, failure scenarios)
- **Total: ~3–4 days** — feasible as the final phase

**Non-blocking design:** SIEM delivery failures are logged but never fail a deployment. The deploy-log JSON on disk (Layer 2) is always the source of truth; SIEM is a secondary, best-effort sink.

**Remote Persistence (via configured remotes):**

The `AuditController` can also commit the deploy-log JSON to one or more configured remotes — the same remotes used by `strata deploy run` for deployment artifacts. This makes the audit output part of the deployment manifest/artifacts:

- Deploy-log JSON is written to the remote's `.strata/deploy-log/` directory
- Uses existing `GitIntegration` to commit + push to the remote (same pattern as deployment manifest persistence)
- Remotes are declared in `configuration.spec.remotes` (type: `gitops`) and referenced in `spec.deployment.audit.remote`

**File location — configurable path structure:**

The deploy-log path below `audit.path` is **configurable via a template**. The template uses tokens resolved from the deployment context at execution time. This follows the same layering available in the configuration — tenant, workspace, deployment, stage — so users compose the structure that fits their organization.

#### Path Template Configuration

```yaml
audit:
  path: .strata/deploy-log                              # base directory (constant)
  structure: "{{ deployment }}/{{ timestamp }}"          # Jinja2 template — directory layout below base path
  file_per_stage: true                                   # true: one file per stage; false: single execution.json
  remote: xyz-configuration
```

#### Available Tokens

Tokens are resolved from the deployment's own metadata — no new config, just references to what's already declared:

| Token                | Source                                           | Example Value          | Always Available       |
| -------------------- | ------------------------------------------------ | ---------------------- | ---------------------- |
| `{{ tenant }}`       | `meta.labels.tenant` or `spec.properties.tenant` | `example-xyz`          | No — omitted if absent |
| `{{ deployment }}`   | `meta.name`                                      | `xyz_platform_prd`     | Yes                    |
| `{{ workspace }}`    | `spec.workspace.name`                            | `xyz_platform`         | Yes                    |
| `{{ environment }}`  | `spec.layers.environment`                        | `prd`                  | Yes                    |
| `{{ stage }}`        | Stage name (at execution time)                   | `infrastructure`       | Yes (per-stage)        |
| `{{ timestamp }}`    | Execution start (ISO 8601)                       | `2026-06-24T14:32:00Z` | Yes                    |
| `{{ date }}`         | Date portion only (YYYY-MM-DD)                   | `2026-06-24`           | Yes                    |
| `{{ version }}`      | `meta.labels.version`                            | `1.0.0`                | No — omitted if absent |
| `{{ region }}`       | `spec.properties.region`                         | `eu-west-1`            | No — omitted if absent |
| `{{ labels.* }}`     | Any `meta.labels` key                            | (varies)               | No — per label         |
| `{{ properties.* }}` | Any `spec.properties` key                        | (varies)               | No — per property      |

**Template resolution rules (Jinja2 — per ADR 0017):**
- Rendered via `TemplateProcessor` (same engine used for scaffolding, build templates, etc.).
- Uses `jinja2.Undefined` (not `StrictUndefined`) — missing optional variables render as empty string.
- Empty path segments (from missing optional tokens) are automatically stripped.
- Supports Jinja2 conditionals for advanced layouts: `{% if tenant %}{{ tenant }}/{% endif %}{{ deployment }}`.
- All `meta.labels.*` and `spec.properties.*` keys are available in the template context — no special syntax needed.

> DOCUMENTATION ! This needs to be documented in the `strata deploy run` reference and in the configuration schema docs, so users know how to configure their audit log paths.

#### Path Definitions (`spec.deployment.paths`)

Path templates are declared **explicitly in the configuration YAML** — not hidden in code. Users can see exactly what's available, override built-in presets, or define their own named paths. This follows the same principle as integrations: declare in configuration, reference by name.

**Built-in defaults (shipped with strata, always available):**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: xyz_configuration
spec:
  deployment:
    paths:
      # Built-in presets — override or extend as needed
      flat: "{{ deployment }}"
      by-stage: "{{ deployment }}/{{ stage }}"
      by-execution: "{{ deployment }}/{{ timestamp }}"
      by-tenant: "{{ tenant }}/{{ deployment }}/{{ timestamp }}"
      full: "{{ tenant }}/{{ workspace }}/{{ deployment }}/{{ timestamp }}"

      # Custom paths — define your own for your organization
      by-region: "{{ region }}/{{ tenant }}/{{ deployment }}/{{ timestamp }}"
      compliance: "{{ tenant }}/{{ environment }}/{{ deployment }}/{{ date }}"
      per-customer: "{{ properties.costcenter }}/{{ tenant }}/{{ deployment }}/{{ timestamp }}"
```

> The 'standard' built-in paths are `flat`, `by-stage`, `by-execution`, `by-tenant`, and `full`. Users can override these or add new ones in their configuration YAML.
> Added in the template configuration file that is available to the user, so they can see and modify it as needed.

**Referencing a path definition:**

```yaml
spec:
  deployment:
    audit:
      structure: by-execution         # references spec.deployment.paths["by-execution"]
      # OR inline a custom template directly (Jinja2):
      structure: "{% if tenant %}{{ tenant }}/{% endif %}{{ deployment }}/{{ timestamp }}"
```

**Resolution order:**
1. Look up `structure` value in `spec.deployment.paths` (named reference)
2. If not found as a name, treat it as an inline Jinja2 template directly
3. If `structure` is absent, use default: `by-stage` for local, `by-tenant` for dedicated audit remotes

**Why in `spec.deployment.paths`?**
- **Visible** — users see available options in the configuration file, not hidden in source
- **Overridable** — redefine `by-tenant` to include region if your org needs it
- **Extensible** — add organization-specific named paths without touching code
- **Reusable** — same path definitions can be referenced by `audit.structure`, `manifest.structure`, or future output paths
- **Documented** — `strata config list --paths` shows all available path definitions

**Validation:**
- Path definition values must be valid Jinja2 templates (validated at config load time)
- Referenced path names must exist in `spec.deployment.paths` (validation error otherwise)
- Circular references are impossible (paths are simple string templates, not expressions)

```yaml
# Use a named path from spec.deployment.paths:
audit:
  structure: by-execution         # → "{{ deployment }}/{{ timestamp }}"

# Or write your own inline (Jinja2 syntax — same as all other strata templates):
audit:
  structure: "{{ tenant }}/{{ environment }}/{{ deployment }}/{{ date }}"

# With conditionals for optional segments:
audit:
  structure: "{% if tenant %}{{ tenant }}/{% endif %}{{ deployment }}/{{ timestamp }}"
```

**Default:** `by-stage` for local workspace, `by-tenant` for dedicated audit remotes (auto-detected when `audit.remote` differs from the configuration remote).

#### File Layout Within Execution

When `file_per_stage: true` (default), each stage produces its own file. When `false`, a single `execution.json` contains all stages:

**`file_per_stage: true` (default) — one file per stage:**

```
{resolved_structure}/
├── infrastructure.json         ← stage result
├── platform.json
├── services.json
└── _execution.json             ← overall execution metadata (optional summary)
```

**`file_per_stage: false` — single file per execution:**

```
{resolved_structure}/
└── execution.json              ← contains all stage results in one file
```

#### Examples by Preset

**Preset: `by-stage` (default for local workspace):**

```
.strata/
├── build/
│   └── xyz_platform_prd-1.0.0/            ← existing build output pattern
└── deploy-log/                              ← audit.path
    └── xyz_platform_prd/                    ← {{ deployment }}
        ├── infrastructure/                  ← {{ stage }}
        │   ├── 2026-06-24T14:32:00Z.json
        │   └── 2026-06-25T09:15:00Z.json
        ├── platform/
        │   └── 2026-06-24T14:35:00Z.json
        └── services/
            └── 2026-06-24T14:40:00Z.json
```

**Preset: `by-execution` (execution-grouped):**

```
.strata/deploy-log/
└── xyz_platform_prd/                        ← {{ deployment }}
    ├── 2026-06-24T14:32:00Z/                ← {{ timestamp }} — one run
    │   ├── _execution.json                  ← overall: execution_id, success, duration, commit_sha
    │   ├── infrastructure.json              ← stage result
    │   ├── platform.json
    │   └── services.json
    └── 2026-06-25T09:15:00Z/                ← partial re-deploy
        ├── _execution.json
        └── infrastructure.json              ← only infra ran
```

**Preset: `by-tenant` (dedicated shared audit remote):**

```
audit-trail/                                  ← dedicated audit remote repo root
└── deploy-log/                               ← audit.path
    ├── example-xyz/                          ← {{ tenant }}
    │   ├── xyz_platform_prd/                 ← {{ deployment }}
    │   │   ├── 2026-06-24T14:32:00Z/
    │   │   │   ├── _execution.json
    │   │   │   ├── infrastructure.json
    │   │   │   ├── platform.json
    │   │   │   └── services.json
    │   │   └── 2026-06-25T09:15:00Z/
    │   │       └── infrastructure.json
    │   └── xyz_platform_stg/                 ← different deployment, same tenant
    │       └── 2026-06-24T10:00:00Z/
    │           └── infrastructure.json
    └── acme-corp/                            ← {{ tenant }} — different customer/team
        └── abc_platform_prd/
            └── 2026-06-23T16:45:00Z/
                └── infrastructure.json
```

**Preset: `full` (enterprise — maximum isolation):**

```
deploy-log/
└── example-xyz/                              ← {{ tenant }}
    └── xyz_platform/                         ← {{ workspace }}
        └── xyz_platform_prd/                 ← {{ deployment }}
            └── 2026-06-24T14:32:00Z/         ← {{ timestamp }}
                ├── _execution.json
                ├── infrastructure.json
                └── services.json
```

#### Path Construction Logic

Uses the existing `TemplateProcessor` (ADR 0017) — no new template engine. Path definitions are loaded from configuration, not hardcoded:

```python
from strata.utils.templater import TemplateProcessor

# Built-in defaults (used when spec.deployment.paths is absent or incomplete)
BUILTIN_PATH_DEFINITIONS: dict[str, str] = {
    "flat": "{{ deployment }}",
    "by-stage": "{{ deployment }}/{{ stage }}",
    "by-execution": "{{ deployment }}/{{ timestamp }}",
    "by-tenant": "{{ tenant }}/{{ deployment }}/{{ timestamp }}",
    "full": "{{ tenant }}/{{ workspace }}/{{ deployment }}/{{ timestamp }}",
}

def resolve_deploy_log_path(
    self,
    base_path: Path,
    structure: str,
    path_definitions: dict[str, str],
    context: DeploymentContext,
    stage: str | None = None,
) -> Path:
    """Resolve the deploy-log directory path from Jinja2 template + deployment context.

    Resolution:
    1. Look up `structure` in path_definitions (from spec.deployment.paths)
    2. Fall back to BUILTIN_PATH_DEFINITIONS
    3. If not found, treat `structure` as an inline Jinja2 template
    """
    # Resolve named path → Jinja2 template
    template = path_definitions.get(
        structure,
        BUILTIN_PATH_DEFINITIONS.get(structure, structure),
    )

    # Build context from deployment metadata
    template_context = {
        "tenant": context.tenant or "",
        "deployment": context.deployment_name,
        "workspace": context.workspace_name,
        "environment": context.environment,
        "stage": stage or "",
        "timestamp": context.execution_timestamp,
        "date": context.execution_timestamp[:10],
        "version": context.version or "",
        "region": context.region or "",
        # All labels and properties are available directly
        **{k: v for k, v in context.labels.items()},
        **{f"properties.{k}": v for k, v in context.properties.items()},
    }

    # Render via Jinja2 (TemplateProcessor handles the engine)
    rendered = TemplateProcessor.render_string(template, template_context)

    # Strip empty segments (from missing optional tokens)
    segments = [s for s in rendered.split("/") if s.strip()]

    return base_path / Path(*segments)
```

#### Configuration in `spec.deployment`

Both `manifest` and `audit` reference the same `paths` definitions — consistent structure across all deployment outputs:

```yaml
spec:
  deployment:
    paths:                                  # shared path templates — used by manifest AND audit
      flat: "{{ deployment }}"
      by-stage: "{{ deployment }}/{{ stage }}"
      by-execution: "{{ deployment }}/{{ timestamp }}"
      by-tenant: "{{ tenant }}/{{ deployment }}/{{ timestamp }}"
      full: "{{ tenant }}/{{ workspace }}/{{ deployment }}/{{ timestamp }}"

    manifest:
      path: .strata/manifests               # base directory
      structure: by-execution               # references spec.deployment.paths
      file_per_stage: true                  # true = one JSON per stage; false = single file
      remote: xyz-configuration             # gitops remote to push to

    audit:
      path: .strata/deploy-log              # base directory
      structure: by-execution               # references spec.deployment.paths
      file_per_stage: true                  # true = one JSON per stage; false = single file
      remote: xyz-configuration             # gitops remote to push to
      include_in_manifest: true             # cross-reference: embed audit path in manifest
```

This means `manifest` and `audit` outputs can use the **same structure** (or different ones) — both resolve from the same `paths` map. They are mirrors: same shape, different purpose.

| Field                 | `manifest`                          | `audit`                             |
| --------------------- | ----------------------------------- | ----------------------------------- |
| `path`                | Base directory for output           | Base directory for output           |
| `structure`           | References `paths` or inline Jinja2 | References `paths` or inline Jinja2 |
| `file_per_stage`      | One file per stage or single file   | One file per stage or single file   |
| `remote`              | Gitops remote to push to            | Gitops remote to push to            |
| `include_in_manifest` | —                                   | Cross-reference audit in manifest   |

**Purpose differs, shape is identical:**
- `manifest` → **what was deployed** (build artifacts, provisioner outputs, resolved values)
- `audit` → **proof it happened** (execution evidence, timing, who, why, success/failure)

```yaml
# Same structure — co-located, easy to correlate:
manifest:
  path: .strata/manifests
  structure: by-execution       # → .strata/manifests/xyz_platform_prd/2026-06-24T14:32:00Z/
audit:
  path: .strata/deploy-log
  structure: by-execution       # → .strata/deploy-log/xyz_platform_prd/2026-06-24T14:32:00Z/

# Different structures — manifests versioned, audit timestamped:
manifest:
  path: .strata/manifests
  structure: "{{ deployment }}/{{ version }}"    # → .strata/manifests/xyz_platform_prd/1.0.0/
audit:
  path: .strata/deploy-log
  structure: by-execution                        # → .strata/deploy-log/xyz_platform_prd/2026-06-24T14:32:00Z/

# Shared audit remote with tenant isolation:
manifest:
  path: .strata/manifests
  structure: by-execution
  remote: xyz-configuration               # manifests stay in config repo
audit:
  path: deploy-log
  structure: by-tenant                    # → deploy-log/example-xyz/xyz_platform_prd/2026-06-24T14:32:00Z/
  remote: audit-trail                     # audit goes to shared audit repo
```

**Full configuration YAML with structure override:**

```yaml
# Single team, local workspace — simple structure
audit:
  path: .strata/deploy-log
  structure: by-stage                       # references spec.deployment.paths["by-stage"]
  remote: xyz-configuration

# Multi-team shared audit repo — tenant isolation
audit:
  path: deploy-log
  structure: by-tenant                      # references spec.deployment.paths["by-tenant"]
  file_per_stage: true
  remote: audit-trail

# Enterprise with custom named path from configuration
audit:
  path: deploy-log
  structure: compliance                     # references spec.deployment.paths["compliance"] (user-defined)
  file_per_stage: true
  remote: audit-central

# Inline Jinja2 template (no named reference needed)
audit:
  path: .strata/deploy-log
  structure: "{% if tenant %}{{ tenant }}/{% endif %}{{ deployment }}/{{ date }}"
  file_per_stage: false
```

**Interaction with `strata audit changes`:** The query command reads the `structure` setting to know how to traverse the directory tree. It doesn't need to know the layout at compile time — it resolves the same template in reverse (pattern matching) to find all deploy-log entries regardless of structure.

The deploy-log lives in the **same repository** as the configuration that caused the change (co-located), or in a dedicated audit repository shared across teams (dedicated remote). Protected branches on either repo prevent history rewriting.

**Configuration with audit remote:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: xyz_configuration
  annotations:
    description: "Production configuration with audit remote"
  labels:
    version: "1.0.1"
    tenant: "example-xyz"
spec:
  remotes:
    - name: platform-iac
      type: bundled
      repository: /
      reference: main
      source_path: deploy
      description: "Platform IaC deployment artifacts"

    - name: platform-modules
      type: gitops
      repository: https://github.com/org/platform-modules.git
      reference: main
      source_path: modules
      description: "Platform application modules"

    - name: xyz-configuration
      type: gitops
      repository: https://github.com/org/xyz-configuration.git
      reference: main
      description: "Configuration repo — audit trail committed here"

  deployment:
    paths:                                # named path templates — visible, overridable, reusable
      flat: "{{ deployment }}"
      by-stage: "{{ deployment }}/{{ stage }}"
      by-execution: "{{ deployment }}/{{ timestamp }}"
      by-tenant: "{{ tenant }}/{{ deployment }}/{{ timestamp }}"
      full: "{{ tenant }}/{{ workspace }}/{{ deployment }}/{{ timestamp }}"
      # Add your own:
      # compliance: "{{ tenant }}/{{ environment }}/{{ deployment }}/{{ date }}"

    manifest:
      path: .strata/manifests         # base directory for deployment manifest artifacts
      structure: by-execution          # references spec.deployment.paths["by-execution"]
      file_per_stage: true            # true: one JSON per stage + _execution.json; false: single file
      remote: xyz-configuration       # name of the gitops remote to push manifest commits to

    audit:
      path: .strata/deploy-log        # base directory for deploy-log JSON files (Layer 2 output)
      structure: by-execution          # references spec.deployment.paths["by-execution"]
      file_per_stage: true            # true: one JSON per stage + _execution.json; false: single file
      remote: xyz-configuration       # name of the gitops remote to push deploy-log commits to
      include_in_manifest: true       # cross-reference: embed deploy-log path in manifest artifact

  integrations:
    - name: sentinel_prod
      type: sentinel
      capabilities: [audit]
      required: false
      enabled: true
      endpoints:
        address: https://<dce>.ingest.monitor.azure.com
      authentication:
        method: managed_identity
      properties:
        data_collection_rule_id: dcr-xxxxxxxxxxxxxxxx
        stream_name: Custom-DeployAudit_CL

    - name: elk_audit
      type: elk
      capabilities: [audit]
      required: false
      enabled: true
      endpoints:
        address: logstash.internal:5000
      properties:
        protocol: tcp
        index_pattern: strata-audit-{yyyy.MM.dd}
        codec: json

    - name: otel_audit
      type: otel
      capabilities: [audit]
      required: false
      enabled: true
      endpoints:
        address: https://otel-collector.internal:4317
      authentication:
        method: bearer_token
        api_key:
          api_key: "@vault/otel-bearer-token"
      properties:
        protocol: grpc
        resource_attributes:
          service.name: strata-audit
          deployment.environment: production
```

**Full deployment YAML (reference example):**

This shows how all layers come together — workspace, environments (with base audit policy), configuration (with remotes + integrations), and stages:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: xyz_platform_prd
  annotations:
    description: "Production deployment — full audit trail enabled"
  labels:
    version: "1.0.0"
    environment: "production"
    tenant: "example-xyz"
  tags: ["platform", "infrastructure", "production"]
spec:
  layers:
    environment: prd

  properties:
    deployment_type: production
    tenant: example-xyz
    region: eu-west-1

  custom:
    costcenter: "platform-ops"
    project: xyz

  workspace:
    name: xyz_platform
    description: "XYZ platform workspace"
    file: "@xyz_configuration/stack/xyz-ws-platform.yaml"

  environments:
    - "@xyz_configuration/environments/base.yaml"           # audit policy defaults (P0+P1 on, file sink)
    - "@xyz_configuration/environments/production.yaml"     # enables SIEM sinks + P2 events

  configurations:
    - name: xyz_configuration
      description: "Production configuration with remotes and integrations"
      file: "@xyz_configuration/config/xyz-configuration.yaml"

  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop
    - name: platform
      provisioner: helm
      depends_on: [infrastructure]
      on_failure: stop
    - name: services
      topology: core_services
      depends_on: [platform]

  variables:
    - key: DEPLOY_ENVIRONMENT
      store: constant
      value: production

  secrets:
    - key: TERRAFORM_API_TOKEN
      store: bitwarden
      value: d47e736b-2db8-47d5-b46b-b2c8016ece73
```

**What happens at deploy time (audit flow):**

1. `strata deploy run --file deploy/xyz-platform-prd.yaml`
2. `AuditController` assembles deploy-log payload from git context + deployment results
3. Enriches with PR data (if commit maps to a merged PR)
4. Resolves path via `audit.structure` template → writes `.strata/deploy-log/xyz_platform_prd/2026-06-24T14:32:00Z/infrastructure.json` to local disk
5. Embeds reference in deployment manifest (because `include_in_manifest: true`)
6. Forwards to SIEM sinks (resolved from merged environment: sentinel_prod + elk_audit + otel_audit)
7. Commits + pushes deploy-log to `xyz-configuration` remote (because `audit.remote` is set)

When `include_in_manifest: true`, the deploy-log entry is referenced in the `DeploymentManifestModel.spec.artifacts` as an audit artifact — linking the deployment manifest to its audit evidence in a single, queryable record.

**Delivery order:**
1. Write deploy-log JSON to local disk (always, Layer 2)
2. Embed reference in deployment manifest if `include_in_manifest: true`
3. Forward to SIEM sinks (fire-and-forget)
4. Commit + push to configured remote (fire-and-forget)

Steps 3 and 4 are non-blocking — failures warn but never fail the deployment.

**Auditor Evidence:** Immutable records in Azure Sentinel with built-in retention policies, tamper-proof Log Analytics workspace (RBAC-controlled), and native KQL queries for compliance reporting. Additionally, deploy-log records committed to the configuration remote provide git-based immutability (protected branches) and co-location with the infrastructure changes they document.

### Audit Policy & Sink Configuration

**Purpose:** Let the user control _what_ gets logged (event policy) and _where_ events are sent (sink routing) — configured at the **environment** level, not the workspace.

**Why environment, not workspace?** The workspace defines _what to build_ (infrastructure blueprint). The environment defines _how to run_ (runtime behaviour, compliance requirements, connectivity). Audit policy and SIEM endpoints are operational concerns that vary per deployment target — production needs full SIEM forwarding; dev only needs local disk. This belongs in the environment layer.

#### Base Environment Pattern

Since deployments already support multiple environments applied in order (later overrides earlier), a **base environment** provides shared audit defaults that all deployments inherit:

```
config/environments/
  base.yaml            ← shared audit policy + sinks (always first)
  production.yaml      ← env-specific: enables all P1/P2 events, production SIEM endpoint
  staging.yaml         ← env-specific: overrides sink to staging workspace
  development.yaml     ← env-specific: disables SIEM, file sink only
```

Every deployment references the base first:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
spec:
  environments:
    - "@config/environments/base.yaml"         # audit defaults applied first
    - "@config/environments/production.yaml"   # production overrides (later wins)
```

This means audit config is defined once, inherited everywhere, and overridable per environment — no duplication.

#### Policy: What Gets Logged

Users configure which event sources are active in `spec.audit.policy.events`. Each event source from the Platform Event Source Matrix can be individually enabled or disabled. Defaults are opinionated toward compliance (deployment and CLI actions always on):

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: base
  annotations:
    description: "Shared audit policy — always applied first in deployment environments[]"
spec:
  audit:
    policy:
      # (additional policies can be layered per environment, e.g. customer-specific ones)
    events:
      deploy_audit: true         # default: true  — deployment evidence (P0)
      cli_action: true           # default: true  — who ran what (P0)
      policy_violation: true     # default: true  — guardrail failures (P1)
      secret_access: true        # default: true  — who resolved secrets (P1)
      lock_event: false          # default: false — concurrency audit (P2)
      validation_result: false   # default: false — pre-deploy evidence (P2)
      drift_alert: false         # default: false — infrastructure drift (P3)
      build_event: false         # default: false — artifact provenance (P3)
```

**Rules:**
- If `spec.audit.policy` is absent, no events are enabled by default.
- Individual event types can be toggled without affecting others.
- A later environment in the deployment's `environments[]` can override specific event flags (merge by key).
- The `AuditController` reads the resolved policy at startup and skips `send_event()` calls for disabled types — zero overhead for disabled sources.

#### Sinks: Where Events Are Sent

Users configure one or more sinks under `spec.audit.sinks`. Each sink **references an integration by name** (declared in `configuration.spec.integrations`) and optionally filters which events it receives. The environment does NOT redeclare connection details — it only controls routing and enablement.

**Separation of concerns:**

| Layer                             | Owns                                                                     | Example                                                              |
| --------------------------------- | ------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| `configuration.spec.integrations` | Integration instances: type, endpoints, auth, validation                 | "sentinel_prod connects to this DCE with this DCR"                   |
| `environment.spec.audit.sinks`    | Sink routing: which integrations to use, enabled/disabled, event filters | "production routes deploy_audit + policy_violation to sentinel_prod" |

**Base environment (shared defaults):**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: base
spec:
  audit:
    sinks:
      - name: ci_stdout
        type: stdout               # built-in: emit to stdout for CI pipeline capture
        enabled: true
      # No other sinks — base stays lightweight. Production adds SIEM.
      # Local disk (per-execution JSONs) is always-on — not a sink.
```

**Production environment (adds SIEM routing):**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: production
spec:
  audit:
    policy:
      events:
        lock_event: true           # production enables more event types
        validation_result: true
    sinks:
      - name: ci_stdout
        type: stdout
        enabled: true

      - name: sentinel_prod
        integration: sentinel_prod   # references configuration.spec.integrations[].name
        enabled: true
        events: [deploy_audit, policy_violation, secret_access]

      - name: splunk_corp
        integration: splunk_corp     # references configuration.spec.integrations[].name
        enabled: false
```

**Development environment (minimal):**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: development
spec:
  audit:
    policy:
      events:
        deploy_audit: true
        cli_action: true
        policy_violation: false    # dev doesn't need policy violation logging
        secret_access: false
    sinks:
      - name: ci_stdout
        type: stdout
        enabled: true
      # no SIEM sinks — dev stays local (baseline disk + stdout only)
```

**Meanwhile, the integrations are declared once in `configuration.yaml`:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
spec:
  integrations:
    - name: sentinel_prod
      type: sentinel
      capabilities: [audit]
      required: false
      enabled: true
      description: "Production Azure Sentinel for compliance audit trail"
      endpoints:
        address: https://<dce>.ingest.monitor.azure.com
      authentication:
        method: managed_identity
      properties:
        data_collection_rule_id: dcr-xxxxxxxxxxxxxxxx
        stream_name: Custom-DeployAudit_CL

    - name: splunk_corp
      type: splunk
      capabilities: [audit]
      required: false
      enabled: false
      description: "Corporate Splunk HEC (disabled until migration)"
      endpoints:
        address: https://splunk.corp.example.com:8088
      authentication:
        method: api_key
        api_key:
          api_key: "@vault/splunk-hec-token"
      properties:
        index: audit_events

    - name: elk_audit
      type: elk
      capabilities: [audit]
      required: false
      enabled: true
      description: "ELK stack — audit events via Logstash TCP or Elasticsearch API"
      endpoints:
        address: logstash.internal:5000       # Logstash TCP input
      properties:
        protocol: tcp                         # tcp | http (Elasticsearch direct)
        index_pattern: strata-audit-{yyyy.MM.dd}
        codec: json

    - name: otel_audit
      type: otel
      capabilities: [audit]
      required: false
      enabled: true
      description: "OTel OTLP exporter — routes audit events to any OTel-compatible backend"
      endpoints:
        address: https://otel-collector.internal:4317   # OTLP gRPC endpoint
      authentication:
        method: bearer_token
        api_key:
          api_key: "@vault/otel-bearer-token"
      properties:
        protocol: grpc                        # grpc | http
        resource_attributes:
          service.name: strata-audit
          deployment.environment: production
```

This follows the exact same pattern as existing integrations (git, terraform, bitwarden, consul) — the configuration declares *what's available*, the environment controls *what's used*.

**New capability: `audit`** — added to the existing capability set (`api`, `container`, `infrastructure`, `secrets`, `variables`, etc.). Integrations with `capabilities: [audit]` are eligible as SIEM sinks.

**Sink types:**

Sinks come in two flavors: **built-in types** (lightweight, no integration config needed) and **integration references** (full SIEM integrations declared in `configuration.spec.integrations`).

**Built-in types** (lightweight — no integration needed):

| Type      | Description                                                                                     | Config Required         |
| --------- | ----------------------------------------------------------------------------------------------- | ----------------------- |
| `stdout`  | Emit JSON to stdout — CI pipelines (GitHub Actions, Azure Pipelines) capture this automatically | None                    |
| `ndjson`  | Append events to a single NDJSON file (streaming log, easy to `tail -f`)                        | `path` only             |
| `syslog`  | Forward to syslog (RFC 5424) — standard on Linux, no external service                           | `address` only          |
| `webhook` | HTTP POST to a URL (fire-and-forget, no auth)                                                   | `url`, optional headers |

**Integration-backed sinks** (full SIEM — declared in `configuration.spec.integrations`):

| Integration Type | Integration Class                | Description                                                        |
| ---------------- | -------------------------------- | ------------------------------------------------------------------ |
| `sentinel`       | `SentinelIntegration`            | Azure Monitor Logs Ingestion API via DCR                           |
| `elk`            | `ElkSiemIntegration`             | Elasticsearch/Logstash — structured audit events (TCP or HTTP API) |
| `otel`           | `OtelSiemIntegration`            | OTLP exporter — any OTel-compatible backend (gRPC or HTTP)         |
| `splunk`         | `SplunkIntegration` (future)     | Splunk HTTP Event Collector                                        |
| `cloudtrail`     | `CloudTrailIntegration` (future) | AWS CloudTrail Lake                                                |

**What about local disk?** Local disk (the per-execution JSON files written by `AuditController`) is **not a sink** — it's always-on baseline behavior. The `audit.path` + `audit.structure` config controls where those files go. Sinks are *additional* forwarding destinations beyond the baseline.

**Note on ELK vs. operational logging:** The `elk` sink type sends *audit events* (structured compliance records) — not application logs. Application logs already flow to ELK via the `LogstashHandler` configured in `logging.yaml`. Both channels can coexist on the same ELK stack using different indices (`strata-audit-*` vs. `strata-logs-*`).

**Note on OTel:** The `opentelemetry-api` and `opentelemetry-sdk` packages are already dependencies. The `OtelSiemIntegration` uses the OTel Logs SDK to emit audit events as OTel Log Records via OTLP. This means any OTel-compatible backend (ELK via OTel Collector, Grafana Loki, Datadog, etc.) can receive audit events without a dedicated integration class — OTel is the universal adapter.

**Rules:**
- Sinks are **optional** — if no sinks are configured, only baseline local disk writes happen (always-on).
- `enabled: false` sinks are loaded but not invoked — allows quick toggling without removing config.
- Sinks that reference an `integration` name are validated against `configuration.spec.integrations` — missing reference = validation error.
- Sink secrets live in the integration's `authentication` block (never in the environment YAML).
- Sinks merge by `name` across environments — a later environment can override `enabled` or `events` for a named sink.
- Built-in types (`stdout`, `ndjson`, `syslog`, `webhook`) don't need an integration reference.

#### Routing: Which Events Go Where

By default, all enabled events go to all enabled sinks. For fine-grained control, sinks can declare an `events` filter:

```yaml
    sinks:
      - name: sentinel_prod
        integration: sentinel_prod
        enabled: true
        events: [deploy_audit, policy_violation, secret_access]  # only these types

      - name: local_disk
        type: ndjson
        enabled: true
        path: .strata/audit-stream.ndjson  # streaming NDJSON — easy to tail/grep
        # no 'events' filter → receives ALL enabled event types
```

**Resolution logic in `AuditController`:**
1. Resolve `spec.audit` from the merged environment stack (deployment `environments[]` applied in order).
2. Check `policy.events[log_type]` — if disabled, skip entirely.
3. For each enabled sink: if `sink.events` is defined, only forward if `log_type` is in the list; otherwise forward all.
4. Fire-and-forget to each matching sink (non-blocking, parallel).

#### Merge Semantics

Since environments compose via the deployment's `environments[]` array (later overrides earlier):

| Field                      | Merge Strategy                                                         |
| -------------------------- | ---------------------------------------------------------------------- |
| `policy.events`            | Merge by key — later environment overrides individual event flags      |
| `sinks`                    | Merge by `name` — later environment overrides matching sink properties |
| New sink name in later env | Appended to sink list                                                  |

This means:
- Base defines the defaults (all sinks, conservative policy)
- Production adds/enables SIEM sinks and enables more event types
- Development disables unnecessary events and SIEM sinks
- No environment needs to redeclare the full config — only overrides

#### Model

```python
class AuditPolicyModel(PlatformBaseModel):
    """Configures which event types are logged."""
    events: dict[str, bool] = Field(default_factory=lambda: {
        "deploy_audit": True,
        "cli_action": True,
        "policy_violation": True,
        "secret_access": True,
        "lock_event": False,
        "validation_result": False,
        "drift_alert": False,
        "build_event": False,
    })

class AuditSinkModel(PlatformBaseModel):
    """A configured audit sink — forwards events to a built-in type or integration."""
    name: PlatformName
    type: str | None = None                  # built-in: "stdout", "ndjson", "syslog", "webhook"
    integration: PlatformName | None = None  # references configuration.spec.integrations[].name
    enabled: bool = True
    events: list[str] | None = None          # None = all events
    # Type-specific config (only for built-in types):
    path: str | None = None                  # ndjson only
    address: str | None = None               # syslog only
    url: str | None = None                   # webhook only
    headers: dict[str, str] | None = None    # webhook only

    @model_validator(mode="after")
    def validate_sink_target(self) -> "AuditSinkModel":
        """Exactly one of 'type' or 'integration' must be set."""
        if not self.type and not self.integration:
            raise ValueError("Sink must specify either 'type' (file) or 'integration' (SIEM reference)")
        if self.type and self.integration:
            raise ValueError("Sink cannot specify both 'type' and 'integration'")
        return self

class AuditConfigModel(PlatformBaseModel):
    """Top-level audit configuration under spec.audit in environment YAML."""
    policy: AuditPolicyModel = Field(default_factory=AuditPolicyModel)
    sinks: list[AuditSinkModel] = Field(default_factory=list)
```

**Integration with `EnvironmentSpecModel`:**

```python
class EnvironmentSpecModel(PlatformBaseModel):
    ...
    audit: AuditConfigModel | None = None  # new field
```

## Consequences

### Good

- **User-controlled policy** — operators decide which events matter for their compliance posture; no forced logging of irrelevant event types
- **Configurable sinks** — each workspace declares its own audit destinations; supports multi-cloud, hybrid, or file-only setups without code changes
- **Multiple evidence sources** — auditors see both _intent_ (PR) and _proof_ (deploy manifest) and _reporting_ (audit changes command)
- **Immutable record** — all artifacts committed to git; audit trail cannot be unilaterally deleted by a single actor
- **Minimal friction** — Layer 2 and 3 are automatic; only Layer 1 (PR template) requires operator discipline
- **SIEM-friendly** — structured JSON output supports integration with external audit platforms
- **Separates concerns** — Layer 2 (deployment proof) is independent of Application Audit Log (Issue #43), avoiding confusion
- **Scalable** — design supports future external immutable storage (Azure Blob, Cosmos DB) without breaking Layer 1–3
- **Non-repudiation** — Azure Sentinel provides tamper-proof storage independent of git; satisfies ISAE 3402 non-repudiation requirement
- **Extensible SIEM pattern** — `SiemBaseIntegration` base class allows adding Splunk, CloudTrail, or custom sinks without modifying core deploy logic
- **ELK stack reuse** — audit events flow to the same ELK stack as operational logs (different index), leveraging existing infrastructure
- **OTel as universal adapter** — `OtelSiemIntegration` routes to any OTel-compatible backend without per-vendor integration code; packages already installed
- **Platform-wide reuse** — `ISiemSink` protocol is not deployment-specific; application audit logs, policy violations, secret access, and future event sources all use the same forwarding interface

### Bad

- **Four implementation phases** — Layers must be done in order (Layer 1 < Layer 2 < Layer 3 < Layer 4); staggered rollout
- **Azure dependency** — Layer 4 requires `azure-identity` and `azure-monitor-ingestion` packages; acceptable (prefer official SDKs over custom implementations)
- **Process dependency** — Layer 1 (PR template) is outside strata's control; strata only governs its own commands, not git/PR behaviour of humans
- **Git mutability assumption** — if repo admin force-pushes audit commits, the trail is damaged; acceptable risk (SIEM/OTel sinks provide immutable copy, branch protection mitigates)
- **Clock dependency** — timestamps rely on system clock accuracy; acceptable for v1 (use `datetime.now(UTC)`)
- **Not retroactive** — existing deployments have no audit trail; new trail starts after this feature ships; acceptable for v1

### Design Notes

- **`ISiemSink` is a platform capability, not a deployment feature:** The sink protocol is designed for broad reuse. When Issue #43's application audit logger gains SIEM forwarding, it uses the same `ISiemSink` instances already configured in the workspace. No new integration setup required — just call `send_event("cli_action", payload)`. This avoids per-feature SIEM configuration sprawl.

- **Separation from Application Audit Log (Issue #43):** Issue #43 (configurable NDJSON audit log) captures _who ran which commands_ for user action tracking. Issue #28 (deployment manifests) captures _which infrastructure changed and why_. Both are valuable but serve different audiences (security team vs. compliance team). Both can forward to the same `ISiemSink` with different `log_type` values.

- **Git as the primary audit store:** Version control is the most accessible and auditable storage for small-to-medium deployments. For FIPS 140-2 or air-gapped environments, future layers will support external immutable stores.

- **Why commit post-deployment, not pre?** Committing the deploy manifest AFTER deployment succeeds ensures the record reflects reality (actual values deployed). Pre-deployment manifests would require rollback tracking to maintain accuracy.

### Implementation Decisions

These decisions were surfaced during design review and must be respected during implementation:

1. **Git commit is the audit anchor; PR/MR is optional enrichment.** The deploy-log is complete and auditable with `commit_sha` + `commit_message` + `commit_author` alone — these are always available, zero dependencies. The `pull_request` section is one possible source context enrichment (GitHub-specific). Future enrichment sources (Azure DevOps PRs via `az repos pr`, GitLab MRs, Jira links) can be added as sibling nullable fields without breaking the schema. No enrichment source is required for a valid audit record. PR lookup uses GitHub's commit→PR API (`gh api /repos/{owner}/{repo}/commits/{sha}/pulls`) — NOT commit message regex. This works for all merge strategies (merge commit, squash, rebase) because GitHub tracks the association internally. If `gh` is unavailable, the repo isn't on GitHub, or the commit has no associated PR (direct push), `pull_request` is `null` — valid, no error. A `strata audit enrich` backfill command exists for records written without enrichment data, but the primary path is inline best-effort during `deploy run`.

2. **Audit delivery never fails a deployment.** SIEM sink failures and remote push failures emit WARNING — never ERROR, never affect exit code. A transient network fault in Sentinel must not rollback a database migration. The deploy-log JSON is already safe on local disk (always written first) and in the git remote (if configured). SIEM is a forwarding convenience, not a persistence layer. A `strata audit resend` command exists to re-send existing local deploy-log records to configured sinks (e.g., after a transient outage resolves). Usage: `strata audit resend --since 2026-06-24` or `strata audit resend --last 5`. It reads local JSONs, resolves current sink config, and re-forwards. Idempotent — sinks handle duplicates via `execution_id`.

3. **Concurrent push conflicts.** The `push_to_remote()` method uses a pull-rebase-push retry (max 3 attempts). Deploy-log files are unique per execution (timestamp + execution_id in path), so content conflicts are impossible — only ref conflicts from concurrent pushes. A simple `git pull --rebase && git push` resolves this. If all retries fail, emit WARNING (per decision #2) and the record remains on local disk for `strata audit resend` later.

4. **`strata audit changes` uses file discovery, not reverse template resolution.** The query command recursively finds all `*.json` files under `audit.path`, parses each, and filters by field values (`timestamp`, `stage`, `deployment`). It does NOT attempt to reverse-resolve the path template. The directory structure is for human navigation; the query command reads file contents.

5. **`_execution.json` is always written.** It is not optional. Written after all stages complete (or after a stage fails with `on_failure: stop`). Contains: `execution_id`, overall `success`, total `duration_seconds`, `commit_sha`, `commit_author`, list of stages that ran. Per-stage JSONs contain stage-specific detail. The execution file is the single entry point for auditors.

6. **`execution_id` is a UUID4** generated once at the start of `deploy run` and shared across all stage JSONs and the `_execution.json` for that run. Format: standard lowercase UUID (`550e8400-e29b-41d4-a716-446655440000`).

7. **Sink type-specific field validation.** `AuditSinkModel` must validate that type-specific fields match the declared `type`:
   - `type: stdout` — no extra fields allowed
   - `type: ndjson` — `path` required, no others
   - `type: syslog` — `address` required, no others
   - `type: webhook` — `url` required, `headers` optional, no others
   - `integration` set — no type-specific fields allowed
   
   Implemented as a `model_validator(mode="after")`.

8. **Sink merge semantics are deep-merge by `name`.** When a later environment declares a sink with the same `name` as an earlier one, fields from the later sink override the earlier — but unset fields inherit from the earlier definition. This matches how environment `spec.variables` merge today. Example: base sets `enabled: true`, production sets `events: [deploy_audit]` → result is `enabled: true, events: [deploy_audit]`.

9. **`spec.deployment.manifest` is forward-looking.** The `manifest` section under `spec.deployment` is designed here but not implemented until a separate ADR/issue addresses deployment manifest artifacts. The `audit` section ships first. The shared `paths` definitions are immediately useful for audit; manifest adopts them later. No coupling risk — if manifest ships with a different shape, `paths` still works independently. Resolved.

10. **Implementation phases refer to layers, not the phase table.** The "Bad" bullet "Four implementation phases" refers to the 4 architectural layers that must ship in dependency order. The implementation plan table breaks this into 7 work phases for scheduling granularity. Both are correct at different abstraction levels. Resolved.

## Implementation Plan

| Phase    | Layer   | Work                                                      | Owner         | Est. Time |
| -------- | ------- | --------------------------------------------------------- | ------------- | --------- |
| Phase 1  | Layer 1 | Document PR template pattern; no code                     | Documentation | 1–2 hours |
| Phase 2  | Layer 2 | `AuditController` + deploy-log JSON writing               | Backend       | 2–3 days  |
| Phase 3  | Layer 3 | `strata audit changes` command via `AuditController`      | Backend       | 1–2 days  |
| Phase 4  | Layer 4 | `ISiemSink` capability + `SiemBaseIntegration` base class | Backend       | 1 day     |
| Phase 5  | Layer 4 | `SentinelIntegration` (Azure Monitor Logs Ingestion API)  | Backend       | 2–3 days  |
| Phase 5b | Layer 4 | `ElkSiemIntegration` + `OtelSiemIntegration`              | Backend       | 1–2 days  |
| Phase 6  | All     | Integration tests across all layers                       | QA            | 1–2 days  |
| Phase 7  | All     | Documentation and audit guide                             | DevRel        | 1–2 days  |

## More Information

- **GitHub Issue:** #28
- **Related ADR:** [0005 (Secret resolution at build time)](0005-secret-resolution-at-build-time.md), [0003 (Layered architecture)](0003-layered-architecture.md)
- **Compliance Standards:** ISO 27001:2022 A.12.1.2, ISAE 3402 Type II controls for change management

---

## Questions for Review

1. **~~External immutable storage:~~** ✅ Resolved — Layer 4 (Azure Sentinel) provides external immutable storage. Additional backends (Azure Blob, S3) can be added as future `SiemBaseIntegration` subclasses.
2. **~~Validation audit trail:~~** ✅ Resolved — No. `validate` and `build` are development-time commands (run locally, iteratively, during authoring). Writing manifests for them would generate noise, not compliance evidence. Only `deploy run` produces audit-worthy records because it mutates infrastructure. If CI wants pre-deploy evidence, the `validation_result` event type already forwards to SIEM sinks — that's sufficient without disk manifests.
3. **~~User identity logging:~~** ✅ Resolved — Git committer is good enough. In CI, the committer is the service principal or bot that merged. Locally, it's the developer. Adding `os.getlogin()` is unnecessary complexity for no audit value.
4. **~~Clock skew:~~** ✅ Resolved — Use system `datetime.now(UTC)`. If the machine clock is wrong, that's an infrastructure problem outside strata's scope. UTC is sufficient for audit ordering.

---

## Implementation Design

This section provides the concrete technical design to guide implementation. It maps directly to the architecture and decisions above, specifying exact file paths, class definitions, method signatures, integration points, and data flow.

### New Files to Create

```
src/strata/
├── controllers/
│   └── audit_controller.py              # AuditController (Layer 2 + 4 orchestration)
├── commands/
│   ├── cli_audit.py                     # Click group: strata audit
│   └── audit/
│       ├── __init__.py
│       ├── changes_command.py           # strata audit changes (Layer 3)
│       └── resend_command.py            # strata audit resend
├── models/
│   ├── deploy_log_model.py             # DeployLogModel, DeployLogStageModel, etc.
│   └── audit_config_model.py           # AuditPolicyModel, AuditSinkModel, AuditConfigModel
├── integrations/
│   └── siem/
│       ├── __init__.py
│       ├── base_siem_integration.py    # SiemBaseIntegration + ISiemSink protocol
│       ├── sentinel_integration.py     # Azure Sentinel (Phase 5)
│       ├── elk_siem_integration.py     # ELK audit events (Phase 5b)
│       └── otel_siem_integration.py    # OTel OTLP exporter (Phase 5b)
tests/strata/
├── controllers/
│   └── test_audit_controller.py
├── commands/audit/
│   ├── test_changes_command.py
│   └── test_resend_command.py
├── models/
│   ├── test_deploy_log_model.py
│   └── test_audit_config_model.py
└── integrations/siem/
    ├── test_base_siem_integration.py
    └── test_sentinel_integration.py
```

### Existing Files to Modify

| File                                               | Change                                                                                                                  |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `src/strata/commands/deploy/run_deploy_command.py` | Call `AuditController.write_deploy_log()` in `_finalize()`                                                              |
| `src/strata/commands/cli.py`                       | Register `audit_group` via `main.add_command(audit_group, name="audit")`                                                |
| `src/strata/integrations/git/git.py`               | Add `add()`, `commit()`, `push()` methods                                                                               |
| `src/strata/integrations/capabilities.py`          | Add `ISiemSink` protocol + register in `CAPABILITY_MAP`                                                                 |
| `src/strata/models/environment_model.py`           | Add `audit: Optional[AuditConfigModel] = None` to `EnvironmentSpecModel`                                                |
| `src/strata/models/configuration_model.py`         | Add `audit: Optional[ConfigurationAuditModel]` to `ConfigurationDeploymentModel`; add `paths: Optional[Dict[str, str]]` |
| `src/strata/services/configuration_service.py`     | Update `get_deploy_log_path()` to read from `spec.deployment.audit.path` when available                                 |
| `src/strata/utils/config.py`                       | No changes — `SOLUTION_DEPLOY_LOG_DIR` already defined                                                                  |

---

### Model Definitions

#### `src/strata/models/deploy_log_model.py`

```python
"""Deploy-log models — Layer 2 audit evidence."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import Field

from strata.models.common_models import PlatformBaseModel, PlatformName


class DeployLogStepModel(PlatformBaseModel):
    """A single provisioner step within a stage."""

    step: str                                    # "setup", "check", "plan", "apply", "destroy"
    success: bool
    duration_seconds: float


class DeployLogStageModel(PlatformBaseModel):
    """Per-stage deployment result."""

    name: PlatformName
    provisioner: Optional[str] = None
    topology: Optional[str] = None
    success: bool
    started_at: str                              # ISO 8601 UTC
    completed_at: str
    duration_seconds: float
    steps: List[DeployLogStepModel] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    messages: List[str] = Field(default_factory=list)


class DeployLogPullRequestModel(PlatformBaseModel):
    """PR/MR enrichment data — nullable, GitHub-specific for now."""

    number: int
    title: str
    url: str
    author: Optional[str] = None
    merged_by: Optional[str] = None
    merged_at: Optional[str] = None
    approvers: List[str] = Field(default_factory=list)
    labels: List[str] = Field(default_factory=list)
    linked_issues: List[str] = Field(default_factory=list)
    files_changed: List[str] = Field(default_factory=list)


class DeployLogModel(PlatformBaseModel):
    """Root deploy-log entry — one per execution.

    Written to .strata/deploy-log/{resolved_structure}/_execution.json
    and optionally per-stage as {stage}.json.
    """

    execution_id: str                            # UUID4
    timestamp: str                               # ISO 8601 UTC (execution start)
    command: str = "deploy_run"                   # CLI command that produced this
    version: str                                 # strata CLI version
    commit_sha: Optional[str] = None
    commit_message: Optional[str] = None
    commit_author: Optional[str] = None
    deployment: PlatformName                     # meta.name of deployment YAML
    workspace: Optional[PlatformName] = None
    environment: Optional[str] = None
    file: str                                    # deployment YAML path (relative)
    force: bool = False
    dry_run: bool = False
    success: bool
    duration_seconds: float
    stages: List[DeployLogStageModel] = Field(default_factory=list)
    pull_request: Optional[DeployLogPullRequestModel] = None
    errors: List[str] = Field(default_factory=list)
    messages: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)  # extensible


class DeployLogStageFileModel(PlatformBaseModel):
    """Per-stage JSON file — subset of DeployLogModel for one stage only."""

    execution_id: str
    timestamp: str
    version: str
    deployment: PlatformName
    stage: DeployLogStageModel
```

#### `src/strata/models/audit_config_model.py`

```python
"""Audit policy and sink configuration models — environment-level config."""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import Field, model_validator

from strata.models.common_models import PlatformBaseModel, PlatformName


class AuditPolicyModel(PlatformBaseModel):
    """Which event types are active. Configured in environment YAML."""

    events: Dict[str, bool] = Field(default_factory=lambda: {
        "deploy_audit": True,
        "cli_action": True,
        "policy_violation": True,
        "secret_access": True,
        "lock_event": False,
        "validation_result": False,
        "drift_alert": False,
        "build_event": False,
    })


class AuditSinkModel(PlatformBaseModel):
    """A configured audit event sink."""

    name: PlatformName
    type: Optional[str] = None                   # built-in: stdout, ndjson, syslog, webhook
    integration: Optional[PlatformName] = None   # references configuration.spec.integrations[].name
    enabled: bool = True
    events: Optional[List[str]] = None           # None = all enabled events

    # Type-specific fields (built-in sinks only):
    path: Optional[str] = None                   # ndjson
    address: Optional[str] = None                # syslog
    url: Optional[str] = None                    # webhook
    headers: Optional[Dict[str, str]] = None     # webhook

    @model_validator(mode="after")
    def validate_sink_target(self) -> "AuditSinkModel":
        if not self.type and not self.integration:
            raise ValueError("Sink must specify either 'type' or 'integration'")
        if self.type and self.integration:
            raise ValueError("Sink cannot specify both 'type' and 'integration'")
        return self

    @model_validator(mode="after")
    def validate_type_specific_fields(self) -> "AuditSinkModel":
        if self.integration:
            if any([self.path, self.address, self.url, self.headers]):
                raise ValueError("Integration-backed sinks must not have type-specific fields")
            return self
        match self.type:
            case "stdout":
                if any([self.path, self.address, self.url, self.headers]):
                    raise ValueError("stdout sink takes no extra fields")
            case "ndjson":
                if not self.path:
                    raise ValueError("ndjson sink requires 'path'")
                if any([self.address, self.url, self.headers]):
                    raise ValueError("ndjson sink only accepts 'path'")
            case "syslog":
                if not self.address:
                    raise ValueError("syslog sink requires 'address'")
                if any([self.path, self.url, self.headers]):
                    raise ValueError("syslog sink only accepts 'address'")
            case "webhook":
                if not self.url:
                    raise ValueError("webhook sink requires 'url'")
                if any([self.path, self.address]):
                    raise ValueError("webhook sink only accepts 'url' and 'headers'")
        return self


class AuditConfigModel(PlatformBaseModel):
    """Top-level audit config — lives under spec.audit in environment YAML."""

    policy: AuditPolicyModel = Field(default_factory=AuditPolicyModel)
    sinks: List[AuditSinkModel] = Field(default_factory=list)
```

---

### Controller Design

#### `src/strata/controllers/audit_controller.py`

```python
"""AuditController — orchestrates deploy-log writing, PR enrichment, SIEM forwarding, and remote push."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional, Tuple

from strata.controllers.base_controller import BaseController
from strata.models.audit_config_model import AuditConfigModel, AuditSinkModel
from strata.models.deploy_log_model import (
    DeployLogModel,
    DeployLogPullRequestModel,
    DeployLogStageFileModel,
)
from strata.utils import config
from strata.utils.templater import TemplateProcessor

if TYPE_CHECKING:
    from strata.integrations.git.git import GitIntegration
    from strata.integrations.siem.base_siem_integration import ISiemSink


class AuditController(BaseController):
    """Orchestrates Layer 2 (disk), Layer 4 (SIEM + remote) audit operations."""

    def __init__(
        self,
        work_path: Path,
        audit_config: Optional[AuditConfigModel] = None,
        siem_sinks: Optional[List["ISiemSink"]] = None,
        git_integration: Optional["GitIntegration"] = None,
    ):
        super().__init__()
        self._work_path = work_path
        self._audit_config = audit_config or AuditConfigModel()
        self._siem_sinks = siem_sinks or []
        self._git = git_integration

    def generate_execution_id(self) -> str:
        """Generate UUID4 execution identifier."""
        return str(uuid.uuid4())

    def write_deploy_log(
        self,
        payload: DeployLogModel,
        structure: str = "by-stage",
        path_definitions: Optional[dict[str, str]] = None,
        base_path: Optional[Path] = None,
        file_per_stage: bool = True,
    ) -> Tuple[bool, Path]:
        """Write deploy-log JSON to disk, forward to SIEM, push to remote.

        Returns:
            (success, path_to_execution_json)

        Always writes to disk first. SIEM and remote failures emit WARNING only.
        """
        ...

    def _write_execution_json(self, payload: DeployLogModel, output_dir: Path) -> Path:
        """Write _execution.json — always written (decision #5)."""
        ...

    def _write_stage_files(self, payload: DeployLogModel, output_dir: Path) -> List[Path]:
        """Write per-stage JSON files when file_per_stage is True."""
        ...

    def _resolve_output_dir(
        self,
        structure: str,
        path_definitions: dict[str, str],
        base_path: Path,
        payload: DeployLogModel,
    ) -> Path:
        """Resolve deploy-log directory from Jinja2 template + deployment context."""
        ...

    def enrich_with_pr_data(self, payload: DeployLogModel) -> DeployLogModel:
        """Query GitHub for PR that produced commit_sha. Best-effort, WARNING on failure.

        Uses: gh api /repos/{owner}/{repo}/commits/{sha}/pulls
        Sets payload.pull_request or leaves it None.
        """
        ...

    def forward_to_siem(self, payload: DeployLogModel) -> None:
        """Fire-and-forget forward to all enabled SIEM sinks matching policy. WARNING only on failure."""
        ...

    def push_to_remote(self, paths: List[Path], remote_name: str) -> bool:
        """Commit + push deploy-log files to configured remote.

        Retry: pull-rebase-push, max 3 attempts (decision #3).
        Returns True on success, False + WARNING on exhausted retries.
        """
        ...

    def resend(
        self,
        since: Optional[str] = None,
        last: Optional[int] = None,
        base_path: Optional[Path] = None,
    ) -> Tuple[int, int]:
        """Re-forward existing local deploy-log records to current sinks.

        Returns (sent_count, failed_count). Idempotent via execution_id.
        """
        ...

    def query_deploy_logs(
        self,
        base_path: Path,
        since: Optional[str] = None,
        stage: Optional[str] = None,
        last: Optional[int] = None,
    ) -> List[DeployLogModel]:
        """File discovery: recursively find *.json under base_path, parse, filter (decision #4)."""
        ...
```

**Key method — `write_deploy_log` flow:**

```
1. resolve_output_dir(structure, path_definitions, base_path, payload)
2. _write_execution_json(payload, output_dir)        ← always (decision #5)
3. _write_stage_files(payload, output_dir)           ← if file_per_stage
4. enrich_with_pr_data(payload)                      ← best-effort, mutates payload in place
5. Re-write _execution.json with enrichment          ← if PR data was found
6. forward_to_siem(payload)                          ← WARNING only (decision #2)
7. push_to_remote(written_paths, remote_name)        ← WARNING only (decision #2)
8. return (True, execution_json_path)
```

---

### Integration Points

#### Modification to `RunDeployCommand._finalize()`

```python
# In src/strata/commands/deploy/run_deploy_command.py

def _finalize(self, success: bool = False, show_footer: bool = True) -> None:
    """Extended to write deploy-log after deployment completes."""
    # --- NEW: Write deploy-log ---
    if self._deploy_started_at and not self._dry_run:
        self._write_deploy_log(success)
    # --- END NEW ---
    super()._finalize(success=success, show_footer=show_footer)

def _write_deploy_log(self, success: bool) -> None:
    """Assemble and write deploy-log via AuditController."""
    from strata.controllers.audit_controller import AuditController
    from strata.models.deploy_log_model import DeployLogModel, DeployLogStageModel, DeployLogStepModel

    # Assemble payload from already-captured data
    stages = []
    for stage_result in self._stage_results:
        stages.append(DeployLogStageModel(
            name=stage_result.name,
            provisioner=stage_result.provisioner,
            topology=stage_result.topology,
            success=(stage_result.status == "success"),
            started_at=stage_result.started_at,
            completed_at=stage_result.completed_at,
            duration_seconds=stage_result.duration_seconds,
            steps=[
                DeployLogStepModel(step=s.name, success=s.success, duration_seconds=s.duration_seconds)
                for s in (stage_result.steps or [])
            ],
            errors=stage_result.error or [],
        ))

    completed_at = _dt.now(_tz.utc).isoformat()
    duration = (
        _dt.fromisoformat(completed_at) - _dt.fromisoformat(self._deploy_started_at)
    ).total_seconds()

    payload = DeployLogModel(
        execution_id=self._execution_id,        # ← generated at _record_deploy_start
        timestamp=self._deploy_started_at,
        version=self._get_version(),
        commit_sha=self._get_commit_sha(),      # ← via GitIntegration.rev_parse
        commit_message=self._get_commit_message(),
        commit_author=self._get_commit_author(),
        deployment=self._deployment_service.model.meta.name,
        workspace=self._workspace_name,
        environment=self._environment_name,
        file=str(self._deployment_file),
        force=self._force,
        dry_run=False,
        success=success,
        duration_seconds=duration,
        stages=stages,
        errors=self._errors,
        messages=self._messages,
    )

    # Resolve audit config from merged environment
    audit_config = self._get_audit_config()
    audit_controller = AuditController(
        work_path=self._work_path,
        audit_config=audit_config,
        siem_sinks=self._resolve_siem_sinks(audit_config),
        git_integration=self._git_integration,
    )

    # Resolve path settings from configuration
    deploy_config = self._configuration_service.get_deployment_audit_config()
    audit_controller.write_deploy_log(
        payload=payload,
        structure=deploy_config.structure,
        path_definitions=deploy_config.path_definitions,
        base_path=deploy_config.base_path,
        file_per_stage=deploy_config.file_per_stage,
    )
```

#### New Git Methods — `src/strata/integrations/git/git.py`

```python
def add(self, working_dir: str, paths: List[str], timeout: int = 30) -> CommandResult:
    """Stage files for commit."""
    args = [self.COMMAND, "add", "--"] + paths
    return self._run_integration(args, cwd=working_dir, timeout=timeout)

def commit(self, working_dir: str, message: str, timeout: int = 30) -> CommandResult:
    """Create a commit with the given message."""
    args = [self.COMMAND, "commit", "-m", message]
    return self._run_integration(args, cwd=working_dir, timeout=timeout)

def push(
    self, working_dir: str, remote: str = "origin", branch: Optional[str] = None, timeout: int = 60
) -> CommandResult:
    """Push to remote. If branch is None, pushes current branch."""
    args = [self.COMMAND, "push", remote]
    if branch:
        args.append(branch)
    return self._run_integration(args, cwd=working_dir, timeout=timeout)

def pull_rebase(self, working_dir: str, remote: str = "origin", timeout: int = 60) -> CommandResult:
    """Pull with rebase — used for retry on ref conflicts."""
    args = [self.COMMAND, "pull", "--rebase", remote]
    return self._run_integration(args, cwd=working_dir, timeout=timeout)

def log(
    self, working_dir: str, format: str = "%H", count: int = 1, timeout: int = 30
) -> CommandResult:
    """Get git log entries."""
    args = [self.COMMAND, "log", f"--format={format}", f"-{count}"]
    return self._run_integration(args, cwd=working_dir, timeout=timeout)
```

#### New Capability Protocol — `src/strata/integrations/capabilities.py`

```python
@runtime_checkable
class ISiemSink(Protocol):
    """Capability: forwards structured events to an immutable audit store."""

    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool: ...
    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool: ...
```

Add to `CAPABILITY_MAP`:
```python
"audit": ISiemSink,
```

---

### CLI Commands

#### `src/strata/commands/cli_audit.py`

```python
"""strata audit — audit reporting and delivery commands."""

import click

from strata.commands.audit.changes_command import ChangesAuditCommand
from strata.commands.audit.resend_command import ResendAuditCommand
from strata.commands.cli_common import (
    click_output_format,
    click_output_quiet,
    click_output_verbose,
    click_work_path,
    handle_command_exit,
)


@click.group(name="audit")
def audit_group():
    """Deployment audit trail and compliance reporting."""
    pass


@audit_group.command(name="changes")
@click.pass_context
@click_work_path
@click_output_format
@click_output_verbose
@click_output_quiet
@click.option("--since", type=str, default=None, help="Filter entries from date (ISO 8601)")
@click.option("--stage", type=str, default=None, help="Filter by stage name")
@click.option("--last", type=int, default=None, help="Show last N deployments")
def changes_command(ctx, since, stage, last, **kwargs):
    """List deploy-log entries — audit evidence for compliance."""
    command = ChangesAuditCommand(ctx)
    success = command.execute(since=since, stage=stage, last=last)
    handle_command_exit(command, success)


@audit_group.command(name="resend")
@click.pass_context
@click_work_path
@click_output_verbose
@click_output_quiet
@click.option("--since", type=str, default=None, help="Resend entries from date (ISO 8601)")
@click.option("--last", type=int, default=None, help="Resend last N entries")
def resend_command(ctx, since, last, **kwargs):
    """Re-forward existing deploy-log records to configured SIEM sinks."""
    command = ResendAuditCommand(ctx)
    success = command.execute(since=since, last=last)
    handle_command_exit(command, success)
```

Register in `cli.py`:
```python
from strata.commands.cli_audit import audit_group
main.add_command(audit_group, name="audit")
```

---

### Data Flow — `strata deploy run` with Audit

```
┌─────────────────────────────────────────────────────────────────┐
│ RunDeployCommand.execute()                                       │
├─────────────────────────────────────────────────────────────────┤
│ 1. _initialize()                                                 │
│ 2. _record_deploy_start()                                        │
│    └─ self._execution_id = uuid4()          ← NEW               │
│ 3. _execute_provisioning()                                       │
│    └─ per stage: _record_stage_result()                          │
│ 4. _write_deployment_manifest()                                  │
│ 5. _finalize(success)                                            │
│    └─ _write_deploy_log(success)            ← NEW               │
│       ├─ Assemble DeployLogModel from self._stage_results        │
│       ├─ AuditController.write_deploy_log(payload)               │
│       │   ├─ resolve_output_dir()                                │
│       │   ├─ _write_execution_json()        ← always            │
│       │   ├─ _write_stage_files()           ← if file_per_stage │
│       │   ├─ enrich_with_pr_data()          ← best-effort       │
│       │   ├─ forward_to_siem()              ← WARNING only      │
│       │   └─ push_to_remote()               ← WARNING only      │
│       └─ Log result                                              │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow — `strata audit changes`

```
┌─────────────────────────────────────────────────────────────────┐
│ ChangesAuditCommand.execute()                                    │
├─────────────────────────────────────────────────────────────────┤
│ 1. Resolve base_path via ConfigurationService.get_deploy_log_path│
│ 2. AuditController.query_deploy_logs(base_path, since, stage)    │
│    └─ Recursively glob *.json under base_path (decision #4)      │
│    └─ Parse each as DeployLogModel                               │
│    └─ Filter by since/stage/last                                 │
│    └─ Sort by timestamp descending                               │
│ 3. Format output (console table / json / ndjson / text)          │
└─────────────────────────────────────────────────────────────────┘
```

### Data Flow — `strata audit resend`

```
┌─────────────────────────────────────────────────────────────────┐
│ ResendAuditCommand.execute()                                     │
├─────────────────────────────────────────────────────────────────┤
│ 1. Resolve base_path + audit_config from environment/config      │
│ 2. AuditController.resend(since, last)                           │
│    └─ query_deploy_logs() to find local records                  │
│    └─ For each record: forward_to_siem(record)                   │
│    └─ Sinks handle dedup via execution_id                        │
│ 3. Report: "{sent} records forwarded, {failed} failures"         │
└─────────────────────────────────────────────────────────────────┘
```

---

### SIEM Base Integration

#### `src/strata/integrations/siem/base_siem_integration.py`

```python
"""Base class for SIEM integrations — shared HTTP transport, retry, auth."""

from __future__ import annotations

import json
from abc import abstractmethod
from typing import Any, Dict, List, Optional

from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import ISiemSink
from strata.logger import get_logger

logger = get_logger(__name__)


class SiemBaseIntegration(BaseIntegration):
    """Abstract base for all SIEM sink integrations.

    Provides:
    - HTTP transport with configurable retry (max 3, exponential backoff)
    - Auth header construction (managed identity, bearer token, API key)
    - Graceful failure — always returns bool, never raises
    """

    CAPABILITIES = [ISiemSink]
    MAX_RETRIES: int = 3
    TIMEOUT_SECONDS: int = 30

    @abstractmethod
    def send_event(self, log_type: str, payload: dict, **kwargs) -> bool:
        """Send a single structured event. Returns True on success."""
        ...

    @abstractmethod
    def send_batch(self, log_type: str, payloads: List[dict], **kwargs) -> bool:
        """Send a batch. Returns True if all sent successfully."""
        ...

    def _send_http(
        self,
        method: str,
        url: str,
        body: Any,
        headers: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Shared HTTP transport with retry. Returns True on 2xx."""
        ...

    def _get_auth_headers(self) -> Dict[str, str]:
        """Build auth headers from integration config (method-dependent)."""
        ...
```

---

### Environment Model Integration

Add to `EnvironmentSpecModel`:

```python
# src/strata/models/environment_model.py
from strata.models.audit_config_model import AuditConfigModel

class EnvironmentSpecModel(PlatformBaseModel):
    lifecycle: Optional[CommonLifecycleModel] = None
    properties: Optional[Dict[str, Any]] = None
    custom: Optional[Dict[str, Any]] = None
    audit: Optional[AuditConfigModel] = None       # ← NEW
    overrides: Optional[EnvironmentOverridesModel] = None
    variables: Optional[List[VariableStoreModel]] = None
    secrets: Optional[List[SecretStoreModel]] = None
    features: Optional[List[FeatureStoreModel]] = None
```

### Configuration Model Integration

Add to `ConfigurationDeploymentModel`:

```python
# src/strata/models/configuration_model.py

class ConfigurationAuditModel(PlatformBaseModel):
    """Audit output config — under spec.deployment.audit."""
    path: str = f"{config.SOLUTION_DIR}/{config.SOLUTION_DEPLOY_LOG_DIR}"
    structure: str = "by-stage"
    file_per_stage: bool = True
    remote: Optional[PlatformName] = None
    include_in_manifest: bool = False

class ConfigurationDeploymentModel(PlatformBaseModel):
    additional_properties: bool = False
    properties: Optional[Dict[str, Union[str, ConfigurationSchemaField]]] = None
    manifest: Optional[ConfigurationManifestModel] = None
    outputs: Optional[ConfigurationOutputsModel] = None
    audit: Optional[ConfigurationAuditModel] = None     # ← NEW
    paths: Optional[Dict[str, str]] = None              # ← NEW — named path templates
```

---

### Path Resolution — BUILTIN_PATH_DEFINITIONS

```python
# In src/strata/controllers/audit_controller.py

BUILTIN_PATH_DEFINITIONS: dict[str, str] = {
    "flat": "{{ deployment }}",
    "by-stage": "{{ deployment }}/{{ stage }}",
    "by-execution": "{{ deployment }}/{{ timestamp }}",
    "by-tenant": "{{ tenant }}/{{ deployment }}/{{ timestamp }}",
    "full": "{{ tenant }}/{{ workspace }}/{{ deployment }}/{{ timestamp }}",
}
```

---

### Push-to-Remote Retry Logic

```python
def push_to_remote(self, paths: List[Path], remote_name: str) -> bool:
    """Commit and push deploy-log files. Max 3 retries on ref conflict."""
    if not self._git:
        self.logger.warning("git_integration_not_available", remote=remote_name)
        return False

    working_dir = str(self._work_path)
    rel_paths = [str(p.relative_to(self._work_path)) for p in paths]

    # Stage files
    result = self._git.add(working_dir, rel_paths)
    if not result.success:
        self.logger.warning("git_add_failed", error=result.stderr)
        return False

    # Commit
    result = self._git.commit(working_dir, message="chore(audit): deploy-log entry")
    if not result.success:
        self.logger.warning("git_commit_failed", error=result.stderr)
        return False

    # Push with retry (decision #3)
    for attempt in range(1, 4):
        result = self._git.push(working_dir, remote=remote_name)
        if result.success:
            return True
        # Ref conflict — pull rebase and retry
        self.logger.warning("git_push_retry", attempt=attempt, error=result.stderr)
        rebase_result = self._git.pull_rebase(working_dir, remote=remote_name)
        if not rebase_result.success:
            break

    # All retries exhausted — WARNING only (decision #2)
    self.logger.warning("git_push_failed_all_retries", remote=remote_name)
    return False
```

---

### PR Enrichment via GitHub API

```python
def enrich_with_pr_data(self, payload: DeployLogModel) -> DeployLogModel:
    """Best-effort PR lookup. Uses gh CLI (decision #1)."""
    if not payload.commit_sha:
        return payload

    if not self._git:
        return payload

    # Detect GitHub remote URL to extract owner/repo
    remote_url = self._git.get_remote_url(str(self._work_path))
    if not remote_url or "github" not in remote_url:
        return payload  # Not GitHub — skip silently

    owner_repo = self._parse_github_owner_repo(remote_url)
    if not owner_repo:
        return payload

    # gh api /repos/{owner}/{repo}/commits/{sha}/pulls
    try:
        result = self._run_gh_api(
            f"/repos/{owner_repo}/commits/{payload.commit_sha}/pulls"
        )
        if not result or not result[0]:
            return payload  # No PR found — valid (direct push)

        pr = result[0]  # First (most recent) associated PR
        payload.pull_request = DeployLogPullRequestModel(
            number=pr["number"],
            title=pr["title"],
            url=pr["html_url"],
            author=pr.get("user", {}).get("login"),
            merged_by=pr.get("merged_by", {}).get("login") if pr.get("merged_by") else None,
            merged_at=pr.get("merged_at"),
            labels=[l["name"] for l in pr.get("labels", [])],
        )
    except Exception:
        self.logger.warning("pr_enrichment_failed", commit=payload.commit_sha)

    return payload
```

---

### Testing Strategy

| Test File                       | Covers                                                                     | Approach                                        |
| ------------------------------- | -------------------------------------------------------------------------- | ----------------------------------------------- |
| `test_deploy_log_model.py`      | Model validation, serialization, optional fields                           | Valid + invalid YAML/dict inputs                |
| `test_audit_config_model.py`    | Sink validation (type/integration exclusive), type-specific fields         | Parameterized valid/invalid combos              |
| `test_audit_controller.py`      | write_deploy_log, path resolution, PR enrichment, SIEM forward, push retry | Mock `GitIntegration`, mock HTTP, mock `gh` CLI |
| `test_changes_command.py`       | CLI output formats, filtering, empty state                                 | `CliRunner.invoke()`, temp deploy-log dirs      |
| `test_resend_command.py`        | Resend flow, idempotency                                                   | Mock sinks, verify send_event calls             |
| `test_base_siem_integration.py` | HTTP transport, retry, auth header construction                            | Mock HTTP responses                             |
| `test_sentinel_integration.py`  | Azure auth, DCR payload shape                                              | Mock `azure.identity`, mock HTTP                |

**Test data:** Create fixture JSON files in `tests/data/deploy-log/` matching the expected schema.

---

### Implementation Order (Dependency Chain)

```
Phase 2 (Layer 2) — can start immediately:
  1. models/deploy_log_model.py + tests
  2. models/audit_config_model.py + tests
  3. controllers/audit_controller.py (write_deploy_log only, no SIEM)
  4. git.py: add commit/push/pull_rebase methods
  5. Modify run_deploy_command.py to call AuditController
  6. Update environment_model.py + configuration_model.py

Phase 3 (Layer 3) — depends on Phase 2 models:
  7. commands/audit/changes_command.py + cli_audit.py
  8. commands/audit/resend_command.py (SIEM part deferred to Phase 4)
  9. Register audit_group in cli.py

Phase 4 (Layer 4) — depends on Phase 2 controller:
  10. integrations/capabilities.py: add ISiemSink
  11. integrations/siem/base_siem_integration.py
  12. Wire SIEM forwarding into AuditController.forward_to_siem()
  13. Wire resend_command to use forward_to_siem

Phase 5 (Layer 4 — concrete sinks):
  14. sentinel_integration.py
  15. elk_siem_integration.py
  16. otel_siem_integration.py
```

---

## Incremental Implementation Guide

Each step below is a self-contained change that passes tests and doesn't break existing behaviour. Steps are ordered so each builds on the previous — no step requires forward knowledge. Commit after each.

---

### Step 1 — Deploy-Log Model (pure addition, no integration)

**Files:** `src/strata/models/deploy_log_model.py`, `tests/strata/models/test_deploy_log_model.py`

**What:**
- Create `DeployLogModel`, `DeployLogStageModel`, `DeployLogStepModel`, `DeployLogPullRequestModel`, `DeployLogStageFileModel`
- All Pydantic v2, extend `PlatformBaseModel`
- Zero imports from controllers/services/integrations

**Verify:** `uv run pytest tests/strata/models/test_deploy_log_model.py`

**Risk:** None. No existing code touched.

---

### Step 2 — Audit Config Model (pure addition, no integration)

**Files:** `src/strata/models/audit_config_model.py`, `tests/strata/models/test_audit_config_model.py`

**What:**
- Create `AuditPolicyModel`, `AuditSinkModel`, `AuditConfigModel`
- Validators: sink target exclusivity, type-specific field enforcement
- Zero imports from controllers/services/integrations

**Verify:** `uv run pytest tests/strata/models/test_audit_config_model.py`

**Risk:** None. No existing code touched.

---

### Step 3 — Git write methods (additive to existing integration)

**Files:** `src/strata/integrations/git/git.py`, `tests/strata/integrations/test_git.py`

**What:**
- Add methods: `add()`, `commit()`, `push()`, `pull_rebase()`, `log()`
- All follow existing pattern: build args list → `self._run_integration(args, cwd, timeout)`
- No changes to existing methods or constructor

**Verify:** `uv run pytest tests/strata/integrations/test_git.py`

**Risk:** Low. Only adds methods to an existing class — no signature changes.

---

### Step 4 — AuditController: local write only (no SIEM, no remote push)

**Files:** `src/strata/controllers/audit_controller.py`, `tests/strata/controllers/test_audit_controller.py`

**What:**
- Create `AuditController(BaseController)` with constructor (`work_path`, `audit_config`)
- Implement: `generate_execution_id()`, `_resolve_output_dir()`, `_write_execution_json()`, `_write_stage_files()`, `write_deploy_log()` (disk only)
- `BUILTIN_PATH_DEFINITIONS` constant
- Uses `TemplateProcessor.render()` for path resolution
- `forward_to_siem()` and `push_to_remote()` are stubs returning `False` / no-op
- `enrich_with_pr_data()` is a stub returning payload unchanged

**Verify:** `uv run pytest tests/strata/controllers/test_audit_controller.py`

**Risk:** None. New file, no existing code touched. Stubs mean it's safe to wire up without SIEM.

---

### Step 5 — Wire AuditController into deploy command

**Files:** `src/strata/commands/deploy/run_deploy_command.py`

**What:**
- In `_record_deploy_start()`: add `self._execution_id = str(uuid.uuid4())`
- Add private method `_write_deploy_log(self, success: bool) -> None`
  - Assembles `DeployLogModel` from existing `self._stage_results`
  - Instantiates `AuditController(work_path=self._work_path)`
  - Calls `audit_controller.write_deploy_log(payload, ...)`
  - Wrapped in try/except — failure logs WARNING, never raises (decision #2)
- In `_finalize()`: call `self._write_deploy_log(success)` if `not self._dry_run`
- Import: `uuid`, `DeployLogModel`, `DeployLogStageModel`, `DeployLogStepModel`, `AuditController`

**Verify:** `uv run pytest tests/strata/commands/deploy/` — existing deploy tests still pass. New test: mock `AuditController.write_deploy_log` is called.

**Risk:** Medium. Touches existing command. Mitigated by:
- Wrapped in try/except (can't break deploy)
- Only runs when `not self._dry_run`
- Controller stubs mean no real I/O beyond local file write

---

### Step 6 — Configuration model: add `audit` + `paths` fields

**Files:** `src/strata/models/configuration_model.py`, `tests/strata/models/test_configuration_model.py`

**What:**
- Add `ConfigurationAuditModel` (path, structure, file_per_stage, remote, include_in_manifest)
- Add to `ConfigurationDeploymentModel`: `audit: Optional[ConfigurationAuditModel] = None`, `paths: Optional[Dict[str, str]] = None`
- All fields Optional with defaults — existing YAML files remain valid

**Verify:** `uv run pytest tests/strata/models/test_configuration_model.py` — existing tests pass unchanged.

**Risk:** Low. All fields are `Optional` with defaults — no breaking change to existing configs.

---

### Step 7 — Environment model: add `audit` field

**Files:** `src/strata/models/environment_model.py`, `tests/strata/models/test_environment_model.py`

**What:**
- Add `audit: Optional[AuditConfigModel] = None` to `EnvironmentSpecModel`
- Import `AuditConfigModel` from `audit_config_model`

**Verify:** `uv run pytest tests/strata/models/test_environment_model.py`

**Risk:** Low. Optional field — existing environment YAMLs unaffected.

---

### Step 8 — Update `ConfigurationService.get_deploy_log_path()`

**Files:** `src/strata/services/configuration_service.py`

**What:**
- Modify existing `get_deploy_log_path()` to read `spec.deployment.audit.path` when the model is validated and the field exists
- Fall back to current constant-based logic (existing behaviour preserved)

**Verify:** `uv run pytest tests/strata/services/test_configuration_service.py`

**Risk:** Low. The existing fallback is unchanged — only adds a "check config first" path.

---

### Step 9 — `strata audit changes` command

**Files:** `src/strata/commands/audit/__init__.py`, `src/strata/commands/audit/changes_command.py`, `src/strata/commands/cli_audit.py`, `src/strata/commands/cli.py`, `tests/strata/commands/audit/test_changes_command.py`

**What:**
- Create `ChangesAuditCommand(BaseCommand)` — calls `AuditController.query_deploy_logs()`
- Implement `query_deploy_logs()` in controller: glob `*.json` → parse → filter → sort
- Create Click group in `cli_audit.py` with `changes` subcommand
- Register `audit_group` in `cli.py`: `main.add_command(audit_group, name="audit")`
- Supports `--since`, `--stage`, `--last`, standard output formats

**Verify:** `uv run pytest tests/strata/commands/audit/test_changes_command.py` + `uv run strata audit changes --help`

**Risk:** Low. New command group — no changes to existing commands.

---

### Step 10 — `strata audit resend` command (stub — forwards to local stdout only)

**Files:** `src/strata/commands/audit/resend_command.py`, `tests/strata/commands/audit/test_resend_command.py`

**What:**
- Create `ResendAuditCommand(BaseCommand)` — calls `AuditController.resend()`
- Implement `resend()` in controller: reads local JSONs, calls `forward_to_siem()` (still a stub → no-op)
- Reports count of records found (even though forwarding does nothing yet)

**Verify:** `uv run pytest tests/strata/commands/audit/test_resend_command.py`

**Risk:** None. Stub SIEM means it reads files and does nothing harmful.

---

### Step 11 — PR enrichment (activate stub)

**Files:** `src/strata/controllers/audit_controller.py`, `tests/strata/controllers/test_audit_controller.py`

**What:**
- Implement `enrich_with_pr_data()`: check for `gh` CLI, parse GitHub remote URL, call `gh api /repos/{owner}/{repo}/commits/{sha}/pulls`
- Add helper `_parse_github_owner_repo(url)` and `_run_gh_api(endpoint)`
- Uses `subprocess` via `_run_integration` pattern (through a lightweight `GhIntegration` or direct call)
- Wrapped in try/except — failure sets `pull_request = None`, logs WARNING

**Verify:** `uv run pytest tests/strata/controllers/test_audit_controller.py -k enrichment` — mock `gh` responses

**Risk:** Low. Best-effort, wrapped in try/except. Only called when `gh` is available + GitHub remote detected.

---

### Step 12 — Push to remote (activate stub)

**Files:** `src/strata/controllers/audit_controller.py`

**What:**
- Implement `push_to_remote()`: `git add` → `git commit` → retry loop (`git push`, on failure `git pull --rebase`)
- Uses `GitIntegration` methods from Step 3
- Max 3 attempts, WARNING on failure (decision #2 + #3)

**Verify:** `uv run pytest tests/strata/controllers/test_audit_controller.py -k push` — mock git commands

**Risk:** Low. Only runs when `audit.remote` is configured + `GitIntegration` is injected. No remote configured → no-op.

---

### Step 13 — ISiemSink capability protocol

**Files:** `src/strata/integrations/capabilities.py`, `tests/strata/integrations/test_capabilities.py`

**What:**
- Add `ISiemSink` protocol: `send_event(log_type, payload, **kwargs) -> bool`, `send_batch(log_type, payloads, **kwargs) -> bool`
- Add `"audit": ISiemSink` to `CAPABILITY_MAP`
- Add `"audit"` to `VALID_CAPABILITY_NAMES`

**Verify:** `uv run pytest tests/strata/integrations/test_capabilities.py`

**Risk:** None. Additive — no existing capabilities changed.

---

### Step 14 — SiemBaseIntegration

**Files:** `src/strata/integrations/siem/__init__.py`, `src/strata/integrations/siem/base_siem_integration.py`, `tests/strata/integrations/siem/test_base_siem_integration.py`

**What:**
- Abstract base class extending `BaseIntegration`
- Implements: `_send_http()` (retry + timeout), `_get_auth_headers()` (from config)
- Abstract: `send_event()`, `send_batch()`, `get_version_command()`, `parse_version()`

**Verify:** `uv run pytest tests/strata/integrations/siem/`

**Risk:** None. New files only.

---

### Step 15 — Wire SIEM forwarding into AuditController

**Files:** `src/strata/controllers/audit_controller.py`

**What:**
- Implement `forward_to_siem()`: iterate `self._siem_sinks`, check policy, call `sink.send_event("deploy_audit", payload.model_dump())`
- Each call wrapped individually — one sink failure doesn't block others
- All failures → WARNING (decision #2)
- Wire `resend()` to also call `forward_to_siem()`

**Verify:** `uv run pytest tests/strata/controllers/test_audit_controller.py -k siem`

**Risk:** Low. Only activates when sinks are configured + injected.

---

### Step 16 — SentinelIntegration (first concrete sink)

**Files:** `src/strata/integrations/siem/sentinel_integration.py`, `tests/strata/integrations/siem/test_sentinel_integration.py`

**What:**
- Extends `SiemBaseIntegration`
- Auth via `azure-identity` `DefaultAzureCredential`
- Sends to Azure Monitor Logs Ingestion API (DCR endpoint)
- `send_event()` shapes payload to match DCR stream schema
- Registered in `IntegrationFactory` for `type: sentinel`

**Verify:** `uv run pytest tests/strata/integrations/siem/test_sentinel_integration.py` — mock Azure auth + HTTP

**Risk:** Low. New file. Requires `azure-identity` + `azure-monitor-ingestion` in `pyproject.toml` (additive dep).

---

### Step 17 — ELK + OTel integrations

**Files:** `src/strata/integrations/siem/elk_siem_integration.py`, `src/strata/integrations/siem/otel_siem_integration.py`

**What:**
- `ElkSiemIntegration`: TCP or HTTP transport to Logstash/Elasticsearch
- `OtelSiemIntegration`: OTel Logs SDK → OTLP exporter (gRPC or HTTP)
- Both registered in `IntegrationFactory`

**Verify:** `uv run pytest tests/strata/integrations/siem/`

**Risk:** Low. New files. OTel packages already in deps.

---

### Checkpoint Summary

| After Step | What Works                                       | Deploy Command Impact |
| ---------- | ------------------------------------------------ | --------------------- |
| 4          | Models validated, controller writes JSON to disk | None                  |
| 5          | **Deploy writes audit JSON automatically**       | Minimal (try/except)  |
| 8          | Config-driven path resolution                    | None                  |
| 9          | `strata audit changes` queries local records     | None                  |
| 10         | `strata audit resend` scaffolded                 | None                  |
| 11         | PR data enriched in audit JSON (best-effort)     | None                  |
| 12         | Audit JSON pushed to git remote                  | None                  |
| 15         | SIEM forwarding active for configured sinks      | None                  |
| 17         | All three SIEM backends operational              | None                  |
| 18         | **Deployment manifest output via same infra**    | Minimal (try/except)  |

**Key principle:** After Step 5, every deployment produces local audit evidence. Everything after that is enrichment and delivery — all optional, all WARNING-only, all independently toggleable via config.

---

### Step 18 — Deployment Manifest Output (reuse path infrastructure)

**Rationale:** The `spec.deployment.manifest` config has the same shape as `spec.deployment.audit` — same `path`, `structure`, `file_per_stage`, `remote` fields, same `paths` definitions. The path resolution, template rendering, and git push logic already exist from the audit work. The manifest is just a different payload written to a different directory using the same machinery.

**Files:**
- `src/strata/models/deployment_manifest_output_model.py` (or extend existing `deployment_manifest_model.py`)
- `src/strata/controllers/manifest_controller.py`
- `src/strata/commands/deploy/run_deploy_command.py` (modify `_write_deployment_manifest()`)
- `tests/strata/controllers/test_manifest_controller.py`

**What:**
- Create `ManifestController(BaseController)` — mirrors `AuditController` structure:
  - `write_manifest(payload, structure, path_definitions, base_path, file_per_stage)` → writes JSON to resolved path
  - `push_to_remote(paths, remote_name)` → reuses same retry pattern (or extract shared base from `AuditController`)
  - No SIEM forwarding — manifests stay in git only
- Refactor: extract `_resolve_output_dir()` and push-retry logic into a shared mixin or utility (`controllers/output_writer.py`) so both `AuditController` and `ManifestController` reuse it without duplication
- The existing `DeploymentManifestService.save_with_config()` already writes manifests — `ManifestController` wraps it with the new path resolution + remote push
- Modify `_write_deployment_manifest()` in `RunDeployCommand`:
  - Read `spec.deployment.manifest` config (structure, path, remote)
  - If configured: resolve path via `paths` definitions (same as audit)
  - Write manifest JSON using existing `DeploymentManifestService.save()` but to the config-resolved path
  - Push to remote if `manifest.remote` is set (same retry logic)
  - Wrapped in try/except — failure logs WARNING, never affects deploy exit code

**Shared extraction (keeps it DRY):**

```python
# src/strata/controllers/output_writer.py — shared path resolution + git push

class OutputWriter:
    """Shared infrastructure for writing structured output (audit + manifest) to disk + remote."""

    def __init__(self, work_path: Path, git_integration: Optional[GitIntegration] = None):
        ...

    def resolve_output_dir(
        self, structure: str, path_definitions: dict[str, str], base_path: Path, context: dict
    ) -> Path:
        """Resolve output directory from named path or inline Jinja2 template."""
        ...

    def push_to_remote(self, paths: List[Path], remote_name: str) -> bool:
        """Commit + push with pull-rebase retry (max 3). WARNING only on failure."""
        ...
```

Both `AuditController` and `ManifestController` compose `OutputWriter` rather than duplicating the logic.

**Configuration (already designed in Step 6):**

```yaml
spec:
  deployment:
    paths:
      by-execution: "{{ deployment }}/{{ timestamp }}"
    manifest:
      path: .strata/manifests
      structure: by-execution
      file_per_stage: true
      remote: xyz-configuration
    audit:
      path: .strata/deploy-log
      structure: by-execution
      file_per_stage: true
      remote: xyz-configuration
```

**What the manifest gets you that the existing `DeploymentManifestService` doesn't:**
- Config-driven path structure (Jinja2 templates, named paths) instead of hardcoded `{name}_{timestamp}.json`
- Remote push to gitops repo (same retry logic as audit)
- `file_per_stage: true` option — per-stage manifest files alongside a summary
- Cross-reference with audit (`include_in_manifest: true` embeds deploy-log path)
- Same `strata deploy list` / `strata deploy history` commands can query both

**Verify:** `uv run pytest tests/strata/controllers/test_manifest_controller.py` + existing deploy tests still pass

**Risk:** Medium — same as Step 5. Modifies `_write_deployment_manifest()` which already works. Mitigated by:
- Try/except wrapper (can't break deploy)
- Falls back to existing `DeploymentManifestService.save_with_config()` if no manifest config present
- New path resolution only activates when `spec.deployment.manifest` is configured

---

## Implementation Status

**Status:** Implemented (Steps 1–17 of 18). ADR accepted.

**Implemented components:**

| Layer       | Component                                                         | Location                                         |
| ----------- | ----------------------------------------------------------------- | ------------------------------------------------ |
| Model       | `DeployLogModel`                                                  | `src/strata/models/deploy_log_model.py`          |
| Model       | `AuditConfigModel`                                                | `src/strata/models/audit_config_model.py`        |
| Integration | Git write methods (`add`, `commit`, `push`, `pull_rebase`, `log`) | `src/strata/integrations/git.py`                 |
| Controller  | `AuditController` (write, query, push, enrich, forward, resend)   | `src/strata/controllers/audit_controller.py`     |
| CLI         | `strata audit changes`                                            | `src/strata/commands/cli_audit.py`               |
| CLI         | `strata audit resend`                                             | `src/strata/commands/cli_audit.py`               |
| CLI         | `strata audit export`                                             | `src/strata/commands/cli_audit.py`               |
| Config      | `audit` field on `ConfigurationSpecModel`                         | `src/strata/models/configuration_model.py`       |
| Config      | `audit` field on `EnvironmentSpecModel`                           | `src/strata/models/environment_model.py`         |
| Manifest    | `audit_log` field in `DeploymentManifestSpecModel`                | `src/strata/models/deployment_manifest_model.py` |

**Test coverage:** 183 tests covering models, integrations, controllers, and CLI commands.

