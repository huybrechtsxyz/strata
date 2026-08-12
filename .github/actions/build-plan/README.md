# Build Plan

Show artifact diff + terraform plan without writing to the real build path. Nothing is written — safe for PR previews. Requires `setup-strata`.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/build-plan@v1
  id: plan
  with:
    deployment_file: deploy/deploy-prd.yaml

- name: Show if anything would change
  if: steps.plan.outputs.has_changes == 'true'
  run: echo "Plan shows changes"
```

## Inputs

| Input             | Required | Default | Description                                   |
| ----------------- | -------- | ------- | --------------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file       |
| `stage`           | No       | —       | Limit terraform plan to a single stage        |
| `artifacts_only`  | No       | `false` | Skip terraform plan — show artifact diff only |
| `work_path`       | No       | `.`     | Workspace root directory                      |

## Outputs

| Output        | Description                                                                |
| ------------- | -------------------------------------------------------------------------- |
| `exit_code`   | Numeric exit code from `strata build plan`                                 |
| `has_changes` | `true`/`false` — whether any artifacts or terraform resources would change |
| `result`      | Raw JSON output (the full strata response envelope)                        |
