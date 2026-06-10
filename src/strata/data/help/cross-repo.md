# Cross-Repo References

Working with `@repo_name/` references across a multi-repo workspace.

## Syntax

```
@repo_name/relative/path/to/file.yaml
```

The `repo_name` must match the name used when the repo was registered:

```
strata repo add infra-repo https://github.com/org/infra --path ./infra-repo
```

After that, `@infra-repo/config/base.yaml` resolves to `./infra-repo/config/base.yaml`
relative to the workspace root.

## Requirements

All referenced repos must be registered with `strata repo add` before any ref
using `@repo_name/` can be resolved. If the repo is registered but not cloned,
the path will not exist and the ref will be skipped with a debug warning.

To clone/sync registered repos:
```
strata repo sync
```

## Using Cross-Repo Refs

```
strata ref configfile add --profile dev --name shared-base --path @infra-repo/config/base.yaml
strata ref envfile add --profile dev --name secrets --path @secrets-repo/envs/dev.env
```

## When the Repo Map Gets Stale

If you rename a repo or change its local path with `strata repo add`, existing refs
using the old `@repo_name/` will stop resolving. Update the refs after renaming:

```
strata ref configfile remove --profile dev --name old-ref
strata ref configfile add --profile dev --name new-ref --path @new-name/config/base.yaml
```

## Circular References

Circular `@repo_name/` references (repo A points to repo B which points to repo A)
are detected and reported as validation errors. No partial loading occurs.

See also: `strata help --topic refs`, `strata help --topic workspace`
