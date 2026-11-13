# Parameter sets and bases (`parameterset`, `meta.params`, `${param:}`) — Design

- Status: current
- Last updated: 2026-11-13

## Overview

A solution that runs the same stack in many places (one spoke per customer behind a few hubs) ends up with many
documents that are copies of each other. They differ in names built from a few identifiers and in a handful of real
values. `extends` shares structure, but it cannot share *identity*: a base cannot say "my environments are
`hub-<this hub>-env`", because it cannot refer to the document that extends it.

strata follows the Helm model:

- a kind, **`parameterset`**, holds one instance's plain values (a values file);
- a deployment, environment or tenant names sets per **role** in **`meta.params`**
  (`params: { hub: hub-hub02, spoke: spoke-acme-001 }`);
- **`${param:<role>.<key>}`** in that document and in every base of its `extends` chain reads the merged values,
  filled in when the solution loads;
- **`extends`** and **`partial`** work the same on deployments, environments and tenants;
- **constraints** on values are ordinary attributeconfig schemas;
- **`strata render`** shows a resolved document and where each of its values comes from.

A spoke then holds one parameter set and one short leaf per stack, and every real fact about it sits in one file.
The user documentation is the config reference (`docs/config/parameterset.md`, and the "Bases" sections of
`docs/config/environment.md` and `docs/config/tenant.md`) and the how-tos
([deployment bases](../how-to/parameterised-deployment-bases.md),
[environment and tenant bases](../how-to/environment-and-tenant-bases.md)).

## Shape

A parameter set holds plain keys and does not know its role; the document that names it gives the role.

```yaml
kind: parameterset
meta:
  name: spoke-acme-001
  labels: { role: spoke }        # optional: for attributeconfig selectors only
spec:
  values: { name: acme-001, hub: hub01, tenant: acme, code: acme, seq: "001" }
---
kind: deployment
meta:
  name: bootstrap_deployment_base
  name_rule: bootstrap-${param:spoke.name}
  params: { hub: bootstrap-defaults }        # the base's defaults, like a chart's values.yaml
spec:
  partial: true
  workspace: bootstrap
  tenant: ${param:spoke.tenant}
  environments: [solution-env, 'hub-${param:spoke.hub}-env', 'sub-${param:spoke.name}-env']
---
kind: deployment
meta:
  name: bootstrap-acme-001
  params: { spoke: spoke-acme-001 }
spec:
  extends: bootstrap_deployment_base
```

## Rules

1. **`parameterset`** is a kind. `spec.values` is a map of any YAML. It has no `extends`, no `partial`, no `params`.
   Values are plain: any `${` in a key or value is an error, so a set depends on nothing and is loaded first. The
   escape `$${...}` is allowed. A set is plain text in git and never holds a secret.
2. **`meta.params`** on a deployment, environment or tenant maps a role (a `PlatformName`) to one set or a list of
   sets. Every name must be a `parameterset`; one set may be named under several roles.
3. **A chain has one set of values per role.** Every document of an `extends` chain may name sets. Per role they
   merge root first, then down the chain, leaf last, later winning; within a list, later wins. Maps merge per leaf
   key; lists and scalars are replaced. A base's sets are defaults, the leaf's override them.
4. **`${param:role.key}`** in any document of the chain reads the chain's merged values of that role. Dots walk into
   nested maps and named list items, as `${value:}` does; `${param:role}` alone is the role's whole map.
5. **Where it may appear:** any string in the `spec` of a deployment, environment or tenant, reference fields
   included. Never in `meta`, `spec.extends` or `spec.partial` (names, sets and chains stay static, so the index never
   depends on substitution), and never in an environment's `secrets[].value`.
6. **Whole value or embedded.** A token that is the entire value is replaced by the value with its type, so a list
   or map can be inserted. A token inside a longer string needs a single value (`param_not_scalar`).
7. **A missing key is an error** (`param_missing`) naming the document, the field, the role's sets and the document
   it was resolved for. There is no default syntax in the token: defaults belong in a base's parameter set.
8. **Substituted text is never scanned again**, so a value cannot insert a token. Run-time tokens (`${var:x}`) are
   left for run time; `param` is not a run-time token kind, so no `${param:}` reaches run time.
9. **`partial: true` is always written, never implied.** A document that is not partial must resolve every
   `${param:}` of its chain.
