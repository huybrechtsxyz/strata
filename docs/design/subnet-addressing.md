# Subnet Addressing: strata declares, Terraform or an IPAM allocates — Design

- Status: implemented in 2.11.0 (2026-11-26), Phases 1–5. Approach 5:
  strata declares WHERE (address ranges, and which IPAM is used — strata
  integrates with no IPAM) and HOW (network structure, subnet prefix
  lengths, who creates what); Terraform or an IPAM computes the actual
  subnet addresses. Supersedes Approach 1b (2026-10-08) and the
  short-lived Approach 4 (2026-11-25). Graduated from
  `docs/work/subnet-cidr-derivation.md`. Phase 0's reference module for
  placing sized subnets without an IPAM ships as
  `examples/terraform/azure-subnets` (2026-11-27); its live-Azure checks
  are still open (see "Remaining Work").
- Last updated: 2026-11-27
- User docs: [how-to](../how-to/declare-subnets.md),
  [network](../config/network.md), [workspace](../config/workspace.md).
- Related: [ADR-0007](../decisions/0007-network-model-design-decisions.md)
  (the `NetworkModel`/`SubnetModel` decisions; decision 7 records this
  design's schema changes);
  [firewall-network-cidr-references.md](firewall-network-cidr-references.md)
  (a firewall rule can reference a subnet's `cidr` with `${value:}` — affected
  by this design, see D7). DNS's own sibling doc,
  `dns-value-and-output-binding.md`, resolved and was removed 2026-10-13 —
  its cross-tenant zone-collision idea shipped as `semantic_checks.py`'s
  `_check_dns_records()`.

## Overview

**Subnet CIDRs are entirely hand-calculated today.** Found in practice
adding AKS's `aks_nodes`/`aks_kubeapi` subnets to a spoke deployment
(2026-10-06): the `/25` + `/28` split is mechanical given the parent range,
but every deployment author re-derives the literal `address_prefixes` by
hand and types them into `SubnetModel.cidr`.

### The use case

1. A configuration repository holds the **shared network document**: the
   vnet, its address ranges, and the **core subnets** (firewall, gateway,
   AKS, ...). A network deployment deploys the vnet and the core
   subnets.
2. Deployments keep arriving over time, and **each needs new subnets of a
   given size inside that existing vnet** — declared in the deploying
   workload's own configuration.
3. When the vnet runs out of room, **a range is added to the address
   space** and allocation continues there.
4. The resulting subnet must be created by **whichever Terraform owns it**
   — the network deployment for core subnets, the workload's own Terraform for a
   workload subnet ("mixed" creators).
5. Today there is one shared network document for all environments
   (ranges differ per environment through Value tokens); that may become
   one document per environment later.

### The question that settled it: is allocation a strata responsibility?

No. Working Approach 4 through (2026-11-25) showed that making allocation
safe inside strata means building an IPAM: a committed register, a new
`allocate` command writing into another repository, prune rules, and
concurrent-request handling — with a register that still drifts from what
really exists in Azure. Allocation is shared, stateful and concurrent;
strata works one solution at a time, from configuration to a build.

Strata's job is to **declare, validate and deliver** the network. The
cloud (or an IPAM) is the place that knows which addresses are really
taken.

## Current state

- `SubnetModel.cidr` (`src/strata/models/network_model.py`) is a required
  `str` — a literal CIDR or a `${var:}`/`${secret:}`/`${feature:}` Value
  token (ADR-0007 decision #2). `NetworkDefinitionModel.address_space` is a
  required list of the same; `subnets` requires at least one entry.
- `NetworkDefinitionModel`'s validators (`validate_subnet_cidr_overlap`,
  `validate_subnets_fit_address_space`) and `NetworkSpecModel`'s
  `validate_cross_network_cidr_overlap` only *check* literal CIDRs, and
  skip a whole network as soon as one CIDR carries a Value token — on the
  stdlib `ipaddress` module.
- Subnets reach Terraform as a **map keyed by subnet name**
  (`_strata_networks()` in `src/strata/integrations/terraform_projection.py`:
  `strata.networks.<doc>.networks.<net>.subnets.<subnet> = {cidr,
  description, configuration, custom}`).
- A workspace lists Network **documents** in `spec.networks`. A workspace
  resource can *reference* a subnet (`WorkspaceResourceSubnetModel`:
  `{network: <document>, subnet: <name>}`); Phase 2
  (`WorkspaceService.validate_resource_subnets()`, code
  `unregistered_subnet`) checks the name against the union of every
  network definition's subnets in that document. A workload has no way to
  *declare* a subnet of its own.
- A firewall rule can take a subnet's address with
  `${value:network.<doc>.spec.networks.<net>.subnets.<subnet>.cidr}`
  (firewall-network-cidr-references.md).

## Decision (2026-11-26) — Approach 5: WHERE and HOW in strata, addresses downstream

| | Who | What |
|---|---|---|
| **WHERE** | strata (configuration) | The address ranges (`address_space: [10.10.0.0/22]`), and/or **which IPAM is used** (type, pool, settings). Strata does not integrate with any IPAM: it only passes that information on to the provisioner (Terraform/OpenTofu, Ansible once ported). |
| **HOW** | strata (configuration) | The structure: vnets, which subnets exist, their **prefix length** (or a literal address when wanted), peerings, and who creates each subnet. |
| **Addresses** | Terraform / IPAM | The actual CIDR of every `prefix_length:` subnet. |

- **With an IPAM**, the provisioner's own code asks it for addresses at
  apply; the IPAM stores the allocation and is the register across
  deployments. **Strata does not integrate with any IPAM** (decided
  2026-11-26): no API calls, no IPAM-specific rules. It only declares which
  IPAM a network uses and passes that on (D2).
- **Without an IPAM, the solution lives in Terraform, and the existing
  vnet is the register**: a reference module reads the vnet's current
  subnets, keeps every existing subnet's address, and places new ones
  first-fit (D6).
- Workloads declare their own subnets in their own workspace — no PR to
  the network owner, because the register is downstream, not in strata.
- Growing the address space stays the network owner's configuration
  change.

## Design

### D1. Subnet: a fixed address or a prefix length

`SubnetModel` takes **exactly one of** `cidr` and `prefix_length`:

```yaml
subnets:
  - { name: firewall, cidr: "${var:fw_subnet}" }   # fixed, as today (Azure name set by the module, V12)
  - { name: aks_nodes,   prefix_length: 25 }                  # allocated downstream
  - { name: aks_kubeapi, prefix_length: 28 }
```

- `cidr: str | None` — unchanged meaning (literal or Value token).
- `prefix_length: int | None` — the block size as a prefix length (`27`
  means a `/27`). Named `prefix_length` rather than `size`, which reads as
  an address count. Range 1–128; when the network's ranges are literals,
  it must not be shorter than the largest range's prefix (a `/21` cannot
  go into `/22` ranges) and must match the ranges' IP version.
