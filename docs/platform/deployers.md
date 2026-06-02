# Deployers Documentation

## Overview

Deployers execute a deployment stage step-by-step against a specific IaC tool or lifecycle script system. They follow a validate → step sequence pattern: call `validate_workspace()` and `validate_environment()` before running any steps. All context is bound at construction time — step methods take no arguments.

**Available deployers:**

| Class               | Module                         | Backend         | Purpose                                                           |
| ------------------- | ------------------------------ | --------------- | ----------------------------------------------------------------- |
| `BaseDeployer`      | `deployers.base_deployer`      | —               | Abstract base — step contracts + constructor                      |
| `TerraformDeployer` | `deployers.terraform_deployer` | Terraform CLI   | Runs init → validate → plan → apply via `terraform`               |
| `AnsibleDeployer`   | `deployers.ansible_deployer`   | Ansible CLI     | Runs galaxy install → syntax-check → check → apply                |
| `ComposeDeployer`   | `deployers.compose_deployer`   | Docker CLI      | Deploys per-namespace Docker Compose/Stack from build output      |
| `HelmDeployer`      | `deployers.helm_deployer`      | Helm CLI        | Deploys per-namespace, per-module Helm releases from build output |
| `ScriptDeployer`    | `deployers.script_deployer`    | Shell/Python/PS | Executes lifecycle scripts from the deployment YAML               |

---

## BaseDeployer

Abstract base class all deployers extend. Binds all deployment context in the constructor; step methods call `self.deployment_service`, `self.build_path`, etc.

### Constructor

```python
BaseDeployer(
    stage: DeploymentStageModel,
    deployment_service: DeploymentService,
    configuration_service: ConfigurationService,
    build_path: Path,
    work_path: Path,
    verbose: bool = False,
    force: bool = False,
)
```

### Abstract interface

| Method                   | Returns                        | Description                            |
| ------------------------ | ------------------------------ | -------------------------------------- |
| `get_deployer_name()`    | `str`                          | Canonical name (e.g. `"terraform"`)    |
| `get_supported_steps()`  | `List[str]`                    | Ordered step names                     |
| `validate_workspace()`   | `Tuple[bool, List[str]]`       | Verify IaC artefacts / lifecycle exist |
| `validate_environment()` | `Tuple[bool, List[str]]`       | Verify tool binary / auth              |
| `setup()`                | `Tuple[bool, List[str]]`       | Initialise the tool                    |
| `check()`                | `Tuple[bool, List[str]]`       | Validate configuration                 |
| `plan()`                 | `Tuple[bool, List[str]]`       | Preview changes                        |
| `apply()`                | `Tuple[bool, List[str]]`       | Apply changes                          |
| `destroy()`              | `Tuple[bool, List[str]]`       | Tear down resources                    |
| `plan_destroy()`         | `Tuple[bool, List[str]]`       | Preview what destroy would remove      |
| `show_plan()`            | `Tuple[bool, Dict, List[str]]` | Decode the saved plan file             |
| `output()`               | `Tuple[bool, Dict, List[str]]` | Retrieve infrastructure outputs        |

### Step constants

```python
from strata.deployers.base_deployer import (
    STEP_SETUP, STEP_CHECK, STEP_PLAN, STEP_APPLY,
    STEP_DESTROY, STEP_PLAN_DESTROY, STEP_SHOW_PLAN, STEP_OUTPUT,
)
```

### Typical call sequence

```python
deployer = TerraformDeployer(stage=stage, deployment_service=svc, ...)

ok, msgs = deployer.validate_workspace()
if not ok:
    raise RuntimeError(msgs)

ok, msgs = deployer.validate_environment()
if not ok:
    raise RuntimeError(msgs)

for step in ["setup", "check", "plan"]:
    ok, msgs = getattr(deployer, step)()
    if not ok:
        break
```

---

## TerraformDeployer

Runs a deployment stage using the Terraform CLI (init → validate → plan → apply).

