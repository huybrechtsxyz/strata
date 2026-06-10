# DNS Configuration

Defines DNS zones and records managed as code. YAML files declare zones, record sets, and TTL
values — validated by strata and built into `dns.auto.tfvars.json` for Terraform consumption.

## When to Use

Use the `dns` kind when you want to:

- Manage DNS zones and records as code alongside your infrastructure
- Validate record structure before applying to a provider (INWX, Cloudflare, Route53, etc.)
- Keep DNS configuration in the same YAML workflow as firewalls and namespaces

## Schema

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: dns
meta:
  name: <resource_name>   # Required: ^[a-z][a-z0-9_]*$
  annotations:
    description: <description>
  labels:
    version: "<version>"
spec:
  provider: <provider>    # Optional: inwx, cloudflare, route53, etc.
  references:             # Optional: declare keys used by var/secret records
    variables:
      - <var_key>         # Resolved from environment variables at build time
    secrets:
      - <secret_key>      # Injected as TF_VAR_<key> at deploy time
  zones:
    - name: <zone>        # Required: fully-qualified domain name
      ttl: <seconds>      # Optional: default TTL for all records in this zone
      records:
        - name: <record>  # Required: hostname or "@" for the zone apex
          type: <type>    # Required: A, AAAA, CNAME, MX, TXT, SRV, NS, PTR, CAA
          value: <value>  # one of: literal record value
          var: <key>      # one of: variable key from spec.references.variables
          secret: <key>   # one of: secret key from spec.references.secrets
          ttl: <seconds>  # Optional: per-record override of the zone TTL
          priority: <n>   # Optional: MX and SRV records only
```

## Top-level Fields

| Field             | Type   | Required | Description                                                                              |
| ----------------- | ------ | -------- | ---------------------------------------------------------------------------------------- |
| `spec.provider`   | string | No       | DNS provider hint (`inwx`, `cloudflare`, `route53`, …).                                  |
| `spec.references` | object | No       | Variable and secret key declarations. Required when any record uses `var:` or `secret:`. |
| `spec.zones`      | array  | Yes      | One or more zone definitions.                                                            |

## spec.references Fields

Declares the variable and secret keys that records in this file may reference. Required when
any record uses `var:` or `secret:`.

| Field       | Type            | Required | Description                                             |
| ----------- | --------------- | -------- | ------------------------------------------------------- |
| `variables` | list of strings | No       | Keys resolved from environment variables at build time. |
| `secrets`   | list of strings | No       | Keys injected as `TF_VAR_<key>` at deploy time.         |

## Zone Fields

| Field     | Type   | Required | Description                              |
| --------- | ------ | -------- | ---------------------------------------- |
| `name`    | string | Yes      | Fully-qualified domain name of the zone. |
| `ttl`     | int    | No       | Default TTL (seconds) for all records.   |
| `records` | array  | No       | List of DNS records for this zone.       |

## Record Fields

| Field      | Type   | Required | Description                                                                         |
| ---------- | ------ | -------- | ----------------------------------------------------------------------------------- |
| `name`     | string | Yes      | Hostname, subdomain, or `@` for the zone apex.                                      |
| `type`     | string | Yes      | Record type — see table below.                                                      |
| `value`    | string | one of   | Literal record value. Written directly to `dns.auto.tfvars.json`.                   |
| `var`      | string | one of   | Variable key from `spec.references.variables` — resolved at build time.             |
| `secret`   | string | one of   | Secret key from `spec.references.secrets` — injected at deploy time via `TF_VAR_*`. |
| `ttl`      | int    | No       | Per-record TTL. Overrides zone-level `ttl` if set.                                  |
| `priority` | int    | No       | Required for MX and SRV; invalid on all other types.                                |

> Exactly one of `value`, `var`, or `secret` must be set per record.

## Record Type Reference

| Type    | Value format                                       | Notes                                                  |
| ------- | -------------------------------------------------- | ------------------------------------------------------ |
| `A`     | IPv4 address (`1.2.3.4`)                           |                                                        |
| `AAAA`  | IPv6 address (`2001:db8::1`)                       |                                                        |
| `CNAME` | Fully-qualified target with trailing dot (`host.`) | Cannot be used at the zone apex (`@`).                 |
| `MX`    | Fully-qualified mail server with trailing dot      | Requires `priority`. Multiple MX records are additive. |
| `TXT`   | Quoted string (`"v=spf1 …"`)                       | SPF, DKIM, DMARC, and domain-verification records.     |
| `SRV`   | `weight port target` (`10 443 svc.example.com.`)   | Requires `priority`. Name format: `_svc._proto`.       |
| `NS`    | Fully-qualified nameserver with trailing dot       | Delegation records. Usually pre-set by your registrar. |
| `PTR`   | Fully-qualified reverse target with trailing dot   | Reverse-DNS; use inside `in-addr.arpa` zones.          |
| `CAA`   | `flags tag "value"` (`0 issue "letsencrypt.org"`)  | Restricts which CAs may issue certificates.            |

**Trailing dot:** CNAME, MX, NS, PTR, and SRV values must end with `.` to indicate a
fully-qualified name. Omitting it may cause the provider to append the zone name automatically.

## Example

A complete zone for a production domain with the most common record types:

```yaml
apiVersion: strata.huybrechts.xyz/v1
kind: dns
meta:
  name: haven_zones
  annotations:
    description: Haven DNS zones — managed via INWX
  labels:
    owner: vincent
    version: "1.0.0"
