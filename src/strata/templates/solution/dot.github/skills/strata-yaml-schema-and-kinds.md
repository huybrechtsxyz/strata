---
description: Strata YAML schema, valid kinds, fields, constraints, and anti-patterns
applyTo: "**/*.yaml"
confidence: high
---

# Strata YAML Schema & Kinds — AI Skill File

## Universal YAML Envelope (Every File)

Every strata YAML file MUST have this structure:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <kind>
meta:
  name: <name>
  annotations:
    description: "Human-readable description"
  labels:
    version: "1.0.0"
spec:
  # Kind-specific fields go here
```

**Rules:**
- `apiVersion: strata.huybrechts.xyz/v1` — always this value, never change
- `kind` — one of the 13 valid kinds listed below
- `meta.name` — **MUST match `^[a-z][a-z0-9_-]*$`** (lowercase, starts with letter, no spaces, no uppercase)
- `meta.annotations.description` — optional but recommended
- `meta.labels` — optional, for user-defined metadata
- `spec` — kind-specific content (structure varies per kind)

**Critical constraint:** Models use `extra="forbid"` — **any unknown field causes validation error 3**. Only use fields documented in the schema.

---

## The 13 Valid Kinds

### Root & Cross-Cutting

#### `configuration` — Hub for provisioners, providers, remotes, policies

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
meta:
  name: my-platform
  annotations:
    description: "Root configuration hub"
spec:
  provisioners:
    - name: terraform
      version: "1.5+"
  providers:
    - name: azure
      version: "3.0+"
  remotes:
    - name: my-infra
      type: git
      url: https://github.com/org/my-infra
  policies:
    - name: cost-limit
      budget: 5000 # USD/month
  stores:
    - name: default
      type: azure-keyvault
      location: "https://..."
```

**Agent rules:**
- One configuration per solution
- Reference it implicitly (environments, workspaces inherit from it)
- Define providers, provisioners, and remotes here FIRST

---

#### `environment` — Environment-specific overrides (dev, staging, prod)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: dev
spec:
  configuration: my-platform
  overrides:
    environment:
      AZ_SUBSCRIPTION_ID: "..."
      REGION: eastus
    secrets:
      - key: db-password
        source: azure-keyvault
        value: dev-db-secret
  variables:
    cluster_size: "small"
```

**Agent rules:**
- One per environment (dev, staging, prod, etc.)
- Overrides are merged, not replaced
- Secrets reference keys in the store, never plain values

---

### Compute & Infrastructure

#### `workspace` — Deployable unit combining resources + modules + namespaces

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
meta:
  name: platform-east
spec:
  resources:
    - file: "@my-infra/resources/aks-cluster.yaml"
  namespaces:
    - file: "@my-infra/namespaces/platform-services.yaml"
  modules:
    - file: "@my-infra/modules/api-gateway.yaml"
  provisioners:
    - name: platform_iac
```

**Agent rules:**
- Workspace groups related resources/modules
- References use `@remote/path.yaml` syntax
- Provisioners define HOW infrastructure is provisioned

---

#### `resource` — Infrastructure primitive (cluster, storage, network)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: resource
meta:
  name: aks-cluster
spec:
  provider: azure
  type: container-service
  properties:
    vm_size: Standard_D4s_v3
    node_count: 3
    kubernetes_version: "1.26"
```

**Agent rules:**
- One resource = one primitive (not a collection)
- Linked to a provider
- Properties are provider-specific

---

#### `namespace` — Logical grouping of modules (Kubernetes namespace concept)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: namespace
meta:
  name: platform-services
spec:
  modules:
    - file: "@my-infra/modules/ingress-controller.yaml"
    - file: "@my-infra/modules/metrics-server.yaml"
  isolation: network
```

**Agent rules:**
- Namespaces group related modules
- Isolation level (network, compute, storage) defines security boundaries

---

#### `module` — Application unit with services, files, environment

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: module
meta:
  name: api-gateway
spec:
  image: myregistry.azurecr.io/api-gateway:1.0.0
  replicas: 2
  services:
    - name: api
      port: 8080
      targetPort: 3000
  environment:
    - name: LOG_LEVEL
      value: info
    - name: DB_PASSWORD
      source: secret
      value: db-secret-key
  files:
    - source: "@config/templates/nginx.conf"
      destination: /etc/nginx/nginx.conf
```

**Agent rules:**
- Modules are deployed units (containers, processes, etc.)
- Never put secrets as plain values — use `source: secret` + `value: <key>`
- Files referenced with `@remote/path` syntax

---

### Deployment & Orchestration

#### `deployment` — Ties workspace + environments + stages into a manifest

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: platform-dev-deploy
spec:
  workspace: platform-east
  environments:
    - dev
  stages:
    - name: infrastructure
      provisioner: terraform
      scope: all
      on_failure: stop
    - name: configuration
      provisioner: ansible
      scope: all
      on_failure: stop
  options:
    dry_run: false
    auto_approve: false
```

**Agent rules:**
- Deployment orchestrates the FULL lifecycle
- Stages execute in order — each can fail independently
- `on_failure` controls whether subsequent stages run

