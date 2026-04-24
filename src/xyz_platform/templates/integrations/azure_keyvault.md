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