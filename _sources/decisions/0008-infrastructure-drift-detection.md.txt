# Infrastructure drift detection

- Status: in design
- Date: 2026-06-16
- Issue: #122

## Context and Problem Statement

After `strata deploy run` applies infrastructure, the actual state can diverge from the
declared configuration.  This happens when:

- Engineers make manual changes via the cloud portal or CLI ("click-ops")
- Automated systems outside strata modify resources (auto-scaling, policy remediation)
- Terraform providers update default values on minor version bumps
- External dependencies disappear or change (deleted VNet peering, rotated cert)

Today, discovering drift requires an operator to run `strata deploy run --dry-run` and
manually inspect the plan output.  There is no structured, schedulable, or observable
mechanism to detect, classify, or alert on drift.

Teams want:

1. A **single command** to check an entire deployment for drift without applying anything
2. A **machine-readable report** so CI/CD or monitoring can trigger alerts
3. **Severity classification** so critical drift (security group changes) is distinguished
   from cosmetic drift (tag edits)
4. **History** to answer "when did this resource start drifting?"
5. **Scheduled CI integration** — run drift checks nightly without operator intervention

## Considered Options

### Option A — Wrapper around `terraform plan -detailed-exitcode`

Run the existing `TerraformDeployer.plan()` step and parse the JSON plan for resource
changes.  Report results without calling `apply`.

- **Pro:** Zero new Terraform logic; reuses the existing deployer pipeline
- **Pro:** Catches all drift that Terraform itself can detect (authoritative)
- **Con:** Requires full `terraform init` + provider download per check (~30-60s)
- **Con:** Cannot detect drift in non-Terraform stages (Ansible, Helm, scripts)
- **Con:** No built-in severity model — all changes look the same in the plan

### Option B — Cloud-native drift APIs

Use provider-specific drift detection (AWS Config rules, Azure Policy compliance, GCP
Asset Inventory) and aggregate results.

- **Pro:** Near-instant — no init/plan cycle
- **Pro:** Can detect changes Terraform doesn't manage (out-of-band resources)
- **Con:** Every cloud needs a different integration
- **Con:** Requires additional IAM permissions (read-only compliance APIs)
- **Con:** Cannot correlate back to Terraform state addresses reliably

### Option C — `terraform plan` with classification layer (recommended)

Same as Option A, but add a classification layer on top of the JSON plan output that
maps resource types and change actions to severity levels.  Extend to non-Terraform
stages with provider-specific lightweight checks where possible (Helm diff, Ansible
check mode).

- **Pro:** Authoritative for Terraform-managed resources
- **Pro:** Classification makes drift actionable (critical vs informational)
- **Pro:** Extensible to other deployers via the same `check()` interface
- **Con:** Still requires init/provider download for Terraform stages
- **Con:** Classification rules must be maintained as new resource types appear

## Decision Outcome

Chosen: **Option C — `terraform plan` with classification layer**, because it leverages
the existing `TerraformDeployer` pipeline (proven, no new Terraform logic), produces
authoritative results, and adds a severity model that makes the output actionable for
alerting and CI gates.

### Consequences

- Good: Single command (`strata deploy drift`) gives a structured drift report
- Good: JSON output integrates with monitoring, Slack alerts, and CI gates
- Good: Classification prevents alert fatigue — only critical drift triggers pages
- Good: History tracking answers "how long has this been drifting?"
- Bad: First run per stage is as slow as `terraform plan` (~30-60s per stage)
- Bad: Classification rules need periodic updates as new resource types are used
- Bad: Non-Terraform stages (Ansible, Helm) get limited drift support initially

---

## Detailed Design

### CLI Surface

```
strata deploy drift [OPTIONS]

Options:
  --file PATH           Deployment YAML file (default: auto-detect)
  --stage NAME          Check only this stage (default: all)
  --severity LEVEL      Minimum severity to report: critical | high | medium | low | info
  --output FORMAT       console | json (default: console)
  --baseline            Save current state as the baseline (resets drift age tracking)
  --work-path PATH      Workspace root
```

Exit codes follow the existing convention:

| Code | Meaning                                                       |
| ---- | ------------------------------------------------------------- |
| `0`  | No drift detected (or all drift is acknowledged)              |
| `1`  | System failure (init error, no build output, network timeout) |
| `3`  | Drift detected (at or above the requested severity threshold) |

### Relationship to `deploy status`

`strata deploy status --plan` shows a **saved** plan from the last `deploy run
--dry-run`.  `strata deploy drift` runs a **fresh** plan, classifies the changes by
severity, tracks history, and returns a structured exit code.  The two commands serve
different purposes:

