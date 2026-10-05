
# Store Integration Configuration — Binding `kind: integration` Documents to Infisical/Azure Key Vault/Azure App Config

- Status: **Phases 1-3 implemented (2026-09-29)** — the auto-bind wiring
  exists and is tested, all three resolvers now actually read
  `config.spec.endpoints`/`.configuration` when a real `Integration`
  document is bound (falling back to their existing env vars unchanged
  otherwise), and the stray docs this design set out to fix
  (`store_model.py`'s docstrings, `integration-layer.md`'s Built classes
  table/Changelog) are corrected
- Date: 2026-09-29
- Related: [integration-layer.md](integration-layer.md) (the subsystem this
  extends — `Integration`/`StoreIntegration` base classes, the registry,
  the three resolver classes), [ADR-0021](../decisions/0021-integration-layer.md)
  (D3 in particular — the existing type-vs-name-keying decision this design
  must not contradict), [value-token-resolution.md](value-token-resolution.md)
  (the resolved-`values` flat map this design's "deliberately out of scope"
  section explains it cannot safely reach into)

## Problem — the config document exists, but nothing ever reads it

Prompted by a direct request ("we need infisical, azure keyvault, azure
appconfig") while resuming [ADR-0020](../decisions/0020-v1-consumer-feature-priority.md)'s
Remaining Work list. Checked the real, current state of each piece
directly rather than assuming from the ADR's own (now partly stale) text:

- `kind: integration` is a fully-built, standalone document kind
  (`integration_model.py`, ADR-0021) — `type`, `capabilities`,
  `authentication`, `endpoints`, `configuration`, `custom`, etc.
- `InfisicalResolver`/`AzureKeyVaultResolver`/`AzureAppConfigResolver`
  (`strata/integrations/*_resolver.py`) are fully real, working
  `StoreIntegration` classes — Infisical over REST, the two Azure resolvers
  over the real Azure SDK with `DefaultAzureCredential`.
- `value_controller.py`'s `_resolve_store_value()` already dispatches
  `store: infisical`/`azure-keyvault`/`azure-appconfig` to these classes
  and calls `.resolve(key)` — end-to-end secret/variable resolution
  genuinely works today, confirmed by reading the dispatch directly, not
  assumed.

**What's actually missing, confirmed directly in source:**

1. **No `kind: integration` document ever reaches a store resolver.**
   `value_controller.py`'s `_Resolvers.get(integration_type)` calls
   `get_integration(integration_type)` with **no** `config` argument,
   always. Compare `integration_resolution.py`'s `resolve_integration()`,
   which already does a named-or-auto-bound lookup
   (`registry.get(doc.spec.type, config=doc)`) for `InfraIntegration`
   (Terraform/Helm/Compose) — that pattern was simply never extended to
   stores.
2. **All three resolvers ignore `config` even when constructed with one.**
   Each accepts `config: IntegrationModel | None = None` (required by the
   base class signature) but reads `os.environ` exclusively —
   `INFISICAL_ADDR`/`INFISICAL_PROJECT_ID`/`INFISICAL_ENVIRONMENT`/
   `INFISICAL_TOKEN`/`INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET`,
   `AZURE_KEYVAULT_URL`, `AZURE_APPCONFIG_ENDPOINT`. `config` is stored
   (via `super().__init__(config)`) and then never referenced again in any
   of the three `__init__`s.
3. **`store_model.py`'s own module docstring is stale.** It currently
   claims "`constant`/`environment` ... are the only stores that resolve to
   anything today; the rest validate as recognized values but have no
   resolver behind them yet" — no longer true since the three resolvers
   above were built and wired into `_resolve_store_value()`'s dispatch.

This is a narrower, more concrete gap than ADR-0020's "port
`ConfigurationSpecModel.integrations`" framing suggested — that framing
predates ADR-0021's decision to make `Integration` its own standalone kind
rather than an embedded list on `Configuration` at all. Nothing about
`ConfigurationSpecModel` needs to change; the real gap is entirely inside
`value_controller.py` and the three resolver classes.

## Why type-based auto-bind, not a named `integration:` field on a store

[ADR-0021's D3](../decisions/0021-integration-layer.md) already decided
this, deliberately, and this design must not contradict it:
`VariableStoreModel`/`SecretStoreModel`/`FeatureStoreModel` have nothing to
name a specific `Integration` document by — `store: infisical` is a
**type**, not a reference to one declaration. D3 confirms this matches
v1's own real behaviour (`ValueController._get_integration_by_type` also
resolves by type, looping every registered integration and returning the
first type match) and explicitly frames v2's "one cached instance per
type" as *"a more deterministic version of that same behaviour, not a
weaker one."*

This design's job is to make that already-decided type-based lookup
actually **find** a real `Integration` document, not to invent named
binding for stores — that would reopen a question ADR-0021 already
closed. If a real, evidenced need for two differently-configured
same-type stores ever appears (e.g. two Infisical projects in one
solution), that is new evidence for a future design, not something to
speculatively build now.

## Proposed design

### 1. Reuse `_auto_bind_config()`'s exact rule, promoted to a shared function

`integration_resolution.py`'s `_auto_bind_config()` already implements the
right rule for this: zero enabled matches falls back to `config=None`
(today's env-var-only behaviour, unchanged); exactly one enabled match
uses it; more than one is a `UsageError` naming every candidate — never an
arbitrary pick, which is *stricter* than v1's own real behaviour (D3 above
already establishes "stricter than v1" as an accepted direction, not a
regression).

Promote it from a private, module-local helper (`_auto_bind_config`) to a
public one — `bind_integration_config(index, integration_type, *,
requester)` — still living in `integration_resolution.py` (the natural
home for "how does strata connect a type string to a real Integration
document," regardless of which capability tier is asking). Both
`resolve_integration()`/`resolve_module_integration()` keep calling it
unchanged (pure rename, zero behaviour change for the existing
`InfraIntegration` path); `value_controller.py` becomes the second caller.
No circular-import risk (checked directly): `integration_resolution.py`
imports only `solution_controller`, `strata.integrations.*`,
`strata.models.*`, `strata.utils.errors` — none of which import
`value_controller.py` or `solution_context.py`, so this is the same safe
direction Phase 5 of the cross-document-value-references work had to fix
*away* from, not another instance of it.

### 2. Thread the `DocumentIndex` into `_Resolvers`

`_Resolvers.__init__()` currently takes no arguments. It gains one:
`_Resolvers(index: DocumentIndex)`. `resolve_values()`'s one call site
(`resolvers = _Resolvers()`) passes `context.controller.index` — already
in scope there, so this is a one-line change at the call site, not a
signature change propagating anywhere else (nothing else constructs
`_Resolvers`).

`_Resolvers.get(integration_type)`'s body changes from:

```python
instance = get_integration(integration_type)
```

to:

```python
config = bind_integration_config(self._index, integration_type, requester=f"Store type '{integration_type}'")
instance = get_integration(integration_type, config=config)
```

Cache key stays `integration_type` (unchanged from today — D3's own
reasoning for why still applies unchanged, this design doesn't touch it).

### 3. Each resolver prefers `config.spec.*` over its own env var, falls back when absent

Every resolver keeps its existing env-var fallback **unchanged** — a
solution with no `Integration` document declared for a type must keep
working exactly as it does today (this is a purely additive change, not a
breaking one). Field mapping, decided by matching each resolver's existing
env var to the closest real `IntegrationSpecModel` field, not inventing new
schema:

| Resolver                 | Env var (fallback, unchanged) | `config.spec.*` field (preferred when set) |
| ------------------------ | ----------------------------- | ------------------------------------------ |
| `InfisicalResolver`      | `INFISICAL_ADDR`              | `endpoints.address`                        |
| `InfisicalResolver`      | `INFISICAL_PROJECT_ID`        | `configuration["project_id"]`              |
| `InfisicalResolver`      | `INFISICAL_ENVIRONMENT`       | `configuration["environment"]`             |
| `AzureKeyVaultResolver`  | `AZURE_KEYVAULT_URL`          | `endpoints.address`                        |
| `AzureAppConfigResolver` | `AZURE_APPCONFIG_ENDPOINT`    | `endpoints.address`                        |

`configuration` is `IntegrationSpecModel`'s existing free-form
`dict[str, Any] | None` passthrough field (already modeled, not new) — used
here for the two Infisical-specific settings that have no better-fitting
typed field (`endpoints`/`authentication` are both generic across every
integration type; `project_id`/`environment` are Infisical-specific
concepts with no equivalent for the two Azure resolvers). Each resolver
reads its own two or three keys directly (`config.spec.configuration.get(
"project_id")`, guarded by `config is not None`); no new typed model is
introduced for this — three call sites reading two dict keys does not
justify a dedicated `InfisicalConfigurationModel`.

`INFISICAL_TOKEN`/`INFISICAL_CLIENT_ID`/`INFISICAL_CLIENT_SECRET` are
**not** in this table — see "Deliberately out of scope" below.

### Worked examples — one `kind: integration` document per resolver

All three deliberately omit `authentication` — see "Why `authentication`
is left out of every example below" immediately after. Each declares only
the non-secret connection info the field-mapping table above actually
wires up; the real credential stays exactly where it already lives today
(a process env var, set outside strata by whatever secret manager/CI
pipeline runs it — Infisical's own universal-auth client secret, or
nothing at all for the two Azure resolvers, which use ambient managed
identity).

**Infisical** — the one integration with real non-secret settings beyond
the endpoint (`project_id`/`environment`), so the clearest case for why
`configuration` earns its place in the mapping table:

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
env vars. Nothing above this line replaces them.

**Azure Key Vault** — `endpoints.address` is the *entire* useful surface
here; there is no Key-Vault-specific `configuration` need, unlike
Infisical:

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

Auth stays ambient: `DefaultAzureCredential`'s own chain (managed
identity first) — genuinely nothing to declare for the common case. A
user-assigned identity still only needs `AZURE_CLIENT_ID` as a plain env
var (already how `DefaultAzureCredential` itself reads it, with or without
this document).

**Azure App Configuration** — identical shape to Key Vault, different
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

### 4. Fix `store_model.py`'s stale docstring

Update the module docstring's claim that only `constant`/`environment`/
`github` resolve today — three more (`infisical`/`azure-keyvault`/
`azure-appconfig`) do too, confirmed by this design's own investigation.

## Deliberately out of scope

- **`authentication` is not wired in this pass — and, checked directly,
  would not actually help any of these three integrations even if it
  were.** Every field on `OAuth2AuthenticationModel`/`AWSAuthenticationModel`/
  etc. is documented as *"a key reference resolved from environment
  declarations"* — i.e. the literal string in `client_id: "SOME_KEY"`
  names a `${var:}`/`${secret:}`-declared key, not the credential itself.
  Actually resolving that key needs the same `ValueResolution`/flat-
  `values`-map machinery `resolve_values()` itself builds — but a store's
  own auth is needed to build the values that its own credential might be
  one of, a real bootstrapping-order problem. Concretely, for these three:
  - **Infisical's real auth** (`INFISICAL_TOKEN`, or `INFISICAL_CLIENT_ID`
    + `INFISICAL_CLIENT_SECRET`) is exactly the kind of value that would
    plausibly be declared as a `${secret:...}` itself — meaning
    `authentication.oauth2.client_secret: "INFISICAL_CLIENT_SECRET_KEY"`
    would need `resolve_values()` to already have resolved
    `INFISICAL_CLIENT_SECRET_KEY`, which (if that key's own store were
    also Infisical) is circular by construction, not merely inconvenient.
    Even scoped to "only `constant`/`environment`-store keys may back an
    Integration's own `authentication`" (avoiding the circularity), that's
    still strictly no better than what already works today — `constant`/
    `environment` stores read a literal or a bare env var directly, which
    is exactly what the resolver already does by reading `os.environ`
    itself. `AuthenticationModel`'s indirection buys nothing here.
  - **Both Azure resolvers' real auth is `DefaultAzureCredential`**, whose
    entire point is *not* needing an explicit credential value at all
    (managed identity is ambient) — there is no secret for
    `OAuth2AuthenticationModel`'s `client_secret` field to even
    meaningfully hold in the common case; forcing one through
    `AuthenticationModel` would be modeling a credential these two
    integrations are specifically designed to avoid needing.
  So: **assume `AuthenticationModel` is not useful for any of these three,
  now or later, without first solving the bootstrapping-order problem
  above for the whole integration layer** (a separate, larger design, not
  scoped here) — the worked examples above deliberately show the realistic
  end state, not a temporary stand-in pending a future field.
  Confirmed directly: **nothing** in `strata.integrations` reads
  `config.spec.authentication` anywhere today (checked via a repo-wide
  search) — this is a pre-existing gap across the *whole* integration
  layer, not something newly discovered or specific to stores.
  `INFISICAL_TOKEN`/`_CLIENT_ID`/`_CLIENT_SECRET` and Azure's
  `DefaultAzureCredential` chain stay env-var/ambient-only — a real,
  working, unregressed mechanism, just not config-document-driven, and
  (per the analysis above) not obviously worth making so.
- **A named `integration:` field on `VariableStoreModel`/`SecretStoreModel`/
  `FeatureStoreModel`.** ADR-0021 D3 already decided against this need
  existing yet — see "Why type-based auto-bind" above. Revisit only with
  real evidence (a solution that genuinely needs two differently-configured
  instances of the same store type). **Elaboration**: [docs/config/integration.md](../config/integration.md)'s
  own current text ("referenced by name from a `provisioner` or a
  `variable`/`secret`/`feature` store") is imprecise for the store half of
  that sentence, confirmed against this design's own investigation — a
  store has no field to name one by, it auto-binds by **type**. Corrected
  as part of this design's own documentation phase (see Implementation
  Plan), not left to drift further.
- **Value-token resolution inside `IntegrationModel.spec.configuration`/
  `.endpoints.address`.** `Integration` is not one of the document kinds
  `_documents_reachable_from_workspace()` walks (confirmed directly —
  dns/network/firewall/provider/resource/topology/module/namespace/
  deployment/tenant/environment only), so a `${var:}`/`${secret:}` token
  written there today has no validate-time check and no deploy-time
  resolution mechanism either. A real, separate, pre-existing gap — out of
  scope here; these two fields are treated as plain literals by this
  design, matching how they are already used (or rather, not yet used) by
  everything else.
- **Azure App Config's feature-flag JSON-blob parsing** (`AzureAppConfigResolver`'s
  own docstring already flags this as unaddressed, unrelated to config
  binding) — untouched by this design.

