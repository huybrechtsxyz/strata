# Azure AKS

Azure Kubernetes Service platform managed with **Terraform** (infrastructure) and **Helm** (workloads).
Provisions an AKS cluster with PostgreSQL, Azure Container Registry, and Key Vault in West Europe.

## Architecture Overview

| Layer      | Tool      | Purpose                         |
| ---------- | --------- | ------------------------------- |
| Provider   | —         | Azure West Europe region        |
| Network    | Terraform | VNet with dedicated subnets     |
| Resources  | Terraform | AKS, PostgreSQL, ACR, Key Vault |
| Namespace  | Helm      | Traefik ingress controller      |
| Deployment | —         | Production instance             |

---

## Configuration

Validation rules, remote repository reference, and integration requirements for the platform.

```{literalinclude} ../../config/azure-aks/config/azure-aks-config.yaml
:language: yaml
```

---

## Workspace

Infrastructure blueprint — wires providers, provisioners, topology, resources, and namespaces together.

```{literalinclude} ../../config/azure-aks/stack/azure-ws-platform.yaml
:language: yaml
```

---

## Provider

```{literalinclude} ../../config/azure-aks/stack/azure-provider-westeurope.yaml
:language: yaml
```

---

## Network

VNet with dedicated subnets for AKS nodes, PostgreSQL, and private endpoints.

```{literalinclude} ../../config/azure-aks/stack/azure-net-platform.yaml
:language: yaml
```

---

## Resources

### AKS Cluster

```{literalinclude} ../../config/azure-aks/stack/azure-res-aks.yaml
:language: yaml
```

### PostgreSQL Flexible Server

```{literalinclude} ../../config/azure-aks/stack/azure-res-postgres.yaml
:language: yaml
```

### Azure Container Registry

```{literalinclude} ../../config/azure-aks/stack/azure-res-acr.yaml
:language: yaml
```

### Key Vault

```{literalinclude} ../../config/azure-aks/stack/azure-res-keyvault.yaml
:language: yaml
```

---

## Namespace

Application namespace grouping the platform's Helm modules.

```{literalinclude} ../../config/azure-aks/stack/azure-ns-platform.yaml
:language: yaml
```

---

## Module

### Traefik Ingress Controller

```{literalinclude} ../../config/azure-aks/stack/azure-mod-traefik.yaml
:language: yaml
```

---

## Environment

Production environment variables and secrets (sensitive values are resolved from the environment at runtime).

```{literalinclude} ../../config/azure-aks/environments/azure-aks-env-prd.yaml
:language: yaml
```

---

## Deployment

The concrete production deployment instance — binds workspace, environment, and stages.

```{literalinclude} ../../config/azure-aks/deploy/azure-aks-deploy-prd.yaml
:language: yaml
```
