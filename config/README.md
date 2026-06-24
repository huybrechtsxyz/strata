# config/

Reference example workspaces demonstrating strata across different cloud providers and deployment patterns. Each sub-directory is a self-contained workspace showing the full file dependency chain: **configuration → environment → workspace → resources/namespaces/modules → deployment**.

Use these as a starting point or learning resource alongside `strata new --list` and `strata sln init --template`.

## Example Workspaces

| Directory | Cloud | Stack | Description |
|-----------|-------|-------|-------------|
| [`azure-aks/`](azure-aks/) | Azure | Terraform + Helm | AKS cluster with Traefik ingress |
| [`aws-eks/`](aws-eks/) | AWS | Terraform + Helm | EKS cluster with ALB Controller |
| [`gcp-gke/`](gcp-gke/) | GCP | Terraform + Helm | GKE Autopilot with NGINX ingress |
| [`hetzner-compose/`](hetzner-compose/) | Hetzner | Terraform + Compose | Cloud server with Docker Compose + INWX DNS |
| [`kamatera-swarm/`](kamatera-swarm/) | Kamatera | Terraform + Swarm | Virtual machines with Docker Swarm + Traefik |

## Common Structure

Every example follows the same directory layout:

```
<workspace>/
├── config/          # kind: configuration — remotes, integrations, zones
├── environments/    # kind: environment   — variables + secrets per env
├── stack/           # kind: workspace, provider, resource, namespace, module
└── deploy/          # kind: deployment    — ties workspace + env into stages
```

## Validating Examples

```bash
# Validate a single deployment
strata validate -f config/azure-aks/deploy/azure-aks-deploy-prd.yaml

# Validate all YAML files in an example
for f in config/azure-aks/**/*.yaml; do strata validate -f "$f"; done
```

---
