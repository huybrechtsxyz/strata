# Deployment workflow orchestration — work items and hand-off gates

- Status: proposed
- Date: 2026-07-27

## Context and Problem Statement

Infrastructure deployments are not always fully automated. At key points in the
build/deploy lifecycle, execution must **pause**, hand control to an external actor
(human or system), and **resume** once a decision is made. Today strata has no general
mechanism for this.

The problem is broader than "approval before deploy". Every scenario below shares the
same structure:

> Execute to a decision point → create a work item → notify interested parties →
> wait for external resolution → resume or abort.

| Scenario              | Trigger                                         | Resolver            | Decision                |
| --------------------- | ----------------------------------------------- | ------------------- | ----------------------- |
| Approval gate         | Destructive changes / high-risk plan            | Human operator      | approve / reject        |
| Cost approval         | Infracost delta > budget threshold              | Finance / team lead | approve / reject        |
| Security review       | SBOM audit finds critical CVEs                  | Security team       | accept risk / block     |
| Change Advisory Board | Production change (ITIL compliance)             | CAB meeting         | approve / reject        |
| Manual verification   | Post-deploy health checks need human eyeballs   | Operator            | complete / escalate     |
| Scheduled deployment  | Deploy window starts at 02:00 UTC               | Clock / "go" signal | proceed / cancel        |
| Incident gate         | Deploy failed, needs investigation before retry | On-call engineer    | retry / rollback        |
| Promotion gate        | Version ready for ring progression              | Release manager     | advance / hold          |
| Drift decision        | Drift detected, action needed                   | Ops team            | reconcile / acknowledge |
| Rollback decision     | Post-deploy monitoring shows degradation        | Operator            | rollback / accept       |

Building ten separate hand-off mechanisms would produce duplicated code, inconsistent
UX, and fragmented audit trails. The right approach is to build **one general
orchestration layer** and implement each scenario as a configuration concern.

## Decision Drivers

- **Single abstraction** — one `WorkItem` model and `WorkItemBackend` interface covers
  all hand-off scenarios.
- **Pluggable storage** — same backend pattern as lock backends: local, S3, Azure Blob,
  GCS, git tags, or a future API server. Start simple, scale without rework.
- **No new server required** — the local and git-tag backends must be fully functional
  for single-operator and small-team use.
- **Serverless path to scale** — the backend abstraction makes a future API server a
  drop-in replacement, not a redesign.
- **Consistent CLI surface** — all work-item types share `strata workitem list/show/approve/reject/complete`.
  Type-specific shortcuts (e.g. `strata deploy approve`) are thin aliases.
- **SIEM integration** — every work-item lifecycle event is forwarded to configured SIEM
  sinks (Sentinel, ELK, Splunk, OTel) for audit and notification. No separate
  notification infrastructure is needed.
- **Policy-engine integration** — work items can be triggered by policy conditions
  (cost threshold, CVE severity, risk score) using the existing policy engine.
- **AI integration** — AI plan analysis (`AiAgentIntegration.analyse_plan()`) feeds into
  the work-item payload, giving approvers full context without running the plan again.
- **CI/CD first** — exit code 5 ("hand-off required") allows CI pipelines to pause and
  resume without custom scripting.
- **Audit trail** — every work-item event is written to the deploy-log and forwarded to
  SIEM. Who created, who resolved, when, why.

## Considered Options

### Option A — General work-item orchestration layer (recommended)

Define a `WorkItem` dataclass, a `BaseWorkItemBackend` ABC, and a `WorkItemController`.
All hand-off scenarios (approval, cost review, scheduled deploy, etc.) are instances of
this single model with a different `type` field. Configuration lives in the environment
or deployment YAML under `spec.gates`.

### Option B — Type-specific commands per scenario

Build `deploy approve/reject`, `cost approve`, `security approve` etc. as separate
command groups. Each stores state independently.

- Pro: simpler per-command implementation.
- Con: duplicated state management, fragmented audit trail, no shared CLI surface,
  cannot query "all pending hand-offs" across types.

### Option C — Cloud-native gates only

Delegate all approvals to GCP Cloud Deploy, AWS CodePipeline, or Azure Pipelines.

- Pro: zero new infrastructure for cloud-managed targets.
- Con: non-cloud and bare-metal targets are unsupported; strata becomes dependent on
  specific cloud provider features; cross-cloud consistency is lost.

