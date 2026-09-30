# Values & Secrets Commands — Design

- Status: **implemented — all 8 phases of the Implementation Plan are
  done.** Every subcommand (`get`, `list`, `resolve`, `set`, `rotate`,
  `status`, `generate`, `mask`) is built, along with the
  `StoreIntegration.set()`/`.metadata()` write capability + `SecretMetadata`
  it's built on.
- Last updated: 2026-09-30

## Overview

v1 exposes runtime variable/secret/feature-flag management through two
separate command groups that share one resolution engine
(`ValueController`) but serve different audiences:

- **`strata values {list,get,set,resolve}`** — read/write the values a
  *deployment* resolves (variables, secrets, features together), scoped to
  one deployment file.
- **`strata secret {generate,mask,list,get,status,put,rotate}`** — secret-
  specific lifecycle operations: cryptographic generation, display masking,
  and store writes/rotation, some of which need no deployment context at
  all (`generate`, `mask` are pure utilities).

v2 has built only `values get` so far
([suggested rebuild order](../../.), confirmed in
`/memories/repo/v1-consumer-usage.md`: "`values get` — DONE", everything
else in both groups is unbuilt). This doc catalogs v1's real behavior for
both groups end-to-end, compares it against v2's current state, and
proposes a phased plan for the rest.