- Model validator `validate_subnet_address` (in `SubnetModel`): exactly one
  of the two — error "subnet 'x' needs exactly one of 'cidr' or
  'prefix_length'".
- `NetworkDefinitionModel.subnets` becomes optional (default `[]`): a vnet
  may hold only workload subnets.

### D2. Network definition: address ranges, and which IPAM is used

Strata does **not integrate with an IPAM** — no API calls, no
IPAM-specific rules. A network definition can only *say* which IPAM
allocates its addresses, so the provisioner's code (Terraform/OpenTofu
today, Ansible once it is ported to v2) knows what to call. It takes
`address_space`, `ipam`, or both — **at least one**:

```yaml
networks:
  - name: vnet-main
    address_space: ["${var:vnet_space}"]       # as today; no IPAM: the vnet is the register (D6)
  - name: vnet-spoke
    ipam:
      type: azure-avnm                         # which IPAM: a free name, never interpreted by strata
      pool: "${var:ipam_pool_id}"              # optional, a Value binding (differs per environment)
      prefix_length: 22                        # optional, the size the network asks the IPAM for
      configuration: { ... }                   # optional, free-form passthrough for the provisioner
  - name: vnet-hub
    address_space: ["10.0.0.0/16"]            # both: the range the IPAM manages, known up front
    ipam: { type: netbox, configuration: { prefix_id: 42 } }
```

- `address_space: list[str] | None` (min 1 entry when given).
- `ipam: NetworkIpamModel | None`:
  - `type: PlatformName` — required; which IPAM (`azure-avnm`, `netbox`,
    `infoblox`, `phpipam`, `aws-vpc-ipam`, ... — not an enum, strata never
    branches on it);
  - `pool: str | None` — Value binding;
  - `prefix_length: int | None`;
  - `configuration: dict[str, Any] | None` — passed through as-is, like
    every other `configuration` in the network model.
- Model validator: at least one of `address_space` and `ipam`.
- When `address_space` is given, strata's literal-range checks (D7 #3–5)
  use it whether or not an IPAM is named. When it is not (or is a token),
  checks 4 and 5 use the block the network asks the IPAM for
  (`ipam.prefix_length`, added 2026-11-27): no subnet larger than it, no
  more addresses than it holds. Without either, they do not run.
- What the IPAM allows (for example Azure's reported refusal to mix pool
  allocation and static prefixes in one vnet, V6) is the provisioner
  code's concern, not a strata rule.

### D3. Workload subnets: `spec.subnets[]` on the workspace

```yaml
# workload solution: workspace
spec:
  networks: [main-network]
  subnets:
    - { network: main-network, virtual_network: vnet-main, name: app1-pe,   prefix_length: 27 }
    - { network: main-network, virtual_network: vnet-main, name: app1-func, prefix_length: 26 }
  resources:
    - name: app1-storage
      subnet: { network: main-network, subnet: app1-pe }   # unchanged reference shape
```

`WorkspaceSubnetModel`:

| Field | Meaning |
|---|---|
| `network` | The Network **document** (same meaning as `WorkspaceResourceSubnetModel.network`); must be in `spec.networks` (Phase 1). |
| `virtual_network` | Which `spec.networks[]` entry of that document. Optional when the document declares exactly one network (Phase 2). |
| `name` | Subnet name; unique among this workspace's subnets per document (Phase 1). |
| `prefix_length` | As in D1. A workload subnet never takes a fixed `cidr` — fixed addresses are the network owner's. |
| `description`, `configuration`, `custom` | As on `SubnetModel`, passed through. |

- A workspace's own subnets count as subnets of that document for the
  existing resource reference check: `validate_resource_subnets()` adds
  them to the union it checks against, so `unregistered_subnet` keeps
  working.
- Strata only ever sees the network document and **this** workspace —
  never other solutions' subnets. Name clashes between workloads are
  caught by Azure (subnet names are unique per vnet) at apply. Convention
  for the docs: prefix workload subnet names with the solution name.

### D4. Who creates what: `declared_by`

Every subnet in the payload carries `declared_by`:

- `network` — declared in the Network document (core subnets); the
  network deployment creates these.
