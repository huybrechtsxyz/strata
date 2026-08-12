# Get Deployment Name

Extract the deployment name from a strata deployment YAML file using `yq`. Requires `setup-yq`.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-yq@v1
- uses: huybrechtsxyz/strata/.github/actions/get-deployment-name@v1
  id: name
  with:
    deployment_file: deploy/deploy-prd.yaml

- run: echo "Deploying ${{ steps.name.outputs.deployment_name }}"
```

## Inputs

| Input             | Required | Default | Description                             |
| ----------------- | -------- | ------- | --------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file |

## Outputs

| Output            | Description                                     |
| ----------------- | ----------------------------------------------- |
| `deployment_name` | The deployment name extracted from `.meta.name` |

## Notes

Lighter-weight than `get-deployment-info` — use this when you only need the name (e.g. for artifact naming) and don't need the full metadata set.
