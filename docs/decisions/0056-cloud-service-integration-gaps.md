# Cloud Service Integration Gaps

- Status: proposed
- Date: 2026-07-23
- Related: ADR-0053 (Azure CLI), ADR-0054 (AWS CLI), ADR-0055 (gcloud CLI)

## Context

strata's integration layer covers secrets, variables, and feature flags for the
self-hosted ecosystem (Vault, Consul, Bitwarden, Infisical, etcd, Flagsmith, OpenBao)
and for Azure (Key Vault, App Configuration). The AWS and GCP cloud-native equivalents
are entirely missing — operators targeting these clouds cannot use `source: aws_secretsmanager`
or `source: gcp_secretmanager` in their deployment YAML.

### Current integration coverage

| Capability                          | Azure               | AWS | GCP | Self-hosted                            |
| ----------------------------------- | ------------------- | --- | --- | -------------------------------------- |
| **Secrets** (`ISecretStore`)        | `azure_keyvault` ✅  | ❌   | ❌   | Vault, Bitwarden, Infisical, OpenBao ✅ |
| **Variables** (`IVariableStore`)    | `azure_appconfig` ✅ | ❌   | ❌   | Consul, etcd ✅                         |
| **Feature flags** (`IFeatureStore`) | `azure_appconfig` ✅ | ❌   | ❌   | Flagsmith ✅                            |
| **Object storage** (build/manifest) | ❌                   | ❌   | ❌   | local / gitops ✅                       |

---

## Proposed integrations

### AWS Secrets Manager — `aws_secretsmanager.py`

AWS Secrets Manager stores secrets with rotation, versioning, and fine-grained IAM
access control. It is the standard secrets backend for AWS-native deployments.

**Capability:** `ISecretStore`
**Authentication:** `AWSCLIIntegration.ensure_available()` (ADR-0054) + IAM role/env vars
**Config YAML:**
```yaml
integrations:
  - name: aws_secrets
    type: aws_secretsmanager
    capabilities: [secrets]
    endpoints:
      address: https://secretsmanager.us-east-1.amazonaws.com   # optional; use AWS_DEFAULT_REGION
```
**Secret reference in deployment YAML:**
```yaml
secrets:
  - key: DB_PASSWORD
    source: aws_secretsmanager
    value: prod/myapp/db_password         # secret name or ARN
    version: AWSCURRENT                   # optional; default: AWSCURRENT
```
**Implementation sketch:**
- `get_secret(key)` → `aws secretsmanager get-secret-value --secret-id <key> --output json`
- Parse `SecretString` (string) or `SecretBinary` (base64-encoded)
- `list_secrets()` → `aws secretsmanager list-secrets --output json`
- Region from `AWS_DEFAULT_REGION` or `endpoints.address`

---

### AWS SSM Parameter Store — `aws_ssm.py`

SSM Parameter Store stores plain strings, encrypted strings (via KMS), and JSON
blobs. Widely used for application configuration, connection strings, and feature flags.

**Capability:** `IVariableStore`, `ISecretStore` (SecureString parameters)
**Authentication:** `AWSCLIIntegration.ensure_available()`
**Config YAML:**
```yaml
integrations:
  - name: aws_ssm
    type: aws_ssm
    capabilities: [variables, secrets]
```
**Variable reference:**
```yaml
variables:
  - key: DATABASE_URL
    source: aws_ssm
    value: /myapp/prod/database_url   # SSM parameter path
```
**Implementation sketch:**
- `get_variable(key)` → `aws ssm get-parameter --name <key> --with-decryption --output json`
- Parse `Parameter.Value`
- `list_variables(prefix)` → `aws ssm get-parameters-by-path --path <prefix>`

---

### GCP Secret Manager — `gcp_secretmanager.py`

Google Secret Manager stores versioned secrets with IAM access control and automatic
replication. Standard secrets backend for GCP-native deployments.