- `workspace` — declared in the workspace being built; that workload
  creates these.

This replaces the earlier `owner`/`managed` pair: the deployment that
creates core subnets is simply the one whose Terraform filters on
`declared_by == "network"`, so strata needs no "which workspace owns this
network" marker. A workload build only ever contains its own workspace
subnets, so `declared_by == "workspace"` means "mine".

### D5. Payload

`strata.networks` keeps its name-keyed shape. Per network: `address_space`
(`[]` when not given) and new `ipam` — always `{type, pool, prefix_length,
configuration}`, with `type` (and the others) null when the network names
no IPAM; never a bare `null` (V13) — the only place a provisioner learns
which IPAM to call. Per subnet: `cidr` (`null` for an allocated subnet), new
`prefix_length` (`null` for a fixed one), new `declared_by`. The
workspace's own subnets are merged into their definition's `subnets` map.

```hcl
strata.networks["main-network"] = {
  ...
  networks = {
    vnet-main = {
      address_space = ["10.10.0.0/22"]
      ipam          = null
      subnets = {
        firewall            = { cidr = "10.10.0.0/26", prefix_length = null, declared_by = "network",   ... }
        aks_nodes           = { cidr = null,           prefix_length = 25,   declared_by = "network",   ... }
        app1-pe             = { cidr = null,           prefix_length = 27,   declared_by = "workspace", ... }
        app1-func           = { cidr = null,           prefix_length = 26,   declared_by = "workspace", ... }
      }
      ...
    }
  }
}
```

The payload reaches Terraform and OpenTofu (`strata.auto.tfvars.json`).
Ansible is not ported to v2 yet (`integrations/registry.py` lists it as a
known v1 type); when it is, it receives the same `networks` data, `ipam`
included — nothing in this design is Terraform-specific except the
reference module.

Value tokens stay literal in the payload at build time, as today
(ADR-0022 D4). A workspace subnet whose name equals a document subnet in
the same definition is an error before this point (D7), so the merge never
overwrites.

### D6. Reference Terraform module (no IPAM)

Strata documents — and may ship under `docs/` as a copyable example — one
module both the network deployment and workloads use:

```hcl
variable "network"     { }   # strata.networks["main-network"].networks["vnet-main"]
variable "declared_by" { }   # "network" in the network deployment, "workspace" in a workload
variable "vnet_name"   { }
variable "resource_group_name" { }

data "azurerm_virtual_network" "this" {
  name                = var.vnet_name
  resource_group_name = var.resource_group_name
}

data "azurerm_subnet" "existing" {
  for_each             = toset(data.azurerm_virtual_network.this.subnets)
  name                 = each.key
  virtual_network_name = var.vnet_name
  resource_group_name  = var.resource_group_name
}

locals {
  mine     = { for n, s in var.network.subnets : n => s if s.declared_by == var.declared_by }
  existing = { for n, s in data.azurerm_subnet.existing : n => s.address_prefixes[0] }
  # 1. fixed:    s.cidr
  # 2. existing: local.existing[n]   (read back by name — stable without committed state)
  # 3. new:      first free aligned /prefix_length block across every range (see below)
  address  = { for n, s in local.mine : n => coalesce(s.cidr, lookup(local.existing, n, null), local.placed[n]) }
}

resource "azurerm_subnet" "this" {
  for_each             = local.mine
  name                 = each.key
  virtual_network_name = var.vnet_name
  resource_group_name  = var.resource_group_name
  address_prefixes     = [local.address[each.key]]

  lifecycle {
    ignore_changes = [address_prefixes]   # an address, once created, never moves
  }
}

output "subnets" {
  # downstream (NSGs, firewall rules) reads the real address from the resource, not from local.address
  value = { for n, s in azurerm_subnet.this : n => s.address_prefixes[0] }
}
```

**Placement of a new subnet** (as built in Phase 0, `placement/step`):
addresses become IPv4 integers `{start, size}`. The lowest free aligned
`/p` block always starts either at a range start or right after a taken
block, rounded up to the block size — so those are the only candidates:
one per range plus one per taken block, whatever the range size. Per
range (in order), the lowest candidate that fits the range and overlaps
no taken block (existing subnets, fixed `cidr`s) wins. This replaced the
first idea — enumerating `cidrsubnet(R, p - r, i)` for every `i` — which
hits Terraform's `range()` limit of 1024 (a /16 range holds 4096 /28s).

**Several new subnets in one apply** need each placement to see the
previous ones, and HCL has no sequential loop. Chosen in Phase 0: an
unrolled chain of 10 calls to a small `step` module, each passing its
`taken` list to the next; new subnets go largest first, then by name; more
than 10 new subnets in one network in one apply fails with a clear
message. Rejected: a provider-defined function or an `external` data
source — shorter, but a dependency outside HCL.

`ignore_changes` on `address_prefixes` is the safety net: even if a later
plan would compute a different address (for example after the subnet was
renamed in Azure), Terraform never tries to move a live subnet.

**With an IPAM** the reference module does not apply: the consumer's own
code reads `ipam.type`/`pool`/`configuration` from the payload and calls
that IPAM (for Azure Virtual Network Manager, for example, the vnet's
`ip_address_pool` block; for NetBox, its provider's available-prefix
resource). Strata ships no IPAM-specific module; the how-to shows the
payload fields and links to the IPAM's own Terraform documentation.

**Concurrency:** two workloads planning at the same moment can pick the
same block; Azure rejects the second subnet as overlapping, and re-running
the second deployment places it elsewhere. Azure also serialises changes
to one vnet, so parallel subnet creates can fail with
`AnotherOperationInProgress` — a retry, not a design problem.

