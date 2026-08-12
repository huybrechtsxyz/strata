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
          value: <value>       # one of: literal record value
          var: <key>           # one of: variable key from spec.references.variables
          secret: <key>        # one of: secret key from spec.references.secrets
          output_key: <key>    # one of: a preceding deployment stage's output name
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

| Field        | Type   | Required | Description                                                                                            |
| ------------ | ------ | -------- | ------------------------------------------------------------------------------------------------------ |
| `name`       | string | Yes      | Hostname, subdomain, or `@` for the zone apex.                                                         |
| `type`       | string | Yes      | Record type — see table below.                                                                         |
| `value`      | string | one of   | Literal record value. Written directly to `dns.auto.tfvars.json`.                                      |
| `var`        | string | one of   | Variable key from `spec.references.variables` — resolved at build time.                                |
| `secret`     | string | one of   | Secret key from `spec.references.secrets` — injected at deploy time via `TF_VAR_*`.                    |
| `output_key` | string | one of   | A preceding deployment stage's output name (e.g. a VM's public IP). Not declared in `spec.references`. |
| `ttl`        | int    | No       | Per-record TTL. Overrides zone-level `ttl` if set.                                                     |
| `priority`   | int    | No       | Required for MX and SRV; invalid on all other types.                                                   |

> Exactly one of `value`, `var`, `secret`, or `output_key` must be set per record.

### `output_key:` — sourcing a record from a preceding stage's output

Use `output_key:` to point a record at the output of an earlier deployment stage instead of a
static `value:` — the common case is an `A`/`AAAA` record pointing at a VM's dynamically
assigned public IP:

```yaml
records:
  - name: "@"
    type: A
    output_key: hearth_public_ip   # matches an `output "hearth_public_ip" {}` in the infra stage
```

- **Not subject to `spec.references`** — unlike `var:`/`secret:`, `output_key:` doesn't name an
  environment-declared value, so it is never required (or allowed) in `spec.references`.
- **Never resolved into `dns.auto.tfvars.json` / `strata_dns.yml`** — build time happens before
  any stage has applied, so the value can't exist yet. The record's coordinates are instead
  bucketed into `dns_output_records` / `strata_dns_output_records` (see below).
- **Only works within a single `strata deploy run` invocation**, where the DNS stage depends on
  (and runs after) the stage that produced the output. It relies on strata's existing generic
  stage-output injection — every resolved stage output is already auto-injected into every
  subsequent stage's subprocess environment as `TF_VAR_<output_key>` (Terraform) or a bare
  `<output_key>` env var (Ansible/Compose) — so the consuming Terraform module or playbook reads
  it directly (`var.hearth_public_ip` / `lookup('env', 'hearth_public_ip')`).

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

Running `strata build run` generates the following files in the build directory:

```
.strata/build/<deployment>/
  terraform/
    dns.auto.tfvars.json              ← Terraform: dns_zones variable
    dns_secret_records.auto.tfvars.json  ← Terraform: secret record stubs (only when secrets present)
    dns_output_records.auto.tfvars.json  ← Terraform: output record stubs (only when output_key: present)
  ansible/
    strata_dns.yml                    ← Ansible: strata_dns_zones variable
    strata_dns_secrets.yml            ← Ansible: strata_dns_secret_records (only when secrets present)
    strata_dns_outputs.yml            ← Ansible: strata_dns_output_records (only when output_key: present)
```

### Terraform — `dns.auto.tfvars.json`

Contains one top-level variable `dns_zones` — a map keyed by the DNS config name. Your
Terraform DNS module declares `variable "dns_zones" {}` and loads it automatically via the
`*.auto.tfvars.json` convention.

