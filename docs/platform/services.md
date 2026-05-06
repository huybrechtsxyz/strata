# Services Documentation

## Overview

Services provide a consistent interface for loading, validating, and managing platform configuration resources (workspace, deployment, provider, etc.).

**Core Features:**

- Load YAML files or data dictionaries
- Validate against Pydantic models (requires validation before property access)
- Structured error handling with error codes
- Automatic service caching (prevents redundant YAML parsing)
- Lifecycle hooks: `on_init()`, `on_ready()`, `on_shutdown()`, `is_healthy()`

**Available Services:** ConfigurationService (singleton), WorkspaceService, DeploymentService, ProviderService, ResourceService, NamespaceService, FirewallService, EnvironmentService, ModuleService

## Basic Usage

```python
# Initialize and validate
service = DeploymentService(path="deployment.yaml")
is_valid, errors = service.validate()

if is_valid:
    kind = service.get_kind()
    services, success = service.load_related_services()
else:
    for error in service.get_structured_errors():
        print(f"{error.error_code}: {error.details}")
```

## Context Manager (Recommended)

```python
from xyz_platform.services import ServiceContext

with ServiceContext() as ctx:
    deployment = ctx.create(DeploymentService, path="deployment.yaml", validate=True)
    # Use services...
# Automatic cleanup: on_shutdown() called in reverse order
```

**Lifecycle:** `ctx.create()` → `on_init()` → `validate()` (if requested) → `on_ready()` (if valid) → exit: `on_shutdown()`

## Service Caching

```python
from xyz_platform.services import get_or_create_service, clear_cache, get_cache_stats

# Cached by class + path (returns same instance)
service = get_or_create_service(DeploymentService, path="deployment.yaml")

# Cache stats and management
stats = get_cache_stats()  # hits, misses, size
clear_cache()
```

**Use caching for:** CLI commands loading same files, related service references, performance optimization  
**Avoid for:** Fresh data needed, long-running processes without invalidation

## Lifecycle Hooks

```python
class CustomService(BaseService):
    def on_init(self):
        """After __init__: setup resources, connections"""

    def on_ready(self):
        """After validation: finalize config, warm caches"""

    def on_shutdown(self):
        """Before cleanup: close connections, temp files"""

    def is_healthy(self) -> bool:
        """Health check (default: returns self._validated)"""
        return self._validated and self._resource_available
```

**Example - Resource Management:**

```python
class DatabaseService(BaseService):
    def on_ready(self):
        self.connection = connect_to_database(self.get_database_config())

    def on_shutdown(self):
        if self.connection:
            self.connection.close()
```

## Best Practices

**Use Context Manager when:**

- ✅ CLI commands with services
- ✅ Guaranteed cleanup needed (connections, temp files)
- ✅ Multiple related services

**Use Caching when:**

- ✅ Same files loaded multiple times
- ✅ Performance optimization needed

**Override Lifecycle Hooks when:**

- ✅ Managing external resources
- ✅ Lazy initialization after validation
- ✅ Custom cleanup/health monitoring

**Combined Pattern:**

```python
def deploy_command(deployment_path: str):
    with ServiceContext() as ctx:
        deployment = get_or_create_service(DeploymentService, path=deployment_path)
        ctx.add(deployment)

        is_valid, errors = deployment.validate()
        if not is_valid:
            raise ValueError(f"Invalid: {errors}")

        workspace = get_or_create_service(WorkspaceService, path=deployment.get_workspace_path())
        ctx.add(workspace)

        execute_deployment(deployment, workspace)
```