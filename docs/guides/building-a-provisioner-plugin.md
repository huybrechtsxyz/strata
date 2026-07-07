# Building a Provisioner Plugin

Create a custom provisioner by subclassing `BaseDeployer` and dropping a Python file into `.strata/provisioners/`.  strata auto-discovers all `BaseDeployer` subclasses in that directory and makes them available as provisioner types in workspace YAML.

---

## Quick Start

**1. Create the plugin file:**

```bash
.strata/
└── provisioners/
    └── my_provisioner.py     # auto-discovered on startup
```

**2. Implement `BaseDeployer`:**

```python
# .strata/provisioners/my_provisioner.py

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from strata.deployers.base_deployer import (
    STEP_APPLY, STEP_CHECK, STEP_DESTROY, STEP_OUTPUT,
    STEP_PLAN, STEP_PLAN_DESTROY, STEP_SETUP, STEP_SHOW_PLAN,
    BaseDeployer,
)


class MyProvisioner(BaseDeployer):

    def get_deployer_name(self) -> str:
        return "my-tool"           # must match provisioner: in workspace YAML

    def get_supported_steps(self) -> List[str]:
        return [STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY,
                STEP_DESTROY, STEP_PLAN_DESTROY, STEP_SHOW_PLAN, STEP_OUTPUT]

    def validate_workspace(self) -> Tuple[bool, List[str]]:
        return True, []

    def validate_environment(self) -> Tuple[bool, List[str]]:
        result = subprocess.run(["my-tool", "--version"], capture_output=True)
        if result.returncode != 0:
            return False, ["my-tool CLI not found on PATH"]
        return True, []

    def setup(self) -> Tuple[bool, List[str]]:
        return True, ["Setup complete"]

    def check(self) -> Tuple[bool, List[str]]:
        return True, []

    def plan(self) -> Tuple[bool, List[str]]:
        return True, ["No changes"]

    def apply(self) -> Tuple[bool, List[str]]:
        return True, [f"✓ Stage '{self.stage.name}' applied."]

    def destroy(self) -> Tuple[bool, List[str]]:
        return True, []

    def plan_destroy(self) -> Tuple[bool, List[str]]:
        return True, []

    def show_plan(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        return True, {}, []

    def output(self) -> Tuple[bool, Dict[str, Any], List[str]]:
        return True, {}, []
```

**3. Reference the plugin in workspace YAML:**

```yaml
# workspace.yaml
spec:
  provisioners:
    - name: infra
      provisioner: my-tool     # matches get_deployer_name()
      source:
        repository: my-repo
        source_path: infra/
```

**4. Use it in a deployment:**

```yaml
# deploy/deploy-prd.yaml
spec:
  stages:
    - name: provision
      provisioner: infra
```

That's it.  `strata build run` and `strata deploy run` will invoke your plugin.

---

## Discovery Mechanism

`DeployerFactory.load_plugins(work_path)` is called once at startup.  It:

1. Scans `.strata/provisioners/*.py` for Python files
2. Imports each module with `importlib`
3. Registers all `BaseDeployer` subclasses found via `__subclasses__()` recursion

Any class that is a concrete subclass of `BaseDeployer` (does not have unimplemented abstract methods) is auto-registered under the name returned by `get_deployer_name()`.

Plugin names are case-sensitive and must be unique. Built-in names (`terraform`, `ansible`, `helm`, `compose`, `script`) cannot be overridden by plugins.

---

## The `BaseDeployer` Contract

Your class must implement these abstract methods:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `get_deployer_name()` | `→ str` | Return the provisioner type name (e.g. `"pulumi"`) |
| `get_supported_steps()` | `→ List[str]` | Return the step names your deployer handles |
| `validate_workspace()` | `→ (bool, List[str])` | Check build artifacts exist |
| `validate_environment()` | `→ (bool, List[str])` | Check external tool availability |
| `setup()` | `→ (bool, List[str])` | Initialise (e.g. `pulumi stack select`) |
| `check()` | `→ (bool, List[str])` | Validate config (e.g. `helm lint`) |
| `plan()` | `→ (bool, List[str])` | Preview changes (e.g. `pulumi preview`) |
| `apply()` | `→ (bool, List[str])` | Apply changes (e.g. `pulumi up`) |
| `destroy()` | `→ (bool, List[str])` | Remove resources |
| `plan_destroy()` | `→ (bool, List[str])` | Preview what destroy would remove |
| `show_plan()` | `→ (bool, dict, List[str])` | Return structured plan data |
| `output()` | `→ (bool, dict, List[str])` | Return infrastructure outputs |

### Optional overrides

| Method | Default behaviour | Override when |
|--------|-------------------|---------------|
| `status()` | Returns empty dict + "not implemented" message | Your tool can report live state |
| `health()` | Returns empty dict + "not implemented" message | Your tool supports health checks |
| `collect_outputs()` | Returns `(True, {}, {}, [])` | Your tool produces named outputs to pass to subsequent stages |
| `describe_plan()` | Returns `[]` | Your tool can surface plan context in dry-run mode |
| `save_plan_json()` | No-op | Your tool produces a structured plan file |

---

## Constructor and Instance Variables