### Option D — Git-only (signed tags)

Every approval is a GPG-signed git tag pushed to the workspace repository.

- Pro: distributed, tamper-evident, no external dependency.
- Con: requires GPG infrastructure; awkward for non-git-savvy users; no timeout
  enforcement without a CI job; no cross-workspace view.

## Decision Outcome

**Option A — General work-item orchestration layer**, because:

- One abstraction replaces ten one-off systems.
- Option C (cloud-native) is supported *within* Option A as a backend variant for cloud
  targets — it is not an alternative.
- Option D (git tags) is supported *within* Option A as the `git_tag` backend.
- The abstraction is minimal: `WorkItem` + `BaseWorkItemBackend` + `WorkItemController`.
  Each specific gate scenario (ADR-0032 approval, future ADR cost gates, etc.) is a
  two-page specification referencing this ADR.

### Consequences

- Good: consistent UX and audit trail across all gate types.
- Good: pluggable backends — local for dev, object storage for teams, API for enterprise.
- Good: approval (ADR-0032) becomes a thin implementation on top of this layer.
- Bad: slightly more upfront design work than a single-purpose approval command.
- Neutral: SIEM forwarding replaces the need for a custom notification system.

---

## Detailed Design

### 1. WorkItem Model

```python
@dataclass
class WorkItem:
    id: str                          # "approval/haven-prd-a1b2c3d-20260727T1430"
    type: str                        # "approval" | "cost_review" | "security_review" |
                                     # "verify" | "scheduled" | "incident" | "cab" |
                                     # "promotion_gate" | "drift_decision" | "rollback"
    status: str                      # "pending" | "approved" | "rejected" |
                                     # "completed" | "expired" | "cancelled"
    deployment: str                  # path to deployment YAML
    commit: str                      # git commit SHA that triggered the work item
    created_by: str                  # identity (email, ARN, cloud principal)
    created_at: str                  # ISO 8601
    expires_at: Optional[str]        # auto-expire timestamp (None = no expiry)

    # Resolution
    resolved_by: Optional[str] = None
    resolved_at: Optional[str] = None
    resolution_note: Optional[str] = None

    # Type-specific context (plan summary, cost delta, CVE list, AI analysis, etc.)
    context: dict = field(default_factory=dict)
```

### 2. WorkItemBackend ABC

```python
class BaseWorkItemBackend(ABC):
    """Pluggable storage for work items — same pattern as lock backends."""

    @abstractmethod
    def create(self, item: WorkItem) -> WorkItem: ...

    @abstractmethod
    def get(self, item_id: str) -> Optional[WorkItem]: ...

    @abstractmethod
    def resolve(
        self,
        item_id: str,
        status: str,           # "approved" | "rejected" | "completed" | "cancelled"
        resolved_by: str,
        note: Optional[str] = None,
    ) -> WorkItem: ...

    @abstractmethod
    def list_pending(self, type: Optional[str] = None) -> List[WorkItem]: ...

    @abstractmethod
    def expire_stale(self) -> int: ...
```

### 3. Backend Implementations

| Backend        | Storage                                               | When to use                              |
| -------------- | ----------------------------------------------------- | ---------------------------------------- |
| `local`        | `.strata/workitems/*.json`                            | Solo operator, dev, CI on single machine |
| `git_tag`      | GPG-signed git tags in workspace repo                 | Small teams, distributed, tamper-evident |
| `git_repo`     | Commit to shared `strata-workitems` repo              | Multi-workspace, no cloud storage        |
| `s3`           | AWS S3 object                                         | AWS-hosted workspaces                    |
| `azblob`       | Azure Blob Storage                                    | Azure-hosted workspaces                  |
| `gcs`          | GCP Cloud Storage                                     | GCP-hosted workspaces                    |
| `cloud_native` | GCP Cloud Deploy / AWS CodePipeline / Azure Pipelines | Cloud-managed pipelines                  |
| `api`          | `POST /api/workitems`                                 | Future central server                    |

Backend resolution follows the same priority chain as lock backends:
`properties.backend` in configuration YAML → environment variable → `local`.

### 4. WorkItemController