spec:
  provider: inwx
  references:
    variables:
      - extra_spf_include       # resolved at build time from environment.yaml
    secrets:
      - google_verify_token     # injected at deploy time via TF_VAR_google_verify_token
  zones:
    - name: huybrechts.xyz
      ttl: 3600
      records:
        # Apex A record — points the root domain to the server
        - name: "@"
          type: A
          value: "1.2.3.4"

        # WWW subdomain
        - name: www
          type: CNAME
          value: "huybrechts.xyz."

        # Mail routing (Proton Mail — higher priority = preferred)
        - name: "@"
          type: MX
          value: "mail.protonmail.ch."
          priority: 10
        - name: "@"
          type: MX
          value: "mailsec.protonmail.ch."
          priority: 20

        # SPF — value sourced from extra_spf_include variable at build time
        - name: "@"
          type: TXT
          var: extra_spf_include

        # Google domain verification — secret injected at deploy time
        - name: "@"
          type: TXT
          secret: google_verify_token

        # DMARC — quarantine messages failing SPF/DKIM
        - name: _dmarc
          type: TXT
          value: "v=DMARC1; p=quarantine; rua=mailto:dmarc@huybrechts.xyz"

        # CAA — only Let's Encrypt may issue certificates
        - name: "@"
          type: CAA
          value: "0 issue \"letsencrypt.org\""
```

## Variable and secret records

Records support three mutually exclusive value sources. Choose based on how sensitive and
environment-specific the record value is.

| Source    | Use when                                                   | Build behaviour                                                                           |
| --------- | ---------------------------------------------------------- | ----------------------------------------------------------------------------------------- |
| `value:`  | The record value is the same in every environment          | Written literally to `dns.auto.tfvars.json`                                               |
| `var:`    | The value varies by environment but is not sensitive       | Resolved from `environment.yaml` at `strata build run`; written to `dns.auto.tfvars.json` |
| `secret:` | The value is sensitive (API key, verification token, etc.) | **Not** written to `dns.auto.tfvars.json`; injected at deploy time as `TF_VAR_<key>`      |

### var: — build-time variable resolution

When a record uses `var: <key>`, strata resolves `<key>` from the `spec.variables` section of
your active `environment.yaml` at `strata build run` and writes the resolved value into
`dns.auto.tfvars.json`. Declare the key in both places:

1. `spec.references.variables` in the DNS YAML file
2. `spec.variables` in the matching `environment.yaml`

### secret: — deploy-time injection

When a record uses `secret: <key>`, the record is emitted into a separate Terraform variable
(`dns_secret_records`) and is **not** written to `dns.auto.tfvars.json`. The value is never
stored on disk by strata. Instead, declare the secret in `environment.yaml` under `spec.secrets`
and ensure it is available as `TF_VAR_<key>` when Terraform runs.

Your Terraform DNS module must declare a dedicated variable for secret records:

```hcl
variable "dns_secret_records" {
  type      = map(string)
  sensitive = true
}
```

Declare the key in both places:

1. `spec.references.secrets` in the DNS YAML file
2. `spec.secrets` in the matching `environment.yaml`

## Linking to a Workspace

Reference one or more DNS zone files from your workspace YAML using `spec.dns_zones`:

```yaml
# workspace.yaml
spec:
  dns_zones:
    - name: haven_zones
      file: "@haven-config/dns/haven-zones.yaml"
```

| Field  | Type   | Required | Description                                             |
| ------ | ------ | -------- | ------------------------------------------------------- |
| `name` | string | Yes      | Unique name. Other workspace files reference this name. |
| `file` | string | Yes      | Path to the DNS YAML file. Supports `@repo-name/` refs. |

Names must be unique across all `dns_zones` entries in the workspace.

## Build Output

Running `strata build run` generates `dns.auto.tfvars.json` in the build directory:

```
.strata/build/<deployment>/
  dns.auto.tfvars.json    ← consumed by your Terraform DNS module
  firewalls.auto.tfvars.json
  platform.json
  ...
```

The file serialises the full zone and record list as a JSON variable file. Your Terraform
module reads it via a `variable "dns_zones" { ... }` declaration and a `*.auto.tfvars.json`
auto-load. Pass the build directory to Terraform — strata places all generated
`*.auto.tfvars.json` files there alongside the other artifacts.

## Validation Rules

| Rule                                                                      | Enforcement           |
| ------------------------------------------------------------------------- | --------------------- |
| `meta.name` must match `^[a-z][a-z0-9_]*$`                                | Pydantic / model load |
| Each `dns_zones` entry name must be unique in the workspace               | Workspace validator   |
| `priority` is only valid on MX and SRV records                            | Model validator       |
| `type` must be one of the nine supported record types                     | Pydantic / model load |
| CNAME must not be used at the zone apex (`name: "@"`)                     | Model validator       |
| Exactly one of `value`, `var`, or `secret` must be set per record         | Model validator       |
| Any key used in `var:` or `secret:` must be declared in `spec.references` | Model validator       |

## Best Practices

- **Use trailing dots** on CNAME, MX, NS, PTR, and SRV values — always fully qualify targets.
- **One file per domain group** keeps diffs readable; group logically related zones when you
  manage many small delegated subdomains in a single file.
- **Set zone-level TTL** as a baseline, then override per record only where the caching
  requirement genuinely differs (e.g. low TTL for a record you update frequently).
- **Comment records** with their purpose (SPF selector, DKIM key id, etc.) using YAML inline
  comments — strata does not carry comments into the build output, but they stay in source for
  reviewers.
- **Naming:** Lowercase with underscores (`haven_zones`, `api_domains`).
