# Firewall

Security ruleset for network traffic and resource access.

A firewall (`kind: firewall`) defines:
- **Ingress rules** — who can connect to your resources
- **Egress rules** — where your resources can connect
- **Policy groups** — reusable sets of rules
- **Priority and action** — allow, deny, log

Firewall rules are cloud-agnostic YAML that strata translates to provider-specific
configs (AWS Security Groups, Azure NSGs, GCP Firewall Rules, etc.).

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: firewall
meta:
  name: api-server
spec:
  description: Ingress rules for API server
  ingress:
    - protocol: tcp
      port: 443
      source: 0.0.0.0/0
      description: HTTPS from anywhere
    - protocol: tcp
      port: 80
      source: 0.0.0.0/0
      description: HTTP redirect
  egress:
    - protocol: -1
      destination: 0.0.0.0/0
      description: Allow all outbound
```

---

## Groups and Reuse

Define rule groups for reuse:

```yaml
spec:
  rule_groups:
    ssh_internal:
      - protocol: tcp
        port: 22
        source: 10.0.0.0/8

  ingress:
    - group: ssh_internal
    - protocol: tcp
      port: 443
      source: 0.0.0.0/0
```

---

## Environment-Specific Rules

Override per environment:

```yaml
# firewall/api-server.yaml
spec:
  ingress:
    - protocol: tcp
      port: 443
      source: ${var.allowed_cidr}
```

Then in environments:

```yaml
# environments/staging.yaml
spec:
  variables:
    allowed_cidr: 10.0.0.0/8      # internal only
```

```yaml
# environments/production.yaml
spec:
  variables:
    allowed_cidr: 0.0.0.0/0       # public internet
```

---

## Policy Integration

Combine with policies:

```yaml
spec:
  policies:
    - type: required_tags
      tags: [Environment, Owner]
    - type: firewall_audit
      action: log_and_alert
```

---

## See Also

- `network` — VPC/VNet topology
- `policies` — governance rules
