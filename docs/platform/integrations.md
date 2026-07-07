# Integrations Documentation

## Overview

Integrations connect the platform to external tools and services (git, terraform, vault, bitwarden, etc.). All integrations follow a consistent pattern: singleton lifecycle, config-driven initialisation, capability protocols, and subprocess isolation via `_run_integration()`.

**Available integrations:**

| Class                       | Module                          | Type string        | Capabilities                                 |
| --------------------------- | ------------------------------- | ------------------ | -------------------------------------------- |
| `GitIntegration`            | `integrations.git`              | `git`              | `IRepositoryTool`                            |
| `TerraformIntegration`      | `integrations.terraform`        | `terraform`        | `IInfrastructureTool`                        |
| `OpenTofuIntegration`       | `integrations.opentofu`         | `opentofu`         | `IInfrastructureTool`                        |
| `AnsibleIntegration`        | `integrations.ansible`          | `ansible`          | `IInfrastructureTool`                        |
| `DockerIntegration`         | `integrations.docker`           | `docker`           | `IContainerTool`                             |
| `HelmIntegration`           | `integrations.helm`             | `helm`             | `IInfrastructureTool`                        |
| `BitwardenIntegration`      | `integrations.bitwarden`        | `bitwarden`        | `ISecretStore`                               |
| `VaultIntegration`          | `integrations.hashicorp_vault`  | `hashicorp_vault`  | `IVariableStore`, `ISecretStore`, `IKVStore`, `IFeatureStore` |
| `OpenBaoIntegration`        | `integrations.openbao`          | `openbao`          | `IVariableStore`, `ISecretStore`, `IKVStore`                  |
| `ConsulIntegration`         | `integrations.hashicorp_consul` | `hashicorp_consul` | `IVariableStore`, `IKVStore`, `IFeatureStore`                 |
| `AzureKeyVaultIntegration`  | `integrations.azure_keyvault`   | `azure_keyvault`   | `ISecretStore`                               |
| `AzureAppConfigIntegration` | `integrations.azure_appconfig`  | `azure_appconfig`  | `IVariableStore`, `IFeatureStore`            |
| `InfisicalIntegration`      | `integrations.infisical`        | `infisical`        | `ISecretStore`, `IVariableStore`             |
| `EtcdIntegration`           | `integrations.etcd`             | `etcd`             | `IVariableStore`, `IKVStore`                 |
| `FlagsmithIntegration`      | `integrations.flagsmith`        | `flagsmith`        | `IFeatureStore`, `IVariableStore`                             |

**SIEM / audit sink integrations** (implement `ISiemSink`, used by `AuditController`):

| Class | Module | Type string | Backend |
|-------|--------|-------------|---------|
| `SplunkSiemIntegration` | `integrations.siem.splunk_siem_integration` | `splunk` | Splunk HTTP Event Collector (HEC) |
| `SentinelIntegration` | `integrations.siem.sentinel_integration` | `sentinel` | Azure Monitor / Sentinel (DCR Logs Ingestion API) |
| `ElkSiemIntegration` | `integrations.siem.elk_siem_integration` | `elk` | Logstash (TCP) or Elasticsearch (HTTP Bulk API) |
| `OtelSiemIntegration` | `integrations.siem.otel_siem_integration` | `otel` | Any OTLP/HTTP backend (Grafana, Datadog, Sumo Logic, etc.) |

SIEM integrations are HTTP-based — they have no CLI command and are not included in `strata tools status`. Use `strata tools check <name>` to probe connectivity. See [SIEM Audit Forwarding Guide](../guides/siem-audit-forwarding.md) for configuration examples.

**Security & Scanning integrations** (implement `ICveScanner`, used by `RunBuildCommand`):

| Class | Module | Type string | Backend | Auto-detect |
|-------|--------|-------------|---------|------------|
| `CveScannerIntegration` | `integrations.cve_scanner` | `cve_scanner` | Trivy (default) or Grype | Yes (first available) |

CVE scanner is optional and used with `strata build run --audit`. It auto-detects the first available backend (Trivy preferred, Grype fallback). See [CVE Vulnerability Scanning Guide](../guides/cve-vulnerability-scanning.md) for configuration and usage examples.

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

