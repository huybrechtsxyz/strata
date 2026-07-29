# Detecting Infrastructure Drift

> **Audience:** Operators who want to know if live infrastructure has diverged from
> what the Terraform code declares — before running a full deploy.
>
> 📐 **Design rationale:** [ADR-0008 — Infrastructure drift detection](../decisions/0008-infrastructure-drift-detection.md)

---

## What is drift?

*Drift* is any difference between the current state of your infrastructure (recorded in
the Terraform state file) and what your Terraform configuration says it should be.

Drift happens when:
- Someone edits a resource directly in the cloud console.
- An upstream provider makes an automatic change (e.g. a managed certificate renewal
  that adds unexpected tags).
- A previous pipeline failed mid-apply, leaving resources in a partially-updated state.

`strata deploy drift` surfaces these differences **without making any changes**.

---

## Quick start

```bash
strata deploy drift run -f deploy/deploy-prd.yaml
```

When drift is found, the output looks like:

```
🔍  Checking drift for 2 stage(s) (threshold: info)…

  Stage: infrastructure
  ADDRESS                                               ACTION    SEVERITY    CHANGED ATTRIBUTES
  ----------------------------------------------------  --------  ----------  ------------------------------
  azurerm_network_security_rule.allow_http              [~]       🔴 critical  priority, access
  azurerm_virtual_machine.web01                         [~]       🟠 high      os_disk_size_gb
  azurerm_app_service.api                               [~]       🟡 medium    app_settings

  Summary: 3 change(s) — 🔴 1 critical  🟠 1 high  🟡 1 medium  🔵 0 low  ⚪ 0 info
```

Exit code **3** is returned when drift is found at or above the configured severity
threshold. Exit code **0** means no drift (or all drift is below the threshold).

---

## How it works

For each deployment stage:

1. **`terraform init`** — ensure providers are up to date.
2. **`terraform plan -detailed-exitcode`** — compare state to configuration.  
   Exit 0 = no changes, exit 2 = changes present.
3. **`terraform show -json`** — decode the plan to get structured resource changes.
4. **Classify** each changed resource by severity using built-in + workspace rules.
5. **Persist history** at `.strata/drift/{deployment}.drift.json`.

No resources are modified. No plan file is saved to the build directory.

---

## Severity levels

| Level      | What it means                                                             |
| ---------- | ------------------------------------------------------------------------- |
| 🔴 critical | Security-sensitive: network security rules, IAM roles, key vault policies |
| 🟠 high     | Core infrastructure: VM sizes, Kubernetes clusters, virtual networks      |
| 🟡 medium   | Configuration: app settings, monitoring rules, kubernetes workloads       |
| 🔵 low      | Cosmetic: tags, descriptions                                              |
| ⚪ info     | Data sources, null resources, bookkeeping resources                       |

Tag changes are always `low` regardless of resource type — they match the attribute-level
`tags` rule, which takes priority over any resource-type rule.

---

## Command reference

`strata deploy drift` is a **subgroup** with three subcommands:

```
strata deploy drift run         # detect drift (was: strata deploy drift)
strata deploy drift acknowledge # suppress a known-drifted address
strata deploy drift history     # view run history and acknowledged addresses
```

### `drift run` flags

```
strata deploy drift run -f <FILE> [OPTIONS]
```

| Flag               | Default    | Description                                                      |
| ------------------ | ---------- | ---------------------------------------------------------------- |
| `-f / --file`      | required   | Path to the deployment YAML file                                 |
| `--stage NAME`     | all stages | Run drift detection on one stage only                            |
| `--severity LEVEL` | `info`     | Minimum severity for exit code 3 (critical/high/medium/low/info) |
| `--baseline`       | off        | Acknowledge all detected drift as baseline; always exits 0       |
| `--output FORMAT`  | console    | `console` or `json`                                              |
| `--verbose`        | off        | Show terraform output lines                                      |

---

## Using severity thresholds

By default, **any drift** (including tag changes) triggers exit code 3. Use
`--severity` to ignore low-signal changes in CI pipelines:

```bash
# Fail only on security-sensitive or core-infrastructure changes
strata deploy drift run -f deploy/deploy-prd.yaml --severity high

# Fail on anything medium or above (ignore tags, data sources)
strata deploy drift run -f deploy/deploy-prd.yaml --severity medium
```

Exit code 0 is returned when all detected drift is below the threshold. The drift is
still **reported** in the output — the threshold only controls the exit code.

---

## Checking one stage

```bash
strata deploy drift run -f deploy/deploy-prd.yaml --stage infrastructure
```

Useful when a specific stage is suspected to have drift and you don't want to run
`terraform init` for every stage.

---

## Setting a baseline

After a known-good deploy, reset the drift history so age tracking starts from now:

```bash
strata deploy drift run -f deploy/deploy-prd.yaml --baseline
```

This runs drift detection, shows the report, acknowledges every detected entry, clears
the run history, and records a `baseline_at` timestamp. Future checks will treat those
resources as having `first_detected` from the next run they appear in.

`--baseline` always exits 0, regardless of what drift was found.

---

## Acknowledging expected drift

Some resources drift intentionally — auto-scalers change replica counts, policy agents
add tags. Rather than resetting the entire baseline, suppress individual addresses:

```bash
# Suppress one address
strata deploy drift acknowledge -f deploy/deploy-prd.yaml \
    --address "azurerm_autoscale_setting.web" \
    --reason "auto-scaler managed — expected drift"

# Re-enable reporting for an address
strata deploy drift acknowledge -f deploy/deploy-prd.yaml \
    --address "azurerm_autoscale_setting.web" \
    --remove
```

Acknowledged addresses are **excluded from the report and do not affect the exit code**.
They remain suppressed until explicitly removed or until the baseline is reset.

