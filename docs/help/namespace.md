# Namespace

Application namespace for organizing resources in a workspace.

A namespace (`kind: namespace`) logically groups related resources:
- **By application** — e.g., `payment-service`, `user-api`, `data-pipeline`
- **By tier** — e.g., `frontend`, `backend`, `database`
- **By feature** — e.g., `observability`, `networking`, `security`

Namespaces do NOT create Kubernetes namespaces or cloud resource groups by
themselves — they are a logical organization tool that helps manage complex
deployments with many resources.

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: namespace
meta:
  name: backend-services
spec:
  description: Core backend services (API, workers, queues)
  labels:
    layer: backend
    owner: backend-team
  resources:
    - api-server
    - worker-processor
    - message-queue
```

---

## Usage

Namespaces are referenced in deployments to group stages, policies, or outputs:

```yaml
kind: deployment
spec:
  namespaces:
    - name: backend-services
      modules:
        - ref: @config/namespaces/backend-services.yaml
```

---

## Cross-Repo

Namespace definitions can live in separate repos:

```yaml
spec:
  modules:
    - ref: @shared/namespaces/observability.yaml
```

---

## Scoping

Use namespaces to scope policies or gates:

```yaml
spec:
  policies:
    - name: backend-cost-limit
      namespace: backend-services
      type: cost_threshold
      configuration:
        monthly_limit: 5000
```

---

## See Also

- `cross-repo` — how @repo references work
- `workspace` — top-level blueprint that organizes namespaces
