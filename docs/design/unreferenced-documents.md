# Unreferenced Documents — Design

- Status: current
- Last updated: 2026-11-20

## Overview

A document no deployment reaches is a leftover, a mistyped name somewhere else, or staged ahead of its first use.
Documents are found by kind and name ([ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md)), so
without a check such a document is invisible: a module whose namespace reference was misspelled simply never
deploys.

`strata validate` reports such documents as **information**, with a hint, and `strata graph --unreferenced` lists
every cleanup candidate in one place. Information, not a warning: a document staged for a deployment still to come is
legitimate, and `--strict` must not fail on it. The code is
[document_reachability.py](../../src/strata/controllers/document_reachability.py), run from `run_semantic_checks()`.

## Reached

From every deployable deployment (not a partial base, has a workspace, resolved through `extends`), following:

- every `References` field: the edges `build_reference_graph()` builds and `strata graph` draws;
- the resolved deployment's own fields, because a deployment template's references exist only once its values are
  substituted;
- every `${value:kind.name...}` token in a reached document, which reads another document by name.

## `document_unreferenced`

| Judged                                                                                                                    | Not judged, and why                                                                                                     |
| ------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `workspace`, `provider`, `resource`, `dns`, `network`, `firewall`, `namespace`, `module`, `tenant`, `environment`, `asset` | `configuration`, `providerconfig`, `topologyconfig`, `attributeconfig`, `integration`: bound by type, not by name        |
|                                                                                                                           | `version`, `artifact`, `parameterset`: a check of their own (`version_document_unlisted`, `artifact_unused`, an unread key per set) |
|                                                                                                                           | `deployment`: every deployment is a starting point                                                                      |
|                                                                                                                           | a `partial: true` base: it exists for `extends`, and the index holds what is built on it                                |

A solution with no deployable deployment (a library of documents) is not judged at all.

## `base_unused`

A `partial: true` deployment, environment or tenant that no document of its kind `extends` is reported as
information, with a hint: a base exists only to be extended, so an unused one is a leftover or a leaf naming another
base by mistake. It is read from the documents as written (`IndexEntry.as_written`), because the index holds the
resolved environments and tenants, which no longer carry `extends`. In a chain, only the unused end is reported: a
base extended by another base counts as extended.

## `strata graph --unreferenced`

Every cleanup candidate as one list, one line per document with the reason and the file. JSON
`data.unreferenced[]` has `kind`, `name`, `path` and `reason`:

| `reason`                | Console                                                    | The `strata validate` finding                                   |
| ----------------------- | ---------------------------------------------------------- | --------------------------------------------------------------- |
| `unreferenced`          | reached by no deployment                                   | `document_unreferenced`                                         |
| `base_unused`           | partial base nothing extends                               | `base_unused`                                                   |
| `version_unlisted`      | listed by no deployment (fine if CI uses it through --pin) | `version_document_unlisted`, `promotion_orphan_ring`            |
| `artifact_unreferenced` | named by no document                                       | `artifact_unused` (the warning; a staged artifact is left out)  |

`cleanup_candidates()` feeds the first three from the index, sharing the functions the findings use
(`unreferenced_documents()`, `unused_bases()`, `version_composition.unlisted_versions()`), so the command and
`strata validate` cannot disagree. Artifacts need the solution context, since their use is read through the
deployments' effective images, so the command adds them with `artifact_usage.unreferenced_artifacts()`, only when
the solution loaded cleanly. Otherwise, like the rest of `strata graph`, it does not require a valid solution: it
resolves deployment chains only.

## Not covered

- **A severity setting** (for example per kind in `configuration`), for a team that wants unused documents to fail
  CI. Nobody has asked; information is right for the common case of staged documents.
- **An opt-out for a document strata cannot see used**, such as a file a Terraform root reads by path. It is
  reported, and the hint says this case exists; an annotation (`strata.unreferenced: ok`, say) waits for a real case.

## Related Decisions

- [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) — documents are found by kind and name,
  so a name nothing uses is invisible without this check.
- [version-composition.md](version-composition.md) — `version_document_unlisted`, the version-specific case this
  check grew out of (its open question 5).

## History

- Built in one go after version composition asked the general question: `document_unreferenced` for eleven kinds,
  then `base_unused`, then `strata graph --unreferenced`, which later also listed unlisted version documents and
  artifacts no document names.
- The repository's own `config/` example had one: `dns/example.yaml` (`example-dns`) was in no workspace's
  `dns_zones`; the example workspace now lists it, with a comment saying why.
- The two open questions both wait for a user to ask, so they moved to "Not covered" and the doc to design.
