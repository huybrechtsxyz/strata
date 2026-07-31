# Secrets, Variables, and Features — The Resolution Pattern

> **Audience:** DevOps engineers configuring strata deployments. You need to understand how to
> declare and reference sensitive data, configuration values, and feature flags across workspace,
> environment, and deployment layers.

All three mechanisms — **secrets**, **variables**, and **features** — use the same resolution pattern.
Understand one, and you understand all three.

---

## The Common Pattern

Each one follows this structure:

```yaml
spec:
  # All three are lists of key-value pairs
  secrets:    # Sensitive data (passwords, API keys)
    - key: DB_PASSWORD
      store: azure-keyvault
      value: prod-db-secret

  variables:  # Non-sensitive configuration (hostnames, counts)
    - key: REPLICA_COUNT
      store: constant
      value: "3"

  features:   # Boolean flags (enable/disable functionality)
    - key: enable_caching
      store: constant
      value: "true"
```

**Common fields:**
- `key` — identifier (how you reference it in YAML: `var: REPLICA_COUNT`, `secret: DB_PASSWORD`, `feature: enable_caching`)
- `store` — **where to resolve the value from** (constant, environment, vault, azure-keyvault, etc.)
- `value` — **what to look up** in that store (literal value, env var name, or secret path)

---

## The Three Types

| Aspect             | Secrets                                      | Variables                                         | Features                                      |
| ------------------ | -------------------------------------------- | ------------------------------------------------- | --------------------------------------------- |
| **Purpose**        | Sensitive data (passwords, API keys, tokens) | Non-sensitive config (hostnames, counts, regions) | Boolean toggles (enable/disable features)     |
| **Sensitivity**    | High — never logged, memory-only             | Low — may appear in output                        | Low — boolean flags                           |
| **Typical output** | Injected into Terraform/Ansible inputs       | Injected into Terraform/Ansible inputs            | `flags.auto.tfvars.json` (`{key: bool}`)      |
| **Git-safe**       | No — resolved at build time                  | Yes — constant values can be in git               | Yes — constant values can be in git           |
| **Example use**    | DB password, API token                       | Replica count, datacenter region                  | Enable canary deployments, enable autoscaling |

---

## Store Types (Backends)

The `store` field determines where strata looks up the value. All three types support the same stores (though some are more common for each):

### Shared stores (all three types support these)

| Store         | Resolves from                      | Example                                   |
| ------------- | ---------------------------------- | ----------------------------------------- |
| `constant`    | Literal value in YAML (this file)  | `value: "3"` or `value: "true"`           |
| `environment` | Environment variable on the runner | `value: MY_ENV_VAR` → reads `$MY_ENV_VAR` |
| `vault`       | HashiCorp Vault path               | `value: secret/data/prod/db-password`     |
| `infisical`   | Infisical secret path              | `value: /prod/db-password`                |

### Secrets-only stores

| Store            | Resolves from                                         |
| ---------------- | ----------------------------------------------------- |
| `github`         | GitHub Actions secret (injected as env var by runner) |
| `azure-keyvault` | Azure Key Vault secret                                |
| `bitwarden`      | Bitwarden item ID                                     |

### Variables-only stores

| Store             | Resolves from               |
| ----------------- | --------------------------- |
| `azure-appconfig` | Azure App Configuration key |
| `consul`          | HashiCorp Consul key        |
| `etcd`            | etcd key                    |

---

## Declaring Values

### Constant values (inline)

Safest for non-sensitive data:

```yaml
variables:
  - key: REPLICA_COUNT
    store: constant
    value: "3"

  - key: DATACENTER
    store: constant
    value: "us-east-1"

features:
  - key: enable_autoscaling
    store: constant
    value: "true"
```

**Best for:** Default values, environment-independent config, feature flags that are always on/off.

### Environment variables (runtime lookup)

Useful when values are already in the runner's environment:

```yaml
secrets:
  - key: API_TOKEN
    store: environment
    value: MY_API_TOKEN         # reads $MY_API_TOKEN from the runner

variables:
  - key: BUILD_TIMESTAMP
    store: environment
    value: BUILD_TIMESTAMP      # reads $BUILD_TIMESTAMP
```

