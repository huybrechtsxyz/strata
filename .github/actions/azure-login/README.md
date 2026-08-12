# Azure Login

Authenticate with Azure using OIDC (preferred, no secrets) or client secret (fallback). Mode is selected automatically based on which inputs are supplied.

## Usage

```yaml
# OIDC (preferred) — requires id-token: write and a federated credential in Entra ID
permissions:
  id-token: write
  contents: read

steps:
  - uses: huybrechtsxyz/strata/.github/actions/azure-login@v1
    with:
      azure_tenant_id: ${{ vars.AZURE_TENANT_ID }}
      azure_subscription_id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      azure_client_id: ${{ vars.AZURE_CLIENT_ID }}
      # azure_client_secret omitted — triggers OIDC mode
```

```yaml
# Client secret (fallback) — no federated credential setup needed
steps:
  - uses: huybrechtsxyz/strata/.github/actions/azure-login@v1
    with:
      azure_tenant_id: ${{ vars.AZURE_TENANT_ID }}
      azure_subscription_id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
      azure_client_id: ${{ vars.AZURE_CLIENT_ID }}
      azure_client_secret: ${{ secrets.AZURE_CLIENT_SECRET }}
```

## Inputs

| Input                   | Required | Default | Description                                          |
| ----------------------- | -------- | ------- | ---------------------------------------------------- |
| `azure_tenant_id`       | No       | —       | Azure tenant ID                                      |
| `azure_subscription_id` | No       | —       | Azure subscription ID                                |
| `azure_client_id`       | No       | —       | Azure service principal / app registration client ID |
| `azure_client_secret`   | No       | —       | Azure client secret. Omit to use OIDC.               |

## Mode selection

| `azure_client_id` | `azure_client_secret` | Mode                                    |
| ----------------- | --------------------- | --------------------------------------- |
| set               | empty                 | OIDC (no secret stored in GitHub)       |
| set               | set                   | Client secret (secret stored in GitHub) |
| empty             | —                     | Skipped — no Azure auth performed       |

## Requirements for OIDC mode

The calling job must declare `permissions: id-token: write`, and a federated credential must be configured in Azure Entra ID for this repo/environment/branch. See [Setup Azure OIDC](../../../docs/guides/setup-azure-oidc.md) for the full setup guide.
