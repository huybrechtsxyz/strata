# Azure App Configuration Integration

Prerequisites
- Azure subscription and App Configuration resource
- Access key or managed identity with appropriate permissions

Authentication
- Use `AZURE_APPCONFIG_CONNECTION_STRING` for connection-string auth
- Or use managed identity: `AZURE_CLIENT_ID` + tenant + subscription where applicable

Usage
- Store feature flags, runtime configuration values, and environment-specific settings
- Use label conventions for env separation (e.g., `dev`, `staging`, `prod`)

Common Commands
- List keys: `az appconfig kv list --name <config-name>`
- Push config: `az appconfig kv set --name <config-name> --key foo --value bar`

Troubleshooting
- Verify connection string or identity has access
- Check network rules on App Configuration resource

Docs
- https://learn.microsoft.com/azure/azure-app-configuration/