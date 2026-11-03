# Deployment tokens - Work

- Status: draft
- Last updated: 2026-11-03

## Overview

A workspace is shared by every deployment and tenant that uses it, so nothing in a workspace document can say
which deployment is running. That matters most for a Terraform state backend: each deployment needs its own
state key, and today the only way to supply one is a per-deployment variable (`${var:...}`) that an author types
into every deployment's environment.

strata already knows the deployment's `meta.name` and its tenant when a step runs. It does not offer them as
value tokens. This doc proposes built-in `${deployment:...}` tokens so a backend can say
`key: ${deployment:name}.tfstate` once, in the workspace, and every deployment gets a unique, readable key.

Not in `docs/design/` yet: nothing is built, and the scope, the empty-tenant behavior and the token naming are
open (below).

## Current state

Verified against the source on 2026-11-03.

- **What a token resolves against.** The deploy-time map is built in `deploy_controller.py`
  (`tokens = {**resolved.values, **visible_outputs}`): the variables, secrets and features declared by the
  deployment's environments, plus the outputs of earlier steps.
- **Where a backend is resolved.** The same `tokens` map is applied to `provisioner.backend.configuration` by
  `resolve_value_tokens_in_mapping()` just before `terraform init`. The map also reaches the Helm, Compose,
  script and Terraform-projection resolvers, so a token that is in the map works wherever strata resolves at
  deploy time.
- **What is not in the map.** The deployment's name and tenant. They are used elsewhere: the Terraform projection
  fills `strata.workspace.deployment` and `strata.tenant`, but those are Terraform variables, and a backend block
  cannot read variables.
- **Why `${value:...}` does not help.** It is resolved once per solution, with no deployment chosen, and it must
  name a fixed document (`value_references.py`). A shared workspace has no fixed document to name.
- **Resolution is kind-blind.** `resolve_value_tokens()` looks up the token's `key` in one flat mapping and
  ignores the kind. `${output:STEP.KEY}` works only because its key carries its own prefix.
- **Validation is per deployment already.** `_check_deployment_value_tokens()` in `semantic_checks.py` walks every
  document reachable from each deployment, with the deployment and its tenant in scope, and
  `unresolved_value_tokens()` already special-cases the `value` and `output` kinds. A new kind fits the same
  shape.
- **Name charset.** `PlatformName` is `^[a-z][a-z0-9_-]*$`: lowercase letters, digits, hyphens and underscores.
  All of it is valid in an Azure blob name, an S3 key and a GCS object name.

## Proposed design

Two built-in tokens, resolved in the same pass as `${var:}`:

| Token                  | Value                                                     |
| ---------------------- | --------------------------------------------------------- |
| `${deployment:name}`   | the deployment's `meta.name`                              |
| `${deployment:tenant}` | the deployment's `spec.tenant`; an error when it has none |

```yaml
backend:
  type: azurerm
  configuration:
    key: ${deployment:name}.tfstate
```

Changes, in the order they would be built:

