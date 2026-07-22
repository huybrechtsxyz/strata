---
description: "Strata secret resolution: build-time secret reference patterns, secret stores, SSH key lifecycle, and safe practices"
applyTo: "**/*.yaml"
---

# Strata Secret Resolution Patterns — AI Skill File

## The Core Rule

**NEVER write secrets as plain values in YAML.** Use named references only. Strata resolves them at build time and injects into artifacts.

```yaml
# ❌ WRONG — never do this
spec:
  password: my-secret-password-123

# ✅ RIGHT — always use references
spec:
  database:
    password_secret: database_password  # name, not value
```

---

## Secret Store Configuration

Define where secrets live in `configuration.yaml`:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
spec:
  stores:
    - key: bitwarden
      source: bitwarden
      vault_url: https://vault.example.com
      organization_id: org123

    - key: azure_kv
      source: azure_key_vault
      vault_name: my-secrets
      resource_group: my-rg

    - key: github
      source: github_secrets
      repository: owner/repo
```

**Supported secret stores:**
- `bitwarden` — Bitwarden vault
- `azure_key_vault` — Azure Key Vault
- `github_secrets` — GitHub repository secrets
- `aws_secrets_manager` — AWS Secrets Manager
- `hashicorp_vault` — HashiCorp Vault

---

## Reference Patterns

### Pattern 1: Direct Secret Reference (Most Common)

In `environment.yaml`:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: prod
spec:
  secrets:
    - key: db_password
      source: bitwarden
      item_id: abc123def456

    - key: api_key
      source: azure_key_vault
      secret_name: prod-api-key
```

Then in deployment manifests, reference by key:

```yaml
spec:
  database:
    password_secret: db_password  # matches the 'key' above

  services:
    api:
      environment:
        - name: API_KEY
          value_secret: api_key  # matches the 'key'
```

**Strata resolves at build time:**
1. Reads `db_password` from Bitwarden (item_id = abc123def456)
2. Injects the actual value into `platform.json`
3. Provisioners (Terraform, Ansible) receive the resolved value

### Pattern 2: Inline Secret Reference (When Store Context Matters)

If you need to fetch from different stores in different environments:

```yaml
spec:
  secret:
    key: my-secret
    source: bitwarden  # or azure_key_vault, github_secrets
    item_id: xyz789    # Bitwarden item ID
```

---

## SSH Private Key Lifecycle (Critical Pattern)

SSH keys are never stored on disk in plain text. The lifecycle is:

1. **Store:** SSH key lives in secret store (Bitwarden, Azure KV)
2. **Reference:** YAML references by key name
3. **Build time:** Strata reads from store, validates (PEM format)
4. **Deploy time:** Strata writes to temp file (`chmod 600`), uses for SSH
5. **After use:** Strata deletes temp file immediately

### SSH Key in Provisioner Configuration

**Ansible provisioner example:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
spec:
  stages:
    - name: configure
      provisioner: ansible
      configuration:
        playbook: site.yml
        inventory: inventory/hosts.yml
        ssh_private_key_secret: ssh_key_prod  # reference to secret key
        extra_vars:
          env: production
```

**Environment configuration:**

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: prod
spec:
  secrets:
    - key: ssh_key_prod
      source: bitwarden
      item_id: ssh-key-item-123
      # Item contains the full PEM-formatted private key
```

### SSH Key Validation

```bash
# Validate SSH key is readable (before deploy)
strata validate deploy/prod.yaml --deep --output json
```

If validation succeeds, SSH key is:
- Readable from secret store
- Valid PEM format
- Permissions correct (600)

---

## Secret Resolution in Different Phases

### Phase 1: Validation (Dry Read Only)

```bash
strata validate deploy/prod.yaml --deep --output json
```

Validates:
- Secret store is accessible
- Named secret exists
- Secret value is readable
- Secret format is valid (for SSH keys, must be valid PEM)

**Doesn't fetch or log the actual value.**

### Phase 2: Build

```bash
strata build run -f deploy/prod.yaml --output json
```

Fetches:
- All referenced secrets
- Injects into `platform.json` (transient, not saved)
- Generates provisioner artifacts (Terraform vars, Ansible extra_vars)

**Secrets stored in memory during build, cleared after artifacts generated.**

### Phase 3: Deploy

Secrets are resolved again at deploy time:
- SSH keys written to temp files (chmod 600)
- Used by provisioners (Ansible SSH connection)
- Deleted immediately after use
- **Never written to persistent logs or audit records**

---

## Common Secret Reference Locations

