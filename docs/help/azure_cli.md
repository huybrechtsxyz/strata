# Azure CLI Integration

The Azure CLI integration (`type: azure_cli`) is the shared foundation for all
Azure CLI-based operations in strata. It checks that `az` is installed **and
authenticated**, exposes subscription context, and provides cached access tokens for
ARM/Bicep and other Azure services.

Installation
```
# macOS
brew install azure-cli

# Linux
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash

# Windows (winget)
winget install Microsoft.AzureCLI

# Windows (MSI)
https://learn.microsoft.com/en-us/cli/azure/install-azure-cli-windows
```

Verify install
```
az --version
```

Authentication

| Method                            | Setup                                                           |
| --------------------------------- | --------------------------------------------------------------- |
| **Interactive login** (local dev) | `az login`                                                      |
| **Managed Identity**              | Automatic on Azure compute — no env vars                        |
| **Service principal (secret)**    | Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` |
| **OIDC / Workload Identity**      | Set `AZURE_TENANT_ID`, `AZURE_CLIENT_ID` — omit secret          |

Verify you are logged in and targeting the right subscription:
```
az account show
az account list --output table
az account set --subscription <id-or-name>
```

Configuration YAML

```yaml
integrations:
  - name: azure
    type: azure_cli
    capabilities: [azure]
    required: true         # true if you use Bicep or Azure-specific commands
```

What `ensure_available()` checks

1. `az` binary in PATH — if not: "Azure CLI not installed"
2. `az account show` succeeds — if not: "Not authenticated (run: az login)"

This is a stronger check than most integrations (binary + login). The Tools view shows:
- ✅ `azure_cli — Logged in (subscription: my-subscription)`
- ❌ `azure_cli — not authenticated (run: az login)`
- ❌ `azure_cli — not found`

Bicep CLI extension (optional)

Bicep is installed as a separate extension to Azure CLI:
```
az bicep install
az bicep version
az bicep upgrade
```

The integration reports the Bicep extension version separately in `strata tools check azure_cli`.

Subscription management
```
az account list --output table           # list all subscriptions
az account set --subscription <id>       # switch active subscription
az account show --output json            # current subscription details
```

Docs
- Azure CLI: https://learn.microsoft.com/en-us/cli/azure/
- Install guide: https://learn.microsoft.com/en-us/cli/azure/install-azure-cli
- Authentication: https://learn.microsoft.com/en-us/cli/azure/authenticate-azure-cli
