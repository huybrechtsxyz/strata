# Integrations Documentation

## Overview

Integrations connect the platform to external tools and services (git, terraform, vault, bitwarden, etc.). All integrations follow a consistent pattern: singleton lifecycle, config-driven initialisation, capability protocols, and subprocess isolation via `_run_integration()`.

**Available integrations:**

| Class                       | Module                          | Type string        | Capabilities                                 |
| --------------------------- | ------------------------------- | ------------------ | -------------------------------------------- |
| `GitIntegration`            | `integrations.git`              | `git`              | `IRepositoryTool`                            |
| `TerraformIntegration`      | `integrations.terraform`        | `terraform`        | `IInfrastructureTool`                        |
| `DockerIntegration`         | `integrations.docker`           | `docker`           | `IContainerTool`                             |
| `BitwardenIntegration`      | `integrations.bitwarden`        | `bitwarden`        | `ISecretStore`                               |
| `VaultIntegration`          | `integrations.hashicorp_vault`  | `hashicorp_vault`  | `IVariableStore`, `ISecretStore`, `IKVStore` |
| `ConsulIntegration`         | `integrations.hashicorp_consul` | `hashicorp_consul` | `IVariableStore`, `IKVStore`                 |
| `AzureKeyVaultIntegration`  | `integrations.azure_keyvault`   | `azure_keyvault`   | `ISecretStore`                               |
| `AzureAppConfigIntegration` | `integrations.azure_appconfig`  | `azure_appconfig`  | `IVariableStore`, `IFeatureStore`            |

## Creating an Integration

```python
from strata.integrations.base_integration import BaseIntegration
from strata.integrations.capabilities import IRepositoryTool
from strata.models.integration_model import IntegrationModel

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
from strata.integrations.factory import IntegrationFactory
IntegrationFactory.register_type("mytool", MyToolIntegration)
```

## Using the Factory

```python
from strata.integrations.factory import IntegrationFactory
from strata.models.integration_model import IntegrationModel

config = IntegrationModel(name="git", type="git", required=True)
integration = IntegrationFactory.create(config)

if integration.is_available():
    version = integration.get_version()
```

`IntegrationFactory.create()` raises `ValueError` if the type is not registered.

## Singleton Pattern

Each integration class maintains one singleton per **instance key** (defaults to `"default"`; subclasses override `_get_instance_key_static()` to key on endpoint URL, access token, etc.). To reset between tests:

```python
from strata.integrations.base_integration import BaseIntegration
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
from strata.integrations.registry import IntegrationRegistry

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
from strata.integrations.capabilities import ISecretStore

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
      type: hashicorp_vault
      required: false
      endpoints:
        address: https://vault.example.com
      authentication:
        method: token
```

## Runtime inspection

Use `strata tools` to inspect integrations at runtime without reading config files:

```
strata tools status              # table of all 8 built-in integrations
strata tools check terraform     # deep-check: availability, env vars, auth
```

---

## Workspace drop-ins

Custom integrations can be added to `.strata/integrations/*.py`. Each file must define a `register()` function:

```python
from strata.integrations.factory import IntegrationFactory
from my_package import MyCustomIntegration

def register():
    IntegrationFactory.register_type("my_tool", MyCustomIntegration)
```

Drop-in files are loaded automatically at CLI startup whenever a workspace is detected (i.e. `.strata/` exists). Files whose names start with `_` are skipped. Errors in individual drop-ins are logged as warnings and never crash the CLI.

`strata sln init` creates `.strata/integrations/` with a `README.md` stub and a fully-commented `my_integration.py` starter template — rename it and fill in the stubs to build your first custom integration.

---

## Per-integration reference

### Git

**CLI command:** `git`  
**Install:** <https://git-scm.com/downloads>

#### Environment variables

No environment variables required. Git uses the system credential store (SSH keys or HTTPS credential helper).

#### Auth methods

| Method            | Description                                                                           |
| ----------------- | ------------------------------------------------------------------------------------- |
| SSH keys          | Add an SSH public key to the remote (e.g. GitHub/GitLab). No env vars needed.         |
| HTTPS credentials | Use a personal access token via the Git credential helper or embed in the remote URL. |

---

### Terraform

**CLI command:** `terraform`  
**Install:** <https://developer.hashicorp.com/terraform/install>

#### Environment variables

| Variable              | Purpose                                                      | Required |
| --------------------- | ------------------------------------------------------------ | -------- |
| `TERRAFORM_API_TOKEN` | API token for Terraform Cloud / HCP Terraform authentication | No       |

#### Auth methods

| Method               | Description                                                                              |
| -------------------- | ---------------------------------------------------------------------------------------- |
| Environment variable | Set `TERRAFORM_API_TOKEN`. The platform writes a temporary `.terraformrc` during deploy. |
| Credentials file     | `~/.terraform.d/credentials.tfrc.json` with a token for `app.terraform.io`.              |
| Interactive login    | Run `terraform login` once; token is stored in the credentials file.                     |

#### Minimal YAML configuration

```yaml
type: terraform
spec:
  source: path/to/module
  backend: remote
```

#### Troubleshooting

Run `strata tools check terraform` for live status. Run `strata help terraform-cloud-auth` for Terraform Cloud setup instructions.

---

### Docker

**CLI command:** `docker`  
**Install:** <https://docs.docker.com/get-docker/>

#### Environment variables

No environment variables required. Docker communicates with the local daemon.

#### Auth methods

| Method         | Description                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------- |
| Docker daemon  | Docker Desktop or the Docker daemon must be running. No env vars required.                  |
| `docker login` | Run `docker login <registry>` to authenticate to a private registry before pushing/pulling. |

