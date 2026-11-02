# Store Integration Configuration — Binding `kind: integration` Documents to Infisical/Azure Key Vault/Azure App Config

- Status: current — fully implemented (2026-09-29). A `variable`/`secret`/
  `feature` store (`store: infisical`/`azure-keyvault`/`azure-appconfig`)
  auto-binds, by type, to a real `kind: integration` document the same way
  `InfraIntegration` already does for Terraform/Helm/Compose; all three
  resolvers read that document's `spec.endpoints`/`.configuration`,
  falling back to their original env vars unchanged whenever no document
  is bound or a specific field is unset.
- Last updated: 2026-10-06 (graduated from `docs/work/`)
- Related: [integration-layer.md](../work/integration-layer.md) (the
  subsystem this extends — `Integration`/`StoreIntegration` base classes,
  the registry, the three resolver classes), [ADR-0021](../decisions/0021-integration-layer.md)
  D3 (the type-vs-name-keying decision this design must not contradict),
  [value-token-resolution.md](value-token-resolution.md) (the resolved
  `values` flat map this design's "Deliberately out of scope" section
  explains it cannot safely reach into)

## Overview

Before this design, `kind: integration` was a fully-built, standalone
document kind (`integration_model.py`, ADR-0021), and
`InfisicalResolver`/`AzureKeyVaultResolver`/`AzureAppConfigResolver`
(`strata/integrations/*_resolver.py`) were fully real, working
`StoreIntegration` classes — but no `Integration` document ever reached
one: `value_controller.py`'s store dispatch called `get_integration(
integration_type)` with no `config` argument, ever, and each resolver read
`os.environ` exclusively even though its constructor already accepted a
`config: IntegrationModel | None`. This is a narrower, more concrete gap
than ADR-0020's original "port `ConfigurationSpecModel.integrations`"
framing suggested — that framing predates ADR-0021's decision to make
`Integration` its own standalone kind rather than an embedded list on
`Configuration` at all. The gap was entirely inside `value_controller.py`
and the three resolver classes; nothing about `ConfigurationSpecModel`
needed to change.

## Why type-based auto-bind, not a named `integration:` field on a store

[ADR-0021's D3](../decisions/0021-integration-layer.md) already decided
this, deliberately, and this design does not contradict it:
`VariableStoreModel`/`SecretStoreModel`/`FeatureStoreModel` have nothing to
name a specific `Integration` document by — `store: infisical` is a
**type**, not a reference to one declaration. D3 confirms this matches
v1's own real behaviour (`ValueController._get_integration_by_type` also
resolves by type, looping every registered integration and returning the
first type match) and explicitly frames v2's "one cached instance per
type" as *"a more deterministic version of that same behaviour, not a
weaker one."*

This design makes that already-decided type-based lookup actually **find**
a real `Integration` document — it does not invent named binding for
stores, which would reopen a question ADR-0021 already closed. A named
`integration:` field would need real evidence first (a solution genuinely
needing two differently-configured instances of the same store type) —
none has appeared.

## Current Design

### 1. One shared auto-bind helper, reused from the `InfraIntegration` path

`integration_resolution.py`'s `bind_integration_config(index,
integration_type, *, requester)` (promoted from a private
`_auto_bind_config()`, now public) is the single rule both capability
tiers use: zero enabled matches falls back to `config=None` (env-var-only
behaviour, unchanged); exactly one enabled match uses it; more than one is
a `UsageError` naming every candidate — never an arbitrary pick, stricter
than v1's own real behaviour (an accepted direction per D3, not a
regression). `resolve_integration()`/`resolve_module_integration()` (the
pre-existing `InfraIntegration` callers) and `value_controller.py` (the
new store-resolution caller) both call the identical function — one rule,
not two independently-drifting implementations.

### 2. `DocumentIndex` threaded into `_Resolvers`

`value_controller.py`'s `_Resolvers(index: DocumentIndex)` — constructed
once per `resolve_values()` call with `context.controller.index`, already
in scope there. `.get(integration_type)`:

```python
config = bind_integration_config(self._index, integration_type, requester=f"Store type '{integration_type}'")
instance = get_integration(integration_type, config=config)
```

Cache key stays `integration_type` (unchanged — D3's own reasoning for
keying by type, not declaration name, still applies here).

### 3. Each resolver prefers `config.spec.*`, falls back to its existing env var

Every resolver keeps its existing env-var fallback **unchanged** — a
solution with no `Integration` document declared for a type keeps working
exactly as before (purely additive, never breaking). Field mapping, per
field, not per resolver (a document missing one field falls back to that
field's own env var, independent of the others):

| Resolver                 | Env var (fallback, unchanged) | `config.spec.*` field (preferred when set) |
| ------------------------ | ----------------------------- | ------------------------------------------ |
| `InfisicalResolver`      | `INFISICAL_ADDR`              | `endpoints.address`                        |
| `InfisicalResolver`      | `INFISICAL_PROJECT_ID`        | `configuration["project_id"]`              |
| `InfisicalResolver`      | `INFISICAL_ENVIRONMENT`       | `configuration["environment"]`             |
| `AzureKeyVaultResolver`  | `AZURE_KEYVAULT_URL`          | `endpoints.address`                        |
| `AzureAppConfigResolver` | `AZURE_APPCONFIG_ENDPOINT`    | `endpoints.address`                        |

`configuration` is `IntegrationSpecModel`'s existing free-form
`dict[str, Any] | None` passthrough field — used here for the two
Infisical-specific settings with no better-fitting typed field
(`endpoints`/`authentication` are both generic across every integration
type; `project_id`/`environment` are Infisical-specific concepts with no
Azure equivalent). Each resolver reads its own two or three keys directly,
guarded by `config is not None` — no dedicated typed sub-model was
introduced for this (see [History](#history), "untyped `configuration`
access").

`INFISICAL_TOKEN`/`INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` are
**not** in this table — see "Deliberately out of scope" below.

### Worked examples — one `kind: integration` document per resolver

All three deliberately omit `authentication` — see "Deliberately out of
scope" below. Each declares only the non-secret connection info the
field-mapping table above wires up; the real credential stays exactly
where it already lives today (a process env var, set outside strata by
whatever secret manager/CI pipeline runs it — Infisical's own
universal-auth client secret, or nothing at all for the two Azure
resolvers, which use ambient managed identity).

**Infisical** — the one integration with real non-secret settings beyond
the endpoint (`project_id`/`environment`):

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: infisical-prod
spec:
  type: infisical
  capabilities: [variables, secrets]
  enabled: true
  endpoints:
    address: https://app.infisical.com
  configuration:
    project_id: "3fa1c2e4-9b8d-4e2a-8c1f-2a9d7e6b5c40"
    environment: prod
```

