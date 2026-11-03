# Deployment token paths - Work

- Status: draft
- Last updated: 2026-11-03

## Overview

`${deployment:name}` and `${deployment:tenant}` exist and are finished ([deployment-tokens.md](../design/deployment-tokens.md)).
They cover the case that motivated them: one rule for a Terraform state key. This doc holds the follow-up that was
deliberately left out: a general `${deployment:PATH}`, a dotted path into the resolved deployment, so a workspace
could also read a value such as `spec.layers.segments.control` or `spec.properties.state_prefix`.

Nothing here is planned. The design doc records "can be added later without changing the two keys"; this doc keeps
the sketch and the conditions under which it is worth building, so the decision is not re-argued from scratch.

## Current state

Verified against the source on 2026-11-03.

- Resolution reads one flat `key -> value` map and ignores the token kind. The deployment entries are stored as
  `deployment:name` and `deployment:tenant`; a colon cannot occur in any other token's key.
- `value_references.py` already has the pieces a path token needs: `_walk_value_reference_path()` (dotted path, list
  index or `name` match) and the scalar rule (`value_reference_not_scalar`) used by `${value:kind.name.path}`.
- What a deployment can supply, and how each source behaves:

| Source                           | Inherited through `extends` and the tenant?              | Note                                                           |
| -------------------------------- | -------------------------------------------------------- | -------------------------------------------------------------- |
| `meta.name`                      | n/a                                                      | already `${deployment:name}`                                   |
| `spec.tenant`                    | yes (a base can set it)                                  | already `${deployment:tenant}`                                 |
| `spec.workspace`, `spec.version` | yes                                                      | `version` changes on promotion: unsuitable in a state key      |
| `spec.layers.segments.<name>`    | per deployment; checked against the document's directory | strings; the one source that names a place in a hierarchy      |
| `spec.properties.<key>`          | yes: merged over the tenant, environments and `extends`  | any type; a shared default with a per-deployment override      |
| `spec.custom.<key>`              | yes: same chain                                          | same, not validated by `attributeconfig` unless one targets it |
| `meta.labels.<key>`              | no: `meta` is not merged                                 | costs the per-deployment typing the tokens exist to avoid      |

## Sketch

- **Syntax.** `${deployment:PATH}`, with `name` and `tenant` kept as they are (shorthand for `meta.name` and
  `spec.tenant`). `PATH` is dotted from the deployment's root, for example `spec.layers.segments.control`. The
  existing key regex already allows dots.
- **Which view.** The resolved deployment (after `extends` and tenant defaults), as `deploy run` and `validate`
  already use. `meta` is the deployment's own.
- **Value rule.** As for `${value:}`: the path must end at a plain scalar. A missing path, a dict or a list is an
  error. A scalar that itself contains a token is not expanded, so a value read this way can never smuggle in a
  `${secret:}`.
- **Map key.** `deployment:<path>`, so no other kind can collide. The difference from today is that the entries are
  no longer two known keys: they depend on which paths the documents use. Either scan the strings a step resolves
  and add one entry per referenced path, or hand the resolvers a mapping that computes `deployment:` keys on demand.
  The resolvers take a plain `dict[str, str]` today, so the scan is the smaller change.
- **Validation.** Per deployment, in the same walk as the two fixed keys: unknown path and non-scalar are errors
  that name the path and the deployment. Same skips as today: a `partial: true` base, and a deployment whose
  `extends` chain could not be merged.
- **Docs and schema.** The token table in the agent skill (a drift test requires a row per kind, not per path), the
  workspace and deployment config pages, and the exported description of `backend.configuration`.

## Why not now

- No real need beyond `name` and `tenant` has been seen. The first request (a unique state key per deployment) is
  fully met by `name`.
- The whole deployment schema becomes a token surface, including fields strata does not interpret yet
  (`locking`, `lifecycle`, `configurations`). A workspace reading `spec.layers.segments.control` breaks if that field
  is renamed, so the schema's stability would start to matter for documents that only read it.
- `spec.properties` and `spec.custom` already reach Terraform as `strata.properties` and `strata.custom`. A
  Terraform root can read them with no token, so a path token mostly matters for a place that Terraform variables
  cannot reach, namely a backend block.

## Related Decisions

- [deployment-tokens.md](../design/deployment-tokens.md): the two fixed keys and why a general path was left out
- [cross-document-value-references.md](../design/cross-document-value-references.md): `${value:kind.name.path}`, the
  path walk and the scalar rule this would reuse

## Remaining Work / Open Questions

- [ ] Wait for evidence: a real workspace that needs a backend value other than the deployment's name or tenant (a
      folder per hierarchy segment, or a shared prefix from `properties`).
- [ ] If it happens, decide the map-key strategy (scan the strings versus a computing mapping) before building.
- [ ] Decide whether a path may read `spec.properties` and `spec.custom`, or only fields strata itself validates
      (`meta.name`, `spec.tenant`, `spec.layers.segments`).
- [ ] Decide whether the two shorthands stay as aliases or become plain paths.

## Changelog

- 2026-11-03: Created to hold the deferred general path, so the deployment tokens design doc does not carry an open
  item. The sketch and the reasons for waiting come from that doc's History and a check of the existing path walk.