### Constructor

```python
TerraformDeployer(
    stage, deployment_service, configuration_service,
    build_path, work_path,
    verbose=False, force=False,
    resolved_values=None,  # ResolvedValues — injected as TF_VAR_* env vars during plan/apply/destroy
)
```

### Step → Terraform command mapping

| Step           | Command                                                 |
| -------------- | ------------------------------------------------------- |
| `setup`        | `terraform init` (with backend config if present)       |
| `check`        | `terraform validate`                                    |
| `plan`         | `terraform plan -out=<stage>.tfplan`                    |
| `apply`        | `terraform apply <stage>.tfplan`                        |
| `destroy`      | `terraform destroy` (`-auto-approve` when `force=True`) |
| `plan_destroy` | `terraform plan -destroy -out=<stage>.tfplan`           |
| `show_plan`    | `terraform show -json <stage>.tfplan`                   |
| `output`       | `terraform output -json` → `{name: value}` dict         |

### validate_workspace

Resolves the `WorkspaceIacModel` for the stage (priority: explicit `stage.provisioner` name → topology lookup → sole provisioner fallback), derives the working directory, and checks that `*.tf` files exist there. Sets `_working_dir` and `_plan_file` for subsequent steps.

### validate_environment

Looks up the `TerraformIntegration` instance by name from `IntegrationService` and calls `is_available()`. Must be called after `validate_workspace()`.

### IaC model resolution priority

1. `stage.provisioner` set — match `workspace.spec.provisioners` by name
2. `stage.topology` set — find the topology, match a provisioner whose `.provisioner` type matches
3. Single provisioner workspace — use it unconditionally

### Working directory

`{deployment_build_path}/{iac_model.source.target_path}` — falls back to `terraform/{iac_model.name}` when `target_path` is unset.

### Resolved values (TF_VAR injection)

When `resolved_values` is provided, `plan`, `apply`, `destroy`, and `plan_destroy` wrap the Terraform call in `inject_tf_vars(resolved_values)`, which temporarily sets `TF_VAR_*` environment variables for the subprocess.

---

## AnsibleDeployer

Runs a deployment stage using Ansible (galaxy install → syntax-check → check-mode → apply).

### Constructor

```python
AnsibleDeployer(
    stage, deployment_service, configuration_service,
    build_path, work_path,
    verbose=False, force=False,
)
```

### Step → Ansible command mapping

| Step           | Command                                                   |
| -------------- | --------------------------------------------------------- |
| `setup`        | `ansible-galaxy collection install -r requirements.yml`   |
| `check`        | `ansible-playbook <playbook> --syntax-check`              |
| `plan`         | `ansible-playbook <playbook> --check --diff`              |
| `apply`        | `ansible-playbook <playbook> [-i inventory] [-e key=val]` |
| `destroy`      | `ansible-playbook destroy.yml` (requires `force=True`)    |
| `plan_destroy` | Not supported — returns `(True, {}, [])`                  |
| `show_plan`    | Not supported — returns `(True, {}, [])`                  |
| `output`       | Not supported — returns `(True, {}, [])`                  |

### validate_workspace

Resolves the `WorkspaceIacModel` for the stage by matching the first provisioner with `type == ProvisionerType.ANSIBLE`. If `stage.provisioner` is set, looks up by name. Verifies the source directory exists on disk.

### validate_environment

Creates a minimal `AnsibleIntegration` instance and calls `ensure_available()`. Sets `self._ansible` for use in subsequent steps.

### IaC spec options

| Key          | Default     | Description                                     |
| ------------ | ----------- | ----------------------------------------------- |
| `playbook`   | `site.yml`  | Main playbook file to execute                   |
| `inventory`  | auto-detect | Inventory file (`inventory`, `hosts.yml`, etc.) |
| `extra_vars` | `{}`        | Extra variables passed via `-e key=value`       |

### Auto-discovery

