# Infracost Integration

Infracost shows cloud cost estimates for Terraform configurations. strata uses it to
generate cost breakdowns and diffs after `strata build run` and during `strata deploy
run --dry-run`.

Installation
- macOS: `brew install infracost`
- Linux (script): `curl -fsSL https://raw.githubusercontent.com/infracost/infracost/master/scripts/install.sh | sh`
- Windows (Chocolatey): `choco install infracost`
- Docker: `docker run infracost/infracost`
- Docs: https://www.infracost.io/docs/install

Verify install
```
infracost --version
```

Minimum recommended version: 0.10.0

Authentication (optional)
Infracost works without an API key using its bundled pricing database. For the latest
cloud prices and additional features, register a free API key:

```
infracost auth login
```

Or set the key directly:
```
export INFRACOST_API_KEY=ico-xxxx
```

Cloud credentials
Infracost uses the same cloud credentials as Terraform — no separate auth setup needed:
- **Azure**: `az login` or service principal env vars (`ARM_CLIENT_ID`, etc.)
- **AWS**: `aws configure` or `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
- **GCP**: `gcloud auth application-default login`

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
```

Usage
```
strata cost show   -f deploy/deploy-prd.yaml   # monthly cost estimate
strata cost diff   -f deploy/deploy-prd.yaml   # cost impact of changes
strata cost history -f deploy/deploy-prd.yaml  # historical snapshots
```

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

Docs
- https://www.infracost.io/docs
