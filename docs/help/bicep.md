# Bicep Provisioner

Bicep is Microsoft's Azure-native IaC language. It compiles to ARM (Azure Resource Manager)
templates and is deployed via the Azure CLI. Unlike Terraform, **no state file or backend
is required** — ARM manages deployment state server-side.

Prerequisites
- Azure CLI (`az`) installed and authenticated (`az login`)
- Bicep CLI extension: `az bicep install`

Verify
```
az --version
az bicep version
```

Workspace YAML

```yaml
provisioners:
  - name: infrastructure
    provisioner: bicep
    source:
      repository: my-repo
      source_path: bicep            # directory containing main.bicep
    configuration:
      scope: resourceGroup          # resourceGroup (default) | subscription | managementGroup | tenant
      resource_group: my-rg         # required when scope=resourceGroup
      location: westeurope          # required for subscription | managementGroup | tenant
      management_group_id: mg-root  # required when scope=managementGroup
      deployment_name: strata-infra # optional: ARM deployment name (default: strata-{stage})
      parameters_file: params.json  # optional: relative to source_path
      mode: Incremental             # Incremental (default) | Complete (resourceGroup only)
```

No `backend:` block needed — Bicep does not use a state backend.

Deployment scopes

| `scope`                   | ARM command                   | Required fields                   |
| ------------------------- | ----------------------------- | --------------------------------- |
| `resourceGroup` (default) | `az deployment group create`  | `resource_group`                  |
| `subscription`            | `az deployment sub create`    | `location`                        |
| `managementGroup`         | `az deployment mg create`     | `location`, `management_group_id` |
| `tenant`                  | `az deployment tenant create` | `location`                        |

Steps executed by strata

| Step      | Azure CLI command                                       | Notes                               |
| --------- | ------------------------------------------------------- | ----------------------------------- |
| `setup`   | `az bicep build --file main.bicep`                      | Syntax validation + module bundling |
| `check`   | same as setup                                           | Alias                               |
| `plan`    | `az deployment {scope} what-if`                         | ARM change preview                  |
| `apply`   | `az deployment {scope} create`                          | Deploy                              |
| `destroy` | `az deployment {scope} delete`                          | Remove ARM deployment record        |
| `output`  | `az deployment {scope} show --query properties.outputs` | Read ARM outputs                    |

Typical deploy sequence
```
strata build run  -f deploy/deploy-prd.yaml   # setup + check (az bicep build)
strata deploy run -f deploy/deploy-prd.yaml   # plan + apply
```

Drift detection
```
strata deploy drift run -f deploy/deploy-prd.yaml  # runs az deployment group what-if
```

Comparison to Terraform

|                             | Terraform             | Bicep                          |
| --------------------------- | --------------------- | ------------------------------ |
| State file                  | Required (`.tfstate`) | None — ARM manages server-side |
| Backend config              | Required              | Not needed                     |
| `terraform init` equivalent | Yes                   | Not required                   |
| Multi-cloud                 | ✅                     | Azure only                     |
| New Azure resources         | Depends on provider   | Day-one via ARM API            |

Docs
- Bicep overview: https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/overview
- Install Bicep: `az bicep install`
- ARM what-if: https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if
