# Service Command Group — Deploy Individual Services

> **Audience:** DevOps engineers managing Kubernetes applications or containerized workloads.
> You want to deploy individual services without running the full deployment pipeline.

The `strata service` command group lets you manage individual services (modules, namespaces,
or containerized workloads) independently of the full deployment.

---

## What is a "Service" in strata?

A **service** is a deployable unit defined in your configuration. Typically:
- A Kubernetes namespace with multiple pods
- A Docker Compose service
- A Helm release
- An individual module within your infrastructure

Services are declared in your deployment YAML under `spec.modules` and can be deployed, updated,
or destroyed independently.

**Key difference from full `strata deploy run`:**

| Operation    | `deploy run`                                               | `service deploy`                                    |
| ------------ | ---------------------------------------------------------- | --------------------------------------------------- |
| Scope        | All stages, all services                                   | Single service                                      |
| Dependencies | Runs entire pipeline (Terraform → Ansible → health checks) | Assumes infrastructure exists, updates service only |
| Typical use  | Initial deployment                                         | Service updates, hotfixes, canary deployments       |
| When to use  | Setup or major infrastructure changes                      | Frequent app updates                                |

---

## service list — Discover Available Services

List all deployable services in a deployment.

```bash
strata service list -f deployments/deploy-prd.yaml
```

Output:
```
Services in deployment: xyz-deploy-prd
──────────────────────────────────────

  ✅ api-service               (module)        [helm]
     Namespace:   api
     Status:      deployed
     Chart:       myrepo/api:v2.3.0
     Replicas:    3 / 3 ready

  ✅ worker-service            (module)        [helm]
     Namespace:   workers
     Status:      deployed
     Chart:       myrepo/worker:v1.8.0
     Replicas:    2 / 2 ready

  ✅ postgres                  (module)        [helm]
     Namespace:   databases
     Status:      deployed
     Chart:       bitnami/postgresql:12.1.0
     Replicas:    1 / 1 ready

  ⚠️  cron-jobs                (module)        [helm]
     Namespace:   jobs
     Status:      degraded
     Chart:       myrepo/cron:v1.0.0
     Replicas:    0 / 1 ready
```

### Machine-readable output

```bash
strata service list -f deploy-prd.yaml --output json
```

Returns:
```text
{
  "file": "deployments/deploy-prd.yaml",
  "services": [
    {
      "name": "api-service",
      "type": "helm",
      "namespace": "api",
      "status": "deployed",
      "replicas": {"ready": 3, "desired": 3}
    },
    ...
  ]
}
```

### Use cases

- Discover what services are available to deploy
- Monitor service health (ready replicas, desired count)
- Verify all services are deployed correctly

---

## service status — Check Service Health

Get detailed status for a specific service.

```bash
strata service status -f deployments/deploy-prd.yaml api-service
```

Output:
```
Service: api-service
────────────────────

Namespace:        api
Type:             Helm
Chart:            myrepo/api:v2.3.0
Status:           ✅ deployed

Replicas:         3 / 3 ready
  - api-service-pod-a1b2c3d4  ✅ ready (CPU: 245m, Memory: 512Mi)
  - api-service-pod-e5f6g7h8  ✅ ready (CPU: 198m, Memory: 498Mi)
  - api-service-pod-i9j0k1l2  ✅ ready (CPU: 267m, Memory: 531Mi)

Endpoints:        3
  - 10.0.1.10:8080
  - 10.0.1.11:8080
  - 10.0.1.12:8080

Recent events:
  2026-07-21 10:05  ✅ Deployment replicas ready
  2026-07-21 09:58  🔄 Deployment updated
  2026-07-21 09:55  ✅ Replicas scaled to 3
```

### Verbose output (with pod details)

```bash
strata service status -f deploy-prd.yaml api-service --verbose
```

Shows detailed pod information, logs, and recent errors.

### Machine-readable output

```bash
strata service status -f deploy-prd.yaml api-service --output json
```

### Use cases

- Verify a service is healthy before directing traffic
- Troubleshoot pod failures or replicas not ready
- Monitor resource usage (CPU, memory)
- Check recent deployment events

---

