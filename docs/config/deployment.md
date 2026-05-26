# Deployment Configuration

Top-level orchestration files combining workspace definitions, environments, and configurations to create **actual infrastructure instances**. A deployment represents a concrete, deployable unit.

## Conceptual Model

| Layer           | Purpose          | Description                                          |
| --------------- | ---------------- | ---------------------------------------------------- |
| **Workspace**   | WHAT to build    | Infrastructure blueprint                             |
| **Environment** | HOW to customize | Environment-specific overrides                       |
| **Deployment**  | ACTUAL INSTANCE  | Combines workspace + environment(s) + configurations |

**Deployment = Workspace + Environment(s) + Configuration(s) + Deployment Overrides**

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: <deployment_name>      # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  properties: {}               # Deployment metadata
  custom: {}                   # Organizational metadata
  workspace:                   # Required: infrastructure blueprint
    name: <workspace_name>
    description: <description>
    source:
      type: <source_type>      # local, gitops, image, script
      repository: <repository>
      reference: <reference>
      source_path: <path>
  environments: []             # Environment configs (applied in order)
    - name: <environment_name>
      description: <description>
      source: {}
  configurations: []           # Additional config layers (optional)
    - name: <configuration_name>
      description: <description>
      source: {}
  features: {}                 # Deployment-specific feature flags
  variables: []                # Deployment variables (highest precedence)
  secrets: []                  # Deployment secrets (highest precedence)
```

## Properties & Custom

**Properties** - Deployment identification:

```yaml
properties:
  customer: acme-corp
  project: platform
  environment: production
```

**Custom** - Organizational metadata:

```yaml
custom:
  owner: "Platform Team"
  costcenter: "PROD-001"
  billing_code: "BC-123"
```

## Workspace Reference

Required reference to infrastructure blueprint:

```yaml
workspace:
  name: platform_workspace
  source:
    type: local # local, gitops, image, script
    repository: /
    reference: /
    source_path: config/workspaces/platform.yaml
```

## Environments

Environment configs applied in order (later overrides earlier):

```yaml
environments:
  - name: production
    source:
      type: local
      repository: /
      reference: /
      source_path: config/environments/production.yaml
  - name: regional_us_east # Overrides production
    source:
      type: local
      repository: /
      reference: /
      source_path: config/environments/us-east.yaml
```

**Multiple environments enable layered configuration composition.**

## Configurations

Additional configuration layers (optional):

```yaml
configurations:
  - name: customer_config
    source:
      type: local
      repository: /
      reference: /
      source_path: config/configurations/customer-a.yaml
```

_Use for: application-specific settings, customer configs, compliance requirements_

## Features, Variables & Secrets

**Features** - Deployment-specific flags (highest precedence):

```yaml
features:
  premium_features: true
  advanced_analytics: true
```

**Variables** - Deployment variables (highest precedence):

```yaml
variables:
  - key: DEPLOYMENT_ID
    source: constant
    value: prod-customer-001
```

**Secrets** - Deployment secrets (highest precedence):

```yaml
secrets:
  - key: CUSTOMER_API_KEY
    source: bitwarden
    value: customer-api-key-id
```

## Configuration Merge Order

Precedence from lowest to highest:

1. Workspace defaults (base)
2. Environment 1
3. Environment 2...N (in order)
4. Configuration 1
5. Configuration 2...N (in order)
6. **Deployment overrides** (highest precedence)

**Merge rules:** Later values override earlier by key (variables/secrets) or extend/override (properties/custom/features).

## Examples

**Simple:**

```yaml
meta:
  name: platform_prod
  labels:
    version: "1.0.0"
spec:
  properties:
    customer: acme-corp
    environment: production
  workspace:
    name: platform_workspace
    source:
      type: local
      repository: /
      reference: /
      source_path: config/workspaces/platform.yaml
  environments:
    - name: production_env
      source:
        type: local
        repository: /
        reference: /
        source_path: config/environments/production.yaml
```

**Multi-Layer:**

```yaml
meta:
  name: customer_deployment
  labels:
    version: "2.0.0"