1. **Grammar.** Add `deployment` to `VALUE_TOKEN_KINDS`, `VALUE_TOKEN_PATTERN` and
   `_RESOLVE_VALUE_TOKEN_PATTERN` in `utils/value_tokens.py`, and to the `_UNSUPPORTED_STORE_VALUE_TOKEN_KINDS`
   set in `services/environment_service.py` (an environment store's own `value` must not use it).
2. **Lookup key.** Store the two values in the token map under `deployment.name` and `deployment.tenant`, and have
   `_substitute()` look up `deployment.<key>` for this kind. Without a prefix, a variable named `name` and
   `${deployment:name}` would share one slot in the flat map.
3. **Injection.** One place: where `tokens` is built in `deploy_controller.py`, add the two entries from the
   `deployment` document that `resolve_deployment_workspace()` already returned.
4. **Validation.** In `unresolved_value_tokens()`, accept only the keys `name` and `tenant` for this kind
   (anything else is `unsupported_value_token_kind`-style error naming the two valid keys). In
   `_check_deployment_value_tokens()`, report `${deployment:tenant}` on a deployment with no tenant, before any
   deploy.
5. **Schema and docs.** `ProvisionerBackendModel.configuration`'s description says it supports `var`, `secret`
   and `feature`; update it, the token table in `src/strata/templates/agent/strata/SKILL.md`,
   `docs/config/workspace.md` and `docs/design/value-token-resolution.md`. The exported schemas have tests that
   keep them in step.

Escaping needs no change: `$${deployment:name}` is already a literal.

## Candidate sources on the deployment

Checked against `deployment_model.py` and `docs/config/deployment.md` for what a backend could read:

| Source                                              | Single value?           | Set once, shared?                                        | Fit for a state key                                                   |
| --------------------------------------------------- | ----------------------- | -------------------------------------------------------- | --------------------------------------------------------------------- |
| `meta.name`                                         | yes, always present     | no need: it is the identity                              | best: unique, nothing typed                                           |
| `spec.tenant`                                       | yes, optional           | per deployment (a base can set it)                       | good as a prefix part; absent on shared deployments                   |
| `spec.workspace`                                    | yes, optional on a base | per deployment (a base can set it)                       | same for every deployment of a workspace: a prefix, not unique        |
| `spec.version`                                      | yes, optional           | per deployment (a base can set it)                       | changes on promotion: unsuitable in a key                             |
| `meta.labels.<key>`                                 | usually a string        | no: `meta` is not merged through `extends` or the tenant | typed per deployment, only moved from the environment to the document |
| `spec.layers.segments.<name>`                       | yes, strings            | per deployment; checked against the document's directory | good where a path convention is in use (for example `control: dev`)   |
| `spec.properties.<key>`                             | any type                | yes: merged over tenant, environments and `extends`      | a shared default with per-deployment override                         |
| `spec.custom.<key>`                                 | any type                | yes: same merge chain                                    | same, but not validated by `attributeconfig` unless one targets it    |
| `spec.environments`                                 | list                    | appended across `extends`                                | not a single value; indexing it is fragile                            |
| `stages`, `providers`, `assets`                     | lists                   | merged by name or slot                                   | not single values                                                     |
| `locking`, `lifecycle`, `configurations`, `partial` | -                       | -                                                        | not interpreted by strata today, or not a value                       |

Labels are the weak candidate: they do not inherit, so they cost the same per-deployment typing as the variable
they would replace. `meta.name` is the only source that needs nothing typed. `spec.properties` and
`spec.custom` are the only ones that inherit, so a base can supply a default (for example a prefix) that a leaf
overrides.

A general form would cover all of these: `${deployment:PATH}`, where `PATH` is a dotted path into the resolved
deployment (after `extends` and tenant defaults), for example `meta.name`, `spec.tenant`,
`spec.layers.segments.control` or `spec.properties.state_prefix`. `value_references.py` already has the path walk
(`_walk_value_reference_path`) and the scalar rule (`value_reference_not_scalar`); `${value:kind.name.path}` does
the same for a fixed document. The cost is that the whole deployment schema becomes a token surface, including
inert fields, and a field rename would break workspaces that read it.

## Decisions

Taken on 2026-11-03, following the proposals above: two fixed keys (`name`, `tenant`); an error, not an empty
value, for `${deployment:tenant}` on a tenant-less deployment; the token usable in every deploy-time field; a
token, not a default key; no further tokens. A general `${deployment:PATH}` can follow later without changing
these. The reasoning:

- **Two fixed keys or a general path.** `${deployment:name}` and `${deployment:tenant}` only, or
  `${deployment:PATH}` over the resolved deployment (a missing or non-scalar path is an error, as for
  `${value:}`). A general path needs the token map filled per referenced path, not with two fixed entries.
- **Empty tenant.** The first proposal made `${deployment:tenant}` empty when the deployment has no tenant. This
  doc recommends an error instead, in line with `resolve_value_tokens()` ("an unknown reference is always an
  error, never a silent empty-string substitution"). An empty value in a state key silently produces
  `-.tfstate`-style keys that collide.
- **Scope.** Because the map is shared, the token would work in every deploy-time field (module service values,
  Helm values, Terraform projection), not just the backend. Either accept that, or restrict it to
  `backend.configuration` at first and reject it elsewhere in validation. Restricting needs a per-field check in
  the generic token walk, so allowing it everywhere is the smaller change.
- **Default key instead of a token.** If a backend sets no `key`, default it to `<deployment name>.tfstate`. This
  is smaller to write but hides the naming rule inside strata, and `backend.type` is an open string with an
  untyped `configuration` dict, so strata does not know which backend types take a `key` at all. The token is
  explicit and lets each workspace choose its own prefix.
- **Further tokens.** Only `name` and `tenant` have a stated use. `workspace`, `environment` and `version` are
  not proposed.

## Backend examples

With the proposed tokens. A step passes `backend.configuration` to `terraform init` as `-backend-config`
settings; the backend block itself (`backend "azurerm" {}`) stays in the Terraform code, and `backend.type` is not
sent to Terraform. The examples assume deployments `prd-contoso` (tenant `contoso`), `prd-acme` (tenant `acme`)
and `prd-shared` (no tenant).

**One rule for every deployment (Azure blob).** The only per-deployment part is the token.

```yaml
backend:
  type: azurerm
  configuration:
    resource_group_name: ${var:tf_state_resource_group}
    storage_account_name: ${var:tf_state_storage_account}
    container_name: tfstate
    key: ${deployment:name}.tfstate
```

| Deployment    | State key             |
| ------------- | --------------------- |
| `prd-contoso` | `prd-contoso.tfstate` |
| `prd-acme`    | `prd-acme.tfstate`    |
| `prd-shared`  | `prd-shared.tfstate`  |

**A folder per tenant.** A blob name may contain `/`, so the tenant becomes a folder. This workspace can then only
be used by deployments that have a tenant: `prd-shared` fails `strata validate` with a message that it has no
tenant, instead of getting a key such as `/prd-shared.tfstate`.

```yaml
backend:
  type: azurerm
  configuration:
    resource_group_name: ${var:tf_state_resource_group}
    storage_account_name: ${var:tf_state_storage_account}
    container_name: tfstate
    key: ${deployment:tenant}/${deployment:name}.tfstate
```

| Deployment    | State key                     |
| ------------- | ----------------------------- |
| `prd-contoso` | `contoso/prd-contoso.tfstate` |
| `prd-acme`    | `acme/prd-acme.tfstate`       |

**S3.** A shared prefix from a variable, the deployment name as the folder.

```yaml
backend:
  type: s3
  configuration:
    bucket: ${var:tf_state_bucket}
    region: ${var:tf_state_region}
    key: ${var:tf_state_prefix}/${deployment:name}/terraform.tfstate
    dynamodb_table: ${var:tf_lock_table}
```

**GCS.** The backend takes a `prefix`, not a `key`.

```yaml
backend:
  type: gcs
  configuration:
    bucket: ${var:tf_state_bucket}
    prefix: ${deployment:name}
```

**Local state (a development workspace).**

```yaml
backend:
  type: local
  configuration:
    path: .state/${deployment:name}.tfstate
```

**Rejected by `strata validate`**

| Written                                              | Why                                                             |
| ---------------------------------------------------- | --------------------------------------------------------------- |
| `${deployment:workspace}`                            | only `name` and `tenant` exist (a general path is a later step) |
| `${deployment:tenant}` on a tenant-less deployment   | no tenant to substitute; reported before any deploy             |
| `${deployment:name}` in an environment store `value` | a store value resolves before a deployment is chosen            |

To keep the text `${deployment:name}` as-is (for a tool that has its own `${...}` syntax), write
`$${deployment:name}`.

**Booleans in `backend.configuration`.** A non-string value is converted with Python's `str()` before it reaches
`-backend-config` (`deploy_controller.py`), so a YAML `true` becomes `True`. Write a boolean setting as a quoted
string (`"true"`). This is independent of the tokens.

## Consequences to accept

- **Renaming a deployment moves its state.** A new name gives a new, empty state, so a rename becomes a
  migration (`terraform state` commands) or is never done. It becomes a hard rule.
- **Decide before the first real deploy.** Every key changes from a hand-typed form to the generated one.
- **No change for authors who keep the variable.** A variable that holds the state key keeps working.

## Implementation plan

One phase, one focused change, then the full check sequence (`ruff format`, `ruff check --fix src tests`,
`mypy src`, `lint-imports`, `pytest -q`) before the next phase. Layering stays
`commands > controllers > services > integrations > models > utils`: the token logic lives in `utils`, the
validation hook in `services`, the wiring in `controllers`.

Verified against the source before planning (2026-11-03): the kind list appears in exactly two regexes in
`utils/value_tokens.py`; three functions there substitute tokens and two of them look the key up themselves
(`resolve_value_tokens()` and `resolve_value_tokens_renaming_secrets()`; `_tracking_secrets()` and `_in_mapping()`
call the first); `unresolved_value_tokens()` has two callers (`EnvironmentService.validate_document_tokens()` and
`_check_deployment_value_tokens()`); `deploy_controller.py` builds `tokens` in one place and passes it to the
backend, Helm, Compose, script and Terraform-projection resolvers.

### Phase 1: grammar and resolution (`utils`)

Files: `src/strata/utils/value_tokens.py`, `tests/strata/utils/test_utils_value_tokens.py`.

1. Add `deployment` to `VALUE_TOKEN_KINDS`, `VALUE_TOKEN_PATTERN` and `_RESOLVE_VALUE_TOKEN_PATTERN`.
2. Add `DEPLOYMENT_TOKEN_KEYS = ("name", "tenant")` and
   `deployment_token_values(name: str, tenant: str | None) -> dict[str, str]`, returning
   `{"deployment.name": name}` plus `{"deployment.tenant": tenant}` only when a tenant is set. One function builds the
   entries, so deploy and validation cannot disagree.
3. Add a private `_lookup_key(kind, key)` returning `deployment.<key>` for the `deployment` kind and `key`
   otherwise. Use it in both `_substitute()` functions (`resolve_value_tokens()` and
   `resolve_value_tokens_renaming_secrets()`). A third copy of the lookup would repeat the bug class where one
   wrapper is missed.
4. Tests: `name` and `tenant` resolve; a missing `deployment.tenant` raises the existing "did not resolve" error;
   a variable named `name` does not change `${deployment:name}`; `$${deployment:name}` stays literal;
   `extract_value_tokens()` returns `("deployment", "name")`; `${deployment:}` is still malformed;
   `resolve_value_tokens_in_mapping()` and `resolve_value_tokens_renaming_secrets()` both resolve it.

Done when: the new tests pass and no existing `value_tokens` test changes.

### Phase 2: validation (`services`, `controllers`)

Files: `src/strata/services/environment_service.py`, `src/strata/controllers/semantic_checks.py`, with tests in
`tests/strata/controllers/test_semantic_checks.py` and the environment service tests.

1. Add `deployment` to `_UNSUPPORTED_STORE_VALUE_TOKEN_KINDS`, so an environment store's `value` rejects it.
2. Skip the `deployment` kind in `unresolved_value_tokens()` (as it already skips `value`): it is not an
   environment key, so the declared-keys check has no opinion. A new `unresolved_deployment_tokens(model,
   deployment_name, tenant)` judges it instead, against `deployment_token_values()` (the entries `deploy run` will
   use, so the two cannot disagree):
   - a key that is not `name` or `tenant`: `unknown_deployment_token`, naming the valid keys;
   - `tenant` on a deployment with no tenant: `unavailable_deployment_token`, naming the deployment.
3. In `_check_deployment_value_tokens()`, run the new check on every document the deployment reaches, using the
   resolved deployment (`resolved[entry.ref.name]`, so a tenant set by an `extends` base counts). Two deviations
   from the first draft, both found while reading the loop:
   - it ran only when the deployment had a resolvable environment (`declared is None` skipped everything), but a
     deployment token needs no environment, so the two checks are now independent;
   - a `partial: true` base is skipped for this check, like the other per-deployment checks: it is judged once
     merged, which is where its tenant is known.
4. Tests: valid `name` and `tenant`; `name` on a tenant-less deployment; the tenant token on a tenant-less one;
   an unknown key; the token in an environment `properties` value (accepted) and in an environment store `value`
   (`store_value_token_unsupported`); a partial base without a tenant (not judged); a tenant inherited through
   `extends`; a deployment with no resolvable environment. The partial-base and no-environment tests were checked
   red-green by removing each guard in turn.

Done when: `strata validate` on a copy of `config/` reports each broken case and nothing else changes.

### Phase 3: deploy-time injection (`controllers`)

Files: `src/strata/controllers/deploy_controller.py`, `tests/strata/controllers/test_deploy_controller.py`.

1. Where `tokens` is built, append `**deployment_token_values(deployment.meta.name, deployment.spec.tenant)`
   last, so a declared variable can never shadow the built-in entries.
2. Tests, modelled on `test_deploy_run_resolves_backend_configuration_tokens`: `key=<name>.tfstate` reaches
   `terraform init`; `${deployment:tenant}` with a tenant; a tenant-less deployment returns a diagnostic, not a
   traceback (deploy does not run `validate`, so the resolver error handling must hold); one Compose or Helm
   module value using the token, to pin the "works everywhere" decision.
3. Confirm the existing `try/except ValueError` call sites still cover every path that now sees the new kind
   (backend, Helm, Compose, script, Terraform projection).

Done when: a real `deploy run` against the fake runner passes `-backend-config key=<name>.tfstate`.

### Phase 4: docs, schema and skill

- `ProvisionerBackendModel.configuration` description in `models/provisioning_model.py` (mentions only `var`,
  `secret` and `feature`).
- `docs/config/workspace.md` (backend section), `docs/config/deployment.md` (one note that its name and tenant are
  available as tokens), `docs/design/value-token-resolution.md` (the kind list and a short section for the new
  kind), `docs/design/v2-schema-overview.md` (value token row).
- The token table in `src/strata/templates/agent/strata/SKILL.md` (done in Phase 1: a drift test requires a row for
  every kind in `VALUE_TOKEN_KINDS`) and `.github/CHANGELOG.md` under
  `[Unreleased]` (Added).
- Move the backend examples from this doc into `docs/config/workspace.md`.
- Run the schema export tests (`tests/strata/models/test_schema_quality.py` and the exporter tests) and a strict
  Sphinx build.

### Phase 5: dogfood and a negative check

1. In `config/workspaces/main.yaml`, add `key: ${deployment:name}.tfstate` to the backend.
2. `strata validate` in `config/` passes; `strata build run` for one deployment still works.
3. On a copy in `$env:TEMP` (never edit `config/` for a negative test), remove a deployment's tenant and use
   `${deployment:tenant}`: `validate` must fail with the tenant message.

### Phase 6: close out

- Full `scripts/Check.ps1` (ruff, mypy, import-linter, pytest, smoke test, Sphinx).
- Graduate this doc to `docs/design/deployment-tokens.md`: collapse the plan and the changelog into one trailing
  `## History` section, delete the work doc, and fix any links to it.
- Release: a new feature, so the next version is `2.1.0`, not a patch. Note it in `docs/work/release-strategy.md`.

### Risks

- **A resolver wrapper is missed.** The lookup is in two functions today; Phase 1 puts it in one helper and tests
  both wrappers.
- **A traceback instead of a diagnostic.** `resolve_value_tokens()` raises a bare `ValueError`. Every call site
  already converts it, but Phase 3 tests the tenant-less case end to end.
- **State key changes.** Existing workspaces keep their hand-typed key until someone edits them; nothing changes
  for them. Switching to the token is a one-time state move per deployment, so it is a deliberate step per
  workspace, not part of this change.
- **Meaning of `deployment` in an environment document.** An environment is checked once per deployment that
  reaches it, so the token validates there; an environment checked on its own reports it as unsupported. Phase 2
  tests both.

## Remaining work

- [x] Decide the open questions above (see Decisions).
- [x] Phase 1: grammar and resolution.
- [x] Phase 2: validation.
- [ ] Phase 3: deploy-time injection.
- [ ] Phase 4: docs, schema and skill.
- [ ] Phase 5: dogfood and a negative check.
- [ ] Phase 6: close out and release.

## Changelog

- 2026-11-03: Phase 2 built: `unresolved_deployment_tokens()` in `environment_service.py`, wired into
  `_check_deployment_value_tokens()` in `semantic_checks.py`, and `deployment` added to the kinds an environment
  store `value` rejects. 20 new tests. Full suite 3958 passed; ruff, mypy and import-linter clean. On a copy of
  `config/`, `${deployment:tenant}` in the backend is reported for `prd-acme-deployment`, which has no tenant:
  the Phase 5 dogfood must use `${deployment:name}` only.
- 2026-11-03: Phase 1 built (`utils/value_tokens.py`): the `deployment` kind in `VALUE_TOKEN_KINDS` and both
  regexes, `DEPLOYMENT_TOKEN_KEYS`, `deployment_token_values()`, and one `_lookup_key()` used by both substituting
  resolvers. 16 new tests in `test_utils_value_tokens.py`. `test_skill_explains_every_value_token_kind` failed
  until the skill's token table got a `deployment` row, so that row moved forward from Phase 4. Full suite
  3938 passed; ruff, mypy and import-linter clean.
- 2026-11-03: Added the implementation plan (six phases, risks), checked against the call sites of the token
  resolvers, the validation entry points and the deploy-time token map.
- 2026-11-03: Decisions taken (two fixed keys, error on an empty tenant, any deploy-time field, token rather
  than default key). Added the backend examples and the boolean caveat.
- 2026-11-03: Created from a question on how to give every deployment a unique state key without a per-deployment
  variable. Verified against the source; corrected the name charset (underscores are allowed) and changed the
  empty-tenant behavior from "empty" to "error".
