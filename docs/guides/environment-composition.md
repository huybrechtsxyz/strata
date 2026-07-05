# Environment Composition

Compose environments from multiple files to separate shared baseline settings from
environment-specific overrides — without repeating yourself.

---

## Why compose?

A single environment file works fine for one environment. Once you have several, common
settings (shared variables, security config, audit policy) get duplicated across each file.
If a shared value changes, every file must be updated in sync.

Composition solves this:

```
environments/
├── base.yaml          ← variables, secrets, audit, and security policy every env shares
├── eu-region.yaml     ← region-specific settings (shared across EU deployments)
└── prd.yaml           ← production-specific overrides (counts, images, feature flags)
```

```yaml
# deploy-prd.yaml
spec:
  environments:
    - environments/base.yaml
    - environments/eu-region.yaml
    - environments/prd.yaml     # last file wins on any key conflict
```

strata merges all listed files — left to right — into a single effective environment
before applying overrides to the workspace.

---

## Merge semantics

Each spec section follows a documented merge strategy. There are no surprises.

| Section                  | Strategy                                                | Notes                                                                                  |
| ------------------------ | ------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `variables`              | Last-wins by `key`                                      | `base.yaml` declares `DB_HOST`; `prd.yaml` overrides it                                |
| `secrets`                | Last-wins by `key`                                      | Same pattern as variables                                                              |
| `features`               | Last-wins by `key`                                      | Each flag merged independently — `prd.yaml` can flip one flag without replacing others |
| `properties`             | Shallow `dict.update()`                                 | Keys not present in later files are preserved from earlier files                       |
| `custom`                 | Shallow `dict.update()`                                 | Same as `properties`                                                                   |
| `lifecycle`              | Last-wins (wholesale)                                   | The last file that declares `lifecycle` becomes the effective lifecycle                |
| `audit`                  | Last-wins (wholesale)                                   | Same as `lifecycle`                                                                    |
| `overrides.resources`    | Last-wins by `resource` name                            | `prd.yaml` can set `count: 3` on `manager` without touching `worker`                   |
| `overrides.modules`      | Last-wins by `(module, resource, namespace, slot_type)` | Matches the override uniqueness constraint                                             |
| `overrides.providers`    | Last-wins by `provider` name                            | —                                                                                      |
| `overrides.remotes`      | Last-wins by `remote` name                              | Pin different branches per env                                                         |
| `overrides.properties`   | Shallow `dict.update()`                                 | —                                                                                      |
| `overrides.includes`     | Append (dedup by `source` path)                         | Includes are additive                                                                  |
| `overrides.output_files` | Append (dedup by output `path`)                         | Output files are additive                                                              |

**Single-file deployments are unaffected.** The merge runs but produces an identical result.

---

## Example: base + prd

### `environments/base.yaml`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: base
  labels:
    version: "1.0.0"
spec:
  variables:
    - key: APP_PORT
      store: constant
      value: "8080"
    - key: LOG_LEVEL
      store: constant
      value: info
  secrets:
    - key: TERRAFORM_API_TOKEN
      store: bitwarden
      value: <shared-tf-token-id>
  features:
    - key: enable_metrics
      store: constant
      value: true
    - key: enable_debug
      store: constant
      value: false
  audit:
    policy:
      events:
        deploy_audit: true
        policy_violation: true
```

### `environments/prd.yaml`

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: production
  labels:
    version: "1.0.0"
spec:
  variables:
    - key: LOG_LEVEL        # overrides base
      store: constant
      value: warning
    - key: REPLICA_COUNT    # prd-only
      store: constant
      value: "5"
  secrets:
    - key: DB_PASSWORD      # prd-only
      store: bitwarden
      value: <prd-db-id>
  features:
    - key: enable_debug     # overrides base: keeps enable_metrics=true
      store: constant
      value: false
  overrides:
    resources:
      - resource: manager
        count: 3
    properties:
      ha_enabled: true
```

### Effective result

| Key                      | Value       | From      |
| ------------------------ | ----------- | --------- |
| `APP_PORT`               | `8080`      | base.yaml |
| `LOG_LEVEL`              | `warning`   | prd.yaml  |
| `REPLICA_COUNT`          | `5`         | prd.yaml  |
| `TERRAFORM_API_TOKEN`    | (bitwarden) | base.yaml |
| `DB_PASSWORD`            | (bitwarden) | prd.yaml  |
| `enable_metrics`         | `true`      | base.yaml |
| `enable_debug`           | `false`     | prd.yaml  |
| `manager.count` override | `3`         | prd.yaml  |
| `ha_enabled` property    | `true`      | prd.yaml  |

---

## Tracing value origins with `--trace`

When debugging a deployment, use `--trace` to see which file contributed each value:

```bash
strata values list -f deploy/deploy-prd.yaml --trace
```

Console output:

```
  VARIABLES
  ──────────────────────────────────────────────────────────────────
  KEY            STORE     VALUE / STATUS  SOURCE
  ──────────────────────────────────────────────────────────────────
  APP_PORT       constant  8080            environments/base.yaml
  LOG_LEVEL      constant  warning         environments/prd.yaml
  REPLICA_COUNT  constant  5               environments/prd.yaml

  SECRETS
  ──────────────────────────────────────────────────────────────────
  KEY                   STORE      VALUE / STATUS  SOURCE
  ──────────────────────────────────────────────────────────────────
  TERRAFORM_API_TOKEN   bitwarden  abc*****        environments/base.yaml
  DB_PASSWORD           bitwarden  xyz*****        environments/prd.yaml

  Merge order: environments/base.yaml → environments/prd.yaml
```

Combine with `--show-store` for full store reference details:

```bash
strata values list -f deploy/deploy-prd.yaml --trace --show-store
```

### JSON output with `--trace`

```bash
strata values list -f deploy/deploy-prd.yaml --trace --output json
```

Each row gains `source` (the winning file) and the top-level response includes `merge_order`:

```json
{
  "file": "deploy/deploy-prd.yaml",
  "merge_order": ["environments/base.yaml", "environments/prd.yaml"],
  "variables": [
    {
      "key": "LOG_LEVEL",
      "store": "constant",
      "store_ref": "warning",
      "display": "warning",
      "ok": true,
      "note": "",
      "source": "environments/prd.yaml"
    }
  ]
}
```

---

## Common patterns

### Base + region + environment

```yaml
# deploy-us-prd.yaml
spec:
  environments:
    - environments/base.yaml
    - environments/us-east.yaml   # region-specific networking, KMS keys
    - environments/prd.yaml       # production sizing and feature flags
```

### Shared security policy

Centralise `audit` and `features` for compliance requirements in `base.yaml`. Individual
env files only override what differs. Since `audit` and features are last-wins, any env
file that doesn't declare them automatically inherits the base policy.

### Tenant overlays

```yaml
# deploy-acme-prd.yaml
spec:
  environments:
    - environments/base.yaml
    - environments/prd.yaml
    - tenants/acme/env-overrides.yaml   # tenant-specific custom metadata and secrets
```

---

## Merge validation

strata validates each file individually before merging. If any file fails schema
validation, the merge is aborted and the error points to the specific file.

After merging, the resulting model is re-validated. Duplicate keys within a single file
are still rejected (enforced by model validators) — the last-wins rule only applies
**across files**.

---

## See also

- [Environment configuration reference](../config/environment.md) — full schema and field reference
- [Deployment configuration reference](../config/deployment.md) — how to declare environment lists
- [Cookbook: Add a New Environment](cookbook-add-environment.md) — step-by-step for adding a second env
- [`strata values list`](../config/deployment.md#inspecting-resolved-values) — resolve and inspect values