spec:
  properties:
    customer: customer-a
    project: saas-platform
  custom:
    customer_id: "CUST-001"
    tier: "premium"
    sla: "99.99%"
  workspace:
    name: saas_workspace
    source:
      type: gitops
      repository: https://github.com/org/workspaces.git
      reference: v1.5.0
      source_path: workspaces/saas-platform.yaml
  environments:
    - name: base_production
      source:
        type: local
        repository: /
        reference: /
        source_path: config/environments/production.yaml
    - name: regional_us_east
      source:
        type: local
        repository: /
        reference: /
        source_path: config/environments/us-east.yaml
  configurations:
    - name: customer_a_config
      source:
        type: local
        repository: /
        reference: /
        source_path: config/configurations/customer-a.yaml
  features:
    premium_features: true
    advanced_analytics: true
  variables:
    - key: DEPLOYMENT_ID
      source: constant
      value: prod-customer-a-001
  secrets:
    - key: CUSTOMER_API_KEY
      source: bitwarden
      value: customer-a-api-key-id
```

**GitOps:**

```yaml
meta:
  name: gitops_deployment
spec:
  workspace:
    name: infrastructure
    source:
      type: gitops
      repository: https://github.com/org/infrastructure.git
      reference: v2.3.0
      source_path: workspaces/main.yaml
  environments:
    - name: production
      source:
        type: gitops
        repository: https://github.com/org/environments.git
        reference: main
        source_path: production/us-east-1.yaml
```

## Use Cases

**Multi-customer SaaS:**

```text
workspace: saas-platform.yaml (same for all)
deployments/
├── customer-a-deployment.yaml  # Premium tier
├── customer-b-deployment.yaml  # Standard tier
└── customer-c-deployment.yaml  # Enterprise tier
```

**Multi-region:**

```text
workspace: global-platform.yaml (same)
deployments/
├── us-east-deployment.yaml     # US East region
├── eu-west-deployment.yaml     # EU West region
└── ap-south-deployment.yaml    # Asia Pacific
```

**Blue-green:**

```yaml
# Blue (current)
blue-deployment.yaml:
  variables:
    - key: DEPLOYMENT_COLOR
      value: blue

# Green (new version)
green-deployment.yaml:
  variables:
    - key: DEPLOYMENT_COLOR
      value: green
    - key: APP_VERSION
      value: v2.0.0
```

**Staged rollout:**

```yaml
# Canary (1%)
canary-deployment.yaml:
  environments: [production.yaml, canary.yaml]
  features:
    traffic_percentage: 1

# Beta (20%)
beta-deployment.yaml:
  environments: [production.yaml, beta.yaml]
  features:
    traffic_percentage: 20

# Full (100%)
production-deployment.yaml:
  environments: [production.yaml]
  features:
    traffic_percentage: 100
```

## Deployment Workflow

1. Load deployment → Parse config
2. Resolve sources → Fetch workspace/environment/config files
3. Merge configurations → Apply merge order
4. Validate → Validate merged config
5. Generate artifacts → Create Terraform/manifests
6. Execute lifecycle → Run workspace phases
7. Provision infrastructure → Deploy infrastructure
8. Deploy applications → Deploy namespaces/modules
9. Verify → Run health checks
10. Register → Register deployment instance

## Source Types

**Local:** Files in current repository (`type: local`)  
**GitOps:** External Git repos (`type: gitops`)  
**Image:** Packaged in containers (`type: image`)  
**Script:** Generated by scripts (`type: script`)

## Best Practices

- **Naming:** Use pattern `<customer>_<environment>_<region>`
- **Version control:** Track deployment files
- **Immutable references:** Use specific versions for workspace/environment
- **Layered config:** Environments for reusable, configurations for one-offs
- **Secret management:** Use appropriate secret managers
- **Feature flags:** Gradual rollouts and A/B testing
- **Documentation:** Document purpose in annotations
- **Validation:** Validate before applying
- **Testing:** Test in lower environments first
- **Rollback plan:** Maintain previous configs

## Validation

Platform validates:

- Valid deployment name (lowercase, alphanumeric, underscores)
- Required workspace reference
- Valid source configurations
- Unique environment/configuration/variable/secret names
- Resolvable source paths

## Troubleshooting

**Validation failed:** Check workspace reference, verify source paths resolvable, ensure unique names  
**Source resolution failed:** Verify source type, check repository URL/path exists, confirm reference exists, validate source_path  
**Merge conflicts:** Review merge order/precedence, check conflicting keys, validate environment/configuration order  
**Feature flags not working:** Verify names match implementation, check merge order, deployment features have highest precedence  
**Instance not unique:** Ensure unique deployment name, check properties uniqueness, review deployment ID

## Summary

Deployments create **concrete infrastructure instances** by combining workspace (blueprint) + environment(s) (settings) + configuration(s) (overrides). Enables:

- Multi-layer configuration composition
- Flexible deployment strategies (blue-green, canary, staged)
- Consistent, repeatable provisioning across customers/regions/environments
- Single source of truth with appropriate customizations