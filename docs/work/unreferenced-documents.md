# Unreferenced Documents - Work

- Status: in-progress - first version built (information only); scope questions open
- Last updated: 2026-11-14

## Overview

A document no deployment reaches is a leftover, a mistyped name somewhere else, or staged ahead of its first use.
Until now nothing said so: a module whose namespace reference was misspelled simply never deployed. Raised as open
question 5 of [version-composition.md](version-composition.md), which already reports the version-specific case
(`version_document_unlisted`).

## Current Design / Progress

`strata validate` reports `document_unreferenced`, **information**, for a document no deployment reaches, with a
hint. Information, not a warning: a document staged for a deployment still to come is legitimate, and `--strict`
must not fail on it. Built in `controllers/document_reachability.py`, run from `run_semantic_checks()`.

### Reached

From every deployable deployment (no partial base, a workspace, resolved through `extends`), following:

- every `References` field: the edges `build_reference_graph()` builds and `strata graph` draws;
- the resolved deployment's own fields, because a deployment template's references exist only once its values are
  substituted;
- every `${value:kind.name...}` token in a reached document, which reads another document by name.

### Judged

| Judged                                                                                              | Not judged, and why                                                                                 |
| --------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| `workspace`, `provider`, `resource`, `dns`, `network`, `firewall`, `namespace`, `module`, `tenant`, `environment`, `asset` | `configuration`, `providerconfig`, `topologyconfig`, `attributeconfig`, `integration`: bound by type, not by name |
|                                                                                                     | `version`, `artifact`, `parameterset`: a check of their own (`version_document_unlisted`, `artifact_unused`, an unread key per set) |
|                                                                                                     | `deployment`: every deployment is a starting point                                                  |
|                                                                                                     | a `partial: true` base: it exists for `extends`, and the index holds what is built on it             |

A solution with no deployable deployment (a library of documents) is not judged at all.

### Partial bases nothing extends

A `partial: true` deployment, environment or tenant that no document of its kind `extends` is reported as
information, `base_unused`, with a hint. A base exists only to be extended, so an unused one is a leftover or a leaf
naming another base by mistake. Read from the documents as written (`IndexEntry.as_written`): the index holds the
resolved environments and tenants, which no longer carry `extends`. In a chain, only the unused end is reported:
a base extended by another base counts as extended.

### Listing them: `strata graph --unreferenced`

Every cleanup candidate as one list, one line per document with the reason and the file; JSON
`data.unreferenced[]` has `kind`, `name`, `path` and `reason`:

| `reason`                | Console                                                     | The `strata validate` finding                         |
| ----------------------- | ----------------------------------------------------------- | ----------------------------------------------------- |
| `unreferenced`          | reached by no deployment                                    | `document_unreferenced`                               |
| `base_unused`           | partial base nothing extends                                | `base_unused`                                         |
| `version_unlisted`      | listed by no deployment (fine if CI uses it through --pin)  | `version_document_unlisted`, `promotion_orphan_ring`  |
| `artifact_unreferenced` | named by no document                                        | `artifact_unused` (the warning; a staged artifact is left out) |

`cleanup_candidates()` feeds the first three from the index, sharing the functions the findings use
(`unreferenced_documents()`, `unused_bases()`, `version_composition.unlisted_versions()`), so the command and
`strata validate` cannot disagree. Artifacts need the solution context (their use is read through the deployments'
effective images), so the command adds them with `artifact_usage.unreferenced_artifacts()`, and only when the
solution loaded cleanly. Otherwise, like the rest of `strata graph`, it does not require a valid solution: it
resolves deployment chains only.

### Example

The repository's `config/` example had one: `dns/example.yaml` (`example-dns`) was in no workspace's
`dns_zones`. The example workspace now lists it, with a comment saying why.

## Related Decisions

- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) - documents are found by kind and name,
  so a name nothing uses is invisible without this check.

## Remaining Work / Open Questions

1. **Warning or information.** Information today. A team that wants unused documents to fail CI would need a
   severity setting (per kind, in `configuration`?). Wait for someone to ask.
2. **References strata does not model by name.** A document read only by a tool (a Terraform root reading a file
   by path) is reported. The hint says so; an opt-out (an annotation such as `strata.unreferenced: ok`) is open.
3. ~~**Partial bases.**~~ Built: `base_unused`.
4. ~~**`strata graph --unreferenced`.**~~ Built.
5. ~~**More in the cleanup list.**~~ Built: unlisted version documents (`version_unlisted`) and artifacts no
   document names (`artifact_unreferenced`). A staged artifact stays out: it is named, just not deployed yet.

## Changelog

- 2026-11-14: `strata graph --unreferenced` also lists version documents no deployment lists and artifacts no
  document names (open question 5).
- 2026-11-14: `base_unused` (partial bases nothing extends) and `strata graph --unreferenced` built; the
  `config/` example's `example-dns` wired into its workspace.

- 2026-11-14: Created and built: `document_unreferenced` (information, with a hint) for eleven kinds, reached from
  deployable deployments through `References` fields, resolved deployments and `${value:}` tokens.
