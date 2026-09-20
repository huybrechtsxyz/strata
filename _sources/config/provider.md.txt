# Provider Configuration

Defines cloud infrastructure providers and connection parameters. YAML files specify provider types, regions, and reference environment variables or secrets for authentication/API access.

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: provider
meta:
  name: <resource_name>         # Required: ^[a-z][a-z0-9_-]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  properties:
    type: <provider_type>       # Required: kamatera, azure, aws, gcp, local
    region: <region>            # Required: provider region/location
  references:
    variables: []               # List of variable key names required from environment
    secrets: []                 # List of secret key names required from environment
    features: []                # List of feature flag names required from environment
```

**References:**

Lists the key names that must be defined in the environment configuration (`environment.yaml`). Actual values and secret stores are defined at the environment level, not in the provider file.

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
apiVersion: strata.huybrechts.xyz/v1
kind: provider
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
      - kamatera_manager_id
      - datacenter_location
    secrets:
      - kamatera_api_key
      - kamatera_api_secret
      - kamatera_private_key
    features:
      - enable_auto_scaling
```

**Azure:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: provider
meta:
  name: azure_westeurope
  labels:
    version: "1.0.0"
spec:
  properties:
    type: azure
    region: westeurope
  references:
    variables:
      - azure_subscription_id
      - azure_resource_group
    secrets:
      - azure_tenant_id
      - azure_client_id
      - azure_client_secret
```

**Local:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: provider
meta:
  name: local_dev
spec:
  properties:
    type: local
  references:
    variables:
      - ssh_user
    secrets:
      - ssh_private_key
```

## Variables vs Secrets

**How references work:**

Provider files declare the key names they require via `spec.references`. Actual values are defined in the environment configuration file (`environment.yaml`), which maps keys to their sources (constants, environment variables, secret stores like Bitwarden, Azure Key Vault, HashiCorp Vault).

**Variables** (non-sensitive, visible in logs):

- Infrastructure details: subscription IDs, resource groups, manager IDs, datacenter locations
- Resolved at build time from environment configuration

**Secrets** (sensitive, encrypted/secret managers):

- API keys, passwords, private keys, access tokens, authentication credentials
- Resolved at build time from the configured secret store
- Never written to version control, only to encrypted build artifacts

**Features** (boolean feature flags):

- Enable/disable optional capabilities: monitoring, auto-scaling, high-availability
- Resolved at build time to "true" or "false"

### Example

**Provider declares requirements:**
```yaml
kind: provider
meta:
  name: kamatera_europe
spec:
  references:
    variables:
      - kamatera_manager_id
    secrets:
      - kamatera_api_key
```

**Environment provides actual values:**
```yaml
kind: environment
meta:
  name: prod
spec:
  variables:
    - key: kamatera_manager_id
      store: constant
      value: "manager-prod-001"
  secrets:
    - key: kamatera_api_key
      store: bitwarden
      value: "d47e736b-2db8-47d5-b46b-b2c8016ece73"  # Bitwarden item UUID
```

At build time, strata validates all declared keys exist in the environment, fetches their values, and injects them into the build artifacts (Terraform, Helm, compose files).

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