Still required, unchanged, set outside this document: `INFISICAL_TOKEN`
**or** `INFISICAL_CLIENT_ID` + `INFISICAL_CLIENT_SECRET` as real process
env vars.

**Azure Key Vault** — `endpoints.address` is the entire useful surface
here:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: azure-keyvault-prod
spec:
  type: azure-keyvault
  capabilities: [secrets]
  enabled: true
  endpoints:
    address: https://my-vault.vault.azure.net
```

Auth stays ambient: `DefaultAzureCredential`'s own chain (managed identity
first) — nothing to declare for the common case. A user-assigned identity
still only needs `AZURE_CLIENT_ID` as a plain env var, with or without this
document.

**Azure App Configuration** — identical shape, different
`type`/`capabilities`:

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: integration
meta:
  name: azure-appconfig-prod
spec:
  type: azure-appconfig
  capabilities: [variables, features]
  enabled: true
  endpoints:
    address: https://my-config.azconfig.io
```

Same ambient-auth story as Key Vault above.

## Deliberately out of scope

- **`authentication` is not wired — and would not actually help any of
  these three integrations even if it were.** Every field on
  `OAuth2AuthenticationModel`/`AWSAuthenticationModel`/etc. is a *key
  reference* resolved from environment declarations, not the credential
  itself — resolving it needs the same `ValueResolution`/flat-`values`-map
  machinery `resolve_values()` itself builds, but a store's own auth is
  needed to build the values that its own credential might be one of, a
  real bootstrapping-order problem. Concretely: Infisical's real auth
  (`INFISICAL_TOKEN`/`INFISICAL_CLIENT_ID`+`INFISICAL_CLIENT_SECRET`) is
  exactly the kind of value that would plausibly be declared as a
  `${secret:...}` itself — circular if that key's own store is also
  Infisical, and no better than today's direct env-var read even scoped to
  `constant`/`environment`-backed keys only. Both Azure resolvers' real
  auth is `DefaultAzureCredential`, whose entire point is *not* needing an
  explicit credential value (managed identity is ambient) — there's
  nothing for `client_secret` to meaningfully hold in the common case.
  Confirmed directly: nothing in `strata.integrations` reads
  `config.spec.authentication` anywhere today — a pre-existing gap across
  the *whole* integration layer, not specific to stores. Revisit only once
  the bootstrapping-order problem is solved for the integration layer as a
  whole (a separate, larger design).
