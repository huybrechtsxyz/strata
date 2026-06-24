# aws-eks/

Reference example: **AWS Elastic Kubernetes Service** platform managed with Terraform + Helm.

## Architecture

- **Provider:** AWS (eu-west-1 — Ireland)
- **Provisioners:** Terraform (infrastructure) + Helm (workloads)
- **Compute:** EKS cluster with managed node group (m5.xlarge)
- **Database:** RDS PostgreSQL (Multi-AZ, encrypted)
- **Registry:** Elastic Container Registry (immutable tags, scan-on-push)
- **Storage:** S3 bucket for artifacts (versioned, KMS-encrypted, lifecycle policies)
- **Network:** VPC with private/public subnets across 2 AZs
- **Services:** AWS Load Balancer Controller for ALB-based ingress

## File Map

```
aws-eks/
├── config/
│   └── aws-eks-config.yaml            # kind: configuration
├── environments/
│   └── aws-eks-env-prd.yaml           # kind: environment
├── stack/
│   ├── aws-provider-eu-west-1.yaml    # kind: provider   — AWS Ireland region
│   ├── aws-net-vpc.yaml               # kind: network    — VPC + subnets
│   ├── aws-res-eks.yaml               # kind: resource   — EKS cluster
│   ├── aws-res-rds.yaml               # kind: resource   — RDS PostgreSQL
│   ├── aws-res-ecr.yaml               # kind: resource   — Container Registry
│   ├── aws-res-s3.yaml                # kind: resource   — S3 artifacts bucket
│   ├── aws-ns-platform.yaml           # kind: namespace  — platform namespace
│   ├── aws-mod-alb-controller.yaml    # kind: module     — ALB controller Helm chart
│   └── aws-ws-platform.yaml           # kind: workspace  — ties it all together
└── deploy/
    └── aws-eks-deploy-prd.yaml        # kind: deployment
```

## Quick Start

```bash
strata validate -f config/aws-eks/deploy/aws-eks-deploy-prd.yaml
strata build plan --work-path config/aws-eks/
```
