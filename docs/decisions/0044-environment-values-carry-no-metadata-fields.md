# Environment values carry no metadata fields beyond requirements

- Status: accepted
- Date: 2026-10-24
- Related: [docs/design/dynamic-configuration-validation.md](../design/dynamic-configuration-validation.md) (how
  requirements are checked), [ADR-0040](0040-attributeconfig-standalone-kind.md) (`attributeconfig`),
  [ADR-0035](0035-unified-terraform-projection.md) (one `strata` object), [ADR-0019](0019-version-pinning.md)
  (version pins with `status`/`reason`/`reviewed`), [ADR-0045](0045-facts-between-layers-are-terraform-records.md)
  (facts between layers), [docs/archive/environment-value-metadata.md](../archive/environment-value-metadata.md)
  (full investigation and evidence)

## Context and Problem Statement

A real consumer's environment files (cfg-int-deployment, a 779-line leaf among 12 files) carried four kinds of
knowledge about values only as YAML comments:

1. **Requirements**: a value is only valid together with another one (`enable_aks` on needs `aks_cluster_id`). A
   blank value of this kind silently disabled private endpoints.
2. **Alias / copy-of**: one value repeated under a second key, with "keep the two values identical".
3. **Provenance**: where a value came from, when it was checked, whether it is final.
4. **Naming budgets**: a name composed from several keys must fit a length limit.

Should `kind: environment` grow metadata fields (an alias source, a provenance block, a length budget) so tools can
read what the comments say?

## Considered Options

- **A metadata block per variable** (alias source, origin/checked/status, budget), validated by strata.
- **Requirements only**, through `attributeconfig` over the merged deployment, and leave the other three where they
  already have a better owner.

## Decision Outcome

Chosen: **requirements only**, because each of the other three turned out to be owned elsewhere once checked against
the real repositories, not the v1-shaped files that produced the comments.

- **Requirements** are rules about a key, so they belong where rules are pushed from: `attributeconfig` with
  `view: effective` over `variables`/`features`/`properties`/`custom` and `block: composite` for cross-block rules.
  This is the one part that needed strata (built; see the design doc).
- **Alias**: all four real duplicates were one value needed under the names different Terraform roots declare, a
  side effect of v1 delivering every key as a same-named flat variable. Under ADR-0035 a root reads
  `local.strata.variables.<key>` and renames in its own `locals`. A merge strategy does not apply: it picks a value
  for one key, it does not rename.
- **Provenance**: in the v3-shaped config the dated notes are change history (git), subscription onboarding facts
  (rendered by the consumer's generator, which records each value's source), or a layer's outputs (which leave the
  config; ADR-0045). The only genuinely pending values were image tags, which `kind: version` already structures
  (ADR-0019).
- **Naming budgets**: Terraform composes the names and Azure/Terraform reject an over-length one at plan. A strata
  check would need its own copy of the composition, a second rule set that can drift from the first.

### Consequences

- Good: `kind: environment` keeps one shape; nothing about a value is stored twice (once in strata, once where it is
  actually produced or enforced).
- Good: the converted leaf shrank from 779 to 338 lines, 465 to 51 comment lines; the three recorded requirements are
  now checks.
- Bad: a reader still learns a placeholder's status from a comment, and an over-length name is still found at plan
  rather than on the pull request.
- Reopen if a defect is traced to a stale or unsourced value, a duplicate turns up that two keys must equal by policy
  rather than by naming, or an over-length name gets past plan.
