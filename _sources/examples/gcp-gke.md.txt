# GCP GKE

Google Kubernetes Engine platform managed with **Terraform** (infrastructure) and **Helm** (workloads).
Provisions a GKE Autopilot cluster with Cloud SQL, Artifact Registry, Cloud Storage, and an NGINX ingress controller in europe-west1 (Belgium).

## Architecture Overview

| Layer      | Tool      | Purpose                                                             |
| ---------- | --------- | ------------------------------------------------------------------- |
| Provider   | —         | GCP europe-west1 (Belgium)                                          |
| Network    | Terraform | VPC with dedicated subnets for nodes, pods, services, and Cloud SQL |
| Resources  | Terraform | GKE Autopilot, Cloud SQL, Artifact Registry, Cloud Storage          |
| Namespace  | Helm      | NGINX Ingress Controller                                            |
| Deployment | —         | Production instance                                                 |

---

## Configuration

```{literalinclude} ../../config/gcp-gke/config/gcp-gke-config.yaml
:language: yaml
```

---

## Workspace

```{literalinclude} ../../config/gcp-gke/stack/gcp-ws-platform.yaml
:language: yaml
```

---

## Provider

```{literalinclude} ../../config/gcp-gke/stack/gcp-provider-europe-west1.yaml
:language: yaml
```

---

## Network

VPC with dedicated subnets for nodes, pods, services, and Cloud SQL private IP.

```{literalinclude} ../../config/gcp-gke/stack/gcp-net-vpc.yaml
:language: yaml
```

---

## Resources

### GKE Autopilot Cluster

```{literalinclude} ../../config/gcp-gke/stack/gcp-res-gke.yaml
:language: yaml
```

### Cloud SQL for PostgreSQL

```{literalinclude} ../../config/gcp-gke/stack/gcp-res-cloudsql.yaml
:language: yaml
```

### Artifact Registry

```{literalinclude} ../../config/gcp-gke/stack/gcp-res-artifact-registry.yaml
:language: yaml
```

### Cloud Storage Bucket

```{literalinclude} ../../config/gcp-gke/stack/gcp-res-gcs.yaml
:language: yaml
```

---

## Namespace

```{literalinclude} ../../config/gcp-gke/stack/gcp-ns-platform.yaml
:language: yaml
```

---

## Module

### NGINX Ingress Controller

```{literalinclude} ../../config/gcp-gke/stack/gcp-mod-ingress-nginx.yaml
:language: yaml
```

---

## Environment

```{literalinclude} ../../config/gcp-gke/environments/gcp-gke-env-prd.yaml
:language: yaml
```

---

## Deployment

```{literalinclude} ../../config/gcp-gke/deploy/gcp-gke-deploy-prd.yaml
:language: yaml
```