10. **`meta.name_rule`** (optional, on any document of a chain) holds only `${param:}` tokens. Every non-partial
    document below it must have exactly that name once filled in (`name_rule_mismatch`, with both names). Names stay
    plain text but cannot drift from their values.
11. **`meta` is not templated** and not inherited: each document's `meta` is its own.
12. **Unused values are information** (`param_unused`): a top-level key no document reads through any role the set
    is named under, and no `${value:parameterset.<name>.spec.values.<key>}` reads. Not a warning, so
    `validate --strict` passes: a set may hold more than one document needs.
13. **The role comes from `params`, not from the set**, so two sets can never collide and a base names a role, never
    a set ("Roles").

## Roles

A customer layer reads two sets, the spoke's (which hub, which subscription) and the customer project's (its code).
Both have a `code`. Under roles they are `spoke.code` and `customer.code`, and each base says which one it means.
Without roles, one would silently override the other in every document that names both.

- A base hard-codes the roles it reads. Moving a key from every spoke's set into the hub's set changes its path
  (`spoke.provider` to `hub.provider`) in every base that reads it; a missed base is a `param_missing`, never a
  wrong value.
- Every leaf names every set it uses (a spoke behind `hub02` also names `hub: hub-hub02`). A set never names the
  sets it builds on, and nothing comes from the folder: each value is explicit, and one name per leaf is far less
  than repeating the values.

## Resolution

In order: parameter sets (loaded first, they reference nothing), environments, tenants, deployments. For one
document:

1. Walk the chain to its root, as written (cycles are `circular_extends`; a missing base is an `unknown_reference`
   on `spec.extends`).
2. Merge the chain's parameter sets per role (rule 3).
3. Substitute `${param:}` in each document's spec.
4. Merge the specs root first, child wins, with the kind's merge (below).
5. For a deployment, fold in its tenant's defaults (ADR-0024), after substitution.
6. Check `name_rule`, validate the merged result as an ordinary document, and check its references.

**Environments and tenants are resolved when the solution loads, and the result replaces the document in the
index** (`resolve_environments_and_tenants()`, called by `open_solution()`; ADR-0062). Every reader (value
assembly, `strata values`, token checks, geography checks, tenant defaults, `${value:}`, attributeconfig) then sees
the merged document with no change of its own. A resolution problem is a load finding, like a document that fails
its schema, so the cross-document checks do not report what follows from it. Every chain is resolved against the
documents as written before any is replaced, and the entry keeps the document as written (`IndexEntry.written`,
read through `as_written`): chain walks, `param_unused` and the `${value:}` source scan read that, so a token a base
holds is reported once, on the base. A document with nothing to resolve stays as it is; a `partial: true` one stays
as written so `extends` can find it.

**Deployments keep a separate resolved map** (`resolve_deployments()`), as before parameter sets: they are read
through `deployment_of()`, resolve against the resolved tenants, and the resolver can be asked for some deployments
only (`names=`), which `strata render` uses.

**Two passes.** The values come from the leaf and apply to every base, so a chain is collected going up (keyed by
name) and substituted and merged coming down. The merged-spec cache is keyed by (name, the sets the chain names), so
leaves naming the same sets share their bases' work.

### Merge tables

| Kind        | Field                                   | Merge with `extends`                                                       |
| ----------- | --------------------------------------- | -------------------------------------------------------------------------- |
| deployment  | nested blocks                           | deep merge per leaf key, child wins                                        |
| deployment  | `stages`, `providers`, `assets`         | by `step`, `slot`, `slot`; a child item deep-merges over the base's        |
| deployment  | `environments`                          | base's list, then the child's new entries                                  |
| environment | `properties`, `custom`                  | deep merge per leaf key, child wins                                        |
| environment | `variables`, `secrets`, `features`      | by `key`: a child entry **replaces the whole entry**; new keys appended     |
| tenant      | `display_name`, `onboarded`             | child wins                                                                 |
| tenant      | `geographies`                           | **replaced** by the child's list when it sets one, never joined            |
| tenant      | `environments`                          | base's list, then the child's new entries                                  |
| tenant      | `properties`, `custom`, `configuration` | deep merge per leaf key, child wins                                        |
| all         | `extends`, `partial`                    | consumed, not part of the result                                           |

