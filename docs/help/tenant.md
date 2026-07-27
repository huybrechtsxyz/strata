# Tenant

Tenant scoping and multi-tenancy configuration.

A tenant (`kind: tenant`) defines:
- **Tenant identity** — unique identifier, name, metadata
- **Isolation boundaries** — resource groups, namespaces, accounts
- **Quotas and limits** — CPU, memory, cost budgets
- **Access control** — which teams/users manage this tenant
- **Billing** — cost allocation and chargeback

Tenants are used in multi-tenant platforms to enforce isolation and governance
for different customers, departments, or business units.

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: tenant
meta:
  name: customer-acme
spec:
  display_name: ACME Corporation
  description: Production tenant for ACME Corp
  
  isolation:
    type: namespace              # or: account, region
    namespace: tenant-acme
    account_id: "123456789012"
  
  quotas:
    cpu: 100
    memory: 500Gi
    monthly_cost_limit: 50000
  
  tags:
    customer: acme
    billing_code: cc-1234
    support_tier: premium
```

---

## Access Control

Define who manages this tenant:

```yaml
spec:
  access:
    owners:
      - team: acme-devops
      - user: alice@acme.com
    developers:
      - team: acme-engineers
    observers:
      - team: platform-ops
```

---

## Resource Allocation

Distribute resources per tenant:

```yaml
spec:
  resources:
    compute:
      - type: ec2_instance
        quantity: 5
        instance_type: t3.large
    storage:
      - type: s3_bucket
        quantity: 3
        size_gb: 100
    databases:
      - type: rds_instance
        quantity: 2
        class: db.t3.medium
```

---

## Environment-Specific Tenants

Create tenant variations per environment:

```yaml
# tenants/customer-acme.yaml
spec:
  display_name: ACME Corporation
```

```yaml
# environments/staging.yaml
spec:
  tenant_overrides:
    customer-acme:
      quotas:
        monthly_cost_limit: 5000    # lower in staging
```

---

## Multi-Tenancy in Deployments

Scope a deployment to a tenant:

```yaml
kind: deployment
spec:
  tenant: @config/tenants/customer-acme.yaml
  environments:
    production: @config/environments/acme-prod.yaml
```

---

## See Also

- `environment` — environment-specific values
- `policies` — enforce quotas and access controls
