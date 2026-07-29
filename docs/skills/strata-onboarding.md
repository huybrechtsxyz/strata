---
description: Onboarding guide for AI agents working with strata YAML configuration files
applyTo: "**/*.yaml"
---

# Strata Onboarding — AI Skill File

## What is strata?

Strata is a Python DevOps CLI that orchestrates infrastructure-as-code deployments across multiple provisioners (Terraform, Helm, Docker Compose, Ansible). Configuration is declarative YAML. The CLI validates, builds artifacts, and deploys.

---

## The Dependency Chain (Key Mental Model)

Files form a dependency tree. Create them in this order:

```
configuration → environments → workspaces → resources/namespaces/modules → deployments
```

| Kind            | Purpose                                                           | References                     |
| --------------- | ----------------------------------------------------------------- | ------------------------------ |
| `configuration` | Hub: provisioners, providers, remotes, policies, stores           | — (root)                       |
| `environment`   | Region/env-specific overrides, variables, secrets                 | configuration (implicit)       |
| `workspace`     | Groups resources + namespaces + modules into a deployable unit    | resources, namespaces, modules |
| `resource`      | Infrastructure primitive (AKS cluster, storage account, etc.)     | provider                       |
| `namespace`     | Logical grouping of modules (e.g. "platform-services")            | modules                        |
| `module`        | Application unit with services, files, environment                | —                              |
| `deployment`    | Ties workspace + environments + stages into a deployable manifest | workspace, environments        |
| `provider`      | Cloud provider config (Azure, AWS, GCP)                           | —                              |
| `tenant`        | Multi-tenancy isolation unit                                      | —                              |
| `firewall`      | Network firewall rules                                            | —                              |
| `network`       | Subnet definitions                                                | —                              |
| `dns`           | DNS zones and records                                             | —                              |

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
- `meta.name` must match `^[a-z][a-z0-9_-]*$` — lowercase, starts with letter, letters/numbers/hyphens/underscores.
- Models use `extra="forbid"` — **any unknown field causes a validation error**. Only use fields that exist in the schema.

---

## Onboarding Command Sequence

```bash
# 1. Initialize a workspace
strata sln init my-platform
# or with a template:
strata sln init my-platform --template azure-aks

# 2. Discover available templates
strata new --list
strata sln init --list

# 3. Scaffold individual files
strata new my-platform --template configuration --output-file config/
strata new dev --template environment --output-file envs/
strata new my-platform --template workspace --output-file stack/
strata new my-platform-dev --template deployment --output-file deploy/

# 4. Validate files
strata validate run -f config/my-platform-config.yaml
strata validate run -f deploy/my-platform-dev-deploy.yaml --explain

# 5. Two ways to fast-track onboarding — pick one:
strata sln init --guided      # cold-start wizard: answers questions, scaffolds a connected workspace
strata console                # interactive REPL: guided next-steps, validate/scaffold/graph from one session

# 6. Build artifacts (dry-run)
strata build plan -f deploy/my-platform-dev-deploy.yaml
```

---

## Cross-File References

Use `@repo_name/relative/path.yaml` to reference files in another registered repository:

```yaml
file: "@my-infra/modules/traefik.yaml"
```

Plain relative paths resolve from the workspace root. The `@repo_name` is defined in the configuration's `spec.remotes` section.

---

## Deployment Stages

Stages route to a provisioner — **never use a `type` field**:

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: platform_iac
      scope: all
      on_failure: stop
    - name: services
      provisioner: platform_compose
      scope: all
      depends_on: [infrastructure]
```

`provisioner` and `topology` are mutually exclusive. Use one or the other.

---

## Secret and Variable References

Never write secret values as plain strings. Use typed refs:

```yaml
environment:
  - key: DB_PASSWORD
    secret: DB_PASSWORD       # injected at deploy time
  - key: APP_VERSION
    var: APP_VERSION          # resolved from environment config at build time
  - key: FEATURE_X
    feature: FEATURE_X        # resolves to "true" or "false"
  - key: TZ
    value: Europe/Brussels    # literal value — only for non-sensitive data
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
    - name: services
      provisioner: platform_compose
      depends_on: [infra]
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
          var: DB_HOST
        - key: DB_PASSWORD
          secret: DB_PASSWORD
      mounts:
        - source: "./config"
          target: "/etc/app"
  files:
    - source: "services/app/config/*"
      target: "config/"
```

---

## Anti-Patterns to Avoid

| Wrong                            | Right                       | Why                                                  |
| -------------------------------- | --------------------------- | ---------------------------------------------------- |
| `type: infrastructure` on stages | `provisioner: platform_iac` | `type` is not a valid field — fails `extra="forbid"` |
| `type: terraform` on stages      | `provisioner: platform_iac` | Same — use provisioner name                          |
| Plain-text secrets in YAML       | `secret: KEY_NAME`          | Never commit secrets                                 |
| Missing `apiVersion`             | Always include it           | Required envelope field                              |
| `kind: customer`                 | `kind: tenant`              | Renamed in v0.11.0                                   |
| Spaces in `meta.name`            | Use underscores/hyphens     | Must match `^[a-z][a-z0-9_-]*$`                      |
| `${var}` syntax in templates     | `{{ var }}` (Jinja2)        | Template engine uses Jinja2                          |

---

## Validation and Fix Suggestions

```bash
# Validate a single file
strata validate run -f <file.yaml>

# Validate with plain-English explanation
strata validate run -f <file.yaml> --explain

# Validate all deployment manifests
strata validate run --pattern "deployments/**"

# Visualize workspace dependency graph
strata validate graph

# Exit code 3 = validation failed — read the suggestions
```

When validation fails, the CLI shows fix suggestions:
- `Unknown field 'type'. Did you mean: provisioner?`
- `Required field 'spec -> workspace -> name' is missing`

Use `strata schema get <kind>` to inspect the full field reference for any kind.

---

## Guided Onboarding

**Cold-start wizard** — asks a few questions and scaffolds a complete connected workspace:

```bash
strata sln init --guided
```

**Dependency scaffolding** — after creating a file, automatically scaffold missing referenced files:

```bash
strata new my-platform --template deployment --scaffold-deps
```

**Workspace readiness checklist** — shows the 8-phase onboarding progress and next action:

```bash
strata guide
strata guide -f deploy/my-platform.yaml   # file-mode analysis
```

---

## Console REPL (Interactive Onboarding)

```bash
strata console
```

Commands inside the REPL:

| Command             | What it does                            |
| ------------------- | ---------------------------------------- |
| `status`            | 8-phase workspace readiness checklist   |
| `next`              | Show the next step to take              |
| `do`                | Execute the suggested next-step command |
| `check <file>`      | Validate a specific file                |
| `new <kind> [name]` | Scaffold a new YAML file                |
| `validate [file]`   | Run validation                          |
| `graph`             | Show dependency graph                   |
| `templates`         | List available templates                |
| `tools`             | Check external tool availability        |
| `open <file>`       | Open file in editor                     |
| `reload`            | Re-evaluate workspace state             |
| `help`              | Show all commands                       |

---

## Reference Examples

The `config/` directory in the strata repository contains complete working examples for multiple cloud providers (Azure AKS, AWS EKS, GCP GKE, Hetzner Compose, Kamatera Swarm). Each includes a full dependency chain from configuration through deployment.