---

### Network & Security

#### `provider` — Cloud provider configuration (Azure, AWS, GCP)

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: provider
meta:
  name: azure
spec:
  type: cloud
  authentication:
    method: managed-identity
    tenant_id: "..."
    subscription_id: "..."
  region: eastus
  features:
    - encryption-at-rest
    - network-policies
```

**Agent rules:**
- One provider per cloud account
- Authentication method varies by provider type
- Features enable/disable optional capabilities

---

#### `firewall` — Network firewall rules

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: firewall
meta:
  name: api-firewall
spec:
  rules:
    - name: allow-https
      priority: 100
      direction: inbound
      action: allow
      protocol: tcp
      destination_port: 443
      source_address: "*"
    - name: deny-all
      priority: 1000
      direction: inbound
      action: deny
      protocol: "*"
      source_address: "*"
```

**Agent rules:**
- Rules execute in priority order (lowest number first)
- Higher priority numbers = lower precedence
- Deny-all as catch-all at end is standard

---

#### `network` — Subnet definitions and routing

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: network
meta:
  name: platform-vnet
spec:
  address_space: "10.0.0.0/16"
  subnets:
    - name: api-subnet
      address_prefix: "10.0.1.0/24"
    - name: db-subnet
      address_prefix: "10.0.2.0/24"
  routes:
    - name: default
      destination: "0.0.0.0/0"
      next_hop_type: internet
```

**Agent rules:**
- Address spaces and prefixes use CIDR notation
- Subnets must not overlap within the address space

---

#### `dns` — DNS zones and records

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: dns
meta:
  name: platform-dns
spec:
  zone: platform.example.com
  records:
    - name: api
      type: A
      value: 203.0.113.42
    - name: www
      type: CNAME
      value: cdn.example.com
```

**Agent rules:**
- One zone per DNS definition
- Record types: A, AAAA, CNAME, MX, NS, TXT, SRV

---

### Multi-Tenancy

#### `tenant` — Isolation unit for multi-tenant deployments

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: customer-acme
spec:
  workspace: platform-east
  environment: prod
  isolation_level: network  # network | compute | storage
  quota:
    cpu: "100"
    memory: "200Gi"
    storage: "500Gi"
```

**Agent rules:**
- Tenants isolate workloads within a shared workspace
- Isolation levels define security boundaries
- Quotas prevent one tenant from consuming all resources

---

## Cross-File Reference Syntax

**Always use `@remote/path.yaml` for cross-repository references:**

```yaml
# In a workspace
resources:
  - file: "@my-infra/resources/aks.yaml"

# In a namespace
modules:
  - file: "@my-infra/modules/api-gateway.yaml"

# In a module
files:
  - source: "@config/templates/nginx.conf"
    destination: /etc/nginx/nginx.conf
```

**Relative paths (same repo) work too:**

```yaml
resources:
  - file: "../resources/aks.yaml"  # Relative to current file
```

---

## Secret Handling (Critical!)

**NEVER write plain secret values in YAML:**

```yaml
# ❌ WRONG
environment:
  - name: DB_PASSWORD
    value: "super-secret-123"

# ✅ CORRECT
environment:
  - name: DB_PASSWORD
    source: secret
    value: db-password-key  # Name of the key in the secret store
```

**SSH key pattern in Ansible provisioner:**

```yaml
provisioners:
  - name: configure
    provisioner: ansible
    configuration:
      ssh_private_key_secret: haven_ssh_key  # References a key in the store
```

---

## Validation Checklist Before Saving

1. **Envelope:** `apiVersion`, `kind`, `meta.name` present?
2. **Name format:** `meta.name` matches `^[a-z][a-z0-9_-]*$`?
3. **No unknown fields:** Only use fields from the schema?
4. **No plain secrets:** All sensitive values use `source: secret` reference?
5. **Cross-refs valid:** All `@remote/path.yaml` references exist?
6. **Provisioner names match:** Provisioners referenced in deployment exist in configuration?

---

## Common Anti-Patterns

| Anti-Pattern                                           | Problem                | Fix                                        |
| ------------------------------------------------------ | ---------------------- | ------------------------------------------ |
| `meta.name: My-Workspace`                              | Uppercase not allowed  | Use lowercase: `my-workspace`              |
| `spec: { unknown_field: value }`                       | Extra forbid violation | Remove the field; check schema             |
| `value: "plain-password"`                              | Secret in plain text   | Use `source: secret` + `value: key-name`   |
| `file: config/module.yaml`                             | Missing `@` prefix     | Use `file: "@repo/config/module.yaml"`     |
| `provisioner: my_provisioner` but not in configuration | Undefined provisioner  | Add to configuration's `spec.provisioners` |

---

## Agent Workflow

1. **Before writing:** Run `strata schema get <kind>` to see valid fields
2. **While writing:** Follow the envelope + kind-specific sections above
3. **Before saving:** Validate with `strata validate <file> --output json`
4. **On error:** Read `errors` array to see which fields failed
