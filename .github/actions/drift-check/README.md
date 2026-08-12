# Drift Check Action

Detect infrastructure drift between Terraform state and declared strata configuration.

## Usage

### In your repository's workflow

```yaml
name: Drift Detection

on:
  schedule:
    - cron: "0 2 * * *"  # Nightly at 02:00 UTC
  workflow_dispatch:

jobs:
  drift-check:
    name: Check Infrastructure Drift
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write

    steps:
      # Authenticate to your cloud provider
      - name: Checkout
        uses: actions/checkout@v4

      - name: Authenticate to Azure
        uses: azure/login@v2
        with:
          client-id: ${{ secrets.AZURE_CLIENT_ID }}
          tenant-id: ${{ secrets.AZURE_TENANT_ID }}
          subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}

      # Run drift detection
      - name: Detect drift
        id: drift
        uses: huybrechtsxyz/strata/.github/actions/drift-check@main
        with:
          deployment: deploy/production.yaml
          severity: high

      # Optionally post results to Slack
      - name: Notify Slack on drift
        if: steps.drift.outputs.has_drift == '1'
        uses: slackapi/slack-github-action@v1
        with:
          payload: |
            {
              "text": "🚨 Infrastructure drift detected in production",
              "blocks": [
                {
                  "type": "section",
                  "text": {
                    "type": "mrkdwn",
                    "text": "*Deployment:* `${{ inputs.deployment }}`\n*Total drift entries:* ${{ steps.drift.outputs.total }}\n*Critical:* ${{ steps.drift.outputs.critical }}\n*High:* ${{ steps.drift.outputs.high }}"
                  }
                }
              ]
            }
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

## Inputs

| Input            | Required | Default  | Description                                                      |
| ---------------- | -------- | -------- | ---------------------------------------------------------------- |
| `deployment`     | Yes      | —        | Path to deployment YAML file relative to repo root               |
| `severity`       | No       | `high`   | Minimum severity: `critical`, `high`, `medium`, `low`, or `info` |
| `baseline`       | No       | `false`  | Set current state as baseline (acknowledge all drift)            |
| `python-version` | No       | `3.13`   | Python version to use                                            |
| `strata-version` | No       | (latest) | Specific strata version to install                               |

## Outputs

| Output      | Description                                          |
| ----------- | ---------------------------------------------------- |
| `exit_code` | Exit code: `0` = clean, `3` = drift found            |
| `has_drift` | Boolean: `1` if drift above threshold, `0` otherwise |
| `total`     | Total number of drift entries                        |
| `critical`  | Number of critical-severity entries                  |
| `high`      | Number of high-severity entries                      |

## Authentication

This action requires credentials to access your cloud infrastructure. Authenticate before calling the action:

**Azure (recommended — uses OIDC):**
```yaml
- uses: azure/login@v2
  with:
    client-id: ${{ secrets.AZURE_CLIENT_ID }}
    tenant-id: ${{ secrets.AZURE_TENANT_ID }}
    subscription-id: ${{ secrets.AZURE_SUBSCRIPTION_ID }}
```

**Azure (service principal with secret):**
```yaml
- run: |
    az login --service-principal \
      --username "${{ secrets.AZURE_CLIENT_ID }}" \
      --password "${{ secrets.AZURE_CLIENT_SECRET }}" \
      --tenant "${{ secrets.AZURE_TENANT_ID }}"
```

**AWS:**
```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: ${{ secrets.AWS_ROLE_ARN }}
    aws-region: us-east-1
```

**GCP:**
```yaml
- uses: google-github-actions/auth@v2
  with:
    workload-identity-provider: ${{ secrets.WIF_PROVIDER }}
    service-account: ${{ secrets.GCP_SERVICE_ACCOUNT }}
```

## Example: Using in a monorepo with multiple deployments

```yaml
strategy:
  matrix:
    deployment:
      - deploy/staging.yaml
      - deploy/production.yaml

steps:
  - uses: huybrechtsxyz/strata/.github/actions/drift-check@main
    id: drift
    with:
      deployment: ${{ matrix.deployment }}
      severity: high

  - name: Report
    run: |
      echo "Checked ${{ matrix.deployment }}"
      echo "Drift entries: ${{ steps.drift.outputs.total }}"
      if [ "${{ steps.drift.outputs.exit_code }}" != "0" ]; then
        echo "::warning::Drift detected"
      fi
```

## Exit Codes

- **0**: No drift detected at or above the specified severity threshold
- **3**: Drift detected; review `drift-report` artifact for details
- **1**: System error (missing file, authentication failure, etc.)

## Artifact

The action uploads `drift-report.json` as a build artifact (`retention-days: 90`). Download it from the workflow run to inspect detailed drift entries.

Example drift report structure:
```json
{
  "success": true,
  "data": {
    "summary": {
      "total": 5,
      "critical": 1,
      "high": 2,
      "medium": 2
    },
    "entries": [
      {
        "address": "azurerm_resource_group.main",
        "action": "modify",
        "severity": "critical",
        "before": { "location": "eastus" },
        "after": { "location": "westus" }
      }
    ]
  }
}
```

## Troubleshooting

### Action fails with "strata command not found"

Ensure Python is installed before calling the action:
```yaml
- uses: actions/setup-python@v5
  with:
    python-version: '3.13'

- uses: huybrechtsxyz/strata/.github/actions/drift-check@main
  with:
    deployment: deploy/production.yaml
```

### "Not inside a strata workspace" error

The action expects a `.strata/` directory in your repository's root. Initialize it:
```bash
strata sln init
```

### Drift always shows zero entries

Verify your Terraform backend is reachable and authentication is working:
```bash
strata tools status --output json
```

## References

- [strata Documentation](https://strata.huybrechts.xyz)
- [Drift Detection Guide](https://strata.huybrechts.xyz/guides/drift-detection/)
- [Architecture Decision Record: Infrastructure Drift Detection](https://strata.huybrechts.xyz/decisions/0008-infrastructure-drift-detection/)