**Decision (2026-09-30): v2 does not port a separate `secret` command
group.** Every `secret` subcommand's behavior is folded into `strata
values` instead — see "v2 Design: a single `values` command" below for
the full mapping and the resulting unified CLI surface. Two v1 groups
become one because the split was never really about *capability*, it was
about *audience framing* over the same `ValueController`: every `secret`
subcommand is either strictly narrower than an existing/planned `values`
subcommand (`secret get` = `values get` scoped to secrets only), a
different access mode of the same operation (`secret list`'s offline,
no-store-access read vs. `values list`'s live-resolve read), or a small
addition with no reason to be its own top-level noun (`generate`/`mask`
are utilities; `put`/`rotate`/`status` are secret-only write/health
operations). One group means one place to look for "how do I read or
write a value", and it matches v2's own `(kind, name)`-addressed,
no-separate-noun-per-slight-variation style already established by
`values get` not needing a `-f PATH`.

## Current Design

### v1: `strata values` (deployment-scoped, all three value kinds together)

All four subcommands take `-f/--file DEPLOYMENT.yaml` and share
`ValueController.resolve_values()` / `resolve_values_via_cache()`.

| Subcommand          | Behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `values list`       | Resolves and displays **every** declared variable/secret/feature. Secrets always masked (first 3 chars + `*****`); variables/features shown in full. `--type variables\|secrets\|features` filters by kind, `--show-store` adds the store reference, `--unresolved` shows only failures, `--trace` shows which environment file a value's declaration came from (merge provenance), `--ai` runs an AI explainer over unresolved entries. Exit 3 if anything failed to resolve. |
| `values get KEY...` | Resolves and reveals the **full, unmasked** value for one or more explicit keys. `--format table\|raw\|env\|export` (raw requires exactly one key; mutually exclusive with `--output`). This is the one v2 already ports.                                                                                                                                                                                                                                                      |
| `values set`        | Writes a value to its configured store backend: `--key/-k`, value from `--value`/`--from-file`/`--stdin` (mutually exclusive, `--from-file`/`--stdin` support multiline for certs/keys). Dispatches by store type: `constant`/`environment` can't be written — prints where to edit instead; `github` shells out to `gh secret set`; integration-backed stores call the integration's `set_variable`/`set_secret`/`set_feature`.                                               |
| `values resolve`    | **Diagnostic only** — walks the resolution chain per key (store type -> integration registered? -> integration available/authenticated?) without revealing values. `--probe` additionally attempts real backend reachability. `--key` scopes to one key. This is `build run`'s real pre-flight check in v1 (per its own docstring).                                                                                                                                            |

Underlying `ValueController` behavior worth carrying over verbatim (already
partially true in v2, confirmed against real source):

- **Precedence on key collision**: secret > feature > variable — v2's
  `resolve_values()` already matches this (`secrets.get(key) or
  features.get(key) or variables.get(key)`).
- **Generate-on-missing** (secrets) / **seed-on-missing** (variables,
  features): if an integration-backed store doesn't have the key yet and a
  `generate`/`default` spec exists, v1 writes it and returns the new value,
  with a race-safe re-read fallback if the write loses a race to another
  process. v2 has the `generate`/`default`/`rotate` **model fields**
  (`store_model.py`) but **no controller logic uses them at all** — a
  missing key is always just a resolution failure today.
- **Age-based rotation** (`_check_rotation`): on every successful secret
  read, if a `rotate:` spec exists and the secret's store-reported age
  exceeds `max_age`, either emit a `rotation_advisory` note (`policy: warn`)
  or transparently regenerate-and-rewrite (`policy: rotate`) — silently, as
  part of ordinary resolution, not just `secret status`/`secret rotate`.
  Requires the integration to expose secret metadata (`created_at`/
  `updated_at`). v2 has no equivalent at all.
- **Store-unavailable is always fatal**, overriding `strict` in both
  directions: a preflight pass (`_preflight_check_stores`) checks every
  store type actually referenced *before* resolving anything, so an
  unreachable/unauthenticated store aborts the whole batch instead of
  surfacing as a per-key error possibly after other keys already mutated
  state (secrets generated, defaults seeded). Already flagged as an open,
  unresolved discrepancy in `/memories/repo/v1-consumer-usage.md` ("Open
  discrepancies... `SecretStoreUnavailableError`") — restated here because
  it's directly relevant to `values list`/`resolve` (whole-environment
  operations, where a partial failure is much more visible than in
  today's `values get`, which typically pulls only a couple of keys).

### v1: `strata secret` (secret-specific lifecycle, partially deployment-scoped)

Two subcommands are **pure, stateless utilities** — no `-f`, no workspace,
no `solution.json`, output the bare value only (unless `--output json`) so
they're pipeable:

| Subcommand        | Behavior                                                                                                                                                                                                                                                |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `secret generate` | `--format urlsafe\|hex\|alphanumeric\|password\|numeric\|base64\|uuid4\|uuid7`, `--length N` (bytes or chars depending on format; ignored for uuid4/uuid7). Same generator (`generate_secret()`) the resolution engine's generate-on-missing path uses. |
| `secret mask`     | `VALUE` positional, `--show N` (default 4, leading chars kept), `--char` (default `*`). Same masking (`mask_secret()`) `values list`/`secret get` use for display.                                                                                      |

The remaining five all take `-f/--file` and read the deployment's
`environment`'s **declared secrets only** (never variables/features — this
group is secrets-specific, unlike `values`):

| Subcommand          | Behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `secret list`       | Lists declared secrets from the YAML with **no store access** — key, store type, value ref, `generate`/`rotate` spec summary. Fast, offline, no auth needed (contrast with `values list`, which resolves everything live).                                                                                                                                                                                                                                          |
| `secret get KEY`    | Resolves one secret via `ValueController._resolve_secret()` directly. Masked by default; `--unmask` reveals it. Narrower than `values get` (secrets-only, single key), but otherwise the same resolution path.                                                                                                                                                                                                                                                      |
| `secret status`     | For every declared secret **with a `rotate:` spec**, queries the integration's `get_secret_metadata()` and reports age vs. `max_age`: `ok` / `overdue` / `no_metadata` / `no_integration` / `no_timestamp`. Exit code 3 if any secret is overdue (`has_validation_errors()` override) — this is the operational health-check surface a scheduled CI job would run, distinct from the *silent* auto-rotation that happens inside ordinary `_resolve_secret()` calls. |
| `secret put KEY`    | Writes a secret: `--value` (explicit) or `--generate` (uses the declared `generate:` spec) — mutually exclusive. Calls `integration.set_secret()` directly (create-if-not-exists), bypassing the read/generate-on-missing/rotation path entirely.                                                                                                                                                                                                                   |
| `secret rotate KEY` | On-demand rotation: requires a `generate:` spec (errors otherwise, pointing at `secret put --value` instead), confirms unless `--force`, generates a new value and calls `integration.update_secret()`.                                                                                                                                                                                                                                                             |

### v2: current state

- `strata values get DEPLOYMENT KEY...` exists (`values_command.py` +
  `value_controller.resolve_values()`), addressed by `(kind, name)` per
  ADR-0015 rather than `-f PATH` (a deliberate, already-settled v2
  convention — see the module's own docstring — not something this doc
  proposes revisiting).
- No `values list`/`set`/`resolve`, no `secret` command group at all.
- `SecretStoreModel`/`VariableStoreModel`/`FeatureStoreModel` already carry
  `generate`/`default`/`rotate` fields end-to-end (ported field-for-field
  from v1, `store_model.py`) — the schema is ready; nothing in
  `value_controller.py` or `StoreIntegration` reads them yet.
- `StoreIntegration` (`integrations/capabilities.py`) is **read-only** —
  one abstract method, `resolve(key) -> str`. There is no `set`/`update`/
  `get_metadata` capability at all, on the ABC or on any of the three real
  resolvers (Infisical, Azure Key Vault, Azure App Configuration). Every
  v1 command in the "not yet built" list above needs at least one of
  these:
  - `set_secret`/`set_variable`/`set_feature` (create-if-not-exists) —
    needed by `values set`, `secret put`.
  - `update_secret` (overwrite existing) — needed by `secret rotate`, and
    by generate-on-missing's race-safe re-read fallback if ported.
  - `get_secret_metadata` (created_at/updated_at) — needed by `secret
    status` and by rotation-on-read if ported.
- No `generate_secret()`/`mask_secret()` utilities exist in v2
  (`src/strata/utils/` has no equivalent of v1's `secret_generator.py`) —
  needed by `secret generate`/`secret mask` and by any generate-on-missing
  or rotation logic.
- No `SecretStoreUnavailableError`-equivalent / preflight concept in v2's
  value_controller — every resolution failure (including a genuinely
  unreachable store) is handled identically, per key, via
  `ValueResolutionError` + a diagnostics entry.

### v2 Design: a single `values` command (no separate `secret` group)

Every v1 `values`/`secret` subcommand maps onto one of eight subcommands
under `strata values`, addressed the same way `values get` already is
(`(kind, name)` deployment name, no `-f PATH`):

| v2 `strata values` subcommand  | Replaces (v1)                 | Notes                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------ | ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `values list DEPLOYMENT`       | `values list` + `secret list` | One subcommand, two modes. Default behavior is v1 `values list`'s live resolve (variables full, secrets masked, features shown) across all three kinds, with `--type variables\|secrets\|features`, `--show-store`, `--unresolved`, `--trace`. A new `--declared-only` flag switches to v1 `secret list`'s offline mode — no store access, just the declarations (key/store/value-ref/generate-or-rotate summary) — and is valid for any type, not just secrets, generalizing v1's secret-only offline listing. `--ai` is **not** ported (see Open Questions). |
| `values get DEPLOYMENT KEY...` | `values get` + `secret get`   | Unchanged from today's v2 implementation — already reveals any key's full value regardless of kind, so it already subsumes `secret get`'s narrower secrets-only + single-key behavior. No `--unmask` needed since `values get`'s whole purpose is revealing values (v1's own docstring: "Secrets are revealed in plain text").                                                                                                                                                                                                                                 |
| `values set DEPLOYMENT`        | `values set` + `secret put`   | `--key/-k`, value from `--value`/`--from-file`/`--stdin` (unchanged, mutually exclusive). Adds `--generate` (mutually exclusive with the three value sources) to fold in `secret put --generate`: uses the key's declared `generate:` spec instead of an explicit value. `--generate` is rejected with a clear error for a non-secret key (variables/features have no `generate:` field at all — enforced today by `validate_generate_not_on_builtin`, so this is just surfacing an existing model constraint as a command error).                             |
| `values resolve DEPLOYMENT`    | `values resolve`              | Unchanged — diagnostic-only resolution-path walk, `--key`, `--probe`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `values rotate DEPLOYMENT KEY` | `secret rotate`               | On-demand rotation, secrets only (rejected for a variable/feature key — no `generate:`/rotation concept for those kinds). Requires a `generate:` spec, confirms unless `--force`, generates + overwrites via the store's write capability (see Phase 4 below).                                                                                                                                                                                                                                                                                                 |
| `values status DEPLOYMENT`     | `secret status`               | Rotation health report for every secret with a `rotate:` spec: age vs. `max_age`, `ok`/`overdue`/`no_metadata`/etc. Exit code 3 if anything is overdue, matching v1.                                                                                                                                                                                                                                                                                                                                                                                           |
| `values generate`              | `secret generate`             | Pure utility, no `DEPLOYMENT` argument, no workspace/solution needed — `--format`, `--length`, same generator vocabulary (urlsafe/hex/alphanumeric/password/numeric/base64/uuid4/uuid7).                                                                                                                                                                                                                                                                                                                                                                       |
| `values mask VALUE`            | `secret mask`                 | Pure utility, no `DEPLOYMENT` argument — `--show`, `--char`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |

This keeps the resolve-only/no-`DEPLOYMENT` utilities (`generate`, `mask`)
as first-class `values` subcommands rather than inventing a separate
"utility" group — they still read naturally as "`strata values generate`
produces a value you'd store with `strata values set --generate`",
keeping the whole value lifecycle (generate -> set -> get -> list ->
rotate -> status) under one noun.

### Examples

Assume a deployment named `web-prod` whose reachable environment(s)
declare a variable `API_URL` (azure-appconfig), a feature `ENABLE_BETA`
(azure-appconfig), and a secret `DB_PASSWORD` (azure-keyvault, with a
`generate:`/`rotate:` spec).

```console
# Pure utilities — no DEPLOYMENT, no solution/workspace needed.
$ strata values generate --format password --length 24
Tr7$kL9wQz!2xVb0mN4pR8sD

