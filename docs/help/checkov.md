# Checkov Integration

Checkov is an open-source IaC static security scanner by Snyk/Bridgecrew. strata runs
`checkov --directory <terraform_dir> --output json` against Terraform build artifacts
during the `build` phase when a `checkov` policy is configured.

Installation
```
pip install checkov
```
Or via Homebrew:
```
brew install checkov
```
Or via Docker: `docker run bridgecrew/checkov`

Verify install
```
checkov --version
```

Minimum recommended version: 2.0.0

Activation
Checkov requires no credentials. Declare the integration and add a policy:

```yaml
integrations:
  - name: checkov
    type: checkov
    capabilities: [iac_security]
    required: false
    validation:
      command: checkov --version
      min_version: "2.0.0"

policies:
  - name: terraform_security_baseline
    type: checkov
    phase: build
    enforcement: deny
    configuration:
      framework: terraform        # default: terraform
      severity_gate: high         # critical|high|medium|low (default: high)
      skip_checks:                # suppress specific checks
        - CKV_AWS_1
      timeout: 120
```

How it works
1. `strata build run` generates Terraform artifacts under `build/<deployment>/terraform/`
2. The `checkov` policy invokes `checkov --directory <dir> --output json --compact`
3. Findings at or above `severity_gate` become policy violations

Severity gate

| `severity_gate`  | Blocks on                 |
| ---------------- | ------------------------- |
| `critical`       | CRITICAL only             |
| `high` (default) | HIGH or CRITICAL          |
| `medium`         | MEDIUM, HIGH, or CRITICAL |
| `low`            | any finding               |

Graceful degradation
- Checkov not installed → policy skips (passes), warning logged — never blocks a build
- No `.tf` files found → policy skips
- Subprocess fails → policy skips (non-fatal)

Custom checks
Place `.py` rule files in `.strata/checkov/custom/` and reference in policy:
```yaml
configuration:
  custom_checks_dir: ".strata/checkov/custom/"
```

Docs
- https://www.checkov.io
- Check library: https://www.checkov.io/5.Policy%20Index/terraform.html
