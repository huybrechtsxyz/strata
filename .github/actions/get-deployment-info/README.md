# Get Deployment Info

Extract metadata from a strata deployment YAML file and expose it as individual outputs and a compact JSON blob. Requires `setup-yq`.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-yq@v1
- uses: huybrechtsxyz/strata/.github/actions/get-deployment-info@v1
  id: info
  with:
    deployment_file: deploy/deploy-prd.yaml

- run: echo "Deploying ${{ steps.info.outputs.name }} (${{ steps.info.outputs.kind }})"
```

## Inputs

| Input             | Required | Default | Description                             |
| ----------------- | -------- | ------- | --------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file |

## Outputs

| Output        | Description                                                     |
| ------------- | --------------------------------------------------------------- |
| `name`        | Deployment name from `.meta.name`                               |
| `kind`        | Document kind from `.kind` (e.g. `deployment`, `workspace`)     |
| `api_version` | API version from `.apiVersion`                                  |
| `description` | Human-readable description from `.meta.annotations.description` |
| `version`     | Deployment version label from `.meta.labels.version`            |
| `info_json`   | All metadata fields as a compact JSON object                    |

## Use cases

- Step summary enrichment (deployment name, kind, version)
- Matrix strategies that branch on deployment kind or environment
- PR comments and audit trails needing human-readable deployment metadata