$ strata values mask "Tr7$kL9wQz!2xVb0mN4pR8sD"
Tr7$********************

# List — live resolve (default): variables/features in full, secrets masked.
$ strata values list web-prod
  API_URL       (variable)  https://api.example.com
  ENABLE_BETA   (feature)   true
  DB_PASSWORD   (secret)    Tr7$*****

$ strata values list web-prod --type secrets --show-store
  DB_PASSWORD   (azure-keyvault)  Tr7$*****

# List — declared-only (offline, no store access, no auth needed).
$ strata values list web-prod --declared-only --type secrets
  DB_PASSWORD   store=azure-keyvault  generate=password/24  rotate=90d/warn

# Get — reveals full values (any kind, one or more keys).
$ strata values get web-prod DB_PASSWORD API_URL
  DB_PASSWORD  Tr7$kL9wQz!2xVb0mN4pR8sD
  API_URL      https://api.example.com

$ strata values get web-prod DB_PASSWORD --format export
export DB_PASSWORD='Tr7$kL9wQz!2xVb0mN4pR8sD'

# Resolve — diagnostic only, never reveals a value.
$ strata values resolve web-prod --key DB_PASSWORD --probe
  DB_PASSWORD  secret  azure-keyvault  integration: registered  reachable: yes  ok

# Set — explicit value, or --generate using the key's own generate: spec.
# (constant/environment/github/store:artifact keys have no integration to
# write to at all — rejected, naming where to edit them directly instead.)
$ strata values set web-prod --key API_URL --value https://api2.example.com
  ✅  Variable 'API_URL' written to azure-appconfig.

$ strata values set web-prod --key DB_PASSWORD --generate
  ✅  Secret 'DB_PASSWORD' generated (password/24) and written to azure-keyvault.

$ cat tls.pem | strata values set web-prod --key TLS_CERT --stdin

# Rotate — on-demand, secrets only, requires a generate: spec.
$ strata values rotate web-prod DB_PASSWORD --force
  ✅  Secret 'DB_PASSWORD' rotated in azure-keyvault.

# Status — rotation health for every secret with a rotate: spec.
$ strata values status web-prod
  DB_PASSWORD  [warn]  ✓  ok (12d/90d, 78d left)

$ strata values status web-prod --output json
{"secrets": [{"key": "DB_PASSWORD", "store": "azure-keyvault", "max_age": 90,
  "policy": "warn", "status": "ok", "age_days": 12, "days_remaining": 78}],
 "overdue": 0}
