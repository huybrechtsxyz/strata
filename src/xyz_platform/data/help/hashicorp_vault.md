# HashiCorp Vault Integration

Installation
- Download from https://www.vaultproject.io/downloads or use package managers:
  - macOS: `brew install vault`
  - Linux: use package manager or download binary

Configuration
- Set `VAULT_ADDR` to your Vault server (e.g., `https://vault.example.com`).
- Authentication methods:
  - Token: set `VAULT_TOKEN`
  - AppRole: set `VAULT_ROLE_ID` and `VAULT_SECRET_ID`
  - Kubernetes: configure Kubernetes auth and role
- Optional: set `VAULT_NAMESPACE` for Vault Enterprise

Connection parameters

- Required (one of):
  - `VAULT_ADDR` — Vault server address (or set `endpoints.address` in integration YAML)
  - Authentication (one of):
    - `VAULT_TOKEN` — direct token
    - `VAULT_ROLE_ID` and `VAULT_SECRET_ID` — AppRole credentials
    - `VAULT_K8S_ROLE` (and accessible JWT) — Kubernetes auth

Examples — integration YAML overrides

```yaml
integration:
  name: vault
  type: hashicorp-vault
  endpoints:
    address: ${VAULT_ADDR}
  authentication:
    method: api_key
    api_key:
      api_key: "MY_VAULT_TOKEN_ENV"
```

Notes
- The integration reads `api_key.api_key` or `oauth2` fields in the YAML as env-var names if provided. Otherwise it falls back to standard env var names shown above.

Common Commands
- `vault status`
- Read secret: `vault kv get secret/myapp`
- Write secret: `vault kv put secret/myapp key=value`

Docs
- https://www.vaultproject.io/docs
