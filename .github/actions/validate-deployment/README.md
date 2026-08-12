# Validate Deployment

Validate a strata deployment file against its schema. Requires `setup-strata`.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/validate-deployment@v1
  with:
    deployment_file: deploy/deploy-prd.yaml
```

## Inputs

| Input             | Required | Default | Description                                         |
| ----------------- | -------- | ------- | --------------------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file to validate |

## Outputs

None. The step fails (non-zero exit) if validation fails — use `continue-on-error: true` if you need to inspect the result before failing the job.
