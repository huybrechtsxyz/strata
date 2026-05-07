# Module Configuration

Defines deployable application components within namespaces. YAML files specify deployment sources, lifecycle hooks, and orchestration for services, applications, or system components.

## Schema

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: module
meta:
  name: <module_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
    category: <category> # proxy, database, cache, etc.
spec:
  lifecycle: # Optional: module-specific hooks
    bootstrap: {} # Before module deployment
    provision: {} # During module provisioning
    configure: {} # After module deployment
    health: {} # After configuration
    protect: {} # After successful deployment
    destroy: {} # During module teardown
  properties:
    source: # Required: deployment source
      type: <source_type> # local, gitops, image, script
      repository: <repository>
      reference: <reference>
      source_path: <source_path>
      deploy_path: <deploy_path>
```

## Source Types

**Local** - Workspace configuration:

```yaml
source:
  type: local
  repository: /
  reference: /
  source_path: services/traefik
  deploy_path: modules/traefik
```

_Use for: workspace services, custom apps, infrastructure services_

**GitOps** - External Git repository:

```yaml
source:
  type: gitops
  repository: https://github.com/org/repo.git
  reference: main
  source_path: deployments/app
  deploy_path: modules/app
```

_Use for: shared configs, external services, multi-workspace deployments_

**Image** - Container image:

```yaml
source:
  type: image
  repository: docker.io/traefik/traefik
  reference: v2.10
  source_path: /
  deploy_path: modules/traefik
```

_Use for: pre-packaged apps, third-party services, standard containers_

**Script** - Custom deployment script:

```yaml
source:
  type: script
  repository: /
  reference: v1.0.0
  source_path: scripts/deploy-custom.sh
  deploy_path: modules/custom
```

_Use for: complex deployment logic, custom procedures, legacy apps_

## Examples

**Traefik Proxy:**

```yaml
meta:
  name: traefik
  labels:
    version: "1.0.0"
    category: proxy
spec:
  properties:
    source:
      type: local
      repository: /
      reference: /
      source_path: services/traefik
      deploy_path: modules/traefik
```

**PostgreSQL with Lifecycle:**

```yaml
meta:
  name: postgres
  labels:
    version: "15.0.0"
    category: database
spec:
  lifecycle:
    bootstrap:
      scripts:
        - file: scripts/postgres/prepare-storage.sh
    configure:
      scripts:
        - file: scripts/postgres/init-db.sh
        - file: scripts/postgres/run-migrations.sh
    health:
      scripts:
        - file: scripts/postgres/health-check.sh
    protect:
      scripts:
        - file: scripts/postgres/backup.sh
  properties:
    source:
      type: image
      repository: docker.io/library/postgres
      reference: "15-alpine"
      source_path: /
      deploy_path: modules/postgres
```

**GitOps Application:**

```yaml
meta:
  name: api_service
  labels:
    version: "2.3.1"
    category: application
spec:
  lifecycle:
    configure:
      scripts:
        - file: scripts/api/setup-env.sh
    health:
      scripts:
        - file: scripts/api/smoke-test.sh
  properties:
    source:
      type: gitops
      repository: https://github.com/org/api-service.git
      reference: v2.3.1
      source_path: deploy/kubernetes
      deploy_path: modules/api
```

**Container Image:**

```yaml
meta:
  name: redis
  labels:
    version: "7.0.0"
    category: cache
spec:
  properties:
    source:
      type: image
      repository: docker.io/library/redis
      reference: "7.0-alpine"
      source_path: /
      deploy_path: modules/redis
```

## Module Categories

| Category      | Purpose                        | Examples                   |
| ------------- | ------------------------------ | -------------------------- |
| `proxy`       | Reverse proxies/load balancers | Traefik, Nginx, HAProxy    |
| `database`    | Data storage                   | PostgreSQL, MySQL, MongoDB |
| `cache`       | Caching                        | Redis, Memcached           |
| `queue`       | Message queues                 | RabbitMQ, Kafka, NATS      |
| `application` | Business apps                  | APIs, web apps, services   |
| `monitoring`  | Observability                  | Prometheus, Grafana, Loki  |
| `security`    | Security services              | Vault, cert-manager        |
| `storage`     | Storage solutions              | MinIO, GlusterFS           |

## Path Resolution

**source_path** - Module source location:

- **Local**: Relative to workspace root (`services/traefik`)
- **GitOps**: Relative to repository root (`deploy/kubernetes`)
- **Image**: Container path (typically `/`)
- **Script**: Path to deployment script

**deploy_path** - Deployment artifacts location:

- Relative to build directory
- Platform generates deployment files here
- Example: `modules/traefik` → `build/modules/traefik`

## Namespace Integration

```yaml
# namespace.yaml
spec:
  modules:
    - name: traefik
      file: config/modules/traefik.yaml
    - name: postgres
      file: config/modules/postgres.yaml
```

## Module Patterns

**Stateless:** Image-based, no lifecycle hooks  
**Stateful:** Lifecycle hooks for bootstrap, configure, protect  
**System:** Local source, critical system services

## Best Practices

- **Naming:** Match service name (`traefik`, `postgres`, `redis`)
- **Version labels:** Track versions for rollback
- **Categories:** Organize modules consistently
- **Idempotent scripts:** Safe to run multiple times
- **Error handling:** Include in all lifecycle scripts
- **Source type:** Choose based on deployment method
- **Paths:** Consistent patterns across modules
- **Documentation:** Clear descriptions in annotations
- **Script ordering:** Number scripts for execution control
- **Dependencies:** Document in annotations if needed

## Validation

Platform validates:

- Valid names (lowercase, alphanumeric, underscores)
- Required fields (name, source type, paths)
- Source type matches options
- Script files exist and executable
- Valid path formats
- No conflicting module names

## Troubleshooting

**Module not found:** Verify file path in namespace config, check file exists in `config/modules/`, ensure name matches reference  
**Source path invalid:** Check path exists (local/script), verify URL (gitops), confirm image exists (image), validate format  
**Lifecycle script failed:** Check executable permissions, verify path, review output, ensure dependencies available  
**Deploy path conflicts:** Ensure unique deploy_path per module, check for overlapping directories  
**Version mismatch:** Verify image tag exists, check Git reference exists, confirm version label accuracy

## Dependencies

Control module order in namespace:

```yaml
spec:
  modules:
    - name: database # First
    - name: cache # Second
    - name: backend # Third (depends on database + cache)
    - name: frontend # Last (depends on backend)
```

Document external dependencies in annotations.