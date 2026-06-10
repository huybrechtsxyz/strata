# Decisions

Canonical record of architectural decisions made by the squad.

---

## AD-NET-1: CidrSourceModel as reusable union type

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Dedicated `CidrSourceModel(value | var | secret)` rather than inlining three fields. Used in two positions: `address_space` (list) and `subnet.cidr` (single). Avoids duplication and makes union validation DRY.

---

## AD-NET-2: Subnets required per network

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`min_length=1` on subnets. A network without subnets has no operational value — resources reference subnets, not bare networks.

---

## AD-NET-3: Peering as lightweight reference

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`(name, target)` pairs only. No routing config, no gateway settings. Strata declares intent; Terraform implements. Keeps model ~3 lines per peering.

---

## AD-NET-4: Qualified subnet references

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`<network_name>/<subnet_name>` string on `WorkspaceResourceModel.subnet`. Explicit, grep-friendly, trivially splittable. Dot notation rejected (PlatformName regex conflict).

---

## AD-NET-5: CIDR overlap severity

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Warning for non-peered networks. Hard error for mutually-peered networks. Non-peered may legitimately overlap (isolated envs); peered overlap fails at provider level.

---

## AD-NET-6: Deferred CIDR validation for var/secret

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Parse-time validates syntax only. Build-time re-validates after variable injection. Models load without environment context.

---

## AD-NET-7: DNS-style merge strategy

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Network merge by name (last-wins). Subnet merge by `(network, subnet)` replacement. Post-merge CIDR re-validation.

---

## AD-NET-8: Separate tfvars file

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

`networks.auto.tfvars.json` — consistent with `dns.auto.tfvars.json`, `firewalls.auto.tfvars.json`.

---

## AD-NET-9: No provider field

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Networks inherit provider from workspace topology. No redundant `spec.provider`. Unlike DNS which needs per-zone provider routing.

---

## AD-NET-10: Field name `spec.networks`

**Date:** 2026-06-10 | **Author:** Danny | **Status:** APPROVED

Plural list, consistent with `spec.firewalls`, `spec.dns_zones`, `spec.namespaces`.

---

## INFO: Network kind test patterns

**Date:** 2026-06-10 | **Author:** Livingston | **Status:** INFO

Network tests follow DNS test pattern: inline dict helpers for unit tests, YAML fixtures for integration tests, programmatic construction for merge tests. Non-peered overlap warning (V11) not tested as `ValidationError` — requires service/warning layer.
