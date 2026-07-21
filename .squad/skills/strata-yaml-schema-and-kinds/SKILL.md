---
name: "strata-yaml-schema-and-kinds"
description: "Kubernetes-style YAML schema, valid kinds, and field validation rules"
domain: "yaml-structure"
confidence: "high"
source: "ADR-0001, strata.instructions.md"
tools:
  - name: "strata schema get <kind>"
    description: "Retrieve detailed schema for a specific kind"
    when: "need to understand valid fields for a YAML kind before writing"
---

## Context

All strata configuration files use **Kubernetes-style YAML structure** with `apiVersion`, `kind`, `meta`, and `spec`. The schema is strict: unknown fields cause validation errors (Pydantic `extra="forbid"`). Agents writing YAML must know the valid kinds and their required/optional fields.

## Valid Kinds

| Kind | Purpose | Common Use |
|------|---------|-----------|
| **workspace** | Define a solution workspace and its configuration repos | Root config, defines what gets deployed |
| **configuration** | Static configuration (config files, manifests) | Application settings, YAML templates |
| **deployment** | Orchestration: stages, provisioners, topology | How to execute build/deploy workflow |
| **environment** | Environment-specific settings (dev/staging/prod) | Value overrides per environment |
| **namespace** | Logical grouping of resources (Kubernetes-style) | Organize resources within cluster |
| **module** | Reusable infrastructure code bundle | Terraform modules, Helm charts |
| **resource** | Individual infrastructure resource | VM, network, storage, security group |
| **provider** | Cloud or infrastructure provider config | AWS, Azure, GCP credentials, regions |
| **firewall** | Network firewall rules | Security, ingress/egress policies |
| **network** | Virtual network definition | VPC, subnets, peering |
| **dns** | DNS configuration | Domain, records, zones |
| **tenant** | Multi-tenant isolation boundaries | Customer separation, resource scoping |

## Universal YAML Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <kind>
meta:
  name: <name>                      # REQUIRED: lowercase, matching ^[a-z0-9][a-z0-9_-]*$
  annotations:
    description: "..."              # optional: human-readable description
  labels:
    version: "1.0.0"                # optional: version or other metadata
spec:
  # kind-specific fields here
```

## Field Validation Rules

### `apiVersion`
- **Required:** Always `strata.huybrechts.xyz/v1`
- **Why:** Tells strata CLI which schema version to use

### `kind`
- **Required:** Must match a valid kind (see table above)
- **Case:** Title case (e.g., `Deployment`, not `deployment`)

### `meta.name`
- **Required:** Lowercase, must match `^[a-z0-9][a-z0-9_-]*$`
- **Why:** Used as resource identifier, referenced in cross-repo paths
- **Invalid:** `MyDeployment`, `my deployment`, `my_deployment-v1.2.3` (too long/special chars)
- **Valid:** `my-deployment`, `deployment_v1`, `prod123`

### `meta.annotations`
- **Optional:** Free-form metadata
- **Common:** `description`, `owner`, `team`
- **Use case:** Document intent, tracking

### `meta.labels`
- **Optional:** Key-value pairs for organization
- **Convention:** `version`, `environment`, `component`, `tier`

### `spec`
- **Kind-specific:** Required fields vary by kind
- **Use `strata schema get <kind>` to inspect**
- **Extra forbid:** Unknown fields cause validation error

## Common Patterns

### 1. Cross-Repo References

Use `@repo_name/relative/path.yaml` syntax for files in other repositories:

```yaml
spec:
  source: "@haven/config/config.yaml"
  provisioners:
    - source:
        repository: haven
        source_path: terraform
```

**Why:** Enables modular configuration across multiple git repos.

### 2. Secret References

NEVER write secrets as plain values. Use `secret: <KEY_NAME>`:

```yaml
# ❌ WRONG — never do this
spec:
  secrets:
    db_password: "super-secret-123"

# ✅ CORRECT
spec:
  secrets:
    - key: db_password
      source: bitwarden
      value: <bitwarden-item-id>  # resolves at build time
```

### 3. Provisioner Definition

Stages use `provisioner: <name>`, never `type`:

```yaml
# ✅ CORRECT
stages:
  - name: infrastructure
    provisioner: platform_iac
    scope: all
    on_failure: stop

  - name: configure
    provisioner: ansible
    scope: all
```

### 4. Environment Overrides

Workspace, provider, and environment can override deployment fields:

```yaml
# In deployment YAML
spec:
  providers:
    - name: azure
      region: eastus

# In environment YAML — overrides the region
spec:
  provider_overrides:
    - provider_name: azure
      region: westeurope
```

## Validation Anti-Patterns

❌ **Unknown fields (causes validation error):**
```yaml
spec:
  provisioners:
    - name: infra
      provisioner: terraform
      type: iac  # ❌ invalid — use provisioner: not type:
```

❌ **Invalid meta.name:**
```yaml
meta:
  name: "MyDeployment"  # ❌ uppercase not allowed
  name: "my deployment"  # ❌ spaces not allowed
```

❌ **Hardcoded secrets:**
```yaml
spec:
  database:
    password: "secret123"  # ❌ never hardcode
```

❌ **Missing apiVersion:**
```yaml
kind: Deployment  # ❌ missing apiVersion
meta:
  name: my-deploy
```

## Schema Inspection Workflow

When writing YAML, follow this pattern:

1. **Check the kind:** `strata schema get Deployment`
2. **Review required fields:** Look at schema output
3. **Write YAML** following the structure
4. **Validate:** `strata validate <file.yaml>`
5. **If errors:** Check `errors` array in JSON output, cross-reference with schema

## Examples

### Minimal Workspace

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Workspace
meta:
  name: my-workspace
  annotations:
    description: "Production environment workspace"
spec:
  remotes:
    - name: config
      path: ../my-configuration
```

### Deployment with Stages

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Deployment
meta:
  name: deploy-prod
spec:
  workspace: my-workspace
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
    - name: configuration
      provisioner: ansible
      scope: all
```

### Environment with Overrides

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Environment
meta:
  name: production
spec:
  provider_overrides:
    - provider_name: azure
      region: westeurope
      sku: premium
```

## Agent Responsibilities

- **Always validate before commit:** `strata validate <file.yaml> --deep`
- **Use lowercase names:** `meta.name` must match `^[a-z0-9][a-z0-9_-]*$`
- **Never hardcode secrets:** Use `secret:` references
- **Use correct provisioner names:** `terraform` or `ansible`, not generic `type`
- **Check schema:** Use `strata schema get <kind>` when uncertain
- **Respect extra="forbid":** Only use documented fields