- **Inventory:** looks for `inventory`, `inventory.yml`, `hosts.yml`, `hosts` in the working directory
- **Requirements:** looks for `requirements.yml` or `collections/requirements.yml`
- **Destroy playbook:** expects `destroy.yml` in the working directory

---

## ScriptDeployer

Executes lifecycle scripts defined in `deployment_model.spec.lifecycle`. No external binary is required.

### Constructor

```python
ScriptDeployer(
    stage, deployment_service, configuration_service,
    build_path, work_path,
    verbose=False, force=False,
)
```

### Step → lifecycle phase mapping

| Step           | Lifecycle phase       |
| -------------- | --------------------- |
| `setup`        | `deploy_setup`        |
| `check`        | `deploy_check`        |
| `plan`         | `deploy_plan`         |
| `apply`        | `deploy_apply`        |
| `destroy`      | `deploy_destroy`      |
| `plan_destroy` | `deploy_plan_destroy` |
| `output`       | `deploy_output`       |
| `show_plan`    | *(not applicable)*    |

A phase that is not defined in the lifecycle → skip (returns `True`). A phase with no scripts → skip (returns `True`).

### validate_workspace

Checks that `deployment_model.spec.lifecycle` is set. Returns `False` with an explanatory message if missing.

### validate_environment

Always returns `(True, [])` — no external binary needed.

### Script types

| Extension      | Interpreter  |
| -------------- | ------------ |
| `.sh`, `.bash` | `bash`       |
| `.py`          | `python`     |
| `.ps1`         | `pwsh -File` |

### Environment variables injected into every script

| Variable     | Value                             |
| ------------ | --------------------------------- |
| `WORK_PATH`  | `work_path` from the constructor  |
| `BUILD_PATH` | `build_path` from the constructor |
| `STAGE_NAME` | `stage.name`                      |

### Timeout

Each script subprocess has a hard 300-second timeout. Exceeding it returns `False` with a timeout message.

### Script entry types

Script entries in the lifecycle phase can be:
- **Plain string** — the path to the script file
- **`ScriptPathModel`** — uses the `.file` attribute

---

## Deployment YAML for ScriptDeployer

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
meta:
  name: my_deployment
spec:
  workspace:
    name: my_workspace
    file: workspace.yaml
  environments:
    - environment.yaml
  stages:
    - name: production
      type: script
  lifecycle:
    deploy_setup:
      scripts:
        - scripts/setup.sh
    deploy_apply:
      scripts:
        - scripts/apply.sh
    deploy_destroy:
      scripts:
        - scripts/destroy.sh
