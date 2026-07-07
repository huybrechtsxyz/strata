# Auto-generated store values (secrets, variables, features)

- Status: implemented
- Date: 2025-06-23

## Context and Problem Statement

Today, every value referenced in an environment YAML — secret, variable, or feature flag — must already exist in the configured store before `strata build run` can resolve it. This creates a repetitive, manual workflow:

**Secrets:**
1. Operator manually generates a secret value (password, key, token).
2. Operator manually stores it in the secret backend (Key Vault, Bitwarden, Vault, etc.).
3. Operator adds the reference to the environment YAML.
4. Pipeline resolves the pre-existing value at build time.

**Variables:**
1. Operator opens the variable store UI (Azure App Config, Consul, etc.).
2. Operator manually creates the key with an initial value.
3. Operator adds the reference to the environment YAML.
4. Pipeline resolves the value at build time.

**Feature flags:**
1. Operator opens the feature flag tool (Flagsmith, Azure App Config, etc.).
2. Operator manually creates the flag with an initial state.
3. Operator adds the reference to the environment YAML.
4. Pipeline resolves the flag at build time.

For secrets that are **internally consumed** (e.g., database passwords between services), there is no reason a human should generate or store them. For variables and feature flags, the YAML already declares the intent — strata can seed the store with a sensible default so operators only need to change values they care about, using their tool of choice.

## Decision Drivers

- Reduce operational toil for values that don't originate from external systems.
- Maintain idempotency — a value written once must persist across subsequent builds/deploys.
- Keep the security posture: secrets land in the configured store (not in YAML or build artifacts any differently than today).
- Let operators manage values in their tool of choice (App Config UI, Vault UI, Flagsmith dashboard) — strata only seeds the initial default.
- Support rotation as a future concern without blocking the initial implementation.

## Considered Options

- **Option A: Seed-on-missing** — ValueController seeds the store when a value is not found, based on a `generate` spec (secrets) or `default` value (variables/features) in the YAML.
- **Option B: Separate `strata values seed` command** — a dedicated CLI command creates missing values before build.
- **Option C: Terraform-native random_password** — delegate secret generation to Terraform's `random_password` resource (secrets only, no variable/feature coverage).

## Decision Outcome

Chosen: **Option A — Seed-on-missing**, because it integrates naturally into the existing resolution flow, requires no extra CLI steps, and keeps value lifecycle management in strata's domain rather than delegating to Terraform state.

### YAML Declaration

**Secrets** — use `generate` to auto-create cryptographically secure values:

```yaml
spec:
  secrets:
    - key: DB_PASSWORD
      store: azure-keyvault
      value: myapp-db-password          # key name in the store
      generate:
        type: password                  # generator type
        length: 32                      # type-specific options
    - key: ENCRYPTION_KEY
      store: azure-keyvault
      value: myapp-encryption-key
      generate:
        type: hex
        length: 64
    - key: SERVICE_TOKEN
      store: azure-keyvault
      value: myapp-service-token
      generate:
        type: uuid4
    - key: SESSION_SECRET
      store: azure-keyvault
      value: myapp-session-secret
      generate:
        type: urlsafe
        length: 48
```

**Variables** — use `default` to seed the store with an initial value:

```yaml
spec:
  variables:
    - key: LOG_LEVEL
      store: azure-appconfig
      value: myapp/log-level            # key name in the store
      default: "info"                   # written to store if key is missing
    - key: MAX_REPLICAS
      store: consul
      value: myapp/max-replicas
      default: "3"
```

**Feature flags** — use `default` to seed the store with an initial state:

```yaml
spec:
  features:
    - key: ENABLE_DARK_MODE
      store: flagsmith
      value: myapp-dark-mode            # flag name in the store
      default: false                    # written to store if flag is missing
    - key: ENABLE_BETA_API
      store: azure-appconfig
      value: myapp-beta-api
      default: true
```

> **Design note:** Secrets use `generate` (with a type and options) because they need
> cryptographic generation. Variables and features use `default` (a literal value)
> because their initial values are known and non-sensitive. Operators can change
> them anytime in their tool of choice — strata only seeds the first value.

### Supported Generator Types

Type names align with the existing `strata secret generate --format` options
(implemented in `commands/secret/generate_secret_command.py`).

| Type           | Output                                            | Options                         |
| -------------- | ------------------------------------------------- | ------------------------------- |
| `urlsafe`      | Base64-URL string (default, safe for tokens)      | `length` (default 32, in bytes) |
| `hex`          | Lowercase hex string                              | `length` (default 32, in bytes) |
| `password`     | Letters + digits + symbols, guaranteed policy mix | `length` (default 32, in chars) |
| `alphanumeric` | Letters + digits only                             | `length` (default 32, in chars) |
| `numeric`      | Digits only (PINs, OTPs)                          | `length` (default 32, in chars) |
| `base64`       | Standard base64 (Kubernetes secrets, Docker auth) | `length` (default 32, in bytes) |
| `uuid4`        | Random UUID v4                                    | (none — length ignored)         |
| `uuid7`        | Time-ordered UUID v7 (RFC 9562)                   | (none — length ignored)         |

Future types (deferred): `rsa-key`, `ed25519-key`, `connection-string`, `certificate`.

> **Note:** The generation logic already exists in
> `src/strata/commands/secret/generate_secret_command.py::generate_secret()`.
> It uses the `secrets` module throughout (cryptographically secure).
> Implementation will extract this to `utils/secret_generator.py` so both the
> CLI command and `ValueController` can import it without violating layer rules
> (controllers must not import from commands).

### Resolution Flow

**Secrets** (`_resolve_secret`):
```
1. Try to read from store (existing behavior)
2. If found → return value (STOP — never overwrite)
3. If NOT found AND item has `generate` spec:
   a. Generate value using the specified type + options
   b. Write value to the store via integration.set_secret(key, value)
      (create-if-not-exists semantics — never overwrite)
   c. Emit audit log entry (action=secret_generated)
   d. Return generated value
4. If NOT found AND no `generate` spec → return error (existing behavior)
```

**Variables** (`_resolve_variable`):
```
1. Try to read from store (existing behavior)
2. If found → return value (STOP — never overwrite, even if default differs)
3. If NOT found AND item has `default`:
   a. Write default to the store via integration.set_variable(key, default)
      (create-if-not-exists semantics — never overwrite)
   b. Emit audit log entry (action=variable_seeded)
   c. Return default value
4. If NOT found AND no `default` → return error (existing behavior)
```

**Features** (`_resolve_feature`):
```
1. Try to read from store (existing behavior)
2. If found → return value (STOP — never overwrite, even if default differs)
3. If NOT found AND item has `default`:
   a. Write default to the store via integration.set_feature(key, default)
      (create-if-not-exists semantics — never overwrite)
   b. Emit audit log entry (action=feature_seeded)
   c. Return default value
4. If NOT found AND no `default` → return error (existing behavior)
```

All three follow the same pattern: **read → miss + spec → seed → return**.
Subsequent builds always read the existing value — the default/generated value
is only written once. Operators can change the value in the store at any time;
strata never overwrites an existing value.

### Consequences

- Good: Zero manual steps for internal secrets — declare intent in YAML, platform handles the rest.
- Good: Variables and features are pre-seeded — operators just update what they need in their preferred tool (App Config UI, Consul UI, Flagsmith dashboard).
- Good: Idempotent — subsequent builds read the existing value; no regeneration or overwrite.
- Good: All values still live in the configured store — no new storage location, same security posture.
- Good: `strata build plan` reports `[generated]` / `[default: X]` from YAML alone (no network). `strata deploy plan` shows richer store-aware status.
- Good: Works with any store that implements `set_secret()`, `set_variable()`, or `set_feature()`.
- Good: Every seed/generate write is recorded in the build audit log — full traceability of what was written, when, and to which store.
- Bad: First build requires write access to the stores (not just read). The audit log makes these writes transparent and reviewable.

> **Store wipe scenario:** If a store is completely wiped, secrets regenerate with
> new values and variables/features re-seed to YAML defaults. This is acceptable
> because a wiped store is already a disaster recovery event — every consumer of
> that store is broken, not just strata-managed values. Strata's seed-on-missing
> actually helps recovery by automatically recreating the values it manages.

## Guardrails

- `generate` (secrets) and `default` (variables/features) are ONLY valid on items with an integration-backed store (not `constant`, not `environment`, not `github`). Validation rejects them on built-in store types.
- `strata build plan` reports `[generated]` (secrets) or `[default: {value}]` (variables/features) based on YAML spec alone — no store connectivity required. `strata deploy plan` can check the store and report `[exists]` vs `[will generate]`.
- `strata build run` performs the actual generate/seed + store write.
- Secret generation uses `secrets` module (Python stdlib) for cryptographic randomness — never `random`.
- Generated secret values are never logged, even at DEBUG level. Default variable/feature values may be logged at DEBUG since they are declared in the YAML (non-sensitive).

### Overwrite Protection

Strata MUST NEVER overwrite an existing value in a store. This is the most critical invariant of the seed-on-missing design.

**Enforcement:**
- The resolution flow always reads first. A write only happens when the read returns "not found".
- Integration `set_secret()` / `set_variable()` / `set_feature()` implementations MUST use **create-if-not-exists** semantics (e.g., Azure Key Vault's `create` vs `update`, Consul's `PUT` with `cas=0`). If the store API doesn't support atomic create-if-not-exists, the integration MUST read-then-write with a warning that a race is theoretically possible.
- There is no `--force` flag to override this. If an operator needs to regenerate a secret, they delete it from the store manually and re-run the build. The audit log records the new generation.
- If a value exists in the store but differs from the YAML `default`, strata uses the store value. The YAML default is only the initial seed — the store is always the source of truth.

### Audit Logging

Every store write (generate or seed-default) MUST produce a structured audit log entry. This ensures full traceability when strata modifies external stores.

**Audit entry fields:**

| Field         | Description                                             | Example                |
| ------------- | ------------------------------------------------------- | ---------------------- |
| `action`      | `secret_generated`, `variable_seeded`, `feature_seeded` | `secret_generated`     |
| `key`         | The store key that was written                          | `myapp-db-password`    |
| `store`       | Store type                                              | `azure-keyvault`       |
| `type`        | Generator type (secrets only)                           | `password`             |
| `default`     | Default value (variables/features only, secrets masked) | `info`                 |
| `deployment`  | Deployment name                                         | `myapp-production`     |
| `environment` | Environment name                                        | `production`           |
| `timestamp`   | ISO 8601 UTC                                            | `2025-06-23T14:30:00Z` |

**Log levels:**
- `INFO` for all seed/generate actions — these are significant operations that operators should see by default.
- Secret audit entries never include the generated value. Variable/feature entries include the default value since it's declared in YAML.

**Output locations:**
- Standard structured log (structlog) — visible in CLI output and CI logs.
- Build artifact metadata — the build output records which values were seeded during this build. `strata deploy plan` can use this to show `[exists]` vs `[will generate]` when the store is reachable.

> **Why audit matters:** Strata is writing to external systems that other teams manage.
> Without an audit trail, a surprise value appearing in Key Vault or App Config
> is unexplainable. The audit log answers: *"Who created this? When? From which deployment?"*

## Security Considerations

- Generated secrets use cryptographically secure randomness (`secrets.token_hex`, `secrets.token_urlsafe`, `uuid.uuid4`).
- Write access to the secret store is required only during `strata build run`, not during deploy.
- The feature does not change where secrets are stored or how they're injected — it only automates the creation step.
- If an operator wants to override an auto-generated secret with a manually chosen value, they write it to the store directly — strata will find it on next build and skip generation.

## Secret Rotation (Phase 3 — Design Sketch)

Rotation applies to secrets only — variables and feature flags don't have a rotation lifecycle.

### Approach: Age-Based Advisory + Opt-In Regeneration