---

### Bitwarden (Secrets Manager)

**CLI command:** `bws`  
**Install:** <https://bitwarden.com/help/secrets-manager-cli/>

#### Environment variables

| Variable           | Purpose                                                    | Required |
| ------------------ | ---------------------------------------------------------- | -------- |
| `BWS_ACCESS_TOKEN` | Machine account access token for Bitwarden Secrets Manager | Yes      |

The env-var name can be overridden via `authentication.api_key.api_key` in the integration spec.

#### Auth methods

| Method                | Description                                                                              |
| --------------------- | ---------------------------------------------------------------------------------------- |
| Machine account token | Set `BWS_ACCESS_TOKEN` (default) or configure `api_key.api_key` in the integration spec. |

#### Minimal YAML configuration

```yaml
type: bitwarden
spec:
  project_id: <project-uuid>
```

---

### HashiCorp Vault

**CLI command:** `vault`  
**Install:** <https://developer.hashicorp.com/vault/install>

#### Environment variables

| Variable      | Purpose                                                                | Required |
| ------------- | ---------------------------------------------------------------------- | -------- |
| `VAULT_TOKEN` | Vault authentication token                                             | Yes      |
| `VAULT_ADDR`  | Vault server address (derived from `endpoints.address` if set in spec) | No       |

#### Auth methods

| Method  | Description                                                                  |
| ------- | ---------------------------------------------------------------------------- |
| Token   | Set `VAULT_TOKEN`. Most common method for automation.                        |
| AppRole | Obtain a token via `vault write auth/approle/login`; then set `VAULT_TOKEN`. |

#### Minimal YAML configuration

```yaml
type: hashicorp_vault
spec:
  endpoints:
    address: https://vault.example.com
```

---

### HashiCorp Consul

**CLI command:** `consul`  
**Install:** <https://developer.hashicorp.com/consul/install>

#### Environment variables

| Variable            | Purpose                                                              | Required |
| ------------------- | -------------------------------------------------------------------- | -------- |
| `CONSUL_HTTP_TOKEN` | Consul ACL token for authentication                                  | Yes      |
| `CONSUL_HTTP_ADDR`  | Consul server HTTP address (derived from `endpoints.address` if set) | No       |
| `CONSUL_NAMESPACE`  | Consul namespace (Consul Enterprise only)                            | No       |

#### Auth methods

| Method    | Description                                                              |
| --------- | ------------------------------------------------------------------------ |
| ACL token | Set `CONSUL_HTTP_TOKEN`. Use a policy-scoped token — not the root token. |

#### Minimal YAML configuration

```yaml
type: hashicorp_consul
spec:
  endpoints:
    address: http://consul.example.com:8500
```

---

### Azure Key Vault

**CLI command:** `az` (Azure CLI)  
**Install:** <https://learn.microsoft.com/en-us/cli/azure/install-azure-cli>

The integration uses the Azure CLI for availability detection and falls back to the Azure SDK for secret retrieval.

#### Environment variables

| Variable                | Purpose                                                            | Required |
| ----------------------- | ------------------------------------------------------------------ | -------- |
| `AZURE_TENANT_ID`       | Azure Active Directory tenant ID                                   | Yes      |
| `AZURE_CLIENT_ID`       | Service principal / managed identity client ID                     | Yes      |
| `AZURE_CLIENT_SECRET`   | Service principal client secret (omit for OIDC / managed identity) | No       |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID                                              | Yes      |

Env-var names can be overridden via `authentication.oauth2.*` fields in the integration spec.

#### Auth methods

| Method                     | Description                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| Service principal (secret) | Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`.                                                   |
| OIDC / Workload Identity   | Set `AZURE_TENANT_ID` and `AZURE_CLIENT_ID`; omit `AZURE_CLIENT_SECRET`. Used in GitHub Actions / Azure Pipelines. |
| Managed Identity           | No env vars required when running on an Azure resource with a Managed Identity assigned.                           |

#### Minimal YAML configuration

```yaml
type: azure_keyvault
spec:
  endpoints:
    address: https://my-vault.vault.azure.net
```

---

### Azure App Configuration

**CLI command:** `az` (Azure CLI)  
**Install:** <https://learn.microsoft.com/en-us/azure/azure-app-configuration/>

#### Environment variables

| Variable                | Purpose                                                            | Required |
| ----------------------- | ------------------------------------------------------------------ | -------- |
| `AZURE_TENANT_ID`       | Azure Active Directory tenant ID                                   | Yes      |
| `AZURE_CLIENT_ID`       | Service principal / managed identity client ID                     | Yes      |
| `AZURE_CLIENT_SECRET`   | Service principal client secret (omit for OIDC / managed identity) | No       |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID                                              | Yes      |

#### Auth methods

| Method                     | Description                                                                              |
| -------------------------- | ---------------------------------------------------------------------------------------- |
| Service principal (secret) | Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`.                         |
| OIDC / Workload Identity   | Set `AZURE_TENANT_ID` and `AZURE_CLIENT_ID`; omit `AZURE_CLIENT_SECRET`.                 |
| Managed Identity           | No env vars required when running on an Azure resource with a Managed Identity assigned. |
| Connection string          | Set `connection_string` in the integration spec. Not recommended for production.         |

#### Minimal YAML configuration

```yaml
type: azure_appconfig
spec:
  endpoints:
    address: https://my-appconfig.azconfig.io
```


Key fields: `name` (str, required), `type` (str, required), `capabilities` (Set[str]), `required` (bool), `enabled` (bool), `validation` (`min_version`, `max_version`), `authentication` (`AuthenticationModel`), `endpoints.address` (str).
