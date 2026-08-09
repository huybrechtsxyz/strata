---
name: strata-secret-resolution-patterns
description: 'Strata secret/variable/feature resolution: store schema, generate-on-missing, rotation, stage-scoped secret allowlists, and SSH key lifecycle. Use when writing secrets/variables into environment YAML or debugging why a value did not reach a stage.'
---

# Strata Secret Resolution Patterns

## The Core Rule

**NEVER write secrets as plain values in YAML.** Reference a store + key; strata resolves the real value at build/deploy time.

```yaml
# ❌ WRONG — never do this
spec:
  secrets:
    - key: DATABASE_PASSWORD
      store: constant
      value: my-secret-password-123   # a literal secret value, hardcoded

# ✅ RIGHT — reference an integration-backed store
spec:
  secrets:
    - key: DATABASE_PASSWORD
      store: infisical                 # or: azure-keyvault, bitwarden, vault
      value: prod/db_password          # the secret's path/ID *within* that store
```

---

## Secret / Variable / Feature Schema (verified against the Pydantic models)

Every item in `spec.secrets[]` (also `spec.variables[]`, `spec.features[]`) has this shape:

```yaml
spec:
  secrets:
    - key: DATABASE_PASSWORD     # name used to reference this value elsewhere
      store: infisical           # which store/integration resolves it
      value: prod/db_password    # meaning depends on `store` (see table below)
      version: null              # optional, only some integration stores support it
      description: "..."         # optional, documentation only
      generate:                  # optional — only for integration-backed stores
        type: password           # password | urlsafe | hex | uuid
        length: 32
      rotate:                    # optional — only for integration-backed stores
        policy: warn             # warn (advisory) | rotate (auto-regenerate)
        max_age_days: 90
```

**`store:` valid values** (this is a closed enum — `extra="forbid"` rejects anything else):

| `store` value    | What `value:` means                                          | Needs an integration? |
| ----------------- | -------------------------------------------------------------- | ---------------------- |
| `constant`         | The literal value itself (only ever use for non-sensitive data) | No                      |
| `environment`      | Name of an env var to read at resolve time                     | No                      |
| `github`           | Name of a GitHub Actions env var (only meaningful in CI)       | No                      |
| `infisical`        | Secret path/ID inside the Infisical project                    | Yes                     |
| `vault`            | Secret path inside HashiCorp Vault (or OpenBao)                | Yes                     |
| `bitwarden`        | Bitwarden secret ID (via the `bws` CLI)                        | Yes                     |
| `azure-keyvault`   | Secret name inside the Azure Key Vault                         | Yes                     |

`generate:`/`rotate:` are **only valid on integration-backed stores** (`infisical`/`vault`/`bitwarden`/`azure-keyvault`) — using them on `constant`/`environment`/`github` fails schema validation.

**Common mistake:** using `source:`/`item_id:` field names — those don't exist. The real fields are `store:` and `value:`.

---

## Where Stores Are Configured

Integration-backed stores (`infisical`, `vault`, `bitwarden`, `azure-keyvault`) themselves are declared once, in `configuration.yaml`'s `spec.integrations` — not per-secret:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: configuration
spec:
  integrations:
    - name: infisical
      type: infisical
      required: true
      enabled: true
      endpoints:
        address: https://app.infisical.com
```

The `store:` value on a secret item (e.g. `infisical`) matches an integration's `type:` here, not an arbitrary name.

---

## Stage-Scoped Secret Allowlists (Critical — a common source of "resolved but missing" bugs)

A secret resolving successfully at the environment level does **not** automatically make it into every stage's provisioner. Each stage only receives the secrets it explicitly allowlists:

```yaml
spec:
  stages:
    - name: infrastructure
      provisioner: terraform
      secrets: [HETZNER_ROOT_PASSWORD, DB_PASSWORD]   # only these two reach this stage
    - name: configure
      provisioner: ansible
      secrets: ['*']                                    # escape hatch: every resolved secret
    - name: verify
      provisioner: terraform
      # no `secrets:` key at all → this stage gets ZERO secrets, even if some resolved fine
```

**Symptom if you get this wrong:** the CLI logs no warning at all (the secret resolved fine), but the provisioner fails with something like Terraform's `Error: No value for required variable` for a variable you were sure had a value. **Always check the failing stage's `secrets:` list first** before assuming a store/integration problem.

---

## SSH Private Key Lifecycle (Critical Pattern)

SSH keys are never stored on disk in plain text. The lifecycle is:

1. **Store:** SSH key lives in an integration-backed secret store
2. **Reference:** the provisioner's `configuration.ssh_private_key_secret` names the secret key
3. **Deploy time:** strata reads the value, writes it to a temp file (`chmod 600`)
4. **Use:** the provisioner (Ansible) uses the temp file for its SSH connection
5. **After use:** strata deletes the temp file immediately

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: deployment
spec:
  stages:
    - name: configure
      provisioner: ansible
      secrets: [ssh_key_prod]              # must ALSO be in this stage's allowlist
      configuration:
        playbook: site.yml
        inventory: inventory/hosts.yml
        ssh_private_key_secret: ssh_key_prod
        extra_vars:
          env: production
```

