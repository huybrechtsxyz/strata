# Azure Lifecycle Scripts

Lifecycle scripts are Python files strata runs before or after deploy stages. The
`AzureScript` base class gives scripts pre-wired access to the Azure CLI — no setup
required.

## Quick start — use a built-in script

Reference built-in scripts directly from workspace YAML lifecycle hooks:

```yaml
lifecycle:
  pre_deploy:
    scripts:
      # Fetch AKS credentials so Helm / ArgoCD can reach the cluster
      - strata://azure_aks_credentials.py

      # Log Docker into Azure Container Registry before image push
      - strata://azure_acr_login.py

  pre_provision:
    scripts:
      # Ensure resource group exists before Bicep subscription-scope deploy
      - strata://azure_resource_group_ensure.py
```

> **Tip:** `strata://` references resolve to strata's built-in scripts directory at
> runtime. Copy any script to `.strata/scripts/` and modify it as needed.

---

## Built-in scripts

### `azure_aks_credentials.py`

Runs `az aks get-credentials --overwrite-existing` to merge cluster credentials
into the local kubeconfig. Use before Helm, ArgoCD, or Flux stages targeting AKS.

Required variables:

| Variable             | Description                           |
| -------------------- | ------------------------------------- |
| `AKS_CLUSTER`        | AKS cluster name                      |
| `AKS_RESOURCE_GROUP` | Resource group containing the cluster |

Optional variables:

| Variable                | Default             | Description                             |
| ----------------------- | ------------------- | --------------------------------------- |
| `AKS_SUBSCRIPTION`      | active subscription | Subscription ID override                |
| `AKS_ADMIN_CREDENTIALS` | `false`             | Set to `true` for `--admin` credentials |
| `AKS_CONTEXT_NAME`      | —                   | Override kubeconfig context name        |

```yaml
variables:
  - key: AKS_CLUSTER
    source: constant
    value: my-aks-cluster
  - key: AKS_RESOURCE_GROUP
    source: constant
    value: my-resource-group
```

---

### `azure_acr_login.py`

Runs `az acr login --name <registry>` so Docker push/pull commands work against
Azure Container Registry in the same stage.

Required variables:

| Variable   | Description                           |
| ---------- | ------------------------------------- |
| `ACR_NAME` | Registry name (without `.azurecr.io`) |

Optional:

| Variable           | Description                                      |
| ------------------ | ------------------------------------------------ |
| `ACR_SUBSCRIPTION` | Subscription ID override                         |
| `ACR_EXPOSE_TOKEN` | Set to `true` to also print the ACR access token |

```yaml
variables:
  - key: ACR_NAME
    source: constant
    value: myregistry
```

---

### `azure_resource_group_ensure.py`

Runs `az group create` — idempotent: if the resource group already exists the
command succeeds and makes no changes. Use before subscription-scope Bicep deploys.

Required variables:

| Variable               | Description                                |
| ---------------------- | ------------------------------------------ |
| `AZURE_RESOURCE_GROUP` | Resource group name                        |
| `AZURE_LOCATION`       | Azure region (e.g. `westeurope`, `eastus`) |

Optional:

| Variable             | Description                                                      |
| -------------------- | ---------------------------------------------------------------- |
| `AZURE_SUBSCRIPTION` | Subscription ID override                                         |
| `AZURE_RG_TAGS`      | Comma-separated `key=value` tags (e.g. `env=prod,team=platform`) |

```yaml
variables:
  - key: AZURE_RESOURCE_GROUP
    source: constant
    value: my-rg-prod
  - key: AZURE_LOCATION
    source: constant
    value: westeurope
```

---

## Write a custom script

Subclass `AzureScript` in `.strata/scripts/`:

```python
# .strata/scripts/pre_deploy_custom.py
from strata.utils.azure_script_base import AzureScript

class MyPreDeploy(AzureScript):
    def run(self):
        rg = self.require_env("AZURE_RESOURCE_GROUP")   # exits with error if absent
        subscription = self.env("AZURE_SUBSCRIPTION")   # empty string if absent

        # Run any az command
        args = ["group", "show", "--name", rg, "--output", "json"]
        if subscription:
            args += ["--subscription", subscription]

        result = self.run_az(args)
        self.exit_on_failure(result, "az group show")   # sys.exit(1) on failure

        self.log(f"Resource group '{rg}' confirmed")    # printed to strata output

if __name__ == "__main__":
    MyPreDeploy().execute()
```

Reference it in workspace YAML:

```yaml
lifecycle:
  pre_deploy:
    scripts:
      - .strata/scripts/pre_deploy_custom.py
```

---

## `AzureScript` reference

| Method                           | Description                                                      |
| -------------------------------- | ---------------------------------------------------------------- |
| `run_az(args, timeout=120)`      | Run `az` subcommand; returns `subprocess.CompletedProcess`       |
| `exit_on_failure(result, label)` | `sys.exit(1)` if `returncode != 0`; prints stdout/stderr         |
| `require_env(name)`              | Return env var value or `sys.exit(1)` with a clear error message |
| `env(name, default="")`          | Return env var value with optional default                       |
| `get_token(resource)`            | Cached bearer token via `az account get-access-token`            |
| `workspace_path()`               | `Path` from `STRATA_WORKSPACE_PATH`                              |
| `build_path()`                   | `Path` from `STRATA_BUILD_PATH`                                  |
| `stage_name()`                   | Stage name from `STRATA_STAGE_NAME`                              |
| `log(msg)`                       | Print to stderr — visible in strata console output               |

## Environment variables in all lifecycle scripts

strata injects these before running any script:

| Variable                 | Content                                          |
| ------------------------ | ------------------------------------------------ |
| `STRATA_PHASE`           | Current lifecycle phase (e.g. `pre_deploy`)      |
| `STRATA_WORKSPACE_PATH`  | Workspace root path                              |
| `STRATA_BUILD_PATH`      | Build artifacts directory                        |
| `STRATA_CONFIG_PATH`     | `.strata/` state directory                       |
| `STRATA_STAGE_NAME`      | Current stage name                               |
| _all resolved variables_ | Secrets and variables from the active deployment |
