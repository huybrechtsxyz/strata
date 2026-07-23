# Bicep Provisioner as Azure-Native Terraform Alternative

- Status: implemented
- Date: 2026-07-20
- Implemented: 2026-07-23

## Context and Problem Statement

strata currently supports seven provisioner types: `terraform`, `ansible`, `script`, `compose`, `helm`, `argocd`, `flux`. For Azure infrastructure provisioning, `terraform` is the default IaC choice.

[Bicep](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/overview) is Microsoft's domain-specific language for deploying Azure resources declaratively. It compiles to ARM (Azure Resource Manager) templates and is supported natively by Azure CLI, Azure DevOps, and GitHub Actions. Many Azure-exclusive organisations use Bicep rather than Terraform because:

- **No state file** — ARM manages state server-side; there is no `.tfstate` to store, lock, or corrupt
- **No backend** — no Terraform Cloud, Azure Storage Account, or S3 bucket configuration required
- **Better Azure type coverage** — Bicep types are auto-generated from the ARM API; new Azure resources are available in Bicep on day one, sometimes weeks before a Terraform provider update ships
- **Simpler authentication** — relies entirely on `az login` / Azure managed identity; no provider version pinning
- **Lower operational overhead** — no state drift, no `terraform init`, no provider caching

As strata's operator base includes Azure-exclusive teams, there is growing demand for a Bicep provisioner that fits the existing workspace model without requiring Terraform knowledge.

## Key Differences vs Terraform

| Aspect                          | Terraform                                  | Bicep                                             |
| ------------------------------- | ------------------------------------------ | ------------------------------------------------- |
| State management                | `.tfstate` file (requires backend storage) | ARM server-side — no file                         |
| Multi-cloud                     | ✅ AWS, Azure, GCP, on-prem                 | ❌ Azure-only                                      |
| Language                        | HCL                                        | Bicep DSL (compiles → ARM JSON)                   |
| Drift detection                 | `terraform plan`                           | `what-if` (ARM deployment preview)                |
| Lock mechanism                  | Provider-specific backends                 | ARM deployment operations are serialised natively |
| Provider version pinning        | Required (`required_providers`)            | Not required — always targets ARM API directly    |
| New Azure resource availability | Depends on provider release cycle          | Day-one via `az bicep upgrade`                    |
| `strata deploy status`          | Reads `.tfstate` / Terraform Cloud API     | Queries ARM deployment history                    |
| `strata deploy drift`           | `terraform plan -detailed-exitcode`        | `az deployment group what-if`                     |

## Impact on strata Architecture

### Build phase (`strata build run`)
Bicep requires a separate build step only when using **modules** (`bicep build` produces `.json` ARM templates). For single-file deployments (`main.bicep`), no explicit build step is needed — Azure CLI compiles on the fly at deploy time. strata's build phase would:
- Validate Bicep syntax: `az bicep build --file main.bicep --stdout` (exit 1 on error)
- Resolve and bundle modules if present
- Produce `platform.json` artifact that references the compiled ARM template path

### Deploy phase (`strata deploy run`)

Bicep supports four ARM deployment scopes. The scope determines which Azure CLI command is used:

| Scope             | Use case                                                 | Azure CLI command             |
| ----------------- | -------------------------------------------------------- | ----------------------------- |
| `resourceGroup`   | Deploy resources into an existing resource group         | `az deployment group create`  |
| `subscription`    | Create resource groups, apply policies, role assignments | `az deployment sub create`    |
| `managementGroup` | Tenant-wide governance, policy, RBAC                     | `az deployment mg create`     |
| `tenant`          | Root-level tenant management                             | `az deployment tenant create` |

Most workloads use `resourceGroup` (default). Teams provisioning resource groups themselves, or applying subscription-level policies, need `subscription` scope. Enterprise teams managing governance across multiple subscriptions use `managementGroup`.

```bash
# resourceGroup scope (default)
az deployment group create \
  --resource-group <rg> \
  --template-file main.bicep \
  --parameters @params.json \
  --mode Incremental

# subscription scope (creates resource groups, applies policies)
az deployment sub create \
  --location <location> \
  --template-file main.bicep \
  --parameters @params.json

# managementGroup scope
az deployment mg create \
  --management-group-id <mg-id> \
  --location <location> \
  --template-file main.bicep
```

### Lock mechanism
Terraform uses backend-specific locks (Azure Storage blob lease, Terraform Cloud lock). Bicep/ARM has **no equivalent client-side lock** — ARM serialises concurrent deployments to the same resource group natively. strata's file-based lock (`deploy lock`) would still be used for coordinating strata-level operations (prevent two strata processes from deploying to the same target simultaneously), but Terraform backend locking is not applicable.

The `backend:` block in workspace YAML is Terraform-specific. For Bicep, it would not be present (or would be omitted/ignored).

### Status and drift
| strata command   | Terraform implementation            | Bicep implementation                                     |
| ---------------- | ----------------------------------- | -------------------------------------------------------- |
| `deploy status`  | Reads Terraform workspace state     | `az deployment group show --name <last-deployment>`      |
| `deploy drift`   | `terraform plan -detailed-exitcode` | `az deployment group what-if --template-file main.bicep` |
| `deploy history` | Terraform workspace run history     | `az deployment group list --resource-group <rg>`         |

### `ProvisionerType` enum addition
```python
class ProvisionerType(str, Enum):
    TERRAFORM = "terraform"
    ANSIBLE   = "ansible"
    SCRIPT    = "script"
    COMPOSE   = "compose"
    HELM      = "helm"
    ARGOCD    = "argocd"
    FLUX      = "flux"
    BICEP     = "bicep"          # ← new
```