An environment store is replaced, not merged field by field: a variable moved from `store: constant` to a Key
Vault store keeps no stale `value`. `geographies` is a data-residency limit: a child that writes `[us]` must not
also allow its base's `europe`. A tenant needs `display_name` unless it is `partial` or has `extends`; the merged
result is where it is checked.

## Templates are validated per instance

A document whose `spec` holds `${param:}` is a **template**, base or leaf. The loader validates its `meta`, its
`extends` and `partial`, and the syntax of its tokens (`DocumentTemplateModel`, with one subclass per kind, chosen
through `TEMPLATE_SERVICE_BY_KIND`); the rest of its spec is kept as written. It is validated once per document
that resolves through it, after substitution:

- A deployment template's substituted spec is validated as a deployment spec per deployable deployment. A finding
  is reported on the template's own field, with the text as written, the role, key, value and set that made it,
  and the deployment (`... (for spoke-initech-001)`). A reference the template writes literally is reported once.
- An environment or tenant template is validated as part of the merged document of each environment or tenant
  built on it.
- Checks that iterate every document skip a template that did not resolve, as they skip what a partial base lacks;
  a non-template partial base is still checked as the valid document it is.

## Bases cannot be used

A `partial: true` document exists only for `extends`:

- a deployment or a non-partial tenant that lists a partial environment is `environment_not_listable`;
- a deployment whose `tenant` names a partial tenant is `tenant_not_selectable`;
- `${value:environment.<base>...}` or `${value:tenant.<base>...}` is `value_reference_to_base`;
- `strata render` and every command that runs a deployment refuse a partial one.

The check runs where a list is in use (a resolved deployable deployment, a non-partial tenant), so a base that lists
a base is reported once per document built on it.

## Constraints: attributeconfig

Values are checked with attributeconfig, in its two views; no new vocabulary.

- **One set at a time**: `kind: parameterset, block: values, view: document`. A set does not know its role, so a
  selector uses its optional label (`meta.labels.role: spoke`); a selector path walks into a map by key.
- **What a document receives**: `block: params, view: effective`, for `kind: deployment`, `environment` or `tenant`.
  The schema sees the chain's sets merged per role, roles as top-level keys (`chain_params()`): "everything a base
  needs is there", which no single set can say. A deployment is matched when deployable, its selector evaluated on
  the resolved deployment; an environment or tenant when not partial. A document that names no set receives `{}`,
  so a schema is narrowed to the documents that use the base. Findings sit at `meta.params.<role>.<key>`.

A schema cannot check that a value names an existing document; the reference check on the resolved document does.

## Rendered view

`strata render deployment|environment|tenant NAME` prints the resolved document field by field: the value, the
document that wrote it, and for a value filled in from a parameter set the text as written and each read (role,
key, value, supplying set). `-o json` carries the same plus `params` and `spec`. It runs nothing and needs no valid
solution; it exits 3 for an unknown name, a partial base or a document that does not resolve, with that document's
own findings.

