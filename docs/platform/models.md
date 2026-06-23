# Models

Pydantic v2 models validating YAML configuration files with a Kubernetes-inspired schema (`apiVersion`, `kind`, `meta`, `spec`).

## YAML Document Structure

All YAML files follow this envelope:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: <kind>
meta:
  name: <name>           # PlatformName: ^[a-z][a-z0-9_-]*$
  annotations:
    description: <text>
  labels:
    version: "1.0.0"
spec:
  ...
```

## Constrained Types

| Type              | Pattern              | Used for                                       |
| ----------------- | -------------------- | ---------------------------------------------- |
| `PlatformName`    | `^[a-z][a-z0-9_-]*$` | All `meta.name` fields                         |
| `PlatformKind`    | enum                 | `kind` field — one of the values below         |
| `PlatformVersion` | enum                 | `apiVersion` — only `strata.huybrechts.xyz/v1` |

Never use plain `str` for `name` fields — always use `PlatformName` from `common_models`.

## Supported Kinds

| Kind                  | Model class               | Description                                                     |
| --------------------- | ------------------------- | --------------------------------------------------------------- |
| `configuration`       | `ConfigurationModel`      | Integration backends and store definitions                      |
| `tenant`              | `TenantModel`             | Tenant record (zones, environments, custom config)              |
| `workspace`           | `WorkspaceModel`          | Infrastructure topology (providers, resources, namespaces)      |
| `deployment`          | `DeploymentModel`         | Deployment configuration referencing a workspace + environment  |
| `deployment-manifest` | `DeploymentManifestModel` | Immutable snapshot written after deploy (audit trail, versions) |
| `environment`         | `EnvironmentModel`        | Environment-specific variable/secret/feature overrides          |
| `provider`            | `ProviderModel`           | Cloud provider connection parameters                            |
| `resource`            | `ResourceModel`           | Infrastructure resource definition (VM size, disk, network)     |
| `firewall`            | `FirewallModel`           | Firewall ruleset                                                |
| `module`              | `ModuleModel`             | Deployable application component (source, lifecycle hooks)      |
| `namespace`           | `NamespaceModel`          | Application deployment unit (groups modules)                    |
| `platform-artifact`   | `PlatformArtifactModel`   | Build output written to `.strata/build/`                        |
| `workspace-template`  | `PlatformTemplateModel`   | Scaffold for `strata sln init --from-template`                  |
| `solution`            | `SolutionModel`           | Solution registry (`solution.json`)                             |
| `remote`              | `RemoteModel`             | Named remote endpoint (artifact source/target)                  |
| `integration`         | `IntegrationModel`        | Integration backend credential config                           |

## Two-Phase Validation

| Phase              | Trigger                               | What it checks                                                                                            |
| ------------------ | ------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Phase 1 — Pydantic | Model instantiation                   | Field types, required fields, enum values, regex constraints, cross-field rules (`model_validator`)       |
| Phase 2 — Dynamic  | `_validate_dynamic(model, work_path)` | `@repo-name/path` references exist on disk, provider names resolve, integration credentials are available |

Phase 2 requires a real filesystem and an initialised workspace. It is never called inside model constructors — only from the service layer.

## Lifecycle Stages

The platform lifecycle runs in this order:

```
clean → check → build → plan → provision → initialize → configure → output → health → protect → destroy
```

Lifecycle hooks in YAML map to these stage names via the `{command}_{stage}` naming convention (e.g., `deploy_provision`, `deploy_configure`).

## Validation Rules

- **Paths:** Relative paths only. Absolute paths, drive letters, and UNC paths are rejected by field validators.
- **Names:** `PlatformName` enforces lowercase, alphanumeric, underscores, and hyphens — no spaces, no uppercase.
- **Cross-field:** Use `model_validator(mode="after")` for relationships between fields within one model.
- **Single-field:** Use `field_validator` with `@classmethod` for format/constraint checks on one field.
- **No filesystem calls in validators:** Models must load without a real filesystem. `Path.exists()` is forbidden inside validators — put it in Phase 2 (`_validate_dynamic`).

## Writing a New Model

```python
from pydantic import BaseModel, field_validator, model_validator
from strata.models.common_models import PlatformName, PlatformKind, PlatformVersion

class MySpec(BaseModel):
    name: PlatformName
    value: str

class MyModel(BaseModel):
    apiVersion: PlatformVersion
    kind: PlatformKind
    meta: MyMeta
    spec: MySpec

    @model_validator(mode="after")
    def check_cross_field(self) -> "MyModel":
        # cross-field rules here
        return self
```
