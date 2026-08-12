# Run

Run any strata command. The extensibility escape hatch — use this for subcommands that don't have a dedicated composite action yet.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/run@v1
  id: schema
  with:
    command: "schema get deployment"

- run: echo '${{ steps.schema.outputs.result }}'
```

## Inputs

| Input           | Required | Default | Description                                                                 |
| --------------- | -------- | ------- | --------------------------------------------------------------------------- |
| `command`       | Yes      | —       | The strata command and arguments to run (e.g. `schema get deployment`)      |
| `work_path`     | No       | `.`     | Workspace root directory                                                    |
| `output_format` | No       | `json`  | Output format: `json`, `text`, or `console`. Leave empty to omit `--output` |

## Outputs

| Output      | Description                 |
| ----------- | --------------------------- |
| `exit_code` | Numeric exit code           |
| `result`    | Raw stdout from the command |

## When to use this vs. a dedicated action

Prefer a dedicated action (`validate-deployment`, `build-deployment`, etc.) when one exists — it has typed inputs/outputs and clearer step summaries. Use `run` for less common commands like `strata secret generate`, `strata sln doctor`, or `strata schema list` that don't warrant their own action.
