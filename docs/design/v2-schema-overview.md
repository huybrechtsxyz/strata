# v2 Schema — Kind Overview

- Status: current
- Last updated: 2026-09-24

## Overview

One-page catalog of every v2 kind (and load-bearing cross-cutting
sub-model/convention), its purpose, and its current implementation status —
so "what does v2 look like today" doesn't require reading all 23 ADRs.
Detailed rationale for each stays in its own ADR; this doc only tracks
current state and links out. Update this table when a kind is added or its
status changes.

## Kinds

| Kind | Purpose | Status | ADR(s) |
| --- | --- | --- | --- |
| `configuration` | Platform-wide policy/type registries (providers, topologies today) | Implemented — minimal slice, extended incrementally | [ADR-0003](../decisions/0003-provider-model-design-decisions.md), [ADR-0013](../decisions/0013-configuration-topology-registry.md), [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md) |
| `provider` | A cloud provider instance (credentials, region, properties) | Implemented | [ADR-0003](../decisions/0003-provider-model-design-decisions.md) |
| `providerconfig` | Registry: valid regions/resource types per provider type | Implemented | [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md) |
| `resource` | A provisioned cloud resource (VM, storage, etc.) | Partially implemented — `subcategory` field placement still open | [ADR-0004](../decisions/0004-resource-model-design-decisions.md) |
| `dns` | DNS zones and records | Implemented | [ADR-0005](../decisions/0005-dns-model-design-decisions.md) |
| `network` | VPCs/VNets, subnets, peerings | Partially implemented — Environment cross-check deferred; peerings porting unverified | [ADR-0007](../decisions/0007-network-model-design-decisions.md) |
| `firewall` | Security rules | Partially implemented — Environment cross-check deferred, multi-file merge not ported | [ADR-0008](../decisions/0008-firewall-model-design-decisions.md) |
| `module` | A deployable workload (containers, env vars, mounts, health checks) | Partially implemented — Environment cross-check deferred | [ADR-0009](../decisions/0009-module-model-design-decisions.md) |
| `namespace` | A named collection of modules | Partially implemented — file-existence check and cross-layer overlap lint deferred | [ADR-0010](../decisions/0010-namespace-model-design-decisions.md) |
| `topology` | Named grouping of resources/namespaces ("what belongs together") | Partially implemented — internal reference/component checks now wired ([solution-loading-and-phase2-validation.md](solution-loading-and-phase2-validation.md)); `TopologyService._validate_dynamic()` itself still not built | [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) |
| `topologyconfig` | Registry: expected component roles per topology type | Implemented | [ADR-0013](../decisions/0013-configuration-topology-registry.md), [ADR-0014](../decisions/0014-provider-topology-config-standalone-kinds.md) |
| `workspace` | The composed "image" — providers/resources/topologies/provisioning recipe | Partially implemented — Topology Phase 2 cross-checks now wired; Network/subnet cross-check still not built; `Deployment` execution engine not started | [ADR-0012](../decisions/0012-workspace-model-design-decisions.md) |
| `solution` | Solution manifest (`strata.yaml`) — identity, remotes, discovery root | Implemented — discovery loader (`solution_controller.py`/`solution_context.py`) built; remote materialisation not built (see [remotes.md](remotes.md)) | [ADR-0015](../decisions/0015-solution-manifest-and-document-discovery.md) |
| `version` | Pinned tool/image/chart versions with rationale | Partially implemented — existence/applicability checked by `strata validate` ([validate-command.md](validate-command.md)); pin overlay onto a rendered artifact not wired (no build layer to apply it yet) | [ADR-0019](../decisions/0019-version-pinning.md) |
| `integration` | A connection to an external tool (Terraform, Helm, Infisical, ...) | Implemented — Phases 1-6 (Terraform, Compose, Helm) | [ADR-0021](../decisions/0021-integration-layer.md), [integration-layer.md](integration-layer.md) |
| `tenant` | Customer/organisation identity — geographies, defaults inherited by every deployment | Implemented — no dedicated ADR (built directly from a real-usage census; see [ADR-0024](../decisions/0024-tenant-defaults-merge.md) for the one follow-up decision) | [ADR-0024](../decisions/0024-tenant-defaults-merge.md) |
| `deployment` | The "container instance": Workspace + Environment + Tenant, `extends` chains, stages | Implemented (schema + `extends`/tenant-defaults resolution, [validate-command.md](validate-command.md)); no dedicated ADR; stage *execution* not started | [ADR-0024](../decisions/0024-tenant-defaults-merge.md) |
| `environment` | Real declared variables/secrets/features that Value tokens resolve against | Implemented — deliberately a small slice of v1 (variables/secrets/features/properties only; `overrides`/`lifecycle`/`promotion` excluded, zero real usage found) — no dedicated ADR | Referenced throughout as a blocker since [ADR-0002](../decisions/0002-requirement-interface-injection-grant-lessons-from-v1.md) |

