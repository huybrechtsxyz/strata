# Azure CLI (`az`) as a First-Class Integration

- Status: implemented (all phases complete)
- Date: 2026-07-23
- Related: ADR-0046 (Bicep provisioner), ADR-0051 (Checkov pattern)

## Context and Problem Statement

strata's Azure support is currently spread across:

| Component               | How it uses Azure                                        |
| ----------------------- | -------------------------------------------------------- |
| `azure_keyvault.py`     | REST API + `az` CLI for secret resolution                |
| `azure_appconfig.py`    | REST API + `az` CLI for variable/feature flag resolution |
| `terraform_deployer.py` | Assumes Azure CLI auth for `azurerm` provider            |
| ADR-0046 (Bicep)        | Needs `az deployment group create`                       |

None of these check whether `az` is actually installed, logged in, or targeting the right
subscription. Each integration independently attempts Azure operations and fails with
unhelpful subprocess errors when `az` is unavailable or not authenticated.

**The opportunity:** `az` is a single entry point to the entire Azure platform. One integration
that validates availability and authentication gives all Azure-related features a shared
foundation — including the proposed Bicep provisioner (ADR-0046).

## What `az` Enables Beyond Bicep

| Capability               | `az` subcommand                     | Use in strata                                 |
| ------------------------ | ----------------------------------- | --------------------------------------------- |
| Bicep/ARM deployments    | `az deployment group/sub/mg create` | Bicep provisioner (ADR-0046)                  |
| Deployment drift         | `az deployment group what-if`       | `strata deploy drift` for Bicep               |
| Deployment history       | `az deployment group list`          | `strata deploy history` for Bicep             |
| AKS credentials          | `az aks get-credentials`            | Pre-deploy setup for Helm/ArgoCD              |
| Container registry       | `az acr login`, `az acr build`      | Container push before deploy                  |
| Resource group lifecycle | `az group create/delete`            | Subscription-scope Bicep                      |
| Subscription context     | `az account show/set`               | Multi-subscription fleet management           |
| Managed identity token   | `az account get-access-token`       | Token for REST API calls when SDK unavailable |
| Monitor / Log Analytics  | `az monitor metrics/logs`           | Health checks, observability                  |

## Relationship to Existing Azure Integrations

### Current: SDK-first, `az` as fallback

```
AzureKeyVaultIntegration    → REST API (urllib) → uses az CLI token if available
AzureAppConfigIntegration   → REST API (urllib) → uses az CLI token if available
```

Both integrations independently resolve OAuth tokens — either from environment variables
(`AZURE_CLIENT_ID` + secret/OIDC) or by shelling out to `az account get-access-token`.

### Proposed: `AzureCLIIntegration` as shared auth foundation

```
AzureCLIIntegration(BaseIntegration)
    COMMAND = "az"
    ensure_available()     → az account show (confirms login + subscription)
    get_access_token()     → az account get-access-token --resource <scope>
    get_subscription()     → current subscription id + name
    ↓ Used by:
    ├── BicepDeployer           (az deployment group create)
    ├── AzureKeyVaultIntegration (token resolution fallback)
    ├── AzureAppConfigIntegration (token resolution fallback)
    ├── AKS credential setup     (az aks get-credentials)
    └── ACR login                (az acr login)
```

**Key principle:** `AzureCLIIntegration` is a tool-availability + auth check, not a
replacement for the existing SDK-based integrations. KeyVault and AppConfig continue using
REST directly (faster, no subprocess per secret). The CLI integration provides:

1. **Tools view status** — "Azure CLI ✅ logged in (subscription: my-sub)" vs "❌ not logged in"
2. **Token caching** — `get_access_token()` caches the token for the session, avoids repeated `az` calls
3. **Subscription validation** — confirms the right subscription is active before deploy
4. **Shared foundation for Bicep deployer** — doesn't need its own CLI bootstrapping

### Impact on existing integrations

| Integration                  | Change needed?               | How                                          |
| ---------------------------- | ---------------------------- | -------------------------------------------- |
| `AzureKeyVaultIntegration`   | No (Phase 1)                 | Continues working as-is                      |
| `AzureAppConfigIntegration`  | No (Phase 1)                 | Continues working as-is                      |
| Terraform `azurerm` provider | No                           | Uses own auth (env vars or `az login`)       |
| Bicep (ADR-0046)             | **Uses AzureCLIIntegration** | `az deployment` commands via the integration |