**The network deployment creates the vnet and its subnets in two
steps.** The module reads the vnet with a data source; on the very first
deploy the vnet does not exist yet, and making the data source wait for
the vnet resource makes `for_each` unknown at plan. So the network deployment declares
the vnet in one execution step and the subnets (this module) in a later
step that `depends_on` it — strata's existing step ordering, no new
mechanism. Workloads are unaffected: their vnet already exists.

**Preconditions in the module** cover what strata cannot check while the
ranges are Value tokens (V3): a `precondition` that every fixed `cidr`
fits a range, and a clear failure "no free /p block in <ranges>; ask the
network owner to add a range" when placement finds nothing.

**Inputs strata does not carry:** the vnet's Azure name and resource
group come from the network definition's `configuration` (passthrough,
as today). The workload's identity needs read access on the vnet and
subnet write access (Network Contributor on the vnet, or a custom role) —
a how-to note.

**Rule for the network deployment, in every mode:** subnets are separate
`azurerm_subnet` resources, never inline `subnet` blocks on
`azurerm_virtual_network` — inline blocks make the vnet deploy delete every
subnet it doesn't declare, including workload-created ones.

### D7. Checks

| # | Check | Where | Severity / code |
|---|---|---|---|
| 1 | Subnet has exactly one of `cidr`/`prefix_length` | `SubnetModel` validator | error (schema) |
| 2 | Network has at least one of `address_space`/`ipam` | `NetworkDefinitionModel` validator | error (schema) |
| 3 | Fixed subnets don't overlap; fixed subnets fit the ranges | existing validators, changed to look at `cidr:` subnets only and skip `prefix_length:` ones (today they skip the whole network when any subnet isn't a literal) | error (schema), as today |
| 4 | `prefix_length` fits: not shorter than the largest literal range's prefix, same IP version; without literal ranges, not shorter than `ipam.prefix_length` | `NetworkDefinitionModel` validator; Phase 2 for workspace subnets | error |
| 5 | Capacity: the address count of fixed + allocated subnets (document + this workspace) is not more than the literal ranges (or the `ipam.prefix_length` block) hold. A lower bound only — other workloads also use the space. | Phase 2 semantic check | error `subnet_capacity_exceeded` |
| 6 | Workspace subnet `network` is in `spec.networks`; names unique per document in the workspace | `WorkspaceSpecModel` validator | error (schema) |
| 7 | Workspace subnet `virtual_network` exists in the document (or the document has exactly one network); its name doesn't equal a document subnet in any network of the document (a resource names a subnet by document and name only) | Phase 2, beside `validate_resource_subnets()` | error `unregistered_virtual_network`, `subnet_name_taken` |
| 8 | Resource subnet references may name a workspace subnet | `validate_resource_subnets()` union | error `unregistered_subnet`, as today |
| 9 | ~~Role minimums for Azure's reserved subnet names~~ — **dropped 2026-11-26 (V12)**: those names (`AzureFirewallSubnet`, `GatewaySubnet`, ...) can't be strata names, and a check keyed on a "role" would be the only cloud-specific rule in strata | — | — |
| 10 | `${value:...subnets.<x>.cidr}` naming a `prefix_length:` subnet | `controllers/value_references.py`, a `resolved is None` branch before the `value_reference_not_scalar` check (today a `None` leaf gives the misleading "resolves to a NoneType … sub-object" message, V4) | error `value_reference_no_address`: "subnet 'x' is allocated at apply and has no address in configuration; reference the subnet in Terraform instead" |
| 11 | Peered-network overlap (`validate_cross_network_cidr_overlap`) skips networks without `address_space` | existing validator | as today |

There is no IPAM-specific check: strata does not know what an IPAM allows
(D2).

Check 5 counts addresses (`2^(bits - prefix)` per subnet); fragmentation
can still make an allocation fail at apply even when it passes, so it is a
"certainly will not fit" check, not a promise.

### D8. What strata can no longer check

