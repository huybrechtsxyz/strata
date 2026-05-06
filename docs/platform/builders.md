# Builders Documentation

## Overview

Builders assemble and persist deployment artifacts from fully-loaded services. They follow a three-phase lifecycle (`before_build → build → after_build`) and accumulate errors and messages instead of raising exceptions.

**Available builders:**

| Class              | Module                       | Purpose                                                              |
| ------------------ | ---------------------------- | -------------------------------------------------------------------- |
| `BaseBuilder`      | `builders.base_builder`      | Abstract base — error/message accumulation + lifecycle hooks         |
| `PlatformBuilder`  | `builders.platform_builder`  | Assembles `platform.json` / `platform.yaml` from services            |
| `TerraformBuilder` | `builders.terraform_builder` | Generates `.tfvars.json` files for Terraform from the platform model |

---

## BaseBuilder

Abstract base class that all builders extend.

```python
from abc import ABC, abstractmethod
from pathlib import Path

class BaseBuilder(ABC):
    def __init__(self, verbose: bool = False) -> None: ...

    def has_errors(self) -> bool: ...
    def has_messages(self) -> bool: ...
    def get_errors(self) -> List[str]: ...
    def get_messages(self) -> List[str]: ...

    @abstractmethod
    def build(self, deployment_service, work_path, build_path, dry_run=False) -> bool: ...
    @abstractmethod
    def before_build(self, deployment_service, work_path, build_path) -> bool: ...
    @abstractmethod
    def after_build(self, deployment_service, work_path, build_path, dry_run=False) -> bool: ...
```

### Extending BaseBuilder

```python
from pathlib import Path
from xyz_platform.builders.base_builder import BaseBuilder

class MyBuilder(BaseBuilder):
    def before_build(self, deployment_service, work_path, build_path):
        if not deployment_service.is_validated():
            self._errors.append("Service not validated")
            return False
        return True

    def build(self, deployment_service, work_path, build_path, dry_run=False):
        # assemble artifacts
        return True

    def after_build(self, deployment_service, work_path, build_path, dry_run=False):
        # verify output files
        return True
```

---

## PlatformBuilder

Assembles a `PlatformArtifactModel` from a fully-loaded `DeploymentService` hierarchy and writes `platform.json` / `platform.yaml` to the deployment build path.

```python
from pathlib import Path
from xyz_platform.builders.platform_builder import PlatformBuilder

builder = PlatformBuilder(verbose=True, configuration_service=config_svc)

if not builder.before_build(deployment_service, work_path, build_path):
    print("Pre-build failed:", builder.get_errors())
elif not builder.build(deployment_service, work_path, build_path, dry_run=False):
    print("Build failed:", builder.get_errors())
elif not builder.after_build(deployment_service, work_path, build_path):
    print("Post-build failed:", builder.get_errors())
else:
    print("Built:", builder._last_platform_model.meta.name)
```

### Constructor

```python
PlatformBuilder(
    verbose: bool = False,
    configuration_service=None,   # optional — used for artifact_path computation
)
```

### Three-Phase Pipeline

#### `before_build(deployment_service, work_path, build_path)` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. Verifies `deployment_service.get_workspace_service()` returns a workspace — error if None

#### `build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. Calls `_build_platform(deployment_service)` to assemble the `PlatformArtifactModel` in memory
2. If `dry_run=True`: logs planned output file paths, returns `True` without writing to disk
3. If `dry_run=False`: persists `platform.json` and `platform.yaml` to the deployment build path

The assembled model is stored in `builder._last_platform_model` for downstream use (e.g. passing to `TerraformBuilder.build()`).

#### `after_build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. If `dry_run=True`: returns `True` immediately (skips file-existence checks)
2. Verifies `platform.json` and `platform.yaml` exist in the deployment build path

### Build Path

The deployment build path is `build_path / "{deployment-name}-{deployment-version}"` as returned by `deployment_service.get_build_path(build_path)`.

---

## TerraformBuilder

Generates Terraform `.tfvars.json` files from a `PlatformArtifactModel`. The builder tracks all variable, feature, and secret references encountered and writes requirement manifests alongside the generated artifacts.

> **Security note:** `TerraformBuilder` NEVER writes resolved values for variables, secrets, or features. It only documents which keys are required.

