# Strata Glossary

Core concepts and terminology used in strata documentation, configuration schemas, and code.
Only implemented concepts are listed here. New concepts are added as they're implemented.

---

## Core Concepts

### Configuration
A `kind: configuration` file that defines the foundational settings for a strata solution:
remotes (versioned repositories), integrations (git, terraform, ansible, kubectl), security policies,
provider definitions, and layering rules. The entry point for understanding an infrastructure deployment.
Typically located at `config/<provider>-config.yaml`.

### Workspace
A `kind: workspace` file that describes a managed infrastructure topology: named resources
(compute, network, database), modules (Helm charts, Terraform modules), namespaces (logical
groupings), and provisioners (Terraform, Ansible). The workspace is instantiated by a deployment
via `spec.workspace: <reference>`.

### Deployment
A `kind: deployment` file that specifies: which workspace to use, which environment(s) to merge,
which stages to execute (infrastructure, platform, application), and deployment-specific settings
(wave assignment for canary rollouts, tenant membership). The deployment is the executable unit that
`strata deploy run` processes.

### Environment
A `kind: environment` file that provides environment-specific configuration: variables, secrets,
overrides to workspace resources/modules/providers, and layers (e.g., `tenant`).
Multiple environments can be merged together (last-wins semantics) to build a complete resolved
environment. Typically scoped to a stage (dev, test, production).

### Tenant
A logical customer/workspace/business unit within a shared deployment. Tenants are referenced
via `spec.tenant` on a deployment and control multi-tenancy scoping. When a deployment specifies
a tenant, tenant-specific environment overrides are applied during merge.

### Module
A reusable infrastructure component (Helm chart, Terraform module, Ansible playbook, Docker Compose
service group). Defined in `stack/<provider>-mod-<name>.yaml`. Modules are referenced by workspaces
and can be overridden per environment or tenant (replica count, size, version).

### Resource
A discrete infrastructure artifact (cloud VM, database, load balancer, Kubernetes cluster).
Defined in `stack/<provider>-res-<name>.yaml`. Resources are declared in a workspace topology and
provisioned via Terraform or other IaC backends. Can have environment-specific overrides.

### Provider
A cloud provider or infrastructure backend (AWS, Azure, GCP, Hetzner, Kubernetes). Defined in
`stack/<provider>-provider-<name>.yaml`. Encodes authentication, region, account settings, and
API credentials. Both Terraform and Kubernetes provisioners reference providers.

### Namespace
A logical grouping of modules and resources within a workspace (e.g., `platform`, `application`,
`networking`). Defined in `stack/<provider>-ns-<name>.yaml`. Used for organizational clarity
and can enforce layer-based access control (see Layering).

### Remote
A versioned git repository that supplies infrastructure code (Terraform modules, Ansible roles,
configuration data). Defined in `configuration.spec.remotes[]` with a base `reference` (branch/tag).
Environments can override the reference per remote to pin specific versions.
Types: `gitops` (config repo), `bundled` (shipped with strata), `container` (OCI registry).

### Provisioner
An execution engine for infrastructure changes: Terraform (AWS, Azure, GCP cloud resources),
Ansible (server configuration), or Kubernetes (via kubectl). Declared in workspace `spec.provisioners[]`.
Each provisioner has a source (git repo path), backend (Terraform Cloud, etc.), and stage ordering.

### Network
A virtual network topology (VPC, subnet, routing, peering). Defined in `stack/<provider>-net-<name>.yaml`.
Can be provisioned via Terraform and referenced by resources.

### Firewall
Network access control policy (security group, network policy). Defined in `stack/<provider>-fw-<name>.yaml`.
Controls ingress/egress rules between resources and networks.

### Manifest / Deployment Manifest
A machine-generated record of what was deployed: artifacts applied, provisioner output,
git commit SHAs, identity (who deployed, when, from which host), and status. Written to the
artifact store by `strata deploy run` for audit and drift detection.

---

## Configuration Schema Concepts (ADR 0001)

### apiVersion
The strata schema version. Always `strata.huybrechts.xyz/v1` for current configurations.
Required on every strata document.

### kind
The document type. Valid kinds: `configuration`, `workspace`, `deployment`, `environment`,
`namespace`, `module`, `resource`, `provider`, `tenant`, `network`, `firewall`, `dns` (user-authored),
plus `platform_model`, `deployment-manifest`, `version-lock`, `version`, `promotion-record`
(internal, machine-generated build/promotion artifacts — never hand-authored).