The base constructor is called automatically via `super().__init__(...)`. Your subclass receives these instance variables:

| Variable | Type | Description |
|----------|------|-------------|
| `self.stage` | `DeploymentStageModel` | The deployment stage configuration |
| `self.deployment_service` | `DeploymentService` | Access to the deployment model |
| `self.configuration_service` | `ConfigurationService` | Access to the configuration model |
| `self.build_path` | `Path` | Root of the build artifacts directory |
| `self.work_path` | `Path` | Workspace root |
| `self.verbose` | `bool` | Verbose flag from CLI |
| `self.force` | `bool` | Force flag from CLI (`--force`) |
| `self.resolved_values` | `Optional[ResolvedValues]` | Resolved secrets and variables for this stage |
| `self.logger` | `structlog.Logger` | Structured logger bound to your module |

### Accessing secrets and variables

`self.resolved_values` is populated from the deployment's `secrets:` and `variables:` blocks, filtered to only the secrets declared for this stage.

```python
def _build_env(self) -> dict:
    import os
    env = os.environ.copy()
    if self.resolved_values is not None:
        for key, value in self.resolved_values.env_vars.items():
            env[key] = str(value)
    return env
```

### Accessing the workspace model

```python
workspace_service = self.deployment_service.get_workspace_service()
if workspace_service and workspace_service.model:
    provisioners = workspace_service.model.spec.provisioners or []
    iac = next((p for p in provisioners if p.name == self.stage.provisioner), None)
```

---

## Step Name Constants

Import step name constants from `base_deployer` to avoid magic strings:

```python
from strata.deployers.base_deployer import (
    STEP_SETUP,        # "setup"
    STEP_CHECK,        # "check"
    STEP_PLAN,         # "plan"
    STEP_APPLY,        # "apply"
    STEP_DESTROY,      # "destroy"
    STEP_PLAN_DESTROY, # "plan_destroy"
    STEP_SHOW_PLAN,    # "show_plan"
    STEP_OUTPUT,       # "output"
    STEP_STATUS,       # "status"
    STEP_HEALTH,       # "health"
)
```

---

## Return Value Conventions

Every step returns a tuple. Follow these conventions:

```python
# Success with messages
return True, ["✓ Applied successfully", f"3 resources created"]

# Failure with diagnostic messages
return False, ["Command failed: exit code 1", "Error: permission denied"]

# Methods returning (success, data, messages)
return True, {"key": "value"}, ["Collected 2 outputs"]
return False, {}, ["Could not retrieve outputs: connection refused"]
```

- Return `(False, messages)` — never raise exceptions — for recoverable failures
- Return `(True, [])` for no-op steps your tool does not support
- Include the stage name in error messages for clarity: `f"Stage '{self.stage.name}': ..."`

---

## Timeout Helpers

Use `self._get_timeout(step, default)` to respect per-stage timeout overrides:

```python
def apply(self) -> Tuple[bool, List[str]]:
    timeout = self._get_timeout(STEP_APPLY, 1800)   # 30min default
    return self._run(["my-tool", "apply"], timeout=timeout)
```

Timeouts are configured in the deployment YAML under `spec.stages[].timeouts`.

---

## Testing Your Plugin

Use `click.testing.CliRunner` to test end-to-end, or test the deployer class directly by mocking subprocess calls.

```python
from unittest.mock import patch, MagicMock
from pathlib import Path
from strata.deployers.factory import DeployerFactory

# Load the plugin file (simulate auto-discovery)
import importlib.util, sys
spec = importlib.util.spec_from_file_location("my_provisioner", ".strata/provisioners/my_provisioner.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

# Verify registration
assert DeployerFactory.is_known_type("my-tool")

# Create an instance
stage = MagicMock()
stage.name = "infra"
stage.provisioner = "infra"
deployer = DeployerFactory.create(
    "my-tool",
    stage=stage,
    deployment_service=MagicMock(),
    configuration_service=MagicMock(),
    build_path=Path("/tmp/build"),
    work_path=Path("/tmp/workspace"),
)

# Test a step
with patch("subprocess.run") as mock_run:
    mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
    ok, msgs = deployer.apply()
    assert ok
```

---

## Complete Examples

Ready-to-use examples are in `docs/examples/provisioners/`:

- [`pulumi_provisioner.py`](../examples/provisioners/pulumi_provisioner.py) — Pulumi IaC plugin
- [`argocd_provisioner.py`](../examples/provisioners/argocd_provisioner.py) — ArgoCD GitOps plugin

---

## Checklist

Before shipping a provisioner plugin:

- [ ] `get_deployer_name()` returns a unique, lowercase, hyphen-separated name
- [ ] `validate_environment()` checks the CLI binary is available on PATH
- [ ] `validate_workspace()` checks that build artifacts exist before any step runs
- [ ] All 8 abstract methods are implemented (even if some are no-ops)
- [ ] Steps return `(False, messages)` on failure, never raise exceptions
- [ ] Subprocess calls use `self._build_env()` to propagate resolved secrets
- [ ] Timeouts are honoured via `self._get_timeout(step, default)`
- [ ] A unit test exists that mocks subprocess calls
