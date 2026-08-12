# Has Changes

Check whether a directory's contents have changed since the last successful run, by hashing its files and comparing against a cached key. No strata CLI call involved — pure content-hash detection, useful for skipping validate/build/deploy steps for unchanged deployments in a monorepo/fleet CI matrix.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/has-changes@v1
  id: changes
  with:
    workspace_name: prod
    hashfile: /tmp/prod.hash
    dir: deploy/prod

- name: Validate (only if changed)
  if: steps.changes.outputs.changed == 'true'
  uses: huybrechtsxyz/strata/.github/actions/validate-deployment@v1
  with:
    deployment_file: deploy/prod/deploy-prd.yaml
```

## Inputs

| Input            | Required | Default | Description                                         |
| ---------------- | -------- | ------- | --------------------------------------------------- |
| `workspace_name` | Yes      | —       | Name of the workspace (used to scope the cache key) |
| `hashfile`       | Yes      | —       | Path to write the computed hash to                  |
| `dir`            | Yes      | —       | Directory to check for changes                      |

## Outputs

| Output    | Description                                                                               |
| --------- | ----------------------------------------------------------------------------------------- |
| `changed` | `'true'` if directory contents changed since the last successful run, `'false'` otherwise |

## How it works

1. Hashes every file under `dir` (sorted, so file order doesn't affect the result) into a single SHA-256 digest
2. Uses that digest as part of a GitHub Actions cache key — a cache **hit** means the same digest was seen on a previous run (nothing changed); a **miss** means this is a new digest (something changed)
3. The cache is automatically saved at job end (standard `actions/cache` behavior), so the next run can compare against it

## Use case: skip unchanged deployments in a matrix

```yaml
strategy:
  matrix:
    deployment: [prod, staging, dev]

steps:
  - uses: huybrechtsxyz/strata/.github/actions/has-changes@v1
    id: changes
    with:
      workspace_name: ${{ matrix.deployment }}
      hashfile: /tmp/${{ matrix.deployment }}.hash
      dir: deploy/${{ matrix.deployment }}

  - if: steps.changes.outputs.changed == 'true'
    run: echo "Deploying ${{ matrix.deployment }} — changes detected"

  - if: steps.changes.outputs.changed == 'false'
    run: echo "Skipping ${{ matrix.deployment }} — no changes"
```

## Notes

This is a generic file-hash mechanism, not a strata-aware diff — it doesn't know that changing a comment vs. a resource definition are different in importance. For a semantically meaningful "what would actually change" check, use `build-plan` instead (compares generated artifacts and terraform plan output).
