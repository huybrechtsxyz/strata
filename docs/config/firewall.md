# Firewall Configuration

Defines network security rules for VMs. YAML files specify allowed/denied traffic applied during provisioning.

## Schema

```yaml
apiVersion: platform.huybrechts.xyz/v1
kind: firewall
meta:
  name: <resource_name> # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  reset: <boolean> # Reset existing rules before applying (default: false)
  defaults: [] # Default rules (usually deny-all)
  deny: [] # Explicit deny rules
  allow: [] # Allow rules
```

## Rule Structure

| Field       | Type             | Required | Description                            |
| ----------- | ---------------- | -------- | -------------------------------------- |
| `direction` | string           | Yes      | `in` or `out`                          |
| `proto`     | string           | No       | `tcp`, `udp`, `icmp`, etc.             |
| `port`      | int/array/string | No       | Port: `22`, `[80, 443]`, `49152:49251` |
| `from`      | string           | No       | Source IP/CIDR (incoming)              |
| `to`        | string           | No       | Destination IP/CIDR (outgoing)         |
| `interface` | string           | No       | Interface name (`lo`, `eth0`)          |
| `comment`   | string           | No       | Descriptive comment                    |

**Port formats:** Single (`22`), List (`[80, 443]`), Range (`49152:49251`)

## Examples

**Basic:**

```yaml
meta:
  name: firewall_basic
spec:
  allow:
    - direction: in
      proto: tcp
      port: 22
      comment: SSH
```

**Default Deny:**

```yaml
meta:
  name: firewall_secure
spec:
  reset: true
  defaults:
    - direction: in
      permission: deny
      comment: Deny all incoming
  allow:
    - direction: out
      proto: udp
      port: 53
      comment: DNS
    - direction: in
      proto: tcp
      port: 80
      comment: HTTP
```

**Internal Network:**

```yaml
spec:
  allow:
    - direction: in
      proto: tcp
      port: 5432
      from: 10.0.0.0/24
      comment: Database from internal
```

**Common Patterns:**

```yaml
# Essential services
allow:
  - direction: out
    proto: udp
    port: 53
    comment: DNS
  - direction: in
    interface: lo
    comment: Loopback

# Web services
allow:
  - direction: in
    proto: tcp
    port: [80, 443]
    comment: HTTP/HTTPS

# Cluster communication
allow:
  - direction: in
    proto: tcp
    port: 2377
    from: 10.0.0.0/23
    comment: Cluster management
```

## Rule Processing

1. **Reset** (if `reset: true`) - Flush existing rules
2. **Defaults** - Applied first (deny-all policies)
3. **Deny** - Explicit deny rules
4. **Allow** - Allow rules (last)

## Best Practices

- **Use reset cautiously:** Ensure defaults provide baseline security
- **Default deny-all:** Start with deny policies for defense-in-depth
- **Specific CIDR:** Limit traffic to specific networks
- **Comment rules:** Makes config auditable
- **Naming:** Lowercase with underscores (`firewall_docker_swarm`)
- **Group rules:** Organize by service/function