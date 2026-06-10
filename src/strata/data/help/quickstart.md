# Quick Start

Get from a fresh install to a running workspace in under 10 minutes.

## Prerequisites

Before you start, ensure the following are available:
- Git (required for `strata repo add` and sync operations)
- Any integrations your config files reference (terraform, docker, etc.)
- Credentials or auth tokens for private repositories

## Canonical Workflow

```
strata sln init --name myproject                         # create workspace
strata repo add myrepo https://github.com/org/repo   # register a git repo
strata profile add dev                               # create a profile
strata profile activate dev                          # set active profile
strata ref envfile add --profile dev --name base --path ./dev.env
strata ref configfile add --profile dev --name app --path ./app.yaml
strata log list                                      # verify activity
```

## Verifying Your Workspace

After setup, check that everything resolved correctly:
- `strata profile list` — confirm 'dev' is active
- `strata ref envfile list --profile dev` — confirm refs are registered
- `strata log list` — review what ran and any warnings

## What NOT to Commit

The `.strata/` directory contains runtime state. Add `.strata/logs/` and
`.strata/merged-config.yaml` to your `.gitignore`. The scaffold does this
automatically — check `.strata/.gitignore` after `strata sln init`.

See also: `strata help --topic workspace`, `strata help --topic profiles`