## service deploy — Update a Service

Deploy or update a single service without running the full pipeline.

```bash
strata service deploy -f deployments/deploy-prd.yaml api-service
```

Output:
```
Deploying service: api-service
──────────────────────────────

Provisioner:      Helm
Chart:            myrepo/api:v2.3.0
Namespace:        api

Dry-run output:
  Release "api-service" will be upgraded
  SUMMARY OF CHANGES:
    - Image tag: v2.3.0 → v2.4.0
    - Replicas:  3 → 3
    - Resources: unchanged

Proceed with deployment? (y/n) y

Applying changes...
  Release upgraded successfully ✅

Verifying deployment...
  Waiting for replicas... 0/3 ready
  Waiting for replicas... 1/3 ready
  Waiting for replicas... 2/3 ready
  Waiting for replicas... 3/3 ready ✅

Deployment complete in 45s
New version: v2.4.0 ready at: api.example.com
```

### Non-interactive mode (CI/CD)

```bash
strata service deploy -f deploy-prd.yaml api-service --force
```

Skips confirmation prompts.

### Dry-run (preview changes)

```bash
strata service deploy -f deploy-prd.yaml api-service --dry-run
```

Shows what would be deployed without making changes.

### Use cases

- **Hotfixes:** Update a service without touching other services
- **Canary deployments:** Deploy new version to a subset of services, verify, then roll out
- **Service-specific updates:** Update image version, replicas, environment variables
- **CI/CD:** Automated service deployments triggered on commit

---

## service destroy — Remove a Service

Destroy (delete) a single service.

```bash
strata service destroy -f deployments/deploy-prd.yaml cron-jobs --force
```

Output:
```
Destroying service: cron-jobs
─────────────────────────────

Namespace:   jobs
Chart:       myrepo/cron:v1.0.0

This will DELETE the following resources:
  - Deployment cron-jobs
  - Service cron-jobs
  - ConfigMap cron-jobs-config
  - PersistentVolumeClaim cron-jobs-storage (data loss!)

⚠️  This action is DESTRUCTIVE and cannot be undone.

Proceed with destruction? (y/n) y

Deleting...
  Resource deleted: Deployment ✅
  Resource deleted: Service ✅
  Resource deleted: ConfigMap ✅
  Resource deleted: PersistentVolumeClaim ✅

Destruction complete.
```

### Dry-run before destroying

```bash
strata service destroy -f deploy-prd.yaml cron-jobs --dry-run
```

Shows what would be deleted without making changes.

### Use cases

- Remove a deprecated service
- Clean up temporary services
- Troubleshoot by removing and redeploying

---

## Common Workflows

### Workflow 1: Canary deployment

Deploy a new service version to a subset, verify it works, then roll out fully.

```bash
# 1. Create a canary variant of your service (10% traffic)
# Edit deployments/deploy-prd-canary.yaml:
# spec:
#   modules:
#     - name: api-service-canary
#       chart: myrepo/api:v2.4.0
#       replicas: 1                    # 1 out of 10 total replicas
#       weight: 0.1                    # 10% of traffic

# 2. Deploy the canary
strata service deploy -f deploy-prd-canary.yaml api-service-canary --force

# 3. Monitor for issues (e.g., error rates, latency)
sleep 300 && strata service status -f deploy-prd-canary.yaml api-service-canary

# 4. If healthy, roll out to full deployment
strata service deploy -f deploy-prd.yaml api-service --force

# 5. If issues, destroy canary
strata service destroy -f deploy-prd-canary.yaml api-service-canary --force
```

### Workflow 2: Update a service without redeploying infrastructure

Change just the app version, not the infrastructure.

```bash
# Edit environments/env-prd.yaml:
# spec:
#   overrides:
#     modules:
#       - name: api-service
#         image: myrepo/api:v2.4.0     # new version

# Deploy the service
strata service deploy -f deploy-prd.yaml api-service --force
```

### Workflow 3: Hotfix a crashing service

A service is crashing due to a bug. Deploy a fixed version immediately.

