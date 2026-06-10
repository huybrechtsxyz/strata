# Config Merge

How strata resolves the final configuration from multiple layered config refs.

## Merge Mechanics

Config files registered under `configfile` refs are deep-merged in registration
order. Later files override earlier files at every nesting level. The result is
stored in memory as `_merged_config` and written to `.strata/configuration.yaml`
as a debug artifact after every command run.

Example — three layers:

```
base.yaml       → {db: {host: localhost, port: 5432}, log_level: INFO}
prod.yaml       → {db: {host: db.prod.example.com}}
local.yaml      → {log_level: DEBUG}

Result          → {db: {host: db.prod.example.com, port: 5432}, log_level: DEBUG}
```

`port` is preserved from `base.yaml` because `prod.yaml` only overrides `host`.
Deep merge means keys are merged at every level, not replaced wholesale.

## Env Files vs. Config Files

|             | `envfile` refs                  | `configfile` refs                  |
| ----------- | ------------------------------- | ---------------------------------- |
| Format      | `.env` KEY=VALUE                | YAML                               |
| Destination | `os.environ`                    | `self._merged_config` dict         |
| Merge       | Later value wins (flat)         | Deep merge (nested keys preserved) |
| Use case    | Auth tokens, connection strings | Structured app/infra config        |

## Inspecting the Merged Output

After any command runs, check `.strata/configuration.yaml` to see what was
actually merged. This file is overwritten on every run — it reflects the last
command's active profile at that moment.

```
cat .strata/configuration.yaml    # Unix
Get-Content .strata\configuration.yaml    # PowerShell
```

## Common Mistakes

- **Wrong order:** If `local.yaml` overrides `prod.yaml` but was registered first,
  `prod.yaml` wins. Check registration order with `strata ref configfile list`.
- **Missing ref:** A ref pointing to a non-existent file is skipped with a debug
  warning — it does not cause a failure. If your config looks wrong, check the log.
- **Wrong profile active:** `strata profile list` shows which profile is active.
  Verify before running.

See also: `strata help --topic refs`, `strata help --topic profiles`
