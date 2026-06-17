# Services Documentation

## Overview

Services provide a consistent interface for loading, validating, and managing platform configuration resources (workspace, deployment, provider, etc.).

**Core Features:**

- Load YAML files or data dictionaries
- Validate against Pydantic models (requires validation before property access)
- Structured error handling
- Automatic service caching (prevents redundant YAML parsing)
- Lifecycle hooks: `on_init()`, `on_ready()`, `on_shutdown()`, `is_healthy()`

**Available Services:** `ConfigurationService` (singleton), `WorkspaceService`, `DeploymentService`, `ProviderService`, `ResourceService`, `NamespaceService`, `FirewallService`, `EnvironmentService`, `ModuleService`, `CustomerService`

## Basic Usage

```python
from strata.services.deployment_service import DeploymentService

# Load with automatic caching (validates by default)
service = DeploymentService.load("deployment.yaml")

if service.is_validated():
    kind = service.get_kind()   # "deployment"
    name = service.get_name()   # value from meta.name
else:
    for error in service.get_validation_errors():
        print(error)
```

## `BaseService.load()` (Recommended)

```python
service = DeploymentService.load(path="deployment.yaml", validate=True)
```

`BaseService.load()` is the preferred constructor. It handles caching via `service_cache` — if the same class+path combination was loaded before, the cached instance is returned. Internally it calls `get_or_cache()` from `strata.utils.service_cache`.

To instantiate without caching (e.g., in tests), construct directly:

```python
service = DeploymentService(path="deployment.yaml")
is_valid, errors = service.validate()
```

## Service Caching

```python
from strata.utils.service_cache import clear_cache, get_cache_stats

# Cache stats and management
stats = get_cache_stats()  # dict with hits, misses, size
clear_cache()              # clear all cached services
```

Use `BaseService.load()` for cached loading. Cache is keyed on `(service_class, path)`.

**Use caching for:** CLI commands loading same files multiple times, cross-service references  
**Avoid for:** Tests (reset cache or construct directly); fresh data needed each time

## Lifecycle Hooks

Override these methods in custom services:

```python
class CustomService(BaseService):
    def on_init(self) -> None:
        """Called after __init__. Set up external resources."""

    def on_ready(self) -> None:
        """Called after successful validation. Finalize config."""

    def on_shutdown(self) -> None:
        """Called before cleanup. Close connections, temp files."""

    def is_healthy(self) -> bool:
        """Health check — default returns self._validated."""
        return self._validated and self._resource_available
```

## `BaseService` API Reference

| Method                                     | Returns             | Description                                  |
| ------------------------------------------ | ------------------- | -------------------------------------------- |
| `load(path, validate=True)`                | service             | Class method — load with caching             |
| `validate(configuration_model, work_path)` | `(bool, List[str])` | Run Pydantic + dynamic validation            |
| `is_validated()`                           | `bool`              | Whether validation has passed                |
| `get_kind()`                               | `Optional[str]`     | YAML `kind` field                            |
| `get_name()`                               | `Optional[str]`     | YAML `meta.name` field                       |
| `get_model()`                              | `ModelT`            | Parsed Pydantic model (requires validation)  |
| `get_label(key)`                           | `Optional[str]`     | Value from `meta.labels[key]`                |
| `get_version()`                            | `Optional[str]`     | Value from `meta.labels.version`             |
| `get_lifecycle_phase(name)`                | phase or `None`     | Lifecycle phase by name (or first if `None`) |
| `get_data()`                               | `Optional[dict]`    | Raw YAML data dict                           |
| `reload_data(path, data)`                  | `None`              | Reload from a new path or dict               |
| `get_validation_errors()`                  | `List[str]`         | List of error strings                        |
| `on_init()`                                | `None`              | Override: post-`__init__` hook               |
| `on_ready()`                               | `None`              | Override: post-validation hook               |
| `on_shutdown()`                            | `None`              | Override: cleanup hook                       |
| `is_healthy()`                             | `bool`              | Override: health check                       |

## ConfigurationService (Singleton)

`ConfigurationService` is a singleton that aggregates multiple YAML configuration files:

```python
from strata.services.configuration_service import ConfigurationService

# Add configuration files
service = ConfigurationService.get_instance()
service.add_configuration(Path("path/to/config.yaml"))

# Reset between tests
ConfigurationService.reset()
```

`ConfigurationService` accumulates errors across all added files. Use `get_validation_errors()` to inspect. Call `reset()` in test teardown to clear the singleton state.