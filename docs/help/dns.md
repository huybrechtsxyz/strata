# DNS

Domain Name System zone configuration and record management.

A DNS (`kind: dns`) document defines:
- **Zone** — domain name (e.g., `example.com`)
- **Records** — A, AAAA, CNAME, MX, TXT, SRV, NS entries
- **Providers** — Route53, Azure DNS, Cloud DNS, Cloudflare, etc.
- **TTL and routing** — standard or geolocation-based

DNS configurations are cloud-agnostic YAML that translate to provider-specific
DNS management systems.

---

## Basic Structure

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: dns
meta:
  name: example-com
spec:
  zone: example.com
  provider: route53
  
  records:
    # Root / apex
    - name: ''
      type: A
      value: 203.0.113.1
      ttl: 300
    
    # Subdomains
    - name: www
      type: A
      value: 203.0.113.1
      ttl: 300
    
    - name: api
      type: A
      value: 203.0.113.2
      ttl: 300
    
    # Mail
    - name: ''
      type: MX
      value: 10 mail.example.com
      ttl: 3600
    
    # TLS verification
    - name: _acme-challenge
      type: TXT
      value: acme-verification-string
      ttl: 60
```

---

## Geolocation Routing

Route traffic by location:

```yaml
spec:
  records:
    - name: api
      type: A
      routing:
        geolocation:
          region: us
          value: 203.0.113.2
        geolocation:
          region: eu
          value: 203.0.113.3
        default: 203.0.113.1
```

---

## Weighted Load Balancing

Distribute traffic across endpoints:

```yaml
spec:
  records:
    - name: api
      type: A
      routing:
        weighted:
          - weight: 80
            value: 203.0.113.2
          - weight: 20
            value: 203.0.113.3
```

---

## Health Checks

Monitor endpoint health:

```yaml
spec:
  records:
    - name: api
      type: A
      value: 203.0.113.1
      health_check:
        protocol: https
        path: /health
        interval: 30
        failure_threshold: 3
```

---

## Environment-Specific Records

Override per environment:

```yaml
# environments/production.yaml
spec:
  dns_overrides:
    example-com:
      records:
        - name: api
          type: A
          value: prod-api.example.com
```

---

## See Also

- `provider` — DNS provider (Route53, Azure DNS, etc.)
- `workspace` — top-level blueprint containing DNS
