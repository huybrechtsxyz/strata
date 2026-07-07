# Pluggable provisioner framework

- Status: accepted
- Date: 2026-07-05
- Issue: [#169](https://github.com/huybrechtsxyz/strata/issues/169)

## Context and Problem Statement

strata supports five provisioner types today (Terraform, Ansible, Helm, Compose, Script), but adding a new one requires:

1. Creating the deployer class (straightforward).
2. Adding a value to the `ProvisionerType` enum.
3. Updating a hardcoded if/elif dispatch chain in `_create_deployer()` — which is **copy-pasted across 9 command files** with minor argument variations.
4. Adding a matching `_execute_*_build()` method in the build command.

There is no way for a user to add a third-party provisioner (Pulumi, CDK, ArgoCD, Flux, etc.) without forking the codebase. This blocks the v1.0.0 extensibility goal.

The SBOM subsystem already solved an analogous problem: `CollectorPluginLoader` discovers `.strata/lockfile_parsers/*.py` files at runtime and auto-registers `LockfileParser` subclasses via `__init_subclass__`. The deployer layer should adopt the same pattern.

### Current Architecture Debt

| Problem | Impact |
|---------|--------|
| `_create_deployer()` duplicated in 9 files | Every new built-in provisioner requires 9 edits |
| `ScriptDeployer` unreachable | `ProvisionerType.SCRIPT` exists in enum + class exists, but no if/elif branch creates it |
| No `status` or `health` on `BaseDeployer` | Health checks are modelled on stages, not delegated to the deployer that understands the tool |
| No plugin discovery for deployers | Third-party provisioners impossible without forking |
| Build command has 6 sequential `_execute_*_build()` with identical boilerplate | Same copy-paste pattern as deployers |

## Decision Drivers

- **Extensibility**: users must be able to add provisioners without modifying strata source.
- **Consistency**: follow the existing plugin pattern established by SBOM collectors.
- **Backward compatibility**: existing YAML files with `provisioner: terraform` must continue to work unchanged.
- **Layer discipline**: respect the strict layered architecture (ADR-0003). The factory lives in `deployers/`, commands delegate to it.
- **Reduce duplication**: centralize `_create_deployer()` into a single factory.

## Considered Options

### Option A — Deployer Factory with auto-discovery (recommended)

A `DeployerFactory` class in `deployers/factory.py` that mirrors `IntegrationFactory`:

- Built-in deployers registered via a static map (lazy-import tuples).
- User plugins discovered from `.strata/provisioners/*.py` at workspace init time.
- Optional `provisioner.yaml` manifest for metadata (name, version, health command).
- `ProvisionerType` enum extended with a wildcard/custom mechanism.

### Option B — Entry-point based plugins

Use Python `entry_points` (setuptools/importlib.metadata) for plugin registration. Provisioners are installed as separate pip packages.

- Pro: Standard Python ecosystem pattern.
- Con: Requires package installation, not just dropping a `.py` file. Misaligned with strata's workspace-local philosophy and existing SBOM plugin pattern.

### Option C — Configuration-only (no code plugins)

Define provisioners entirely through YAML configuration — map lifecycle commands to shell commands:

```yaml
provisioners:
  - name: pulumi
    commands:
      setup: "pulumi login"
      plan: "pulumi preview"
      apply: "pulumi up --yes"
      destroy: "pulumi destroy --yes"
```

- Pro: Zero Python knowledge required.
- Con: Cannot handle complex logic (output parsing, state management, secret injection). Insufficient for real-world provisioners.

## Decision Outcome

Chosen: **Option A — Deployer Factory with auto-discovery**, because it follows the proven SBOM plugin pattern, eliminates the 9-file duplication, and allows both simple and complex provisioner plugins.

Option C can be supported *within* Option A as a built-in `GenericDeployer` that reads commands from YAML — this covers the simple case without sacrificing the power of Python plugins.

### Consequences

- Good: Adding a new built-in provisioner requires editing **one file** (`deployers/factory.py`) instead of nine.
- Good: Third-party provisioners are workspace-local `.py` files — no fork required.
- Good: `ScriptDeployer` becomes reachable immediately via the factory.
- Good: Consistent plugin model across SBOM collectors and provisioners.
- Bad: One-time migration cost to centralize the existing `_create_deployer()` logic.
- Bad: Plugin authors must understand the `BaseDeployer` ABC contract.

---

## Detailed Design

### 1. DeployerFactory (`deployers/factory.py`)

Central registry replacing all 9 `_create_deployer()` copies.

```
deployers/
├── __init__.py
├── base_deployer.py          # existing — unchanged
├── factory.py                # NEW — DeployerFactory
├── terraform_deployer.py     # existing — unchanged
├── ansible_deployer.py       # existing — unchanged
├── compose_deployer.py       # existing — unchanged
├── helm_deployer.py          # existing — unchanged
└── script_deployer.py        # existing — unchanged
```

```python
class DeployerFactory:
    """Central registry for deployer types — built-in and user plugins."""

    # Lazy-import map: type string → (module_path, class_name)
    _BUILTIN_MAP: ClassVar[Dict[str, Tuple[str, str]]] = {
        "terraform": ("strata.deployers.terraform_deployer", "TerraformDeployer"),
        "ansible":   ("strata.deployers.ansible_deployer",   "AnsibleDeployer"),
        "compose":   ("strata.deployers.compose_deployer",   "ComposeDeployer"),
        "helm":      ("strata.deployers.helm_deployer",      "HelmDeployer"),
        "script":    ("strata.deployers.script_deployer",    "ScriptDeployer"),
    }

    # Runtime registry populated at startup
    _registry: ClassVar[Dict[str, Type[BaseDeployer]]] = {}

    @classmethod
    def register(cls, name: str, deployer_class: Type[BaseDeployer]) -> None:
        """Register a deployer class under the given provisioner name."""

    @classmethod
    def create(
        cls,
        provisioner_type: str,
        stage: DeploymentStageModel,
        deployment_service: DeploymentService,
        configuration_service: ConfigurationService,
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        resolved_values: Optional[ResolvedValues] = None,
        solution_controller: Optional["SolutionController"] = None,
    ) -> BaseDeployer:
        """Create a deployer instance for the given provisioner type.

        Resolution order:
        1. _registry (runtime-registered, includes user plugins)
        2. _BUILTIN_MAP (lazy import)
        Raises PlatformError if type is unknown.
        """

    @classmethod
    def get_known_types(cls) -> List[str]:
        """Return all registered + built-in type names."""

    @classmethod
    def load_plugins(cls, work_path: Path) -> None:
        """Discover and register user plugins from .strata/provisioners/*.py."""

    @classmethod
    def reset(cls) -> None:
        """Clear runtime registry (test helper)."""
```

**Plugin loading** follows the SBOM `CollectorPluginLoader` pattern:

1. Scan `.strata/provisioners/*.py` (skip `_`-prefixed files).
2. `importlib.util.spec_from_file_location()` → `exec_module()`.
3. Find all `BaseDeployer` subclasses in the module.
4. Call `register(deployer.get_deployer_name(), deployer_class)` for each.
5. Log warnings for load failures; never raise.

**Called once** during workspace initialization — `SolutionController.__init__()` or `BaseCommand._initialize()` calls `DeployerFactory.load_plugins(work_path)`.

### 2. ProvisionerType Enum Extension

The `ProvisionerType` enum keeps its existing values for schema validation of built-in types. For user plugins, the workspace model accepts `str` directly:

```python
class ProvisionerType(str, Enum):
    TERRAFORM = "terraform"
    ANSIBLE = "ansible"
    SCRIPT = "script"
    COMPOSE = "compose"
    HELM = "helm"
```

The workspace model's `provisioner` field changes from `ProvisionerType` to `str` with a validator:

```python
class WorkspaceIacModel(PlatformBaseModel):
    provisioner: str  # validated: must be a known ProvisionerType or registered plugin

    @field_validator("provisioner")
    @classmethod
    def validate_provisioner(cls, v: str) -> str:
        """Accept built-in enum values as-is.

        Plugin names are validated at deployment time (not parse time)
        because plugins are loaded after YAML parsing.
        """
        # Accept known built-in values
        try:
            ProvisionerType(v)
        except ValueError:
            pass  # Unknown type — could be a plugin, validated at deploy time
        return v
```

This preserves backward compatibility: `provisioner: terraform` still works. New values like `provisioner: pulumi` pass parsing and are validated when the factory attempts to create the deployer.

### 3. Provisioner Manifest (Optional)

Users may place a `provisioner.yaml` alongside their plugin `.py` file in `.strata/provisioners/`:

```yaml
name: pulumi
version: "1.0.0"
description: "Pulumi IaC provisioner for strata"
supported_steps:
  - setup
  - plan
  - apply
  - destroy
  - output
health_check:
  command: "pulumi version"
  expected_exit_code: 0
requires:
  - pulumi  # binary name to check on PATH
```

**Schema model** (`models/provisioner_manifest_model.py`):

```python
class ProvisionerManifestModel(PlatformBaseModel):
    name: PlatformName
    version: str
    description: Optional[str] = None
    supported_steps: Optional[List[str]] = None
    health_check: Optional[ProvisionerHealthCheckModel] = None
    requires: Optional[List[str]] = None
```

The manifest is **optional** — if absent, the factory relies on the deployer class's `get_deployer_name()` and `get_supported_steps()` methods. If present, it provides metadata for `strata tools status` and the onboarding guide.

### 4. BaseDeployer Lifecycle Additions

Add two optional methods to `BaseDeployer` for the lifecycle hooks requested in #169:

```python
class BaseDeployer(ABC):
    # ... existing methods ...

    def status(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Query current infrastructure state.

        Returns:
            (success, status_data, messages)
        """
        return True, {}, ["Status not implemented for this provisioner"]

    def health(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        """Run health checks against deployed infrastructure.

        Returns:
            (success, health_data, messages)
        """
        return True, {}, ["Health check not implemented for this provisioner"]
```

These are **non-abstract with default implementations** — existing deployers continue to work without changes. Built-in deployers can override them incrementally (e.g., `TerraformDeployer.status()` could call `terraform show`).

The step constants are extended:

```python
STEP_STATUS = "status"
STEP_HEALTH = "health"
```

### 5. Deployer Context

Deployers already receive comprehensive context via constructor arguments. This design formalizes it:

| Context | Source | Available via |
|---------|--------|---------------|
| Stage metadata | `DeploymentStageModel` | `self.stage` |
| Resolved variables | `ResolvedValues.variables` | `self.resolved_values.variables` |
| Resolved secrets | `ResolvedValues.secrets` | `self.resolved_values.secrets` |
| Feature flags | `ResolvedValues.features` | `self.resolved_values.features` |
| Stage outputs (upstream) | `ResolvedValues.stage_outputs` | `self.resolved_values.stage_outputs` |
| Build artifacts path | `Path` | `self.build_path` |
| Workspace root | `Path` | `self.work_path` |
| IaC model (provisioner config) | `WorkspaceIacModel` | `self._resolve_iac_model()` |
| Solution controller | `SolutionController` | `self.solution_controller` |

**Change**: Make `resolved_values` a first-class constructor parameter on `BaseDeployer` instead of only on concrete deployers:

```python
class BaseDeployer(ABC):
    def __init__(
        self,
        stage: DeploymentStageModel,
        deployment_service: DeploymentService,
        configuration_service: ConfigurationService,
        build_path: Path,
        work_path: Path,
        verbose: bool = False,
        force: bool = False,
        solution_controller: Optional["SolutionController"] = None,
        resolved_values: Optional["ResolvedValues"] = None,   # NEW
    ):
```

### 6. Command Layer Refactoring

All 9 `_create_deployer()` copies are replaced with a single call:

```python
# Before (9 copies of ~80 lines each):
def _create_deployer(self, stage):
    # ... 80 lines of if/elif, lazy imports, error handling ...

# After (one line):
deployer = DeployerFactory.create(
    provisioner_type=resolved_type,
    stage=stage,
    deployment_service=self._deployment_service,
    configuration_service=self._configuration_service,
    build_path=self._build_path,
    work_path=self._work_path,
    verbose=self._is_verbose(),
    force=self._force,
    resolved_values=self._resolved_values,
    solution_controller=self._solution_controller,
)
```

The provisioner-type resolution logic (finding `_iac` model from stage.provisioner or stage.topology) moves into a shared helper — either on `BaseDeployer._resolve_iac_model()` (already partially exists) or a standalone function in `deployers/factory.py`.

### 7. Build Command Refactoring (Follow-up)

The 6 identical `_execute_*_build()` methods in `run_build_command.py` are a separate but related duplication. This is out of scope for #169 but could follow the same factory pattern via a `BuilderFactory`. Noted here for completeness.

### 8. Example Provisioners

Two example provisioners demonstrate the plugin API:

#### Pulumi Example (`.strata/provisioners/pulumi_provisioner.py`)

```python
"""Pulumi provisioner plugin for strata."""

from strata.deployers.base_deployer import (
    BaseDeployer, STEP_SETUP, STEP_PLAN, STEP_APPLY, STEP_DESTROY, STEP_OUTPUT
)

class PulumiDeployer(BaseDeployer):
    def get_deployer_name(self) -> str:
        return "pulumi"

    def get_supported_steps(self):
        return [STEP_SETUP, STEP_PLAN, STEP_APPLY, STEP_DESTROY, STEP_OUTPUT]

    def validate_environment(self):
        # Check pulumi binary on PATH
        ...

    def setup(self):
        # pulumi login + pulumi stack select
        ...

    def plan(self):
        # pulumi preview --json
        ...

    def apply(self):
        # pulumi up --yes --json
        ...

    def destroy(self):
        # pulumi destroy --yes
        ...
```

#### ArgoCD Example (`.strata/provisioners/argocd_provisioner.py`)

```python
"""ArgoCD GitOps provisioner plugin for strata."""

class ArgoCDDeployer(BaseDeployer):
    def get_deployer_name(self) -> str:
        return "argocd"

    def setup(self):
        # argocd login
        ...

    def apply(self):
        # argocd app sync <app-name> --wait
        ...

    def status(self):
        # argocd app get <app-name> -o json
        ...

    def health(self):
        # argocd app get <app-name> --health-status
        ...
```

---

## Implementation Plan

### Phase 1 — Factory & Centralization (core)

| Step | Files | Description |
|------|-------|-------------|
| 1.1 | `deployers/factory.py` | Create `DeployerFactory` with `_BUILTIN_MAP`, `register()`, `create()`, `get_known_types()`, `reset()` |
| 1.2 | `deployers/base_deployer.py` | Add `resolved_values` to constructor, add `status()` and `health()` with default impls, add `STEP_STATUS`/`STEP_HEALTH` constants |
| 1.3 | 5 concrete deployers | Update constructors to pass `resolved_values` to `super().__init__()` |
| 1.4 | 9 command files | Replace `_create_deployer()` with `DeployerFactory.create()` call |
| 1.5 | Tests | `tests/strata/deployers/test_deployer_factory.py` — registration, creation, unknown-type error, reset |

### Phase 2 — Plugin Discovery

| Step | Files | Description |
|------|-------|-------------|
| 2.1 | `deployers/factory.py` | Add `load_plugins(work_path)` — scan `.strata/provisioners/*.py`, import, register subclasses |
| 2.2 | `models/provisioner_manifest_model.py` | Create optional manifest schema |
| 2.3 | `models/common_models.py` | Change `WorkspaceIacModel.provisioner` from `ProvisionerType` to `str` with validator |
| 2.4 | `commands/base_command.py` or `SolutionController` | Call `DeployerFactory.load_plugins()` at startup |
| 2.5 | Tests | Plugin loading, discovery, manifest validation |

### Phase 3 — Examples & Documentation

| Step | Files | Description |
|------|-------|-------------|
| 3.1 | `docs/examples/provisioners/pulumi_provisioner.py` | Full Pulumi example |
| 3.2 | `docs/examples/provisioners/argocd_provisioner.py` | Full ArgoCD example |
| 3.3 | `docs/guides/building-a-provisioner-plugin.md` | User guide with lifecycle, context, testing, publishing |
| 3.4 | `docs/platform/provisioner-plugin-api.md` | API reference for `BaseDeployer` and `DeployerFactory` |

---

## File Impact Summary

| File | Change Type |
|------|-------------|
| `src/strata/deployers/factory.py` | **New** — DeployerFactory |
| `src/strata/deployers/base_deployer.py` | **Modify** — add `resolved_values`, `status()`, `health()` |
| `src/strata/deployers/terraform_deployer.py` | **Modify** — pass `resolved_values` to super |
| `src/strata/deployers/ansible_deployer.py` | **Modify** — pass `resolved_values` to super |
| `src/strata/deployers/compose_deployer.py` | **Modify** — pass `resolved_values` to super |
| `src/strata/deployers/helm_deployer.py` | **Modify** — pass `resolved_values` to super |
| `src/strata/deployers/script_deployer.py` | **Modify** — pass `resolved_values` to super |
| `src/strata/models/provisioner_manifest_model.py` | **New** — manifest schema |
| `src/strata/models/common_models.py` | **Modify** — `WorkspaceIacModel.provisioner` type |
| `src/strata/commands/deploy/run_deploy_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/deploy/destroy_deploy_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/deploy/health_deploy_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/deploy/status_deploy_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/deploy/plan_deploy_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/deploy/output_deploy_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/envs/status_env_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/envs/output_env_command.py` | **Modify** — replace `_create_deployer()` |
| `src/strata/commands/envs/drift_env_command.py` | **Modify** — replace `_create_deployer()` |
| `tests/strata/deployers/test_deployer_factory.py` | **New** |
| `docs/decisions/0023-pluggable-provisioner-framework.md` | **New** — this ADR |
| `docs/guides/building-a-provisioner-plugin.md` | **New** |
| `docs/platform/provisioner-plugin-api.md` | **New** |
| `docs/examples/provisioners/pulumi_provisioner.py` | **New** |
| `docs/examples/provisioners/argocd_provisioner.py` | **New** |

## Risks

| Risk | Mitigation |
|------|------------|
| Plugin `.py` files executing arbitrary code | Same risk as SBOM plugins — documented as "workspace-trust" model. Plugins only load from `.strata/` which is under workspace owner control |
| `provisioner` field change from enum to str breaks existing YAML | Validator accepts all `ProvisionerType` values unchanged; only behavior change is that unknown strings pass parsing (validated at deploy time) |
| Large refactoring across 9 command files | Each command's `_create_deployer()` is replaced with a 1-line factory call — mechanical, low-risk change that can be done per-file |
| Plugin API stability | `BaseDeployer` ABC is the contract. Mark as `@stable` in docs. New optional methods use default implementations to avoid breaking plugins |

## More Information

- Existing SBOM plugin pattern: [extending-sbom-plugins guide](../guides/extending-sbom-plugins.md)
- Layered architecture: [ADR-0003](0003-layered-architecture.md)
- Integration factory pattern: `src/strata/integrations/factory.py`
- BaseDeployer ABC: `src/strata/deployers/base_deployer.py`
