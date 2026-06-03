---
description: Authoring rules for strata YAML configuration files
applyTo: "**/*.yaml"
---

# Strata YAML Authoring Rules

Apply these rules whenever you create or edit a strata YAML configuration file.

## Document envelope (required on every file)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <Kind>
meta:
  name: <name>
  annotations:
    description: "Human-readable description"
spec:
  ...
```

- `apiVersion` is always `strata.huybrechts.xyz/v1` — do not change it.
- `kind` must be one of: `Workspace`, `Configuration`, `Deployment`, `Namespace`, `Module`, `Environment`, `Provider`, `Resource`, `Firewall`.
- `meta.name` must match `^[a-z0-9][a-z0-9_-]*$` — lowercase, no spaces, letters/numbers/hyphens/underscores only.

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

## Module files (kind: Module)

```yaml
kind: Module
spec:
  source:
    repository: my-repo       # optional, defaults to workspace repo
  type: compose               # compose | helm | argocd | script
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

- `spec.type` is required for service deployment commands.
- `spec.files` copies extra config files into the build output alongside `docker-compose.yml` / `values.yaml`.
- Glob patterns in `source` require `target` to end with `/`.
- Template substitution (`STRATA_*` variables) is applied to all copied text files.

## Namespace files (kind: Namespace)

```yaml
kind: Namespace
spec:
  modules:
    - name: traefik
      file: "@my-repo/modules/traefik.yaml"
```

## Deployment files (kind: Deployment)

```yaml
kind: Deployment
spec:
  workspace:
    file: "@my-repo/workspace.yaml"
  namespaces:
    - name: base
      file: "@my-repo/namespaces/base.yaml"
  stages:
    - name: infra
      type: terraform
    - name: services
      type: compose
      depends_on: [infra]
```

## Validation

After writing any file run:

```bash
strata validate <file.yaml>
```

Exit code 3 means validation failed — read the error list and fix the flagged fields before proceeding.