```json
{
  "dns_zones": {
    "haven_zones": {
      "description": "Haven DNS zones — managed via INWX",
      "labels": {"owner": "vincent"},
      "tags": [],
      "provider": "inwx",
      "zones": {
        "huybrechts.xyz": {
          "ttl": 3600,
          "records": [
            {"name": "@",    "type": "A",     "value": "1.2.3.4",          "ttl": null, "priority": null},
            {"name": "www",  "type": "CNAME", "value": "huybrechts.xyz.",   "ttl": null, "priority": null},
            {"name": "@",    "type": "MX",    "value": "mail.protonmail.ch.", "ttl": null, "priority": 10},
            {"name": "@",    "type": "TXT",   "value": "v=spf1 include:...", "ttl": null, "priority": null},
            {"name": "@",    "type": "TXT",   "value": null,                "ttl": null, "priority": null}
          ]
        }
      }
    }
  }
}
```

- `value:` records are written literally.
- `var:` records are resolved from `environment.yaml` at build time and written as the resolved string.
- `secret:` records are emitted with `"value": null` — the actual value is never written to disk.
- `output_key:` records are also emitted with `"value": null` — the value only exists once a
  preceding stage has applied, which build time cannot know about.

### Terraform — `dns_secret_records.auto.tfvars.json`

Only written when at least one record uses `secret:`. Contains record stubs keyed by
`{record_name}_{record_type}` so your Terraform module can look up the secret value at apply
time via `TF_VAR_<secret_key>`.

```json
{
  "dns_secret_records": {
    "haven_zones": {
      "huybrechts.xyz": {
        "@_TXT": {
          "name": "@",
          "type": "TXT",
          "secret_key": "google_verify_token",
          "ttl": null,
          "priority": null
        }
      }
    }
  }
}
```

Declare the matching variable in your Terraform module and mark it sensitive:

```
variable "dns_secret_records" {
  type      = any
  sensitive = true
  default   = {}
}
```

At deploy time, set `TF_VAR_google_verify_token=<value>` so Terraform can resolve it.

### Terraform — `dns_output_records.auto.tfvars.json`

Only written when at least one record uses `output_key:`. Contains record stubs keyed by
`{record_name}_{record_type}`, structurally identical to `dns_secret_records.auto.tfvars.json`
but carrying `output_key` instead of `secret_key`.

```json
{
  "dns_output_records": {
    "haven_zones": {
      "huybrechts.xyz": {
        "@_A": {
          "name": "@",
          "type": "A",
          "output_key": "hearth_public_ip",
          "ttl": null,
          "priority": null
        }
      }
    }
  }
}
```

No new Terraform variable declaration is required to *consume* the value — every resolved stage
output is already auto-injected as `TF_VAR_<output_key>` into every subsequent stage's `terraform
apply` (the same generic mechanism used for `stage_outputs` everywhere else in strata). Declare
`variable "hearth_public_ip" {}` in the DNS stage's Terraform module and reference `var.hearth_public_ip`
directly — `dns_output_records` exists purely so the module knows *which record* needs *which*
output key; use `tf_required_variables.json`-style tooling or read this file to wire the two
together. This only resolves within a single `strata deploy run` invocation where the DNS stage
depends on (and runs after) the stage that produced the output.

### Ansible — `strata_dns.yml`

Written only when DNS zones are present. Contains one top-level variable `strata_dns_zones`
with the same map structure as Terraform. Load it with `vars_files` or place it in your
`group_vars/` directory.

```yaml
strata_dns_zones:
  haven_zones:
    description: "Haven DNS zones — managed via INWX"
    labels:
      owner: vincent
    tags: []
    provider: inwx
    zones:
      huybrechts.xyz:
        ttl: 3600
        records:
          - {name: "@",   type: A,     value: "1.2.3.4",            ttl: null, priority: null}
          - {name: www,   type: CNAME, value: "huybrechts.xyz.",     ttl: null, priority: null}
          - {name: "@",   type: MX,    value: "mail.protonmail.ch.", ttl: null, priority: 10}
          - {name: "@",   type: TXT,   value: "v=spf1 include:...",  ttl: null, priority: null}
