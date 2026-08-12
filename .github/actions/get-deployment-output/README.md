# Get Deployment Output

Retrieve Terraform output(s) for a deployment via `strata deploy output` — backend-agnostic (local, S3, GCS, Azure Blob, Consul, Terraform Cloud/HCP, etc.), using strata's own local cache. Requires `setup-strata`.

## Usage

```yaml
# Get a single output value
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/get-deployment-output@v1
  id: endpoint
  with:
    deployment_file: deploy/deploy-prd.yaml
    key: endpoint

- run: curl "${{ steps.endpoint.outputs.value }}/health"
```

```yaml
# Get all outputs as JSON
- uses: huybrechtsxyz/strata/.github/actions/get-deployment-output@v1
  id: outputs
  with:
    deployment_file: deploy/deploy-prd.yaml

- run: echo '${{ steps.outputs.outputs.result }}' | jq .
```

## Inputs

| Input             | Required | Default | Description                                                                             |
| ----------------- | -------- | ------- | --------------------------------------------------------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file                                                 |
| `key`             | No       | —       | A single output key to retrieve. Leave empty to retrieve all outputs as JSON            |
| `stage`           | No       | —       | Limit to a single deployment stage                                                      |
| `provisioner`     | No       | —       | Limit to stages that use a specific provisioner                                         |
| `refresh`         | No       | `false` | Re-run `terraform output -json` and update the cache instead of reading the local cache |
| `work_path`       | No       | `.`     | Workspace root directory                                                                |

## Outputs

| Output      | Description                                                                                |
| ----------- | ------------------------------------------------------------------------------------------ |
| `value`     | The bare output value, when `key` is set (masked automatically, since it may be sensitive) |
| `result`    | All outputs as JSON, when `key` is not set                                                 |
| `exit_code` | Numeric exit code from `strata deploy output`                                              |

## Notes

- Without `refresh: "true"`, this reads from strata's local `.tf-outputs.json` cache — fast, but may be stale if infrastructure changed outside this workflow run.
- With `key` set, the output is masked (`::add-mask::`) since output values sometimes carry sensitive data (connection strings, generated passwords, etc.).
- This replaces the archived `get-tfoutput` action, which called `terraform pull`/`terraform show` directly and only worked against HashiCorp Cloud Platform (Terraform Cloud). This version works with whatever backend the deployment's provisioner is configured for.
