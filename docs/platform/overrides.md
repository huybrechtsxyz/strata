# Environment Overrides

## Overview

Environment overrides allow you to customize workspace resource configurations for specific deployment environments (development, staging, production, etc.) without duplicating the entire workspace definition. This enables a single workspace specification to be deployed in different environments with environment-specific variations.

### Critical Architecture Concepts

**1 Deployment = 1 Workspace + 1 Environment**

- **Workspace**: Defines the base infrastructure configuration (resources, providers, modules, etc.)
- **Environment**: Defines environment-specific overrides and configuration for that deployment
- **Deployment**: Represents a single instance of the workspace with environment overrides applied
- **Stages**: Pipeline metadata for deployment orchestration (approval workflows, ordering, rollback strategies)

**IMPORTANT:** Stages and environments have **NO relationship**. Stages are purely for pipeline control, not for configuration. Each deployment has exactly one environment that provides overrides to the workspace.

### Key Concepts

- **Workspace**: Defines the base infrastructure configuration (resources, providers, modules, etc.)
- **Environment**: Defines environment-specific overrides and settings for a deployment
- **Override**: A specific modification to workspace configuration for a particular environment
- **Precedence**: The order in which configuration values are applied
- **Stage**: Pipeline metadata (approval type, deployment order) - **not linked to environments**

## Why Use Overrides?

Environment overrides solve several common infrastructure challenges:

1. **DRY Principle**: Define infrastructure once in workspace, override only what changes per environment
2. **Scale Differences**: Production needs more replicas/larger VMs than development
3. **Configuration Variance**: Different API endpoints, datacenter locations, or feature flags per environment
4. **Cost Optimization**: Smaller resources in dev/staging, full scale in production
5. **Risk Management**: Enable advanced features only in production after testing

## Override Types

### 1. Resource Overrides

Modify workspace resource configurations for specific environments.

**Can Override:**

- `description` - Resource description
- `enabled` - Enable/disable resource
- `condition` - Conditional deployment logic
- `role` - Resource role assignment
- `count` - Number of instances
- `depends_on` - Resource dependencies
- `references` - Cross-resource references
- `firewalls` - Firewall rule assignments
- `configuration` - Resource-specific configuration (deep merge)
- `custom` - Custom metadata (deep merge)
- `labels` - Resource labels (merge)
- `tags` - Resource tags (replace)

**Example:**

```yaml
# environment-production.yaml
spec:
  overrides:
    resources:
      - resource: manager
        count: 3 # Override from 1 to 3 for HA
        configuration:
          vm_size: Standard_D4s_v3 # Larger VM
          disk_size_gb: 100
          monitoring:
            enabled: true
        labels:
          tier: production
        tags: ["critical", "ha-enabled"]

      - resource: worker
        count: 5 # Override from 2 to 5 for load
        configuration:
          vm_size: Standard_D8s_v3
          auto_scaling:
            enabled: true
            min_instances: 3
            max_instances: 10
```

### 2. Module Overrides

Modify module configurations within workspace resources.

**Can Override:**

- `slot_type` - Module deployment slot (main, canary, preview)
- `enabled` - Enable/disable module
- `configuration` - Module-specific configuration (deep merge)

**Identification:**
Modules are identified by: `resource` + `module` + `slot_type`

**Example:**

```yaml
# environment-production.yaml
spec:
  overrides:
    modules:
      - resource: manager
        module: traefik
        slot_type: main
        enabled: true
        configuration:
          replicas: 2
          resources:
            cpu: "2000m"
            memory: "2Gi"
          monitoring:
            enabled: true
            prometheus_endpoint: /metrics

      # Canary deployment in production only
      - resource: manager
        module: traefik
        slot_type: canary
        enabled: true
        configuration:
          replicas: 1
          traffic_percentage: 10
```

### 3. Provider Overrides

Modify provider configurations for environment-specific settings.

**Can Override:**

- `description` - Provider description
- Additional provider-specific configurations

**Example:**

```yaml
# environment-production.yaml
spec:
  overrides:
    providers:
      - provider: kamatera_europe
        description: Production datacenter in France
        configuration:
          datacenter: FR
          network_tier: premium
          backup_enabled: true
          monitoring_enabled: true
```

## Configuration Precedence

Overrides are applied in the following order (lowest to highest priority):

1. **Workspace Base Values** (lowest priority)
   - Defined in workspace YAML files
   - Default configuration for all environments

2. **Environment Properties**
   - Defined in `environment.spec.properties`
   - Apply to entire environment

