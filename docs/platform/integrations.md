# Integrations Documentation

## Overview

Integrations connect the platform to external tools and services (git, terraform, vault, bitwarden, etc.). All integrations follow a consistent pattern: singleton lifecycle, config-driven initialisation, capability protocols, and subprocess isolation via `_run_integration()`.

**Available integrations:**

| Class                       | Module                          | Type string       | Capabilities                                 |
| --------------------------- | ------------------------------- | ----------------- | -------------------------------------------- |
| `GitIntegration`            | `integrations.git`              | `git`             | `IRepositoryTool`                            |
| `TerraformIntegration`      | `integrations.terraform`        | `terraform`       | `IInfrastructureTool`                        |
| `BitwardenIntegration`      | `integrations.bitwarden`        | `bitwarden`       | `ISecretStore`                               |
| `VaultIntegration`          | `integrations.hashicorp_vault`  | `vault`           | `IVariableStore`, `ISecretStore`, `IKVStore` |
| `ConsulIntegration`         | `integrations.hashicorp_consul` | `consul`          | `IVariableStore`, `IKVStore`                 |
| `AzureKeyVaultIntegration`  | `integrations.azure_keyvault`   | `azure_keyvault`  | `ISecretStore`                               |
| `AzureAppConfigIntegration` | `integrations.azure_appconfig`  | `azure_appconfig` | `IVariableStore`, `IFeatureStore`            |

## Creating an Integration

```python
from xyz_platform.integrations.base_integration import BaseIntegration
from xyz_platform.integrations.capabilities import IRepositoryTool
from xyz_platform.models.integration_model import IntegrationModel

class MyToolIntegration(BaseIntegration):
    COMMAND = "mytool"
    CAPABILITIES = [IRepositoryTool]

    def get_version_command(self):
        return ["mytool", "--version"]

    def parse_version(self, version_output: str) -> str:
        import re
        match = re.search(r"(\d+\.\d+\.\d+)", version_output)
        return match.group(1) if match else version_output.strip()
```

Register it with the factory:

```python
from xyz_platform.integrations.factory import IntegrationFactory
IntegrationFactory.register_type("mytool", MyToolIntegration)
```

## Using the Factory

```python
from xyz_platform.integrations.factory import IntegrationFactory
from xyz_platform.models.integration_model import IntegrationModel

config = IntegrationModel(name="git", type="git", required=True)
integration = IntegrationFactory.create(config)

if integration.is_available():
    version = integration.get_version()
```

`IntegrationFactory.create()` raises `ValueError` if the type is not registered.

## Singleton Pattern

Each integration class maintains one singleton per **instance key** (defaults to `"default"`; subclasses override `_get_instance_key_static()` to key on endpoint URL, access token, etc.). To reset between tests:

```python
from xyz_platform.integrations.base_integration import BaseIntegration
BaseIntegration._instances.clear()
```

## `BaseIntegration` API

| Method                                 | Returns         | Description                                                   |
| -------------------------------------- | --------------- | ------------------------------------------------------------- |
| `is_available(use_cache=True)`         | `bool`          | Whether the CLI command is on PATH                            |
| `get_version(use_cache=True)`          | `Optional[str]` | Installed version string                                      |
| `validate_version()`                   | `(bool, str)`   | Check against `config.validation.min_version` / `max_version` |
| `ensure_available()`                   | `(bool, str)`   | Combined availability + version check                         |
| `get_info()`                           | `dict`          | Name, type, command, availability, version                    |
| `_run_integration(args, cwd, timeout)` | `CommandResult` | Run `[command] + args` via `run_command()`                    |
| `_get_env_var(name, default)`          | `Optional[str]` | Read environment variable                                     |
| `_resolve_env_vars(value)`             | `str`           | Expand `${VAR}` and `$VAR` in a string                        |

Abstract methods that subclasses must implement: `get_version_command()` → `List[str]`, `parse_version(output)` → `str`.

## `IntegrationRegistry`

Singleton registry for tracking loaded integrations and validating operation requirements.

```python
from xyz_platform.integrations.registry import IntegrationRegistry

registry = IntegrationRegistry.get_instance()
registry.register_integration("git", git_integration)
registry.register_requirement("deploy", ["git", "terraform"])

ok, errors = registry.validate_operation("deploy")
if not ok:
    for err in errors:
        print(err)
```

**Reset between tests:** `IntegrationRegistry.reset()`

## Capability Protocols

Capability protocols are `runtime_checkable` `Protocol` classes in `integrations.capabilities`. Use `isinstance()` to check capability support:

```python
from xyz_platform.integrations.capabilities import ISecretStore

if isinstance(integration, ISecretStore):
    secret = integration.get_secret("my/secret")
```

| Protocol              | Methods                                          |
| --------------------- | ------------------------------------------------ |
| `IVariableStore`      | `get_variable`, `set_variable`, `list_variables` |
| `ISecretStore`        | `get_secret`, `set_secret`, `list_secrets`       |
| `IFeatureStore`       | `get_feature`, `set_feature`, `list_features`    |
| `IKVStore`            | `get_kv`, `set_kv`                               |
| `IRepositoryTool`     | `clone`, `pull`, `get_current_branch`            |
| `IInfrastructureTool` | `init`, `plan`, `apply`, `destroy`               |

`StoreIntegration` (base for all store-type integrations) provides no-op default implementations for all store methods — subclasses override only what they support.

## `IntegrationModel` Configuration

```yaml
spec:
  integrations:
    - name: my_git
      type: git
      required: true
      capabilities: [repository]

    - name: vault_prod
      type: vault
      required: false
      endpoints:
        address: https://vault.example.com
      authentication:
        method: token
```

Key fields: `name` (str, required), `type` (str, required), `capabilities` (Set[str]), `required` (bool), `enabled` (bool), `validation` (`min_version`, `max_version`), `authentication` (`AuthenticationModel`), `endpoints.address` (str).