### Workspace YAML shape

The `scope` field determines the ARM deployment target. `resource_group` is only required when scope is `resourceGroup`; `location` is required for `subscription`, `managementGroup`, and `tenant` scopes.

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: workspace
spec:
  provisioners:
    - name: infrastructure
      provisioner: bicep
      source:
        repository: my-repo
        source_path: bicep           # directory containing main.bicep
      configuration:
        scope: resourceGroup         # resourceGroup (default) | subscription | managementGroup | tenant
        resource_group: my-rg        # required when scope = resourceGroup
        location: westeurope         # required when scope = subscription | managementGroup | tenant
        management_group_id: mg-root # required when scope = managementGroup
        deployment_name: strata-deploy  # optional: ARM deployment name
        parameters_file: params.json    # optional: parameters file path
        mode: Incremental            # optional: Incremental (default) or Complete (resourceGroup only)
```

`backend:` is absent — no state storage is required.

**Scope validation rules** (enforced at `strata validate` time):

| scope             | required fields                   | forbidden fields                        |
| ----------------- | --------------------------------- | --------------------------------------- |
| `resourceGroup`   | `resource_group`                  | —                                       |
| `subscription`    | `location`                        | `resource_group`, `management_group_id` |
| `managementGroup` | `location`, `management_group_id` | `resource_group`                        |
| `tenant`          | `location`                        | `resource_group`, `management_group_id` |

## Considered Options

### Option A: No Bicep support — Terraform only
- Keep current `terraform` provisioner as the only IaC option
- Operators wanting Bicep must wrap it via the `script` provisioner
- **Rejected:** `script` provisioner loses strata's structured `deploy status`, `deploy drift`, `deploy history` integrations; Bicep workloads are second-class citizens

### Option B: Bicep as a built-in provisioner type (CHOSEN)
- Add `ProvisionerType.BICEP = "bicep"` to the enum
- Implement `BicepBuilder` and `BicepDeployer` following the existing `Terraform*` pattern
- Wire into the ADR-0023 pluggable dispatcher
- **Chosen:** First-class Bicep support with full strata integration (build, deploy, status, drift, history)

### Option C: Bicep as a workspace plugin (ADR-0023)
- Users drop a `bicep_deployer.py` into `.strata/deployers/`
- No changes to strata core
- **Deferred:** ADR-0023 pluggable deployer framework is not yet complete; plugin API is not stable. Bicep is common enough to warrant built-in support. Revisit if the plugin framework ships first.

## Decision Outcome

Chosen: **Option B — Bicep as a built-in provisioner type.**

## Implementation Roadmap

### Phase 1 — Enum and model (no behaviour change)
1. Add `ProvisionerType.BICEP = "bicep"` to `common_models.py`
2. Add `bicep` to `WorkspaceIacModel` validator (`validate_provisioner_fields`) — mark `source` as required for Bicep (same as Terraform)
3. Add `configuration.scope` field (default `resourceGroup`) with cross-field validation: `resource_group` required for `resourceGroup`; `location` required for `subscription`/`managementGroup`/`tenant`; `management_group_id` required for `managementGroup`
4. Schema update: add Bicep to `workspace` kind schema documentation

### Phase 2 — Build integration
1. `BicepBuilder` — validates Bicep syntax (`az bicep build`); bundles modules; writes `platform.json` artifact with template path and resolved scope reference
2. Wire into `_create_builder()` in the build command dispatcher
3. `strata build plan` — runs scope-appropriate `az deployment {scope} what-if` as a dry-run equivalent

### Phase 3 — Deploy integration
1. `BicepDeployer` — dispatches to scope-appropriate `az deployment {scope} create`; parses ARM deployment result; populates `_output_data`
2. `BicepDeployer.status()` — queries `az deployment {scope} show`
3. `BicepDeployer.drift()` — runs `az deployment {scope} what-if`
4. `BicepDeployer.history()` — queries `az deployment {scope} list`
5. Wire into `_create_deployer()` in the deploy command dispatcher

### Phase 4 — Tooling check
1. `strata tools status` — add `az bicep version` check (Bicep CLI is a separate install from Azure CLI)
2. `strata tools install` — add `az bicep install` step for Bicep provisioner workspaces

## Consequences

### Positive
- Azure-exclusive teams can use strata without a Terraform prerequisite
- Eliminates Terraform backend configuration burden for Azure-only deployments
- Full `deploy status`, `deploy drift`, `deploy history` integration via ARM APIs
- No state file management, no `.tfstate` corruption risk

### Negative
- Bicep is Azure-only — not applicable to multi-cloud or on-premises workloads
- ARM `what-if` (drift detection) is less precise than `terraform plan` for complex dependency graphs
- Azure CLI Bicep extension must be separately installed (`az bicep install`)
- No equivalent to Terraform workspaces for environment isolation — Bicep relies on resource group separation

### Out of scope
- `ProvisionerType.ARM` (raw ARM JSON) — Bicep supersedes raw ARM templates; not added
- Pulumi, CDK — separate ADR if needed

## More Information

- [Bicep documentation](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/overview)
- [Bicep vs Terraform comparison](https://learn.microsoft.com/en-us/azure/azure-resource-manager/bicep/compare-template-syntax)
- [az deployment group what-if](https://learn.microsoft.com/en-us/azure/azure-resource-manager/templates/deploy-what-if)
- Related ADRs: ADR-0023 (pluggable provisioner framework), ADR-0002 (Python Click CLI), ADR-0041 (GitOps controller integration)
