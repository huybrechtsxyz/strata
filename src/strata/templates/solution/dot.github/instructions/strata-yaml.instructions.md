---
description: Authoring rules for strata YAML configuration files
applyTo: "**/*.yaml"
---

# Strata YAML Authoring Rules

Apply these rules whenever you create or edit a strata YAML configuration file.

## Document envelope (required on every file)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <kind>
meta:
  name: <name>
  annotations:
    description: "Human-readable description"
spec:
  ...
```

- `apiVersion` is always `strata.huybrechts.xyz/v1` — do not change it.
- `kind` must be one of: `workspace`, `configuration`, `deployment`, `namespace`, `module`, `environment`, `provider`, `resource`, `firewall`, `network`, `dns`, `tenant`.
- `meta.name` must match `^[a-z0-9][a-z0-9_-]*$` — lowercase, no spaces, letters/numbers/hyphens/underscores only.
- Models use `extra="forbid"` — any unknown field causes a validation error. Only use fields that exist in the schema.

## Cross-file references

Use `@repo_name/relative/path.yaml` to reference files in another registered repository:

```yaml
file: "@my-infra/modules/traefik.yaml"
```

Plain relative paths resolve from the workspace root.

## Secret and variable references

Never write secret values as plain strings. Use refs:

```yaml
environment:
  - key: DB_PASSWORD
    secret: DB_PASSWORD       # injected at deploy time via .env / --set
  - key: APP_VERSION
    var: APP_VERSION          # resolved from environment config at build time
  - key: FEATURE_X
    feature: FEATURE_X        # resolves to "true" or "false"
  - key: TZ
    value: Europe/Brussels    # literal value — only for non-sensitive data
```

## Deployment stages

Stages route to a provisioner or topology — never use a `type` field:

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: platform_iac     # references a workspace provisioner by name
      scope: all
      on_failure: stop

    - name: services
      provisioner: platform_compose
      scope: all
      depends_on: [infrastructure]
```

**WRONG** (will fail validation — `type` is not a valid field):
```yaml
    - name: infrastructure
      type: infrastructure    # ❌ does not exist
      type: terraform         # ❌ does not exist
```

Use `provisioner: <name>` (explicit provisioner) or `topology: <name>` (derive provisioner from topology). These are mutually exclusive.

## Module files (kind: module)

```yaml
kind: module
spec:
  source:
    repository: my-repo       # optional, defaults to workspace repo
  services:
    - name: app
      image: "myapp:1.0"
      environment: [...]
      mounts: [...]
  files:
    - source: "services/traefik/traefik.yaml"
      target: "traefik.yaml"
    - source: "services/traefik/conf.d/*"
      target: "conf.d/"       # trailing / = directory (required for globs)
```

- `spec.files` copies extra config files into the build output alongside `docker-compose.yml` / `values.yaml`.
- Glob patterns in `source` require `target` to end with `/`.
- Template substitution (`STRATA_*` variables) is applied to all copied text files.

## Namespace files (kind: namespace)

```yaml
kind: namespace
spec:
  modules:
    - name: traefik
      file: "@my-repo/modules/traefik.yaml"
```

## Deployment files (kind: deployment)

```yaml
kind: deployment
spec:
  workspace:
    name: my_platform
    file: "@my-repo/stack/ws-platform.yaml"
  environments:
    - "@my-repo/envs/env-prd.yaml"
  namespaces:
    - name: base
      file: "@my-repo/namespaces/base.yaml"
  stages:
    - name: infra
      provisioner: platform_iac
    - name: services
      provisioner: platform_compose
      depends_on: [infra]
```

## Tenant files (kind: tenant)

```yaml
kind: tenant
spec:
  tenant: acme
  configuration:
    tier: enterprise
    region: eu-west
```

Note: The `customer` kind was renamed to `tenant` in v0.11.0.

## Validation

After writing any file run:

```bash
strata validate <file.yaml>
```

Exit code 3 means validation failed — read the error list and fix the flagged fields before proceeding.

Use `strata schema get <kind>` to inspect the full field reference for any kind.