## Cross-cutting sub-models and conventions

| Concept | Purpose | Status | ADR(s) |
| --- | --- | --- | --- |
| `SourceModel` | Unified git/OCI/chart remote reference | Implemented (schema/selection only — no resolver, see [remotes.md](remotes.md)) | [ADR-0018](../decisions/0018-source-model-unified-remote-reference.md) |
| `ProvisionerModel`/`ProvisioningStepModel` | Workspace's provisioning recipe (tool + execution graph) | Implemented (schema); execution engine not built | [ADR-0011](../decisions/0011-topology-and-provisioning-decoupling.md) |
| Value tokens (`${var:}`/`${secret:}`/`${feature:}`) | Embedded value-binding syntax | Partially implemented | [value-token-resolution.md](value-token-resolution.md) |
| Interface/Injection/Grant/Translation/Context | Provisioner input-resolution model | Designed, not implemented | [provisioning-injection-model.md](provisioning-injection-model.md) |
| Tags/labels/`configuration`/`custom` placement | Which kind gets cloud tags vs. k8s labels vs. passthrough config | Partially implemented | [ADR-0017](../decisions/0017-tags-labels-configuration-custom-placement.md) |
| `kind` field validation | Every root model rejects a mismatched `kind:` | Implemented | [ADR-0016](../decisions/0016-kind-field-validation.md) |
| Build pipeline (Integration → build run → rendering) | Renders workspace/module documents into Terraform/Compose/Helm artifacts | Mixed — see dedicated status doc | [build-pipeline-status.md](build-pipeline-status.md), [build-command.md](build-command.md) |
| Solution-wide document loading & Phase 2 validation | The loader every kind's deferred cross-document check waits on | Loader implemented; 4 of 7 validators wired, 3 still open | [solution-loading-and-phase2-validation.md](solution-loading-and-phase2-validation.md) |
| `strata validate` command | Schema + cross-document validation entry point | Implemented | [validate-command.md](validate-command.md) |
| `strata build run` command | Renders artifacts, does not execute | Not implemented (building blocks exist) | [build-command.md](build-command.md) |
| v1 schema audit resolution | Tracks resolution of ADR-0001's 14 original findings | See tracker | [v1-schema-parity-tracking.md](v1-schema-parity-tracking.md) |

## Related Decisions

All 23 ADRs in `docs/decisions/` — see the tables above for which applies to
which kind/concept.

## Remaining Work / Open Questions

- `environment`/`deployment`/`tenant` were implemented without a dedicated
  numbered ADR (only ADR-0024, a narrow follow-up decision, exists) —
  consider writing one retroactively per the repo's own ADR convention if
  their design is revisited.
- Deployment *stage execution* (`gates`/`promotion`, the actual `build`/
  `deploy run` commands) is the remaining structural gap now — not the
  kinds themselves, which are built.
- Keep this table in sync when a new kind is added or a status changes —
  it's the fastest place to look before opening an ADR.

## Changelog

- 2026-09-24: Created.
