---
name: strata-onboarding
description: 'Onboarding guide for AI agents working with strata YAML configuration files: the dependency chain, envelope rules, and the CLI commands to scaffold a new workspace. Start here on a new strata workspace.'
---

# Strata Onboarding

## What is strata?

Strata is a Python DevOps CLI that orchestrates infrastructure-as-code deployments across multiple provisioners (Terraform, Helm, Docker Compose, Ansible). Configuration is declarative YAML. The CLI validates, builds artifacts, and deploys.

---

## The Dependency Chain (Key Mental Model)

Files form a dependency tree. Create them in this order:

```
configuration → environments → workspaces → resources/namespaces/modules → deployments
```

| Kind            | Purpose                                                            | References                       |
| --------------- | ------------------------------------------------------------------- | ----------------------------------- |
| `configuration` | Hub: provisioners, providers, remotes, policies, integrations       | — (root)                            |
| `environment`   | Region/env-specific overrides, variables, secrets                   | configuration (implicit)            |
| `workspace`     | Groups resources + namespaces + modules into a deployable unit     | resources, namespaces, modules      |
| `resource`      | Infrastructure primitive (AKS cluster, storage account, etc.)      | provider                            |
| `namespace`     | Logical grouping of modules (e.g. "platform-services")             | modules                             |
| `module`        | Application unit with services, files, environment                  | —                                    |
| `deployment`    | Ties workspace + environments + stages into a deployable manifest  | workspace, environments             |
| `provider`      | Cloud provider config (Azure, AWS, GCP)                              | —                                    |
| `tenant`        | Multi-tenancy isolation unit                                         | —                                    |
| `firewall`      | Network firewall rules                                               | —                                    |
| `network`       | Subnet definitions                                                   | —                                    |
| `dns`           | DNS zones and records                                                | —                                    |

For the full schema of every kind, see the `strata-yaml-schema-and-kinds` skill. For secrets/variables/features specifically, see `strata-secret-resolution-patterns`.

---

## YAML Document Envelope (Required on Every File)

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

**Rules:**
- `apiVersion` is always `strata.huybrechts.xyz/v1` — never change it.
- `kind` must be one of the kinds listed above.
- `meta.name` must match `^[a-z0-9][a-z0-9_-]*$` — lowercase, starts with a letter or digit, letters/numbers/hyphens/underscores.
- Models use `extra="forbid"` — **any unknown field causes a validation error**. Only use fields that exist in the schema.

---

## Onboarding Command Sequence

```bash
# 1. Initialize a workspace
strata sln init --name my-platform

# 2. Discover available templates
strata new --list

# 3. Scaffold individual files
strata new my-platform-config --template configuration --output-file config/
strata new dev --template environment --output-file envs/
strata new my-platform --template workspace --output-file stack/
strata new my-platform-dev --template deployment --output-file deploy/

# 4. Validate files
strata validate -f config/my-platform-config.yaml --output json
strata validate -f deploy/my-platform-dev-deploy.yaml --explain --output json

# 5. Check overall workspace readiness
strata sln status --output json
strata guide show

# 6. Build artifacts (dry-run)
strata build run -f deploy/my-platform-dev-deploy.yaml --dry-run --output json
```

---

## Cross-File References

Use `@repo_name/relative/path.yaml` to reference files in another registered repository:

```yaml
file: "@my-infra/modules/traefik.yaml"
```

Plain relative paths resolve from the current file's location. The `@repo_name` is defined in the configuration's `spec.remotes` section (register repos with `strata repo add`).

---

## Deployment Stages

Stages route to a provisioner — **never use a `type` field**, and always declare which secrets a stage may access:

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: platform_iac
      scope: all
      on_failure: stop         # stop | rollback | continue
      secrets: [db_password]   # allowlist — see strata-secret-resolution-patterns
    - name: services
      provisioner: platform_compose
      scope: all
      secrets: ['*']
```

`provisioner` and `topology` are mutually exclusive. Use one or the other.

---

## Secret and Variable References

Never write secret values as plain strings. Every variable/secret/feature item has `key`, `store`, and `value`:

```yaml
spec:
  secrets:
    - key: db_password
      store: azure-keyvault    # constant | environment | github | infisical | vault | bitwarden | azure-keyvault
      value: prod-db-password  # meaning depends on `store` — see strata-secret-resolution-patterns
  variables:
    - key: app_version
      store: constant
      value: "1.2.0"
```

Reference a declared secret from a module's environment with `secret: <key-name>` (never a literal value):

```yaml
environment:
  - key: DB_PASSWORD
    secret: db_password
```

---

## Common Patterns

### Workspace with resources and modules

```yaml
kind: workspace
spec:
  provisioners:
    - name: platform_iac
      type: terraform
    - name: platform_compose
      type: compose
  resources:
    - name: aks_cluster
      file: "stack/res-aks.yaml"
  namespaces:
    - name: platform
      file: "stack/ns-platform.yaml"
```

### Deployment referencing workspace + environments

```yaml
kind: deployment
spec:
  workspace:
    name: my_platform
    file: "@my-repo/stack/ws-platform.yaml"
  environments:
    - "@my-repo/envs/env-prd.yaml"
  stages:
    - name: infra
      provisioner: platform_iac
      secrets: [db_password]
    - name: services
      provisioner: platform_compose
      secrets: ['*']
```

### Module with services

```yaml
kind: module
spec:
  services:
    - name: app
      image: "myapp:{{ APP_VERSION }}"
      environment:
        - key: DB_HOST
          value: db.internal
        - key: DB_PASSWORD
          secret: db_password
      mounts:
        - source: "./config"
          target: "/etc/app"
  files:
    - source: "services/app/config/*"
      target: "config/"
```

---

## Anti-Patterns to Avoid

| Wrong                              | Right                        | Why                                                        |
| ---------------------------------------- | --------------------------------- | ---------------------------------------------------------------- |
| `type: infrastructure` on stages       | `provisioner: platform_iac`      | `type` is not a valid field — fails `extra="forbid"`             |
| `type: terraform` on stages            | `provisioner: platform_iac`      | Same — use provisioner name                                       |
| Plain-text secrets in YAML              | `secret: KEY_NAME`                | Never commit secrets                                              |
| Missing `apiVersion`                     | Always include it                  | Required envelope field                                           |
| `kind: customer`                        | `kind: tenant`                     | Renamed in v0.11.0                                                |
| Spaces/uppercase in `meta.name`        | Use lowercase + hyphens/underscores | Must match `^[a-z0-9][a-z0-9_-]*$`                                |
| `${var}` syntax in templates             | `{{ var }}` (Jinja2)               | Template engine uses Jinja2                                       |
| Stage uses a secret without declaring it | Add key to `stage.secrets`        | A resolved value is silently dropped from stages that don't allowlist it |

---

## Validation and Fix Suggestions

```bash
# Validate a single file
strata validate -f <file.yaml> --output json

# Validate with plain-English explanation
strata validate -f <file.yaml> --explain --output json

# Exit code 3 = validation failed — read the errors array
```

Use `strata schema get <kind>` to inspect the full field reference for any kind.

---

## Reference Examples

The `config/` directory in the strata repository contains complete working examples for multiple cloud providers (Azure AKS, AWS EKS, GCP GKE, Hetzner Compose, Kamatera Swarm). Each includes a full dependency chain from configuration through deployment.
