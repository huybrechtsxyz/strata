# Provisioner Plugin API Reference

Complete API reference for `BaseDeployer` and `DeployerFactory`.

---

## `BaseDeployer`

**Module:** `strata.deployers.base_deployer`

Abstract base class for all provisioner deployers — built-in and user plugins.

```python
from strata.deployers.base_deployer import BaseDeployer
```

### Constructor

```python
def __init__(
    self,
    stage: DeploymentStageModel,
    deployment_service: DeploymentService,
    configuration_service: ConfigurationService,
    build_path: Path,
    work_path: Path,
    verbose: bool = False,
    force: bool = False,
    solution_controller: Optional[SolutionController] = None,
    resolved_values: Optional[ResolvedValues] = None,
)
```

Always call `super().__init__(...)` with all arguments from your subclass constructor.  Do not pass `**kwargs` — list all parameters explicitly.

### Step Name Constants

Import these from `strata.deployers.base_deployer` to avoid magic strings in `get_supported_steps()` and callers:

| Constant            | Value            | Step purpose                                         |
| ------------------- | ---------------- | ---------------------------------------------------- |
| `STEP_SETUP`        | `"setup"`        | Initialise the IaC tool (e.g. `terraform init`)      |
| `STEP_CHECK`        | `"check"`        | Validate configuration (e.g. `terraform validate`)   |
| `STEP_PLAN`         | `"plan"`         | Preview changes (e.g. `terraform plan`)              |
| `STEP_APPLY`        | `"apply"`        | Apply changes (e.g. `terraform apply`)               |
| `STEP_DESTROY`      | `"destroy"`      | Destroy resources (e.g. `terraform destroy`)         |
| `STEP_PLAN_DESTROY` | `"plan_destroy"` | Preview destroy                                      |
| `STEP_SHOW_PLAN`    | `"show_plan"`    | Decode last saved plan (e.g. `terraform show -json`) |
| `STEP_OUTPUT`       | `"output"`       | Retrieve infrastructure outputs                      |
| `STEP_STATUS`       | `"status"`       | Query live infrastructure state                      |
| `STEP_HEALTH`       | `"health"`       | Run health checks against deployed resources         |

---

### Abstract Methods

All of these must be implemented in every concrete subclass.

#### `get_deployer_name() → str`

Return the canonical provisioner type name.  This must match the `provisioner:` value in workspace YAML.

```python
def get_deployer_name(self) -> str:
    return "pulumi"
```

Must be unique.  Cannot override built-in names: `terraform`, `ansible`, `helm`, `compose`, `script`.

---

#### `get_supported_steps() → List[str]`

Return the ordered list of step names this deployer supports.  Use the `STEP_*` constants.

```python
def get_supported_steps(self) -> List[str]:
    return [STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY, STEP_DESTROY, STEP_OUTPUT]
```

---

#### `validate_workspace() → Tuple[bool, List[str]]`

Called before any steps run.  Verify that the build artifacts needed by this deployer exist (e.g. IaC source files copied to `build_path`).

```python
def validate_workspace(self) -> Tuple[bool, List[str]]:
    stage_dir = self.build_path / str(self.stage.provisioner)
    if not stage_dir.exists():
        return False, [f"Build directory not found: {stage_dir}. Run 'strata build run' first."]
    return True, []
```

---

#### `validate_environment() → Tuple[bool, List[str]]`

Called before any steps run.  Verify the external CLI tool is available on PATH and any required environment variables are set.

```python
def validate_environment(self) -> Tuple[bool, List[str]]:
    result = subprocess.run(["my-tool", "--version"], capture_output=True)
    if result.returncode != 0:
        return False, ["my-tool is not installed or not on PATH"]
    return True, []
```

---

#### `setup() → Tuple[bool, List[str]]`

Initialise the IaC tool for the stage (e.g. `terraform init`, `pulumi stack select`, `helm dependency build`).

---

#### `check() → Tuple[bool, List[str]]`

Validate configuration syntax and references without making any changes (e.g. `terraform validate`, `helm lint`).

---

#### `plan() → Tuple[bool, List[str]]`

Preview infrastructure changes without applying them (e.g. `terraform plan`, `pulumi preview`).

---

#### `apply() → Tuple[bool, List[str]]`

Apply infrastructure changes (e.g. `terraform apply`, `pulumi up`, `helm upgrade --install`).

Append a success confirmation message when `ok=True`:

```python
if ok:
    msgs.append(f"✓ Stage '{self.stage.name}' applied successfully.")
return ok, msgs
```

---

#### `destroy() → Tuple[bool, List[str]]`

Destroy all infrastructure resources managed by this stage.  Guard with `self.force` if destruction is irreversible without explicit confirmation.

---

#### `plan_destroy() → Tuple[bool, List[str]]`

Preview what `destroy()` would remove without making any changes.

---

#### `show_plan() → Tuple[bool, Dict[str, Any], List[str]]`

Return structured plan data decoded from the last saved plan file.  Return `(True, {}, [message])` for tools that do not produce binary plan files.

---

#### `output() → Tuple[bool, Dict[str, Any], List[str]]`

Return the infrastructure outputs produced by this stage as a flat key-value dict.  These are stored in the build cache and displayed by `strata deploy output`.

```python
def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
    ok, raw, msgs = self._run_and_parse_json(["my-tool", "output", "--json"])
    if not ok:
        return False, {}, msgs
    return True, raw, msgs
```

---

### Non-Abstract Methods (override as needed)

#### `status() → Tuple[bool, Dict[str, Any], List[str]]`

Query current live infrastructure state.  Default returns an empty dict and an "not implemented" message.

Override to support `strata deploy status` and `strata env status`:

```python
def status(self) -> Tuple[bool, Dict[str, Any], List[str]]:
    ok, data, msgs = self._get_live_state()
    return ok, data, msgs
```