- Overlaps between allocated addresses — they only exist after apply.
- A literal CIDR for anything that points at an allocated subnet: firewall
  rules and NSGs must use the reference module's `subnets` output (D6)
  instead of `${value:}` (D7 #10). firewall-network-cidr-references.md gets
  a note.

### D9. Docs

- How-to: declare a core subnet, declare a workload subnet, consume both in
  Terraform with the reference module, grow the address space.
- `docs/config/network.md` and `docs/config/workspace.md`: the new fields.
- ADR-0007: a new decision for `prefix_length`/`ipam`/`declared_by`.
- firewall-network-cidr-references.md: the D8 note.
- Graduate this doc to `docs/design/` when implemented.

## Validation (2026-11-26)

The design was checked against the code and against Terraform/Azure
documentation. Findings, and what changed in the design:

| # | Finding | Result |
|---|---|---|
| V1 | `SubnetModel.cidr` has one consumer outside the model: `_strata_networks()` (payload). `address_space` likewise, plus `schema_examples.py`. | Confirms a small blast radius; D5 covers both. |
| V2 | The overlap/fit validators skip the whole network when any subnet is not a literal. | D7 #3 holds: exclude `prefix_length:` subnets from the comparison, keep the token rule as is. |
| V3 | Value tokens stay literal in the payload (ADR-0022 D4); strata never resolves `${var:}` ranges itself. With today's shared document (`address_space: ["${var:vnet_space}"]`), checks 3, 4 and 5 **never run** — they only cover literal ranges. | Added `precondition`s to the reference module (D6): Terraform has the resolved ranges, so the real fit/capacity safety lives there. Strata's checks stay for literal ranges and per-environment documents. |
| V4 | `${value:}` dumps the target without `exclude_none`, so a `cidr: null` leaf reaches the `value_reference_not_scalar` branch with a misleading "NoneType … sub-object" message (`controllers/value_references.py`). `${value:}` already refuses token-valued targets (`value_reference_target_not_literal`). | D7 #10 now names the exact place and a new code `value_reference_no_address`. |
| V5 | Workspace resource subnet references name a **document**; Phase 2 checks the subnet against the union of all definitions in it (`WorkspaceService.validate_resource_subnets()`, `unregistered_subnet`). | D3's `network` + `virtual_network` split matches; D7 #8 extends that union. |
| V6 | IPAM: Azure Virtual Network Manager IPAM pools exist (`azurerm_network_manager_ipam_pool`); `azurerm_virtual_network` gained an `ip_address_pool` block (`number_of_ip_addresses`). Subnet-level pool allocation in `azurerm_subnet` is **not confirmed**. Azure is reported (Azure Verified Module release notes, secondary source) to reject mixing pool allocation and static prefixes in the same vnet/subnet, and to require subnet pools to be a subset of the vnet's. Superseded by the "no IPAM integration" decision: strata only passes the IPAM information on (D2), so these Azure rules are the consumer code's concern, not strata checks, and the IPAM variant leaves the spike. |
| V7 | The Terraform variable is `networks = map(any)` (`templates/terraform/strata_variables.tf`): every subnet object must have the same attributes so the map unifies. | D5: every subnet always carries `cidr`, `prefix_length`, `declared_by` (null when unused); a test asserts the shape. |
| V8 | A data source that reads the vnet fails on the very first network deploy (the vnet does not exist yet), and deferring it makes `for_each` unknown at plan. | D6: the network deployment puts the vnet and the subnets in two execution steps. |
| V9 | The overlap test in HCL relies on `cidrhost()` ignoring host bits beyond the prefix (`cidrhost("10.10.1.32/24", 0)` → `10.10.1.0`). Reported to behave that way, not documented. | Spike item; if it fails, compare the blocks at the shorter prefix with `cidrsubnet()` instead. |
| V10 | A workload subnet whose name already exists in the vnet: the reference module reads the other subnet back "by name" and the create fails with azurerm's "already exists — import it" error. | Acceptable: a clear failure at apply. The how-to recommends prefixing workload subnet names with the solution name. |
| V11 | Parallel subnet creates in one vnet can fail with `AnotherOperationInProgress`. | Noted in D6; a retry. |
| V12 | (Found in Phase 4.) Strata names match `^[a-z][a-z0-9_-]*$` (`PlatformName`), so Azure's reserved subnet names (`AzureFirewallSubnet`, `GatewaySubnet`, `AzureBastionSubnet`, ...) cannot be subnet names. A module maps a strata name (`firewall`) to Azure's, for example through the subnet's `configuration`. | Check 9 dropped: a name-keyed check could never fire, and a new "role" field just for Azure minimums would be the only cloud-specific rule in strata — the same reason IPAM rules stay out (D2). The how-to shows the name mapping. Examples in this doc use `firewall`. |
| V13 | (Found in Phase 5, by running `terraform plan` with the real `strata_variables.tf` on payloads built by `build_strata_payload()`.) `strata.networks` is `map(any)`: Terraform refused a payload where one network's `ipam` was an object and another's `null` — "attribute types must all match for conversion to map". Every other new field passed: sized subnets (`cidr: null` beside a string), workspace subnets, an empty `address_space`, and differing `configuration` maps. | D5: `ipam` is always the four-key object, `type: null` for no IPAM. Re-checked with 9 payload variants, all accepted; the how-to's consumer HCL was run against the full variant. |

No finding changes the Decision; V3 and V8 change the Design (D6, D7),
V13 changes D5. V6 is settled by the later "no IPAM integration"
decision.

## Implementation plan

Each phase ends with `scripts/Check.ps1` green. Phase 0 gates the rest;
phases 1–5 can ship as one minor release (2.11.0): no breaking change —
every existing document still validates, since a subnet with `cidr` and a
network with `address_space` mean exactly what they meant before.

### Phase 0 — Spike the reference module (gate)

Outside the strata code, against a throwaway vnet:

- one new subnet per apply in pure HCL; the V9 `cidrhost()` behaviour;
- several new subnets per apply: the unrolled chain vs. a provider
  function / `external` data source (D6) — pick one;
- the AKS `/22` → `/25` + `/28` layout; a full range spilling into an
  added range; a second plan showing no diff; a renamed subnet with
  `ignore_changes`;
- the two-step vnet/subnets first deploy (V8).

Output: the module source and a short result note in this doc; the
answer to the "several new subnets" open question.

**Result (2026-11-27).** No throwaway vnet was used; everything that
doesn't need live Azure was proven with `terraform test`:

| Item | Result |
|---|---|
| V9 `cidrhost()` drops host bits | Confirmed (placement test `cidrhost_drops_host_bits`). |
| One / several new subnets per apply | Pure HCL; a chain of 10 `step` calls (D6). 10 /28s in one apply pass, 11 fail with a clear message. |
| AKS `/22` → `/25` + `/28` (+ `/24`) | `10.0.0.0/24`, `10.0.1.0/25`, `10.0.1.128/28`: largest first, no gaps. |
| Full range spills into an added range | Gap filled first; a block that fits nowhere in range 1 lands in range 2. Ranges are walked in order, not by address. |
| Large ranges | A `/8` with `/29`s: no enumeration, so no `range()` 1024 limit. |
| Existing subnet kept by name | Mocked read-back at an address placement would not choose: kept. |
| Real payload | Through `strata_variables.tf` (`map(any)`), azurerm 4.81 and 5.15. |
| Second plan with no diff, rename with `ignore_changes`, two-step first deploy (V8) | Need a live vnet — open in "Remaining Work". |

### Phase 1 — Network schema (D1, D2; checks 1–4, 11)

- `src/strata/models/network_model.py`:
  - `SubnetModel`: `cidr: str | None`, `prefix_length: int | None`
    (`ge=1, le=128`), validator exactly-one-of (check 1).
  - `NetworkIpamModel` (`type: PlatformName`, `pool: str | None` Value
    binding, `prefix_length: int | None`, `configuration: dict | None`).
  - `NetworkDefinitionModel`: `address_space: list[str] | None`,
    `ipam: NetworkIpamModel | None`, at-least-one-of (check 2);
    `subnets` default `[]`; `validate_subnet_cidr_overlap` and
    `validate_subnets_fit_address_space` look only at `cidr:` subnets and
    skip a network without `address_space` (check 3); new `prefix_length`
    fit and IP-version check against literal ranges (check 4);
    `schema_rules` updated.
  - `NetworkSpecModel.validate_cross_network_cidr_overlap`: skip networks
    without `address_space` (check 11).
- Field descriptions with "Design notes" (they feed the generated schema
  reference); `schema_examples.py` shows one `prefix_length` subnet.
- Tests: `tests/strata/models/test_models_network.py` — each new
  validator both ways; every existing test unchanged.

### Phase 2 — Workspace subnets (D3; checks 6, 7, 8)

- `src/strata/models/workspace_model.py`: `WorkspaceSubnetModel`
  (`network`, `virtual_network`, `name`, `prefix_length`, `description`,
  `configuration`, `custom`); `WorkspaceSpecModel.subnets`; Phase 1
  validator: `network` in `spec.networks`, names unique per document
  (check 6); `schema_rules` updated.
- `src/strata/services/workspace_service.py`: a Phase 2
  `validate_workspace_subnets()` — `virtual_network` exists (or the document
  has one network), name not taken by a document subnet
  (`unregistered_virtual_network`, `subnet_name_taken`, check 7);
  `validate_resource_subnets()` includes workspace subnets in its union
  (check 8). Wired where `validate_resource_subnets()` is called
  (`controllers/semantic_checks.py`).
- Tests: `test_models_workspace.py`, `test_services_workspace.py`,
  `test_semantic_checks.py`.

### Phase 3 — Payload (D4, D5)

- `src/strata/integrations/terraform_projection.py` `_strata_networks()`:
  `address_space` (`[]` when not given), `ipam` (all four fields, nulls
  when unset), per subnet `cidr`,
  `prefix_length`, `declared_by`; merge the workspace's own subnets into
  their definition with `declared_by = "workspace"`.
- Tests: `test_integrations_terraform_projection.py` — the payload shape,
  the merge, and the uniform subnet attribute set (V7).

### Phase 4 — Remaining checks (5, 9, 10)

- `controllers/semantic_checks.py`: capacity (`subnet_capacity_exceeded`,
  literal ranges only) and Azure role minimums
  (`subnet_below_role_minimum`, warning).
- `controllers/value_references.py`: `value_reference_no_address`
  (check 10).
- Tests in `test_semantic_checks.py` and the value-reference tests.

### Phase 5 — Docs and release

- How-to "Declare subnets and let Terraform place them": core vs. workload
  subnets, the reference module (from Phase 0), the two-step network
  deploy, the separate-`azurerm_subnet` rule, permissions, naming
  convention, growing the address space.
- `docs/config/network.md`, `docs/config/workspace.md`; ADR-0007 new
  decision; firewall-network-cidr-references.md note (D8).
- CHANGELOG `[2.11.0]` (Added), `VERSION.txt`; graduate this doc to
  `docs/design/`.

## Real-world IPAM practice this follows

- **Allocation is append-only** — an existing subnet never gets
  renumbered (the module reads it back by name and ignores address
  changes, or the IPAM keeps it).
- **One register of what's taken** — the IPAM, or the vnet itself; not a
  second copy in configuration that can drift.
- **Fixed sizes per role** — the prefix length is the reviewable intent in
  configuration; the literal address is per environment.
- **Growing the address space is the network owner's decision.**
- **Plan before apply** — the computed address shows up in `terraform
  plan` before it becomes real.

## Candidate approaches considered

### Approach 1 — `cidrsubnet()`-equivalent (`new_bits` + `netnum`) in strata — rejected

Stable (each address depends only on its own two integers), but the
author still plans every position by hand, and it knows nothing about
other deployments' subnets.

### Approach 1b — `cidrsubnets()` (plural) in Terraform over the declared list — rejected 2026-11-25

Chosen 2026-10-08. Dropped because subnets reach Terraform as a
name-keyed map (declaration order is lost); `cidrsubnets()` packs one
prefix in order and cannot fill gaps around fixed subnets, use an added
range, or see other deployments' subnets; and its snapshot tripwire had no
durable home. Approach 5 keeps "Terraform computes" but reads the real
vnet instead of trusting list order.

### Approaches 2/3 — first-fit in strata, recomputed live (stdlib or `netaddr`) — rejected

Unstable: changing any sibling shifts later addresses. Fixing that needs a
register, which leads to Approach 4.

### Approach 4 — strata-side register (committed lock file) + `strata network allocate` — rejected 2026-11-26

Designed 2026-11-25: a per-environment lock next to the shared network
document, first-fit across all ranges, a command writing new entries into
the network owner's repository, `--prune`. It worked on paper but made
strata an IPAM — a register that drifts from Azure, cross-repository
writes, and concurrency by git conflict.

### Approach 5 — strata declares WHERE/HOW, Terraform or an IPAM allocates — CHOSEN 2026-11-26

See "Decision" and "Design".

- Pros: strata stays a declare/validate/deliver tool; the register is what
  really exists (stable, sees hand-made and other deployments' subnets);
  workloads declare their own subnets without a PR to the network owner;
  works with an IPAM or without one; no lock file, no new command; no
  breaking schema change.
- Cons: allocated addresses are not known to strata — no static overlap
  check for them, and firewall rules must reference them through
  Terraform; the no-IPAM path needs a first-fit walk in HCL (to be proven,
  D6); concurrent applies can collide and need a re-run.

## Open questions

- ~~Which IPAM~~ — settled 2026-11-26: strata integrates with none; a
  network only names the IPAM it uses (D2), and the reference module
  covers the no-IPAM case.
- ~~Several new subnets per apply~~ — settled 2026-11-27 (Phase 0): an
  unrolled chain of 10 `step` module calls, pure HCL (D6).
- ~~Ship the reference module~~ — settled 2026-11-27: shipped in this
  repository as a copyable example, `examples/terraform/azure-subnets`
  (not under `docs/`, which Sphinx builds; not in the Python package).
- ~~Field names~~ — settled 2026-11-26: `prefix_length`, `ipam.type`/
  `pool`/`prefix_length`/`configuration` and `declared_by` kept; the
  workspace subnet's `definition` renamed to `virtual_network` (it leaked
  the internal `NetworkDefinitionModel` term; `network` already means the
  document). Diagnostic code `unregistered_virtual_network`.
- **Sub-range per workload** (the owner delegates a range such as
  `10.10.4.0/24` to app1, which allocates inside it): an optional pattern
  for isolation; not needed when the vnet is the register.

## Remaining Work

- [x] Settle the field names. Done 2026-11-26.
- [x] Settle shipping the reference module. Done 2026-11-27.
- [x] Phase 0 — spike the reference module. Done 2026-11-27 without a
      live vnet: `examples/terraform/azure-subnets`, 12 offline placement
      tests (also run by pytest) and 4 mocked-azurerm tests; the real
      payload through `strata_variables.tf` on azurerm 4.81 and 5.15.
- [ ] Phase 0, live Azure: against a throwaway vnet, a second plan shows
      no diff; a subnet renamed in Azure keeps its address
      (`ignore_changes`); the two-step first deploy (V8); Azure's overlap
      rejection on two concurrent workloads (V11).
- [x] Phase 1 — network schema (D1, D2; checks 1–4, 11). Done 2026-11-26
      on `feat/network-subnet-prefix-length`, ahead of the Phase 0 spike
      (the schema does not depend on how Terraform places addresses). The
      payload only got a guard (`address_space` `[]` when absent); the
      `prefix_length`/`ipam`/`declared_by` payload fields are Phase 3.
- [x] Phase 2 — workspace subnets (D3; checks 6–8, plus check 4 for
      workspace subnets: `subnet_does_not_fit`). Done 2026-11-26. Not yet
      in the payload (Phase 3).
- [x] Phase 3 — payload (D4, D5). Done 2026-11-26.
- [x] Phase 4 — checks 5 and 10; check 9 dropped (V12). Done 2026-11-26.
- [x] Phase 5 — docs, CHANGELOG 2.11.0, graduate this doc. Done
      2026-11-26: how-to `declare-subnets.md`, `network.md`,
      `workspace.md`, ADR-0007 decision 7, the firewall note; V13 fixed.
- [ ] File the proposal upstream.

## Changelog

- 2026-11-27 (IPAM block): Checks 4 and 5 use `ipam.prefix_length` as
  the space when a network has no literal address space — a strata
  field, so not an IPAM-specific rule (D2). The IP version is taken from
  a fixed subnet, else IPv6 when any length is past /32; the verdict does
  not depend on it.
- 2026-11-27 (code review): A workspace subnet's name may not reuse a
  subnet name from *any* network of the document (was: only the target
  network), since a resource names a subnet by document and name only.
  The capacity check now counts the sized subnets even when a fixed
  `cidr` is still a token (that one is left out — still a lower bound).
  `${value:}` on any unset field says "not set" instead of "whole
  sub-object". One rule each for "which network does a workspace subnet
  go into" (`NetworkModel.target_definition`) and "does a size fit"
  (`NetworkDefinitionModel.prefix_length_fits`), shared by validation and
  the payload.
- 2026-11-27 (Phase 0): Built the reference module
  `examples/terraform/azure-subnets`: a pure `placement` module (IPv4
  integers; candidates are range starts and taken-block ends, so no
  `range()` limit; a chain of 10 `step` calls for several new subnets,
  largest first) and an azurerm wrapper (reads the vnet, keeps existing
  subnets by Azure name, `configuration.azure_name` mapping,
  `ignore_changes`, preconditions for a full network, a fixed `cidr`
  outside the ranges, more than 10 new subnets, an IPAM network). V9
  confirmed (`cidrhost()` drops host bits). Tested offline and with a
  mocked provider; the live-Azure checks remain.
- 2026-11-26 (field names): Reviewed the new names before release. Kept
  `prefix_length`, `ipam.*` and `declared_by`; renamed the workspace
  subnet's `definition` to `virtual_network`, and its diagnostic code
  `unregistered_network_definition` to `unregistered_virtual_network`.
- 2026-11-26 (Phase 5): Ran the payload through real Terraform (1.12)
  with `strata_variables.tf` and found V13 (`ipam: null` beside an object
  breaks `map(any)`); `ipam` is now always the four-key object. Wrote the
  how-to and the user docs, ADR-0007 decision 7, the firewall note;
  released as 2.11.0. Graduated from `docs/work/subnet-cidr-derivation.md`
  to this file. Phase 0 (reference placement module) stays open.
- 2026-11-26 (Phase 4): Implemented check 5 — capacity:
  `NetworkDefinitionModel.address_shortfall()` (fixed + sized subnets vs.
  the collapsed literal ranges; skipped for token ranges or fixed subnets
  and mixed IP versions), used by a model validator for the document's
  own subnets and by `validate_workspace_subnets()` with the workspace's
  subnets added (`subnet_capacity_exceeded`). Implemented check 10 —
  `value_reference_no_address` for `${value:...subnets.<x>.cidr}` on a
  sized subnet. Dropped check 9 (V12): Azure's reserved subnet names are
  not valid strata names. Examples renamed to `firewall`.
- 2026-11-26 (Phase 3): Implemented the payload in `_strata_networks()` —
  per network `ipam` (`null` or all four fields), per subnet `cidr`,
  `prefix_length`, `declared_by` always present (`_subnet_payload()`, V7);
  the workspace's own subnets merged into their definition with
  `declared_by: workspace`; a workspace subnet whose definition cannot be
  resolved is left out (validation already reported it).
- 2026-11-26 (Phase 2): Implemented workspace subnets —
  `WorkspaceSubnetModel` (`network`, `virtual_network`, `name`,
  `prefix_length`, `description`, `configuration`, `custom`) and
  `WorkspaceSpecModel.subnets`; Phase 1 check that `network` is in
  `spec.networks` and names are unique per document; Phase 2
  `WorkspaceService.validate_workspace_subnets()`
  (`unregistered_virtual_network`, `subnet_name_taken`,
  `subnet_does_not_fit`), wired into `_check_workspace_subnets()`;
  `validate_resource_subnets()` accepts the workspace's own subnets.
  `NetworkDefinitionModel.literal_ranges()` made public for the service.
- 2026-11-26 (Phase 1): Implemented the network schema —
  `SubnetModel.prefix_length` (exactly one of `cidr`/`prefix_length`),
  `NetworkIpamModel` (`type`, `pool`, `prefix_length`, `configuration`),
  `NetworkDefinitionModel.address_space` optional with at least one of
  `address_space`/`ipam`, `subnets` may be empty; overlap/fit checks only
  compare `cidr` subnets; a new `prefix_length` fit check; the peering
  overlap check skips networks without `address_space`.
- 2026-11-26 (no IPAM integration): Decided strata integrates with no
  IPAM; it only tells the provisioner which IPAM a network uses. D2's
  `ipam` becomes a declaration (`type`, optional `pool`, `prefix_length`,
  `configuration`), allowed alongside `address_space` (at least one of
  the two); dropped check 12 and the IPAM variant from the reference
  module and the spike; the payload carries `ipam` to Terraform/OpenTofu,
  and to Ansible once ported. No code changed.
- 2026-11-26 (validation): Checked the design against the code and
  Terraform/Azure docs (V1–V11). Design changes: a fixed `cidr` in an
  `ipam` network is an error for now (V6, check 12); the reference module
  gets preconditions, because strata's fit/capacity checks never run on
  token ranges (V3); the network deployment creates the vnet and the
  subnets in two steps (V8); check 10 gets its own code and place (V4);
  the payload keeps a uniform subnet shape for `map(any)` (V7). Replaced
  the short plan with a phased implementation plan (Phases 0–5). No code
  changed.
- 2026-11-26 (later): Added the detailed design (D1–D9) and an
  implementation plan: `prefix_length` xor `cidr` on a subnet; `ipam` xor
  `address_space` on a network definition; workspace `spec.subnets[]`
  (`network`, `virtual_network`, `name`, `prefix_length`); `declared_by`
  replaces the earlier `owner`/`managed` pair; payload fields; the
  reference Terraform module (vnet as register, read back by name,
  first-fit, `ignore_changes`, the several-new-subnets problem); eleven
  checks with codes. No code changed.
- 2026-11-26: Reconsidered whether allocation is strata's job — no.
  Rejected Approach 4 (strata-side register and `allocate` command: it
  turns strata into an IPAM). New decision, Approach 5: strata declares
  WHERE (literal ranges or an IPAM pool) and HOW (structure, sizes,
  owners); an IPAM allocates when there is one, otherwise a Terraform
  reference module reads the existing vnet as the register. No code
  changed.
- 2026-11-25: Re-read against the use case (shared network document
  in a configuration repository, workloads requesting subnets over time, ranges added,
  mixed creators); rejected Approach 1b (name-keyed payload loses order;
  `cidrsubnets()` cannot fill gaps or see other deployments); designed
  Approach 4. No code changed.
- 2026-10-08: Chose Approach 1b (`cidrsubnets()` in Terraform, `new_bits`
  only, snapshot tripwire). No code changed.
- 2026-10-06: First write-up; chose an allocate-and-persist command
  (Approaches 2/3). No code changed.