```yaml
# environment.yaml
spec:
  secrets:
    - key: ssh_key_prod
      store: bitwarden
      value: ssh-key-item-123   # item must contain the full PEM-formatted private key
```

**Agent rule:** never put SSH private keys in YAML directly — always use `ssh_private_key_secret` + a store reference, and remember the stage's `secrets:` allowlist must include that key.

---

## Secret Resolution in Different Phases

### Phase 1: Validation (Dry Read Only)

```bash
strata validate -f deploy/prod.yaml --deep --output json
```

Checks the store is registered and reachable — does not fetch or log the actual value.

### Phase 2: Build

```bash
strata build run -f deploy/prod.yaml --output json
```

Does NOT resolve secrets — `build` produces provisioner artifacts (Terraform/Ansible files) from resources/modules, not resolved runtime values.

### Phase 3: Deploy

```bash
strata deploy run -f deploy/prod.yaml --dry-run --force --output json
```

This is where resolution actually happens, in order:
1. **Pre-flight** — every distinct store referenced anywhere in the deployment is checked for availability ONCE, before anything else. If any is unreachable/unauthenticated, the whole deploy aborts immediately (this is always fatal, regardless of `--strict`) — it never silently generates or overwrites a secret because a store happened to be down.
2. **Resolve** — each variable/secret/feature is fetched. A confirmed "key not found" on a secret with a `generate:` spec creates and stores a new value; a store-connectivity failure is a different, always-fatal category (never confused with "not found").
3. **Scope per stage** — each stage's provisioner only receives the secrets in its `secrets:` allowlist (see above).
4. SSH keys are written to temp files at this point, deleted immediately after the provisioner subprocess exits.

**Secrets never appear in logs, audit records, or state — only key names and store types do.**

---

## Common Secret Reference Locations

| Context                | Field                                                        | Example              |
| ----------------------- | -------------------------------------------------------------- | ---------------------- |
| Any environment secret | `environment.spec.secrets[].key`                              | `db_password`         |
| Module env var (secret) | `module.spec.services[].environment[].secret`                | `DB_PASSWORD`         |
| SSH private key        | `provisioner.configuration.ssh_private_key_secret`             | `ssh_key`              |
| Stage secret allowlist | `deployment.spec.stages[].secrets`                             | `[DB_PASSWORD]` or `['*']` |

---

## Multi-Environment Secret Override Pattern

Use a different store per environment for the same logical secret key:

```yaml
# environment/dev.yaml
spec:
  secrets:
    - key: db_password
      store: environment
      value: DEV_DB_PASSWORD

# environment/prod.yaml
spec:
  secrets:
    - key: db_password
      store: azure-keyvault
      value: prod-db-password
```

Same deployment YAML, different secrets resolved depending on which environment profile is active.

---

## Best Practices

1. **Never commit secrets to git** — use `store:`/`value:` references only, never `store: constant` for sensitive data
2. **Check the stage's `secrets:` allowlist** whenever a value "resolved" but a provisioner says it's missing
3. **Use different secrets per environment** — prod != dev
4. **Validate before build** — `strata validate --deep` catches missing/misconfigured stores early
5. **Use `rotate: warn` before `rotate: rotate`** — advisory rotation first, auto-regeneration only once you trust the flow
6. **Review audit logs** — `strata audit list` never logs actual values, only key names and store types

---

## Troubleshooting

| Problem                                     | Cause                                              | Fix                                                                      |
| --------------------------------------------- | ----------------------------------------------------- | --------------------------------------------------------------------------- |
| `key '<name>' not found in '<store>' store`  | Secret genuinely doesn't exist at that path/ID       | Create it in the store, or add a `generate:` spec                        |
| `Store '<name>' unavailable: ...`            | Network/auth issue reaching the store                | Verify integration credentials/connectivity — strata aborts, never guesses |
| Provisioner says variable has no value        | Value resolved but wasn't in the stage's allowlist   | Add the key to that stage's `secrets:` list (or use `['*']`)              |
| `generation succeeded but store write failed` | `generate:` created a value but the store rejected the write (permissions) | Check the store credential's write/create permission                     |
| SSH key invalid                                | Not valid PEM format                                 | Re-upload the key to the store; validate with `openssl`                  |

---

## Agent Best Practices

1. **Use `store:`/`value:`, never `source:`/`item_id:`** — those aren't real fields
2. **When a stage's provisioner reports a missing value, check `stage.secrets` before the store** — this is the most common false lead
3. **Validate with `--deep` before building** — catches unregistered/unreachable stores early
4. **Never hardcode credentials** — `store: constant` is only for genuinely non-sensitive values
5. **Document what each secret should contain** — use the optional `description:` field
