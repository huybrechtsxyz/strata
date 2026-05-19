# Configuration Files

strata uses YAML configuration files to define infrastructure, environments, and deployments.

## File Types

**Infrastructure:**

- [workspace.md](workspace.md) - Infrastructure blueprint (topology, components)
- [environment.md](environment.md) - Environment customization (overrides)
- [deployment.md](deployment.md) - Deployment instance (target, approval)

**Platform:**

- [configuration.md](configuration.md) - Validation rules (providers, topologies, regex patterns)

**Resources:**

- [provider.md](provider.md) - Cloud provider credentials
- [resource.md](resource.md) - VM/storage specs
- [firewall.md](firewall.md) - Security rules
- [module.md](module.md) - Reusable components
- [namespace.md](namespace.md) - Application packages

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
```

## Quick Start

1. Define provider (credentials, region)
2. Define resources (VMs, firewalls)
3. Create workspace (topology, components)
4. Create environment (overrides)
5. Create deployment (target)

See individual files for detailed schemas and examples.