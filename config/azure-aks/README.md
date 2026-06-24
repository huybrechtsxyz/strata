# azure-aks/

Reference example: **Azure Kubernetes Service** platform managed with Terraform + Helm.

## Architecture

- **Provider:** Azure (West Europe)
- **Provisioners:** Terraform (infrastructure) + Helm (workloads)
- **Compute:** AKS cluster with autoscaling node pool
- **Database:** PostgreSQL Flexible Server (zone-redundant HA)
- **Registry:** Azure Container Registry (Premium, geo-replicated)
- **Security:** Azure Key Vault (RBAC-enabled, purge-protected)
- **Network:** VNet with dedicated subnets for AKS, PostgreSQL, and private endpoints
- **Services:** Traefik ingress controller via Helm chart

## File Map

```
azure-aks/
├── config/
│   └── azure-aks-config.yaml          # kind: configuration
├── environments/
│   └── azure-aks-env-prd.yaml         # kind: environment
├── stack/
│   ├── azure-provider-westeurope.yaml # kind: provider   — Azure West Europe
│   ├── azure-net-platform.yaml        # kind: network    — VNet + subnets
│   ├── azure-res-aks.yaml             # kind: resource   — AKS cluster
│   ├── azure-res-postgres.yaml        # kind: resource   — PostgreSQL Flexible Server
│   ├── azure-res-acr.yaml             # kind: resource   — Container Registry
│   ├── azure-res-keyvault.yaml        # kind: resource   — Key Vault
│   ├── azure-ns-platform.yaml         # kind: namespace  — platform namespace
│   ├── azure-mod-traefik.yaml         # kind: module     — Traefik Helm chart
│   └── azure-ws-platform.yaml         # kind: workspace  — ties it all together
└── deploy/
    └── azure-aks-deploy-prd.yaml      # kind: deployment
```

## Quick Start

```bash
strata validate -f config/azure-aks/deploy/azure-aks-deploy-prd.yaml
strata build plan --work-path config/azure-aks/
```
