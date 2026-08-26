# Configuration Files

strata uses YAML configuration files to define infrastructure, environments, and deployments.

## File Types

**Infrastructure:**

- [workspace.md](workspace.md) - Infrastructure blueprint (topology, components)
- [environment.md](environment.md) - Environment customization (overrides)
- [deployment.md](deployment.md) - Deployment instance (target, stages, gates)

**Platform:**

- [configuration.md](configuration.md) - Validation rules (providers, topologies, regex patterns)
  - [manifest.md](manifest.md) - `configuration.spec.manifest` — deployment manifest/audit-record settings (not a standalone kind)

**Multi-tenancy:**

- [tenant.md](tenant.md) - Tenant identity, zones, and deployment defaults (default location `tenants/{code}.yaml`, overridable via a path convention)

**Resources:**

- [provider.md](provider.md) - Cloud provider credentials
- [resource.md](resource.md) - VM/storage specs
- [dns.md](dns.md) - DNS zones and records
- [firewall.md](firewall.md) - Security rules
- [network.md](network.md) - Network topologies, subnets, and peerings
- [module.md](module.md) - Reusable components
- [namespace.md](namespace.md) - Application packages

**Diagrams & records (machine-written or generated):**

- [diagram.md](diagram.md) - Visual diagram definitions rendered as Mermaid (ADR-0034)
- [version.md](version.md) - Intended component versions for a deployment ring
- [version-lock.md](version-lock.md) - Frozen, immutable snapshot of what was actually deployed
- [promotion-record.md](promotion-record.md) - Audit log of a version promotion through a ring progression

**Workspace state:**

- [workflow.md](workflow.md) - `.strata/workflow.yaml` — onboarding sequence for `strata guide`

## Architecture

Deployment → Environment → Workspace → Resources (providers, VMs, firewalls, modules)

## Naming Conventions

```
configurations/  00-defaults.yaml, 10-providers.yaml
workspaces/      platform.yaml, applications.yaml
environments/    development.yaml, production.yaml
deployments/     platform-dev.yaml, app-prod.yaml
providers/       kamatera-eu.yaml, azure-westeurope.yaml
resources/       vm_manager.yaml, vm_worker.yaml
firewalls/       web_public.yaml, api_internal.yaml
modules/         postgres.yaml, redis.yaml
namespaces/      frontend.yaml, backend.yaml
tenants/         acme.yaml, contoso.yaml   # default location; overridable, see tenant.md
```

## Quick Start

1. Define provider (credentials, region)
2. Define resources (VMs, firewalls)
3. Create workspace (topology, components)
4. Create environment (overrides)
5. Create deployment (workspace and environment references, optional stages/gates)

See individual files for detailed schemas and examples.