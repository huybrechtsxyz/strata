# DNS (`kind: dns`)

DNS zones and records. A single file can declare multiple independently-tagged zones.

## Schema

- `spec.provider` — optional DNS provider name (e.g. `cloudflare`, `inwx`, `route53`) — documentation only
- `spec.zones[]`, each:
  - `name` — the domain name (e.g. `example.com`)
  - `ttl` — default TTL in seconds for records in this zone (default `3600`)
  - `records[]`, each `{name, type, value, ttl, priority, description, notes}`
    - `type` — `A`, `AAAA`, `CNAME`, `MX`, `TXT`, `SRV`, `NS`, `PTR`, `CAA`
    - `value` — a literal, or a `${var:}`/`${secret:}`/`${feature:}`/`${output:STEP.KEY}`/
      `${value:kind.name.path}` token
    - `priority` — only valid for `MX`/`SRV` records
  - `configuration` — raw provisioner passthrough
  - `default_tags` / `custom_tags` — cloud tags
  - `custom` — free-form data

## Validation

- Within one zone, records are rejected if they exactly duplicate another
  record's `(name, type, value)`, or if a `CNAME` shares its `name` with any
  other record (including another `CNAME`) — invalid in DNS itself, not just
  a strata convention. A record whose `value` still carries an unresolved
  `${...}` token is skipped for the duplicate-value check (its real value
  isn't known until build/deploy time).
- The same zone domain (`name`) declared by two or more documents produces a
  `strata validate` warning (`dns_zone_declared_twice`) — provisioning both
  will likely conflict depending on provider/apply order.

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: dns
meta:
  name: example-dns
spec:
  provider: cloudflare
  zones:
    - name: example.com
      ttl: 3600
      records:
        - name: "@"
          type: A
          value: "203.0.113.1"
        - name: www
          type: A
          value: "${var:PUBLIC_IP}"
      default_tags:
        environment: example
```

## Notes

- v1's `output_key` field (binding a record's value to a preceding deployment stage's provisioner
  output) was not ported as its own field — the same need is already met by `${output:STEP.KEY}`
  in `value`, as long as the zone is claimed by an owning execution step's `targets`.
- Azure-style private DNS zones (VNet links, writing records into a pre-existing/externally-owned
  zone) are deliberately modeled via `configuration`, not dedicated fields — see
  [How To: Model Azure Private DNS Zones](../how-to/azure-private-dns-zones.md).