| Context              | Field                                                      | Example            |
| -------------------- | ---------------------------------------------------------- | ------------------ |
| Database password    | `spec.database.password_secret`                            | `db_password`      |
| API key              | `spec.services[].environment[].value_secret`               | `api_key`          |
| SSH private key      | `spec.provisioners[].configuration.ssh_private_key_secret` | `ssh_key`          |
| Registry credentials | `spec.image_pull_secret`                                   | `docker_registry`  |
| TLS certificate      | `spec.tls.cert_secret`                                     | `tls_cert`         |
| TLS private key      | `spec.tls.key_secret`                                      | `tls_key`          |
| Webhook signing key  | `spec.webhook.secret_key_secret`                           | `webhook_sign_key` |

---

## Secret Store Specifics

### Bitwarden

```yaml
stores:
  - key: bitwarden
    source: bitwarden
    vault_url: https://vault.example.com
    organization_id: org123

spec:
  secrets:
    - key: db_password
      source: bitwarden
      item_id: abc123  # Bitwarden item ID (UUID)
```

**Item requirements:**
- Format: JSON with at least `password` field, or plain text value
- Example: `{"username": "admin", "password": "secret123"}`

### Azure Key Vault

```yaml
stores:
  - key: azure_kv
    source: azure_key_vault
    vault_name: my-secrets
    resource_group: my-rg

spec:
  secrets:
    - key: db_password
      source: azure_key_vault
      secret_name: db-password-prod
```

**Secret requirements:**
- Stored as Azure KV secret with exact name match
- Value can be plain text or JSON
- Access via service principal or managed identity

### GitHub Secrets

```yaml
stores:
  - key: github
    source: github_secrets
    repository: owner/repo
    token_secret_ref: github_pat  # secret store ref for GitHub PAT

spec:
  secrets:
    - key: api_key
      source: github_secrets
      secret_name: API_KEY_PROD
```

**Secret requirements:**
- Defined in GitHub repo Settings → Secrets
- Name matches exactly
- GitHub PAT needs `repo:read:secrets` scope

---

## Multi-Environment Secret Override Pattern

Use different secrets per environment:

```yaml
# environment/dev.yaml
spec:
  secrets:
    - key: db_password
      source: bitwarden
      item_id: dev-db-pwd-item

# environment/prod.yaml
spec:
  secrets:
    - key: db_password
      source: azure_key_vault
      secret_name: prod-db-password
```

Then in deployment:

```yaml
spec:
  environments:
    - source: "@config/environment/prod.yaml"  # uses prod db_password
```

**Result:** Same deployment YAML, different secrets per environment.

---

## Audit and Logging

**What appears in logs:**
- Secret key names (e.g., `db_password`)
- Secret store used (e.g., `azure_key_vault`)
- Resolution status (✅ resolved, ❌ failed)

**What NEVER appears in logs:**
- Secret values (encrypted, not logged)
- SSH key contents (never logged)
- API keys (never logged)

**Verify security:**

```bash
strata audit list --last --output json | grep -i password
# Should NOT show actual values, only key names
```

---

## Best Practices

1. **Never commit secrets to git** — use secret store references only
2. **Use different secrets per environment** — prod != dev secrets
3. **Rotate SSH keys regularly** — update in secret store, redeploy
4. **Use short expiration for temporary secrets** — API keys, tokens
5. **Validate before build** — catch missing secrets early
6. **Review audit logs** — verify secrets resolved correctly
7. **Use managed identities** — avoid long-lived credentials
8. **Encrypt secret stores in transit** — TLS/HTTPS only
9. **Limit secret store access** — principle of least privilege
10. **Archive old secrets** — compliance and debugging

---

## Troubleshooting

| Problem                    | Cause                     | Fix                                                      |
| -------------------------- | ------------------------- | -------------------------------------------------------- |
| `Secret not found`         | Key name doesn't match    | Check `environment.spec.secrets[].key` matches reference |
| `Secret store unavailable` | Network/auth issue        | Verify store credentials, network connectivity           |
| `SSH key invalid`          | Not valid PEM format      | Re-upload key to secret store, validate with `openssl`   |
| `Permission denied (SSH)`  | SSH key permissions wrong | Verify key is 600 chmod during deploy                    |
| `Secret value in logs`     | Security leak             | Check logging settings, disable debug logging in prod    |

---

## Agent Best Practices

1. **Always use `secret_name` or `_secret` suffix** — makes it clear it's a reference, not a value
2. **Reference secret by key in spec** — match exactly with `environment.spec.secrets[].key`
3. **Validate before building** — `strata validate --deep` checks secret accessibility
4. **Never hardcode credentials** — use references in YAML, never plain values
5. **Document secret expectations** — add comments about what each secret should contain
6. **Test secret resolution** — dry-run deploy to verify secrets resolve