|                      | `deploy status --plan`         | `deploy drift`                               |
| -------------------- | ------------------------------ | -------------------------------------------- |
| Plan source          | Saved `.tfplan` file (offline) | Live `terraform plan` (online)               |
| Network access       | None (reads local file)        | Yes (state refresh + provider APIs)          |
| Classification       | None — raw plan output         | Severity-classified entries                  |
| History tracking     | No                             | Yes — `first_detected`, `consecutive_checks` |
| Exit code on changes | Always 0                       | 3 if drift above threshold                   |
| Lock required        | No                             | Yes                                          |

### Architecture

```
commands/
  deploy/
    drift_deploy_command.py     ← New: DriftDeployCommand (BaseDeployCommand subclass)

  cli_deploy.py                 ← Modified: register `deploy drift` subcommand

controllers/
  drift_controller.py           ← New: orchestrates per-stage drift detection

models/
  drift_model.py                ← New: DriftReport, DriftEntry, DriftSeverity

deployers/
  terraform_deployer.py         ← Modified: add drift() step (plan → parse JSON)
  base_deployer.py              ← Modified: add drift() to abstract interface

data/
  drift_rules.yaml              ← New: classification rules (resource_type → severity)

utils/
  drift_history.py              ← New: DriftHistoryStore (load/save/acknowledge)
```

### Prerequisite: Fresh Build

Drift detection compares **live infrastructure state** against the **declared
configuration** in the build output directory (`.strata/build/{stage}/`).  This means
a `strata build run` must precede `strata deploy drift`.

If the build output is stale (config changed since last build), drift detection
compares against old config and may produce false positives.  The command verifies
build freshness:

1. Check that `.strata/build/{stage}/` exists for every stage being checked
2. Compare the build timestamp (from `platform.json`) against the deployment YAML's
   `mtime` — warn if the build is older
3. If no build output exists, exit with code 1 and message:
   `"No build output found. Run 'strata build run' before drift detection."`

In CI, the workflow should always run `strata build run` → `strata deploy drift` as
a pair.

### Locking Semantics

`terraform plan` refreshes remote state, which is not safe to run concurrently with
`terraform apply`.  Drift detection therefore **acquires the deployment lock** using
the same mechanism as `deploy run`:

- `strategy: wrap` — acquires the lock before running plan, releases after
- `strategy: delegate` — skips strata's lock (TFC queues plans natively)
- `--dry-run` is NOT set — this is a real plan with state refresh

This means a drift check blocks (up to `wait_timeout`) if a deploy is in progress,
and a deploy blocks if a drift check is running.  This is the correct behaviour —
concurrent state refresh and apply would corrupt state.

### Execution Flow

```text
sequenceDiagram
    participant CLI as strata deploy drift
    participant Cmd as DriftDeployCommand
    participant Ctrl as DriftController
    participant TF as TerraformDeployer
    participant Svc as DriftService

    CLI->>Cmd: execute()
    Cmd->>Cmd: _initialize() (load deployment, workspace)
    Cmd->>Cmd: verify build output exists & freshness
    Cmd->>Cmd: acquire lock (same as deploy run)

    Cmd->>Ctrl: detect_drift(stages)

    loop each stage (sequential)
        Ctrl->>TF: setup() → drift()
        TF-->>Ctrl: plan JSON (resource_changes[])
        Ctrl->>Ctrl: classify(resource_changes, rules)
    end

    Ctrl->>Svc: save_report(report)
    Ctrl-->>Cmd: DriftReport
    Cmd->>Cmd: release lock
    Cmd->>CLI: render output + exit code
```

**Stages run sequentially** — not in parallel.  Multiple stages may share the same
Terraform backend (state file), and parallel `terraform plan` calls would race on
state refresh.  Even with different backends, provider plugin cache contention can
cause issues.  Sequential execution matches the existing `deploy run` behaviour.

### Drift Report Model

