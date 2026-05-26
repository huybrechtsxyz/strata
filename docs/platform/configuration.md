# Configuration Service

The `ConfigurationService` loads, validates, and merges YAML configuration files sourced from the active profile's `configfile` references. It exposes the merged result as a `ConfigurationModel` to controllers and services.

## Load Order

Files are merged in order — later entries override earlier ones for the same key:

```
1. Platform defaults   src/STRATA_platform/data/configuration.yaml
2. Numeric-prefixed config files from active profile (00-*.yaml → 99-*.yaml)
3. Deployment-level overrides (spec.overrides in deployment.yaml)
```

Numeric prefixes control merge order:

```
00-defaults.yaml
10-providers.yaml
20-topologies.yaml
30-validation.yaml
99-overrides.yaml
```

## Repo Map

Before any `@repo-name/path` reference can be resolved, `ConfigurationService.get_repo_map()` must be called to build a mapping from repo name to local path. This map is passed to `utils/system.py::resolve_path()` whenever a cross-repo reference appears.

```python
from strata.services.configuration_service import ConfigurationService

config_svc = ConfigurationService.load(work_path)
repo_map = config_svc.get_repo_map()
```

## Two-Phase Validation

| Phase              | What it checks                                                                                     |
| ------------------ | -------------------------------------------------------------------------------------------------- |
| Phase 1 (Pydantic) | YAML structure, required fields, type constraints, enum values                                     |
| Phase 2 (dynamic)  | Cross-repo `@` references exist on disk, provider names resolve, integration credentials available |

Phase 2 is triggered via `_validate_dynamic(model, work_path)` and requires a real filesystem and an active profile with resolved refs.

## Caching

Services are singletons per path — `BaseService.load(path)` returns a cached instance if the same path has been loaded before. Call `service_cache.clear()` in tests to reset state between test cases.

## Key Methods

| Method                                 | Description                                                    |
| -------------------------------------- | -------------------------------------------------------------- |
| `ConfigurationService.load(work_path)` | Load and cache the service for the given workspace             |
| `config_svc.get_repo_map()`            | Return `{name: Path}` mapping for all registered repos         |
| `config_svc.model`                     | The validated `ConfigurationModel` (None if not yet validated) |
| `config_svc.has_errors()`              | True if Phase 1 or Phase 2 validation found errors             |
| `config_svc.get_errors()`              | List of accumulated error strings                              |

## ConfigurationModel Fields

Defined in `src/STRATA_platform/models/`. Key fields:

| Field               | Type              | Description                                            |
| ------------------- | ----------------- | ------------------------------------------------------ |
| `apiVersion`        | `PlatformVersion` | Always `strata.huybrechts.xyz/v1`                    |
| `kind`              | `PlatformKind`    | Always `configuration`                                 |
| `meta.name`         | `PlatformName`    | Workspace-scoped identifier                            |
| `spec.integrations` | list              | Declared integration backends (bitwarden, vault, etc.) |
| `spec.stores`       | list              | Variable/secret/feature store definitions              |

## Example

```python
from pathlib import Path
from strata.services.configuration_service import ConfigurationService

work_path = Path("/workspace")
config_svc = ConfigurationService.load(work_path)

if config_svc.has_errors():
    for err in config_svc.get_errors():
        print(err)
else:
    repo_map = config_svc.get_repo_map()
    print(repo_map)
```
