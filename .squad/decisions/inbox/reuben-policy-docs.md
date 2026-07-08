# Reuben — Policy Docs Notes
**Date:** 2026-06-15  
**Author:** Reuben (Docs)

## Observations

### Phase 2 feature documentation strategy
Documenting Phase 2 features (`required_tags`, `naming_pattern`, `script`) before they are implemented creates a risk: the YAML interface shown may drift from what is eventually shipped. Recommendation: pin the Phase 2 YAML examples to a doc version tag or add a `<!-- TODO: update when Phase 2 lands -->` comment so the author who implements Phase 2 knows which doc sections to revisit.

### `customer_zone` needs a zones model doc
The `customer_zone` policy references `configuration.spec.zones` (zone-to-region mapping) but that field is not yet documented in `docs/config/configuration.md`. The policy doc notes the dependency but cannot fully explain the zone model until `configuration.md` is updated. A follow-up task: document `spec.zones` and `spec.customers` in `configuration.md`.

### `audit` enforcement and the deployment manifest
The `audit` enforcement level records results in the deployment manifest (`spec.policy_results`). That manifest schema field (`policy_results`) is not yet implemented (Phase 3 per ADR 0006). The policies.md doc describes the behavior correctly, but `docs/config/manifest.md` will need a new section when Phase 3 ships.

### Index placement decision
`platform/policies` was placed after `platform/lifecycles` in the Internals toctree. Rationale: a reader encountering policies naturally wants to understand how they differ from lifecycle hooks — placing policies immediately after lifecycles creates a logical reading order. Validators comes before both because it is lower-level infrastructure that policies build on.
