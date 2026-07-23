# Azure Lifecycle Script Base

`AzureScript` is a Python base class for lifecycle scripts in `.strata/scripts/`. It
wraps the Azure CLI (`az`) with helpers for running commands, reading strata environment
variables, and failing cleanly when az operations fail.

## Three built-in scripts (no code needed)

Reference these directly from workspace YAML lifecycle hooks:

```yaml
lifecycle:
  pre_deploy:
    scripts:
      - strata://azure_aks_credentials.py      # az aks get-credentials
      - strata://azure_acr_login.py            # az acr login
  pre_provision:
    scripts:
      - strata://azure_resource_group_ensure.py  # az group create (idempotent)
```

### `azure_aks_credentials.py`
Required env vars: `AKS_CLUSTER`, `AKS_RESOURCE_GROUP`
Optional: `AKS_SUBSCRIPTION`, `AKS_ADMIN_CREDENTIALS=true`, `AKS_CONTEXT_NAME`

### `azure_acr_login.py`
Required: `ACR_NAME`
Optional: `ACR_SUBSCRIPTION`, `ACR_EXPOSE_TOKEN=true`

### `azure_resource_group_ensure.py`
Required: `AZURE_RESOURCE_GROUP`, `AZURE_LOCATION`
Optional: `AZURE_SUBSCRIPTION`, `AZURE_RG_TAGS=key=val,key2=val2`

## Write a custom script

```python
# .strata/scripts/my_script.py
from strata.utils.azure_script_base import AzureScript

class MyScript(AzureScript):
    def run(self):
        rg = self.require_env("AZURE_RESOURCE_GROUP")
        result = self.run_az(["group", "show", "--name", rg])
        self.exit_on_failure(result, "az group show")
        self.log(f"Resource group {rg} found")

if __name__ == "__main__":
    MyScript().execute()
```

## Available helpers

| Method                    | Description                                         |
| ------------------------- | --------------------------------------------------- |
| `run_az(args)`            | Run az subcommand; returns CompletedProcess         |
| `exit_on_failure(result)` | sys.exit(1) if returncode != 0                      |
| `require_env(name)`       | Get env var or exit(1) with error message           |
| `env(name, default="")`   | Get env var with default                            |
| `get_token(resource)`     | Cached bearer token via az account get-access-token |
| `workspace_path()`        | Path from STRATA_WORKSPACE_PATH                     |
| `build_path()`            | Path from STRATA_BUILD_PATH                         |
| `log(msg)`                | Print to stderr (visible in strata output)          |

New workspaces get `.strata/scripts/azure_lifecycle_example.py` with usage examples.

Docs: see `docs/guides/azure-lifecycle-scripts.md`