| YAML capability string | Protocol              | Methods                                          | Covers (examples)                                                                                                           |
| ---------------------- | --------------------- | ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- |
| `variables`            | `IVariableStore`      | `get_variable`, `set_variable`, `list_variables` | Consul, Azure App Configuration, Vault, Flagsmith (identity traits)                                                         |
| `secrets`              | `ISecretStore`        | `get_secret`, `set_secret`, `list_secrets`       | Vault, Azure KeyVault, Bitwarden, Infisical — **`github` and `environment` are built-in resolvers, not integrations**       |
| `features`             | `IFeatureStore`       | `get_feature`, `set_feature`, `list_features`    | Azure App Configuration, Flagsmith, Vault (KV prefix), Consul (KV prefix)                                                  |
| `keyvalue`             | `IKVStore`            | `get_kv`, `set_kv`                               | Consul, Vault KV, etcd                                                                                                      |
| `repository`           | `IRepositoryTool`     | `clone`, `pull`, `get_current_branch`            | Git, Mercurial                                                                                                              |
| `infrastructure`       | `IInfrastructureTool` | `init`, `plan`, `apply`, `destroy`               | Terraform, OpenTofu, **Ansible**, **Helm**, Pulumi *(umbrella for all IaC + config-management tools — not `configuration`)* |
| `container`            | `IContainerTool`      | `build`, `run`, `push`, `pull`                   | Docker, Podman *(umbrella for all container + container-native deployment tools — not `deployment`)*                        |
| `api`                  | *(no protocol)*       | Generic REST/HTTP — no capability interface      | Any REST/HTTP endpoint                                                                                                      |

`StoreIntegration` (base for all store-type integrations) provides no-op default implementations for all store methods — subclasses override only what they support.

