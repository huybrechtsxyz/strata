# Kind Gap Analysis — Networking & Related
_Date: 2026-06-09_

---

## Current Kinds (10)

| Kind             | What it models                                                             |
| ---------------- | -------------------------------------------------------------------------- |
| `configuration`  | Global platform config — providers, integrations, repos, security policies |
| `deployment`     | Ties everything together — stages, provisioners, approvals, health checks  |
| `environment`    | Per-env overrides — variables, secrets, features, resource overrides       |
| `workspace`      | Topology — providers, resources, namespaces, firewalls, modules            |
| `provider`       | Cloud/infra provider definition — type, region, auth                       |
| `resource`       | Infrastructure unit — VM, DB, storage — with disks, volumes, dependencies  |
| `namespace`      | Logical grouping — modules within a namespace                              |
| `module`         | Application/service config — Helm chart, compose, script                   |
| `firewall`       | Network rules — direction, protocol, port, CIDR                            |
| `platform_model` | Internal build artifact                                                    |

---

## Identified Gaps

### 1. `dns` — DNS zones and records
Same pattern as `firewall`. Structured records (A, CNAME, MX, TXT, SRV) with validation.
Triggered by: haven moving to INWX, which has a Terraform provider (`inwx/inwx`).

> Done. See `docs/config/dns.md` for the new reference doc, and `test_models_dns.py` for the new model tests.

### 3. `network` — Network topology / VNet / subnet definitions
Resources have a `category: networking` dependency mechanism, but no first-class kind for
declaring the actual network layout (CIDRs, subnets, peerings, route tables). Currently
buried in unvalidated resource `configuration` blobs.

### 4. `backup` / `schedule` — Backup policies and scheduled operations
Retention policies, schedules, targets. Currently nowhere in the model — everyone handles
this in raw Terraform. Cross-cuts multiple resource types.

### 5. `monitoring` / `observability` — Alerting rules, dashboards, log forwarding
Strata already has health checks on deployment stages, but no way to declare persistent
monitoring — alerting thresholds, log destinations, uptime checks. Config that drifts
if not declared declaratively.

---

## Borderline / Not Yet Needed

- **`user` / `identity`** — IAM roles, service accounts. Usually provider-specific enough to stay in Terraform.
- **`ingress` / `loadbalancer`** — Often a module concern (Traefik, nginx). Modules handle this today.
- **`storage`** — Already modeled inside `resource`. Extracting would be over-engineering.


### 2. `certificate` / `tls` — Certificate definitions
Certs are referenced implicitly via secrets today, but they have their own lifecycle:
issuer, domain, SANs, expiry, renewal. Let's Encrypt / ACME, Azure Key Vault certs — all
structured, all validatable. Haven needs this for Traefik TLS termination.
`dns` and `certificate` are a natural pair — ACME DNS-01 challenges tie them directly.

---

## Priority for Haven

| Priority | Kind          | Reason                                              |
| -------- | ------------- | --------------------------------------------------- |
| 1        | `dns`         | INWX move — immediate need                          |
| 2        | `certificate` | Traefik TLS / Let's Encrypt — natural pair with dns |
| 3        | `network`     | Subnet/CIDR validation catches real drift bugs      |
| 4        | `backup`      | Nice to have, not urgent                            |
| 5        | `monitoring`  | Important but hardest to do generically             |
