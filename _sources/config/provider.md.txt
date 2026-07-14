# Provider Configuration

Defines cloud infrastructure providers and connection parameters. YAML files specify provider types, regions, and reference environment variables or secrets for authentication/API access.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: provider
meta:
  name: <resource_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  properties:
    type: <provider_type> # Required: kamatera, azure, aws, gcp, local
    region: <region> # Optional: provider region/location
  references:
    variables: {} # Non-sensitive config (key-value)
    secrets: {} # Sensitive credentials (key-value)
```

**Key-Value Format:**

- **Key:** IaC template parameter name
- **Value:** Environment variable/secret reference name

## Provider Types

| Type       | Description           | Typical Regions                         |
| ---------- | --------------------- | --------------------------------------- |
| `kamatera` | Kamatera cloud        | `eu-fr`, `us-ny`, `ca-tr`               |
| `azure`    | Microsoft Azure       | `westeurope`, `eastus`, `southeastasia` |
| `aws`      | Amazon Web Services   | `us-east-1`, `eu-west-1`, `ap-south-1`  |
| `gcp`      | Google Cloud Platform | `europe-west1`, `us-central1`           |
| `local`    | Local/on-premises     | N/A                                     |

## Examples

**Kamatera:**

```yaml
meta:
  name: kamatera_europe
  labels:
    version: "1.0.0"
spec:
  properties:
    type: kamatera
    region: eu-fr
  references:
    variables:
      kamatera_manager_id: KAMATERA_MANAGER_ID
    secrets:
      kamatera_api_key: KAMATERA_API_KEY
      kamatera_api_secret: KAMATERA_API_SECRET
      kamatera_private_key: KAMATERA_PRIVATE_KEY
```

**Azure:**

```yaml
meta:
  name: azure_westeurope
spec:
  properties:
    type: azure
    region: westeurope
  references:
    variables:
      subscription_id: AZURE_SUBSCRIPTION_ID
      resource_group: AZURE_RESOURCE_GROUP
    secrets:
      tenant_id: AZURE_TENANT_ID
      client_id: AZURE_CLIENT_ID
      client_secret: AZURE_CLIENT_SECRET
```

**Local:**

```yaml
meta:
  name: local_dev
spec:
  properties:
    type: local
  references:
    variables:
      ssh_user: LOCAL_SSH_USER
    secrets:
      ssh_private_key: LOCAL_SSH_PRIVATE_KEY
```

## Variables vs Secrets

**Reference-based approach:** Provider files map IaC parameters to environment variables. Actual values loaded externally (env vars, workspace config, secret managers). Credentials never in version control.

**Variables** (non-sensitive, visible in logs):

- Subscription IDs, resource groups, manager IDs

**Secrets** (sensitive, encrypted/secret managers):

- API keys, passwords, private keys, access tokens

Example: `kamatera_api_key: KAMATERA_API_KEY` → IaC uses `kamatera_api_key`, platform loads from `KAMATERA_API_KEY` env var/secret

## Workspace Integration

```yaml
# workspace.yaml
spec:
  providers:
    - name: kamatera_europe
      file: config/providers/kamatera-eu-fr.yaml
  topology:
    - name: platform_swarm
      provider: kamatera_europe # References provider by name
```

**Multi-region setup:**

```
config/providers/
├── kamatera-eu-fr.yaml
├── kamatera-us-ny.yaml
├── azure-westeurope.yaml
└── azure-eastus.yaml
```

## Environment-Specific Provider Overrides

Different environments can use different provider files (e.g., dev vs prod in different regions or cloud accounts). See [Environment Provider Overrides](environment.md#provider-overrides) for syntax and examples.

**Example:** Use a different provider file for production:

```yaml
# environments/prod.yaml
spec:
  overrides:
    providers:
      - provider: kamatera_europe
        file: config/providers/kamatera-eu-fr-prod.yaml  # Different prod config
```

This allows dev and prod to deploy to different regions, cloud accounts, or with different authentication without modifying the workspace.

## Validation

Platform validates:

- Valid provider names (lowercase, alphanumeric, underscores)
- Required fields (name, type)
- Referenced providers exist in workspace
- Environment variables set at runtime

## Troubleshooting

**Provider not found:** Verify name matches workspace topology, check file path
**Missing credentials:** Ensure env vars set, names match exactly (case-sensitive), check secret manager connectivity
**Region errors:** Confirm region valid for provider, check documentation