Rotation has two levels, both opt-in via a `rotate:` field on `SecretStoreModel`.
`rotate:` is a **sibling of `generate:`**, not nested inside it. This separates the
creation concern (`generate:`) from the lifecycle concern (`rotate:`) and allows
advisory rotation on manually-placed secrets (see Design Issue #11).

```yaml
secrets:
  # Auto-generated secret with automatic rotation
  - key: DB_PASSWORD
    store: azure-keyvault
    value: myapp-db-password
    generate:
      type: password
      length: 32
    rotate:
      max_age: 90                     # days (integer, not a duration string)
      policy: warn                    # warn | rotate

  # Manually-placed secret with advisory rotation only
  - key: VENDOR_API_KEY
    store: azure-keyvault
    value: myapp-vendor-api-key
    # no generate: — strata cannot regenerate this
    rotate:
      max_age: 180
      policy: warn                    # warn only; policy: rotate is invalid without generate:
```

**Validation rules:**
- `rotate.policy: rotate` requires `generate:` to be present — Pydantic `model_validator` raises a validation error if `generate:` is absent.
- `rotate.policy: warn` is valid with or without `generate:`.
- `rotate:` is rejected on `constant`/`environment`/`github` stores (same rule as `generate:`).

**`policy: warn`** (default, safe) — during `strata deploy run`, if the secret's age exceeds `max_age`, emit a warning:
```
⚠ Secret 'myapp-db-password' is 112 days old (max_age: 90 days). Consider rotating.
```
No action taken. The operator decides when to rotate.

**`policy: rotate`** (automatic) — during `strata build run`, if the secret's age exceeds `max_age`:
1. Generate a new value.
2. Write the new value to the store (this is the ONE controlled exception to "never overwrite").
3. Emit an audit log entry with `action=secret_rotated`.
4. The build uses the new value — all downstream consumers get the new secret on next deploy.

### How Strata Knows the Secret's Age

Strata does NOT maintain its own state for secret age. It relies entirely on the store:

- **If the store exposes creation/modification dates or expiry** (Azure Key Vault: `created`, `updated`, `expires_on`; HashiCorp Vault: `metadata.created_time`; Bitwarden: `revisionDate`) → rotation is supported for that store.
- **If the store does NOT expose timestamps** → rotation is not available. Strata emits a `WARNING` log the first time a rotation check is skipped for a given key (not on every build — deduplicated per key per session). The operator is never silently unaware that their `rotate:` spec is a no-op.

```
⚠ Secret 'myapp-vendor-api-key': store 'bitwarden' does not expose creation
  timestamps — rotation age check skipped. Manage this secret's rotation manually.
```

This keeps the design simple: rotation support is a capability of the store integration, not a strata concern. The integration interface exposes:

```python
@dataclass
class SecretMetadata:
    created: Optional[datetime] = None
    updated: Optional[datetime] = None
    expires_on: Optional[datetime] = None

def get_secret_metadata(self, key: str) -> Optional[SecretMetadata]:
    """Return creation/update/expiry info, or None if the store doesn't support it."""
```

Age is computed as `now() - (metadata.updated or metadata.created)`. Using `SecretMetadata` rather than a bare `timedelta` lets `strata deploy run` surface richer detail (e.g. exact last-updated date) without a second round-trip to the store.

If the integration returns `None`, the `rotate` spec emits the warning above and is otherwise a no-op.

### Interaction with Overwrite Protection

Rotation is the **only** scenario where strata overwrites an existing value. This exception is tightly scoped:

- Only triggered when `rotate.policy` is `rotate` AND age exceeds `max_age`.
- Always emits an audit entry with `action=secret_rotated` (distinct from `secret_generated`).
- `strata deploy run` checks age from the store and reports the warning or performs rotation.
- `strata build plan` is **store-free** — it cannot check actual age. It reports `[rotation configured: Nd / warn]` or `[rotation configured: Nd / rotate]` from YAML only, with no age annotation (see Design Issue #13).
- If `rotate.policy` is `warn` or absent, overwrite protection remains absolute.

### Deployment Coordination

Rotation generates a new secret, but consumers of that secret (databases, APIs, other services) need the new value too. This is the operator's responsibility — strata handles the store, not the consumer migration. However:

- `strata build plan` shows which secrets will rotate, giving operators advance notice.
- The audit log records exactly when rotation happened.
- Future work: a `strata secret rotate --key X --deployment Y` command for explicit, on-demand rotation outside the build cycle.

## Pros and Cons of the Options

### Option B: Separate `strata values seed` command

- Good: Explicit step — operator decides when values are seeded.
- Good: No write access needed during build.
- Bad: Extra CLI step that operators will forget — defeats the goal of reducing toil.
- Bad: Requires a separate command implementation and documentation.
- Bad: Doesn't integrate with the resolution flow — seeds must run before every first build.

### Option C: Terraform-native random_password

- Good: No strata code needed — Terraform handles it.
- Good: Value is in Terraform state (managed lifecycle).
- Bad: Secret value lives in Terraform state (which may be less secure than a dedicated vault).
- Bad: Cannot be referenced by other stages or non-Terraform deployers (e.g., Docker Compose).
- Bad: Doesn't work for secrets needed before Terraform runs (e.g., provider auth).
- Bad: Tight coupling to Terraform — doesn't generalise to Helm, Compose, or script deployers.
- Bad: Only covers secrets — no solution for variables or feature flags.

## Implementation Plan

### Phase 1: Secret generation (generate-on-missing)

1. Extract `generate_secret()` from `commands/secret/generate_secret_command.py` to `utils/secret_generator.py`. Update the CLI command to re-import from `utils/`.
2. Add `SecretGenerateSpec` model (`type`, `length`) to `store_models.py`. Type is a `SecretGenerateType` enum matching the format names above.
3. Add optional `generate: SecretGenerateSpec` field to `SecretStoreModel`.
4. Add model validation: `generate` rejected on `constant`/`environment`/`github` stores.
5. Extend `ValueController._resolve_secret()` with generate-on-missing logic: read → miss + generate spec → generate → `set_secret()` → return.
6. Add plan awareness: `build plan` reports `[generated]` from YAML (no network); `deploy plan` checks store for `[exists]` vs `[will generate]`.
7. Add structured audit logging for every `set_secret()` call (action, key, store, type, deployment, timestamp).
8. Tests for model validation, controller generate-on-missing flow, idempotency (existing value not overwritten), and audit log emission.

### Phase 2: Variable and feature defaults (seed-on-missing)

9. Add optional `default: str` field to `VariableStoreModel`.
10. Add optional `default: str` field to `FeatureStoreModel` (values: `"true"` / `"false"`).
11. Add model validation: `default` rejected on `constant`/`environment` stores.
12. Extend `ValueController._resolve_variable()` with seed-on-missing logic: read → miss + default → `set_variable()` → return.
13. Extend `ValueController._resolve_feature()` with seed-on-missing logic: read → miss + default → `set_feature()` → return.
14. Add structured audit logging for every `set_variable()` / `set_feature()` call.
15. Add `--dry-run` awareness: report `[will seed default]` without side effects.
16. Tests for variable/feature default seeding, model validation, idempotency, and audit log emission.

### Phase 3 Implementation Steps

1. Add `SecretMetadata` dataclass (`created`, `updated`, `expires_on`) to `src/strata/utils/secret_metadata.py`.
2. Add `SecretRotateSpec` model (`max_age: int` (days), `policy: Literal["warn", "rotate"]`) to `store_models.py`.
3. Add optional `rotate: SecretRotateSpec` field to **`SecretStoreModel`** (sibling of `generate:`, NOT inside `SecretGenerateSpec`).
4. Add `model_validator(mode="after")` on `SecretStoreModel`: if `rotate.policy == "rotate"` and `generate` is `None` → validation error.
5. Add `get_secret_metadata(key: str) → Optional[SecretMetadata]` to `ISecretStore` protocol (optional capability — integrations that don't support it return `None`).
6. Implement `get_secret_metadata()` in Key Vault (reads `properties.created_on`, `properties.updated_on`, `properties.expires_on`) and HashiCorp Vault (reads `metadata.created_time`).
7. Extend `ValueController._resolve_secret()`: after a successful read (value found), if `item.rotate` is set → call `integration.get_secret_metadata()` → compute age → check vs `max_age` → warn or rotate.
8. Add `update_secret(key, value)` to `ISecretStore` protocol and implement in Key Vault and Vault integrations (explicit overwrite — distinct from `set_secret()`).
9. Add `strata secret rotate` command (new CLI group `secret`; subcommand `rotate --key K --deployment FILE [--force]`): loads deployment → environment → finds secret by key → requires `generate:` or errors → generates new value → calls `update_secret()` → audit log. `--force` skips the age check and always rotates.
10. Tests for: model validation (policy:rotate without generate: → error), age detection, warn-only path, auto-rotate path, update_secret called not set_secret, audit log emission, rotate command happy path and error paths.

## Open Questions & Design Issues

Issues identified during design review. Must be resolved before moving to `accepted`.

### 1. ~~Race condition between parallel builds~~

If two CI pipelines run `strata build run` simultaneously for the same deployment, both may read "not found" and both attempt to write. Even with create-if-not-exists, the second write either fails (error) or silently succeeds (duplicate work).

**Mitigating factor:** `strata deploy run` already acquires a deployment lock (`_acquire_lock()`) before calling `ValueController.resolve_values()`. If secret generation moves to deploy-time (or if it stays at build-time but the actual deployment is serialized), only one process touches the store at a time for a given deployment.

**Remaining gap:** `strata build run` does NOT have locking today. Two parallel builds could still race. However:
- For variables/features: both write the same `default` value — idempotent, no conflict.
- For secrets: two builds generate *different* random values. First-writer-wins is the correct behavior.

**Decision:** Integration `set_secret()` must handle conflict gracefully: if create-if-not-exists fails (key already exists), re-read and return the existing value. Log a warning. This makes the race harmless regardless of whether locking is present.

### 2. ~~`set_secret()` / `set_variable()` / `set_feature()` don't exist yet~~

The `ISecretStore`, `IVariableStore`, and `IFeatureStore` protocols currently define `get_*` and `list_*` methods. Write methods (`set_*`) are declared but **not implemented** in any concrete integration. Phase 1 requires working write implementations.

**Decision:** Implement `set_*` methods on ALL integrations in the first iteration. Every integration that supports reading a value type must also support writing it. This is the prerequisite work — without it the feature is dead code.

| Integration      | `set_secret()` | `set_variable()` | `set_feature()` |
| ---------------- | -------------- | ---------------- | --------------- |
| azure-keyvault   | ✅ implement    | —                | —               |
| azure-appconfig  | —              | ✅ implement      | ✅ implement     |
| hashicorp-vault  | ✅ implement    | —                | —               |
| hashicorp-consul | —              | ✅ implement      | —               |
| bitwarden        | ✅ implement    | —                | —               |
| infisical        | ✅ implement    | ✅ implement      | —               |
| etcd             | —              | ✅ implement      | —               |
| flagsmith        | —              | —                | ✅ implement     |

### 3. ~~Dry-run and `strata build plan` — how does it know "already seeded"?~~

**Decision:** `strata build plan` does NOT contact the store. It operates purely from the YAML spec:
- Secret with `generate` → reports `[generated]`
- Variable/feature with `default` → reports `[default: {value}]`

No network access, no "already seeded" detection at plan time. The plan phase shows declarative intent only. The `strata deploy plan` phase (which already has store connectivity) can provide richer status such as `[exists]` vs `[will generate]` by actually reading the store.

### 4. ~~Multi-environment secret sharing~~

Two deployments in different environments may reference the **same** store key (e.g., a shared service account). If deployment A generates the secret first, deployment B finds it and uses it — correct. But if they run simultaneously, you're back to issue #1 with divergent generated values.

**Decision:** Documentation + static validation. At runtime, first-writer-wins from issue #1 already handles the race gracefully. For detection:
- `strata validate` (cross-environment scan) detects when the same store key has `generate` in multiple environments and emits a warning: *"Secret key 'X' in store 'Y' has `generate` in multiple environments — only the first build to run will generate it."*
- Docs explicitly warn: shared generated secrets are supported but operators should be aware that the first deployment to run "wins" the generation.

No runtime mitigation beyond what issue #1 provides.

### 5. ~~`default` field type ambiguity for variables~~

`VariableStoreModel.default` is typed as `Any`. But variables are ultimately resolved as strings (env vars are strings). Should `default` be constrained to `str`? What if someone writes `default: 3` (int) — does strata write `"3"` to the store or `3`?

**Decision:** Constrain `default` to `str` on both `VariableStoreModel` and `FeatureStoreModel`. All store values are strings — bools are `"true"` / `"false"`, numbers are their string representation. Pydantic coerces at load time. This matches how env vars work and avoids type ambiguity at the store layer.

### 6. ~~Integration availability at build time~~

The resolution flow assumes the integration is initialized and available. But `_ensure_integrations_initialized()` may fail (missing credentials, network issues). Today this is a read failure. With seed-on-missing, a failure at build time means the secret is neither read NOR generated — the build fails.

**Decision:** Non-issue after issue #3. `strata build plan` doesn't contact the store — it reports from YAML alone, so "store unreachable" never applies at plan time. For `strata build run`, an unreachable integration already fails reads the same way today — seed-on-missing doesn't change the failure mode. Fail loudly is correct.

### 7. ~~Phase 3 rotation contradicts Phase 1 overwrite protection~~

Phase 1 establishes "NEVER overwrite" as the hardest invariant. Phase 3 introduces an exception. This isn't inherently wrong, but the code needs to be very clear about the boundary:
- `set_secret()` in integrations uses create-if-not-exists (Phase 1).
- `rotate_secret()` is a separate method that explicitly overwrites (Phase 3).

If we accidentally reuse `set_secret()` for rotation, the create-if-not-exists semantics would reject the write. The integration interface needs distinct methods for "create new" vs "replace existing".

**Decision:** Confirmed — rotation uses a separate `update_secret()` method, not `set_secret()`. The two methods have explicitly different semantics: `set_secret()` = create-if-not-exists (Phase 1), `update_secret()` = replace-existing (Phase 3 only). This makes the overwrite boundary impossible to cross accidentally.

### 9. ~~Rotation dependency chain — downstream consumers~~

Rotation generates a new secret value, but consumers of that secret (databases, APIs, other services) still hold the old value. Automatic rotation can break running systems if the consumer isn't updated in the same deployment cycle. This is a coordination problem that goes beyond strata's store write:
- Database passwords require the database to accept the new password before consumers switch.
- API keys require the upstream service to recognize the new key.
- Shared secrets across services require all consumers to deploy with the new value simultaneously (or support dual-key validation).

**Decision (Phase 3 — inform only):** Strata can only own the store-side rotation (generate new value, write it). Consumer coordination is out of scope — strata cannot force applications to pick up new secrets. However, some stores natively support rotation overlap:
- **Azure Key Vault:** Supports secret versioning — a new version becomes active while the old version remains valid until its expiry. Applications using the Key Vault SDK can fetch the latest version automatically.
- **HashiCorp Vault:** Supports dynamic secrets and leases with TTLs, enabling graceful overlap periods.

**Affected module detection:** Strata CAN trace which modules consume a rotated secret. The reference chain is explicit: `module.spec.references.secrets[]` declares which secrets a module needs, and `services[].environment[].secret` maps them to service env vars. On rotation, strata can build the reverse map and report: *"Rotated 'DB_PASSWORD' — affects modules: postgres, api-server, worker."*

**Phase 3 approach — inform only:**
- On rotate, `deploy plan` reports affected modules: *"[will rotate] DB_PASSWORD → affects: postgres, api-server, worker — these modules will need a restart to pick up the new value."*
- The audit log records which modules are affected.
- No automatic restart — the operator triggers redeployment of affected modules.
- Automatic restart (`restart: affected` in the rotate spec) is deferred — it's deployer-specific (Helm rolling update vs. Compose restart vs. script-based) and needs its own design around ordering, rollback, and downtime risk. An implicit restart from a YAML spec change is too dangerous without explicit opt-in.

Strata's role in Phase 3 is limited to: (1) writing the new secret version to the store, (2) reporting which modules are affected, (3) documenting that `policy: rotate` requires the operator to restart affected modules. Automatic restart is explicitly out of scope — applications are expected to handle secret refresh themselves using their secret manager SDK/sidecar (e.g., Key Vault CSI driver refresh, Vault Agent auto-renewal, Kubernetes secret rotation). Strata reports; applications react.

### 8. ~~No rollback if `set_secret()` succeeds but the build fails later~~

The build generates a secret, writes it to the store, then continues. If the build fails at a later step (e.g., Terraform plan fails), the secret is already in the store. Next build run reads it successfully — no issue. But the operator might not know the secret was written during a failed build.

**Decision:** Acceptable — no rollback needed. The generated value is valid regardless of whether the build that created it succeeded. The audit log records the write, and `strata deploy plan` shows `[exists]` for values already in the store. Next build reads it and moves on.

### 10. ~~Spec drift — generate spec changed but value already exists~~

The operator changes the `generate` spec in YAML (e.g., `length: 32` → `length: 64`, or `type: password` → `type: hex`), but the secret already exists in the store. With "never overwrite", strata silently uses the old value — the operator has no idea the stored value no longer matches the declared spec.

Similarly for variables/features: the `default` in YAML changed, but the store still holds the original seeded value. This is by design (store is source of truth), but the drift should be visible.

**Decision:** Warn on drift via `deploy plan`. When a value exists in the store AND has a `generate`/`default` spec:
- **Secrets:** `deploy plan` warns: *"Secret 'X' exists in store — generate spec will not be applied. Delete the secret and re-run to regenerate with the new spec."*
- **Variables/features:** `deploy plan` warns when the YAML `default` differs from the store value: *"Variable 'X' in store is '{stored}', YAML default is '{declared}' — store value takes precedence."*
- Strata doesn't track what spec produced the current value — only the store holds the value, not the generation parameters. The warning is informational; overwrite protection remains absolute.

### 11. `rotate:` field placement — sibling of `generate:`, not nested inside it

The original Phase 3 design sketch placed `rotate:` inside `SecretGenerateSpec`:
```yaml
generate:
  type: password
  length: 32
  rotate:           # ← original design
    max_age: 90d
    policy: warn
```

**Problem A:** This makes advisory rotation (`policy: warn`) impossible for manually-placed secrets that have no `generate:` spec. An operator wanting strata to warn them when a vendor API key is 180 days old has no way to express this.

**Problem B:** `generate:` describes *how to create* a secret. `rotate:` describes *when to replace* it. These are separate concerns with different lifecycles — coupling them in the model conflates creation spec with lifecycle policy.

**Decision:** Move `rotate: Optional[SecretRotateSpec]` to be a field on `SecretStoreModel` (sibling of `generate:`). Enforce at the model level: `policy: rotate` requires `generate:` to be present (model validator); `policy: warn` is valid with or without `generate:`. See the updated YAML example above.

### 12. `max_age` type — int (days), not a duration string

The original design used `max_age: 90d` (a duration string like `"90d"`).

**Problem:** Pydantic has no built-in duration string parser. A custom validator is needed to parse `"90d"`, `"30d"`, `"1y"`, etc. This adds complexity and potential edge cases (months? years? weeks?).

**Decision:** `max_age: int` representing days. Simple, unambiguous, no custom validator required. The CLI and plan display can render it as `"90 days"`. Value must be >= 1.

### 13. `strata build plan` cannot show rotation-overdue status

The acceptance criterion in v1-todo.md states: *"`strata build plan` shows `[rotation overdue]` annotation when `max_age` exceeded."*

**Problem:** `strata build plan` is explicitly store-free (Design Issue #3, resolved). It never contacts the store. Age information requires reading store-native metadata at runtime — which is a network operation.

**Decision:** `strata build plan` shows rotation *configuration* from YAML only:
- Secret with `rotate:` → `[rotation: Nd / warn]` or `[rotation: Nd / rotate]`
- No age annotation — the plan phase does not know how old the secret is

Actual age checking and the `[rotation overdue]` annotation happen only in `strata deploy run` (which already has store connectivity for value resolution). The v1-todo.md acceptance criterion must be updated to reflect this.

### 14. `strata secret rotate` command — scope for manually-placed secrets

The `strata secret rotate --key K --deployment F` command needs to handle two cases:

1. **Secret has `generate:` spec** → generate new value using current YAML spec → `update_secret()` → audit log. `--force` bypasses the `max_age` check and always rotates.

2. **Secret has no `generate:` spec** (manually placed) → strata cannot auto-generate a replacement → command exits with an error: *"Secret 'K' has no `generate:` spec — strata cannot regenerate it. Update the secret manually in your store."*

**Decision:** `strata secret rotate` is only for strata-managed (generated) secrets. Manually-placed secrets are the operator's responsibility. The command fails explicitly rather than silently doing nothing.

---

## Detailed Design

This section describes the full implementation across all three phases. Each subsection maps to concrete files, classes, methods, and data structures in the strata codebase.

### Layer Impact Summary

```
models/          ← SecretGenerateSpec, SecretRotateSpec, default fields, store type guards
utils/           ← secret_generator.py (extracted from commands/)
services/        ← No changes (services load models; new fields come for free via Pydantic)
controllers/     ← ValueController: seed-on-missing logic, audit logging, drift detection
integrations/    ← set_secret/set_variable/set_feature on all backends + update_secret (Phase 3)
commands/        ← generate_secret_command re-imports from utils/; plan commands show seed status
```

### 1. Models — `src/strata/models/store_models.py`

#### 1.1 `SecretGenerateType` enum

New enum for generator type names. Values match the existing `--format` options in the CLI command.

```python
class SecretGenerateType(str, Enum):
    URLSAFE = "urlsafe"
    HEX = "hex"
    PASSWORD = "password"
    ALPHANUMERIC = "alphanumeric"
    NUMERIC = "numeric"
    BASE64 = "base64"
    UUID4 = "uuid4"
    UUID7 = "uuid7"
```

#### 1.2 `SecretGenerateSpec` model (Phase 1)

Nested model for the `generate` field on `SecretStoreModel`.

```python
class SecretGenerateSpec(BaseModel):
    type: SecretGenerateType
    length: int = 32

    @field_validator("length")
    @classmethod
    def validate_length(cls, v: int, info: ValidationInfo) -> int:
        if v < 8:
            raise ValueError("length must be >= 8")
        if v > 1024:
            raise ValueError("length must be <= 1024")
        return v
```

Length is ignored for `uuid4`/`uuid7` — the generator handles that (existing behaviour).

#### 1.3 `SecretRotateSpec` model (Phase 3)

Nested model for the `rotate` field on `SecretGenerateSpec`.

```python
class SecretRotatePolicy(str, Enum):
    WARN = "warn"
    ROTATE = "rotate"

class SecretRotateSpec(BaseModel):
    max_age: str  # e.g. "90d", "24h"
    policy: SecretRotatePolicy = SecretRotatePolicy.WARN

    @field_validator("max_age")
    @classmethod
    def validate_max_age(cls, v: str) -> str:
        pattern = r"^\d+[dhm]$"
        if not re.match(pattern, v):
            raise ValueError("max_age must be a number followed by d (days), h (hours), or m (minutes)")
        return v
```

#### 1.4 Updated `SecretStoreModel`

```python
class SecretStoreModel(BaseModel):
    key: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    store: SecretStoreType
    value: Any
    version: Optional[str] = None
    description: Optional[str] = None
    generate: Optional[SecretGenerateSpec] = None          # Phase 1

    @model_validator(mode="after")
    def validate_generate_not_on_builtin(self) -> "SecretStoreModel":
        builtin = {SecretStoreType.CONSTANT, SecretStoreType.ENVIRONMENT, SecretStoreType.GITHUB}
        if self.generate and self.store in builtin:
            raise ValueError(
                f"'generate' is not valid on built-in store type '{self.store.value}'. "
                "Use an integration-backed store (azure-keyvault, vault, bitwarden, infisical)."
            )
        return self

    @model_validator(mode="after")
    def validate_version_not_set_for_github(self) -> "SecretStoreModel":
        # existing validator — unchanged
        ...
```

#### 1.5 Updated `VariableStoreModel` (Phase 2)

```python
class VariableStoreModel(BaseModel):
    key: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    store: VariableStoreType
    value: Any
    version: Optional[str] = None
    description: Optional[str] = None
    default: Optional[str] = None                          # Phase 2

    @model_validator(mode="after")
    def validate_default_not_on_builtin(self) -> "VariableStoreModel":
        builtin = {VariableStoreType.CONSTANT, VariableStoreType.ENVIRONMENT}
        if self.default is not None and self.store in builtin:
            raise ValueError(
                f"'default' is not valid on built-in store type '{self.store.value}'. "
                "Use an integration-backed store (azure-appconfig, consul, vault, infisical, etcd)."
            )
        return self
```

#### 1.6 Updated `FeatureStoreModel` (Phase 2)

```python
class FeatureStoreModel(BaseModel):
    key: Annotated[str, StringConstraints(min_length=1, strip_whitespace=True)]
    store: FeatureStoreType
    value: Any
    version: Optional[str] = None
    description: Optional[str] = None
    default: Optional[str] = None                          # Phase 2

    @model_validator(mode="after")
    def validate_default_not_on_builtin(self) -> "FeatureStoreModel":
        builtin = {FeatureStoreType.CONSTANT, FeatureStoreType.ENVIRONMENT}
        if self.default is not None and self.store in builtin:
            raise ValueError(
                f"'default' is not valid on built-in store type '{self.store.value}'. "
                "Use an integration-backed store (azure-appconfig, flagsmith)."
            )
        return self
```

### 2. Utility — `src/strata/utils/secret_generator.py`

Extract the `generate_secret(fmt, length)` function from `commands/secret/generate_secret_command.py` into a new utility module. The function signature and body stay identical — only the location changes.

```python
# src/strata/utils/secret_generator.py
"""Cryptographic secret generation utilities."""

import base64
import secrets
import string
import time
import uuid


def generate_secret(fmt: str, length: int) -> str:
    """Return a cryptographically secure secret in the requested format.

    This function is the single implementation used by both the CLI command
    (`strata secret generate`) and the ValueController seed-on-missing flow.
    """
    # ... existing body from generate_secret_command.py, unchanged
```

Update `commands/secret/generate_secret_command.py` to re-import:

```python
from strata.utils.secret_generator import generate_secret
```

No functional change — only the import path moves. All existing tests and CLI behaviour remain the same.

### 3. Integration `set_*` Implementations

All store integrations inherit from `StoreIntegration`, which already declares stub `set_secret()`, `set_variable()`, `set_feature()` methods that return `False`. Each integration below overrides only the methods it supports.

#### 3.1 `set_secret()` Implementations

**Semantics:** Create-if-not-exists. If the key already exists, re-read and return the existing value. Never overwrite.

##### Azure Key Vault (`azure_keyvault.py`)

```python
def set_secret(self, key: str, value: str, **kwargs) -> bool:
    timeout: int = kwargs.get("timeout", 60)
    available, error = self.ensure_available()
    if not available:
        logger.warning("Cannot write secret to Azure Key Vault", error=error)
        return False

    # Prefer CLI: `az keyvault secret set --vault-name X --name Y --value Z`
    # If key already exists, the az CLI creates a new version — which is
    # acceptable for create-if-not-exists because Key Vault is versioned.
    # However, to honour "never overwrite", we check existence first.
    existing = self.get_secret(key, timeout=timeout)
    if existing is not None:
        logger.info("Secret already exists — skipping write", secret_name=key)
        return True

    result = self._run_integration(
        ["keyvault", "secret", "set",
         "--vault-name", self._vault_name(),
         "--name", key,
         "--value", value,
         "--output", "none"],
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.warning("Failed to write secret to Azure Key Vault",
                       secret_name=key, stderr=result.stderr)
        return False
    logger.info("Secret written to Azure Key Vault", secret_name=key)
    return True
```

##### HashiCorp Vault (`hashicorp_vault.py`)

```python
def set_secret(self, key: str, value: str, **kwargs) -> bool:
    # Uses `vault kv put` with `-cas=0` (create-only, fails if key exists).
    # If CAS check fails (key exists), re-read and log info. Return True.
    ...
```

##### Bitwarden (`bitwarden.py`)

```python
def set_secret(self, key: str, value: str, **kwargs) -> bool:
    # Uses `bw create item` with a secure note or login item.
    # Check existence first via get_secret().
    ...
```

##### Infisical (`infisical.py`)

Already implemented. No changes needed — existing `set_secret()` uses the API and follows the correct pattern.

#### 3.2 `set_variable()` Implementations

**Semantics:** Create-if-not-exists. Check existence first; if found, skip.

##### Azure App Configuration (`azure_appconfig.py`)

```python
def set_variable(self, key: str, value: Any, **kwargs) -> bool:
    label = kwargs.get("label")
    timeout: int = kwargs.get("timeout", 60)
    available, error = self.ensure_available()
    if not available:
        return False

    existing = self.get_variable(key, label=label, timeout=timeout)
    if existing is not None:
        logger.info("Variable already exists — skipping write", key=key)
        return True

    args = ["appconfig", "kv", "set",
            "--endpoint", self.appconfig_endpoint,
            "--key", key,
            "--value", str(value),
            "--yes",
            "--output", "none"]
    if label:
        args.extend(["--label", label])
    result = self._run_integration(args, timeout=timeout)
    if result.returncode != 0:
        logger.warning("Failed to write variable", key=key, stderr=result.stderr)
        return False
    logger.info("Variable written to Azure App Configuration", key=key)
    return True
```

##### HashiCorp Consul (`hashicorp_consul.py`)

```python
def set_variable(self, key: str, value: Any, **kwargs) -> bool:
    # Uses `consul kv put` with `?cas=0` (create-only).
    # Check existence first via get_variable().
    ...
```

##### HashiCorp Vault (`hashicorp_vault.py`)

```python
def set_variable(self, key: str, value: Any, **kwargs) -> bool:
    # Delegates to set_secret() — Vault treats variables and secrets the same way.
    return self.set_secret(key, str(value), **kwargs)
```

##### etcd (`etcd.py`)

```python
def set_variable(self, key: str, value: Any, **kwargs) -> bool:
    # Uses `etcdctl put` with lease or check-and-set.
    # Check existence first via get_variable().
    ...
```

##### Infisical (`infisical.py`)

Already implemented — delegates to `set_secret()`.

#### 3.3 `set_feature()` Implementations

**Semantics:** Create-if-not-exists. Check existence first; if found, skip.

##### Azure App Configuration (`azure_appconfig.py`)

```python
def set_feature(self, key: str, value: bool, **kwargs) -> bool:
    label = kwargs.get("label")
    timeout: int = kwargs.get("timeout", 60)
    available, error = self.ensure_available()
    if not available:
        return False

    existing = self.get_feature(key, label=label, timeout=timeout)
    if existing is not None:
        logger.info("Feature flag already exists — skipping write", key=key)
        return True

    # `az appconfig feature set --endpoint X --feature Y --yes`
    # Feature flags in App Config are created disabled by default.
    # After creation, enable/disable based on `value`.
    args = ["appconfig", "feature", "set",
            "--endpoint", self.appconfig_endpoint,
            "--feature", key,
            "--yes",
            "--output", "none"]
    if label:
        args.extend(["--label", label])
    result = self._run_integration(args, timeout=timeout)
    if result.returncode != 0:
        logger.warning("Failed to create feature flag", key=key, stderr=result.stderr)
        return False

    # Set enabled/disabled state
    state_cmd = "enable" if value else "disable"
    self._run_integration(
        ["appconfig", "feature", state_cmd,
         "--endpoint", self.appconfig_endpoint,
         "--feature", key,
         "--yes",
         "--output", "none"],
        timeout=timeout,
    )
    logger.info("Feature flag written to Azure App Configuration", key=key, enabled=value)
    return True
```

##### Flagsmith (`flagsmith.py`)

```python
def set_feature(self, key: str, value: bool, **kwargs) -> bool:
    # Uses Flagsmith REST API: POST /api/v1/features/ to create,
    # then POST /api/v1/environments/{env_key}/featurestates/ to set state.
    # Check existence first via get_feature().
    ...
```

### 4. ValueController — Seed-on-Missing Logic

#### 4.1 Updated `_resolve_secret()` (Phase 1)

The existing method resolves built-in stores (constant, environment, github) first — that logic is unchanged. The change is in the integration-backed branch:

```python
def _resolve_secret(self, item: SecretStoreModel) -> Tuple[Optional[Any], Optional[str]]:
    # ... existing constant/environment/github handling unchanged ...

    # Integration-backed store
    self._ensure_integrations_initialized()
    integration = self._get_integration_by_type(item.store.value)
    if integration is None:
        return None, f"Secret '{item.key}': no integration for store '{item.store.value}'"

    value = integration.get_secret(item.value)
    if value is not None:
        return value, None  # EXISTING VALUE — never overwrite

    # Value not found — check for generate spec
    if item.generate is None:
        return None, f"Secret '{item.key}': key '{item.value}' not found in {item.store.value}"

    # Generate and seed
    generated = generate_secret(item.generate.type.value, item.generate.length)
    ok = integration.set_secret(item.value, generated)
    if not ok:
        # set_secret failed — might be a race (another build created it).
        # Re-read: if the key now exists, use that value.
        reread = integration.get_secret(item.value)
        if reread is not None:
            logger.warning(
                "Secret created by another process — using existing value",
                key=item.key,
                store=item.store.value,
            )
            return reread, None
        return None, f"Secret '{item.key}': generation succeeded but store write failed"

    # Audit log
    logger.info(
        "Secret generated and stored",
        action="secret_generated",
        key=item.value,
        store=item.store.value,
        generator_type=item.generate.type.value,
    )
    return generated, None
```

#### 4.2 Updated `_resolve_variable()` (Phase 2)

Same pattern. The integration-backed branch gets a seed-on-missing check:

```python
def _resolve_variable(self, item: VariableStoreModel) -> Tuple[Optional[Any], Optional[str]]:
    # ... existing constant/environment handling unchanged ...

    # Integration-backed store
    self._ensure_integrations_initialized()
    integration = self._get_integration_by_type(item.store.value)
    if integration is None:
        return None, f"Variable '{item.key}': no integration for store '{item.store.value}'"

    value = integration.get_variable(item.value)
    if value is not None:
        return value, None

    # Value not found — check for default
    if item.default is None:
        return None, f"Variable '{item.key}': key '{item.value}' not found in {item.store.value}"

    ok = integration.set_variable(item.value, item.default)
    if not ok:
        reread = integration.get_variable(item.value)
        if reread is not None:
            return reread, None
        return None, f"Variable '{item.key}': store write for default failed"

    logger.info(
        "Variable seeded with default",
        action="variable_seeded",
        key=item.value,
        store=item.store.value,
        default=item.default,
    )
    return item.default, None
```

#### 4.3 Updated `_resolve_feature()` (Phase 2)

```python
def _resolve_feature(self, item: FeatureStoreModel) -> Tuple[Optional[bool], Optional[str]]:
    # ... existing constant/environment handling unchanged ...

    # Integration-backed store
    self._ensure_integrations_initialized()
    integration = self._get_integration_by_type(item.store.value)
    if integration is None:
        return None, f"Feature '{item.key}': no integration for store '{item.store.value}'"

    value = integration.get_feature(item.value)
    if value is not None:
        return value, None

    # Value not found — check for default
    if item.default is None:
        return None, f"Feature '{item.key}': key '{item.value}' not found in {item.store.value}"

    default_bool = item.default.lower() not in ("0", "false", "no", "off")
    ok = integration.set_feature(item.value, default_bool)
    if not ok:
        reread = integration.get_feature(item.value)
        if reread is not None:
            return reread, None
        return None, f"Feature '{item.key}': store write for default failed"

    logger.info(
        "Feature flag seeded with default",
        action="feature_seeded",
        key=item.value,
        store=item.store.value,
        default=item.default,
    )
    return default_bool, None
```

### 5. Plan Commands — Reporting Seed Status

#### 5.1 `strata build plan` (YAML-only, no network)

The plan command reads the deployment's environment spec and reports seed status from the YAML alone:

| Condition              | Report                                   |
| ---------------------- | ---------------------------------------- |
| Secret has `generate`  | `[generated] DB_PASSWORD (password, 32)` |
| Variable has `default` | `[default: "info"] LOG_LEVEL`            |
| Feature has `default`  | `[default: false] ENABLE_DARK_MODE`      |
| No generate/default    | (existing display — no change)           |

This requires no integration access. The plan command reads `SecretStoreModel.generate` and `VariableStoreModel.default` / `FeatureStoreModel.default` directly from the loaded model.

#### 5.2 `strata deploy plan` (store-aware)

The deploy plan has integration access and can provide richer status:

| Condition                            | Report                                                                        |
| ------------------------------------ | ----------------------------------------------------------------------------- |
| Value exists in store                | `[exists] DB_PASSWORD`                                                        |
| Value missing + generate spec        | `[will generate] DB_PASSWORD (password, 32)`                                  |
| Value missing + default              | `[will seed] LOG_LEVEL → "info"`                                              |
| Value missing + no spec              | `[missing] API_KEY ⚠`                                                         |
| Value exists + generate spec present | `[exists — generate spec ignored] DB_PASSWORD`                                |
| Value exists + default differs       | `[exists: "debug"] LOG_LEVEL (YAML default: "info" — store takes precedence)` |

The last two rows implement the drift detection from issue #10.

### 6. Audit Logging

Every store write emits a structured log entry at `INFO` level. The audit entries use the project's standard structlog pattern — keyword arguments for structured context.

#### 6.1 Audit entry fields

```python
logger.info(
    "Secret generated and stored",         # message
    action="secret_generated",             # action type
    key="myapp-db-password",               # store key
    store="azure-keyvault",                # store type
    generator_type="password",             # generator format (secrets only)
    deployment="myapp-production",         # deployment name (from context)
    environment="production",              # environment name (from context)
)
```

For variables and features, the `default` value is included:

```python
logger.info(
    "Variable seeded with default",
    action="variable_seeded",
    key="myapp/log-level",
    store="azure-appconfig",
    default="info",
    deployment="myapp-production",
    environment="production",
)
```

Secret values are **never** logged — not even at DEBUG. Variable/feature defaults are safe to log because they are declared in the YAML (non-sensitive).

#### 6.2 Passing deployment context

`ValueController.resolve_values()` already receives `deployment_service`. The deployment name and environment name are available from the service. The controller stores these in instance variables during `resolve_values()` so the per-item resolvers can include them in audit entries:

```python
def resolve_values(self, deployment_service, strict=False):
    self._deployment_name = deployment_service.get_deployment_name()
    self._environment_name = deployment_service.get_environment_name()
    # ... existing resolution loop ...
```

### 7. Phase 3 — Rotation Design

#### 7.1 `SecretMetadata` dataclass

Returned by integrations that support secret age detection:

```python
@dataclass
class SecretMetadata:
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    version: Optional[str] = None
```

#### 7.2 `get_secret_metadata()` on `StoreIntegration`

```python
class StoreIntegration(BaseIntegration):
    # ... existing methods ...

    def get_secret_metadata(self, key: str, **kwargs) -> Optional[SecretMetadata]:
        """Return secret metadata (age, version) if the store supports it.
        Returns None if the store does not expose metadata."""
        return None  # default — subclasses override
```

Implementations:

| Integration     | Source of `updated_at`                                     | Notes        |
| --------------- | ---------------------------------------------------------- | ------------ |
| Azure Key Vault | `az keyvault secret show --query attributes.updated`       | UTC datetime |
| HashiCorp Vault | `vault kv metadata get -format=json` → `data.updated_time` | RFC 3339     |
| Bitwarden       | `bw get item --query revisionDate`                         | ISO 8601     |
| Infisical       | API response `updatedAt` field                             | ISO 8601     |

#### 7.3 `update_secret()` on `StoreIntegration`

Separate from `set_secret()` to maintain the overwrite protection boundary:

```python
class StoreIntegration(BaseIntegration):
    def update_secret(self, key: str, value: str, **kwargs) -> bool:
        """Replace an existing secret value. Phase 3 rotation ONLY.
        
        Unlike set_secret() (create-if-not-exists), this method explicitly
        overwrites the current value. Must only be called from the rotation
        flow when policy=rotate and age exceeds max_age.
        """
        logger.warning("Store does not support secret update", store=self.integration_type)
        return False
```

Each integration overrides this with a write that replaces the existing value (e.g., `az keyvault secret set` without the existence check, `vault kv put` without `-cas=0`).

#### 7.4 Rotation flow in `_resolve_secret()`

Added after the "value found" branch (step 2 in the resolution flow):

```python
    value = integration.get_secret(item.value)
    if value is not None:
        # Value exists — check rotation (Phase 3)
        if item.generate and item.generate.rotate:
            self._check_rotation(integration, item, value)
        return value, None
```

```python
def _check_rotation(
    self,
    integration: StoreIntegration,
    item: SecretStoreModel,
    current_value: str,
) -> None:
    rotate = item.generate.rotate
    metadata = integration.get_secret_metadata(item.value)

    if metadata is None:
        logger.info(
            "Rotation not supported by store — skipping age check",
            key=item.value,
            store=item.store.value,
        )
        return

    age = self._calculate_age(metadata)
    max_age = self._parse_duration(rotate.max_age)

    if age <= max_age:
        return  # Not yet due

    if rotate.policy == SecretRotatePolicy.WARN:
        logger.warning(
            "Secret exceeds max_age — consider rotating",
            key=item.value,
            store=item.store.value,
            age_days=age.days,
            max_age=rotate.max_age,
        )
        return

    if rotate.policy == SecretRotatePolicy.ROTATE:
        new_value = generate_secret(item.generate.type.value, item.generate.length)
        ok = integration.update_secret(item.value, new_value)
        if not ok:
            logger.error(
                "Secret rotation failed — store write error",
                key=item.value,
                store=item.store.value,
            )
            return
        logger.info(
            "Secret rotated",
            action="secret_rotated",
            key=item.value,
            store=item.store.value,
            age_days=age.days,
            max_age=rotate.max_age,
        )
        # Report affected modules
        self._report_affected_modules(item)
```

#### 7.5 Affected module detection

`_report_affected_modules()` traces the secret reference chain:

```
environment.spec.secrets[].key
  → namespace.spec.modules[].spec.references.secrets[]
    → module.spec.services[].environment[].secret
```

On rotation, the controller builds the reverse map and logs:

```python
def _report_affected_modules(self, item: SecretStoreModel) -> None:
    # Walk loaded modules, find any whose references.secrets[] includes item.key
    affected = [m.name for m in self._modules if item.key in m.get_secret_refs()]
    if affected:
        logger.warning(
            "Rotated secret affects modules — restart required to pick up new value",
            action="rotation_impact",
            key=item.value,
            affected_modules=affected,
        )
```

Apps are expected to handle secret refresh themselves via their secret manager SDK/sidecar. Strata only reports.

### 8. Integration Capability Matrix

Complete matrix of what each integration needs to implement across all phases:

| Integration          | Phase 1        | Phase 2                                      | Phase 3                                    |
| -------------------- | -------------- | -------------------------------------------- | ------------------------------------------ |
| **azure-keyvault**   | `set_secret()` | —                                            | `get_secret_metadata()`, `update_secret()` |
| **azure-appconfig**  | —              | `set_variable()`, `set_feature()`            | —                                          |
| **hashicorp-vault**  | `set_secret()` | `set_variable()` (delegates to `set_secret`) | `get_secret_metadata()`, `update_secret()` |
| **hashicorp-consul** | —              | `set_variable()`                             | —                                          |
| **bitwarden**        | `set_secret()` | —                                            | `get_secret_metadata()`, `update_secret()` |
| **infisical**        | ✅ done         | ✅ done (`set_variable` delegates)            | `get_secret_metadata()`, `update_secret()` |
| **etcd**             | —              | `set_variable()`                             | —                                          |
| **flagsmith**        | —              | `set_feature()`                              | —                                          |

### 9. Test Plan

#### 9.1 Model Tests

- `SecretGenerateSpec` validates type enum, length bounds (8–1024), default 32.
- `SecretGenerateSpec` rejects unknown type strings.
- `SecretStoreModel` with `generate` on `constant`/`environment`/`github` raises `ValueError`.
- `SecretStoreModel` with `generate` on `azure-keyvault` passes validation.
- `VariableStoreModel.default` coerces `int` → `str` via Pydantic.
- `VariableStoreModel` with `default` on `constant`/`environment` raises `ValueError`.
- `FeatureStoreModel` with `default` on `constant`/`environment` raises `ValueError`.
- `SecretRotateSpec` validates `max_age` format (`90d`, `24h`, `30m`; rejects `foo`).

#### 9.2 Controller Tests (mock integrations)

- `_resolve_secret()`: value exists → returns it, no write.
- `_resolve_secret()`: value missing + `generate` → generates, calls `set_secret()`, returns value.
- `_resolve_secret()`: value missing + no `generate` → returns error.
- `_resolve_secret()`: `set_secret()` fails, re-read succeeds → returns re-read value + warning.
- `_resolve_secret()`: `set_secret()` fails, re-read fails → returns error.
- `_resolve_variable()`: value missing + `default` → calls `set_variable()`, returns default.
- `_resolve_feature()`: value missing + `default: "false"` → calls `set_feature(key, False)`.
- Idempotency: second call with same key → `get_*` returns value, no `set_*` call.
- Audit log: verify structured log entries emitted with correct `action`, `key`, `store` fields.

#### 9.3 Integration Tests (mock subprocess)

- Each integration's `set_secret()` / `set_variable()` / `set_feature()` calls the correct CLI command.
- Create-if-not-exists semantics: `set_*` when key exists → returns `True`, no write.
- CLI failure → returns `False`, logs warning.

#### 9.4 Plan Tests

- `build plan` with `generate` spec → output contains `[generated]`.
- `build plan` with `default` → output contains `[default: "X"]`.
- `deploy plan` with value in store + generate spec → output contains drift warning.

### 10. File Change Summary

| File                                                    | Phase | Change                                                    |
| ------------------------------------------------------- | ----- | --------------------------------------------------------- |
| `src/strata/models/store_models.py`                     | 1+2   | Add enums, specs, `generate`/`default` fields, validators |
| `src/strata/utils/secret_generator.py`                  | 1     | New file — extracted from commands                        |
| `src/strata/commands/secret/generate_secret_command.py` | 1     | Re-import from `utils/`                                   |
| `src/strata/controllers/value_controller.py`            | 1+2   | Seed-on-missing logic, audit logging, deployment context  |
| `src/strata/integrations/azure_keyvault.py`             | 1     | `set_secret()`                                            |
| `src/strata/integrations/hashicorp_vault.py`            | 1+2   | `set_secret()`, `set_variable()`                          |
| `src/strata/integrations/bitwarden.py`                  | 1     | `set_secret()`                                            |
| `src/strata/integrations/azure_appconfig.py`            | 2     | `set_variable()`, `set_feature()`                         |
| `src/strata/integrations/hashicorp_consul.py`           | 2     | `set_variable()`                                          |
| `src/strata/integrations/etcd.py`                       | 2     | `set_variable()`                                          |
| `src/strata/integrations/flagsmith.py`                  | 2     | `set_feature()`                                           |
| `src/strata/integrations/store_integration.py`          | 3     | `get_secret_metadata()`, `update_secret()` stubs          |
| `src/strata/integrations/capabilities.py`               | 3     | `ISecretMetadata` protocol                                |
| Plan commands                                           | 1+2   | Seed status in build/deploy plan output                   |
| `tests/strata/models/test_store_models.py`              | 1+2   | Model validation tests                                    |
| `tests/strata/controllers/test_value_controller.py`     | 1+2   | Seed-on-missing flow tests                                |
| `tests/strata/integrations/test_*`                      | 1+2+3 | Per-integration `set_*` tests                             |

---

## Post-v1.0 Gaps & Future Work

This section documents features designed but not implemented in v1.0, explicitly scoped for future releases.

### Gap 1: Build Plan Seed Status Display

**Status:** ✅ DONE

`strata build plan` now shows a **Values** table derived from YAML alone (no store access) with a `status` column: `ok` (built-in store), `seeded` (has `default:`), `generated` (has `generate:` spec), `required` (integration-backed, no default/generate).

`strata deploy run` shows `↳ Seeded on first run: KEY=value` and `↳ Generated on first run: KEY` lines after value resolution, sourced from `ResolvedValues.*_notes`.

**Modified files:** `src/strata/commands/builders/plan_build_command.py`, `src/strata/commands/deploy/run_deploy_command.py`

**Tests:** `TestPlanBuildValueStatus` (13 tests) in `tests/strata/commands/test_commands_build.py`; `TestDeployRunSeedNotes` (5 tests) in `tests/strata/commands/test_commands_deploy.py`.

---

### Gap 2: Missing Integration Implementations

**Status:** ✅ DONE (all 7 integrations implemented)

All Phase 1+2 `set_*` methods are now implemented across every integration:

- ✅ Azure Key Vault (`set_secret()`)
- ✅ Azure App Config (`set_variable()`, `set_feature()`)
- ✅ Bitwarden (`set_secret()`)
- ✅ Infisical (`set_secret()`, `set_variable()`)
- ✅ etcd (`set_variable()`)
- ✅ HashiCorp Vault (`set_variable()`, `set_feature()` via KV prefix)
- ✅ HashiCorp Consul (`set_variable()`, `set_feature()` via KV prefix)
- ✅ Flagsmith (`set_variable()` via identity traits API, `set_feature()` via flag toggle)

All implementations use create-if-not-exists semantics and write structured audit log entries on successful writes.

---

### Gap 3: Secret Rotation (Phase 3)

**Status:** ❌ NOT STARTED (design complete, code not implemented)

The design sketch for rotation (age-based advisory + opt-in regeneration) is documented in this ADR under "Secret Rotation (Phase 3 — Design Sketch)".

**What's missing — the entire Phase 3 implementation:**
1. `SecretRotateSpec` model (`max_age`, `policy` enum) — designed, not coded
2. `get_secret_metadata()` protocol method — designed, not coded
3. `update_secret()` method (distinct from `set_secret()` to preserve overwrite protection) — designed, not coded
4. Age-check logic in `_resolve_secret()` (warn vs. rotate policy) — designed, not coded
5. Affected module detection and reporting — designed, not coded
6. Integration implementations for metadata fetching (Azure Key Vault, Vault, Bitwarden, Infisical) — designed, not coded
7. Integration implementations for `update_secret()` (replacement writes) — designed, not coded
8. Comprehensive test suite for rotation scenarios — designed, not coded
9. `strata build plan` reporting of rotation intent (age warnings) — designed, not coded
10. `strata deploy plan` reporting of affected modules — designed, not coded

**Impact:** High for long-lived deployments with security rotation policies. Low for initial deployments.

Rotation is **opt-in via `generate.rotate` spec** — if not present, Phase 1 behavior (seed-once, never touch again) is standard. Phase 3 is additive; v1 deployments are not affected by its absence.

**Post-v1 action:**
```
Epic: "Secret Rotation — Age-based advisory and automatic rotation"
Phase 3a: Core infrastructure (models, metadata protocol, update methods)
  - Define SecretRotateSpec + enums
  - Add get_secret_metadata() + SecretMetadata dataclass
  - Add update_secret() to store integration base
  - Test model validation, protocol stubs
  - Estimated effort: 6-8 hours

Phase 3b: Integration implementations
  - Azure Key Vault: metadata fetch + update
  - HashiCorp Vault: metadata fetch + update
  - Bitwarden: metadata fetch + update
  - Infisical: metadata fetch + update
  - Estimated effort: 12-16 hours

Phase 3c: Controller logic + reporting
  - Age check in _resolve_secret()
  - Warn vs. rotate logic
  - Affected module detection
  - Audit logging for rotations
  - Estimated effort: 8-10 hours

Phase 3d: Plan and test coverage
  - build plan rotation intent reporting
  - deploy plan affected modules display
  - Comprehensive test suite (age detection, warn, auto-rotate)
  - Estimated effort: 10-12 hours

Total estimated effort for Phase 3: 36-46 hours
```

---

### Gap 4: `SecretRotateSpec` — `rotate:` placement and model validator (Issue #11)

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

The original design nested `rotate:` inside `SecretGenerateSpec`. This was rejected because it makes advisory rotation impossible for manually-placed secrets that have no `generate:` spec.

**Decision:** `rotate: Optional[SecretRotateSpec]` is a field on `SecretStoreModel` — sibling of `generate:`, not nested inside it. A model validator on `SecretStoreModel` enforces: `policy: rotate` requires `generate:` to be present; `policy: warn` is valid with or without `generate:`.

**Implementation required:**
- Move `rotate` field from `SecretGenerateSpec` to `SecretStoreModel`
- Add `model_validator(mode="after")` on `SecretStoreModel` enforcing the `rotate+generate` constraint
- Update YAML schema and any existing tests that reference the old nesting

**Impact:** Any Phase 3 implementation must use the correct model shape — implementing with the old design would silently block advisory rotation on manually-placed secrets.

**Estimated effort:** 2–3 hours (model + validator + tests)

---

### Gap 5: `max_age` type — `int` (days) in `SecretRotateSpec` (Issue #12)

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

The original design used a duration string (`max_age: "90d"`). The decision is `max_age: int` representing days — no custom parser, no ambiguity.

**Decision:** `max_age: int` (days, `>= 1`). The CLI and plan output render it as `"N days"`.

**The code change is one line. The documentation is the real work:**
- This ADR's YAML examples, the Phase 3 design sketch, and `SecretRotateSpec` model docs must all use `max_age: 90` (integer), never `max_age: "90d"` (string)
- `docs/config/workspace.md` (or equivalent store model reference) must document `max_age` as an integer with units clearly stated as days
- `strata schema get` output for the workspace kind must reflect `"type": "integer"` for `max_age`
- All YAML examples in guides, decisions, and inline code comments must be consistent — a single stray `"90d"` example will confuse operators

**Implementation required:**
- `field_validator` on `max_age` ensuring `>= 1` (bundled with Gap 4 model work)
- Audit every YAML example in this ADR and in `docs/` for the old string syntax and update to `int`
- Update JSON schema output to reflect `"type": "integer", "minimum": 1, "description": "Maximum secret age in days"`

**Estimated effort:** 1–2 hours (model: 15 min; documentation sweep: the rest)

---

### Gap 6: Rotation status — `strata secret status`, a dedicated read-only command (Issue #13)

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

The v1-todo.md acceptance criterion stated "`strata build plan` shows `[rotation overdue]` annotation when `max_age` exceeded." This is impossible: `strata build plan` is explicitly store-free (Design Issue #3).

The obvious fallback — `strata deploy plan --rotation` — also doesn't fit: the existing `deploy plan` command reads a saved Terraform `.tfplan` file offline and never contacts a store. Rotation age checking is a store read (calls `get_secret_metadata()`), which is a completely different operation.

**Decision:** Add `strata secret status -f FILE` as a dedicated subcommand in the existing `secret` group. This completes the group's lifecycle story:

```
strata secret generate                       # generate a standalone value (no deployment file)
strata secret status -f deploy.yaml          # check rotation status for all secrets (read-only)
strata secret rotate -f deploy.yaml --key K  # rotate a specific key (write)
```

`strata secret plan` contacts the store, reads `get_secret_metadata()` for every secret that has a `rotate:` spec, and reports:

```
strata secret status -f deploy/deploy-prd.yaml

Secret rotation status:
  DB_PASSWORD     azure-keyvault   90 days / warn    age: 112 days  ⚠ overdue
  SESSION_SECRET  azure-keyvault   90 days / rotate  age: 34 days   ok
  VENDOR_API_KEY  azure-keyvault   180 days / warn   age: unknown   (store has no timestamp)
  API_KEY         azure-keyvault   (no rotate spec)  —
```

Secrets with no `rotate:` spec are listed but show `—` in the rotation column. Operators can see what's tracked vs. what isn't. Exit code `0` = all ok or no rotation specs; exit code `3` = one or more secrets overdue (useful in CI).

**`strata build plan` (Tier 1 — YAML only, no store):**
Still shows rotation *config* from YAML in the values detail column: `[rotation: 90d / warn]`. No age. Purely declarative — unchanged from today.

**`strata deploy run` (Tier 3 — in-band, CI/CD visible):**
During value resolution, `deploy run` already has store connectivity and emits the seeded/generated notes inline. Rotation warnings follow the same pattern — they appear in the same output block, directly in CI/CD logs:

```
✓ Resolved 14 values (3 secrets, 8 variables, 3 features)
  ↳ Generated on first run: DB_PASSWORD
  ↳ Seeded on first run: LOG_LEVEL=info, MAX_REPLICAS=3
  ↳ Rotation advisory: SESSION_SECRET is 112 days old (max: 90 days) — consider rotating
  ↳ Rotated: ENCRYPTION_KEY (was 97 days old, policy: rotate)
```

- `policy: warn`, overdue → emit `↳ Rotation advisory: KEY is N days old (max: M days) — consider rotating`. Deploy continues normally.
- `policy: rotate`, overdue → call `update_secret()`, emit `↳ Rotated: KEY (was N days old, policy: rotate)`. This is the only write path for automatic rotation.
- Both are visible in stdout and structured audit log — no extra flags needed.

**The key principle:** age checking = read-only store access = `strata secret status`, never gated behind a write/apply.

**Implementation required:**
- New `src/strata/commands/secret/status_secret_command.py` extending `BaseCommand`
- Register as `@secret_group.command(name="status")` in `src/strata/commands/cli_secret.py`
- Required options: `--deployment` / `-f`, `--work-path`, `--stage` (optional, limit to one stage's environment)
- Logic: load deployment → resolve environment → for each secret with `rotate:` → call `get_secret_metadata()` → compute age → compare to `max_age` → build status table
- Exit 3 if any secret is overdue (allows `strata secret status -f FILE || alert`)
- `run_deploy_command.py`: after value resolution, collect rotation outcomes from `ResolvedValues` and emit `↳ Rotation advisory` / `↳ Rotated` lines (same block as seeded/generated notes)
- `plan_build_command.py`: update `_build_value_status_rows()` to show `[rotation: Nd / policy]` in detail column from YAML (no store access)
- Update v1-todo.md acceptance criterion for this item

**Estimated effort:** 5–7 hours

---

### Gap 7: `strata secret rotate` command — add to existing `secret` group (Issue #14)

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

**Command home:** The `secret` group already exists with `generate` and `mask` subcommands (`src/strata/commands/cli_secret.py`, `commands/secret/`). `rotate` is a natural peer alongside `status` (Gap 6) — no new group needed.

```
strata secret generate   ← already exists (standalone, no deployment file)
strata secret mask       ← already exists (standalone)
strata secret get        ← new (Gap 9): direct store read
strata secret status     ← new (Gap 6): read-only rotation status check
strata secret put        ← new (Gap 8): create / explicit seed
strata secret rotate     ← new (Gap 7): update / cycle
```

**Two use cases for `strata secret rotate --key K --deployment F`:**

1. **Secret has `generate:` spec** — strata can auto-generate a replacement:
   - Generate new value using the current YAML spec
   - Call `update_secret()` (not `set_secret()` — explicit overwrite, see Issue #7)
   - Write audit log entry (`action=secret_rotated`, affected modules list)

2. **No `generate:` spec** (manually placed) — strata cannot produce a replacement:
   - Exit with explicit error: *"Secret 'K' has no `generate:` spec — strata cannot regenerate it. Update the secret manually in your store."*
   - Clear failure, not a silent no-op

**Relationship to Gap 6:**
`strata secret rotate` is the **on-demand write** path. The full rotation surface is:
- `strata build plan` → YAML config only (Tier 1, Gap 6)
- `strata secret status -f FILE` → read-only age check, exit 3 if overdue (Tier 2, Gap 6)
- `strata deploy run` → emits advisory/rotation notes inline in CI/CD output (Tier 3, Gap 6)
- `strata secret rotate -f FILE --key K` → explicit on-demand rotation outside a build cycle (Gap 7)

Typical workflow:
```
strata secret status -f deploy.yaml                                  # check ages (read-only)
strata secret rotate --key DB_PASSWORD --deployment deploy.yaml      # act on it
strata secret rotate --key DB_PASSWORD --deployment deploy.yaml --force  # ignore max_age, always rotate
```

**`--force`** bypasses the `max_age` guard entirely and always rotates, regardless of current age. Useful for emergency rotation or after a suspected compromise.

**Implementation required:**
- New `src/strata/commands/secret/rotate_secret_command.py` extending `BaseCommand`
- Register as `@secret_group.command(name="rotate")` in `src/strata/commands/cli_secret.py`
- Required options: `--key` (secret key name), `--deployment` / `-f` (deployment YAML path), `--work-path`, `--force`
- Logic: load deployment → resolve environment → find `SecretStoreModel` by `key` → require `generate:` or fail → call `update_secret()` → audit log → report affected modules
- Tests: rotate with spec, rotate without spec (expect exit 3), rotate with `--force` skips age check

**Estimated effort:** 6–8 hours

---

### Gap 8: `strata secret put` command — explicit write / pre-seed

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

The current `secret` group has no write path for manually-placed secrets and no way to explicitly pre-seed a generated secret before a `deploy run`. Today:

- **Manually-placed secrets** (no `generate:` spec) must be written via the store UI — no `strata` CLI command exists for it.
- **Generated secrets** are seeded automatically on first `deploy run` (seed-on-missing). An operator bootstrapping a new environment must trigger a full deploy; there is no way to pre-seed explicitly.
- `strata secret rotate` does not cover either case — it requires the secret to already exist and only works for `generate:` secrets.

**Decision:** Add `strata secret put -f FILE --key K` as a new subcommand in the existing `secret` group. This completes the CRUD story for the group:

```
strata secret generate   ← generate, no store (standalone)
strata secret mask       ← standalone
strata secret get        ← direct store read (Gap 9)
strata secret status     ← rotation status (live store check) (Gap 6)
strata secret put        ← create / explicit seed (Gap 8)
strata secret rotate     ← update / cycle (Gap 7)
```

**Command interface:**

```
strata secret put -f FILE --key K --value VALUE    # write a literal value (manually-placed secrets)
strata secret put -f FILE --key K --generate       # generate using spec + write (generated secrets)
```

- `--value` and `--generate` are mutually exclusive; exactly one is required.
- `--generate` requires the secret to have a `generate:` spec in the YAML; fails explicitly otherwise.
- If the secret **already exists** in the store: fail by default — `put` is a create/seed operation, not a rotation.
- `--force` overrides the existence check and writes regardless (upsert). Useful for importing an externally generated value or overriding a generated secret with a known value.

**Semantic separation from `secret rotate`:**

| Command         | Semantic           | Requires existing?                    | Write path               |
| --------------- | ------------------ | ------------------------------------- | ------------------------ |
| `secret put`    | seed / bootstrap   | no (fails if exists unless `--force`) | any secret               |
| `secret rotate` | cycle to new value | yes (fails if missing)                | `generate:` secrets only |

The one overlap — `secret put --generate --force` on an existing secret — is functionally identical to `secret rotate --force`. This duplication is acceptable: `put` expresses bootstrap intent; `rotate` expresses maintenance/compliance intent. Operators pick whichever matches their mental model.

**Implementation required:**
- New `src/strata/commands/secret/put_secret_command.py` extending `BaseCommand`
- Register as `@secret_group.command(name="put")` in `src/strata/commands/cli_secret.py`
- Required options: `--key` (secret key name), `--deployment` / `-f` (deployment YAML path), `--value VALUE` or `--generate` (mutually exclusive, use Click's `cls=MutuallyExclusiveOption` or manual validation), `--work-path`, `--force`
- Logic: load deployment → resolve environment → find `SecretStoreModel` by `key` → validate `--generate` requires `generate:` spec → check existence (`get_secret_metadata()`) → fail if exists and no `--force` → generate value or use `--value` → call `set_secret()` (create) or `update_secret()` (overwrite when `--force`) → audit log
- Tests: put with `--value` (missing → created), put with `--generate` (missing → generated + created), fail when exists without `--force`, succeed with `--force`, `--generate` without spec → exit 3

**Estimated effort:** 4–6 hours

---

### Gap 9: `strata secret get` command — direct store read

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

`strata values get` resolves a value through the full deployment chain — YAML → store → seed-on-missing. It is deployment-scoped, covers all value types, and can trigger a write as a side effect.

`strata secret get` is store-scoped: it reads the raw value directly from the store without going through value resolution and without triggering seeding. The key use cases are:

- **Verify after `secret put`** — confirm the value actually landed in the store before running a deploy
- **Debug a specific key** — inspect the raw store value without running the full resolution chain
- **Read without side effects** — a guaranteed non-mutating read, useful in audit or read-only CI contexts

**Command interface:**

```
strata secret get -f FILE --key K            # read the raw store value for secret K
strata secret get -f FILE --key K --masked   # print the value masked (for safe logging)
```

- `--key K` is the deployment YAML key name (e.g., `DB_PASSWORD`), not the store key name. The command resolves the store reference from the YAML and reads directly.
- If the secret does not exist in the store: exit 3 with a clear message. No seeding.
- `--masked` prints the value as `****` — useful for confirming a value is present without exposing it in terminal history.
- Output: the raw store value to stdout (or masked form). Structured JSON output with `--output json`.

**Distinction from `strata values get`:**

|                   | `strata values get`          | `strata secret get`          |
| ----------------- | ---------------------------- | ---------------------------- |
| Scope             | deployment (all value types) | store (secrets only)         |
| Triggers seeding? | yes, if missing + spec       | no — pure read               |
| Returns           | resolved value (post-chain)  | raw store value              |
| Use case          | deployment verification      | store inspection / debugging |

**Implementation required:**
- New `src/strata/commands/secret/get_secret_command.py` extending `BaseCommand`
- Register as `@secret_group.command(name="get")` in `src/strata/commands/cli_secret.py`
- Required options: `--key` (deployment YAML key name), `--deployment` / `-f`, `--work-path`, `--masked`
- Logic: load deployment → resolve environment → find `SecretStoreModel` by `key` → initialize store integration → call `integration.get_secret(item.value)` directly (no resolution chain, no seeding) → print value or masked form → exit 3 if not found
- Tests: key exists → value printed, key missing → exit 3, `--masked` → value masked, store unavailable → exit 1

**Estimated effort:** 3–5 hours

---

### Gap 10: `strata secret list` command — YAML inventory (no store)

**Status:** ❌ NOT STARTED (design decision made, implementation pending)

`strata secret status -f FILE` (Gap 6) contacts the store to check live rotation ages. There is no store-free command that answers the simpler question: *"what secrets does this deployment declare?"* That gap belongs to `secret list`.

`strata secret list -f FILE` reads only the YAML — no store credentials, no network I/O — and renders a table of every secret in the deployment:

```
strata secret list -f deploy/deploy-prd.yaml

Secrets in deploy/deploy-prd.yaml (environment: production):
  KEY              STORE             STORE KEY                  GENERATE          ROTATE
  DB_PASSWORD      azure-keyvault    myapp-db-password          password / 32     90d / warn
  ENCRYPTION_KEY   azure-keyvault    myapp-encryption-key       hex / 64          90d / rotate
  SESSION_SECRET   azure-keyvault    myapp-session-secret       urlsafe / 48      —
  VENDOR_API_KEY   azure-keyvault    myapp-vendor-api-key       —                 180d / warn
  API_KEY          azure-keyvault    myapp-api-key              —                 —
```

**Distinction from `secret status`:**

|                    | `strata secret list`         | `strata secret status`          |
| ------------------ | ---------------------------- | ------------------------------- |
| Store access       | ❌ none                       | ✅ reads metadata                |
| Shows              | YAML declaration             | live age + overdue status       |
| Exit 3?            | no                           | yes (if overdue)                |
| Needs credentials? | no                           | yes                             |
| Use case           | audit, scripting, no-cred CI | rotation health check, alerting |

This command is also useful as a complement to `secret put` and `secret get` — operators can run `secret list` first to see all key names before targeting one with `get` or `put`.

**Completes the full `secret` group:**

```
strata secret generate   ← generate value, no store (standalone)
strata secret mask       ← mask for safe display (standalone)
strata secret list       ← YAML inventory, no store (Gap 10)
strata secret get        ← direct store read, no seeding (Gap 9)
strata secret status     ← live rotation health check (Gap 6)
strata secret put        ← create / bootstrap (Gap 8)
strata secret rotate     ← cycle to new value (Gap 7)
```

**Implementation required:**
- New `src/strata/commands/secret/list_secret_command.py` extending `BaseCommand`
- Register as `@secret_group.command(name="list")` in `src/strata/commands/cli_secret.py`
- Required options: `--deployment` / `-f`, `--work-path`, `--stage` (optional, limit to one stage)
- Logic: load deployment → resolve environment → iterate `env.spec.secrets` → build table rows from YAML fields only (`key`, `store`, `value`, `generate`, `rotate`) → render table
- No integration initialization, no network calls
- Tests: deployment with mixed secrets (generate, rotate, neither) → correct columns; `--stage` filters correctly

**Estimated effort:** 2–3 hours

---

### Intentionally Excluded: `strata secret delete`

`secret delete` was considered during design review and explicitly excluded. This decision is recorded here so it is not re-litigated.

**1. The force-regeneration flow already exists.**
Delete the key in the store manually, re-run `strata deploy run` — strata seeds a fresh generated value via seed-on-missing. That friction is intentional: a human hand on the delete operation is the right guard before dropping a live database password or encryption key.

**2. Store UIs provide the right safety rails.**
Azure Key Vault mandates a 90-day soft-delete recovery window (since API 2021-10-01) with optional Purge Protection. HashiCorp Vault has three explicit tiers — `kv delete` (soft, reversible), `kv destroy` (permanent version data), `kv metadata delete` (permanent all versions) — with deliberately different permission requirements at each tier. These UIs and their friction exist specifically because deletion is dangerous. A `--force` flag does not replicate them.

**3. Integration complexity makes a safe implementation non-trivial.**
Bitwarden Secrets Manager identifies secrets by UUID, not key name: deletion requires a two-step `bws secret list → find UUID → bws secret delete <uuid>` call chain with no recovery. Azure Key Vault soft-delete **reserves the deleted name** for the entire retention window — you cannot create a new secret with the same name until purged, which directly breaks the `delete + re-seed` workflow. Infisical and etcd have permanent deletion with a 404-on-mismatch that is indistinguishable from a successful delete.

**4. Environment teardown belongs in `strata deploy destroy`.**
If an operator is decommissioning an environment, that operation belongs in `deploy destroy`, which already carries appropriate lifecycle guardrails. A standalone `secret delete` operating outside the deployment lifecycle is a footgun with no natural scope boundary.

**If `secret delete` is revisited in the future**, Basher’s integration analysis provides the non-negotiable guardrails: soft-delete defaults for AKV (never call `purge` by default), `kv delete` only for Vault (never `kv metadata delete` without an explicit flag), UUID lookup for Bitwarden, existence check before all operations, and `--force` required for any permanently destructive path.

**Note on the `plan` → `status` rename (related):** The `plan` verb in strata has a strict contract: store-free, YAML-only, no network I/O (Design Issue #3). The original `secret plan` violated that contract by calling `get_secret_metadata()` over the wire. Renamed to `secret status` — consistent with `deploy status` — to make the live-store-read semantics explicit and preserve `plan`’s meaning across the codebase.

---

### Summary: v1.0 vs Post-v1.0

| Feature                                                        | v1.0 Status   | Gap Impact | Post-v1 Effort  |
| -------------------------------------------------------------- | ------------- | ---------- | --------------- |
| Secret generation                                              | ✅ Implemented | None       | —               |
| Variable defaults                                              | ✅ Implemented | None       | —               |
| Feature flag defaults                                          | ✅ Implemented | None       | —               |
| Build plan seed display                                        | ✅ Done        | None       | —               |
| Vault `set_*` methods                                          | ✅ Done        | None       | —               |
| Consul `set_*` methods                                         | ✅ Done        | None       | —               |
| Flagsmith `set_*` methods                                      | ✅ Done        | None       | —               |
| Rotation core (Phase 3 — Gap 3)                                | ❌ Not started | High       | 36–46h          |
| `SecretRotateSpec` model shape (Gap 4 + 5)                     | ❌ Not started | High       | 2–4h            |
| `build plan` / `secret status` / `deploy run` rotation (Gap 6) | ❌ Not started | Low        | 5–7h            |
| `strata secret rotate` command (Gap 7)                         | ❌ Not started | Medium     | 6–8h            |
| `strata secret put` command (Gap 8)                            | ❌ Not started | Low        | 4–6h            |
| `strata secret get` command (Gap 9)                            | ❌ Not started | Low        | 3–5h            |
| `strata secret list` command (Gap 10)                          | ❌ Not started | Low        | 2–3h            |
| **Total post-v1.0 backlog**                                    |               |            | **58–79 hours** |

**v1.0 is production-ready** for seed-on-missing across all supported integrations. Secret rotation and its supporting model/command work are the only remaining gaps, explicitly deferred to post-v1.0 releases.

## Final Design

This section is the authoritative summary of all design decisions made across the ADR and the post-v1.0 gap reviews. It describes the complete intended state of the feature — the target all implementation phases are building toward.

---

### The `strata secret` Command Group

The `secret` group manages the full lifecycle of secrets declared in a deployment YAML. Commands are ordered by store access level — from no store contact to write operations:

| Command                                 | Store access | Write?      | Purpose                                                                   | Gap      |
| --------------------------------------- | ------------ | ----------- | ------------------------------------------------------------------------- | -------- |
| `secret generate`                       | ❌ none       | —           | Generate a cryptographically secure value standalone (no deployment file) | existing |
| `secret mask`                           | ❌ none       | —           | Mask a value for safe display or logging                                  | existing |
| `secret list -f FILE`                   | ❌ none       | —           | YAML inventory — what secrets does this deployment declare?               | Gap 10   |
| `secret get -f FILE --key K`            | ✅ read       | —           | Direct store read without triggering seeding                              | Gap 9    |
| `secret status -f FILE`                 | ✅ read       | —           | Live rotation health check — age vs `max_age`, exit 3 if overdue          | Gap 6    |
| `secret put -f FILE --key K`            | ✅ write      | ✅ create    | Explicit seed: write a literal value or generate + write a new secret     | Gap 8    |
| `secret put -f FILE --key K --generate` | ✅ write      | ✅ create    | Explicit seed: generate + write a new secret                              | Gap 8    |
| `secret rotate -f FILE --key K`         | ✅ write      | ✅ overwrite | On-demand rotation: re-generate a `generate:`-spec secret                 | Gap 7    |

**`secret delete` is intentionally excluded** — see "Intentionally Excluded: `strata secret delete`".

**The `plan` verb contract** is preserved throughout: any `strata * plan` command is store-free, YAML-only, and never contacts an integration. `secret status` (live store read) was explicitly named to avoid violating this contract.

---

### YAML Model Shape

The final `SecretStoreModel` has `generate:` and `rotate:` as **siblings** — separate concerns with separate lifecycles:

```yaml
secrets:
  # Auto-generated secret with rotation policy
  - key: DB_PASSWORD
    store: azure-keyvault
    value: myapp-db-password
    generate:
      type: password
      length: 32
    rotate:
      max_age: 90        # days (integer >= 1)
      policy: warn       # warn | rotate

  # Manually-placed secret with advisory rotation only
  - key: VENDOR_API_KEY
    store: azure-keyvault
    value: myapp-vendor-api-key
    # no generate: — strata cannot regenerate this
    rotate:
      max_age: 180
      policy: warn       # policy: rotate is invalid without generate:

  # Secret with generation, no rotation policy
  - key: SESSION_SECRET
    store: azure-keyvault
    value: myapp-session-secret
    generate:
      type: urlsafe
      length: 48

  # Plain secret — manually placed, no strata lifecycle management
  - key: THIRD_PARTY_KEY
    store: azure-keyvault
    value: myapp-third-party-key
```

**Model invariants (enforced by Pydantic validators):**
- `rotate.policy: rotate` requires `generate:` to be present — validation error otherwise
- `rotate.policy: warn` is valid with or without `generate:`
- Both `generate:` and `rotate:` are rejected on built-in store types (`constant`, `environment`, `github`)
- `max_age` is an `int` (days, `>= 1`) — never a duration string like `"90d"`

---

### Value Resolution Lifecycle

The complete flow for a secret, from declaration to resolved value:

```
strata deploy run
│
├── 1. Read from store
│   ├── Found → return value
│   │   └── rotate: spec present?
│   │       ├── No → done
│   │       └── Yes → get_secret_metadata()
│   │           ├── age <= max_age → done
│   │           ├── age > max_age, policy: warn
│   │           │   └── emit ↳ Rotation advisory (stdout + audit log) → done
│   │           └── age > max_age, policy: rotate
│   │               └── update_secret() → emit ↳ Rotated (stdout + audit log) → return new value
│   │
│   └── Not found
│       ├── No generate: spec → error (missing required secret)
│       └── generate: spec present
│           └── generate_secret() → set_secret() → emit ↳ Generated on first run → return value
│
└── All values resolved → continue deploy
```

**Key invariants in the flow:**
- `set_secret()` = create-if-not-exists — NEVER overwrites
- `update_secret()` = explicit overwrite — Phase 3 rotation ONLY
- Secret values are never logged at any level
- Every store write emits a structured audit log entry

---

### The Rotation Surface (4 Tiers)

Rotation information is surfaced at four levels, each with different store access and scope:

| Tier | Command                                | Store access          | What it shows                                              | Exit 3?                     |
| ---- | -------------------------------------- | --------------------- | ---------------------------------------------------------- | --------------------------- |
| 1    | `strata build plan`                    | ❌ YAML only           | `[rotation: 90d / warn]` — declarative config              | no                          |
| 2    | `strata secret status -f FILE`         | ✅ read-only           | Live age table, marks overdue secrets                      | yes                         |
| 3    | `strata deploy run`                    | ✅ (already connected) | `↳ Rotation advisory` / `↳ Rotated` inline in CI/CD output | no (warn) / writes (rotate) |
| 4    | `strata secret rotate -f FILE --key K` | ✅ write               | On-demand rotation outside a deploy cycle                  | —                           |

---

### Integration Contract

Two methods, explicitly different semantics — the overwrite boundary is enforced at the method level:

| Method                      | Semantics                                                                  | When called                                   | Policy  |
| --------------------------- | -------------------------------------------------------------------------- | --------------------------------------------- | ------- |
| `set_secret(key, value)`    | Create-if-not-exists. If key exists, re-read and return. NEVER overwrites. | Seed-on-missing (deploy run, secret put)      | Phase 1 |
| `update_secret(key, value)` | Explicit overwrite. Replaces current value unconditionally.                | Rotation only (policy: rotate, secret rotate) | Phase 3 |

Calling `set_secret()` from rotation code is a bug — the create-if-not-exists semantics would silently no-op the rotation. The distinct method names make this impossible to do accidentally.

---

### Implementation Sequence

Gaps must be implemented in this order due to dependencies:

```
Phase 0 — Prerequisites (implement first, everything else depends on these)
  Gap 4: SecretRotateSpec model + rotate: field on SecretStoreModel
  Gap 5: max_age: int validator + documentation sweep

Phase 1 — Core rotation infrastructure
  Gap 3a: SecretMetadata dataclass + get_secret_metadata() protocol
  Gap 3b: Integration implementations (AKV, Vault, Bitwarden, Infisical)
  Gap 3c: Age-check logic in _resolve_secret() + update_secret() method + audit logging
  Gap 3d: build plan / deploy plan rotation reporting + test suite

Phase 2 — CLI commands (parallel, no inter-dependencies after Phase 0)
  Gap 6:  secret status command + deploy run rotation advisory output
  Gap 7:  secret rotate command
  Gap 8:  secret put command
  Gap 9:  secret get command
  Gap 10: secret list command   ← simplest, can be done anytime after Phase 0
```

Gap 10 (`secret list`) only requires the model fields from Phase 0 — it can be implemented before Phase 1 as a low-risk warm-up. Gap 6 (`secret status`) depends on Phase 1 (`get_secret_metadata()` must exist). Gaps 7–9 depend on `update_secret()` (Gap 7) or store integrations already existing (Gaps 8, 9).

---

### Acceptance Criteria (full feature)

The feature is complete when all of the following are true:

**Model:**
- [x] `SecretStoreModel` has `rotate: Optional[SecretRotateSpec]` as a sibling of `generate:`
- [x] `rotate.policy: rotate` without `generate:` → Pydantic validation error
- [x] `max_age` is `int` (days, `>= 1`) with a `field_validator`
- [x] All YAML examples in this ADR and `docs/` use integer `max_age`

**Integration contract:**
- [x] `set_secret()` on all integrations uses create-if-not-exists semantics
- [x] `update_secret()` exists as a distinct method from `set_secret()`
- [x] `get_secret_metadata()` implemented on AKV, HashiCorp Vault, Bitwarden, Infisical

**Rotation logic:**
- [x] `deploy run` checks rotation age after a successful value read
- [x] `policy: warn`, overdue → emits `↳ Rotation advisory` line, deploy continues
- [x] `policy: rotate`, overdue → calls `update_secret()`, emits `↳ Rotated` line
- [x] Store with no timestamp support → skips rotation check gracefully, no error

**Commands (all exit codes verified):**
- [x] `secret list -f FILE` → YAML table, no store contact, exit 0
- [x] `secret get -f FILE KEY` → raw value or `--unmask`, exit 1 if missing
- [x] `secret status -f FILE` → age table, exit 3 if any overdue
- [x] `secret put -f FILE KEY --value V` → creates via `set_secret()` (create-if-not-exists)
- [x] `secret put -f FILE KEY --generate` → generates from spec + creates
- [x] `secret rotate -f FILE KEY` → rotates via `update_secret()`, requires `generate:` spec, `--force` skips confirmation
- [x] `secret rotate` on manually-placed secret → explicit error, exit 1

**Plan commands:**
- [x] `build plan` shows `[rotation: Nd / policy]` from YAML (no store access)
- ~~`deploy plan` shows affected modules for secrets that will rotate~~ — removed: `deploy plan` is store-free by contract (Design Issue #3). Affected module tracing belongs in `deploy run` output or `secret status` if needed in the future.

**Tests:**
- [x] All model validation invariants covered (26 tests in `test_secret_rotation.py`)
- [x] Rotation warn path: advisory emitted, value unchanged
- [x] Rotation rotate path: `update_secret()` called (not `set_secret()`), failure returns old value
- [x] All 7 `secret` commands: happy path + error paths + exit code assertions