```

## Design Decisions

Resolved 2026-09-30 (supersedes the "Open Questions" framing this doc
previously had for these four points):

- **D1 — One command group.** `strata values` only; no `secret` group.
  Settled above ("v2 Design: a single `values` command").
- **D2 — No implicit store mutation on a read.** v1's generate-on-missing
  and rotation-on-read (silent side effects inside ordinary secret
  resolution) are **not ported**. `values get`/`list`/`resolve` are pure
  reads, always — a missing key with a `generate:`/`default:` spec is
  reported as an ordinary resolution failure (with a diagnostic note
  pointing at `values set --generate`), never auto-created; a secret past
  its `rotate: max_age` is reported by `values status` only, never
  auto-rotated by a `get`/`list` call. This removes the race-safe re-read
  fallback v1 needed (nothing writes during a read, so there is no race to
  guard against) and means a script piping `values get` output can never
  trigger a store mutation as a side effect of reading — the actual
  rotate/create operations (`values rotate`, `values set --generate`) are
  the only place a write happens, always explicit, always operator- or
  automation-initiated on purpose.
- **D3 — Write capability lives directly on `StoreIntegration`, base-
  implemented not abstract.** Two new methods, both defaulting to raising
  `IntegrationError(f"{self.name} does not support writes")`:
  `set(key, value) -> None` (create-or-overwrite — collapsed from v1's
  separate `set_*`/`update_*` pair now that D2 removed the only caller
  that cared about create-vs-overwrite semantics) and
  `metadata(key) -> SecretMetadata | None` (returns `None` when the
  backend can't report timestamps). Matches `InfraIntegration`'s own
  "base-implemented, not abstract" precedent (ADR-0023 D5) rather than a
  second capability class (e.g. `MutableStoreIntegration`) — every real
  store backend (Infisical, Azure Key Vault, Azure App Config) can
  plausibly support writes, so gating it behind a whole extra class this
  doc's own resolvers would all have to adopt anyway is unnecessary
  ceremony; a resolver that generally cannot write (rare) just inherits
  the default and every write-side `values` subcommand reports a clean
  "does not support writes" error for it.
- **D4 — No whole-batch preflight.** v1's `SecretStoreUnavailableError`
  fail-fast (abort the entire batch before resolving anything if any
  referenced store is unreachable) is **not ported**. `values list`/
  `resolve` reuse the same per-key `Diagnostics` accumulation `values get`
  already uses today — an unreachable store surfaces as one `Diagnostic`
  per affected key, not a single batch-level error. More verbose when
  every key backed by the same down store fails identically, but keeps
  one uniform failure model across all `values` subcommands instead of a
  second, batch-abort-only mode that only some of them would exhibit —
  and it's the same model `--output json` already renders consistently
  (`Diagnostics.to_dict()`-shaped items) everywhere else in v2. Closes the
  discrepancy tracked in `/memories/repo/v1-consumer-usage.md`.
- **`--ai` (v1 `values list`'s unresolved-value AI explainer) is dropped**,
  not deferred — no `ai_agent` integration concept exists anywhere in v2
  yet; revisit only alongside a general such concept, not as a
  `values`-specific flag.

## Technical Design

### New types

```python
# strata/integrations/resolved_context.py (alongside ValueResolution/ValueReference)
@dataclass(frozen=True)
class SecretMetadata:
    """What a store can tell us about a secret without revealing its value."""
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

```python
# strata/integrations/capabilities.py — StoreIntegration, two new methods (D3)
class StoreIntegration(Integration):
    @abstractmethod
    def resolve(self, key: str) -> str: ...          # unchanged

    def set(self, key: str, value: str) -> None:
        """Create or overwrite `key`. Base default: unsupported."""
        raise IntegrationError(f"{self.name} does not support writes")

    def metadata(self, key: str) -> SecretMetadata | None:
        """Best-effort created/updated timestamps for `key`, or None
        when the backend can't report them. Base default: unsupported."""
        raise IntegrationError(f"{self.name} does not report secret metadata")
```

`infisical_resolver.py`/`azure_keyvault_resolver.py` (the two secret-
capable resolvers) are the two expected to override both; `set()` maps
onto each SDK's own upsert call (Infisical's update-or-create secret
endpoint; Key Vault's `set_secret`, which is already create-or-overwrite).
`azure_appconfig_resolver.py` (variables/features) only needs `set()`.

### New utility module — `strata/utils/secret_generator.py`

Ported from v1's `secret_generator.py` (confirmed same vocabulary already
declared in `SecretGenerateType`, `store_model.py`):

```python
def generate_secret(fmt: str, length: int = 32) -> str: ...
def mask_secret(value: str, show: int = 4, char: str = "*") -> str: ...
```

Pure functions, no I/O — used by `values generate`/`values mask` directly,
and by `values set --generate`/`values rotate` (reads a key's
`SecretGenerateSpec`, calls `generate_secret(spec.type.value, spec.length)`).

### New `value_controller.py` functions

