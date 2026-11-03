# Deployment tokens - Design

- Status: current
- Last updated: 2026-11-03

## Overview

`${deployment:name}` and `${deployment:tenant}` are value tokens for the deployment being run. A workspace is shared
by every deployment and tenant that uses it, so nothing written in it can say which deployment is running, and
every other token kind reads something declared in a document. The result was that a Terraform state backend, which
needs a different key per deployment, could only get one from a variable typed into every deployment's
environment. With the tokens a workspace states the rule once:

```yaml
backend:
  type: azurerm
  configuration:
    key: ${deployment:name}.tfstate
```

and every deployment gets a unique, readable state key with nothing typed per deployment. The user-facing
reference, with examples for Azure, S3, GCS and local state, is "Terraform backend" in
[workspace.md](../config/workspace.md). The token mechanism as a whole is in
[value-token-resolution.md](value-token-resolution.md).

## Current Design

### The tokens

| Token                  | Value                                                         |
| ---------------------- | ------------------------------------------------------------- |
| `${deployment:name}`   | the deployment's `meta.name`                                  |
| `${deployment:tenant}` | the deployment's `spec.tenant`, from the deployment or a base |

Only these two keys exist (`DEPLOYMENT_TOKEN_KEYS` in `utils/value_tokens.py`). `${deployment:tenant}` on a
deployment with no tenant is an error, never an empty string. `$${deployment:name}` is a literal, like any escaped
token.

### Where it works

Resolution is the same pass as `${var:}`. `deploy_controller.py` appends the deployment's entries to the `tokens`
map it already builds from the resolved values and the outputs of earlier steps, and that one map reaches the
Terraform backend, the Terraform projection, Helm, Compose and script resolvers. So the tokens work in any field
strata resolves at deploy time, not only a backend. They are deliberately not added to `ValueResolution.values`,
which feeds `TF_VAR_*`, secret redaction and the GitOps path.

An environment store's own `value` cannot use them (`store_value_token_unsupported`): a store value is resolved
before a deployment is chosen.

### The flat-map key

Resolution reads a token's key from one flat mapping and ignores its kind, so the entries are stored as
`deployment:name` and `deployment:tenant`. `deployment_token_values(name, tenant)` builds them, leaves the tenant
out when there is none, and is the only place that knows the key shape; `_lookup_key()` applies it for both
resolvers that look keys up (`resolve_value_tokens()` and `resolve_value_tokens_renaming_secrets()`). The token
regex never allows a colon in a key, so a variable, secret, feature or step output (`step.key`) can never equal one
of these entries.

### Validation

`strata validate` judges the tokens per deployment, in every document the deployment reaches:

- `unresolved_value_tokens()` skips the kind, as it skips `value`: it is not an environment key.
- `unresolved_deployment_tokens(model, deployment_name, tenant)` in `services/environment_service.py` judges it
  against the same entries `deploy run` uses, so the two cannot disagree. A key other than `name` or `tenant` is
  `unknown_deployment_token`; `${deployment:tenant}` with no tenant is `unavailable_deployment_token`, naming the
  deployment.
- `_check_deployment_value_tokens()` in `semantic_checks.py` runs it with the resolved deployment, so a tenant set
  by an `extends` base counts. It runs whether or not the deployment resolves an environment, and skips a
  `partial: true` base, which is judged only once a deployment extends it.

`deploy run` does not run `validate`, so a missing tenant also fails there, as a diagnostic that names the token
and before any Terraform command.

### In the example solution

`config/workspaces/main.yaml` uses `key: ${deployment:name}.tfstate`. Tests over the example check that every
deployable deployment resolves to its own `<name>.tfstate` (including one with no tenant), that `deploy run` passes
the key to `terraform init`, and that a key using `${deployment:tenant}` is reported for the tenant-less deployment
only.

## Related Decisions

- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md): the unified
  `${kind:KEY}` token syntax this extends.
- [value-token-resolution.md](value-token-resolution.md): the resolver, the escape syntax and the validation reach.

## History

- **A token, not a default key.** Defaulting a backend's `key` to `<deployment name>.tfstate` is smaller to write but
  hides the naming rule inside strata, and `backend.type` is an open string with an untyped `configuration`, so
  strata cannot know which backend types take a `key` at all (GCS takes a `prefix`). The token is explicit and lets
  each workspace choose its own prefix.
- **A token, not a variable.** A variable that holds the state key works today, but it exists only to feed the key
  and each deployment still has to list the right environment for it to resolve. The knowledge already lives in
  strata, which knows which deployment it is running.
- **`${value:}` cannot do it.** It resolves once per solution with no deployment chosen, and it must name a fixed
  document; a shared workspace has none to name.
- **Not labels.** `meta` is not merged through `extends` or the tenant, so a label costs the same per-deployment
  typing as the variable it would replace. `meta.name` is the one source that needs nothing typed.
  `spec.properties` and `spec.custom` do inherit and would suit a shared default, but then someone still types a
  value somewhere.
- **Two fixed keys, not a general `${deployment:PATH}`.** A path into the resolved deployment would also reach
  `spec.layers.segments.*`, `spec.properties.*` and `spec.custom.*`, and the path walk and scalar rule already exist
  for `${value:}`. It would also turn the whole deployment schema, inert fields included, into a token surface, so a
  field rename would break workspaces that read it. It can be added later without changing the two keys.
- **A missing tenant is an error.** The first draft made it empty. An empty value in a state key silently produces
  colliding keys such as `-.tfstate`, and `resolve_value_tokens()` already holds that an unknown reference is never
  an empty substitution.
- **A colon in the flat key, not a dot.** With `deployment.name`, a step called `deployment` that outputs `name`
  would produce the same key as `${output:deployment.name}`, and a variable declared as `deployment.name` could be
  shadowed. The regex excludes a colon from every key, which rules all of that out.
- **Validation must not depend on an environment.** The existing per-deployment token check skipped a deployment
  with no resolvable environment, but a deployment token needs none, so the two checks run independently. A
  `partial: true` base is skipped because its tenant is only known once merged; judging it would flag a tenant-less
  base that a tenant-bearing deployment extends.
- **The skill table is enforced.** A drift test requires the agent skill's token table to list every kind in
  `VALUE_TOKEN_KINDS`, so adding the kind required its row.
- **Renaming a deployment moves its state.** A new name gives a new, empty state, so a rename becomes a migration
  (`terraform state` commands) or is never done. The tokens make that a hard rule rather than creating it: the
  default build folder is already `build/<deployment>`.
- **Backend values go through `str()`.** Found while writing the examples, and independent of the tokens: a YAML
  `true` reaches `-backend-config` as `True`, so a boolean setting is written as a quoted string.