Future (Phase 2): KeyVault and AppConfig could delegate their `_get_access_token()` to
`AzureCLIIntegration.get_access_token()` instead of duplicating the `az account get-access-token`
subprocess call. This would centralize token caching and reduce subprocess spawns.

## Design

### `AzureCLIIntegration(BaseIntegration)`

```python
class AzureCLIIntegration(BaseIntegration):
    COMMAND = "az"
    CAPABILITIES = []  # No shared protocol yet; capability name: "azure"

    def ensure_available(self) -> Tuple[bool, str]:
        """Check az is installed AND authenticated (az account show succeeds)."""

    def get_subscription(self) -> Optional[Dict[str, str]]:
        """Return {id, name, tenantId} from az account show."""

    def get_access_token(self, resource: str = "https://management.azure.com") -> Optional[str]:
        """Return a cached bearer token for the given resource scope."""

    def bicep_version(self) -> Optional[str]:
        """Return az bicep version string (None if not installed)."""
```

### Configuration YAML

```yaml
integrations:
  - name: azure
    type: azure_cli
    capabilities: [azure]
    required: true        # or false — depends on whether Azure is the target
    validation:
      command: az account show
```

No `endpoints` or `authentication` block needed — `az` uses its own credential chain
(managed identity → service principal env vars → interactive login).

### Tools view output

```
azure_cli    2.61.0    ✅    (subscription: my-sub-name)
az bicep     0.28.1    ✅    (installed via az)
```

When not logged in:
```
azure_cli    2.61.0    ⚠️    not authenticated (run: az login)
```

### What `ensure_available()` checks

1. `az` binary in PATH → if not: "Azure CLI not installed"
2. `az account show --output json` succeeds → if not: "Not authenticated (run `az login`)"
3. Returns subscription name for display in Tools view

This is a stronger check than most integrations (which only verify the binary exists).
For Azure, binary-without-login is useless — operators need to know immediately.

## Implementation Plan

### Phase 1 — Integration + Bicep deployer foundation ✅
1. `src/strata/integrations/azure_cli.py` — `AzureCLIIntegration` ✅
2. `ProvisionerType.BICEP = "bicep"` added to enum ✅
3. Register `azure_cli` in `IntegrationFactory._BUILTIN_CLASS_MAP` ✅
4. Help file: `src/strata/data/help/azure_cli.md` ✅
5. Tests for `ensure_available()`, `get_subscription()`, `get_access_token()` ✅ (20 tests)

### Phase 2 — Bicep deployer ✅
- `BicepDeployer(BaseDeployer)` in `src/strata/deployers/bicep_deployer.py` ✅
- All `az deployment {scope}` commands routed through `AzureCLIIntegration.run_az()` ✅
- Steps: `setup` (`az bicep build`), `plan` (`what-if`), `apply` (`create`), `destroy` (`delete`), `output` ✅
- Four ARM scopes: `resourceGroup`, `subscription`, `managementGroup`, `tenant` ✅
- Registered in `DeployerFactory._BUILTIN_MAP` ✅
- Help file: `src/strata/data/help/bicep.md` ✅
- `docs/config/workspace.md` updated ✅
- 27 tests ✅

### Phase 3 — Token unification ✅
- `AzureKeyVaultIntegration._get_access_token_via_cli()` delegates to `AzureCLIIntegration.get_access_token("https://vault.azure.net")` ✅
- `AzureAppConfigIntegration._get_access_token_via_cli()` delegates to `AzureCLIIntegration.get_access_token("https://azconfig.io")` ✅
- Single cached token per resource scope per session — second call is instant ✅
- Zero regressions — existing auth fallback chain unchanged, no YAML config changes needed

## Consequences

### Positive
- **Single source of truth** for Azure CLI availability and auth status
- **Tools view** shows clear "logged in / not logged in" at a glance
- **Bicep deployer** gets a pre-validated `az` foundation — no bootstrap complexity
- **Token caching** reduces subprocess calls when multiple Azure integrations are active
- **Subscription context** — operators know which subscription will be targeted before deploy

### Negative
- `az account show` is slightly slower than a simple `az --version` check (~500ms)
- Operators with multiple subscriptions must `az account set` before strata — but this is already true for Terraform

### Neutral
- Existing KeyVault/AppConfig integrations continue to work unchanged (no migration needed)
- `az bicep version` is a sub-check — Bicep binary is installed separately via `az bicep install`
