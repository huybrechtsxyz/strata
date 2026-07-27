# Resource

Custom resource definition for infrastructure components.

A resource (`kind: resource`) describes an infrastructure entity that:
- **Has inputs and outputs** — configuration → deployment → results
- **Belongs to a provider** — AWS, Azure, GCP, Kubernetes, etc.
- **Can be templated** — apply across environments with variable substitution
- **Supports policies** — tagging, cost limits, security checks

Resources are typically managed by Terraform modules, but the resource YAML
allows explicit declaration and governance.

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: resource
meta:
  name: postgres-primary
spec:
  type: aws_rds_instance
  provider: aws-us-east-1
  inputs:
    engine: postgres
    instance_class: db.t3.medium
    allocated_storage: 100
    backup_retention_days: 30
  tags:
    Application: myapp
    Environment: production
    CostCenter: platform
  policies:
    - required_tags: [Application, Environment, CostCenter]
    - max_size_gb: 1000
```

---

## Templating Across Environments

Use `${var.name}` for substitution:

```yaml
spec:
  inputs:
    instance_class: ${var.db_instance_class}
    allocated_storage: ${var.db_size_gb}
```

Then override per environment:

```yaml
# environments/production.yaml
spec:
  resource_overrides:
    postgres-primary:
      db_instance_class: db.m5.large
      db_size_gb: 500
```

---

## Outputs

Extract values from deployed resources:

```yaml
spec:
  outputs:
    - name: endpoint
      path: aws_rds_instance.primary.endpoint
    - name: port
      path: aws_rds_instance.primary.port
```

Use in other resources:

```yaml
outputs:
  db_endpoint: ${resource.postgres-primary.endpoint}
```

---

## See Also

- `provider` — cloud account and region
- `workspace` — top-level blueprint containing resources
- `policies` — governance for resources
