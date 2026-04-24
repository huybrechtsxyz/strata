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

Common Commands
- `vault status`
- Read secret: `vault kv get secret/myapp`
- Write secret: `vault kv put secret/myapp key=value`

Docs
- https://www.vaultproject.io/docs
