# Validators Documentation

## Overview

The validators layer provides structural validation of individual platform YAML files. A validator resolves the file's `kind`, delegates loading to the matching service, and runs optional lifecycle hooks before and after validation.

**Available validators:**

| Class               | Module                          | Purpose                                                 |
| ------------------- | ------------------------------- | ------------------------------------------------------- |
| `BaseValidator`     | `validators.base_validator`     | Abstract base — error/message accumulation + hook hooks |
| `PlatformValidator` | `validators.strata_validator` | Validates a single platform YAML file by kind           |

---

## BaseValidator

Abstract base class that all validators extend.

```python
from abc import ABC, abstractmethod
from pathlib import Path

class BaseValidator(ABC):
    def __init__(self) -> None: ...

    def has_errors(self) -> bool: ...
    def has_messages(self) -> bool: ...
    def get_errors(self) -> List[str]: ...
    def get_messages(self) -> List[str]: ...

    @abstractmethod
    def validate(self, work_path: Path) -> bool: ...
    @abstractmethod
    def before_validate(self, work_path: Path) -> bool: ...
    @abstractmethod
    def after_validate(self, work_path: Path) -> bool: ...
```

### Extending BaseValidator

```python
from pathlib import Path
from strata.validators.base_validator import BaseValidator

class MyValidator(BaseValidator):
    def before_validate(self, work_path: Path) -> bool:
        # pre-checks, lifecycle hooks
        return True

    def validate(self, work_path: Path) -> bool:
        # main validation logic
        return True

    def after_validate(self, work_path: Path) -> bool:
        # post-validation cleanup, lifecycle hooks
        return True
```

---

## PlatformValidator

Validates a single platform YAML file. The caller is responsible for calling the three phases in order.

```python
from pathlib import Path
from strata.validators.strata_validator import PlatformValidator

file_path = Path("path/to/workspace.yaml")
work_path = Path("/workspace/root")

validator = PlatformValidator(file_path)

if not validator.before_validate(work_path):
    print("Pre-validation failed:", validator.get_errors())
elif not validator.validate(work_path):
    print("Validation failed:", validator.get_errors())
elif not validator.after_validate(work_path):
    print("Post-validation failed:", validator.get_errors())
else:
    print("Valid!", validator.detected_kind)
```

### Constructor

```python
PlatformValidator(
    file_path: Path,
    configuration_service=None,  # optional ConfigurationService for Phase 2 dynamic validation
)
```

### Properties

| Property        | Type                     | Description                                       |
| --------------- | ------------------------ | ------------------------------------------------- |
| `detected_kind` | `Optional[PlatformKind]` | Resolved kind after `before_validate()` succeeds  |
| `service`       | `Optional[BaseService]`  | Loaded service instance after `validate()` passes |

### Three-Phase Pipeline

#### `before_validate(work_path)` → `bool`

1. Checks the file exists — error if not found
2. Parses YAML — error on malformed YAML
3. Validates the document is a mapping — error otherwise
4. Extracts and validates `kind` against `PlatformKind` enum — error for unknown kinds
5. Optionally parses `spec.lifecycle` for lifecycle hook execution
6. Executes the `validate_before` lifecycle phase (no-op when no hooks defined)

#### `validate(work_path)` → `bool`

1. Maps `detected_kind` to the appropriate service class
2. Phase 1: Loads the file via `service_class.load()` — Pydantic structural validation
3. Phase 2: If `configuration_service` was provided, runs `service.validate(configuration_model, work_path)` for cross-reference checks

#### `after_validate(work_path)` → `bool`

Executes the `validate_after` lifecycle phase (no-op when no hooks defined).

### Kind → Service Mapping

| `kind` value     | Service class        |
| ---------------- | -------------------- |
| `deployment`     | `DeploymentService`  |
| `environment`    | `EnvironmentService` |
| `firewall`       | `FirewallService`    |
| `module`         | `ModuleService`      |
| `namespace`      | `NamespaceService`   |
| `platform_model` | `PlatformService`    |
| `provider`       | `ProviderService`    |
| `resource`       | `ResourceService`    |
| `workspace`      | `WorkspaceService`   |

> **Note:** `configuration` kind is intentionally excluded — `ConfigurationService` is a path-less singleton incompatible with `BaseService.load(path)`.

### With Configuration Service (Phase 2)

```python
from strata.services.configuration_service import ConfigurationService
from strata.validators.strata_validator import PlatformValidator

config_svc = ConfigurationService.get_instance()
# ... load configuration ...

validator = PlatformValidator(
    file_path=Path("workspace.yaml"),
    configuration_service=config_svc,
)

validator.before_validate(work_path)
validator.validate(work_path)   # Phase 1 + Phase 2 cross-reference checks
validator.after_validate(work_path)
```

### Error Accumulation

Errors accumulate in the validator across all three phases. They do not reset between phases.

```python
validator = PlatformValidator(Path("bad.yaml"))
validator.before_validate(work_path)  # may add errors
validator.validate(work_path)         # may add more errors
print(validator.get_errors())         # all errors from all phases
```

> **Note:** When Phase 1 Pydantic validation fails, the validator returns `False` but the error list may be empty for services (like `WorkspaceService`) that override `get_validation_errors()` to return dynamic validation errors only. Always check the return value of each phase, not just `has_errors()`.
