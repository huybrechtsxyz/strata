# Pin Granularity - Work

- Status: blocked - waiting on whether a real solution needs either
- Last updated: 2026-11-14

## Overview

Version composition ([../design/version-composition.md](../design/version-composition.md)) is built: a deployment
lists several version documents, every pin has one owning document. Two questions about how *finely* something can
be pinned were left open on purpose, because neither has a real case yet. They are tracked here so the finished
design doc carries no open work.

## Current Design / Progress

Nothing is designed yet. What each question is, and the options seen so far:

### 1. A separate infrastructure version per product root

When every product's root module is fetched from one remote (`iac`), a shared document owns that remote's pin, so
all products' infrastructure code moves together in a ring while their images and artifacts move per product.
Products that must move their infrastructure code on their own need a pinnable target per product root:

- **A remote per product root.** The same URL declared several times in `strata.yaml` (`iac-datahub`,
  `iac-staging`), each pinned by its product's document. Works today, against "configure once".
- **A pin keyed by remote and path.** `remotes: { iac/datahub: datahub/v1.4 }`, with product-scoped tags in the
  repository. New pin shape and resolution; cleaner if several products need it.

Asked of the reporter (config-repo) with the 2.7.0 release: does each product need its own `iac` version?

### 2. Overriding one pin of a listed document

A customer that differs from its product on one pin lists a different document for the part that differs. When
the product document is large, that means splitting it or copying it. An override (a deployment, or a customer
document, changing one listed pin with a required `reason`) was part of the first composition design and dropped
in favour of one owner per pin. Revisit if a real customer case makes splitting documents painful.

## Related Decisions

- [ADR-0063](../decisions/0063-a-deployment-lists-several-version-documents.md) - one owning document per pin, which
  either question would have to keep or deliberately relax.

## Remaining Work / Open Questions

1. Answer from config-repo on per-product infrastructure versions; design the chosen option if yes.
2. A real case for single-pin overrides; until then, a different listed document is the answer.

## Changelog

- 2026-11-14: Created from the two open questions of the version composition work doc when that doc moved to
  `docs/design/`.