## Impact map

### No change needed

- The registry (`registry.py`'s `get(integration_type, config=None)`) —
  already accepts `config`, already passes it through to the class
  constructor. Nothing here changes.
- `integration_resolution.py`'s `resolve_integration()`/
  `resolve_module_integration()` — unaffected by the rename (same
  behaviour, new name for the shared helper).
- `IntegrationModel`/`IntegrationSpecModel` (`integration_model.py`) — the
  schema already has every field this design reads (`endpoints.address`,
  `configuration`). No new fields.

### Small, additive change to an existing function

- `integration_resolution.py`: rename `_auto_bind_config()` ->
  `bind_integration_config()` (public), update its two existing call
  sites' spelling only.
- `value_controller.py`: `_Resolvers.__init__(self, index: DocumentIndex)`;
  `.get()`'s body calls `bind_integration_config()` then passes the result
  to `get_integration()`. `resolve_values()`'s one construction site passes
  `context.controller.index`.

### New code

- `InfisicalResolver.__init__`/`AzureKeyVaultResolver.__init__`/
  `AzureAppConfigResolver.__init__`: each gains a small
  "`config.spec.X` if set, else the existing env var" read per field in
  the mapping table above.

### Documentation

- `store_model.py`'s module docstring — corrected per above.
- This document.
- [integration-layer.md](integration-layer.md)'s "Remaining Work" section —
  add a line pointing here once implemented, matching that doc's own
  "grounded in the real source" convention (not duplicating this design's
  content into that file).
- [docs/config/integration.md](../config/integration.md) — correct the
  imprecise "referenced by name" claim for stores (see "Deliberately out
  of scope" above).
- **New**: `docs/integrations/` — a user-facing "how do I configure this"
  guide per real integration, distinct from `docs/config/integration.md`
  (the generic *schema* reference for the whole `kind: integration` shape,
  covering every type uniformly) the same way `docs/config/*.md` is a
  schema reference and `docs/design/*.md` is design rationale — this is a
  third, task-focused tier: "I have an Infisical project, how do I point
  strata at it," answered concretely, per real integration, with the
  worked example from this design plus the env vars still required for
  auth. One file per integration (`infisical.md`/`azure-keyvault.md`/
  `azure-appconfig.md`) plus an index (`README.md`), mirroring
  `docs/config/readme.md`'s own index-page convention.

## Implementation Plan

Phased, matching this project's own established convention — each phase
independently shippable and testable.

### Phase 1 — Promote and thread the auto-bind helper — ~~IMPLEMENTED (2026-09-29)~~

Implemented exactly as designed, no deviations. `_auto_bind_config()` ->
`bind_integration_config()` (public) in `integration_resolution.py`, both
existing call sites updated (pure rename, zero behaviour change — existing
`test_integration_resolution.py` tests confirmed passing unchanged).
`value_controller.py`'s `_Resolvers.__init__(self, index: DocumentIndex)`;
`.get()` now calls `bind_integration_config()` then passes the result to
`get_integration(..., config=...)`. `resolve_values()`'s one construction
site (`_Resolvers(context.controller.index)`) updated.

Confirmed no circular import at runtime (not just via mypy/ruff/
`get_errors`, which don't catch this class of bug — a lesson from this
same design's own earlier work elsewhere in the codebase): ran the actual
test suite immediately after wiring the new cross-module import, not just
static checks.

- **Tests**: 4 new in `test_value_controller.py`, all going through the
  real public `resolve_values()` entry point (matching that test file's
  own established convention of never importing a private name directly)
  with `get_integration` monkeypatched to a spy returning a minimal fake
  `StoreIntegration` (`_FakeStore`, never a real network call) — one
  enabled matching document passes it through; zero candidates passes
  `config=None` (today's behaviour, unchanged); a disabled candidate is
  ignored (`config=None`); more than one enabled candidate raises
  `UsageError` naming both. Full check suite green: mypy (108 files),
  ruff, import-linter (1 kept, 0 broken), pytest (1289 passed — the one
  unrelated failure in the full run is pre-existing, uncommitted local
  drift in the repo-root `config/` example solution, confirmed via `git
  status`/`git diff` to predate and be unrelated to this work).

Original design (for reference — implemented with zero deviation):

- Rename `_auto_bind_config()` -> `bind_integration_config()` in
  `integration_resolution.py`, exported (no leading underscore).
- `value_controller.py`: `_Resolvers.__init__(self, index: DocumentIndex)`;
  `.get()` calls `bind_integration_config()` then `get_integration(...,
  config=...)`. Update the one construction call site in
  `resolve_values()`.
- **Tests**: a new test proving a store type with exactly one enabled,
  matching `Integration` document gets it passed to `get_integration()`
  (mock/spy on `get_integration`, or a lightweight fake `StoreIntegration`
  subclass asserting `self.config is not None`); a regression test proving
  zero candidates still passes `config=None` (today's behaviour,
  unchanged); a regression test proving multiple candidates still raises
  `UsageError` naming every one (same rule as the existing
  `InfraIntegration` tests, applied to a store type instead). Existing
  `test_integration_resolution.py` tests continue to pass unchanged (pure
  rename).

### Phase 2 — Wire real config fields into each resolver — ~~IMPLEMENTED (2026-09-29)~~

Implemented exactly as designed, no deviations. Each resolver's
`__init__` now reads `config.spec.endpoints.address`
(`InfisicalResolver`/`AzureKeyVaultResolver`/`AzureAppConfigResolver`) and
`config.spec.configuration["project_id"]`/`["environment"]`
(`InfisicalResolver` only), falling back to the existing env var,
unchanged, whenever `config` is `None` or that specific field is unset —
confirmed per-field, not per-resolver all-or-nothing, exactly as designed.
Two of the three resolvers' error messages (raised when no URL/endpoint
resolves from either source) were updated to mention both routes
(`"set AZURE_KEYVAULT_URL, or bind an Integration document with spec.
endpoints.address set"`) rather than only the env var — checked directly
against existing tests' `pytest.raises(..., match=...)` assertions first;
both still matched (they only asserted the env var name appears as a
substring), so no existing test needed changing.

Each resolver's own module docstring was also updated in the same pass —
they previously opened with "Configured entirely from environment
variables," now stale since this phase; corrected to describe both routes
and explicitly note real credentials are unaffected (still env-var/ambient
only), cross-referencing this design's own "Deliberately out of scope"
section so a future reader isn't left wondering why `spec.authentication`
still isn't read.

- **Tests**: 16 new across the three resolvers' own test files
  (`test_infisical_resolver.py`/`test_azure_keyvault_resolver.py`/
  `test_azure_appconfig_resolver.py`) — one set per field (config
  preferred when set; env var used with no document bound; env var used
  when a document is bound but that field is unset), plus Infisical's own
  two default-value cases (`_DEFAULT_ADDR`/`_DEFAULT_ENVIRONMENT` still
  apply when neither config nor env var supplies a value). All 17
  pre-existing resolver tests confirmed passing unchanged. Full check
  suite green: mypy (108 files), ruff, import-linter (1 kept, 0 broken),
  pytest (1305 passed — the sole failure in the full run remains the same
  pre-existing, unrelated `config/` example-solution drift flagged in
  Phase 1's own changelog entry).

Original design (for reference — implemented with zero deviation):

- `InfisicalResolver`: `endpoints.address`/`configuration["project_id"]`/
  `configuration["environment"]`, per the mapping table above.
- `AzureKeyVaultResolver`/`AzureAppConfigResolver`: `endpoints.address`.
- **Tests**: one per resolver per field — config value used when
  `Integration` document is passed and the field is set; env var used when
  `config is None`; env var used when `config` is set but that specific
  field is unset (proving per-field fallback, not per-resolver
  all-or-nothing).

### Phase 3 — Documentation and verification — ~~IMPLEMENTED (2026-09-29)~~

The `docs/config/integration.md` correction and the entire
`docs/integrations/` directory (`README.md` +
`infisical.md`/`azure-keyvault.md`/`azure-appconfig.md`) were already
completed ahead of schedule during the earlier documentation-elaboration
pass (see this design's own Changelog) — nothing further needed there.
This phase's remaining scope was the two source/design-doc corrections:

- `store_model.py`'s module docstring corrected: it previously claimed
  "What does NOT exist is the *runtime* resolution layer" and that only
  `constant`/`environment`/`github` resolve — both now stale post-Phases
  1-2. Rewritten to name exactly which stores resolve against a real
  backend today (`infisical`, `azure-keyvault`, `azure-appconfig`, per the
  field-mapping table above) versus which remain recognized-but-unbuilt
  placeholders (`consul`/`vault`/`etcd`/`bitwarden`/`flagsmith`). The three
  affected enums' (`FeatureStoreType`/`VariableStoreType`/`SecretStoreType`)
  own docstrings were corrected the same way, each naming its specific
  resolved-vs-placeholder split rather than a blanket "see module
  docstring" pointer to stale text.
- [integration-layer.md](integration-layer.md)'s Built classes table Notes
  column and Changelog updated — the three store resolvers' rows no
  longer say "unchanged behaviour"/bare "Retrofit" now that they're
  config-driven; added a changelog entry pointing here rather than
  duplicating this design's content into that file, matching its own
  "grounded in the real source" convention. Its "Remaining Work" list
  itself needed no line item removed or added — the store-config-binding
  gap this design closed was never listed there in the first place
  (confirmed while first investigating this design, see this doc's own
  Changelog), so there was nothing stale to remove, only the two spots
  above that actively described the old, now-incorrect behaviour.
- Full check suite confirmed green: mypy (108 files), ruff, import-linter
  (1 kept, 0 broken), pytest (1305 passed — same pre-existing, unrelated
  `config/` example-solution drift as the sole failure, unchanged from
  Phases 1-2; this is a docstring/doc-only phase, so the passing test
  count itself is unchanged from Phase 2).
- Skipped: a live proof in `.v2-haven`/`.v2-cfg` — optional per the
  original design, not attempted this pass.

Original design (for reference — implemented with zero deviation apart
from the two already-ahead-of-schedule items noted above):

- Fix `store_model.py`'s stale docstring.
- Update [integration-layer.md](integration-layer.md)'s Remaining Work
  list.
- Correct [docs/config/integration.md](../config/integration.md)'s
  imprecise "referenced by name" claim for stores.
- **Create `docs/integrations/`** (new directory, no dependency on Phases
  1-2's code landing first — the worked examples already reflect the
  intended end state): `README.md` (index, mirrors `docs/config/readme.md`'s
  shape) plus one file per integration (`infisical.md`/`azure-keyvault.md`/
  `azure-appconfig.md`) — each covering what it is, the env vars still
  required for auth (unchanged by this design, see "Deliberately out of
  scope"), the worked `kind: integration` document from this design's own
  examples, how an Environment's `store:` field selects it by type, and a
  troubleshooting section for the "more than one enabled candidate"
  `UsageError` this design introduces.
- Full check suite (mypy/ruff/import-linter/pytest).
- Consider a live proof in `.v2-haven`/`.v2-cfg` if either declares a
  `kind: integration` document for one of these three types already — not
  required, matching this project's own "optional, most direct validation
  available" framing for this kind of proof.

## Open questions for review

1. **Is `configuration["project_id"]`/`["environment"]` (untyped dict
   access) acceptable, or should Infisical get a small typed sub-model
   (e.g. `IntegrationSpecModel.configuration` stays generic, but a
   resolver-specific `InfisicalConfigurationModel` validates the two keys
   it needs)?** Leaning untyped — two keys, read in one place, no
   cross-field validation needed between them — but flagging since typed
   would give a schema-validation-time error for a typo'd key name instead
   of a silent no-op at resolve time.
2. **Should a `config.spec.enabled == False` `Integration` document with a
   matching type be a hard validate-time error, or silently ignored (today's
   `bind_integration_config()`'s existing behaviour for
   `InfraIntegration`)?** Recommend: keep the existing behaviour unchanged
   (silently ignored, same as an `InfraIntegration` binding) — no evidence
   this needs to differ for stores, and changing it would be a second,
   unrelated behaviour change bundled into this one.

## Changelog

- 2026-09-29: Created, per direct request ("we need infisical, azure
  keyvault, azure appconfig" while resuming ADR-0020's Remaining Work
  list). Investigated the real current state directly rather than trusting
  ADR-0020's own framing — found the actual gap is narrower (store
  resolvers exist and work, but never receive a real `Integration`
  document) than "port `ConfigurationSpecModel.integrations`" suggested,
  since ADR-0021 already replaced that whole approach with a standalone
  kind. Design only, nothing implemented yet.
- 2026-09-29: Added a worked `kind: integration` example for each of the
  three resolvers, per direct request ("add an example for each
  integration, assume the authenticationmodel is of no help"). Made the
  "`authentication` is out of scope" reasoning concrete rather than
  abstract: worked through what `authentication` would actually need to
  do for each of the three real integrations and found it would not help
  even if built — Infisical's own real auth is exactly the kind of value
  that would need to be a `${secret:...}` reference itself (circular, or
  no better than today's direct env-var read), and both Azure resolvers'
  real auth (`DefaultAzureCredential`) is deliberately ambient/credential-
  free in the common case, leaving nothing for `client_secret`-shaped
  fields to meaningfully hold. Confirms the worked examples (declaring
  only `endpoints`/`configuration`, never `authentication`) are the
  realistic end state, not a stand-in pending a future field.
- 2026-09-29: Elaborated the design and expanded the Implementation Plan,
  per direct request ("elaborate on the design if needed, then create an
  implementation plan, for documentation add a docs/integrations/{doc}.md
  so people can easily find how to configure"). Found one more small,
  real inaccuracy while elaborating: `docs/config/integration.md` claims
  an integration is "referenced by name" from a store, which is only true
  for a `provisioner` — a store auto-binds by type (this design's own
  "Why type-based auto-bind" section) and has no field to name one by;
  added correcting it to Phase 3. Added a new documentation deliverable
  to Phase 3 and the Impact Map: `docs/integrations/` — a user-facing,
  per-integration "how do I configure this" guide, a third documentation
  tier distinct from `docs/config/*.md` (generic schema reference) and
  `docs/design/*.md` (design rationale). Created
  `docs/integrations/README.md` plus `infisical.md`/`azure-keyvault.md`/
  `azure-appconfig.md` in this same pass (a pure documentation deliverable,
  no dependency on Phases 1-2's code landing first — the worked examples
  already reflect the intended, designed end state). Phases 1-2 (the code
  itself) remain unimplemented, per the request's own scope (documentation
  only, not "implement").
- 2026-09-29: **Implemented Phase 1** (promote and thread the auto-bind
  helper), per request ("design, plan, and implement phase 1").
  Implemented exactly as designed, zero deviations: `_auto_bind_config()`
  promoted to public `bind_integration_config()` in
  `integration_resolution.py`; `value_controller.py`'s `_Resolvers` now
  takes a `DocumentIndex` and looks up a real `Integration` document by
  type before constructing. 4 new tests in `test_value_controller.py`, all
  through the real `resolve_values()` entry point with `get_integration`
  monkeypatched to a spy (never a real network call), matching that test
  file's own convention of not importing private names directly. Full
  check suite green: mypy (108 files), ruff, import-linter (1 kept, 0
  broken), pytest (1289 passed — the sole failure in the full run is
  pre-existing, uncommitted `config/` example-solution drift, confirmed
  unrelated via `git status`). Phase 2 (each resolver actually reading
  `config.spec.*`) and Phase 3 (documentation fixes) remain unimplemented
  — a bound `Integration` document reaches `self.config` today but no
  resolver reads it yet, so behaviour is still identical to before this
  phase from an end-user perspective.
- 2026-09-29: **Implemented Phase 2** (wire real config fields into each
  resolver), per request ("design, plan, and implement phase 2").
  Implemented exactly as designed, zero deviations: all three resolvers'
  `__init__` now prefer `config.spec.endpoints.address`
  (Infisical/Azure Key Vault/Azure App Config) and
  `config.spec.configuration["project_id"]`/`["environment"]` (Infisical
  only) over their existing env vars, per the field-mapping table above,
  falling back to the identical env-var behaviour whenever `config` is
  `None` or that specific field is unset. Hit one real, reusable mypy
  gotcha along the way: `(endpoint_address or environ.get("X", default)).
  rstrip("/")` — an `Optional[str] or str` fallback — spuriously failed
  narrowing ("Item 'None' of 'str | None' has no attribute 'rstrip'")
  even with an intermediate `addr: str = ...` assignment; resolved by
  rewriting as an explicit `if endpoint_address is not None: ... else:
  ...` block instead of the one-liner `or` pattern. 16 new tests across
  the three resolvers' own test files (one set per config field: config
  preferred, env-var fallback with no document bound, env-var fallback
  with a document bound but that field unset, plus Infisical's own
  default-value cases). All 17 pre-existing resolver tests confirmed
  passing unchanged before the new tests were added. Full check suite
  green: mypy (108 files), ruff, import-linter (1 kept, 0 broken), pytest
  (1305 passed — the same pre-existing, unrelated `config/`
  example-solution drift is the sole failure, unchanged from Phase 1).
  Phase 3 (documentation fixes to `store_model.py`'s stale docstring and
  `integration-layer.md`'s Remaining Work list) remains unimplemented —
  the `docs/config/integration.md`/`docs/integrations/` portion of Phase
  3 was already completed ahead of schedule in the prior, documentation-
  only pass.
- 2026-09-29: **Implemented Phase 3** (documentation and verification),
  per request ("design, plan, and implement phase 3") — the final phase,
  closing this design out. `store_model.py`'s module docstring corrected
  (it previously claimed the runtime resolution layer didn't exist at
  all, and that only `constant`/`environment`/`github` resolve — both
  stale post-Phases 1-2); now names exactly which stores resolve against
  a real backend today (`infisical`/`azure-keyvault`/`azure-appconfig`)
  versus which remain recognized-but-unbuilt placeholders
  (`consul`/`vault`/`etcd`/`bitwarden`/`flagsmith`). The three affected
  enums' own docstrings corrected the same way, replacing a blanket "see
  module docstring" pointer with each enum's specific resolved-vs-
  placeholder split. `integration-layer.md`'s Built classes table Notes
  column and Changelog updated to stop saying "unchanged behaviour" for
  the three store resolvers, with a changelog entry pointing back here
  rather than duplicating content — its "Remaining Work" list itself
  needed no edit, since the gap this design closed was never listed
  there to begin with (confirmed during this design's own investigation
  phase). Full check suite green: mypy (108 files), ruff, import-linter
  (1 kept, 0 broken), pytest (1305 passed, unchanged from Phase 2 since
  this phase touched only docstrings — same pre-existing, unrelated
  `config/` example-solution drift remains the sole failure). Skipped the
  optional `.v2-haven`/`.v2-cfg` live-proof step. All three phases of
  this design are now implemented; nothing further planned here.
