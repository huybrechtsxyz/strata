# Kamatera Swarm

Kamatera Cloud platform with **Terraform** (VM provisioning and Docker Swarm bootstrap) and **Docker Compose** modules.
Provisions a Swarm cluster — one manager, two infra workers, two app workers — with Traefik ingress in the EU-FR datacenter.

## Architecture Overview

| Layer      | Tool      | Purpose                                               |
| ---------- | --------- | ----------------------------------------------------- |
| Provider   | —         | Kamatera EU-FR datacenter                             |
| Firewall   | Terraform | Swarm cluster inbound/outbound rules                  |
| Resources  | Terraform | Manager VM, infrastructure worker VMs, app worker VMs |
| Namespace  | Compose   | Base namespace (Traefik ingress)                      |
| Deployment | —         | Production instance                                   |

---

## Configuration

Includes a custom provider schema that validates Kamatera VM CPU/RAM/billing constraints, and a `docker_swarm` topology type with manager/worker role rules.

```{literalinclude} ../../config/kamatera-swarm/config/kamatera-swarm-config.yaml
:language: yaml
```

---

## Workspace

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-ws-platform.yaml
:language: yaml
```

---

## Provider

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-provider-eu-fr.yaml
:language: yaml
```

---

## Firewall

Declarative security rules applied to the Swarm cluster nodes.

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-fw-base.yaml
:language: yaml
```

---

## Resources

### Swarm Manager

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-res-vm-manager.yaml
:language: yaml
```

### Infrastructure Workers

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-res-vm-infra.yaml
:language: yaml
```

### Application Workers

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-res-vm-worker.yaml
:language: yaml
```

---

## Namespace

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-ns-base.yaml
:language: yaml
```

---

## Module

### Traefik Ingress

```{literalinclude} ../../config/kamatera-swarm/stack/kamatera-mod-traefik.yaml
:language: yaml
```

---

## Environment

```{literalinclude} ../../config/kamatera-swarm/environments/kamatera-swarm-env-prd.yaml
:language: yaml
```

---

## Deployment

```{literalinclude} ../../config/kamatera-swarm/deploy/kamatera-swarm-deploy-prd.yaml
:language: yaml
```