---

#### `health() → Tuple[bool, Dict[str, Any], List[str]]`

Run health checks against deployed infrastructure.  Default returns an empty dict and an "not implemented" message.

Override to support `strata deploy health`:

```python
def health(self) -> Tuple[bool, Dict[str, Any], List[str]]:
    ok, data, msgs = self._wait_healthy(timeout=300)
    return ok, data, msgs
```

---

#### `collect_outputs() → Tuple[bool, Dict[str, Any], Dict[str, Any], List[str]]`

Collect outputs after a successful `apply()`.  Returns `(success, non_sensitive, sensitive, messages)`.

Non-sensitive outputs are injected as `TF_VAR_<key>` (or verbatim) env vars into subsequent stage subprocesses.  Sensitive outputs (second dict) are never injected into subprocess environments.

Default: `(True, {}, {}, [])` — no outputs collected.

Override when your tool produces named outputs that should be passed to later stages:

```python
def collect_outputs(self):
    ok, outputs, msgs = self.output()
    if not ok:
        return False, {}, {}, msgs
    sensitive = {k: v for k, v in outputs.items() if k.startswith("_")}
    normal = {k: v for k, v in outputs.items() if not k.startswith("_")}
    return True, normal, sensitive, msgs
```

---

#### `describe_plan() → List[str]`

Return human-readable lines describing what this deployer would execute.  Called in dry-run mode after validation.  Default: `[]`.

---

#### `save_plan_json() → Tuple[bool, Optional[Path], List[str]]`

Persist the plan as a JSON file alongside the binary plan.  Default: no-op.  Override in deployers that produce structured plan output.

---

### Protected Helpers

#### `_get_timeout(step: str, default: int) → int`

Return the per-step timeout from `stage.timeouts`, falling back to `default` if not configured.

```python
timeout = self._get_timeout(STEP_APPLY, 1800)
```

---

#### `_resolve_iac_model() → Optional[WorkspaceIacModel]`

Resolve the `WorkspaceIacModel` for the provisioner referenced by this stage.

```python
iac = self._resolve_iac_model()
if iac:
    source_path = iac.source.source_path
```

---

## `DeployerFactory`

**Module:** `strata.deployers.factory`

Central registry for deployer types.  Built-in deployers use lazy imports; user plugins are discovered from `.strata/provisioners/*.py`.

```python
from strata.deployers.factory import DeployerFactory
```

---

### `DeployerFactory.create(provisioner_type, *, stage, deployment_service, configuration_service, build_path, work_path, verbose, force, resolved_values, solution_controller) → BaseDeployer`

Create a deployer instance for the given provisioner type.

```python
deployer = DeployerFactory.create(
    "pulumi",
    stage=stage,
    deployment_service=deployment_service,
    configuration_service=configuration_service,
    build_path=build_path,
    work_path=work_path,
    verbose=verbose,
    force=force,
    resolved_values=resolved_values,
    solution_controller=solution_controller,
)
```

Raises `ValueError` if `provisioner_type` is not known.  Always call within a `try/except ValueError`.

---

### `DeployerFactory.resolve_type(stage, deployment_service) → Tuple[Optional[str], List[str]]`

Resolve the provisioner type string for a stage.  Handles both `stage.provisioner` (direct name lookup) and `stage.topology` (topology → provisioner derivation).

```python
resolved_type, errors = DeployerFactory.resolve_type(stage, deployment_service)
if errors:
    self._errors.extend(errors)
if resolved_type is None:
    return None
```

Returns `(None, errors)` when resolution fails.  Never raises.

---

### `DeployerFactory.register(name, deployer_class)`

Register a deployer class under a provisioner type name.  Used by `load_plugins()` internally; call directly for programmatic registration in tests.

```python
DeployerFactory.register("my-tool", MyProvisioner)
```

---

### `DeployerFactory.load_plugins(work_path)`

Scan `.strata/provisioners/*.py`, import each module, and register all `BaseDeployer` subclasses found.  Called automatically at startup from `BaseDeployCommand`.

```python
DeployerFactory.load_plugins(work_path=Path("/my/workspace"))
```

Silently skips files that fail to import (logs a warning).

---

### `DeployerFactory.is_known_type(type_str) → bool`

Return `True` if the type is a known built-in or registered plugin.

```python
if not DeployerFactory.is_known_type("pulumi"):
    raise ValueError("pulumi plugin is not loaded")
```

---

### `DeployerFactory.get_known_types() → List[str]`

Return all registered and built-in type names, sorted.

```python
print(DeployerFactory.get_known_types())
# ['ansible', 'compose', 'helm', 'pulumi', 'script', 'terraform']
```

---

### `DeployerFactory.reset()`

Clear the runtime plugin registry.  Use in tests to isolate plugin state between test cases.

```python
def teardown_method(self):
    DeployerFactory.reset()
```

---

## Built-in Provisioner Types

| Type        | Class               | IaC tool                                      |
| ----------- | ------------------- | --------------------------------------------- |
| `terraform` | `TerraformDeployer` | HashiCorp Terraform                           |
| `ansible`   | `AnsibleDeployer`   | Red Hat Ansible                               |
| `helm`      | `HelmDeployer`      | Helm (Kubernetes)                             |
| `compose`   | `ComposeDeployer`   | Docker Compose                                |
| `script`    | `ScriptDeployer`    | Lifecycle scripts (shell, Python, PowerShell) |

---

## See Also

- [Building a Provisioner Plugin](../guides/building-a-provisioner-plugin.md) — step-by-step authoring guide
- [Plugin Examples](../examples/provisioners/) — Pulumi and ArgoCD reference implementations
- [Deployers](deployers.md) — architecture overview for the deployer layer
