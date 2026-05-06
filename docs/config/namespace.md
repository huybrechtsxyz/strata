# Namespace Configuration

Defines application deployment units within the platform. YAML files specify modules and lifecycle hooks managing application stacks, services, and deployment orchestration.

## Schema

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: namespace
meta:
  name: <namespace_name>        # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: <version>
spec:
  lifecycle:                     # Optional (requires lifecycle OR modules)
    bootstrap: {}                # Initial setup before provisioning
    provision: {}                # During infrastructure creation
    configure: {}                # After infrastructure provisioned
    health: {}                   # After configuration complete
    protect: {}                  # After successful deployment
    destroy: {}                  # During infrastructure destruction
  modules: []                    # Optional: deployable units
    - name: <module_name>
      file: <module_path>
      description: <description>
```

**Note:** Namespace must have **lifecycle** OR **modules** (or both). Empty namespaces not allowed.

## Lifecycle Phases

| Phase       | Description          | When Executed       |
| ----------- | -------------------- | ------------------- |
| `bootstrap` | Initial setup        | Before provisioning |
| `provision` | Infrastructure hooks | During creation     |
| `configure` | Configuration/setup  | After provisioning  |
| `health`    | Health checks        | After configuration |
| `protect`   | Security/backup      | After deployment    |
| `destroy`   | Cleanup/teardown     | During destruction  |

**Lifecycle structure:**

```yaml
lifecycle:
  <phase_name>:
    scripts:
      - file: <script_path>
        description: <description>
```

**Execution order:** bootstrap → provision → configure → health → protect (during deployment); destroy (during teardown)

## Examples

**Infrastructure:**

```yaml
meta:
  name: infra
  labels:
    version: 1.0.0
spec:
  modules:
    - name: traefik
      file: config/modules/traefik.yaml
      description: Reverse proxy and load balancer
    - name: portainer
      file: config/modules/portainer.yaml
```

**Application with Lifecycle:**

```yaml
meta:
  name: webapp
  labels:
    version: 2.0.0
spec:
  lifecycle:
    bootstrap:
      scripts:
        - file: scripts/prepare-storage.sh
    configure:
      scripts:
        - file: scripts/setup-database.sh
        - file: scripts/configure-app.sh
    health:
      scripts:
        - file: scripts/health-check.sh
  modules:
    - name: database
      file: config/modules/postgres.yaml
    - name: api
      file: config/modules/api-service.yaml
    - name: frontend
      file: config/modules/frontend.yaml
```

**Monitoring:**

```yaml
meta:
  name: monitoring
spec:
  lifecycle:
    configure:
      scripts:
        - file: scripts/setup-grafana-dashboards.sh
        - file: scripts/configure-alerts.sh
  modules:
    - name: prometheus
      file: config/modules/prometheus.yaml
    - name: grafana
      file: config/modules/grafana.yaml
```

**Lifecycle-Only:**

```yaml
meta:
  name: security
spec:
  lifecycle:
    bootstrap:
      scripts:
        - file: scripts/security/scan-images.sh
    protect:
      scripts:
        - file: scripts/security/harden-hosts.sh
```

## Script Execution

Scripts execute in order defined. Best practices:

- Number scripts to indicate order (`01-database.sh`, `02-migrations.sh`)
- Make idempotent (safe to run multiple times)
- Include error handling and validation
- Log execution progress

## Workspace Integration

```yaml
# workspace.yaml
spec:
  namespaces:
    - name: infrastructure # Deploy first
      file: config/namespaces/platform-infra.yaml
    - name: applications # Deploy second
      file: config/namespaces/platform-apps.yaml
    - name: monitoring # Deploy last
      file: config/namespaces/platform-monitoring.yaml
```

Platform processes namespaces in order defined. All paths relative to project root.

## Module Organization

**Service Modules:** Individual services (`api_gateway`, `auth_service`)  
**Component Modules:** Related components (`backend`, `frontend`, `cache`)  
**Stack Modules:** Complete stacks (`wordpress`)

## Common Patterns

**Three-Tier:** database → backend → frontend  
**Microservices:** user_service, order_service, payment_service, notification_service  
**Infrastructure:** ingress, registry, secrets

## Namespace Types

| Type               | Purpose                | Examples                             |
| ------------------ | ---------------------- | ------------------------------------ |
| **Infrastructure** | Core platform services | Ingress, DNS, certificates, registry |
| **Application**    | Business applications  | APIs, web apps, services             |
| **Data**           | Storage and processing | Databases, caches, queues            |
| **Monitoring**     | Observability          | Metrics, logs, traces, alerts        |
| **Security**       | Security services      | Secrets, scanning, compliance        |

## Best Practices

- **Naming:** Descriptive (`infra`, `webapp`, `monitoring`, `security`)
- **Logical grouping:** Related modules in same namespace
- **Lifecycle scripts:** Single responsibility, focused
- **Module independence:** Minimize cross-module dependencies
- **Version control:** Track namespace versions
- **Script idempotency:** Safe to run multiple times
- **Execution order:** Numbered prefixes for scripts
- **Module uniqueness:** Unique names within namespace

## Validation

Platform validates:

- Valid names (lowercase, alphanumeric, underscores)
- Required fields (name, lifecycle OR modules)
- Unique module names within namespace
- Script files exist and executable
- Module config files exist
- No circular dependencies

## Troubleshooting

**Validation failed:** Ensure lifecycle or modules defined, check duplicate module names, verify file paths valid
**Module not found:** Verify file path, check file exists, ensure relative to project root
**Script execution failed:** Check executable permissions (`chmod +x`), verify path, review logs, ensure dependencies installed
**Module conflicts:** Check duplicate names across namespaces, verify no conflicting resources
**Lifecycle phase skipped:** Verify phase defined, check scripts configured, review platform logs