# Cost Estimation and Visibility

> **Audience:** DevOps engineers and platform teams who want to understand the
> monthly cost of infrastructure before deploying it.

---

## Overview

strata integrates with [Infracost](https://www.infracost.io) to provide cost
estimates for Terraform-based deployments. Cost estimation is separate from the
build pipeline — **builds stay offline and deterministic**.

```
strata build run -f deploy/prd.yaml      # generates terraform (offline, no cost)
strata cost show -f deploy/prd.yaml      # estimates cost (calls Infracost)
strata deploy run -f deploy/prd.yaml     # deploys (also shows cost diff in --dry-run)
```

---

## Prerequisites

Install [Infracost](https://www.infracost.io/docs/install):

```bash
# macOS
brew install infracost

# Windows
choco install infracost

# Linux / download binary
curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh
```

Verify the installation:

```bash
infracost --version
```

Infracost uses the **same cloud credentials** already configured in your environment
(Azure CLI, AWS env vars, GCP application credentials). No extra authentication is needed.

---

## Quick Start

```bash
# 1. Build terraform artifacts
strata build run -f deploy/prd.yaml

# 2. Initialize terraform (required for Infracost)
cd build/production-1.0.0/terraform
terraform init
cd -

# 3. Show cost estimate
strata cost show -f deploy/prd.yaml
```

Example output:

```
──────────────────────────────────────────────────────────────────
💰 Cost Estimate — provisioner: terraform
──────────────────────────────────────────────────────────────────

Resource                                          Monthly
───────────────────────────────────────────────── ──────────────
azurerm_kubernetes_cluster.aks                    2,100.00
azurerm_postgresql_flexible_server.postgres       1,202.40
azurerm_container_registry.acr                      200.00
azurerm_key_vault.keyvault                           12.00
───────────────────────────────────────────────── ──────────────
Total                                             3,514.40
```

---

## Supported Providers

| Cloud    | Support |
| -------- | ------- |
| Azure    | ✅ Full  |
| AWS      | ✅ Full  |
| GCP      | ✅ Full  |
| Hetzner  | ❌ None  |
| Kamatera | ❌ None  |

For unsupported providers, the cost command skips gracefully with no error.

---

## Commands

### `strata cost show`

Runs `infracost breakdown` on terraform build artifacts and displays a cost table.

```bash
strata cost show -f deploy/prd.yaml
strata cost show -f deploy/prd.yaml --currency EUR
strata cost show -f deploy/prd.yaml --provisioner terraform
strata cost show -f deploy/prd.yaml --output json
strata cost show -f deploy/prd.yaml --refresh   # bypass local cache
```

Results are cached in `.strata/cache/cost/` for 7 days. Use `--refresh` to force a
fresh estimate.

A `cost.json` file is written alongside `platform.json` in the build directory
(`build/{deployment}-{version}/cost.json`) after each successful estimate.

### `strata cost diff`

Shows the cost impact of a terraform plan (how much the planned changes add or remove).

```bash
# First, create the plan JSON:
terraform -chdir=build/production-1.0.0/terraform \
  plan -out=plan.tfplan
terraform -chdir=build/production-1.0.0/terraform \
  show -json plan.tfplan > plan.json

# Then diff:
strata cost diff -f deploy/prd.yaml --plan-file plan.json
```

Example output:

```
──────────────────────────────────────────────────────────────────
💰 Cost Diff
──────────────────────────────────────────────────────────────────

  Before: 0.00/month
  After:  3514.40/month
  Delta:  +3514.40/month
```

### `strata cost history`

Shows historical cost snapshots. Snapshots are recorded automatically every time
`strata cost show` runs successfully.

```bash
strata cost history -f deploy/prd.yaml
strata cost history -f deploy/prd.yaml --last 20
strata cost history -f deploy/prd.yaml --output json
```

Example output:

```
──────────────────────────────────────────────────────────────────────
💰 Cost History — production (last 3 snapshots)
──────────────────────────────────────────────────────────────────────

Date (UTC)             Version    Monthly           Delta
────────────────────── ────────── ────────────────  ────────────
2026-07-20 08:00:00    1.0.0       3514.40 USD               —
2026-07-21 14:30:00    1.0.1       3702.20 USD          +187.80
2026-07-22 10:15:00    1.0.2       3514.40 USD          -187.80
```

History is stored at `.strata/cost/{deployment}.cost-history.json` (up to 50 entries).

---

## Cost During Deploy (`--dry-run`)

When running `strata deploy run --dry-run`, strata automatically shows a cost diff
after the terraform plan step — if Infracost is installed and `cost.json` is available.
This is non-fatal: if Infracost is not installed or the cost cannot be computed, the
dry-run continues normally.

```bash
strata deploy run -f deploy/prd.yaml --dry-run
```

```
  ▶  [DRY-RUN] Stage: infrastructure  [terraform]
    setup
    check
    plan
    plan JSON → build/production-1.0.0/terraform.tfplan.json
    💰 Cost impact:  0.00 → 3514.40/month  (delta: +3514.40)
```

---

## Cost Policy (`cost_threshold`)

Add a `cost_threshold` policy to your configuration to block or warn on deployments
that exceed a monthly cost limit.

```yaml
# config/configuration.yaml
spec:
  policies:
    # Block production deployments over €10,000/month
    - name: prod_cost_gate
      type: cost_threshold
      phase: plan
      enforcement: deny
      configuration:
        max_monthly: 10000
        currency: EUR

    # Warn when dev environments exceed €500/month
    - name: dev_cost_cap
      type: cost_threshold
      phase: plan
      enforcement: warn
      configuration:
        max_monthly: 500
        environment_pattern: "dev*"
        currency: EUR
```

The policy reads from the pre-computed `cost.json` in the build directory.
**Run `strata cost show` before `strata deploy` to generate the cost file.**

See [Policy Types](../platform/policies.md#cost_threshold) for full configuration reference.

---

## Configuration Reference

### Integration entry (configuration.yaml)

```yaml
spec:
  integrations:
    - name: infracost
      type: infracost
      capabilities: [cost]
      required: false        # graceful degradation if not installed
      validation:
        command: infracost --version
        min_version: "0.10.0"
      authentication:
        method: cli           # uses existing cloud credentials
```

Add this to the configuration for deployments using Azure, AWS, or GCP. Infracost is
already included in the built-in `azure-aks`, `aws-eks`, and `gcp-gke` templates.

---

## Troubleshooting

| Problem                        | Solution                                                                    |
| ------------------------------ | --------------------------------------------------------------------------- |
| `No cost estimator available`  | Install Infracost: https://www.infracost.io/docs/install                    |
| `Terraform not initialized`    | Run `terraform init` in the build directory first                           |
| `No build artifacts found`     | Run `strata build run` first                                                |
| `No terraform provisioners`    | Check workspace YAML has a `terraform` provisioner                          |
| Estimate seems stale           | Use `--refresh` to bypass the 7-day cache                                   |
| `cost.json` missing for policy | Run `strata cost show` before `strata deploy`                               |
| Provider not supported         | Infracost only covers Azure, AWS, GCP — Hetzner/Kamatera return no estimate |