**Best for:** CI/CD runners (GitHub Actions, Azure Pipelines) where you already set env vars.

### External stores (Vault, Key Vault, Bitwarden, etc.)

For production-grade secret management:

```yaml
secrets:
  - key: DB_PASSWORD
    store: azure-keyvault
    value: prod-db-secret       # secret name in Azure Key Vault

  - key: GITHUB_TOKEN
    store: bitwarden
    value: a1b2c3d4-0000...     # Bitwarden item ID

variables:
  - key: CONFIG_VERSION
    store: vault
    value: secret/config/version
```

**Best for:** Production deployments where secrets are managed by a central system.

---

## Layering: Workspace → Environment → Deployment

All three types **layer** the same way. Later layers override earlier ones:

```
Workspace layer (foundation)
    ↓ (overridden by)
Environment layer(s)
    ↓ (overridden by)
Deployment layer (highest precedence)
```

### Example: Layering a variable

**`workspace.yaml`** (shared across all environments):
```yaml
spec:
  variables:
    - key: REPLICA_COUNT
      store: constant
      value: "1"        # baseline: 1 replica
```

**`env-prd.yaml`** (production-specific):
```yaml
spec:
  variables:
    - key: REPLICA_COUNT
      store: constant
      value: "5"        # override: 5 replicas in production
```

**`deploy-prd.yaml`** (deployment-level override):
```yaml
spec:
  variables:
    - key: REPLICA_COUNT
      store: constant
      value: "10"       # override: 10 replicas for this specific deployment
  environment:
    - env-prd
  workspace:
    ...
```

**Resolution at build time:**
1. Workspace sets `REPLICA_COUNT=1`
2. Environment overrides to `REPLICA_COUNT=5`
3. Deployment overrides to `REPLICA_COUNT=10`
4. **Final value: 10**

### Same layering rule for secrets and features

```yaml
# workspace.yaml
spec:
  secrets:
    - key: DB_PASSWORD
      store: vault
      value: secret/shared/db  # shared password

# env-prd.yaml
spec:
  secrets:
    - key: DB_PASSWORD
      store: vault
      value: secret/prod/db    # production-specific password (override)
```

---

## Referencing Values in YAML

Once declared, reference values using the syntax specific to each type:

### Variables: `var: <key>`

```yaml
# In DNS records
spec:
  records:
    - name: api
      var: API_FQDN           # resolves to the value of FQDN variable

# In network CIDRs
spec:
  cidrs:
    - var: ALLOWED_CIDR       # resolves to the value of ALLOWED_CIDR variable

# In module overrides
overrides:
  - replicas:
      var: REPLICA_COUNT      # resolves to REPLICA_COUNT from variables
```

### Secrets: `secret: <key>`

Secrets are resolved into memory and injected into provisioner inputs (Terraform, Ansible). They are **not referenced directly in config** — instead, they're injected via the build artifacts:

```yaml
# Declaring the secret (what value to fetch)
spec:
  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: prod-db-secret

# Referencing in Terraform input
# At build time, this becomes:
# → variables.tfvars includes: db_password = "***secret-value***"
# → passed to Terraform as input variable
```

### Features: `feature: <key>` or `featureFlags: {}`

Feature flags are resolved into a flat boolean map and written to `flags.auto.tfvars.json`:

```yaml
# Declaring a feature flag
spec:
  features:
    - key: enable_caching
      store: constant
      value: "true"

# Referencing in a module
spec:
  modules:
    - name: api
      overrides:
        enable_caching:
          feature: enable_caching  # resolves to true/false
```

**Output at build time:** `.strata/build/flags.auto.tfvars.json`
```json
{
  "enable_caching": true,
  "enable_autoscaling": false
}
```

---

## Practical Examples

### Example 1: Multi-environment database credentials

**Problem:** Different databases per environment, secrets stored in Key Vault.

**Solution:**