```bash
# 1. See what's currently deployed
strata service status -f deploy-prd.yaml api-service --verbose

# 2. Check logs
kubectl logs -n api deployment/api-service | tail -50

# 3. Fix the bug, rebuild image, tag as v2.4.1-hotfix
docker build -t myrepo/api:v2.4.1-hotfix .
docker push myrepo/api:v2.4.1-hotfix

# 4. Update environment override
# Edit environments/env-prd.yaml:
# spec:
#   overrides:
#     modules:
#       - name: api-service
#         image: myrepo/api:v2.4.1-hotfix

# 5. Deploy the hotfix
strata service deploy -f deploy-prd.yaml api-service --force
```

### Workflow 4: CI/CD service pipeline

Automatically deploy service updates on every commit.

```yaml
# .github/workflows/deploy-service.yml
name: Deploy Service

on:
  push:
    branches: [ main ]
    paths:
      - 'src/**'           # only deploy if app code changed
      - 'deployment/**'    # or if deployment config changed

jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - uses: actions/checkout@v3

      - name: Set up strata
        run: pip install strata

      - name: Build and push image
        run: |
          docker build -t myrepo/api:${{ github.sha }} .
          docker push myrepo/api:${{ github.sha }}

      - name: Update deployment
        run: |
          # Update the image tag in env-prd.yaml
          sed -i "s|myrepo/api:.*|myrepo/api:${{ github.sha }}|" environments/env-prd.yaml

      - name: Dry-run service deployment
        run: strata service deploy -f deployments/deploy-prd.yaml api-service --dry-run

      - name: Deploy service
        run: strata service deploy -f deployments/deploy-prd.yaml api-service --force

      - name: Verify health
        run: strata service status -f deployments/deploy-prd.yaml api-service

      - name: Notify on success
        if: success()
        run: |
          curl -X POST https://hooks.slack.com/... \
            -d '{"text": "Service deployed successfully"}'
```

---

## When to Use service vs. deploy

| Scenario                     | Command                              | Reason                                      |
| ---------------------------- | ------------------------------------ | ------------------------------------------- |
| Initial infrastructure setup | `deploy run`                         | Need to create networks, databases, compute |
| Regular app updates          | `service deploy`                     | Just update the container image/config      |
| Hotfixes                     | `service deploy`                     | Minimal change, fast feedback               |
| Scaling up replicas          | `service deploy`                     | Update Helm chart values only               |
| Network or storage changes   | `deploy run`                         | Infrastructure-level change                 |
| Full environment promotion   | `deploy run`                         | Entire stack (infrastructure + apps)        |
| One service failing          | `service destroy` + `service deploy` | Isolate the problem                         |

---

## Troubleshooting

### "Service not found" error

```
Error: Service 'api-service' not found in deployment
```

**Check:**
```bash
strata service list -f deployments/deploy-prd.yaml
```

Verify the service name is spelled correctly.

### Service deployment hangs (waiting for replicas)

```
Waiting for replicas... 1/3 ready
(timeout after 5 minutes)
```

**Investigate:**
```bash
kubectl describe pod -n api <pod-name>
kubectl logs -n api <pod-name>
```

**Common causes:**
- Image pull failure (wrong credentials or image doesn't exist)
- Not enough resources (CPU/memory)
- Liveness probe failing

### Service shows as degraded

```
Status: degraded
Replicas: 0 / 1 ready
```

**Check health:**
```bash
strata service status -f deploy-prd.yaml my-service --verbose
```

See pod events and logs to diagnose.

---

## Best Practices

1. **Always dry-run first:** `strata service deploy ... --dry-run`
2. **Monitor after deploy:** `strata service status` after deployment completes
3. **Use canary for risky updates:** Test with `--weight 0.1` before rolling out
4. **Tag images for reproducibility:** Use git commit SHA or semantic version, not `latest`
5. **Keep service overrides in environment files:** Not hardcoded in deployment YAML
6. **Automated deployment in CI/CD:** Service updates should be hands-free
7. **Verify health after deploy:** Run integration tests to ensure the service is working

---

## See Also

- [Commands Reference — service](../platform/commands.md#service) — full command documentation
- [Deploying Infrastructure](./deploying.md) — full deployment pipeline
- [Module Configuration](../config/module.md) — defining services/modules