### `drift acknowledge` flags

| Flag             | Required | Description                                                       |
| ---------------- | -------- | ----------------------------------------------------------------- |
| `-f / --file`    | yes      | Deployment YAML to identify the deployment                        |
| `--address ADDR` | yes      | Terraform resource address (e.g. `azurerm_autoscale_setting.web`) |
| `--reason TEXT`  | no       | Explanation stored in history for audit purposes                  |
| `--remove`       | no       | Remove a previously added acknowledgement                         |

---

## Viewing drift history

```bash
strata deploy drift history -f deploy/deploy-prd.yaml
strata deploy drift history -f deploy/deploy-prd.yaml --last 5
```

Shows:
- A table of recent drift-check runs with their timestamps and drifted resource counts.
- A list of currently acknowledged (suppressed) addresses.

### `drift history` flags

| Flag          | Default | Description                                |
| ------------- | ------- | ------------------------------------------ |
| `-f / --file` | yes     | Deployment YAML to identify the deployment |
| `--last N`    | `10`    | Number of most-recent runs to display      |
| `--output`    | console | `console` or `json`                        |

---

## JSON output for CI

```bash
strata deploy drift run -f deploy/deploy-prd.yaml --output json
```

Output shape:

```json
{
  "deployment": "my-deployment",
  "checked_at": "2026-07-06T09:00:00Z",
  "stages_checked": ["infrastructure", "services"],
  "has_drift": true,
  "max_severity": "critical",
  "summary": {
    "critical": 1, "high": 0, "medium": 1, "low": 0, "info": 0, "total": 2
  },
  "entries": [
    {
      "address": "azurerm_network_security_rule.allow_http",
      "resource_type": "azurerm_network_security_rule",
      "action": "update",
      "severity": "critical",
      "stage": "infrastructure",
      "changed_attributes": ["priority", "access"],
      "before": { "priority": 100, "access": "Allow" },
      "after":  { "priority": 200, "access": "Deny"  },
      "first_detected": "2026-07-05T08:00:00Z",
      "consecutive_checks": 2
    }
  ]
}
```

Sensitive values (flagged by Terraform) are replaced with `"(sensitive)"` so they
are never written to history or output.

---

## Drift history

Every run appends to `.strata/drift/{deployment}.drift.json`. This file tracks:

- `first_detected` — timestamp of the first run that found this resource drifting.
- `last_detected` — timestamp of the most recent run that found it drifting.
- `consecutive_checks` — how many consecutive runs found it in a drifted state.

`consecutive_checks` is reset to 0 when a resource stops drifting (e.g. after a deploy
or a manual fix). It is **never** overwritten: if a resource was first detected 30 runs
ago, that timestamp is preserved.

The history file is automatically added to `.gitignore`.

---

## Customising severity rules

To override severity for specific resource types in your workspace, create
`.strata/drift_rules.yaml`. Workspace rules take priority over the built-in rules:

```yaml
# .strata/drift_rules.yaml
rules:
  # Our team considers tag drift worth tracking as medium (not low)
  - attribute: "tags"
    severity: medium

  # We manage this resource manually — treat any drift as info only
  - resource_type: "azurerm_management_lock"
    severity: info

defaults:
  severity: medium
```

Rules are matched in this order:
1. Attribute-level rules (most specific — any resource with this attribute).
2. Resource-type rules (exact or glob, e.g. `azurerm_vm*`).
3. `defaults.severity` (catch-all).

Workspace rules are **prepended** to the built-in rule list, so they are evaluated
first. You cannot remove built-in rules; you can only add higher-priority rules that
match before them.

---

## Using drift in CI

Add a drift check step before every deploy to catch out-of-band changes:

```yaml
# .github/workflows/deploy.yml (example)
- name: Check for drift
  run: strata deploy drift run -f deploy/deploy-prd.yaml --severity high
  continue-on-error: true   # report drift but don't block the deploy

- name: Deploy
  run: strata deploy run -f deploy/deploy-prd.yaml --force
```

Or fail the pipeline on critical drift:

```yaml
- name: Fail on critical drift
  run: strata deploy drift run -f deploy/deploy-prd.yaml --severity critical
```

### Nightly scheduled drift check

A reusable GitHub Actions workflow is provided at
`.github/workflows/drift-check.yml`. Use it directly:

```yaml
# .github/workflows/nightly.yml
jobs:
  drift:
    uses: ./.github/workflows/drift-check.yml
    with:
      deployment: deploy/production.yaml
      severity: high
    secrets: inherit
```

The workflow:
- Runs nightly at 02:00 UTC.
- Posts a Slack notification when critical/high drift is found (optional — set
  `SLACK_DRIFT_WEBHOOK` secret).
- Uploads `drift-report.json` as an artifact (kept 90 days).
- Exits 3 when drift at or above `severity` is detected.

---

## Exit codes

| Code | Meaning                                                          |
| ---- | ---------------------------------------------------------------- |
| `0`  | No drift found, or all drift is below `--severity` threshold     |
| `1`  | Execution error (terraform failure, auth problem, missing build) |
| `3`  | Drift found at or above `--severity` threshold                   |

---

## Prerequisites

- Build artifacts must exist (`strata build run` must have run first).
- Terraform must be on `PATH` (`strata tools status` to verify).
- Authentication to the Terraform backend must be configured (same credentials required
  by `terraform plan` — e.g. `az login` for Azure, `AWS_*` env vars for AWS).

---

## Related

- [How deployments work](how-deployments-work.md)
- [Troubleshooting: what changed?](troubleshooting-what-changed.md)
- [How deployment locking works](how-deployment-locking-works.md)
- [`docs/decisions/0008-infrastructure-drift-detection.md`](../decisions/0008-infrastructure-drift-detection.md)
