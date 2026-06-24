# hetzner-compose/

Reference example: **Hetzner Cloud** platform with Docker Compose services and INWX DNS.

## Architecture

- **Provider:** Hetzner Cloud (Falkenstein datacenter)
- **DNS:** INWX (domain management)
- **Provisioners:** Terraform (infrastructure) + Docker Compose (services)
- **Resources:** Cloud server (CX31) with Ubuntu 24.04
- **Services:** Traefik reverse proxy + application stack (API + PostgreSQL)

## File Map

```
hetzner-compose/
├── config/
│   └── hetzner-config.yaml            # kind: configuration
├── environments/
│   └── hetzner-env-prd.yaml           # kind: environment
├── stack/
│   ├── hetzner-provider-fsn1.yaml     # kind: provider   — Falkenstein DC
│   ├── hetzner-res-app.yaml           # kind: resource   — cloud server
│   ├── hetzner-dns-inwx.yaml          # kind: dns        — INWX DNS records
│   ├── hetzner-ns-services.yaml       # kind: namespace  — services group
│   ├── hetzner-mod-traefik.yaml       # kind: module     — Traefik proxy
│   ├── hetzner-mod-app.yaml           # kind: module     — app + database
│   └── hetzner-ws-platform.yaml       # kind: workspace  — ties it all together
└── deploy/
    └── hetzner-deploy-prd.yaml        # kind: deployment
```

## Quick Start

```bash
strata validate -f config/hetzner-compose/deploy/hetzner-deploy-prd.yaml
strata build plan --work-path config/hetzner-compose/
```