```python
from pathlib import Path
from xyz_platform.builders.terraform_builder import TerraformBuilder

builder = TerraformBuilder(verbose=True)

# dry_run with a pre-assembled model (avoids reading platform.json from disk)
if not builder.before_build(deployment_service, work_path, build_path, dry_run=True):
    print("Pre-build failed:", builder.get_errors())
elif not builder.build(
    deployment_service, work_path, build_path,
    dry_run=True,
    platform_model=platform_builder._last_platform_model,
):
    print("Build failed:", builder.get_errors())
```

### Constructor

```python
TerraformBuilder(verbose: bool = False)
```

### Requirement Tracking

The builder accumulates references to variables, features, and secrets during `_build_terraform_vars()`. These are tracked in:

| Attribute       | Type              | Contents                                            |
| --------------- | ----------------- | --------------------------------------------------- |
| `variable_refs` | `Dict[str, dict]` | Variables referenced by resources/providers/modules |
| `feature_refs`  | `Dict[str, dict]` | Feature flags referenced                            |
| `secret_refs`   | `Dict[str, dict]` | Secrets referenced                                  |

Each entry has: `key`, `description`, `required`, `suggested_env_var` (variables only), `used_by`.

The refs are **reset at the start of each `build()` call** to avoid stale data from previous runs.

### Three-Phase Pipeline

#### `before_build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. Verifies `deployment_service.is_validated()` — error if not validated
2. If `dry_run=False`: verifies `platform.json` exists in the deployment build path — error if missing (run `PlatformBuilder` first)

#### `build(deployment_service, work_path, build_path, dry_run=False, platform_model=None)` → `bool`

1. Resets requirement tracking dicts
2. If `platform_model` is supplied: uses it directly (no disk read)
3. If `platform_model=None` and `dry_run=False`: loads `platform.json` from the deployment build path
4. Calls `_build_terraform_vars()` to assemble all payloads
5. If `dry_run=True`: logs planned file paths and requirement counts, returns `True` without writing
6. If `dry_run=False`: writes all files to `{deployment_build_path}/terraform/`

#### `after_build(deployment_service, work_path, build_path, dry_run=False)` → `bool`

1. If `dry_run=True`: returns `True` immediately
2. Verifies the 7 base artifact files exist in `{deployment_build_path}/terraform/`

### Output Files

All files are written to `{deployment_build_path}/terraform/`:

| File                           | Contents                                            |
| ------------------------------ | --------------------------------------------------- |
| `workspace.auto.tfvars.json`   | Workspace identity, labels, metadata                |
| `providers.auto.tfvars.json`   | Provider configurations keyed by name               |
| `topologies.auto.tfvars.json`  | Topology definitions with components/volumes        |
| `modules.auto.tfvars.json`     | Module source paths and properties                  |
| `resx_{type}.auto.tfvars.json` | Resources grouped by `resource_type` (one per type) |
| `tf_required_variables.json`   | Required variable keys (no values)                  |
| `tf_required_features.json`    | Required feature flag keys (no values)              |
| `tf_required_secrets.json`     | Required secret keys (no values)                    |

---

## Typical Build Sequence

```python
from pathlib import Path
from xyz_platform.builders.platform_builder import PlatformBuilder
from xyz_platform.builders.terraform_builder import TerraformBuilder

work_path = Path("/workspace")
build_path = Path("/workspace/.xyz_platform/builds")

# Step 1: Build the platform model
platform_builder = PlatformBuilder(verbose=True, configuration_service=config_svc)
platform_builder.before_build(deployment_svc, work_path, build_path)
platform_builder.build(deployment_svc, work_path, build_path, dry_run=False)
platform_builder.after_build(deployment_svc, work_path, build_path)

# Step 2: Build Terraform artifacts using the in-memory model
tf_builder = TerraformBuilder(verbose=True)
tf_builder.before_build(deployment_svc, work_path, build_path, dry_run=False)
tf_builder.build(
    deployment_svc, work_path, build_path,
    dry_run=False,
    platform_model=platform_builder._last_platform_model,
)
tf_builder.after_build(deployment_svc, work_path, build_path)

# Check for errors
for err in platform_builder.get_errors() + tf_builder.get_errors():
    print("ERROR:", err)
```
