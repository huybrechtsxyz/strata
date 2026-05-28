# Resource Configuration

Defines infrastructure resources (e.g., VMs) deployed by the platform. YAML files specify compute, storage, networking for provisioning via IaC tools. Values are provider-agnostic but may include provider-specific codes (OS versions, instance types). Used to create workspace variable definitions (e.g., Terraform TF-vars).

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: resource
meta:
  name: <resource_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: <version>
    environment: <environment> # production, staging, development
spec:
  properties:
    provider_type: <provider_type> # Required: kamatera, azure, aws, etc.
    resource_type: <resource_type> # Required: virtualmachine, container, database, storage
    unit_cost: <cost> # Optional: cost per unit time (USD/month)
  configuration:
    # Provider-agnostic params (may include provider-specific codes)
    os_name: <string>
    os_code: <string> # Provider-specific version code
    cpu_type: <string> # Provider-specific identifier
    cpu_cores: <integer>
    ram_mb: <integer>
    billing: <string> # hourly, monthly
  virtualmachine: # If resource_type=virtualmachine
    installpoint: <path> # Base installation path
    firewall:
      name: <firewall_name>
      file: <firewall_file> # Relative path
    disks: # Physical storage devices
      - size: <integer> # GB
        label: <string>
        mount: <absolute_path>
    volumes: # Logical mount points
      - name: <string>
        path: <absolute_path>
```

## Resource Types

| Type             | Description           | Use Cases                                   |
| ---------------- | --------------------- | ------------------------------------------- |
| `virtualmachine` | Standard VM           | General compute, app hosting, cluster nodes |
| `container`      | Container instance    | Lightweight workloads, microservices        |
| `database`       | Managed database      | SQL/NoSQL databases                         |
| `storage`        | Storage volume/bucket | File/object storage, backups                |

## Examples

**Docker Swarm Manager:**

```yaml
meta:
  name: vm_manager
  labels:
    version: 1.0.0
    environment: production
spec:
  properties:
    provider_type: kamatera
    resource_type: virtualmachine
    unit_cost: 5.00
  configuration:
    os_name: "Ubuntu"
    os_code: "24.04 64bit"
    cpu_type: "A"
    cpu_cores: 1
    ram_mb: 1024
    billing: "hourly"
  virtualmachine:
    installpoint: /opt/install/app
    firewall:
      name: fw_docker
      file: config/firewalls/fw-vm-docker.yaml
    disks:
      - size: 20
        label: vm-manager-os
        mount: /mnt/data1
    volumes:
      - name: config
        path: /mnt/data1/etc/app
      - name: logs
        path: /mnt/data1/var/logs/app
```

**Azure VM:**

```yaml
meta:
  name: vm_azure_web
spec:
  properties:
    provider_type: azure
    resource_type: virtualmachine
    unit_cost: 73.00
  configuration:
    sku: "Standard_B2s"
  virtualmachine:
    firewall:
      name: fw_web
      file: config/firewalls/fw-web.yaml
    disks:
      - size: 30
        label: os-disk
        mount: /
      - size: 100
        label: data-disk
        mount: /data
```

**Minimal:**

```yaml
meta:
  name: vm_minimal
spec:
  properties:
    provider_type: local
    resource_type: virtualmachine
  configuration:
    os_name: "Ubuntu"
    os_code: "24.04 64bit"
```

## Disks vs Volumes

**Disks** (physical storage devices):

- OS disk + additional data disks
- Each has size, label, mount point

**Volumes** (logical application directories):

- Named directories for organized storage
- Mapped to paths on attached disks
- Used by applications/orchestration

Example: Disks = hardware allocation, Volumes = logical app directories

## Workspace Integration

```yaml
# workspace.yaml
spec:
  topology:
    - name: platform_swarm
      components:
        - name: manager
          file: config/resources/vm-manager.yaml # Resource reference
          type: manager
          count: 1
```

## Cost Tracking

`unit_cost` field enables cost estimation (no enforced currency, no automatic updates, manual tracking):

```yaml
spec:
  properties:
    unit_cost: 5.00 # USD per month (recommended)
```

Usage: Calculate total costs, compare efficiency, budget planning, cost optimization

## Best Practices

- **Naming:** Descriptive with type prefix (`vm_manager`, `vm_worker`)
- **Separate by role:** Distinct files for different node roles
- **Document costs:** Always include `unit_cost`
- **Version control:** Semantic versioning in labels
- **Environment labels:** Tag by environment
- **Firewall references:** Always specify for security
- **Disk planning:** Separate OS and data disks
- **Volume organization:** Consistent naming across resources
- **Provider codes:** Verify OS codes/CPU types match provider docs
- **Right-sizing:** Start minimal, scale based on monitoring

## Common Patterns

**High Availability:** 3 managers (quorum), multiple workers
**Development:** Minimal resources, hourly billing
**Storage-Optimized:** 2+ CPU, 4GB+ RAM, multiple large data disks

## Validation

Platform validates:

- Valid names (lowercase, alphanumeric, underscores)
- Required fields (name, provider_type, resource_type, configuration)
- Positive values (CPU, RAM, disk sizes)
- Valid mount points (absolute paths)
- Firewall file references exist
- Provider-specific requirements

## Troubleshooting

**Resource not found:** Verify file path in topology, check file exists in `config/resources/`
**Invalid config:** Check positive integers (CPU/RAM), verify OS codes, ensure sufficient disk sizes, validate absolute mount paths
**Provider mismatch:** Ensure `provider_type` matches workspace provider, verify CPU/OS codes valid for provider
**Cost tracking:** Update `unit_cost` on pricing changes, use consistent currency (USD recommended)