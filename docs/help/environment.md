# Environment

Configuration set for a specific deployment stage (dev, staging, production).

An environment (`kind: environment`) defines:
- **Target values** — variables, secrets, and configuration specific to this stage
- **Resource overrides** — e.g., different instance sizes for prod vs. dev
- **Feature flags** — enable/disable features per environment
- **Access control** — who can deploy to this environment
- **Promotion rules** — requirements to promote from previous stage

Environments allow the same workspace blueprint to deploy across multiple stages
with different configurations (smaller, cheaper resources for dev; larger, HA
resources for production).

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: environment
meta:
  name: production
spec:
  description: Production environment — multi-AZ, HA
  
  variables:
    environment_name: production
    region: us-east-1
    availability_zones: [a, b, c]
    db_instance_class: db.m5.large
    db_backup_days: 30
    log_retention_days: 90
  
  secrets:
    db_admin_password: vault:production/db-password
    api_key: bitwarden:prod-api-key
  
  tags:
    Environment: production
    Compliance: pci-dss
    CostCenter: ops-budget
```

---

## Staging Progression

Define environments for promotion pipeline:

```yaml
# environments/development.yaml
spec:
  order: 1
  description: Development environment
  variables:
    db_instance_class: db.t3.medium
    db_size_gb: 50
```

```yaml
# environments/staging.yaml
spec:
  order: 2
  description: Staging — mirrors production
  variables:
    db_instance_class: db.m5.large
    db_size_gb: 200
  promotion_from: development
  approval_required: false
```

```yaml
# environments/production.yaml
spec:
  order: 3
  description: Production — live traffic
  variables:
    db_instance_class: db.m5.xlarge
    db_size_gb: 500
  promotion_from: staging
  approval_required: true
  approvers: [ops-lead, cto]
```

---

## Resource Overrides

Customize resource config per environment:

```yaml
spec:
  resource_overrides:
    api-server:
      instance_class: t3.large
      min_replicas: 3
      max_replicas: 10
    database:
      backup_window: "03:00-04:00"
      multi_az: true
```

---

## Feature Flags

Enable/disable features per environment:

```yaml
spec:
  features:
    new_payment_processor: false   # dev/staging only
    enhanced_logging: true         # prod only
    beta_api_endpoints: false      # staging only
```

---

## Promotion Gates

Control promotion from one environment to the next:

```yaml
spec:
  promotion:
    from: staging
    requires:
      - approval: security-team
      - cost_review: max_5000_usd
      - test_coverage: minimum_80_percent
```

---

## See Also

- `environments` — multi-environment deployment strategy
- `deployment` — how environments are bound to deployments
- `profiles` — development vs. production profiles
