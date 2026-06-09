# Environment Configuration

Environment-specific overrides and extensions for workspace deployments. **Workspaces define WHAT infrastructure to build**, **environments define HOW to customize it** for different deployment contexts (production, staging, development) without modifying workspace definitions.

## Purpose

- Consistent workspace replication across environments
- Environment-specific variable and secret values
- Feature flag management per environment
- Custom properties for environment metadata

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: <environment_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    environment: <environment> # production, staging, development
    version: "<version>"
spec:
  properties: {} # Environment-specific settings
  custom: {} # Organizational metadata
  overrides: # Override workspace resources, modules, and providers
    resources: [] # Resource-level overrides
    modules: [] # Module-level overrides (images, chart versions, enabled state)
    providers: [] # Provider-level overrides
    properties: {} # Override workspace properties
    includes: [] # Terraform file includes
  variables: [] # Override/extend workspace variables
  secrets: [] # Override/extend workspace secrets
  features: {} # Feature flags
```

## Properties & Custom

**Properties** - Environment-specific configuration:

```yaml
properties:
  debug_mode: false
  log_level: "INFO"
  backup_enabled: true
```

**Custom** - Organizational metadata:

```yaml
custom:
  costcenter: "PROD-INFRA-001"
  owner: "Platform Team"
  compliance: "PCI-DSS"
  backup_retention_days: 30
```

## Overrides

Environment overrides let you customize workspace resources, modules, and providers
without modifying their source definitions. All override fields are optional — only
specify what you need to change.

### Module Overrides

Override module settings per environment: enable/disable modules, pin container
image versions, pin Helm chart versions, or merge additional configuration.

```yaml
spec:
  overrides:
    modules:
      - module: xyz_backend           # Module meta.name (required)
        services:                     # Override service container images
          - name: server
            image: registry.omp.com/product/backend:2025.3.0
          - name: worker
            image: registry.omp.com/product/backend:2025.3.0

      - module: xyz_frontend
        chart_version: "26.1.0"       # Pin Helm chart version
        services:
          - name: app
            image: registry.omp.com/product/frontend:2025.3.0

      - module: xyz_monitoring
        enabled: false                # Disable in this environment
```

**Scoping:** When a module appears in multiple resources or slots, use optional
qualifiers to narrow the override:

```yaml
overrides:
  modules:
    - module: xyz_backend             # Applies to ALL instances
      services:
        - name: server
          image: registry.omp.com/product/backend:2025.3.0

    - module: xyz_backend             # Narrow to canary slot only
      slot_type: canary
      services:
        - name: server
          image: registry.omp.com/product/backend:2025.3.0-rc.3

    - module: xyz_backend             # Narrow to specific resource
      resource: kubernetes_cluster
      services:
        - name: server
          image: registry.omp.com/product/backend:2025.3.0
```

| Field           | Required | Description                                                              |
| --------------- | -------- | ------------------------------------------------------------------------ |
| `module`        | Yes      | Module `meta.name` to override                                           |
| `resource`      | No       | Narrow to module within this resource                                    |
| `namespace`     | No       | Narrow to module within this namespace                                   |
| `slot_type`     | No       | Narrow to specific slot (`main`, `staging`, `canary`, `sidecar`, `init`) |
| `enabled`       | No       | Enable or disable the module                                             |
| `chart_version` | No       | Override Helm chart version                                              |
| `services`      | No       | List of service image overrides (`name` + `image`)                       |
| `configuration` | No       | Arbitrary configuration merged into module config                        |

**Constraints:**
- `resource` and `namespace` are mutually exclusive
- Service names within a module override must be unique
- When multiple overrides match, the most specific one wins (resource/namespace > module-only)

### Resource Overrides

Override resource-level settings such as instance count, enabled state, or configuration:

```yaml
spec:
  overrides:
    resources:
      - resource: manager
        count: 3
        enabled: true
        configuration:
          instance_type: "Standard_D4s_v3"
```

### Provider Overrides

Override provider descriptions or configuration:

```yaml
spec:
  overrides:
    providers:
      - provider: kamatera_europe
        configuration:
          region: "eu-west-1"
```

## Variables

Environment-specific variables **override or extend** workspace variables:

```yaml
variables:
  - key: ENVIRONMENT # UPPER_SNAKE_CASE
    source: constant # constant, env, file, computed
    value: production
```

**Merging:** Same key overrides workspace, new keys extend workspace.

## Secrets

Environment-specific secrets **override or extend** workspace secrets:

```yaml
secrets:
  - key: DATABASE_PASSWORD
    source: bitwarden # bitwarden, vault, env, file
    value: prod-db-password-id
```

**Merging:** Same key overrides workspace, new keys extend workspace.

## Features

Feature flags for environment-specific capabilities:

```yaml
features:
  monitoring_enabled: true
  debug_logging: false
  auto_scaling: true
  canary_deployments: false
```

## Examples

**Production:**

```yaml
meta:
  name: production
  labels:
    environment: production