```

- `value:` records are written literally.
- `var:` records are resolved from `environment.yaml` at build time and written as the resolved
  string, same as Terraform. If the variable has no resolved value, the `value` key is omitted
  from the record (rather than the record being silently blank) and a build message is emitted.
- `secret:` records never have their value written to `strata_dns.yml` — instead their
  coordinates are written to `strata_dns_secrets.yml` (see below).
- `output_key:` records behave the same way — coordinates are written to `strata_dns_outputs.yml`
  (see below) instead of `strata_dns.yml`.

### Ansible — `strata_dns_secrets.yml`

Only written when at least one record uses `secret:`. Contains record stubs keyed by
`{record_name}_{record_type}`, mirroring Terraform's `dns_secret_records.auto.tfvars.json`, so
playbooks can look up the actual secret value from the process environment at runtime —
`AnsibleDeployer` already injects every resolved secret verbatim (unprefixed) into the
`ansible-playbook` subprocess environment.

```yaml
strata_dns_secret_records:
  haven_zones:
    huybrechts.xyz:
      "@_TXT":
        name: "@"
        type: TXT
        secret_key: google_verify_token
        ttl: null
        priority: null
```

```yaml
- name: Resolve DNS secret record values
  set_fact:
    dns_secret_values: >-
      {{ dns_secret_values | default({}) | combine({
        item.key: lookup('env', item.value.secret_key)
      }) }}
  loop: "{{ strata_dns_secret_records.haven_zones['huybrechts.xyz'] | dict2items }}"
```

### Ansible — `strata_dns_outputs.yml`

Only written when at least one record uses `output_key:`. Contains record stubs keyed by
`{record_name}_{record_type}`, structurally identical to `strata_dns_secrets.yml` but carrying
`output_key` instead of `secret_key`. Every resolved stage output is already injected verbatim
(unprefixed) into the `ansible-playbook` subprocess environment by `AnsibleDeployer`, so a
playbook resolves the value the same way it resolves secrets:

```yaml
strata_dns_output_records:
  haven_zones:
    huybrechts.xyz:
      "@_A":
        name: "@"
        type: A
        output_key: hearth_public_ip
        ttl: null
        priority: null
```

```yaml
- name: Resolve DNS output record values
  set_fact:
    dns_output_values: >-
      {{ dns_output_values | default({}) | combine({
        item.key: lookup('env', item.value.output_key)
      }) }}
  loop: "{{ strata_dns_output_records.haven_zones['huybrechts.xyz'] | dict2items }}"
```

This only resolves within a single `strata deploy run` invocation where the DNS stage depends on
(and runs after) the stage that produced the output.

Reference the variable in a playbook task:

```yaml
- name: Ensure DNS records
  community.general.inwx_record:
    domain: "{{ item.0.key }}"
    record: "{{ item.1.name }}"
    type: "{{ item.1.type }}"
    value: "{{ item.1.value }}"
    ttl: "{{ item.1.ttl | default(3600) }}"
  loop: >-
    {{ strata_dns_zones[dns_config].zones | dict2items
       | subelements('value.records') }}
  vars:
    dns_config: haven_zones
```

## Validation Rules

| Rule                                                                                                | Enforcement           |
| --------------------------------------------------------------------------------------------------- | --------------------- |
| `meta.name` must match `^[a-z][a-z0-9_]*$`                                                          | Pydantic / model load |
| Each `dns_zones` entry name must be unique in the workspace                                         | Workspace validator   |
| `priority` is only valid on MX and SRV records                                                      | Model validator       |
| `type` must be one of the nine supported record types                                               | Pydantic / model load |
| CNAME must not be used at the zone apex (`name: "@"`)                                               | Model validator       |
| Exactly one of `value`, `var`, `secret`, or `output_key` must be set per record                     | Model validator       |
| Any key used in `var:` or `secret:` must be declared in `spec.references` (`output_key:` is exempt) | Model validator       |

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