```python
class DriftSeverity(str, Enum):
    CRITICAL = "critical"   # Security-sensitive changes (NSG, IAM, firewall)
    HIGH = "high"           # Core infrastructure changes (VM size, disk, network)
    MEDIUM = "medium"       # Configuration changes (app settings, scaling rules)
    LOW = "low"             # Cosmetic changes (tags, descriptions)
    INFO = "info"           # Informational (output-only resources, data sources)


@dataclass
class DriftEntry:
    address: str              # e.g. "azurerm_network_security_rule.allow_ssh"
    resource_type: str        # e.g. "azurerm_network_security_rule"
    action: str               # "update" | "delete" | "create" (unexpected)
    severity: DriftSeverity
    stage: str
    changes: List[str]        # list of changed attribute paths
    before: Dict[str, Any]    # previous values (from state)
    after: Dict[str, Any]     # desired values (from config)
    first_detected: str       # ISO timestamp — when strata first saw this drift
    consecutive_checks: int   # how many consecutive checks found it


@dataclass
class DriftReport:
    deployment: str
    checked_at: str           # ISO timestamp
    stages_checked: List[str]
    entries: List[DriftEntry]
    summary: DriftSummary     # counts per severity level

    @property
    def has_drift(self) -> bool:
        return len(self.entries) > 0

    @property
    def max_severity(self) -> Optional[DriftSeverity]:
        ...
```

### Classification Rules

Rules live in `src/strata/data/drift_rules.yaml` and follow a match-first pattern:

```yaml
# drift_rules.yaml — maps resource types + attributes to severity levels
rules:
  # Security-critical resources — any change is critical
  - resource_type: "azurerm_network_security_rule"
    severity: critical
  - resource_type: "azurerm_network_security_group"
    severity: critical
  - resource_type: "aws_security_group_rule"
    severity: critical
  - resource_type: "azurerm_role_assignment"
    severity: critical
  - resource_type: "aws_iam_*"
    severity: critical

  # Core infrastructure — high severity
  - resource_type: "azurerm_virtual_machine*"
    severity: high
  - resource_type: "azurerm_kubernetes_cluster"
    severity: high
  - resource_type: "aws_instance"
    severity: high

  # Attribute-level overrides — tag changes are low regardless of resource
  - attribute: "tags"
    severity: low
  - attribute: "tags_all"
    severity: low

defaults:
  # Anything not matched above
  severity: medium
```

Resolution order:
1. Attribute-specific rules (most specific)
2. Resource-type rules (glob matching via `fnmatch`)
3. Default severity

Users can override by placing a `drift_rules.yaml` in their workspace root or under
`.strata/`. The workspace file is merged on top of the built-in defaults.

### Drift History & Baseline

History is stored per-deployment at `.strata/drift/{deployment_name}.drift.json`:

```text
{
  "deployment": "prod-platform",
  "baseline_at": "2026-06-10T00:00:00Z",
  "acknowledged": [
    {
      "address": "azurerm_autoscale_setting.web",
      "reason": "auto-scaler managed — expected drift",
      "acknowledged_at": "2026-06-12T09:00:00Z",
      "acknowledged_by": "alice@company.com"
    }
  ],
  "checks": [
    {
      "checked_at": "2026-06-16T02:00:00Z",
      "entries_count": 3,
      "max_severity": "high",
      "entries": [...]
    }
  ]
}
```

- `--baseline` resets `baseline_at` and clears history — used after a known-good deploy
- `first_detected` on each entry is carried forward from previous checks by matching
  on `address`
- `consecutive_checks` increments each time the same address shows drift

#### Resource address stability

History tracking uses Terraform resource addresses (e.g.
`module.network.azurerm_subnet.main`).  If a resource is refactored (moved into a
module, renamed via `moved` block), the address changes and:
- The old address appears as "drift resolved" (false resolution)
- The new address appears as "newly detected" (resets `first_detected`)

This is a known limitation.  Terraform's `moved` blocks handle the state migration
but strata's drift history file has no visibility into those renames.  For Phase 1
this is acceptable — address renames are infrequent and the drift itself is still
detected, only the age tracking resets.

#### Acknowledging expected drift

Some drift is intentional — auto-scalers change replica counts, policy remediations
add tags, etc.  Rather than resetting the entire baseline, operators can acknowledge
specific addresses:

```
strata deploy drift acknowledge \
  --deployment prod-platform \
  --address "azurerm_autoscale_setting.web" \
  --reason "auto-scaler managed"
```

Acknowledged entries are excluded from the report and do not affect the exit code.
They remain acknowledged until the operator removes them or resets the baseline.

### TerraformDeployer Changes

Add a `drift()` method to `TerraformDeployer`:

```python
def drift(
    self,
    line_callback: Optional[Callable[[str, str], None]] = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """terraform plan -detailed-exitcode -json → parsed plan.

    Returns (ok, plan_json_dict, messages).
    Does NOT produce a saved .tfplan file — read-only operation.
    Uses -refresh-only=false (full plan, not just refresh).
    """
```

