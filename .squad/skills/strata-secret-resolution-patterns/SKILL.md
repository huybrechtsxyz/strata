---
name: "strata-secret-resolution-patterns"
description: "Build-time secret resolution, safe key management, and secret store integration patterns"
domain: "security"
confidence: "high"
source: "ADR-0005, strata.instructions.md"
tools:
  - name: "strata secret"
    description: "Secret store management"
    when: "register and retrieve secrets"
  - name: "strata ref secret"
    description: "Bind secrets to profiles"
    when: "associate secret keys with environment profiles"
---

## Context

Secrets MUST be resolved at **build time**, NOT runtime. They are never written to YAML, committed to git, or stored as plain text. Instead, agents reference secrets by key name, and strata resolves them from a secure store at build time. This applies to database passwords, API keys, SSH keys, and any sensitive material.

**Core principle:** If a secret appears in plain text in YAML or committed to git, it's a security breach. Always use the reference pattern.

## Secret Resolution Lifecycle

```
┌─────────────────────────────────────────────────────────────┐
│ 1. YAML declares secret reference (not the value)           │
│    spec.secrets:                                            │
│      - key: db_password                                     │
│        source: bitwarden                                    │
│        value: <bitwarden-item-id>                           │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 2. During build, strata resolves from secret store          │
│    (Bitwarden, Azure Key Vault, env vars, etc.)            │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 3. Resolved value injected into Terraform/Ansible          │
│    (never written to disk; kept in memory)                 │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│ 4. Provisioner uses secret (SSH key written to temp file,  │
│    deleted after use; passwords passed via environment)    │
└─────────────────────────────────────────────────────────────┘
```

## Secret Sources

### 1. Bitwarden

Store secrets in Bitwarden, reference by item ID:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Deployment
meta:
  name: deploy-prod
spec:
  secrets:
    - key: db_password
      source: bitwarden
      value: "a1b2c3d4-e5f6-7890-abcd-ef1234567890"  # item ID, not password
    - key: api_key
      source: bitwarden
      value: "x9y8z7w6-v5u4-t3s2-r1q0-ponmlkjihgfe"
```

**How to get item ID:**
```bash
bw list items --search "db-password-prod"  # find the item
# Response will include "id": "a1b2c3d4-..."
```

### 2. Azure Key Vault

Store secrets in Azure KV, reference by secret name:

```yaml
spec:
  secrets:
    - key: db_password
      source: azure_keyvault
      value: "prod-db-password"  # secret name in KV
    - key: ssl_cert
      source: azure_keyvault
      value: "prod-ssl-certificate"
```

**Prerequisites:**
- Managed Identity with KV access
- Correct RBAC roles assigned

### 3. Environment Variables

For CI/CD pipelines, read from environment:

```yaml
spec:
  secrets:
    - key: github_token
      source: env
      value: "GITHUB_TOKEN"  # env var name
    - key: docker_password
      source: env
      value: "DOCKER_PASSWORD"
```

**When building:** Ensure env var is set before `strata build run`.

### 4. Local File (Development Only)

For local development, read from `.env` or `.secrets/` (NEVER commit):

```yaml
spec:
  secrets:
    - key: db_password
      source: file
      value: ".secrets/db-password.txt"  # relative to workspace root
```

**WARNING:** `.secrets/` must be in `.gitignore`. Never commit secret files.

## SSH Key Pattern (Critical for Ansible)

Ansible provisioners require SSH private keys. Never commit keys. Instead:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: Deployment
meta:
  name: deploy-prod
spec:
  stages:
    - name: configuration
      provisioner: ansible
      scope: all
      configuration:
        playbook: site.yml
        ssh_private_key_secret: haven_ssh_key  # reference by name
  
  secrets:
    - key: haven_ssh_key
      source: bitwarden
      value: "e5f6g7h8-i9j0-k1l2-m3n4-o5p6q7r8s9t0"  # item contains full PEM
```

### How It Works

1. **Build time:** strata fetches the PEM key from Bitwarden
2. **Create temp file:** writes to `/tmp/haven_ssh_key_XXXXX` with `chmod 600`
3. **Ansible runs:** uses temp file for SSH
4. **Cleanup:** deletes temp file immediately after Ansible completes
5. **Result:** key never persisted to disk, never staged in git