spec:
  properties:
    log_level: "WARN"
    backup_enabled: true
  custom:
    costcenter: "PROD-INFRA-001"
    owner: "Platform Team"
    sla: "99.9%"
  variables:
    - key: ENVIRONMENT
      source: constant
      value: production
    - key: REPLICA_COUNT
      source: constant
      value: 5
  secrets:
    - key: DATABASE_PASSWORD
      source: bitwarden
      value: prod-db-password-id
  features:
    auto_scaling: true
    monitoring: true
    debug_mode: false
```

**Staging:**

```yaml
meta:
  name: staging
  labels:
    environment: staging
spec:
  properties:
    log_level: "INFO"
  variables:
    - key: ENVIRONMENT
      source: constant
      value: staging
    - key: REPLICA_COUNT
      source: constant
      value: 3
  secrets:
    - key: DATABASE_PASSWORD
      source: bitwarden
      value: staging-db-password-id
  features:
    monitoring: true
    canary_deployments: true
```

**Development:**

```yaml
meta:
  name: development
  labels:
    environment: development
spec:
  properties:
    log_level: "DEBUG"
    backup_enabled: false
  variables:
    - key: ENVIRONMENT
      source: constant
      value: development
    - key: REPLICA_COUNT
      source: constant
      value: 1
  secrets:
    - key: DATABASE_PASSWORD
      source: constant
      value: dev_password_123
  features:
    debug_mode: true
    hot_reload: true
```

## Workspace & Environment Relationship

**Workspace (WHAT to build):**

```yaml
# workspace.yaml
spec:
  topology:
    - name: cluster
      components:
        - name: worker
          resource:
            count: ${REPLICA_COUNT} # Parameterized
  variables:
    - key: REPLICA_COUNT
      source: constant
      value: 1 # Default
```

**Environment (HOW to customize):**

```yaml
# production.yaml - Override REPLICA_COUNT to 5
spec:
  variables:
    - key: REPLICA_COUNT
      source: constant
      value: 5

# development.yaml - Keep REPLICA_COUNT at 1
spec:
  variables:
    - key: REPLICA_COUNT
      source: constant
      value: 1
```

## Configuration Merge Order

1. Workspace defaults (base)
2. Environment overrides (override/extend)
3. Runtime parameters (CLI args)

**Merge strategy:** Variables/secrets with same key override, new keys extend.

## Use Cases

**Multi-environment deployment:**

```text
config/
├── workspaces/platform.yaml        # Infrastructure (WHAT)
└── environments/
    ├── production.yaml             # Prod config (HOW)
    ├── staging.yaml                # Staging config (HOW)
    └── development.yaml            # Dev config (HOW)
```

**Regional deployments:**

```yaml
# us-east.yaml
spec:
  variables:
    - key: REGION
      source: constant
      value: us-east-1

# eu-west.yaml
spec:
  variables:
    - key: REGION
      source: constant
      value: eu-west-1
```

**Feature flags:**

```yaml
# production.yaml
spec:
  features:
    new_dashboard: false    # Not ready
    legacy_api: true        # Keep compatibility

# staging.yaml
spec:
  features:
    new_dashboard: true     # Test in staging
    beta_features: true     # Enable for testing
```

## CLI Integration

```bash
# Validate a deployment YAML file
strata validate repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Deploy using the active profile's environment refs
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml

# Dry-run (plan only — no changes applied)
strata deploy run -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run

# Tear down infrastructure
strata deploy destroy -f repos/xyz-infrastructure/deployments/xyz-deploy-prd.yaml --dry-run
```

## File Location

Environment files should be in:

```
config/environments/<environment_name>.yaml
```

Examples:

- `config/environments/production.yaml`
- `config/environments/staging.yaml`
- `config/environments/development.yaml`

## Best Practices

- **Naming:** Use deployment stage names (`production`, `staging`, `development`)
- **Consistent structure:** Same structure across environment files
- **Secret management:** Use appropriate secret managers per environment
- **Minimal overrides:** Only override what's necessary
- **Default values:** Set sensible defaults in workspace
- **Testing:** Test environment configs in lower environments first
- **Documentation:** Document overrides and purpose
- **Version control:** Track environment files (exclude sensitive data)

## Validation

Platform validates:

- Valid environment name (lowercase, alphanumeric, underscores)
- Unique variable keys within environment
- Unique secret keys within environment
- Valid variable/secret sources

## Troubleshooting

**Validation failed:** Check required fields, verify unique keys, ensure valid sources  
**Variable not overriding:** Verify key matches workspace variable exactly (case-sensitive), check environment file specified in CLI  
**Secret not found:** Ensure source configured correctly, verify secret ID/path exists, check secret manager authentication  
**Feature flag not working:** Verify feature name referenced correctly in code, check environment file loaded

## Environment vs Workspace

| Aspect          | Workspace                       | Environment                      |
| --------------- | ------------------------------- | -------------------------------- |
| **Purpose**     | Define infrastructure structure | Customize for deployment context |
| **Scope**       | What to build                   | How to configure                 |
| **Changes**     | Infrastructure changes          | Configuration changes            |
| **Reusability** | Single definition               | Multiple environments            |
| **Frequency**   | Changes infrequently            | Changes per deployment           |

## Summary

Environment configurations enable **consistent infrastructure replication** across deployment contexts by providing variable/secret overrides, feature flag management, and custom properties. This separation allows a single workspace to deploy to multiple environments with appropriate customizations.