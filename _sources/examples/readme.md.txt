# Platform Examples

Complete, working reference configurations for each supported deployment target.
Each example shows a full set of strata config files — from workspace definition through provider, resources, namespace, environment, and deployment — using a realistic production-grade setup.

## Available Examples

| Platform                              | Runtime      | Provisioners        | Notes                |
| ------------------------------------- | ------------ | ------------------- | -------------------- |
| [Azure AKS](azure-aks.md)             | Kubernetes   | Terraform + Helm    | Managed K8s on Azure |
| [AWS EKS](aws-eks.md)                 | Kubernetes   | Terraform + Helm    | Managed K8s on AWS   |
| [GCP GKE](gcp-gke.md)                 | Kubernetes   | Terraform + Helm    | Managed K8s on GCP   |
| [Hetzner Compose](hetzner-compose.md) | Docker       | Terraform + Compose | VMs on Hetzner Cloud |
| [Kamatera Swarm](kamatera-swarm.md)   | Docker Swarm | Terraform + Compose | VMs on Kamatera      |

## How These Examples Are Structured

Each example follows the same layout, mirroring how a real strata workspace repository is organised:

```
config/
├── config/
│   └── *-config.yaml        # kind: configuration  — validation rules
├── deploy/
│   └── *-deploy-prd.yaml    # kind: deployment     — concrete instance
├── environments/
│   └── *-env-prd.yaml       # kind: environment    — overrides for prd
└── stack/
    ├── *-provider-*.yaml    # kind: provider        — cloud credentials / region
    ├── *-ws-platform.yaml   # kind: workspace       — blueprint
    ├── *-net-*.yaml         # kind: network         — VNet / VPC (where applicable)
    ├── *-res-*.yaml         # kind: resource        — compute, database, registry, …
    ├── *-mod-*.yaml         # kind: module          — reusable Helm / Compose components
    ├── *-ns-*.yaml          # kind: namespace       — application package groups
    ├── *-fw-*.yaml          # kind: firewall        — security rules (where applicable)
    └── *-dns-*.yaml         # kind: dns             — DNS zones / records (where applicable)
```
