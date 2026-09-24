# v1 Schema Audit — Resolution Tracking

- Status: current
- Last updated: 2026-09-24

## Overview

[ADR-0001](../decisions/0001-v1-schema-analysis-findings-for-v2.md) audited
v1's kind schemas and recorded 6 discrepancies and 8 architectural issues as
a raw findings report — no decisions were made in that ADR itself. Every item
below has since been resolved, deliberately deferred, or left open by a
follow-up ADR. This doc is the live tracker so ADR-0001 doesn't need to be
edited every time another item resolves — update the table here instead.

## Discrepancies

| # | Finding | Resolution | Status |
| --- | --- | --- | --- |
| 1 | Firewall has no `spec.references` — can't parametrize rules per environment | Not fixed by adding `references` (that concept was rejected globally, [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)). Fixed instead via Value-token bindings on `from`/`to` — [ADR-0008](../decisions/0008-firewall-model-design-decisions.md) | Resolved |
| 2 | DNS `output_key` (stage-output-sourced values) is DNS-only, breaks the reference pattern | Generalized concept identified as **Context** ([ADR-0006](../decisions/0006-context-shared-stage-runtime-store.md)); removed from `DnsRecordModel` for now ([ADR-0005](../decisions/0005-dns-model-design-decisions.md)) pending the Context object and `${step:}` token | Design decided, implementation pending |
| 3 | DNS (flat) vs Network (nested `CidrSourceModel`) structural inconsistency | Unified on Value tokens ([ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)); `CidrSourceModel` removed, Network now flat like DNS ([ADR-0007](../decisions/0007-network-model-design-decisions.md)) | Resolved |
| 4 | Network peerings (cross-network references) — unique to Network | Not revisited since the audit — verify whether `peerings` was actually ported when reviewing the Network model next | Unverified |
| 5 | Resource `subcategory` field — second categorization level, unique to Resource | Explicitly left as-is; revisit once a second kind needs subcategory-like classification ([ADR-0004](../decisions/0004-resource-model-design-decisions.md)) | Open, deliberately deferred |
| 6 | DNS TTL/priority fields — unique to DNS | Not a cross-kind problem — kept as DNS-specific fields, no action needed | Non-issue, closed |

## Architectural Issues

| # | Issue | Resolution | Status |
| --- | --- | --- | --- |
| 1 | Module is vastly more complex than every other kind | Ported faithfully, complexity treated as inherent to real workload-deployment needs rather than something to reduce ([ADR-0009](../decisions/0009-module-model-design-decisions.md)) | Accepted as-is |
| 2 | Workspace mixes infrastructure and application concerns | Split into three concepts — Topology (grouping), Provisioner/ProvisioningStep (execution recipe), Workspace (the composed "image") — [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md), [ADR-0012](../decisions/0012-workspace-model-design-decisions.md) | Resolved |
| 3 | Terraform-specific patterns dominate; other IaC tools under-modeled | Generalized via `ProvisionerType`/`Provisioner` covering 8 tool types ([ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md)) and the Integration layer ([ADR-0021](../decisions/0021-integration-layer.md), Terraform + Compose + Helm) | Resolved |
| 4 | Firewall lacks parametrization (ports, CIDRs, priority) | Same fix as Discrepancy 1 — Value tokens on `from`/`to` ([ADR-0008](../decisions/0008-firewall-model-design-decisions.md)). `port` and rule priority remain unparametrized — no cited real need | Partially resolved |
| 5 | Lifecycle hierarchy ambiguity (workspace/namespace/module precedence undocumented) | Not yet addressed by any ADR | Open |
| 6 | No cross-kind validation — everything is late-binding by name string | Addressed as a pattern, not a single fix: the two-phase validation convention (Phase 1 schema, Phase 2 cross-document) threaded through every kind, `kind` field validation ([ADR-0016](../decisions/0016-kind-field-validation.md)), Topology/Resource Phase 2 cross-checks ([ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md), [ADR-0012](../decisions/0012-workspace-model-design-decisions.md)), and the planned `Environment` cross-check for Value tokens ([ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)) | Mostly resolved as a pattern; Environment cross-check still pending that kind's build |
| 7 | Source path inconsistency (`@repo_name/path` vs. workspace-relative-only) | `SourceModel` unified onto one `remote` field ([ADR-0018](../decisions/0018-source-model-unified-remote-reference.md)), but the `SourceModel.repository` vs. `@reponame/path` convention split itself is still unresolved per ADR-0012's own remaining work | Partially resolved |
| 8 | References don't flow down — no wiring between declared references and actual environment values | Resolved conceptually via Interface/Injection/Grant ([ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md)); implementation waits on the provisioner/build layer | Design decided, implementation pending |

## Related Decisions

- [ADR-0001](../decisions/0001-v1-schema-analysis-findings-for-v2.md) — the original audit this doc tracks resolution for
- [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) through [ADR-0021](../decisions/0021-integration-layer.md) — individual resolutions, linked per row above

## Remaining Work / Open Questions

- Verify Network peerings status (Discrepancy 4).
- Lifecycle hierarchy precedence (Architectural Issue 5) — no ADR yet.
- Resolve the `SourceModel.repository` vs. `@reponame/path` convention split (Architectural Issue 7).
- Wire the `Environment` cross-check for Value tokens once the `environment` kind is built (Architectural Issue 6, Discrepancy 2/Issue 8's Context/Injection implementation).

## Changelog

- 2026-09-24: Created, backfilling resolution status for all 14 ADR-0001 findings against ADRs 0002–0021.