```python
class WorkItemController:
    """Orchestrates work-item creation, resolution, and lifecycle management."""

    def __init__(self, backend: BaseWorkItemBackend) -> None: ...

    def request(
        self,
        type: str,
        deployment: str,
        commit: str,
        requester: str,
        context: dict,
        expires_minutes: Optional[int] = None,
    ) -> WorkItem: ...

    def resolve(
        self,
        item_id: str,
        status: str,
        resolver: str,
        note: Optional[str] = None,
    ) -> WorkItem: ...

    def get(self, item_id: str) -> Optional[WorkItem]: ...

    def list_pending(self, type: Optional[str] = None) -> List[WorkItem]: ...

    def expire_stale(self) -> int: ...

    def verify_resolved(
        self,
        item_id: str,
        expected_type: str,
        expected_commit: str,
    ) -> WorkItem:
        """Called at deploy-resume time to verify the work item is valid."""
        ...
```

### 5. Gate Configuration in YAML

Gates are declared in the **environment YAML** (not deployment YAML) because gate
requirements are environment-level concerns:

```yaml
# environment YAML
spec:
  gates:
    # Human approval required for all production deploys
    - type: approval
      when: always
      approvers: ["ops-team"]          # cloud IAM group, git usernames, or email list
      min_approvals: 1
      timeout_minutes: 60

    # Finance approval when monthly cost rises by more than $1,000
    - type: cost_review
      when:
        cost_delta_monthly: ">= 1000"
      approvers: ["finance"]
      timeout_minutes: 240

    # Security team review when SBOM audit finds critical CVEs
    - type: security_review
      when:
        cve_critical: ">= 1"
      approvers: ["security-team"]
      timeout_minutes: 480             # 8 hours

    # Scheduled deploys — only proceed during the maintenance window
    - type: scheduled
      when:
        time_utc: "02:00-04:00"        # deployment window
      auto_resolve: true               # resolved automatically by the clock

    # Manual verification after deploy
    - type: verify
      when: always
      timeout_minutes: 30
      auto_complete: false
```

### 6. Lifecycle in the Deploy Pipeline

```
strata deploy run -f deploy/deploy-prd.yaml

  Phase 1: PLAN
    └─ terraform plan
    └─ AI plan review (if configured)
    └─ Policy evaluation

  Phase 2: GATE CHECK
    ├─ No gates configured → proceed to APPLY
    └─ Gates configured →
         └─ For each gate in spec.gates:
              └─ Evaluate `when` condition against plan output + cost + AI risk
              └─ Condition not met → skip gate
              └─ Condition met → create WorkItem via WorkItemController

  Phase 3: HAND-OFF
    ├─ Interactive (TTY, --wait):
    │    └─ Poll backend every 10s
    │    └─ Timeout → abort
    │    └─ Resolved → continue to APPLY
    └─ Non-interactive (CI, default):
         └─ Print work-item ID and resolution instructions
         └─ SIEM: send_event("workitem.created", item.to_dict())
         └─ Exit code 5 ("hand-off required")
         └─ CI picks up exit 5 → triggers external approval step
         └─ CI re-runs: strata deploy run ... --resume <item-id>

  Phase 4: RESUME VERIFICATION
    └─ strata deploy run ... --resume <item-id>
    └─ WorkItemController.verify_resolved(item_id, type="approval", commit=...)
    └─ Not resolved → refuse, print status
    └─ Expired → refuse, explain
    └─ Wrong commit → refuse, require new work item
    └─ Resolved → proceed to APPLY

  Phase 5: APPLY
    └─ terraform apply (same plan, verified commit)
    └─ SIEM: send_event("workitem.completed", item.to_dict())
    └─ Audit log entry
```

### 7. Identity Resolution

Resolver identity is established in this priority order:

1. **Cloud IAM principal** — `az ad signed-in-user show`, `aws sts get-caller-identity`,
   `gcloud config get-value account`. Strongest — backed by corporate SSO and MFA.
2. **Git identity** — `git config user.email`. Available everywhere git is.
3. **CI token** — `GITHUB_ACTOR`, `BUILD_REQUESTEDFOREMAIL`, or OIDC subject.
4. **Explicit `--as` flag** — `strata workitem approve <id> --as ops@company.com`.
   Logged as "asserted identity" (weaker; human-readable audit note added).

### 8. Notification via SIEM

Work-item events are forwarded to all configured SIEM sinks using the existing
`ISiemSink.send_event()` interface. No new notification infrastructure is needed.