**Capability:** `ISecretStore`
**Authentication:** `GCloudCLIIntegration.ensure_available()` (ADR-0055, pending) / Application Default Credentials
**Config YAML:**
```yaml
integrations:
  - name: gcp_secrets
    type: gcp_secretmanager
    capabilities: [secrets]
    properties:
      project: my-gcp-project
```
**Secret reference:**
```yaml
secrets:
  - key: API_TOKEN
    source: gcp_secretmanager
    value: my-api-token              # secret name (without version)
    version: latest                  # optional; default: latest
```
**Implementation sketch:**
- `get_secret(key)` → `gcloud secrets versions access latest --secret=<key> --project=<project>`
  or REST: `GET https://secretmanager.googleapis.com/v1/projects/{project}/secrets/{name}/versions/latest:access`
- Parse `payload.data` (base64)
- `list_secrets()` → `gcloud secrets list --project=<project> --format=json`

---

### Cloud Object Storage — manifests and build artifacts

Deployment manifests (`spec.deployment.manifest`) currently support two backends:
`local` and `gitops`. A cloud object storage backend would allow manifests to be stored
in S3/GCS/Azure Blob without a git repository.

This is a **separate capability** (`IObjectStore`) — not secrets or variables.

| Backend   | Service              | Config                        |
| --------- | -------------------- | ----------------------------- |
| `s3`      | AWS S3               | `bucket`, `prefix`, `region`  |
| `gcs`     | Google Cloud Storage | `bucket`, `prefix`, `project` |
| `azurerm` | Azure Blob Storage   | `container`, `account`        |

**Manifest config:**
```yaml
spec:
  deployment:
    manifest:
      type: s3
      bucket: my-deployment-manifests
      prefix: strata/manifests
      region: us-east-1
```

**Scope:** Applies to `spec.deployment.manifest` and potentially `spec.deployment.outputs`
for durable cross-stage output persistence. Not for build artifacts (those stay in `.strata/build/`).

---

## Priority order

| Priority   | Integration                                       | Unblocks                                           |
| ---------- | ------------------------------------------------- | -------------------------------------------------- |
| **High**   | `aws_secretsmanager.py`                           | AWS-native secret resolution for any deployment    |
| **High**   | `aws_ssm.py`                                      | AWS-native variable/config resolution              |
| **High**   | `gcp_secretmanager.py`                            | GCP-native secret resolution                       |
| **Medium** | Cloud manifest storage (S3/GCS)                   | Enterprise manifest persistence without a git repo |
| **Low**    | GCP variable store (Cloud Storage KV / Firestore) | GCP-native variable resolution                     |

---

## Implementation notes

### AWS integrations (Secrets Manager + SSM)

Both follow the pattern established by `AzureKeyVaultIntegration`:
- Primary: REST API via `urllib.request` (fast, no subprocess per secret)
- Fallback: CLI subprocess (`aws secretsmanager get-secret-value`)
- Auth: `AWSCLIIntegration.ensure_available()` OR env vars (`AWS_ACCESS_KEY_ID` etc.)
- Register in `IntegrationFactory._BUILTIN_CLASS_MAP`

### GCP Secret Manager

Two auth paths:
1. `gcloud secrets versions access latest` (CLI) — uses Application Default Credentials
2. REST API: `Authorization: Bearer $(gcloud auth print-access-token)` — needs `GCloudCLIIntegration.get_access_token()` (ADR-0055)

### Cloud manifest storage

New `IObjectStore` protocol with `put_object(key, data)` / `get_object(key)` / `list_objects(prefix)`.
`ConfigurationDeploymentModel.manifest.type` extended with `s3`, `gcs`, `azurerm` values.
Each backend writes the manifest JSON to the object store post-deploy.

---

## Related decisions

- ADR-0005 — Secret resolution at build time
- ADR-0021 — Deployment manifests as first-class artifacts
- ADR-0053 — Azure CLI integration
- ADR-0054 — AWS CLI integration
- ADR-0055 — gcloud CLI integration (proposed)