**The origin map is computed beside the merge** (`controllers/rendered_view.py`), not inside the merge functions.
Each layer is substituted as resolution does and dumped with only the fields its author wrote (a deployment
template's layer validated first, as resolution shapes it); every layer and the result are walked into leaf paths;
each leaf of the result gets the last layer that wrote it. Paths are normalized so no merge rule needs tracking: an
item of a named list by its name (`providers[slot=...]`, from the merges' own `LIST_MERGE_KEYS` and
`STORE_MERGE_KEYS`), a plain list item by its value. A leaf no layer wrote is a schema default. A deployment's tenant
defaults are laid out from the tenant's own chain, with the tenant's own sets, so a value the tenant inherited names
the document and set that wrote it.

**Secrets are always masked** (`********`, as `deploy report`), empty or not: every `value` under an environment's
`spec.secrets`, its text as written, and its reads. Since rule 5 forbids `${param:}` there, a secret never reaches
`params` either.

## Where it lives

| Module                                                     | Holds                                                                      |
| ---------------------------------------------------------- | -------------------------------------------------------------------------- |
| `models/parameterset_model.py`                             | the kind; the plain-values check                                           |
| `models/template_model.py`                                 | `ParamSetNames`, the `name_rule` check, `DocumentTemplateModel`            |
| `utils/param_tokens.py`                                    | merge per role, substitution, origins of substituted strings               |
| `controllers/document_chains.py`                           | chain walk, sets of a chain, `name_rule`, environment and tenant resolution |
| `controllers/deployment_resolution.py`                     | deployment resolution, per-template findings, `param_unused`               |
| `services/environment_service.py`, `services/tenant_service.py` | `merge_environment_specs()`, `merge_tenant_specs()`                   |
| `controllers/semantic_checks.py`, `controllers/value_references.py` | the base checks; the `params` view                                |
| `controllers/rendered_view.py`, `commands/render_command.py` | the origin map and `strata render`                                       |
| `models/schema_export.py`                                  | the exported schemas accept `${param:}` where `strata validate` does       |

Out of scope by decision: values implied by the folder; a set naming the sets it builds on; a default syntax in the
token; templated names.

## Related Decisions

- [ADR-0061](../decisions/0061-parameter-sets-and-load-time-param-token.md): parameter sets and the load-time
  `${param:}` token
- [ADR-0062](../decisions/0062-resolve-environments-and-tenants-into-the-index.md): environments and tenants are
  resolved when the solution loads and replace their documents in the index
- [ADR-0024](../decisions/0024-tenant-defaults-merge.md): tenant defaults, folded in after `extends`
- [cross-document-value-references.md](cross-document-value-references.md): `${value:}`, whose path walk `${param:}`
  reuses
- [dynamic-configuration-validation.md](dynamic-configuration-validation.md): attributeconfig, with the `values` and
  `params` blocks
- [schema-export.md](schema-export.md): how the exported schemas accept `${param:}`

## History

- **Measured case.** A consumer hub-and-spoke solution: seven documents per spoke, about 355 lines holding about ten
  real facts; tenants (7 copies) and tenant environments copied the same way. Most of the difference was identity
  built from (hub, spoke, tenant), then values following a naming rule, then real per-spoke data.
- **Rejected options.** A generic base bound in `extends` (`base<hub: hub01>`): `extends` stops being a plain name
  and every tool needs a parser. Layer segments as parameters: references resolve at load, before run-time tokens,
  and environments have no `layers`. A generator script: the copies stay and drift; it remains the stopgap until a
  solution migrates. An expansion kind (one table, N deployments): deployments stop being files, breaking audit
  paths, `deploy list` and the folder check. Overlays: `extends` already is that for `spec`.
- **Version 1 declared typed inputs on each base** ([archived](../archive/base-parameters.md)), bound per level with
  per-document scope and forwarding. It worked but needed three fields and eleven rules; parameter sets need one
  kind, one field and one token. Up-front constraints moved to attributeconfig, defaults to a base's own sets.
- **The kind is `parameterset`, not `values`**: strata already uses "value" for run-time values (`${value:}`, value
  tokens, Helm values), and the design depends on keeping load time and run time apart. Not `parameterconfig`:
  `*config` kinds are rules about other documents.
- **Roles moved from inside the set into `params`.** A first version kept each set's values under one top-level role
  key by convention, enforced by attributeconfig; one set writing another role's key overrode it silently. With the
  role given where a set is named, collisions cannot happen and no convention is needed.
- **Names are never templated**; `meta.name_rule` checks them instead, so every tool that finds a document by name
  keeps working.
- **`${value:parameterset...}` stays allowed**: it reads one set raw, by name, from any document, which suits
  solution-wide constants. Nothing is silently wrong with it; `param_unused` counts its reads.
- **Replace in the index, not a resolved map per kind**, for environments and tenants (ADR-0062). The cost, findings
  on a merged document naming the leaf's file, was accepted; keeping the document as written beside the resolved one
  turned out necessary for chain walks and `param_unused`.
- **The effective `params` view covers all three kinds at once**: once environments and tenants resolve like
  deployments there was nothing to wait for.
- **A new command for the rendered view**, not an option: `deploy list` is deployments only, one row each; `path
  get` is about files; `graph` about references. The origin map is computed beside the merge so the three merge
  functions stay as they are; normalized paths make it independent of their list rules.
- **Review fixes to the rendered view**: a `${param:}` read into a secret leaked through the reads and `params`, so
  rule 5 now forbids `${param:}` in a secret's value outright; layers became JSON-shaped and written-fields-only, so
  dates no longer break `-o json` and defaults are not credited to a document; tenant defaults are laid out from the
  tenant's own chain; rendering resolves only the requested deployment.
