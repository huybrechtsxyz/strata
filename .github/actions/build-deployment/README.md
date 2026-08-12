# Build Deployment

Build the strata platform artifact from a deployment file. Writes output to `.strata/` in the work path. Requires `setup-strata`.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/build-deployment@v1
  with:
    deployment_file: deploy/deploy-prd.yaml
    deployment_name: prd
```

## Inputs

| Input             | Required | Default | Description                                                                         |
| ----------------- | -------- | ------- | ----------------------------------------------------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file                                             |
| `work_path`       | No       | `.`     | Working directory for the build output (`.strata/` is written here)                 |
| `deployment_name` | No       | —       | Name used for the uploaded artifact (`strata-build-{name}`). Defaults to the run ID |
| `upload_artifact` | No       | `true`  | Upload the `.strata/` build output as a GitHub Actions artifact                     |

## Outputs

None.

## Artifact

Uploads `strata-build-{deployment_name}` containing the `.strata/` directory — available for inspection even when a later step (e.g. deploy) fails. Set `upload_artifact: false` to skip.