| Function                                                                                                                                              | Used by          | Behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_values(context, deployment_name, *, type_filter=None, show_store=False, unresolved_only=False, declared_only=False)` — **BUILT (Phases 2 + 4)** | `values list`    | Returns `(list[DeclaredValueRow] \| list[ValueListRow], Diagnostics)`. `declared_only=True`: `DeclaredValueRow`s, skips all store I/O — reads `merge_environment_models(reachable_environments(...))`, no `resolve()` call at all. `declared_only=False` (default): `ValueListRow`s, live-resolves every declared key via the same `_resolve_store_value()`/`store: artifact` dispatch `resolve_values()` uses, masking secret values (`mask_secret()`) and leaving variables/features in full; `unresolved_only` filters the returned rows to failures. `show_store` is accepted but unused by this function — purely a console-rendering concern (`values_command.py`'s `_print_value_list()`). No `--trace`/merge-provenance in either mode — v2 has no provenance tracking built at all yet (confirmed: no `merge_order`/`get_merge_provenance()`-equivalent anywhere in the codebase), a separate effort. |
| `resolve_diagnostic(context, deployment_name, *, key=None, probe=False)`                                                                              | `values resolve` | For each in-scope key: store type, whether a resolver class is registered for it (`strata.integrations.registry`), and — only with `--probe` — an actual `resolve()` call (value discarded, only success/failure kept). Never returns a value, only a diagnostic per key.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `set_value(context, deployment_name, key, *, value=None, from_file=None, from_stdin=None, generate=False)`                                            | `values set`     | Finds `key` in the merged environment; for `constant`/`environment`/`github`/`artifact` stores, raises `UsageError` naming where to edit instead (no write possible — matches v1's `values set` messaging for these, since D2/D3 don't change what a built-in store even means); otherwise resolves the write value (explicit / file / stdin / `generate_secret()` per the key's own `generate:` spec) and calls `resolvers.get(store_type).set(str(store.value), resolved_value)`.                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `rotate_secret(context, deployment_name, key, *, force=False)`                                                                                        | `values rotate`  | Secrets only (`UsageError` for a variable/feature key). Requires a `generate:` spec (`UsageError` otherwise, pointing at `values set --value`). Confirms via `click.confirm` at the command layer, not here (controller stays free of interactive I/O, matching every other controller in this codebase). Generates + calls `.set()`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `secret_status(context, deployment_name)`                                                                                                             | `values status`  | Every secret with a `rotate:` spec: calls `.metadata()`, compares age to `max_age`, classifies `ok`/`overdue`/`no_metadata`/`no_integration`/`no_timestamp`. Never mutates anything (D2) — this is the read-only health surface; `values rotate` is the only thing that actually rotates.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

All five share `_Resolvers` (already built) for lazy, per-store-type
integration construction — no new resolver-caching mechanism needed.

### CLI contract

All six deployment-scoped subcommands take the same `DEPLOYMENT`
positional + `--path` + `--output`/`--quiet`/`--verbose` triad `values get`
already uses (no `-f PATH`, ADR-0015). `generate`/`mask` take neither
`DEPLOYMENT` nor `--path` — they never call `open_solution()` at all.

```
strata values list DEPLOYMENT [--path PATH] [--type variables|secrets|features]
                    [--declared-only] [--show-store] [--unresolved]
                    [--output console|json] [--quiet] [--verbose]
  └─ values_command.py: values_list()   — BUILT (Phases 2 + 4)
       ├─ context = open_solution(resolve_work_path(path)).require_valid()
       ├─ rows, diagnostics = list_values(context, deployment, type_filter=type,
       │                                   show_store=show_store, unresolved_only=unresolved_only,
       │                                   declared_only=declared_only)
       │    # declared_only=True: DeclaredValueRow, no store I/O (Phase 2)
       │    # declared_only=False (default): ValueListRow, live-resolves every key,
       │    #   masking secrets (mask_secret()) — variables/features shown in full (Phase 4)
       │    # --trace (merge provenance) is NOT built in either mode — v2 has no
       │    # merge-provenance tracking at all yet; a separate effort, out of scope here
       ├─ if output == "console":
       │      _print_declared_list(rows) if declared_only else _print_value_list(rows, show_store=show_store)
       │      # declared console rendering omits value_ref (matches v1's secret list);
       │      # live console rendering honors --show-store for the store column;
       │      # --output json always carries every field in both modes regardless
       └─ run.report(diagnostics, root=context.root); run.ok = diagnostics.ok
  Exit: 0 all resolved (or --declared-only, which never fails on a value) |
        2 bad args / no such deployment | 3 live mode: one or more keys failed to resolve

strata values get DEPLOYMENT KEY... [--path PATH] [--format table|raw|env|export]
                   [--output console|json] [--quiet] [--verbose]
  (unchanged — already built)

strata values resolve DEPLOYMENT [--path PATH] [--key KEY] [--probe]
                       [--output console|json] [--quiet] [--verbose]
  └─ values_command.py: values_resolve()
       ├─ context = open_solution(...).require_valid()
       ├─ results, diagnostics = resolve_diagnostic(context, deployment, key=key, probe=probe)
       └─ run.report(diagnostics, root=context.root); run.ok = diagnostics.ok
  Exit: 0 every checked key would resolve | 2 bad args | 3 one or more would fail

strata values set DEPLOYMENT --key KEY [--value V | --from-file PATH | --stdin | --generate]
                   [--path PATH] [--output console|json] [--quiet] [--verbose]
  └─ values_command.py: values_set()   — BUILT (Phase 6)
       ├─ context = open_solution(...).require_valid()
       └─ result, diagnostics = set_value(context, deployment, key, value=value, from_file=from_file,
                                          from_stdin=from_stdin, generate=generate)
            # raises UsageError for bad args/unknown key/built-in store/bad --generate usage;
            # a real write failure is reported as a Diagnostic, NOT raised (IntegrationError
            # is not a StrataError — command_run()'s handler wouldn't catch it) — run.ok = diagnostics.ok
  Exit: 0 written | 2 bad args (mutually-exclusive value sources, unknown key, built-in
        store, --generate on a non-secret or with no generate: spec), unknown deployment | 3 the store write itself failed

strata values rotate DEPLOYMENT KEY [--force] [--path PATH]
                      [--output console|json] [--quiet] [--verbose]
  └─ values_command.py: values_rotate()   — BUILT (Phase 7)
       ├─ context = open_solution(...).require_valid()
       ├─ if not force: click.confirm(f"Rotate secret '{key}'? ...", abort=True, err=True)
       │    # err=True keeps the prompt off stdout, which --output json must keep pure
       └─ result, diagnostics = rotate_secret(context, deployment, key, force=force)
            # write failure -> Diagnostic, not raised (same IntegrationError-is-not-
            # a-StrataError reasoning as values set) -- run.ok = diagnostics.ok
  Exit: 0 rotated | 2 bad args / KEY not a secret / no generate: spec / unknown deployment |
        3 the store write itself failed

