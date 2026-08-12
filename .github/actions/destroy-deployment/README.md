# Destroy Deployment

Tear down infrastructure for a deployment. Requires `force: "true"` for a real destroy — use with caution.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/destroy-deployment@v1
  with:
    deployment_file: deploy/deploy-prd.yaml
    dry_run: "true"    # preview only — nothing is destroyed
```

```yaml
# Real destroy (non-interactive CI) — requires explicit opt-in
- uses: huybrechtsxyz/strata/.github/actions/destroy-deployment@v1
  with:
    deployment_file: deploy/deploy-prd.yaml
    dry_run: "false"
    force: "true"
```

## Inputs

| Input             | Required | Default | Description                                                            |
| ----------------- | -------- | ------- | ---------------------------------------------------------------------- |
| `deployment_file` | Yes      | —       | Path to the strata deployment YAML file                                |
| `stage`           | No       | —       | Limit destruction to a single deployment stage                         |
| `force`           | No       | `false` | Auto-approve destruction (required for a non-interactive/real destroy) |
| `dry_run`         | No       | `true`  | Run `terraform plan -destroy` only — nothing is destroyed              |
| `work_path`       | No       | `.`     | Workspace root directory                                               |

## Outputs

| Output      | Description                                         |
| ----------- | --------------------------------------------------- |
| `exit_code` | Numeric exit code from `strata deploy destroy`      |
| `result`    | Raw JSON output (the full strata response envelope) |

## Caution

This action deletes real infrastructure when `dry_run: "false"` and `force: "true"` are both set. Review the plan output first — consider requiring manual workflow approval before this step runs.
