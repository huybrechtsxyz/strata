# DNS (`kind: dns`)

DNS zones and records. A single file can declare multiple independently-tagged zones.

## Schema

- `spec.provider` — optional DNS provider name (e.g. `cloudflare`, `inwx`, `route53`) — documentation only
- `spec.zones[]`, each:
  - `name` — the domain name (e.g. `example.com`)
  - `ttl` — default TTL in seconds for records in this zone (default `3600`)
  - `records[]`, each `{name, type, value, ttl, priority, description, notes}`
    - `type` — `A`, `AAAA`, `CNAME`, `MX`, `TXT`, `SRV`, `NS`, `PTR`, `CAA`
    - `value` — a literal, or a `${var:}`/`${secret:}`/`${feature:}` token
    - `priority` — only valid for `MX`/`SRV` records
  - `configuration` — raw provisioner passthrough
  - `default_tags` / `custom_tags` — cloud tags
  - `custom` — free-form data

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

- v1 also had an `output_key` field, binding a record's value to a preceding deployment stage's
  provisioner output. Not ported yet — it needs a shared runtime "Context" store that doesn't
  exist in v2 (see ADR-0006).
