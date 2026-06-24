# Hetzner Compose

Hetzner Cloud platform with **Terraform** (VM provisioning) and **Docker Compose** (services).
Provisions a cloud server in Falkenstein with Traefik reverse proxy, an application stack, and INWX DNS records.

## Architecture Overview

| Layer      | Tool      | Purpose                                   |
| ---------- | --------- | ----------------------------------------- |
| Provider   | —         | Hetzner Cloud (Falkenstein DC)            |
| Resource   | Terraform | CX31 cloud server with Ubuntu 24.04       |
| DNS        | Terraform | INWX zone and A records                   |
| Namespace  | Compose   | Services group (Traefik + app + database) |
| Deployment | —         | Production instance                       |

---

## Configuration

```{literalinclude} ../../config/hetzner-compose/config/hetzner-config.yaml
:language: yaml
```

---

## Workspace

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-ws-platform.yaml
:language: yaml
```

---

## Provider

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-provider-fsn1.yaml
:language: yaml
```

---

## Resource

### Application Server

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-res-app.yaml
:language: yaml
```

---

## DNS

INWX DNS zone with records pointing to the server's public IP.

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-dns-inwx.yaml
:language: yaml
```

---

## Namespace

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-ns-services.yaml
:language: yaml
```

---

## Modules

### Traefik Reverse Proxy

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-mod-traefik.yaml
:language: yaml
```

### Application Stack

```{literalinclude} ../../config/hetzner-compose/stack/hetzner-mod-app.yaml
:language: yaml
```

---

## Environment

```{literalinclude} ../../config/hetzner-compose/environments/hetzner-env-prd.yaml
:language: yaml
```

---

## Deployment

```{literalinclude} ../../config/hetzner-compose/deploy/hetzner-deploy-prd.yaml
:language: yaml
```
