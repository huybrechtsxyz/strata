# Strata Glossary

Core concepts and terminology used in strata v2 documentation, configuration schemas, and code.
Only implemented concepts are listed here. New concepts are added as they're implemented — see
[docs/design/v2-schema-overview.md](design/v2-schema-overview.md) for the authoritative,
up-to-date status of every kind.

---

## Document Basics

### apiVersion

The strata schema version. Currently `strata.huybrechts.xyz/v2` for every document.
Required on every strata document.

### kind

The document type — see [Kinds](#kinds) below for the full list. Every root model rejects a
mismatched `kind:` (ADR-0016).

### meta

The metadata block on every strata document: `name` (the document's identity, unique within its
kind), plus optional `annotations`, `labels`, and `tags`. Required on every document.

### spec

The specification block containing the document's actual content. Schema varies by `kind`.
Models use Pydantic v2 in strict mode (`extra="forbid"`) — unknown fields are a validation error.

### Solution

The root of a strata document tree: one `strata.yaml` file (`kind: solution`) that names the
solution, points at its `configuration`, declares `remotes`, and optionally excludes paths from
discovery. Document discovery walks up from any path to find it (ADR-0015), then indexes every
document underneath by `(kind, meta.name)` — never by file path or directory location.

### Configuration

A `kind: configuration` document defining platform-wide policy: which provider types and
topology types this solution allows (`providers`, `topologies`).

---

## Kinds

| Kind             | Purpose                                                                                                            |
| ---------------- | ------------------------------------------------------------------------------------------------------------------ |
| `configuration`  | Platform-wide policy — allowed providers/topologies.                                                               |
| `provider`       | A cloud provider instance: credentials, region, properties.                                                        |
| `providerconfig` | Registry of valid regions/resource types per provider type.                                                        |
| `resource`       | A provisioned cloud resource (VM, storage, etc.).                                                                  |
| `dns`            | DNS zones and records.                                                                                             |
| `network`        | VPCs/VNets, subnets, peerings.                                                                                     |
| `firewall`       | Security rules.                                                                                                    |
| `module`         | A deployable workload — containers, environment variables, mounts, health checks.                                  |
| `namespace`      | A named collection of modules.                                                                                     |
| `topology`       | A named grouping of resources/namespaces — "what belongs together".                                                |
| `topologyconfig` | Registry of expected component roles per topology type.                                                            |
| `workspace`      | The composed "image": providers, resources, topologies, and the provisioning recipe.                               |
| `solution`       | The solution manifest (`strata.yaml`) — identity, remotes, discovery root.                                         |
| `version`        | Pinned tool/image/chart versions with rationale.                                                                   |
| `integration`    | A connection to an external tool — Terraform, Helm, Compose today.                                                 |
| `tenant`         | Customer/organization identity — geographies, defaults inherited by every deployment.                              |
| `deployment`     | The "container instance": a workspace + an environment + a tenant, with `extends` chains and stages.               |
| `environment`    | Real declared variables/secrets/features that value tokens resolve against.                                        |
| `artifact`       | A pinnable, named reference to something external strata doesn't fetch or deploy itself (a container image today). |

---

## Composition Concepts

### extends

Structural inheritance between two `deployment` documents: a child deployment's spec is merged
on top of the parent's (`extends: deploy-base`) before validation — child fields win; stages
merge by step name; environment lists append base-first. Distinct from `environments` below,
which composes *values*, not structure.

### environments (on a deployment)

The ordered list of `environment` documents a deployment pulls variables/secrets/features from.
Multiple environments merge left-to-right — later entries win on key collisions.

### Tenant

A customer/organization identity referenced by a deployment (`spec.tenant`). Supplies defaults
that a deployment inherits unless it overrides them explicitly.

### Value token

Embedded value-binding syntax inside a document: `${var:KEY}`, `${secret:KEY}`, `${feature:KEY}`.
Resolved at build/deploy time against the named key's declared `store` (`constant`,
`environment`, `artifact`, or an integration-backed secret store).

### Source / Remote

A `remotes[]` entry on the solution manifest names an external module source — `local`, `git`,
or `helm`/OCI-chart — that a workspace's provisioner or module can pull from
(`SourceModel`, ADR-0018). `vendor/` in an example solution is typically a local stand-in for
what would otherwise be a real remote.

### Provisioner / Provisioning Step

A workspace's execution recipe: which tool (Terraform, Helm, Compose) provisions which part of
the topology, in what order, with what dependencies (`ProvisionerModel`/`ProvisioningStepModel`,
ADR-0011). `deploy run` executes these steps in dependency order; `build run` renders them.

### Integration

The connection layer between strata and an external tool — Terraform, Helm, Compose today.
Each integration knows how to check tool availability, render its own artifact shape, and (for
`deploy run`) call `plan`/`apply`/upgrade against what `build run` already wrote.

---

## Operational Concepts

### `strata build run`

Renders a deployment's workspace provisioners into on-disk artifacts (Terraform files, Helm
values, Compose files). Never executes anything — no `plan`, `apply`, or `deploy` call.

### `strata deploy run`

Executes what `build run` already rendered: `terraform plan`/`apply` (or the Helm/Compose
equivalent), per provisioning step, in dependency order. Never renders anything itself.

### Diagnostics

The structured findings a validation or run produces — errors, warnings, and info messages, each
naming the failing document/field. What both console and `--output json` render from, so the two
never describe a run differently.