### meta
Metadata block on every strata document containing `name` (unique identifier, lowercase,
alphanumeric + `_-`), `labels` (key-value pairs for filtering), and `annotations` (key-value
for tooling metadata). Required on every document.

### spec
Specification block containing the document's business logic. Schema varies by `kind`.
Required on every document. Models use Pydantic's `extra="forbid"` — unknown fields cause validation errors.

---

## Layering & Multi-Tenancy Concepts (ADR 0003)

### Layer
A horizontal slice of deployment scope (e.g., `zone`, `tenant`, `application`).
Defined in `configuration.spec.layering[]` with a `name` and optional `default`.
Deployments declare which layers they support via `spec.layers: [layer1, layer2, ...]`.
Used for scope filtering in promotion strategies (ADR 0011).

### Layering
The mechanism for organizing infrastructure across multiple scopes simultaneously.
Each layer is independent; deployments are independent across layers. A deployment can be
both zone-level (multi-tenant infra) and tenant-level (per-tenant apps) simultaneously.

---

## State & Audit Concepts

### Drift
Unintended deviation between the desired infrastructure (strata config) and actual runtime state.
Detected by comparing Terraform state against the current workspace topology.
Logged to the drift record; used to warn of manual infrastructure changes (ADR 0008).

### Deployment Manifest
See "Manifest" above.

### Promotion Record
A machine-generated audit record written when a promotion completes or rolls back.
Captures: target (what was promoted, from/to versions), strategy, progression, ring waves,
gates passed/failed, commits, outcome (completed/partial/rolled-back), identity, and linked
deployment manifests. Stored in the artifact remote for permanent audit trail (ADR 0011).

### SBOM (Software Bill of Materials)
A comprehensive inventory of software dependencies across the deployment:
container images, Helm charts, Terraform modules, application dependencies (lockfiles),
and provisioner versions. Generated by `strata build sbom` and formatted as CycloneDX 1.6
for compliance tooling integration (ADR 0009).

---

## Promotion Concepts (ADR 0011)

### Ring
A named group of environments at the same delivery tier (e.g., `dev`, `test`, `qas`, `prd`).
Each ring may contain multiple environments. Progressions advance ring-by-ring with safety
gates to control version flow from development to production.

### Progression
An ordered list of rings that a version must traverse (e.g., `dev → test → qas → prd`).
Defined in `configuration.spec.promotions.progressions[]`.

### Strategy
A named policy that governs how a version moves into a ring: wave count, scope, gates.
Defined in `configuration.spec.promotions.strategies[]`.
Examples: `infra-cautious` (slow canary rollout for Terraform changes),
`image-wave` (multi-wave application image promotion).

### Wave
An ordering unit within promotion. **Deployment wave** (named, e.g., `canary`, `all`):
a subset of deployments within one environment. **Ring wave** (integer, e.g., 1, 2):
a subset of environments within a ring. Together they control rollout granularity.

### Scope
A layer name (from `configuration.spec.layering[]`) that determines which deployments
participate in deployment waving. Deployments lacking the layer are excluded from scoped
waves (all-at-once only).

### Gate
A precondition evaluated before promotion proceeds. Example: `require_progression_order`
checks that the previous ring meets its quorum before allowing advancement.

### Promotion Target
The artifact being versioned and promoted: `remote` (git ref), `helm_chart` (chart version),
`image` (container image tag), or `tool` (provisioner version, deferred).

### Version-Lock
A machine-managed file (`versions/<ring>.yaml`, `kind: version-lock`) that pins every
promotable target to an exact version for one ring. Authoritative when present; optional
otherwise. Promotion advances the lock. Scoped overlays (`versions/<ring>.<scope>.yaml`)
pin versions for canary/early waves.

---

## Integration & Backend Concepts

### Integration
A plugin interface to external tools (git, Terraform Cloud, Kubernetes, AWS CLI, Ansible, etc.).
Defined in `configuration.spec.integrations[]`. Controls authentication, API endpoints, and
tool availability.

### Backend
A runtime target for provisioners. Example: Terraform Cloud backend (organization, workspace).
Configured per provisioner in `workspace.spec.provisioners[].backend`.

---

## Operational Concepts

### Solution
The registry of all artifacts managed by strata for a given infrastructure: deployments,
environments, repositories, profiles, and plugins. Stored in `.strata/solution.json`.
Managed via `strata sln` commands.

### Profile
A named execution context (development, staging, production) that selects which values
(variables, secrets, references) apply during deployment. Defined in `.strata/` and activated
via `strata profile activate <name>`.

---

*Last updated: July 2026*
*Add new concepts as they are implemented.*
