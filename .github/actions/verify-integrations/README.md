# Verify Integrations

Configure store environment variables and verify integration availability via `strata tools status`. Masks all secret inputs before writing them to the environment.

## Usage

```yaml
- uses: huybrechtsxyz/strata/.github/actions/setup-strata@v1
- uses: huybrechtsxyz/strata/.github/actions/azure-login@v1   # if using Azure stores
  with:
    azure_tenant_id: ${{ vars.AZURE_TENANT_ID }}
    azure_client_id: ${{ vars.AZURE_CLIENT_ID }}
- uses: huybrechtsxyz/strata/.github/actions/verify-integrations@v1
  with:
    bitwarden_token: ${{ secrets.BITWARDEN_TOKEN }}
    azure_tenant_id: ${{ vars.AZURE_TENANT_ID }}
    azure_subscription_id: ${{ vars.AZURE_SUBSCRIPTION_ID }}
    azure_client_id: ${{ vars.AZURE_CLIENT_ID }}
    azure_client_secret: ${{ secrets.AZURE_CLIENT_SECRET }}
    vault_address: ${{ vars.VAULT_ADDR }}
    vault_token: ${{ secrets.VAULT_TOKEN }}
```

## Inputs

| Input                   | Required | Default | Description                                                   |
| ----------------------- | -------- | ------- | ------------------------------------------------------------- |
| `bitwarden_token`       | No       | —       | Bitwarden Secrets Manager access token (`BWS_ACCESS_TOKEN`)   |
| `azure_tenant_id`       | No       | —       | Azure tenant ID (`AZURE_TENANT_ID`)                           |
| `azure_subscription_id` | No       | —       | Azure subscription ID (`AZURE_SUBSCRIPTION_ID`)               |
| `azure_client_id`       | No       | —       | Azure service principal client ID (`AZURE_CLIENT_ID`)         |
| `azure_client_secret`   | No       | —       | Azure service principal client secret (`AZURE_CLIENT_SECRET`) |
| `vault_address`         | No       | —       | HashiCorp Vault server address (`VAULT_ADDR`)                 |
| `vault_token`           | No       | —       | HashiCorp Vault token (`VAULT_TOKEN`)                         |
| `consul_address`        | No       | —       | Consul server address (`CONSUL_HTTP_ADDR`)                    |
| `consul_token`          | No       | —       | Consul ACL token (`CONSUL_HTTP_TOKEN`)                        |

## What it does

1. Masks all secret inputs (`::add-mask::`) so they never appear in logs
2. Writes the matching env var(s) for each store you supply credentials for — stores with no credentials are simply skipped (not an error)
3. Runs `strata tools status --output json` and prints a per-integration availability table to the step summary

## Notes

- Each store requires its **full** credential set (e.g. both `vault_address` and `vault_token`) — supplying only one produces a warning, not a hard failure.
- Azure supports two modes: client-secret (`azure_client_id` + `azure_client_secret`) or OIDC (`azure_client_id` only, requires `azure-login` to have run first).
