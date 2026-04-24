# Azure App Configuration Integration

Prerequisites
- Azure subscription and App Configuration resource
- Access key or managed identity with appropriate permissions

Authentication
- Use `AZURE_APPCONFIG_CONNECTION_STRING` for connection-string auth
- Or use managed identity: `AZURE_CLIENT_ID` + tenant + subscription where applicable

Connection parameters

Set one of the following approaches to allow this integration to authenticate to your App Configuration store.

- Connection string (easy for local/dev):
	- Env var: `APPCONFIG_CONNECTION_STRING` (default) — value from the Azure portal, e.g.:

		```
		Endpoint=https://<your-appconfig>.azconfig.io;Id=<id>;Secret=<secret>
		```

	- The integration also supports overriding the env-var name via the integration YAML (see examples below).

- App Configuration endpoint + Azure AD (recommended for production):
	- Endpoint: set `endpoints.address` in the integration config or set env var `APPCONFIG_ENDPOINT` (e.g. `https://<your-appconfig>.azconfig.io`).
	- Service principal (client secret): set these environment variables:
		- `AZURE_TENANT_ID`
		- `AZURE_CLIENT_ID`
		- `AZURE_CLIENT_SECRET`
	- Federated / OIDC credential flows (federated identity): ensure your workload identity is configured in Azure and set:
		- `AZURE_TENANT_ID`
		- `AZURE_CLIENT_ID`
		- `AZURE_SUBSCRIPTION_ID` (some flows/checks require a subscription context)

- Azure CLI as a fallback: if the `az` CLI is installed and you have run `az login`, the integration will attempt to use the CLI's access token prior to other REST flows.

How the integration chooses auth (priority):

1. If a connection string env var (default `APPCONFIG_CONNECTION_STRING` or a custom env-var name configured in YAML) is present, the integration uses connection-string authentication.
2. Otherwise, if an App Configuration endpoint is configured, the integration will try Azure CLI tokens, then Azure AD credentials (service principal / OIDC) using the `AZURE_*` env vars.
3. If neither is available the integration will report the endpoint/auth as not configured.

Examples — override env-var names in integration YAML

```yaml
integration:
	name: appconfig
	type: azure-appconfig
	endpoints:
		address: ${APPCONFIG_ENDPOINT}
	authentication:
		method: api_key
		api_key:
			api_key: "MY_CUSTOM_APPCONN_ENV"

# OR: OAuth2 env-var names
authentication:
	method: oauth2
	oauth2:
		tenant_id: "MY_TENANT_ENV"
		client_id: "MY_CLIENT_ENV"
		client_secret: "MY_SECRET_ENV"


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