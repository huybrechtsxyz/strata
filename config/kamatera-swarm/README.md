# kamatera-swarm/

Reference example: **Kamatera Cloud** platform with Terraform-provisioned virtual machines running a **Docker Swarm** cluster with Traefik ingress.

## Architecture

- **Provider:** Kamatera (EU-FR datacenter)
- **Provisioner:** Terraform (VM provisioning + Swarm bootstrap)
- **Topology:** Docker Swarm — 1 manager, 2 infra workers, 2 app workers
- **Services:** Traefik reverse proxy (Compose module)
- **Secrets:** Bitwarden Secrets Manager

## File Map

```
kamatera-swarm/
├── config/
│   └── kamatera-swarm-config.yaml       # kind: configuration — remotes, integrations, zones, provider schema
├── environments/
│   └── kamatera-swarm-env-prd.yaml      # kind: environment  — production variables + secrets
├── stack/
│   ├── kamatera-provider-eu-fr.yaml     # kind: provider     — Kamatera EU-FR region
│   ├── kamatera-fw-base.yaml            # kind: firewall     — Swarm cluster firewall rules
│   ├── kamatera-mod-traefik.yaml        # kind: module       — Traefik Compose service
│   ├── kamatera-ns-base.yaml            # kind: namespace    — base namespace (Traefik)
│   ├── kamatera-res-vm-manager.yaml     # kind: resource     — Swarm manager VM spec
│   ├── kamatera-res-vm-infra.yaml       # kind: resource     — infrastructure worker VM spec
│   ├── kamatera-res-vm-worker.yaml      # kind: resource     — app worker VM spec
│   └── kamatera-ws-platform.yaml        # kind: workspace    — ties providers, provisioners, resources
└── deploy/
    └── kamatera-swarm-deploy-prd.yaml   # kind: deployment   — production deployment
```

## Key Concepts Demonstrated

- **Custom provider schema** — `spec.providers` in the configuration defines the Kamatera VM resource schema with CPU/RAM/billing constraints, validated at `strata validate` time.
- **Custom topology** — `spec.topologies` defines the `docker_swarm` topology type with manager/worker role rules.
- **Firewall kind** — Declarative firewall rules attached to resources via workspace `firewalls:`.
- **Bundled module** — Traefik defined as a `kind: module` with Compose services, mounted configs, and health checks.
- **Multi-VM topology** — Three distinct resource specs (manager/infra/worker) with different CPU/RAM profiles and role assignments.

## Quick Start

```bash
# Validate the configuration file
strata validate config/kamatera-swarm/config/kamatera-swarm-config.yaml

# Validate the full deployment
strata validate config/kamatera-swarm/deploy/kamatera-swarm-deploy-prd.yaml
```

Replace `<bitwarden-item-id>` placeholders in `environments/kamatera-swarm-env-prd.yaml` with real Bitwarden item IDs before running `strata build` or `strata deploy`.
