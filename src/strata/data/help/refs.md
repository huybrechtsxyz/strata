# Refs

Understanding references: env files, config files, and how they layer.

## What a Ref Is

A ref is a named, ordered pointer to a file that belongs to a profile. When a
command runs, the active profile's refs are resolved and loaded in order.

There are four ref types:

| Type         | CLI                      | Loaded as                                          |
| ------------ | ------------------------ | -------------------------------------------------- |
| `envfile`    | `strata ref envfile ...`    | Variables injected into `os.environ`               |
| `configfile` | `strata ref configfile ...` | YAML files deep-merged into `self._merged_config`  |
| `datafile`   | `strata ref datafile ...`   | Available for integration use (e.g., Ansible vars) |
| `secretfile` | `strata ref secretfile ...` | Same as datafile — treated as sensitive            |

## Adding Refs

```
strata ref envfile add --profile dev --name base --path ./envs/dev.env
strata ref configfile add --profile dev --name app --path ./config/app.yaml
strata ref configfile add --profile dev --name overrides --path ./config/local.yaml
```

Names must be unique within a profile and ref type. Registration order controls
merge precedence — later-added refs override earlier ones.

## Cross-Repo Paths

Paths can reference files in registered repos using `@repo_name/` notation:

```
strata ref configfile add --profile dev --name shared --path @infra-repo/config/base.yaml
```

The repo `infra-repo` must be registered with `strata repo add` before this ref
can be resolved. See `strata help --topic cross-repo`.

## Listing Refs

```
strata ref envfile list --profile dev
strata ref configfile list --profile dev
strata ref envfile list                   # lists for the active profile
```

See also: `strata help --topic config-merge`, `strata help --topic cross-repo`