### Generating SSH Keys

```bash
# Generate
ssh-keygen -t rsa -b 4096 -f haven_ssh_key -N ""

# Upload to Bitwarden (copy the private key content)
bw create item <template> --name "haven-ssh-key" \
  --field "private_key=$(cat haven_ssh_key)"

# Note the item ID
# Clean up local files
rm haven_ssh_key haven_ssh_key.pub
```

## Profile-Specific Secret Binding

Different profiles (dev/staging/prod) use different secrets:

```yaml
# Step 1: Create secrets in secret store
# Bitwarden: "dev-db-password", "staging-db-password", "prod-db-password"

# Step 2: Bind to profiles via strata CLI
strata ref secret db_password \
  --source bitwarden \
  --value "prod-db-password" \
  --profile prod \
  --output json

strata ref secret db_password \
  --source bitwarden \
  --value "staging-db-password" \
  --profile staging \
  --output json
```

**At deploy time:**
```bash
strata profile activate prod
strata deploy run -f deploy.yaml --force --output json
# Uses prod-db-password
```

## Common Patterns

### 1. Database Password

```yaml
spec:
  secrets:
    - key: db_password
      source: bitwarden
      value: "<id-of-bitwarden-db-password-item>"

  terraform:
    variables:
      - name: db_password
        value: "${secrets.db_password}"  # injected at apply time
```

### 2. API Keys

```yaml
spec:
  secrets:
    - key: github_api_key
      source: env
      value: "GITHUB_API_KEY"
    - key: docker_registry_password
      source: bitwarden
      value: "<id>"
```

### 3. Certificates & Keys

```yaml
spec:
  secrets:
    - key: ssl_certificate
      source: azure_keyvault
      value: "prod-ssl-cert"
    - key: ssl_private_key
      source: azure_keyvault
      value: "prod-ssl-key"
```

## Validation Rules

❌ **Anti-Patterns (NEVER do this):**

```yaml
# ❌ WRONG — hardcoded secret
spec:
  terraform:
    variables:
      - name: db_password
        value: "super-secret-123"

# ❌ WRONG — secret in comment
spec:
  # password: "admin123"
  provisioners: []

# ❌ WRONG — secret in git history
git add deploy.yaml  # contains secret references, but OK
git add .env         # ❌ NEVER — contains actual values
```

✅ **Correct Pattern:**

```yaml
# ✅ CORRECT — reference only
spec:
  secrets:
    - key: db_password
      source: bitwarden
      value: "<item-id>"

# ✅ CORRECT — use reference
  terraform:
    variables:
      - name: db_password
        value: "${secrets.db_password}"
```

## Troubleshooting

| Error | Cause | Fix |
|-------|-------|-----|
| "Secret not found" | Item ID wrong or not in store | Verify item exists: `bw get item <id>` |
| "Cannot access Azure KV" | No Managed Identity or RBAC | Check identity: `az identity show` |
| "SSH key not found" | Key not in secret store | Upload SSH key to Bitwarden/KV |
| "Secret resolved as empty" | Env var not set | Check env: `echo $GITHUB_TOKEN` |
| "Ansible fails: SSH auth failed" | Wrong SSH key | Verify public key in authorized_keys on target |

## Workflow Checklist

- [ ] **Never write secrets in YAML** — use reference pattern
- [ ] **Register secrets in store first** — Bitwarden, Azure KV, or env var
- [ ] **Reference by key name or item ID** — not the actual value
- [ ] **Validate before build** — `strata validate --deep` checks secret sources
- [ ] **Build, don't deploy manually** — strata resolves at build time
- [ ] **Test in dev first** — before deploying to prod
- [ ] **Rotate regularly** — update secret store, rebuild, redeploy
- [ ] **Audit after deploy** — `strata audit list` records what was used

## Security Best Practices

1. **Use Managed Identities** — for cloud resources (Azure MSI, AWS IAM role)
2. **Rotate secrets regularly** — every 90 days minimum
3. **Minimize access** — principle of least privilege for secret store
4. **Never debug with real secrets** — use dummy values locally
5. **Use separate stores per environment** — dev/staging/prod isolation
6. **Log secret *resolution*, not values** — audit trail without exposing keys
7. **Clean temp files** — strata auto-deletes SSH key temp files; verify cleanup
