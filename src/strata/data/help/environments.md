# Environments

Mapping profiles to deployment environments (dev, stg, prd).

## Convention

Create one profile per deployment environment, named to match:

```
xyz profile add dev
xyz profile add stg
xyz profile add prd
```

Each profile gets its own refs pointing to environment-specific files:

```
xyz ref envfile add --profile dev --name base --path ./envs/dev.env
xyz ref envfile add --profile stg --name base --path ./envs/stg.env
xyz ref envfile add --profile prd --name base --path ./envs/prd.env

xyz ref configfile add --profile dev --name app --path ./config/app-dev.yaml
xyz ref configfile add --profile stg --name app --path ./config/app-stg.yaml
xyz ref configfile add --profile prd --name app --path ./config/app-prd.yaml
```

## Promoting Config from dev → stg → prd

The recommended pattern is layered config:

1. Register a `base` configfile ref in every profile pointing to the same shared
   base config (e.g., `@infra-repo/config/base.yaml`).
2. Register an `overrides` configfile ref per profile pointing to environment-
   specific overrides (e.g., `./config/overrides-stg.yaml`).
3. The overrides file only contains what differs. The merge produces the full
   config for that environment.

This means promoting a change means updating the shared base — all profiles
inherit it automatically.

## Switching Between Environments

```
xyz profile activate stg    # switch to staging context
xyz log list                # review what loaded
```

All subsequent commands operate against the staging profile until you switch again.

## Dry-Run Before Activation

Before activating a new profile for the first time, inspect the merged output:

```
xyz profile activate dev
xyz log list --level debug    # see which files loaded and what was skipped
cat .strata/configuration.yaml    # review merged config
```

Correct any missing refs or wrong paths before running build or deploy.

See also: `xyz help --topic profiles`, `xyz help --topic config-merge`
