# Azure Key Vault Integration

Prerequisites
- Azure subscription with Key Vault access
- Key Vault created in Azure Portal (or create one)
- Service Principal or Managed Identity with required permissions

Authentication Methods
1. OIDC / Federated Identity (recommended for CI/CD)
   - AZURE_TENANT_ID: Azure Tenant ID
   - AZURE_CLIENT_ID: Application (client) ID
   - AZURE_SUBSCRIPTION_ID: Azure Subscription ID
   - KEYVAULT_URL: Key Vault URL (e.g., https://myvault.vault.azure.net/)
   - Use cases: GitHub Actions, Azure Pipelines, Managed Identity

2. Service Principal with Client Secret (local/dev)
   - AZURE_TENANT_ID
   - AZURE_CLIENT_ID
   - AZURE_CLIENT_SECRET
   - KEYVAULT_URL

Connection parameters

What to set for xyz-platform to connect to Azure Key Vault:

- Required (one of):
   - `KEYVAULT_URL` environment variable (e.g., `https://myvault.vault.azure.net/`), or
   - `endpoints.address` in the integration YAML (supports `${ENV_VAR}` substitution).

- Authentication (choose one):
   - OIDC / Federated Identity:
      - `AZURE_TENANT_ID` — tenant id
      - `AZURE_CLIENT_ID` — client/app id
      - `AZURE_SUBSCRIPTION_ID` — subscription id (used by some flows)
   - Service principal (client secret):
      - `AZURE_TENANT_ID`
      - `AZURE_CLIENT_ID`
      - `AZURE_CLIENT_SECRET`

Examples — integration YAML (env-var references or literals):

```yaml
integration:
   name: keyvault
   type: azure-keyvault
   endpoints:
      address: ${KEYVAULT_URL}
   authentication:
      method: oauth2
      oauth2:
         tenant_id: AZURE_TENANT_ID
         client_id: AZURE_CLIENT_ID
         client_secret: AZURE_CLIENT_SECRET
```

Notes
- The integration will first try Azure CLI tokens (if `az login` was used), then the configured OAuth2/env vars.
- If you supply env-var names in the YAML (e.g., `tenant_id: "MY_TENANT_ENV"`), the integration reads those env vars.

Configuration Notes
- Set only one auth method at a time.
- Ensure Key Vault access policy allows Get/List for the principal.
- Test connection: `az keyvault secret list --vault-name <vault-name>`

Common Issues
- Missing KEYVAULT_URL or malformed URL
- Insufficient Key Vault permissions
- Mixing auth methods (e.g., client secret + federated)

Docs
- https://learn.microsoft.com/azure/key-vault/
- https://learn.microsoft.com/azure/active-directory/develop/