strata values status DEPLOYMENT [--path PATH] [--output console|json] [--quiet] [--verbose]
  └─ values_command.py: values_status()   — BUILT (Phase 8)
       ├─ context = open_solution(...).require_valid()
       ├─ rows, diagnostics = secret_status(context, deployment)
       ├─ if output == "console": _print_status_rows(rows)
       ├─ run.report(diagnostics, root=context.root)   # one ERROR per overdue secret, one WARNING per unmeasurable one
       └─ run.ok = not any(row.status == "overdue" for row in rows)   # derived from rows, not diagnostics.ok
  Exit: 0 nothing overdue (including when no secret declares a rotate: spec) |
        2 bad args / unknown deployment | 3 one or more secrets overdue

strata values generate [--format urlsafe|hex|alphanumeric|password|numeric|base64|uuid4|uuid7]
                        [--length N] [--output console|json]
  └─ values_command.py: values_generate()   — no open_solution() at all
       └─ click.echo(generate_secret(fmt, length))   # bare value only unless --output json
  Exit: 0 always (pure function; bad --format/--length is a Click usage error, exit 2)

strata values mask VALUE [--show N] [--char C] [--output console|json]
  └─ values_command.py: values_mask()   — no open_solution() at all
       └─ click.echo(mask_secret(value, show=show, char=char))
  Exit: 0 always
