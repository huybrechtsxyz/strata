# Run Deployment

Execute a strata deployment (plan or apply) from a built platform artifact. Requires `setup-strata` and `build-deployment` to have run first.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/build-deployment@v1
  with:
    deployment_file: deploy/deploy-prd.yaml
- uses: huybrechtsxyz/strata/.github/actions/run-deployment@v1
  with:
    deployment_file: deploy/deploy-prd.yaml
    dry_run: "false"   # set to "false" to actually deploy
```

## Inputs

| Input             | Required | Default | Description                                                         |
| ----------------- | -------- | ------- | ------------------------------------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file                             |
| `deployment_name` | No       | —       | Human-readable name of the deployment (used for log output)         |
| `dry_run`         | No       | `true`  | Run in dry-run mode (plan only, no apply). Set to `false` to deploy |
| `work_path`       | No       | `.`     | Working directory containing the built `.strata/` artifact          |

## Outputs

None.

## Artifact

Uploads `terraform-plans-{deployment_name}` containing `**/*.tfplan.json` files, if any were produced.