| Event                | Payload                                                          |
| -------------------- | ---------------------------------------------------------------- |
| `workitem.created`   | Full `WorkItem` dict including plan summary, AI risk, cost delta |
| `workitem.approved`  | Work item + resolver identity + note                             |
| `workitem.rejected`  | Work item + resolver identity + reason                           |
| `workitem.expired`   | Work item                                                        |
| `workitem.completed` | Work item + outcome                                              |

Notification routing (email, Teams, Slack, PagerDuty) is configured at the SIEM
level — strata emits structured events, the SIEM routes them. This is consistent with
the existing deploy-audit forwarding pattern.

### 9. CLI Surface

```
# Core work-item commands
strata workitem list [--type TYPE] [--status STATUS] [--deployment FILE]
strata workitem show <id>
strata workitem approve <id> [--note TEXT]
strata workitem reject <id> [--reason TEXT]
strata workitem complete <id> [--comment TEXT]
strata workitem cancel <id> [--reason TEXT]
strata workitem expire [--older-than MINUTES]

# Resume a paused deployment
strata deploy run -f deploy.yaml --resume <work-item-id>

# Type-specific shortcuts (thin aliases to workitem commands)
strata deploy approve <id>        →  strata workitem approve <id>
strata deploy reject <id>         →  strata workitem reject <id>
```

### 10. Exit Codes

| Code  | Meaning                                                                         |
| ----- | ------------------------------------------------------------------------------- |
| 0     | Success                                                                         |
| 1     | System error                                                                    |
| 2     | Usage error                                                                     |
| 3     | Validation failure                                                              |
| 4     | Lock conflict                                                                   |
| **5** | **Hand-off required** — work item created; use `--resume <id>` after resolution |

### 11. Security Considerations

- Work-item IDs include the commit SHA. Resolving a work item for the wrong commit is
  rejected at resume time — prevents replay attacks.
- Local backend files are written with permissions `0600`. Sensitive context fields
  (plan JSON, cost data) are not written to SIEM — only summary fields.
- The `--as` flag (asserted identity) is logged with a `[asserted]` marker in the audit
  trail. It cannot be used to bypass approver group membership checks.
- Expired work items are non-resumable. A new deployment run (and new work item) is
  required after expiry.

---

## Implementation Phases

### Phase 1 — Foundation

- `WorkItem` dataclass
- `BaseWorkItemBackend` ABC
- `LocalWorkItemBackend` (`.strata/workitems/*.json`)
- `WorkItemController` with `request()`, `resolve()`, `get()`, `list_pending()`
- `strata workitem list/show/approve/reject`
- Exit code 5 wired into `deploy run`
- SIEM event forwarding for `workitem.created` and `workitem.approved/rejected`
- Audit log entries for all work-item events

### Phase 2 — Gate Configuration

- `spec.gates` in environment YAML (schema + validation)
- Gate condition evaluation engine (`when:` clauses)
- `strata deploy run --resume <id>` with `verify_resolved()` commit check
- `strata workitem expire` for cleanup
- `GitTagWorkItemBackend`

### Phase 3 — Cloud Backends

- `S3WorkItemBackend`
- `AzureBlobWorkItemBackend`
- `GCSWorkItemBackend`
- `CloudNativeWorkItemBackend` (GCP Cloud Deploy / AWS CodePipeline / Azure Pipelines)

### Phase 4 — Gate Types

- `type: approval` — ADR-0032
- `type: cost_review` — requires Infracost integration
- `type: security_review` — requires CVE audit integration
- `type: verify` — post-deploy manual verification
- `type: scheduled` — time-window gate with auto-resolve

### Phase 5 — VS Code Integration

- `@strata /approvals` chat command (extend existing chat participant)
- Pending work-item tree view in VS Code sidebar
- Push notification via SIEM → WebSocket (future server) or polling

---

## References

- [ADR-0003: Layered architecture](0003-layered-architecture.md)
- [ADR-0006: Policy engine for deployment guardrails](0006-policy-engine-for-deployment-guardrails.md)
- [ADR-0007: Deployment state locking](0007-deployment-state-locking.md)
- [ADR-0018: Deployment audit and traceability](0018-deployment-audit-traceability.md)
- [ADR-0025: AI agent integration for build and deploy workflows](0025-ai-agent-integration-for-build-and-deploy.md)
- [ADR-0032: Approval workflows and gates](0032-approval-workflows-and-gates.md)