3. **Environment-Specific Overrides** (highest priority)
   - Defined in `environment.spec.overrides`
   - Highest precedence - wins over all other values

### Merge Strategy

The override system uses different merge strategies depending on the field type:

| Field Type       | Merge Strategy                         | Example                                           |
| ---------------- | -------------------------------------- | ------------------------------------------------- |
| **Dictionaries** | Deep merge (override wins on conflict) | `configuration`, `custom`, `references`, `labels` |
| **Lists**        | Replace entirely                       | `depends_on`, `firewalls`, `tags`                 |
| **Scalars**      | Replace                                | `count`, `enabled`, `description`, `role`         |
| **None values**  | Ignored (workspace value preserved)    | Any override field set to `null`                  |

**Deep Merge Example:**

```yaml
# Workspace
configuration:
  vm_size: Standard_D2s_v3
  disk_size_gb: 50
  networking:
    vnet: default

# Environment Override
configuration:
  vm_size: Standard_D4s_v3  # Replaces workspace value
  disk_size_gb: 100         # Replaces workspace value
  # networking.vnet: default preserved from workspace
```

## Deployment Workflow

### 1. Load Services

```python
service = DeploymentService(data=deployment_data)
service.validate()

# Load workspace and environment services (single environment per deployment)
services, success = service.load_related_services(objects_path)
```

### 2. Validate Cross-References

```python
# Validate that overrides reference existing workspace entities
is_valid, errors = service.validate_related_services()
if not is_valid:
    print(f"Validation errors: {errors}")
```

### 3. Apply Overrides

```python
# Apply environment overrides to workspace models
# Note: No stage parameter - environments are deployment-level, not stage-specific
success, warnings = service.apply_environment_overrides()
```

### 4. Deploy

After overrides are applied, the workspace models contain the final merged configuration ready for deployment.

## Error Handling

### Critical Errors

Operations that fail the entire override application:

- Missing workspace service
- Invalid stage name specified
- Actual critical failures (not skipped entities)

### Warnings (Non-Critical)

Operations that log warnings but don't fail the application:

- Override references non-existent resource (skipped)
- Override references non-existent module (skipped)
- Override references non-existent provider (skipped)

**Rationale:** Environment files may contain overrides for resources that don't exist in all workspace configurations. This allows flexibility in environment definitions.

## Complete Example

### Workspace Definition

```yaml
# workspace-platform.yaml
apiVersion: platform.huybrechts.xyz/v1
kind: workspace
meta:
  name: platform_workspace
spec:
  providers:
    - name: kamatera_europe
      file: providers/kamatera.yaml

  resources:
    - name: manager
      role: manager
      file: resources/vm-manager.yaml
      count: 1 # Base: single manager
      modules:
        - name: traefik
          file: modules/traefik.yaml
          slot_type: main
      configuration:
        vm_size: Standard_D2s_v3
        disk_size_gb: 50

    - name: worker
      role: worker
      file: resources/vm-worker.yaml
      count: 2 # Base: two workers
      configuration:
        vm_size: Standard_D4s_v3
        disk_size_gb: 100
```

### Development Environment

```yaml
# environment-dev.yaml
apiVersion: platform.huybrechts.xyz/v1
kind: environment
meta:
  name: development
spec:
  variables:
    - key: ENVIRONMENT
      store: constant
      value: development
    - key: LOG_LEVEL
      store: constant
      value: debug

  # Minimal overrides for dev
  overrides:
    resources:
      - resource: manager
        count: 1 # Keep single manager
        labels:
          environment: dev

      - resource: worker
        count: 1 # Reduce to single worker for cost savings
        labels:
          environment: dev
```

### Production Environment

```yaml
# environment-prod.yaml
apiVersion: platform.huybrechts.xyz/v1
kind: environment
meta:
  name: production
spec:
  variables:
    - key: ENVIRONMENT
      store: constant
      value: production
    - key: LOG_LEVEL
      store: constant
      value: info

  # Production overrides: HA, scaling, monitoring
  overrides:
    resources:
      - resource: manager
        count: 3 # HA: 3 managers
        configuration:
          vm_size: Standard_D4s_v3 # Larger VMs
          disk_size_gb: 100
          monitoring:
            enabled: true
        labels:
          environment: production
          tier: critical
        tags: ["production", "ha-enabled", "monitored"]

      - resource: worker
        count: 5 # Scale to 5 workers
        configuration:
          vm_size: Standard_D8s_v3 # Larger VMs
          disk_size_gb: 200
          auto_scaling:
            enabled: true
            min_instances: 3
            max_instances: 10
        labels:
          environment: production
        tags: ["production", "auto-scaling"]

    modules:
      - resource: manager
        module: traefik
        slot_type: main
        configuration:
          replicas: 2 # HA load balancer
          resources:
            cpu: "2000m"
            memory: "2Gi"
          monitoring:
            enabled: true

      # Canary deployment only in production
      - resource: manager
        module: traefik
        slot_type: canary
        enabled: true
        configuration:
          replicas: 1
          traffic_percentage: 10

    providers:
      - provider: kamatera_europe
        configuration:
          datacenter: FR # Production in France
          network_tier: premium
          backup_enabled: true
```