This reuses the existing `setup()` → `plan()` flow but:
- Passes `-json` to get structured output instead of human-readable
- Does NOT write a `.tfplan` file (no `-out=` flag)
- Stores nothing — the caller (DriftController) handles classification and persistence

#### Sensitive value handling

Terraform's JSON plan marks sensitive values with `"before_sensitive"` and
`"after_sensitive"` boolean fields per attribute.  The `drift()` method replaces any
value where the corresponding sensitivity flag is `true` with the string
`"(sensitive)"` before returning the plan dict.  strata does NOT apply its own
sensitivity heuristics — it trusts Terraform's markers.

This means values that are sensitive in practice but not marked `sensitive` in the
Terraform schema (e.g., connection strings in `app_settings`) will appear in the
drift report.  Operators should mark such attributes as `sensitive` in their
Terraform configuration.

### BaseDeployer Extension

```python
# base_deployer.py — new abstract method (default: not supported)
def drift(
    self,
    line_callback: Optional[Callable[[str, str], None]] = None,
) -> Tuple[bool, Dict[str, Any], List[str]]:
    """Detect drift for this stage. Default: not supported."""
    return True, {}, [f"Drift detection not supported for {self.get_deployer_name()}"]
```

Non-Terraform deployers return an empty result (no drift info, no failure).  Future
phases can add:
- `HelmDeployer.drift()` → `helm diff upgrade --dry-run`
- `AnsibleDeployer.drift()` → `ansible-playbook --check --diff`

### DriftController

```python
class DriftController(BaseController):
    """Orchestrates drift detection across all stages of a deployment."""

    def detect_drift(
        self,
        stages: List[DeploymentStageModel],
        severity_threshold: DriftSeverity = DriftSeverity.INFO,
    ) -> DriftReport:
        ...
```

Responsibilities:
1. Instantiate the correct deployer per stage (same factory as `RunDeployCommand`)
2. Call `deployer.setup()` → `deployer.drift()` per stage
3. Parse the JSON plan: extract `resource_changes[]` where `change.actions != ["no-op"]`
4. Classify each change using `drift_rules.yaml`
5. Filter by `severity_threshold`
6. Merge with previous history (`DriftService`) to populate `first_detected` and
   `consecutive_checks`
7. Return the assembled `DriftReport`

### Drift Utilities

`DriftService` does **not** extend `BaseService` — it manages JSON history files, not
YAML config models.  `BaseService` assumes `load()` + two-phase validation + service
cache, none of which apply here.  Instead, it is a plain utility class:

```python
class DriftHistoryStore:
    """Load and persist drift history per deployment."""

    def __init__(self, work_path: Path) -> None:
        self._drift_dir = work_path / ".strata" / "drift"

    def load_history(self, deployment_name: str) -> Optional[DriftHistory]: ...
    def save_report(self, deployment_name: str, report: DriftReport) -> None: ...
    def reset_baseline(self, deployment_name: str) -> None: ...
    def acknowledge(self, deployment_name: str, address: str, reason: str) -> None: ...
    def remove_acknowledgement(self, deployment_name: str, address: str) -> None: ...
```

History files live in `.strata/drift/` (git-ignored, local state like lock history).

### CI Integration

Drift checks are designed to run on a schedule (nightly cron):

```yaml
# .github/workflows/drift-check.yml
name: Drift Detection
on:
  schedule:
    - cron: '0 2 * * *'  # 2 AM daily

jobs:
  drift:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: strata deploy drift --output json --severity high > drift-report.json
      - if: ${{ failure() }}
        run: |
          # Exit code 3 = drift detected — post to Slack / create issue
          cat drift-report.json | jq '.entries[] | select(.severity == "critical")'
```

### Console Output Example

```
$ strata deploy drift --deployment prod-platform

Drift Report — prod-platform
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Stage: production
  🔴 CRITICAL  azurerm_network_security_rule.allow_ssh
               Action: update
               Changed: destination_port_range (22 → 22,3389)
               First detected: 2026-06-14T02:00:00Z (2 days ago)

  🟡 MEDIUM    azurerm_app_service.api
               Action: update
               Changed: site_config.always_on (true → false)
               First detected: 2026-06-16T02:00:00Z (new)

  🟢 LOW       azurerm_resource_group.main
               Action: update
               Changed: tags.last-reviewed
               First detected: 2026-06-12T02:00:00Z (4 days ago)

Summary: 1 critical, 0 high, 1 medium, 1 low, 0 info
Exit code: 3 (drift detected at severity >= info)
```

