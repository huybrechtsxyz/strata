# gcp-gke/

Reference example: **Google Kubernetes Engine** platform managed with Terraform + Helm.

## Architecture

- **Provider:** GCP (europe-west1 — Belgium)
- **Provisioners:** Terraform (infrastructure) + Helm (workloads)
- **Compute:** GKE Autopilot cluster (managed nodes, automatic scaling)
- **Database:** Cloud SQL for PostgreSQL (regional HA, private IP)
- **Registry:** Artifact Registry (Docker format, cleanup policies)
- **Storage:** Cloud Storage bucket for artifacts (versioned, lifecycle policies)
- **Network:** VPC with dedicated subnets for nodes, pods, services, and Cloud SQL
- **Services:** NGINX Ingress Controller with GCP NEG integration

## File Map

```
gcp-gke/
├── config/
│   └── gcp-gke-config.yaml              # kind: configuration
├── environments/
│   └── gcp-gke-env-prd.yaml             # kind: environment
├── stack/
│   ├── gcp-provider-europe-west1.yaml   # kind: provider   — GCP Belgium region
│   ├── gcp-net-vpc.yaml                 # kind: network    — VPC + subnets
│   ├── gcp-res-gke.yaml                 # kind: resource   — GKE Autopilot cluster
│   ├── gcp-res-cloudsql.yaml            # kind: resource   — Cloud SQL PostgreSQL
│   ├── gcp-res-artifact-registry.yaml   # kind: resource   — Artifact Registry
│   ├── gcp-res-gcs.yaml                 # kind: resource   — Cloud Storage bucket
│   ├── gcp-ns-platform.yaml             # kind: namespace  — platform namespace
│   ├── gcp-mod-ingress-nginx.yaml       # kind: module     — NGINX ingress Helm chart
│   └── gcp-ws-platform.yaml             # kind: workspace  — ties it all together
└── deploy/
    └── gcp-gke-deploy-prd.yaml          # kind: deployment
```

## Quick Start

```bash
strata validate -f config/gcp-gke/deploy/gcp-gke-deploy-prd.yaml
strata build plan --work-path config/gcp-gke/
```