### Deployment Configuration

```yaml
# deployment-platform.yaml
apiVersion: platform.huybrechts.xyz/v1
kind: deployment
meta:
  name: platform_deployment
  labels:
    version: "1.0.0"
spec:
  workspace:
    name: platform_workspace
    file: workspaces/workspace-platform.yaml

  environments:
    - environments/environment-prod.yaml

  stages:
    - name: production
      type: infrastructure
      provisioner: terraform
```

## Best Practices

### 1. Minimize Overrides

Only override what truly differs between environments. Keep workspace as the source of truth for common configuration.

**Good:**

```yaml
overrides:
  resources:
    - resource: manager
      count: 3 # Only what changes
```

**Avoid:**

```yaml
overrides:
  resources:
    - resource: manager
      count: 3
      role: manager # Unnecessary - same as workspace
      file: resources/vm-manager.yaml # Unnecessary
```

### 2. Use Descriptive Names

Make it clear what environment the override applies to:

- `environment-dev.yaml`
- `environment-staging.yaml`
- `environment-production.yaml`

### 3. Document Why Overrides Exist

Use comments to explain non-obvious overrides:

```yaml
overrides:
  resources:
    - resource: worker
      count: 10 # Peak season capacity (Nov-Jan)
      configuration:
        vm_size: Standard_D16s_v3 # Handles 2000 req/sec per instance
```

### 4. Test Override Application

Always validate that overrides apply correctly:

```python
# After applying overrides
workspace_service = deployment_service.get_workspace_service()
for resource in workspace_service.model.spec.resources:
    print(f"{resource.name}: count={resource.count}, vm_size={resource.configuration.get('vm_size')}")
```

### 5. Validate Cross-References

Ensure overrides reference existing entities:

```python
is_valid, errors = service.validate_related_services()
if not is_valid:
    for error in errors:
        if "override" in error.lower():
            print(f"Override issue: {error}")
```

### 6. Progressive Rollout

Use module slot types for progressive deployment:

```yaml
modules:
  - resource: api
    module: service
    slot_type: canary
    enabled: true
    configuration:
      version: "2.0.0"
      traffic_percentage: 5 # Start with 5% traffic
```

## Implementation Details

### Service Architecture

- **DeploymentService.load_related_services()**: Loads workspace and environment services
- **DeploymentService.validate_related_services()**: Validates override cross-references
- **DeploymentService.apply_environment_overrides()**: Applies overrides to workspace models

### Override Models

- **EnvironmentResourceOverrideModel**: Mirrors WorkspaceResourceModel fields
- **EnvironmentModuleOverrideModel**: Module-specific overrides
- **EnvironmentProviderOverrideModel**: Provider-specific overrides

### Environment Service Helpers

- `has_overrides()` - Check if environment has any overrides
- `get_resource_override(name)` - Get resource override by name
- `get_module_override(resource, module, slot)` - Get module override
- `get_provider_override(name)` - Get provider override
- `get_overridden_resource_names()` - List all overridden resource names
- `get_overridden_module_keys()` - List all overridden module keys
- `get_overridden_provider_names()` - List all overridden provider names

## Limitations

1. **In-Place Modification**: Overrides modify workspace models in-place. Original workspace values are not preserved after application.
2. **Single Environment per Deployment**: Currently, environments are merged if multiple files exist, but all stages share the same environment instance.
3. **No Conditional Overrides**: Overrides are all-or-nothing. No built-in support for "if condition X, override Y".
4. **List Replacement**: Lists (depends_on, firewalls, tags) are fully replaced, not merged.

## See Also

- [Environment Configuration](environment.md) - Environment file structure
- [Workspace Configuration](workspace.md) - Workspace file structure
- [Deployment Structure](deployment.md) - Deployment orchestration
- [Services](services.md) - Service layer architecture
