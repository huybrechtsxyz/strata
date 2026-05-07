# Quick Start

Get from a fresh install to a running workspace in under 10 minutes.

## Prerequisites

Before you start, ensure the following are available:
- Git (required for `xyz repo add` and sync operations)
- Any integrations your config files reference (terraform, docker, etc.)
- Credentials or auth tokens for private repositories

## Canonical Workflow

```
xyz init --name myproject                         # create workspace
xyz repo add myrepo https://github.com/org/repo   # register a git repo
xyz profile add dev                               # create a profile
xyz profile activate dev                          # set active profile
xyz ref envfile add --profile dev --name base --path ./dev.env
xyz ref configfile add --profile dev --name app --path ./app.yaml
xyz log list                                      # verify activity
```

## Verifying Your Workspace

After setup, check that everything resolved correctly:
- `xyz profile list` — confirm 'dev' is active
- `xyz ref envfile list --profile dev` — confirm refs are registered
- `xyz log list` — review what ran and any warnings

## What NOT to Commit

The `.platform/` directory contains runtime state. Add `.platform/logs/` and
`.platform/merged-config.yaml` to your `.gitignore`. The scaffold does this
automatically — check `.platform/.gitignore` after `xyz init`.

See also: `xyz help --topic workspace`, `xyz help --topic profiles`