- **A named `integration:` field on `VariableStoreModel`/`SecretStoreModel`/
  `FeatureStoreModel`.** ADR-0021 D3 already decided against this — see
  "Why type-based auto-bind" above. Revisit only with real evidence (a
  solution genuinely needing two differently-configured instances of the
  same store type).
- **Value-token resolution inside `IntegrationModel.spec.configuration`/
  `.endpoints.address`.** `Integration` is not one of the document kinds
  `_documents_reachable_from_workspace()` walks, so a `${var:}`/`${secret:}`
  token written there has no validate-time check and no deploy-time
  resolution mechanism — a real, separate, pre-existing gap. These two
  fields are treated as plain literals here, matching how they're already
  used (or not used) everywhere else.
- **Azure App Config's feature-flag JSON-blob parsing** — already flagged
  by `AzureAppConfigResolver`'s own docstring as unaddressed, unrelated to
  config binding.

## Related Decisions

- [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md) — the
  original, broader "port `ConfigurationSpecModel.integrations`" framing
  this design replaced with a narrower, evidence-grounded gap.
- [ADR-0021](../decisions/0021-integration-layer.md) D3 — type-based,
  not name-based, store→integration binding; this design's core
  constraint.
- [integration-layer.md](../work/integration-layer.md) — the base
  `Integration`/`StoreIntegration` classes, registry, and the three
  resolver classes this design binds real config into.
- [docs/config/integration.md](../config/integration.md) — corrected here
  to state a store auto-binds by type (no name field), distinct from a
  `provisioner`'s by-name `integration:` reference.
- [docs/integrations/](../integrations/README.md) — the user-facing
  "how do I configure this" guide per real integration, created alongside
  this design (`README.md` + `infisical.md`/`azure-keyvault.md`/
  `azure-appconfig.md`).

## History

- **The real gap was narrower than ADR-0020's original framing** — the
  store resolvers and the `Integration` document kind both already
  existed and worked independently; the only missing piece was threading
  a resolved document into `get_integration(..., config=...)`, the same
  pattern `InfraIntegration` resolution already used.
- **Reused `integration_resolution.py`'s existing auto-bind rule rather
  than inventing a second one** — promoted `_auto_bind_config()` to a
  public `bind_integration_config()`, used identically by both the
  pre-existing `InfraIntegration` callers and the new store-resolution
  caller. Confirmed no circular-import risk by running the real test
  suite after wiring the new cross-module import, not just static checks
  (`integration_resolution.py` doesn't import `value_controller.py`/
  `solution_context.py`).
- **Field mapping decided per-field, not per-resolver** — each resolver
  falls back to its own existing env var independently per field, so a
  document that sets `endpoints.address` but omits `configuration` still
  gets a fully working resolver instead of an all-or-nothing switch.
- **`configuration["project_id"]`/`["environment"]` stay untyped dict
  access, not a dedicated `InfisicalConfigurationModel`** — decided:
  two keys, read in exactly one place, no cross-field validation needed
  between them. A typo'd key would silently no-op at resolve time rather
  than fail at schema-validation time, but that tradeoff wasn't judged
  worth a single-purpose sub-model for this narrow a surface — revisit
  only if `configuration` grows real cross-field validation needs for
  some resolver.
- **A disabled (`enabled: false`) matching `Integration` document stays
  silently ignored for stores** — decided: matches
  `bind_integration_config()`'s existing, unchanged behaviour for
  `InfraIntegration` bindings; no evidence surfaced that stores need to
  differ, and diverging would have been a second, unrelated behaviour
  change bundled into this design.
- **`authentication` was investigated concretely, not just deferred
  abstractly** — worked through what it would actually need to do for
  each of the three real integrations and found it wouldn't help even if
  built (see "Deliberately out of scope" above) — a bootstrapping-order
  problem (a store's own credential resolution depending on
  `resolve_values()`, which depends on stores already working) shared by
  the whole integration layer, not specific to this design.
- **`docs/integrations/` was created as a third documentation tier** —
  distinct from `docs/config/*.md` (generic schema reference, uniform
  across every integration type) and `docs/design/*.md` (design
  rationale): a task-focused "I have a real Infisical project — how do I
  point strata at it" guide, one file per integration plus an index,
  mirroring `docs/config/readme.md`'s own index convention. Built ahead
  of the code phases landing, since the worked examples already reflected
  the intended end state and had no dependency on the implementation.
- **Implemented in three independently-shippable, fully green phases**
  (promote/thread the auto-bind helper; wire real config fields into each
  resolver, field-by-field; correct the stale docs
  `store_model.py`/`integration-layer.md`/`docs/config/integration.md`
  pointed readers at) — each phase's own full check suite (mypy/ruff/
  import-linter/pytest) passed before the next began, with zero deviation
  from the original per-phase design in any of the three.