```

---

## ComposeDeployer

Deploys Docker Compose/Stack artifacts produced by `ComposeBuilder`. For each namespace that has a `docker-compose.yml` in the build path, `ComposeDeployer` runs `docker stack deploy`. Because Docker Stack is the Swarm-mode orchestrator, **Docker must be running in Swarm mode** (`docker swarm init` or join an existing swarm) before any steps are executed.

> **Note:** `docker stack` commands require Docker Swarm mode. Running `docker stack deploy` against a daemon that is not a Swarm manager returns an error.

### Step → Docker command mapping

| Step           | Docker command                                                                 |
| -------------- | ------------------------------------------------------------------------------ |
| `setup`        | `docker info` — verifies the daemon is reachable                               |
| `check`        | Verifies `docker-compose.yml` exists on disk for each namespace                |
| `plan`         | Parses compose files locally and counts services per namespace (no subprocess) |
| `apply`        | `docker stack deploy --with-registry-auth -c {file} {namespace}` per namespace |
| `destroy`      | `docker stack rm {namespace}` per namespace (requires `--force`)               |
| `plan_destroy` | `docker stack ls` — lists running stacks matching namespace names              |
| `output`       | `docker stack services {namespace}` per namespace                              |
| `show_plan`    | Informational — no persisted plan format; returns an explanatory message       |

### Constructor

```python
ComposeDeployer(
    stage, deployment_service, configuration_service,
    build_path, work_path,
    verbose=False, force=False,
    resolved_values=None,  # ResolvedValues — injected as bare KEY env vars during apply
)
```

### validate_workspace

Iterates all namespace services from the deployment. For each namespace, looks for `{build_path}/{deployment_name}/{namespace}/docker-compose.yml`. Namespaces with no compose file are silently skipped — a missing file is not an error at this stage. Only namespaces where the file exists are registered for subsequent steps; if no files are found at all, a warning message is returned (but validation still passes).

### validate_environment

Calls `DockerIntegration.ensure_available()`. Sets `self._docker` for subsequent steps. Fails with an error message if Docker is not on PATH or the daemon is unreachable.

### Resolved values (compose env injection)

When `resolved_values` is provided, `apply` does two things for each namespace:

1. **Writes a `.env` file** alongside `docker-compose.yml` — consumed by `docker compose up` for local workflows.
2. **Injects vars into `os.environ`** via `inject_compose_env()` for the duration of the `docker stack deploy` subprocess — required because `docker stack` does **not** read `.env` files.

| Aspect           | Detail                                                                                                   |
| ---------------- | -------------------------------------------------------------------------------------------------------- |
| Key format       | Bare key — no prefix (e.g. `DB_HOST`, not `TF_VAR_DB_HOST`)                                              |
| Merge order      | features → variables → secrets (secrets win on collision)                                                |
| Feature booleans | Lowercased (`"true"` / `"false"`); `None` features are skipped                                           |
| `.env` file      | Always written to the namespace directory, even when empty                                               |
| Secret values    | Written to `.env` — the build directory must be protected                                                |
| Logging          | Counts are logged (`Injecting X variable(s), Y feature(s), Z secret(s)`) — secret names are never logged |

### Deployment YAML example

```yaml
spec:
  stages:
    - name: production
      provisioner: xyz_swarm
      steps: [apply]
```

Where `xyz_swarm` references a provisioner with `provisioner: compose` in the workspace YAML.

See also: [DockerIntegration](../platform/integrations.md#docker)

---

## HelmDeployer

Deploys per-namespace, per-module Helm releases from build output produced by `HelmBuilder`. For each module with `type: helm`, reads `values.yaml` and `meta.yaml` from the build path and runs `helm upgrade --install`.

### Step → Helm command mapping

| Step           | Helm command                                                                         |
| -------------- | ------------------------------------------------------------------------------------ |
| `setup`        | `helm repo add <alias> <url>` + `helm repo update` (registry charts only)            |
| `check`        | `helm lint <chart_ref>` (local charts only; registry charts skipped)                 |
| `plan`         | `helm upgrade --dry-run --install -n <ns> -f values.yaml <release> <chart>`          |
| `apply`        | `helm upgrade --install --create-namespace -n <ns> -f values.yaml <release> <chart>` |
| `destroy`      | `helm uninstall -n <ns> <release>` (requires `--force`)                              |
| `plan_destroy` | `helm get manifest -n <ns> <release>`                                                |
| `output`       | `helm get values -n <ns> <release>`                                                  |
| `show_plan`    | No-op — returns empty dict                                                           |

### validate_workspace

Iterates all namespace/module pairs in the build path. Only processes modules where `module.spec.type == ServiceDeployerType.HELM`. For each qualifying module, reads `values.yaml` and `meta.yaml` from the build output.

### validate_environment

Calls `HelmIntegration.ensure_available()`.

### Chart source resolution

`meta.yaml` carries `releaseName` and `namespace`. The chart reference is derived from `module.spec.source`:

- `chart_repository + chart_name` → registry chart
- `repository + source_path` → local chart path in the build directory

### Deployment YAML example

```yaml
spec:
  stages:
    - name: platform
      provisioner: xyz_iac
      steps: [apply]
```

Where `xyz_iac` references a provisioner with `provisioner: helm` in the workspace YAML.