```

### File-layout impact

- `strata/utils/secret_generator.py` — new (ported from v1).
- `strata/integrations/resolved_context.py` — add `SecretMetadata`.
- `strata/integrations/capabilities.py` — add `StoreIntegration.set()`/`.metadata()`.
- `strata/integrations/infisical_resolver.py`, `azure_keyvault_resolver.py`,
  `azure_appconfig_resolver.py` — override `set()` (all three); override
  `metadata()` on the two secret-capable ones only.
- `strata/controllers/value_controller.py` — add `list_values()`,
  `resolve_diagnostic()`, `set_value()`, `rotate_secret()`,
  `secret_status()`, alongside the existing `resolve_values()`.
- `strata/commands/values_command.py` — add the six new subcommands to the
  existing `values_command` group (no new command file — matches
  `deploy_command.py`/`build_command.py`'s one-file-per-group convention
  regardless of subcommand count).

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) — value token design this resolution layer ultimately serves.
- [ADR-0021](../decisions/0021-integration-layer.md) — integration layer (`StoreIntegration`/`InfraIntegration` capability split) this proposal extends.

## Implementation Plan

Build order — smallest, least-risky first. Each phase is independently
shippable and testable; nothing later blocks anything earlier.

1. [x] **`values generate` / `values mask`** — new
   `strata/utils/secret_generator.py` (ported), two new subcommands, no
   `open_solution()` call, no store, no deployment argument. Zero design
   risk — proves nothing beyond porting two pure functions.
2. [x] **`values list --declared-only`** — `list_values(..., declared_only=True)`
   in `value_controller.py`, offline, no store access, no integration
   changes. Also covers `--type`/`--show-store` filtering for the declared
   path.
3. [x] **`values resolve`** — `resolve_diagnostic()`, read-only against the
   *existing* `resolve()`-only `StoreIntegration` contract — no
   integration-layer changes needed. Doubles as `build run`'s missing
   pre-flight check once wired up there (out of scope for this doc).
4. [x] **`values list` (live)** — `list_values(..., declared_only=False)`,
   reusing `resolve()` per key exactly like `values get` does today, with
   per-kind masking (`mask_secret()` for secrets).
5. [x] **`StoreIntegration.set()`/`.metadata()` + `SecretMetadata`** (D3)
   — the write-capability groundwork, implemented on
   `infisical_resolver.py`/`azure_keyvault_resolver.py`/
   `azure_appconfig_resolver.py`. No CLI-visible change yet; testable in
   isolation against each resolver's existing test doubles.
6. [x] **`values set`** (including `--generate`) — `set_value()`, built on
   phase 5's `.set()`.
7. [x] **`values rotate`** — `rotate_secret()`, built on phase 5's `.set()`.
8. [x] **`values status`** — `secret_status()`, built on phase 5's
   `.metadata()`.

## Changelog

- 2026-09-30: Initial design analysis — cataloged v1's real `values`/
  `secret` command behavior against v2's current state (`values get` only),
  identified the `StoreIntegration` write-capability gap as the actual
  blocker for everything beyond read-only listing/diagnostics, proposed a
  5-phase build order.
- 2026-09-30: Decided against porting a separate `secret` command group —
  folded every `secret` subcommand into `strata values` instead (mapping
  table in "v2 Design: a single `values` command"), and updated the
  phased build order's subcommand names to match.
- 2026-09-30: Added the full technical design — resolved all four
  previously-open questions as explicit decisions (D1-D4: single group,
  no implicit store mutation on read, write capability added directly to
  `StoreIntegration` base-implemented-not-abstract, no whole-batch
  preflight), added the `SecretMetadata` type, the CLI contract and
  call-chain pseudocode for all eight subcommands, the file-layout impact
  list, and an 8-phase Implementation Plan with checkboxes replacing the
  prior "Remaining Work / Open Questions" section.
- 2026-09-30: Implemented Phase 1 — `strata/utils/secret_generator.py`
  (ported `generate_secret()`/`mask_secret()` verbatim from v1, plus a
  `FORMATS` vocabulary constant), and `values generate`/`values mask`
  subcommands in `values_command.py` (plain Click commands, no
  `command_run()`/`open_solution()` — bare value on stdout by default,
  a minimal JSON object with `--output json`). Verified with mypy, ruff,
  `lint-imports`, and new tests (`test_utils_secret_generator.py`, plus
  additions to `test_commands_values.py`) — all green in isolation.
- 2026-09-30: Implemented Phase 2 — `DeclaredValueRow` dataclass and
  `list_values()` in `value_controller.py` (declared-only path only;
  raises `UsageError` when `declared_only=False`, since live resolution
  is Phase 4), plus `values list --declared-only` in `values_command.py`.
  Two scope refinements found during implementation, not in the original
  Technical Design: (1) `--show-store` is **not** wired yet — the store
  type is always shown for a declared row (it's the only substantive
  column besides the key in this mode, unlike Phase 4's live mode where a
  resolved/masked value is the main payload and the store is genuinely
  optional context) — deferred to Phase 4 alongside `--unresolved`/
  `--trace`, which are equally meaningless without live resolution/
  provenance; (2) console rendering omits `value_ref` (matches v1's own
  `secret list` console output exactly), while `--output json` always
  carries it. Verified with mypy, ruff, `lint-imports`, and new tests at
  both the controller level (`test_value_controller.py`) and the CLI level
  (`test_commands_values.py`) — all green in isolation (1494 passed; the
  only other failure in the full suite is the pre-existing, unrelated
  `test_solution_controller.py::test_shipped_example_solution_loads_cleanly`
  from a concurrent, in-progress audit feature in another session — see
  `/memories/repo/v1-consumer-usage.md`).
- 2026-09-30: Implemented Phase 3 — `ResolveRow` dataclass and
  `resolve_diagnostic()` in `value_controller.py` (checks store type,
  integration registration via `_Resolvers.get()`, and — only with
  `--probe` — an actual `resolve()` call, value discarded), plus
  `values resolve` in `values_command.py` (`--key`, `--probe`). Matches
  the Technical Design's CLI contract and function table exactly, no
  scope refinements needed this phase. Verified with mypy, ruff,
  `lint-imports`, and new tests at both the controller level (15 new
  cases in `test_value_controller.py`, including constant/environment/
  artifact/unregistered-store/probe-success/probe-failure/`--key`/
  sorting/multiple-enabled-integration-documents) and the CLI level (10
  new cases in `test_commands_values.py`) — all green in isolation
  (1527 passed; same single pre-existing unrelated audit-feature failure
  as Phase 2).
- 2026-09-30: Implemented Phase 5 — `SecretMetadata` in
  `resolved_context.py`, and `StoreIntegration.set()`/`.metadata()` in
  `capabilities.py` (base-implemented, not abstract, per D3 — default
  raises `IntegrationError`). Overridden on all three real resolvers:
  `InfisicalResolver.set()` (upsert via `PATCH`, falling back to `POST`
  on a 404 — Infisical's real API has distinct create/update endpoints,
  presented as one per D3; also invalidates the bulk-fetch `resolve()`
  cache) and `.metadata()` (a dedicated single-secret `GET`, parsing
  `createdAt`/`updatedAt`, returning `None` rather than raising on any
  failure — not found, unreachable, or a malformed response);
  `AzureKeyVaultResolver.set()` (`SecretClient.set_secret()`, already an
  upsert) and `.metadata()` (reuses `get_secret()`'s own
  `SecretProperties.created_on`/`.updated_on`, `None` only when the
  secret isn't found — a config/auth failure still raises, unlike
  Infisical's `metadata()`); `AzureAppConfigResolver.set()`
  (`set_configuration_setting()`, already an upsert — no `.metadata()`
  override, per the design's own "only the two secret-capable resolvers"
  scope). No CLI-visible change yet, exactly as planned — `values set`/
  `values rotate`/`values status` (Phases 6-8) are what will actually
  call these. Verified with mypy, ruff, `lint-imports`, and new tests
  across `test_integrations_capabilities.py` (base-default behavior),
  `test_infisical_resolver.py`, `test_azure_keyvault_resolver.py`, and
  `test_azure_appconfig_resolver.py` — all green in isolation (1549
  passed; same single pre-existing unrelated audit-feature failure as
  every prior phase).
- 2026-09-30: Implemented Phase 4 — new `ValueListRow` dataclass and a
  `_list_values_live()` helper in `value_controller.py`, wired into
  `list_values()` as the `declared_only=False` default path (removing the
  `UsageError` guard). Reuses `resolve_values()`'s exact per-key dispatch
  (`_resolve_store_value()`, `store: artifact`'s direct path), masking
  secrets with `mask_secret()` and leaving variables/features in full;
  one `Diagnostic` per failed key (D4 — same uniform model as `values
  get`/`resolve`). `values list` in `values_command.py` gained
  `--show-store` (console-only — every row always carries `store`
  regardless) and `--unresolved` (filters returned rows to failures);
  `--trace` deliberately still not built (confirmed no merge-provenance
  tracking exists anywhere in v2 yet — a separate effort, not just CLI
  wiring). Verified with mypy, ruff, `lint-imports`, and new tests at
  both the controller level (8 new cases in `test_value_controller.py`)
  and the CLI level (2 old tests updated + 10 new cases in
  `test_commands_values.py`) — all green in isolation (1564 passed; same
  single pre-existing unrelated audit-feature failure as every prior
  phase).
- 2026-09-30: Implemented Phase 6 — `SetResult` dataclass and `set_value()`
  in `value_controller.py`, plus `values set` (`--key`/`--value`/
  `--from-file`/`--stdin`/`--generate`) in `values_command.py`, built on
  Phase 5's `StoreIntegration.set()`. Precedence for finding `key` matches
  every other `values` subcommand (secret > feature > variable); rejects
  `constant`/`environment`/`github`/`store: artifact` keys (no integration
  to write to) naming where to edit them directly instead; requires
  exactly one value source. **One real design flaw found and fixed during
  implementation, not just a scope refinement:** the original Technical
  Design's CLI contract said `set_value()` "raises UsageError/
  IntegrationError on failure" — but `IntegrationError` is *not* a
  `StrataError`, and `command_run()`'s exception handler only catches
  `StrataError`, so a real write failure would have crashed uncaught
  instead of producing the documented exit code 3. Fixed by having
  `set_value()` catch `IntegrationError` itself and return `(None,
  Diagnostics)` — matching `values get`/`list`/`resolve`'s existing
  uniform "the operation itself failed" reporting (D4) instead of
  introducing a second, inconsistent failure mode. `SetResult` never
  carries the written value, only what/where, so `values set`'s console
  confirmation can never accidentally echo a secret. Also fixed the
  Examples section, which had `API_URL` (a `constant`) as a `values set`
  target — no longer possible now that builtin stores are correctly
  rejected; changed the example's `API_URL` to `azure-appconfig`-backed.
  Verified with mypy, ruff, `lint-imports`, and new tests at both the
  controller level (23 new cases in `test_value_controller.py`, including
  a dedicated "write failure is reported not raised" regression test for
  the flaw above) and the CLI level (11 new cases in
  `test_commands_values.py`) — all green in isolation (1594 passed; same
  single pre-existing unrelated audit-feature failure as every prior
  phase).
- 2026-09-30: Implemented Phase 7 — `rotate_secret()` in
  `value_controller.py`, reusing Phase 6's `SetResult` unchanged
  (`kind="secret"`, `generated=True` — no new type needed), plus
  `values rotate` (`KEY`, `--force`) in `values_command.py`, built on
  Phase 5's `StoreIntegration.set()`. Confirmed a genuine simplification
  over `set_value()`: rotation needs no separate builtin-store rejection
  check at all — `SecretStoreModel.validate_generate_not_on_builtin()`
  already guarantees a `constant`/`environment`/`github` secret can never
  carry a `generate:` spec, so the single "has no generate: spec" check
  rejects every built-in store too, for free. Same D4 write-failure
  handling as `set_value()`/Phase 6 (`IntegrationError` caught internally,
  reported as a `Diagnostic`, never raised). `values_rotate()`'s
  `click.confirm(..., err=True)` keeps the prompt off stdout so
  `--output json` stays pure, matching the order the CLI contract already
  specified (`open_solution()` → confirm → `rotate_secret()`). Verified
  with mypy, ruff, `lint-imports`, and new tests at both the controller
  level (11 new cases in `test_value_controller.py`, including a
  two-rotations-differ sanity check and reuse of Phase 6's fake
  writable/failing store test doubles) and the CLI level (10 new cases in
  `test_commands_values.py`, including both confirm-accepted and
  confirm-declined paths) — all green in isolation (1617 passed; same
  single pre-existing unrelated audit-feature failure as every prior
  phase, plus two newly-appeared failures in `test_semantic_checks.py`/
  `test_models_tenant.py` confirmed via `git diff --stat` to belong to a
  different, concurrent session's in-progress tenant/geography work —
  neither file is touched by this phase's diff).- 2026-09-30: Implemented Phase 8 — `SecretStatusRow` dataclass and
  `secret_status()` in `value_controller.py`, built on Phase 5's
  `StoreIntegration.metadata()`, plus `values status` in
  `values_command.py`. **Scope refinement vs. the original sketch:** the
  design's own CLI-contract pseudocode showed `secret_status()` returning
  bare `rows` with no `Diagnostics` at all — implemented instead as
  `(rows, Diagnostics)`, matching every other function in this table, so
  `values status` gets the same "X errors, Y warnings" console/JSON
  summary the rest of the `values` group already has: one `ERROR` per
  `overdue` secret, one `WARNING` for anything that couldn't be measured
  (`no_metadata`/`no_integration`/`no_timestamp`) — `ok` rows get no
  diagnostic. The exit code still comes from the rows themselves
  (`any(row.status == "overdue" ...)`), not `diagnostics.ok` alone,
  exactly as the original contract specified — `Diagnostics` here is a
  presentation convenience layered on top, not the success/failure
  signal. Confirms every classification path reuses the base
  `StoreIntegration.metadata()` contract correctly: `None` and a raised
  `IntegrationError` both fold into `"no_metadata"` (a `WARNING`, not a
  hard failure) — secrets/status is deliberately forgiving of a resolver
  that can't report timestamps, since that's expected for most store
  types today (only Infisical/Azure Key Vault implement `.metadata()` at
  all — Phase 5). Verified with mypy, ruff, `lint-imports`, and new tests
  at both the controller level (18 new cases in
  `test_value_controller.py`, covering every `status` classification,
  `updated_at`-over-`created_at` precedence, sort order, and a
  never-calls-`.set()` confirmation) and the CLI level (9 new cases in
  `test_commands_values.py`) — all green in isolation (1639 passed; same
  single pre-existing unrelated audit-feature failure; the two
  tenant/geography failures from Phase 7's run have since resolved
  themselves, confirming they belonged to the other concurrent session's
  work as suspected, not this one).

**All 8 phases of the Implementation Plan are now complete.** Every
`strata values` subcommand documented in this design (`list`, `get`,
`resolve`, `set`, `rotate`, `status`, `generate`, `mask`) is built and
tested, along with the `StoreIntegration.set()`/`.metadata()` write
capability and `SecretMetadata` type they're built on.