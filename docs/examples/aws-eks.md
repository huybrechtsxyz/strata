# AWS EKS

AWS Elastic Kubernetes Service platform managed with **Terraform** (infrastructure) and **Helm** (workloads).
Provisions an EKS cluster with RDS, ECR, S3, and an ALB ingress controller in eu-west-1 (Ireland).

## Architecture Overview

| Layer      | Tool      | Purpose                                      |
| ---------- | --------- | -------------------------------------------- |
| Provider   | —         | AWS eu-west-1 (Ireland)                      |
| Network    | Terraform | VPC with private/public subnets across 2 AZs |
| Resources  | Terraform | EKS, RDS PostgreSQL, ECR, S3                 |
| Namespace  | Helm      | AWS Load Balancer Controller                 |
| Deployment | —         | Production instance                          |

---

## Configuration

```{literalinclude} ../../config/aws-eks/config/aws-eks-config.yaml
:language: yaml
```

---

## Workspace

```{literalinclude} ../../config/aws-eks/stack/aws-ws-platform.yaml
:language: yaml
```

---

## Provider

```{literalinclude} ../../config/aws-eks/stack/aws-provider-eu-west-1.yaml
:language: yaml
```

---

## Network

VPC with public and private subnets across two availability zones.

```{literalinclude} ../../config/aws-eks/stack/aws-net-vpc.yaml
:language: yaml
```

---

## Resources

### EKS Cluster

```{literalinclude} ../../config/aws-eks/stack/aws-res-eks.yaml
:language: yaml
```

### RDS PostgreSQL

```{literalinclude} ../../config/aws-eks/stack/aws-res-rds.yaml
:language: yaml
```

### Elastic Container Registry

```{literalinclude} ../../config/aws-eks/stack/aws-res-ecr.yaml
:language: yaml
```

### S3 Artifacts Bucket

```{literalinclude} ../../config/aws-eks/stack/aws-res-s3.yaml
:language: yaml
```

---

## Namespace

```{literalinclude} ../../config/aws-eks/stack/aws-ns-platform.yaml
:language: yaml
```

---

## Module

### AWS Load Balancer Controller

```{literalinclude} ../../config/aws-eks/stack/aws-mod-alb-controller.yaml
:language: yaml
```

---

## Environment

```{literalinclude} ../../config/aws-eks/environments/aws-eks-env-prd.yaml
:language: yaml
```

---

## Deployment

```{literalinclude} ../../config/aws-eks/deploy/aws-eks-deploy-prd.yaml
:language: yaml
```