```yaml
# workspace.yaml
spec:
  variables:
    - key: DB_HOST
      store: constant
      value: localhost      # fallback for local dev

  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: db-password    # will be overridden per environment

# env-prd.yaml
spec:
  variables:
    - key: DB_HOST
      store: constant
      value: prod-db.postgres.database.azure.com

  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: prod-db-master-password    # production secret name

# env-stg.yaml
spec:
  variables:
    - key: DB_HOST
      store: constant
      value: stg-db.postgres.database.azure.com

  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: stg-db-master-password     # staging secret name
```

At build time, strata injects the correct host and password per environment into Terraform inputs.

### Example 2: Feature flags for canary deployment

**Problem:** Roll out new version to 10% of traffic, but only in production.

**Solution:**

```yaml
# workspace.yaml
spec:
  features:
    - key: canary_deployment
      store: constant
      value: "false"        # disabled by default

# env-prd.yaml
spec:
  features:
    - key: canary_deployment
      store: constant
      value: "true"         # enabled only in production

# Module references the flag
spec:
  modules:
    - name: api-gateway
      overrides:
        canary_weight:
          feature: canary_deployment    # 10% if true, 0% if false
```

### Example 3: GitHub Actions with env var secrets

**Problem:** GitHub Actions injects secrets as environment variables; strata should use them.

**Solution:**

```yaml
# env-github-actions.yaml
spec:
  secrets:
    - key: DOCKER_REGISTRY_TOKEN
      store: github
      value: DOCKER_REGISTRY_TOKEN    # GitHub automatically injects this

  variables:
    - key: BUILD_COMMIT
      store: environment
      value: GITHUB_SHA               # GitHub Actions sets $GITHUB_SHA
```

When strata runs in GitHub Actions, it reads `$DOCKER_REGISTRY_TOKEN` and `$GITHUB_SHA` from the
runner environment.

---

## Security Best Practices

### Never commit secrets to git

✓ **Good:**
```yaml
secrets:
  - key: API_KEY
    store: vault
    value: secret/prod/api-key     # resolved at build time
```

✗ **Bad:**
```yaml
secrets:
  - key: API_KEY
    store: constant
    value: "sk_live_1234567890"    # hardcoded! Never do this.
```

### Variables can be in git (they're non-sensitive)

✓ **OK to commit:**
```yaml
variables:
  - key: REPLICA_COUNT
    store: constant
    value: "3"

  - key: REGION
    store: constant
    value: "us-east-1"
```

### Secrets are never logged

Even if a secret appears in Terraform or deployment output, strata:
1. Resolves it into memory only
2. Never prints it to stdout/stderr
3. For Ansible SSH keys: writes a temp file with `chmod 600`, deletes after playbook runs

---

## Auto-Generated Secrets

If a secret doesn't exist yet, ask strata to create it:

```yaml
spec:
  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: prod-db-password
      auto_generate:
        enabled: true
        generator: random-string
        length: 32
        charset: alphanumeric
```

On first `strata build`, strata will:
1. Check if `prod-db-password` exists in Azure Key Vault
2. If not, generate a 32-character random string
3. Store it in Key Vault
4. Use it for the build

Subsequent builds reuse the same secret (no re-generation).

> Need to store a *transformed* value (e.g. a hash) while keeping the raw generated secret
> retrievable separately? See [Cookbook: Hash a Secret Before Storing It](cookbook-hash-secret-before-storage.md).

---

## Troubleshooting

### "Secret key not found"

The `store` couldn't resolve the `value`. Check:
- Is the backend (vault, Key Vault, Bitwarden) accessible?
- Does the secret/variable actually exist in that backend?
- Do you have permission to read it?

### "Variable referenced but not declared"

You used `var: MISSING_KEY` in YAML but never declared `MISSING_KEY` in `spec.variables`. Add the declaration or fix the reference.

### Secret appears in logs

If a secret was logged, it's a strata bug. Report it — secrets should never be logged. As a
temporary workaround, rotate the secret immediately.

---

## See Also

- [Environment Configuration](../config/environment.md) — full schema reference
- [Secret Resolution at Build Time](../decisions/0005-secret-resolution-at-build-time.md) — design rationale
- [Auto-Generated Secrets](../decisions/0013-auto-generated-secrets.md) — how auto-generation works
