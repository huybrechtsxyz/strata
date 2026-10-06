# Firewall (`kind: firewall`)

Security rules — an ordered set of allow/deny rules plus default in/out behavior.

## Schema

- `spec.reset` — if `true`, wipe all existing firewall rules before applying these (default `false`)
- `spec.defaults[]` — baseline `{direction, permission, comment}` rules (`permission`: `allow`/`deny`);
  directions must be unique
- `spec.allow[]` / `spec.deny[]` — explicit rule lists, each a rule:
  - `direction` — `in` or `out`
  - `proto` — `tcp`, `udp`, or `icmp` (required whenever `port` is set)
  - `port` — a single port, a range string (`"80:90"`), or a list of either; not valid for `icmp`
  - `interface` — optional network interface name
  - `from` / `to` — source/destination IP or CIDR: a literal, or a `${var:}`/`${secret:}`/
    `${feature:}` token
  - `comment` — optional documentation
  - the same rule (by signature) cannot appear in both `allow` and `deny`
- `spec.configuration` — raw provisioner passthrough (e.g. Terraform NSG arguments)
- `spec.default_tags` / `spec.custom_tags` — cloud tags
- `spec.custom` — free-form data

## Example

```yaml
apiVersion: strata.huybrechts.xyz/v2
kind: firewall
meta:
  name: web-public
spec:
  defaults:
    - direction: in
      permission: deny
    - direction: out
      permission: allow
  allow:
    - direction: in
      proto: tcp
      port: 443
      from: "0.0.0.0/0"
      comment: "Public HTTPS"
  default_tags:
    environment: example
```

## Notes

- `from`/`to` were v1's proposed-but-never-built `spec.references` gap (ADR-0001) — superseded by
  the same `${var:}`/`${secret:}`/`${feature:}` Value-token syntax every other kind uses, rather
  than a separate hand-authored declared-keys list.
