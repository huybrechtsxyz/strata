# Infracost Integration

Infracost shows cloud cost estimates for Terraform configurations. strata uses it to
generate cost breakdowns and diffs after `strata build run` and during `strata deploy
run --dry-run`.

> **Only the 0.10.x CLI is supported.** Infracost 2.0 replaced the `breakdown`/`diff`
> commands this integration drives with a different command set (`auth login`, `setup`,
> `scan`, `inspect`, `update`), hosted in a separate repository (`infracost/cli`). The
> generic `https://www.infracost.io/docs/install` page now installs the v2 CLI, which
> this integration cannot drive — use the pinned 0.10.x installer below instead.

Installation (0.10.x — pinned, do not use the generic install page)
- Script (Linux/macOS, restricted to `<1.0.0`): `curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh`
- Or download a specific `0.10.x` release directly from https://github.com/infracost/infracost/releases

Verify install
```
infracost --version
```

Minimum recommended version: 0.10.0 (last 0.10.x release: v0.10.45). Version strings
`2.x.y` and above are rejected by this integration even if `min_version` alone would
otherwise accept them — set an explicit `max_version` (see Configuration YAML below)
so a v2 install fails validation with a clear message instead of failing later at
command dispatch.

Authentication (required)
Infracost has **no bundled pricing database and no anonymous/offline mode** — every
estimate is a live call to `pricing.api.infracost.io`, which requires an Infracost
account. Register a free API key and either:

```
infracost auth login
```

Or set the key directly (required for CI/non-interactive use):
```
export INFRACOST_API_KEY=ico-xxxx
```

Cloud credentials (Azure CLI, AWS env vars, GCP application-default credentials) are
used by Terraform itself and are unrelated to Infracost's pricing-lookup auth — having
them configured does **not** satisfy Infracost's authentication requirement.

Self-hosted Cloud Pricing API (enterprise networks)
If `pricing.api.infracost.io` / `dashboard.api.infracost.io` are unreachable from your
network (e.g. blocked by corporate egress rules), point Infracost at a self-hosted
Cloud Pricing API instead:

```
export INFRACOST_PRICING_API_ENDPOINT=https://internal-pricing.example.com
```

See https://www.infracost.io/docs/cloud_pricing_api/self_hosted/ for hosting it yourself.

Configuration YAML

```yaml
integrations:
  - name: infracost
    type: infracost
    capabilities: [cost]
    required: false
    validation:
      command: infracost --version
      min_version: "0.10.0"
      max_version: "0.99.99"   # reject Infracost 2.x — different, unsupported CLI
```

Usage
```
strata cost show   -f deploy/deploy-prd.yaml   # monthly cost estimate
strata cost diff   -f deploy/deploy-prd.yaml   # cost impact of changes
strata cost history -f deploy/deploy-prd.yaml  # historical snapshots
```

`strata cost show` requires the provisioner's `.terraform/` directory to already exist
(`terraform init`). For workspaces whose remote backend state is only reachable from CI
credentials, this makes `cost show` a CI-only workflow for that provisioner — running
`terraform init -backend=false` locally is enough for module resolution if you only
need resource-shape cost estimates, not a state-aware diff.

Cost threshold policy
```yaml
policies:
  - name: cost_gate
    type: cost_threshold
    phase: plan
    enforcement: deny
    configuration:
      max_monthly_cost: 500.00
      currency: USD
```

The policy reads `cost.json`, which is only written by `strata cost show`. If that step
never ran, the policy skips (passes) rather than failing closed — the skip is logged at
warning level so it's visible in normal command output, not just debug logs.

Docs
- https://www.infracost.io/docs
