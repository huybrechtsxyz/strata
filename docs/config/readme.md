# Configuration Files

strata uses YAML documents to define infrastructure, environments, and deployments. Every
document follows the same shape (`apiVersion`, `kind`, `meta`, `spec`) and is discovered
automatically from a solution root (`strata.yaml`) — see
[docs/GLOSSARY.md](../GLOSSARY.md#document-basics) for the discovery mechanism itself.
This folder documents each `kind`'s schema individually; for current implementation status,
see [docs/design/v2-schema-overview.md](../design/v2-schema-overview.md).

## File Types

**Bootstrap:**

- [solution.md](solution.md) — `strata.yaml`, the solution manifest (identity, remotes, discovery root)
- [configuration.md](configuration.md) — platform-wide policy (allowed provider/topology types)

**Type registries:**

- [providerconfig.md](providerconfig.md) — valid regions/resource types per provider type
- [topologyconfig.md](topologyconfig.md) — expected component roles per topology type

**Infrastructure:**

- [provider.md](provider.md) — a cloud provider instance (credentials, region)
- [resource.md](resource.md) — a provisioned cloud resource (VM, storage, etc.)
- [network.md](network.md) — VPCs/VNets, subnets, peerings
- [firewall.md](firewall.md) — security rules
- [dns.md](dns.md) — DNS zones and records
- [topology.md](topology.md) — a named grouping of resources/namespaces
- [workspace.md](workspace.md) — the composed "image": providers, resources, topologies, provisioning recipe

**Workloads:**

- [module.md](module.md) — a deployable workload (containers, env vars, mounts, health checks)
- [namespace.md](namespace.md) — a named collection of modules

**Deployment:**

- [tenant.md](tenant.md) — customer/organization identity and inherited defaults
- [environment.md](environment.md) — real declared variables/secrets/feature flags
- [deployment.md](deployment.md) — workspace + environment + tenant, tied into stages
- [version.md](version.md) — pinned tool/image/chart versions with rationale
- [artifact.md](artifact.md) — a pinnable reference to something external (a container image today)

**Tooling:**

- [integration.md](integration.md) — a connection to an external tool (Terraform, Helm, Compose, ...)

## Architecture

```
Solution (strata.yaml)
  └─ Configuration ── ProviderConfig / TopologyConfig (type registries)
  └─ Workspace ── Provider, Resource, Topology, Namespace ── Module
  └─ Deployment ── extends → Workspace + Environment(s) + Tenant
```

Every reference between documents is `(kind, meta.name)` — never a file path (ADR-0015). Rename
or reorganize files freely; nothing here depends on directory layout or file naming.

## Quick Start

See [`config/`](../../config/README.md) for a real, working solution exercising every kind above
end to end, and its own README for the validate/build/deploy commands to run against it.