### JSON Output Schema

```json
{
  "success": false,
  "data": {
    "deployment": "prod-platform",
    "checked_at": "2026-06-16T14:30:00Z",
    "stages_checked": ["production"],
    "summary": {
      "critical": 1,
      "high": 0,
      "medium": 1,
      "low": 1,
      "info": 0,
      "total": 3
    },
    "entries": [
      {
        "address": "azurerm_network_security_rule.allow_ssh",
        "resource_type": "azurerm_network_security_rule",
        "action": "update",
        "severity": "critical",
        "stage": "production",
        "changes": ["destination_port_range"],
        "before": {"destination_port_range": "22"},
        "after": {"destination_port_range": "22,3389"},
        "first_detected": "2026-06-14T02:00:00Z",
        "consecutive_checks": 3
      }
    ]
  }
}
```

---

## Implementation Phases

### Phase 1 — Core Terraform drift (MVP)

- `DriftReport` / `DriftEntry` / `DriftSeverity` models
- `TerraformDeployer.drift()` method with sensitive value redaction
- `DriftController` with basic classification
- `DriftHistoryStore` for history persistence
- `strata deploy drift` CLI command (with build freshness check)
- Deployment lock acquisition (same as `deploy run`)
- Built-in `drift_rules.yaml` for Azure + AWS resource types
- Console + JSON output
- Exit code 3 on drift

### Phase 2 — Severity customisation + CI helpers

- Workspace-level `drift_rules.yaml` override/merge
- `--baseline` flag
- `strata deploy drift acknowledge` subcommand
- `strata deploy drift history` subcommand
- Webhook/notification integration (Slack, Teams)
- GitHub Actions reusable workflow in `.github/actions/`

### Phase 3 — Multi-deployer support

- `HelmDeployer.drift()` via `helm diff`
- `AnsibleDeployer.drift()` via `--check --diff`
- `ComposeDeployer.drift()` via `docker compose config` diff

---

## Risks and Mitigations

| Risk                                         | Impact                                                     | Mitigation                                                                                                    |
| -------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| `terraform plan` is slow (~30-60s per stage) | Drift checks take minutes for large deployments            | Cache providers across runs; stages sequential (shared state safety)                                          |
| Stale build output                           | Drift report compares against old config → false positives | Check build timestamp vs YAML mtime; warn if stale; CI always runs `build` → `drift`                          |
| Classification rules go stale                | New resource types default to "medium" — may under-report  | Periodic rule updates; workspace override allows teams to customise immediately                               |
| False positives from provider defaults       | Noise in drift reports                                     | Attribute-level ignore rules in `drift_rules.yaml` (`severity: ignore`) + `drift acknowledge` for known drift |
| Secrets in plan JSON output                  | Sensitive values exposed in drift report                   | Redact values marked `before_sensitive`/`after_sensitive` by Terraform; never log full plan JSON              |
| Concurrent drift check + deploy run          | State corruption if concurrent state refresh + apply       | Drift check acquires the deployment lock (same as `deploy run`); blocks until released                        |
| Resource address renames                     | History tracking resets on refactored Terraform            | Known limitation — drift is still detected, only age tracking resets; acceptable for Phase 1                  |

---

## Alternatives Not Chosen

### Cloud-native drift APIs (Option B)

While Azure Policy compliance and AWS Config provide near-instant drift detection, they:
- Cannot correlate findings back to Terraform resource addresses
- Require additional IAM permissions beyond what Terraform needs
- Need per-provider integration code with different data models
- Miss resources that Terraform manages but the compliance tool doesn't cover

The Terraform plan approach is authoritative: if Terraform says "this will change on
next apply," that IS drift by definition.

### `terraform plan -refresh-only`

Considered using `-refresh-only` mode (introduced in Terraform 1.1) which only refreshes
state without computing a full plan.  Rejected because:
- It only detects drift from real-world state → Terraform state
- It does NOT detect drift from Terraform state → desired config
- A resource could be drifted in both directions simultaneously

Full `terraform plan` catches both directions and is the established mechanism.

---

## Related

- [ADR 0007 — Deployment state locking](0007-deployment-state-locking.md) — drift checks
  respect the locking mechanism (`strategy: wrap` acquires a read lock; `delegate`
  skips)
- [How deployments work](../guides/how-deployments-work.md) — stage/provisioner model
- [Deployers reference](../platform/deployers.md) — `TerraformDeployer` step interface
- [CI integration](../platform/ci-integration.md) — scheduled workflow patterns