> **Built-in secret resolvers (`constant`, `environment`, `github`)** do not use the integration system at all — they are resolved inline by the `ValueController` without any registered integration. `github` reads environment variables that GitHub Actions injects into the runner before each step. See [configuration.md — Secret Stores](../config/configuration.md#secret-stores) for full details on `store: github`.

### Store-specific notes

#### Vault and Consul — feature flags via KV prefix

Neither Vault nor Consul has a native feature flag concept. `IFeatureStore` is implemented by
mapping flag names to KV entries under a configurable path prefix (default: `features/`).

| Method | KV path |
|--------|---------|
| `get_feature("dark-mode")` | `features/dark-mode` |
| `set_feature("dark-mode", True)` | writes `"true"` to `features/dark-mode` |
| `list_features("")` | lists all keys under `features/` |

The prefix is controlled by the `features_path` keyword argument (default `"features"`).
This separation keeps feature data organised alongside — but distinct from — other KV entries.

`set_feature()` and `set_variable()` both use **create-if-not-exists** semantics: if the key
already exists the write is skipped and the existing value is returned. This ensures the
`default:` field in YAML only seeds the store on the very first build.

#### Flagsmith — variables via identity traits

Flagsmith has no native key/value store. `IVariableStore` is implemented using Flagsmith's
**identity traits** API — key/value pairs attached to a named identity.

| Method | Flagsmith API |
|--------|--------------|
| `get_variable("theme")` | `GET /api/v1/identities/?identifier=<identity>` |
| `set_variable("theme", "light")` | `POST /api/v1/traits/` with `X-Environment-Key` |
| `list_variables("")` | returns all trait keys for the identity |

The `identity` kwarg (default `"default"`) scopes traits to a specific Flagsmith identity.
**Required env var:** `FLAGSMITH_ENVIRONMENT_KEY` must be a **server-side** (not client-side)
key — client-side keys are read-only and will cause `set_variable()` to return `False`.

#### Flagsmith — feature flag seeding limitations

`set_feature()` does **not** create feature flags. Flagsmith flags must be created in the
Flagsmith dashboard or via their management API before strata can resolve them. When called:

- Returns `True` if the flag exists (verifies presence; does not toggle the flag state).
- Returns `False` if the flag is not found, with a log message directing the operator to
  create it in the Flagsmith dashboard.

As a result, a `default:` value on a Flagsmith-backed feature entry cannot seed a missing
flag — the resolution will fail if the flag was never created externally. The `default:` field
acts as a fallback value _for the build output_ only, not as a create-and-seed operation.

**Optional:** Set `FLAGSMITH_MANAGEMENT_KEY` to enable future management API operations
(flag lifecycle management). This is separate from `FLAGSMITH_ENVIRONMENT_KEY`.

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
strata tools status                                        # table of all 8 built-in integrations
strata tools status --deployment deployment.yaml           # adds Requirement column (required/optional/—)
strata tools status --deployment deployment.yaml --required --missing  # CI gate: exit 3 if any required are absent
strata tools check terraform                               # deep-check: availability, env vars, auth
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

### OpenTofu

**CLI command:** `tofu`  
**Install:** <https://opentofu.org/docs/intro/install/>

OpenTofu is the Linux Foundation fork of Terraform (MPL-2.0), offering a drop-in CLI replacement. `type: opentofu` targets the `tofu` binary; everything else (state format, provider ecosystem, env vars) is identical to Terraform.

#### Environment variables

Same as Terraform — see the [Terraform](#terraform) section above.

#### Auth methods

| Method               | Description                                                                              |
| -------------------- | ---------------------------------------------------------------------------------------- |
| Environment variable | Set `TERRAFORM_API_TOKEN`. The platform writes a temporary `.terraformrc` during deploy. |
| Credentials file     | `~/.terraform.d/credentials.tfrc.json` with a token for `app.terraform.io`.              |
| Interactive login    | Run `tofu login` once; token is stored in the credentials file.                          |

#### Minimal YAML configuration

```yaml
type: opentofu
spec:
  source: path/to/module
  backend: remote
```

#### Troubleshooting

Run `strata tools check opentofu` for live status.

---

### Ansible

**CLI command:** `ansible-playbook`  
**Install:** <https://docs.ansible.com/ansible/latest/installation_guide/>

#### Environment variables

| Variable                      | Purpose                                          | Required |
| ----------------------------- | ------------------------------------------------ | -------- |
| `ANSIBLE_CONFIG`              | Path to a custom `ansible.cfg` file              | No       |
| `ANSIBLE_INVENTORY`           | Default inventory file or directory              | No       |
| `ANSIBLE_VAULT_PASSWORD_FILE` | Path to a vault password file for encrypted vars | No       |

#### Auth methods

| Method              | Description                                                                  |
| ------------------- | ---------------------------------------------------------------------------- |
| SSH keys            | Configure SSH keys on managed hosts. Ansible connects over SSH by default.   |
| Vault password file | Set `ANSIBLE_VAULT_PASSWORD_FILE` to decrypt encrypted variables at runtime. |
| `--ask-become-pass` | Prompt for sudo password (interactive only — not suitable for CI).           |

#### Minimal YAML configuration

```yaml
type: ansible
spec:
  source: ansible/          # directory containing playbooks
  playbook: site.yml         # main playbook (defaults to site.yml)
  inventory: hosts.yml       # optional — auto-discovered if not set
```

#### AnsibleDeployer step mapping

| Deployer Step  | Ansible Command                                                              |
| -------------- | ---------------------------------------------------------------------------- |
| `setup`        | `ansible-galaxy collection install -r requirements.yml`                      |
| `check`        | `ansible-playbook <playbook> --syntax-check`                                 |
| `plan`         | `ansible-playbook <playbook> --check --diff [-e @file ...] [-e key=val ...]` |
| `apply`        | `ansible-playbook <playbook> [-e @file ...] [-e key=val ...]`                |
| `destroy`      | `ansible-playbook destroy.yml` (requires `--force`)                          |
| `plan_destroy` | Not supported — returns empty                                                |
| `show_plan`    | Not supported — returns empty                                                |
| `output`       | Not supported — returns empty                                                |

#### Strata variable files

When `AnsibleBuilder` has run (as part of `strata build run`), it writes `strata_*.yml` files into the provisioner build directory. `AnsibleDeployer` discovers these automatically and passes them as `-e @file.yml` arguments to every playbook invocation — before any inline `extra_vars` from the IaC configuration.

This provides playbooks with structured access to the full platform model without any manual `vars_files` declarations. See [AnsibleBuilder](builders.md#ansiblebuilder) for the complete list of generated files and their variable keys.

#### SSH private key injection

`AnsibleDeployer` resolves an SSH private key from `resolved_values.secrets` (key name configurable via `ssh_private_key_secret` in the provisioner configuration, defaulting to `ssh_private_key`) or from the matching environment variable. The key is loaded into a temporary ssh-agent session so it never touches disk. If ssh-agent is unavailable, a `chmod 600` tempfile is used and deleted immediately after the subprocess exits.

#### Minimal YAML configuration

```yaml
type: ansible
spec:
  source: ansible/                    # directory containing playbooks
  playbook: site.yml                  # main playbook (defaults to site.yml)
  inventory: hosts.yml                # optional — auto-discovered if not set
  ssh_private_key_secret: my_ssh_key  # optional — secret name for SSH private key
  extra_vars:                         # optional — passed as -e key=value after strata var files
    target_env: production
```

#### Troubleshooting

Run `strata tools check ansible` for live status. Ensure `ansible-playbook` is in your PATH and that SSH connectivity to managed hosts is configured.

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

Used by `ComposeDeployer` for Docker Stack deployments.

---

### Helm

**CLI command:** `helm`  
**Install:** <https://helm.sh/docs/intro/install/>

#### Environment variables

| Variable         | Purpose                                                | Required |
| ---------------- | ------------------------------------------------------ | -------- |
| `KUBECONFIG`     | Path to kubeconfig file (defaults to `~/.kube/config`) | No       |
| `HELM_NAMESPACE` | Optional default namespace for Helm operations         | No       |

#### Auth methods

| Method                     | Description                                                                        |
| -------------------------- | ---------------------------------------------------------------------------------- |
| kubeconfig                 | Service account token, OIDC, or exec plugin — configured in `~/.kube/config`.      |
| In-cluster service account | When running inside a Kubernetes pod, Helm uses the mounted service account token. |

#### Which deployer uses it

`HelmDeployer`

#### Troubleshooting

Run `strata tools check helm` for live status. Ensure `helm` is in your PATH and that `kubectl` can reach your cluster (`kubectl cluster-info`).

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

### OpenBao

**CLI command:** `bao`  
**Install:** <https://openbao.org/docs/install/>

OpenBao is the Linux Foundation fork of HashiCorp Vault (MPL-2.0), maintaining full API and authentication compatibility. `type: openbao` targets the `bao` binary; secret paths, auth methods, and env vars are the same as Vault.

#### Environment variables

Same as HashiCorp Vault — see the [HashiCorp Vault](#hashicorp-vault) section above.

#### Auth methods

| Method  | Description                                                                |
| ------- | -------------------------------------------------------------------------- |
| Token   | Set `VAULT_TOKEN`. Most common method for automation.                      |
| AppRole | Obtain a token via `bao write auth/approle/login`; then set `VAULT_TOKEN`. |

#### Minimal YAML configuration

```yaml
type: openbao
spec:
  endpoints:
    address: https://bao.example.com
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

---

### Infisical

**CLI command:** `infisical` (optional — falls back to HTTP API when CLI is absent)  
**Install:** <https://infisical.com/docs/cli/overview>

#### Environment variables

| Variable                  | Purpose                                         | Required |
| ------------------------- | ----------------------------------------------- | -------- |
| `INFISICAL_TOKEN`         | Service token for direct authentication         | No       |
| `INFISICAL_CLIENT_ID`     | Client ID for universal auth (machine identity) | No       |
| `INFISICAL_CLIENT_SECRET` | Client secret for universal auth                | No       |
| `INFISICAL_PROJECT_ID`    | Project (workspace) ID to query                 | Yes      |
| `INFISICAL_ENVIRONMENT`   | Environment slug (defaults to `prod`)           | No       |

Either `INFISICAL_TOKEN` or the `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET` pair is required. Token takes priority.

Env-var names can be overridden via `authentication.api_key.api_key` (token) and `authentication.oauth2.*` (universal auth) in the integration spec.

#### Auth methods

| Method         | Description                                                                                |
| -------------- | ------------------------------------------------------------------------------------------ |
| Service token  | Set `INFISICAL_TOKEN`. Simple, suitable for CI/CD pipelines.                               |
| Universal auth | Set `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET`. Recommended for machine identities. |

#### Minimal YAML configuration

```yaml
type: infisical
spec:
  endpoints:
    address: https://app.infisical.com   # or self-hosted URL
```

#### Using as a secret or variable store

```yaml
secrets:
  - key: db_password
    store: infisical
    value: DB_PASSWORD             # secret name in Infisical

variables:
  - key: feature_timeout
    store: infisical
    value: FEATURE_TIMEOUT
```

---

### etcd

**CLI command:** `etcdctl` (optional — falls back to v3 HTTP API when CLI is absent)  
**Install:** <https://etcd.io/docs/latest/install/>

#### Environment variables

| Variable         | Purpose                                                      | Required |
| ---------------- | ------------------------------------------------------------ | -------- |
| `ETCD_ENDPOINTS` | Comma-separated etcd endpoint URLs (e.g. `http://host:2379`) | No       |
| `ETCD_USERNAME`  | Username for basic authentication                            | No       |
| `ETCD_PASSWORD`  | Password for basic authentication                            | No       |
| `ETCD_CA_FILE`   | CA certificate file path for TLS verification                | No       |
| `ETCD_CERT_FILE` | Client certificate file for mutual TLS                       | No       |
| `ETCD_KEY_FILE`  | Client private key file for mutual TLS                       | No       |

Defaults to `http://127.0.0.1:2379` if no endpoint is configured. The standard `ETCDCTL_ENDPOINTS`, `ETCDCTL_CACERT`, `ETCDCTL_CERT`, and `ETCDCTL_KEY` env vars are also honoured.

#### Auth methods

| Method     | Description                                                 |
| ---------- | ----------------------------------------------------------- |
| Anonymous  | No auth — suitable for trusted networks / development only. |
| Basic auth | Set `ETCD_USERNAME` + `ETCD_PASSWORD`.                      |
| mTLS       | Set `ETCD_CA_FILE` + `ETCD_CERT_FILE` + `ETCD_KEY_FILE`.    |

#### Minimal YAML configuration

```yaml
type: etcd
spec:
  endpoints:
    address: http://etcd.example.com:2379
```

#### Using as a variable or KV store

```yaml
variables:
  - key: db_host
    store: etcd
    value: /config/myapp/db_host   # etcd key path
```

---

### Flagsmith

**CLI command:** none (API-only — no CLI binary required)  
**Docs:** <https://flagsmith.com/docs>

Flagsmith is checked for availability by probing the HTTP API (`GET /api/v1/flags/`) rather than looking for a binary. `ensure_available()` fails fast if `FLAGSMITH_ENVIRONMENT_KEY` is not set or the API endpoint is unreachable.

`set_feature()` always returns `False` — the environment API key is read-only. Flag modifications require the management API with a server-side key.

#### Environment variables

| Variable                    | Purpose                                          | Required |
| --------------------------- | ------------------------------------------------ | -------- |
| `FLAGSMITH_ENVIRONMENT_KEY` | Environment API key (`X-Environment-Key` header) | Yes      |

The env-var name can be overridden via `authentication.api_key.api_key` in the integration spec.

#### Auth methods

| Method              | Description                                                                          |
| ------------------- | ------------------------------------------------------------------------------------ |
| Environment API key | Set `FLAGSMITH_ENVIRONMENT_KEY` to the environment key from the Flagsmith dashboard. |

#### Minimal YAML configuration

```yaml
type: flagsmith
spec:
  endpoints:
    address: https://edge.api.flagsmith.com   # or self-hosted URL
```

#### Using as a feature flag store

```yaml
features:
  - key: dark_mode
    store: flagsmith
    value: dark_mode              # flag name in Flagsmith

  - key: max_upload_size
    store: flagsmith
    value: max_upload_size        # returns feature_state_value if set
```
